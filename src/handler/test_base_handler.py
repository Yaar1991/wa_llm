from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from handler.base_handler import BaseHandler
from models import BaseMessage, Group, Message, Sender, upsert
from test_utils.mock_session import AsyncSessionMock
from whatsapp.jid import JID


async def test_store_message_skips_direct_messages(mock_session: AsyncSessionMock):
    handler = BaseHandler(mock_session, AsyncMock())
    message = Message(
        **BaseMessage(
            message_id="dm-1",
            chat_jid="1234567890@s.whatsapp.net",
            sender_jid="1234567890@s.whatsapp.net",
            text="private",
        ).model_dump()
    )

    assert await handler.store_message(message) is None
    mock_session.execute.assert_not_awaited()


async def test_send_message_flags_message_as_sent_by_bot(
    mock_session: AsyncSessionMock,
):
    whatsapp = AsyncMock()
    whatsapp.send_message = AsyncMock(
        return_value=MagicMock(results=MagicMock(message_id="sent-1"))
    )
    whatsapp.get_my_jid = AsyncMock(
        return_value=JID(user="1234567890", server="s.whatsapp.net")
    )
    handler = BaseHandler(mock_session, whatsapp)
    handler.store_message = AsyncMock(side_effect=lambda message: message)

    stored = await handler.send_message("123-456@g.us", "summary")

    assert stored is not None
    assert stored.sent_by_bot is True
    assert stored.sender_jid == "1234567890@s.whatsapp.net"


async def test_upsert_keeps_sent_by_bot_sticky(mock_session: AsyncSessionMock):
    message = Message(
        message_id="m1",
        chat_jid="123-456@g.us",
        sender_jid="1234567890@s.whatsapp.net",
        text="hi",
    )

    await upsert(mock_session, message, sticky_true={"sent_by_bot"})

    call = mock_session.execute.await_args
    assert call is not None
    stmt = call.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "sent_by_bot = (message.sent_by_bot OR excluded.sent_by_bot)" in sql
    assert "text = excluded.text" in sql


async def test_store_message_fills_in_missing_sender_name(
    mock_session: AsyncSessionMock,
):
    # Group owners are created by group sync without a display name.
    owner = Sender(jid="1234567890@s.whatsapp.net", push_name=None)
    mock_session._storage[("Sender", owner.jid)] = owner
    mock_session._storage[("Group", "123-456@g.us")] = Group(
        group_jid="123-456@g.us", managed=True
    )
    mock_session.add = MagicMock()
    handler = BaseHandler(mock_session, AsyncMock())
    # Built like the webhook path does, so validators derive group_jid.
    message = Message(
        **BaseMessage(
            message_id="m1",
            chat_jid="123-456@g.us",
            sender_jid=owner.jid,
            text="hi",
        ).model_dump()
    )

    await handler.store_message(message, sender_pushname="Yaar")

    assert owner.push_name == "Yaar"
    mock_session.add.assert_called_once_with(owner)
