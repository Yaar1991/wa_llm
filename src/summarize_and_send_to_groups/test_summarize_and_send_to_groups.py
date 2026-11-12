from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

import summarize_and_send_to_groups as summary_module
from config import Settings
from models import Group, Message, Sender
from summarize_and_send_to_groups import (
    _generate,
    clean_output,
    introduced_scripts,
    pack_chunks,
    summarize,
    summarize_and_send_to_group,
    summarize_and_send_to_groups,
)

GROUP_JID = "123456789-123456@g.us"


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_uri="postgresql+asyncpg://u:p@localhost/db",
        whatsapp_host="http://localhost:3000",
        **overrides,
    )


def _messages(count: int, text: str = "הודעה") -> list[Message]:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    messages = []
    for i in range(count):
        m = Message(
            message_id=f"m{i}",
            chat_jid=GROUP_JID,
            sender_jid="111@s.whatsapp.net",
            text=f"{text} {i}",
            timestamp=start + timedelta(minutes=i),
        )
        m.sender = Sender(jid="111@s.whatsapp.net", push_name="Dana")
        messages.append(m)
    return messages


class RecordingModel:
    """FunctionModel that records (system prompt, user prompt) for each call."""

    def __init__(self, reply: str = "סיכום"):
        self.calls: list[tuple[str, str]] = []
        self.reply = reply
        self.model = FunctionModel(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        system = "".join(
            p.content for p in request.parts if isinstance(p, SystemPromptPart)
        )
        user = "".join(
            str(p.content) for p in request.parts if isinstance(p, UserPromptPart)
        )
        self.calls.append((system, user))
        return ModelResponse(parts=[TextPart(f"{self.reply} {len(self.calls)}")])


def test_pack_chunks_respects_limit_and_order():
    chunks = pack_chunks(["aaaa", "bbbb", "cccc"], max_chars=9)
    assert chunks == ["aaaa\nbbbb", "cccc"]


def test_pack_chunks_truncates_oversized_item():
    assert pack_chunks(["x" * 20, "y"], max_chars=10) == ["x" * 10, "y"]


def test_clean_output_removes_think_blocks_and_mentions():
    text = "<think>reasoning here</think>\nסיכום: @972501234567 אמר שלום"
    assert clean_output(text) == "סיכום: 972501234567 אמר שלום"


async def test_summarize_single_chunk_uses_quick_summary_prompt():
    recorder = RecordingModel()

    result = await summarize(recorder.model, _settings(), "משפחה", _messages(3))

    assert result == "סיכום 1"
    assert len(recorder.calls) == 1
    system, user = recorder.calls[0]
    assert "Hebrew" in system and '"משפחה"' in system
    assert "Dana: הודעה 0" in user


async def test_summarize_long_chat_in_parts_then_merges():
    recorder = RecordingModel()
    settings = _settings(summary_chunk_max_chars=1000)
    messages = _messages(60, text="x" * 40)  # ~60 lines of ~60 chars

    result = await summarize(recorder.model, settings, "group", messages)

    partial_calls = [c for c in recorder.calls if "part " in c[0]]
    assert len(partial_calls) >= 2
    assert all(len(user) <= 1000 for _, user in partial_calls)
    final_system, final_user = recorder.calls[-1]
    assert "notes about consecutive parts" in final_system
    assert "---" in final_user
    assert result == f"סיכום {len(recorder.calls)}"


def _session_returning(messages: list[Message]) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = messages
    session.exec = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


@pytest.fixture
def send_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(summary_module.BaseHandler, "send_message", mock)
    return mock


async def test_summarize_and_send_to_group_sends_and_advances_clock(send_mock):
    last_sync = datetime(2026, 1, 1, 9, 0)
    group = Group(group_jid=GROUP_JID, group_name="g", managed=True)
    group.last_summary_sync = last_sync
    session = _session_returning(_messages(20))

    status = await summarize_and_send_to_group(
        _settings(), session, AsyncMock(), group, RecordingModel().model, 15
    )

    assert status == "sent"
    send_mock.assert_awaited_once_with(GROUP_JID, "סיכום 1")
    assert group.last_summary_sync > last_sync
    session.commit.assert_awaited_once()

    # The query excludes messages the bot sent.
    query = str(session.exec.await_args.args[0])
    assert "message.sent_by_bot IS false" in query


async def test_summarize_and_send_to_group_skips_small_backlog(send_mock):
    last_sync = datetime(2026, 1, 1, 9, 0)
    group = Group(group_jid=GROUP_JID, group_name="g", managed=True)
    group.last_summary_sync = last_sync
    recorder = RecordingModel()

    status = await summarize_and_send_to_group(
        _settings(),
        _session_returning(_messages(3)),
        AsyncMock(),
        group,
        recorder.model,
        15,
    )

    assert status == "skipped"
    assert recorder.calls == []
    send_mock.assert_not_awaited()
    assert group.last_summary_sync == last_sync


async def test_summarize_and_send_to_group_keeps_clock_when_send_fails(send_mock):
    send_mock.side_effect = RuntimeError("whatsapp down")
    last_sync = datetime(2026, 1, 1, 9, 0)
    group = Group(group_jid=GROUP_JID, group_name="g", managed=True)
    group.last_summary_sync = last_sync

    status = await summarize_and_send_to_group(
        _settings(),
        _session_returning(_messages(20)),
        AsyncMock(),
        group,
        RecordingModel().model,
        15,
    )

    assert status == "failed"
    assert group.last_summary_sync == last_sync


async def test_summarize_and_send_to_groups_unloads_model_after_use(
    monkeypatch: pytest.MonkeyPatch, send_mock
):
    group = Group(group_jid=GROUP_JID, group_name="g", managed=True)
    group.last_summary_sync = datetime(2026, 1, 1, 9, 0)

    groups_result = MagicMock()
    groups_result.all.return_value = [group]
    messages_result = MagicMock()
    messages_result.all.return_value = _messages(20)
    session = MagicMock()
    session.exec = AsyncMock(side_effect=[groups_result, messages_result])
    session.commit = AsyncMock()

    unload = AsyncMock()
    monkeypatch.setattr(summary_module, "unload_model", unload)
    monkeypatch.setattr(
        summary_module, "build_model", lambda settings: RecordingModel().model
    )

    results = await summarize_and_send_to_groups(_settings(), session, AsyncMock())

    assert results == {"g": "sent"}
    unload.assert_awaited_once()


def test_introduced_scripts_only_flags_alphabets_missing_from_chat():
    assert introduced_scripts("הנה סיכום מהحدث最近", "שלום לכולם") == {
        "arabic",
        "cjk",
    }
    # Arabic quoted in the chat itself is allowed in the summary.
    assert introduced_scripts("הוא אמר سلام", "דנה: سلام לכולם") == set()


async def test_generate_retries_when_model_drifts_into_another_alphabet():
    replies = iter(["סיכום מה最近 קרה", "סיכום מה שקרה"])

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(next(replies))])

    result = await _generate(
        FunctionModel(respond),
        _settings(),
        "quick_summary.j2",
        "דנה: שלום",
        group_name="g",
    )

    assert result == "סיכום מה שקרה"


async def test_generate_strips_foreign_alphabet_as_last_resort():
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("סיכום 最近 של היום")])

    result = await _generate(
        FunctionModel(respond),
        _settings(),
        "quick_summary.j2",
        "דנה: שלום",
        group_name="g",
    )

    assert result == "סיכום של היום"


async def test_summarize_uses_own_name_for_unnamed_owner():
    recorder = RecordingModel()
    message = Message(
        message_id="m1",
        chat_jid=GROUP_JID,
        sender_jid="972500000000@s.whatsapp.net",
        text="I typed this",
        timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    )

    await summarize(
        recorder.model,
        _settings(),
        "g",
        [message],
        fallback_names={"972500000000": "Yaar"},
    )

    assert "Yaar: I typed this" in recorder.calls[0][1]
