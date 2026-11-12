from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from gowa_sdk.webhooks import WebhookEnvelope
from handler import MessageHandler
from models import Group, Message
from test_utils.mock_session import AsyncSessionMock
from whatsapp.jid import JID

GROUP_JID = "123456789-123456@g.us"


def _payload(chat_id: str, text: str | None = "hello") -> WebhookEnvelope:
    return WebhookEnvelope.model_validate(
        {
            "event": "message",
            "payload": {
                "id": "msg-1",
                "chat_id": chat_id,
                "from": "1234567890@s.whatsapp.net",
                "from_name": "Test User",
                "timestamp": datetime.now(timezone.utc),
                "body": text,
            },
        }
    )


@pytest.fixture
def whatsapp():
    client = AsyncMock()
    client.get_my_jid = AsyncMock(
        return_value=JID(user="1234567890", server="s.whatsapp.net")
    )
    return client


@pytest.fixture
def handler(mock_session: AsyncSessionMock, whatsapp: AsyncMock) -> MessageHandler:
    h = MessageHandler(mock_session, whatsapp)
    h.store_message = AsyncMock(side_effect=lambda message, **_: message)
    h.store_reaction = AsyncMock()
    return h


def _add_group(session: AsyncSessionMock, managed: bool) -> None:
    session._storage[("Group", GROUP_JID)] = Group(group_jid=GROUP_JID, managed=managed)


async def test_direct_message_is_not_stored(handler: MessageHandler, whatsapp):
    result = await handler(_payload("1234567890@s.whatsapp.net"))

    assert result is None
    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]
    whatsapp.send_message.assert_not_awaited()


async def test_unknown_group_is_not_stored(handler: MessageHandler):
    result = await handler(_payload(GROUP_JID))

    assert result is None
    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]


async def test_disabled_group_is_not_stored(
    handler: MessageHandler, mock_session: AsyncSessionMock
):
    _add_group(mock_session, managed=False)

    result = await handler(_payload(GROUP_JID))

    assert result is None
    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]


async def test_enabled_group_message_is_stored_without_reply(
    handler: MessageHandler, mock_session: AsyncSessionMock, whatsapp
):
    _add_group(mock_session, managed=True)

    result = await handler(_payload(GROUP_JID, "@1234567890 summarize please"))

    assert isinstance(result, Message)
    assert result.group_jid == GROUP_JID
    assert result.sent_by_bot is False
    handler.store_message.assert_awaited_once()  # type: ignore[attr-defined]
    assert handler.store_message.await_args.kwargs == {"sender_pushname": "Test User"}  # type: ignore[attr-defined]
    whatsapp.send_message.assert_not_awaited()
    mock_session.commit.assert_awaited()


async def test_message_without_text_is_not_stored(
    handler: MessageHandler, mock_session: AsyncSessionMock
):
    _add_group(mock_session, managed=True)

    await handler(_payload(GROUP_JID, text=None))

    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]


async def test_reaction_is_delegated(handler: MessageHandler):
    payload = WebhookEnvelope.model_validate(
        {
            "event": "message.reaction",
            "payload": {
                "from": "1234567890@s.whatsapp.net",
                "reaction": "👍",
                "reacted_message_id": "msg-1",
            },
        }
    )

    await handler(payload)

    handler.store_reaction.assert_awaited_once_with(payload)  # type: ignore[attr-defined]
    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]


async def test_other_events_are_ignored(handler: MessageHandler):
    payload = WebhookEnvelope.model_validate({"event": "message.ack", "payload": {}})

    assert await handler(payload) is None
    handler.store_message.assert_not_awaited()  # type: ignore[attr-defined]
