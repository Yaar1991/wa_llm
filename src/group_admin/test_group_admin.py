from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import group_admin
from config import Settings
from group_admin import (
    GroupSelectionError,
    ask_yes_no,
    build_parser,
    cmd_set_enabled,
    format_group_table,
    resolve_group,
    sort_groups,
)
from models import Group


def _groups() -> list[Group]:
    return sort_groups(
        [
            Group(group_jid="3@g.us", group_name="Work chat"),
            Group(group_jid="1@g.us", group_name="משפחה", managed=True),
            Group(group_jid="2@g.us", group_name="Family"),
            Group(group_jid="4@g.us", group_name="Work friends"),
        ]
    )


def test_sort_groups_by_name():
    assert [g.group_name for g in _groups()] == [
        "Family",
        "Work chat",
        "Work friends",
        "משפחה",
    ]


@pytest.mark.parametrize(
    "ref, expected_jid",
    [
        ("1", "2@g.us"),  # number from the list
        ("4", "1@g.us"),
        ("3@g.us", "3@g.us"),  # JID
        ("family", "2@g.us"),  # case-insensitive name
        ("משפחה", "1@g.us"),
        ("chat", "3@g.us"),  # unique substring
    ],
)
def test_resolve_group(ref: str, expected_jid: str):
    assert resolve_group(_groups(), ref).group_jid == expected_jid


def test_resolve_group_ambiguous():
    with pytest.raises(GroupSelectionError, match="several groups"):
        resolve_group(_groups(), "work")


def test_resolve_group_missing():
    with pytest.raises(GroupSelectionError, match="No group matches"):
        resolve_group(_groups(), "nothing")


def test_format_group_table_marks_enabled():
    table = format_group_table(_groups())
    assert "  1  [ ]  Family  (2@g.us)" in table
    assert "  4  [x]  משפחה  (1@g.us)" in table


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_uri="postgresql+asyncpg://u:p@localhost/db",
        whatsapp_host="http://localhost:3000",
    )


async def test_enable_group_starts_clock(monkeypatch: pytest.MonkeyPatch):
    groups = _groups()
    family = groups[0]
    old_sync = datetime(2020, 1, 1)
    family.last_summary_sync = old_sync
    monkeypatch.setattr(group_admin, "gather_groups", AsyncMock(return_value=groups))
    session = MagicMock()
    session.commit = AsyncMock()

    code = await cmd_set_enabled(
        session, AsyncMock(), _settings(), "1", True, confirm=lambda _: True
    )

    assert code == 0
    assert family.managed is True
    assert family.last_summary_sync > old_sync
    session.add.assert_called_once_with(family)


def _session_with(monkeypatch: pytest.MonkeyPatch, groups: list[Group]) -> MagicMock:
    monkeypatch.setattr(group_admin, "gather_groups", AsyncMock(return_value=groups))
    session = MagicMock()
    session.commit = AsyncMock()
    return session


async def test_enable_by_number_asks_with_group_name(monkeypatch: pytest.MonkeyPatch):
    # Numbers shift when groups join or are renamed, so a stale number must
    # not silently enable a different group.
    groups = _groups()
    session = _session_with(monkeypatch, groups)
    prompts: list[str] = []

    def confirm(prompt: str) -> bool:
        prompts.append(prompt)
        return True

    code = await cmd_set_enabled(
        session, AsyncMock(), _settings(), "2", True, confirm=confirm
    )

    assert code == 0
    assert groups[1].managed is True
    assert len(prompts) == 1
    assert "Work chat" in prompts[0] and "3@g.us" in prompts[0]


@pytest.mark.parametrize("enabled, idx", [(True, 0), (False, 3)])
async def test_declined_confirmation_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, idx: int
):
    groups = _groups()
    before = groups[idx].managed
    session = _session_with(monkeypatch, groups)

    code = await cmd_set_enabled(
        session,
        AsyncMock(),
        _settings(),
        str(idx + 1),
        enabled,
        confirm=lambda _: False,
    )

    assert code == 1
    assert groups[idx].managed == before
    session.add.assert_not_called()


@pytest.mark.parametrize("ref", ["Work chat", "3@g.us"])
async def test_enable_by_name_or_jid_does_not_ask(
    monkeypatch: pytest.MonkeyPatch, ref: str
):
    groups = _groups()
    session = _session_with(monkeypatch, groups)
    confirm = MagicMock(return_value=False)

    code = await cmd_set_enabled(
        session, AsyncMock(), _settings(), ref, True, confirm=confirm
    )

    assert code == 0
    assert groups[1].managed is True
    confirm.assert_not_called()


async def test_assume_yes_skips_question(monkeypatch: pytest.MonkeyPatch):
    groups = _groups()
    session = _session_with(monkeypatch, groups)
    confirm = MagicMock(return_value=False)

    code = await cmd_set_enabled(
        session,
        AsyncMock(),
        _settings(),
        "1",
        True,
        confirm=confirm,
        assume_yes=True,
    )

    assert code == 0
    assert groups[0].managed is True
    confirm.assert_not_called()


async def test_already_enabled_by_number_does_not_ask(monkeypatch: pytest.MonkeyPatch):
    groups = _groups()
    session = _session_with(monkeypatch, groups)
    confirm = MagicMock(return_value=False)

    code = await cmd_set_enabled(
        session, AsyncMock(), _settings(), "4", True, confirm=confirm
    )

    assert code == 0
    confirm.assert_not_called()


@pytest.mark.parametrize(
    "answer, expected",
    [("y", True), ("YES", True), (" yes ", True), ("", False), ("n", False)],
)
def test_ask_yes_no(monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool):
    monkeypatch.setattr("builtins.input", lambda _: answer)
    assert ask_yes_no("? ") is expected


def test_ask_yes_no_without_terminal_is_no(monkeypatch: pytest.MonkeyPatch):
    def no_stdin(_: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    assert ask_yes_no("? ") is False


async def test_disable_group(monkeypatch: pytest.MonkeyPatch):
    groups = _groups()
    monkeypatch.setattr(group_admin, "gather_groups", AsyncMock(return_value=groups))
    session = MagicMock()
    session.commit = AsyncMock()

    code = await cmd_set_enabled(session, AsyncMock(), _settings(), "משפחה", False)

    assert code == 0
    assert groups[3].managed is False


async def test_enable_unknown_group_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(group_admin, "gather_groups", AsyncMock(return_value=_groups()))
    session = MagicMock()
    session.commit = AsyncMock()

    code = await cmd_set_enabled(session, AsyncMock(), _settings(), "nope", True)

    assert code == 2
    session.add.assert_not_called()


def test_parser():
    args = build_parser().parse_args(["groups", "enable", "My Group"])
    assert (args.command, args.action, args.group) == ("groups", "enable", "My Group")
    assert args.yes is False
    args = build_parser().parse_args(["groups", "disable", "3", "--yes"])
    assert args.yes is True
    args = build_parser().parse_args(["summarize-now", "--min-messages", "1"])
    assert args.min_messages == 1


def test_parser_preview():
    args = build_parser().parse_args(["preview", "3", "--last", "20"])
    assert (args.command, args.group, args.last) == ("preview", "3", 20)
