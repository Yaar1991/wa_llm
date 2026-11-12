import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from gowa_sdk.webhooks import WebhookEnvelope, WebhookMessagePayload
from models import Group, Message
from whatsapp import WhatsAppClient
from whatsapp.jid import JIDParseError
from .base_handler import BaseHandler

logger = logging.getLogger(__name__)


class MessageHandler(BaseHandler):
    """
    Stores incoming WhatsApp messages for enabled groups only.

    The bot never replies to messages: summaries are sent on a schedule.
    Direct messages and messages from groups that were not enabled are
    dropped without touching the database.
    """

    def __init__(self, session: AsyncSession, whatsapp: WhatsAppClient):
        super().__init__(session, whatsapp)

    async def __call__(self, payload: WebhookEnvelope) -> Message | None:
        if payload.event == "message.reaction":
            await self.store_reaction(payload)
            await self.session.commit()
            return None

        if payload.event != "message":
            return None

        try:
            message = Message.from_webhook(payload)
        except (AssertionError, ValueError, JIDParseError) as e:
            logger.warning(f"Ignoring unparseable message webhook: {e}")
            return None

        if not message.text:
            return None

        # Direct messages are never stored.
        if not message.group_jid:
            return None

        group = await self.session.get(Group, message.group_jid)
        if group is None or not group.managed:
            return None

        data = WebhookMessagePayload.model_validate(payload.payload)
        stored = await self.store_message(message, sender_pushname=data.from_name)
        await self.session.commit()
        return stored
