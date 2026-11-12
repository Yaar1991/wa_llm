import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from gowa_sdk.webhooks import WebhookEnvelope, WebhookMessagePayload
from models import (
    BaseGroup,
    BaseSender,
    Message,
    Sender,
    Group,
    BaseMessage,
    Reaction,
    upsert,
)
from whatsapp import WhatsAppClient, SendMessageRequest
from whatsapp.jid import normalize_jid

logger = logging.getLogger(__name__)


class BaseHandler:
    def __init__(self, session: AsyncSession, whatsapp: WhatsAppClient):
        self.session = session
        self.whatsapp = whatsapp

    async def store_message(
        self,
        message: Message | BaseMessage,
        sender_pushname: str | None = None,
    ) -> Message | None:
        """
        Store a group message in the database.

        Direct messages (no group) and messages without text are never stored.
        :param message: Message to store
        :param sender_pushname: Pushname of the sender [Optional]
        :return: The stored message, or None if it was not stored
        """
        if not isinstance(message, Message):
            message = Message(**message.model_dump())

        if not message.text or not message.group_jid:
            return None

        async with self.session.begin_nested():
            # Ensure sender exists and is committed
            sender = await self.session.get(Sender, message.sender_jid)
            if sender is None:
                sender = Sender(
                    **BaseSender(
                        jid=message.sender_jid,  # Use normalized JID from message
                        push_name=sender_pushname,
                    ).model_dump()
                )
                await self.upsert(sender)
                await (
                    self.session.flush()
                )  # Ensure sender is visible in this transaction
            elif sender_pushname and sender.push_name != sender_pushname:
                # Senders can be created without a name (e.g. group owners during
                # group sync), and people change their display names.
                sender.push_name = sender_pushname
                self.session.add(sender)
                await self.session.flush()

            group = await self.session.get(Group, message.group_jid)
            if group is None:
                group = Group(**BaseGroup(group_jid=message.group_jid).model_dump())
                await self.upsert(group)
                await self.session.flush()

            # Finally add the message. `sent_by_bot` is sticky so a webhook echo
            # of a message we sent can't clear the flag.
            stored_message = await upsert(
                self.session, message, sticky_true={"sent_by_bot"}
            )
            return stored_message if isinstance(stored_message, Message) else message

    async def store_reaction(self, payload: WebhookEnvelope) -> Reaction | None:
        """
        Store a reaction from a WhatsApp webhook payload.

        Only reactions to messages already stored for an enabled group are kept.
        :param payload: WhatsApp webhook payload containing reaction data
        :return: The stored reaction, or None if it was not stored
        """
        data = WebhookMessagePayload.model_validate(payload.payload)
        if payload.event != "message.reaction" or not data.reaction:
            logger.warning("No reaction found in webhook payload")
            return None

        try:
            reaction = Reaction.from_webhook(payload)

            # The reacted-to message is only in the DB if its group is enabled.
            message = await self.session.get(Message, reaction.message_id)
            if message is None or not (message.group and message.group.managed):
                return None

            async with self.session.begin_nested():
                # Ensure sender exists
                sender = await self.session.get(Sender, reaction.sender_jid)
                if sender is None:
                    sender = Sender(
                        **BaseSender(
                            jid=reaction.sender_jid,
                            push_name=data.from_name,
                        ).model_dump()
                    )
                    await self.upsert(sender)
                    await self.session.flush()

                stored_reaction = await Reaction.upsert_reaction(self.session, reaction)
                logger.info(
                    f"Stored/updated reaction from {reaction.sender_jid} on message {reaction.message_id}"
                )
                return stored_reaction

        except Exception as e:
            logger.error(f"Error storing reaction: {e}")
            return None

    async def send_message(
        self, to_jid: str, message: str, in_reply_to: str | None = None
    ) -> Message | None:
        """
        Send a message to a JID over WhatsApp, and store it (flagged as sent by the bot)
        :param to_jid: The JID to send the message to
        :param message: The message text to send
        :param in_reply_to: The JID of the message to reply to [Optional]
        :return: The stored message, or None if it was not stored (not a group)
        """
        assert to_jid, "to_jid is required"
        assert message, "message is required"
        to_jid = normalize_jid(to_jid)

        resp = await self.whatsapp.send_message(
            SendMessageRequest(
                phone=to_jid,
                message=message,
                reply_message_id=in_reply_to,
            )
        )
        assert resp.results, "Failed to send message"
        sent_message_id = resp.results.message_id
        assert sent_message_id, "Failed to get sent message ID"
        my_number = await self.whatsapp.get_my_jid()
        new_message = BaseMessage(
            message_id=sent_message_id,
            text=message,
            sender_jid=str(my_number),
            chat_jid=to_jid,
            reply_to_id=in_reply_to,
            sent_by_bot=True,
        )
        return await self.store_message(Message(**new_message.model_dump()))

    async def upsert(self, model):
        return await upsert(self.session, model)
