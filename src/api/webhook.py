from typing import Annotated

from fastapi import APIRouter, Depends, Request

from api.deps import get_handler
from handler import MessageHandler
from gowa_sdk.webhooks import WebhookEnvelope
from whatsapp.group_sync import GroupSyncThrottle

# Create router for webhook endpoints
router = APIRouter(tags=["webhook"])

MESSAGE_EVENTS = {"message", "message.reaction"}


def is_group_sync_event(event: str) -> bool:
    return event.lower().startswith("group.")


def get_group_sync(request: Request) -> GroupSyncThrottle:
    assert request.app.state.group_sync, "Group sync not initialized"
    return request.app.state.group_sync


@router.post("/webhook")
async def webhook(
    payload: WebhookEnvelope,
    handler: Annotated[MessageHandler, Depends(get_handler)],
    group_sync: Annotated[GroupSyncThrottle, Depends(get_group_sync)],
) -> str:
    """
    WhatsApp webhook endpoint for receiving incoming messages.
    Returns:
        Simple "ok" response to acknowledge receipt
    """
    event = payload.event.lower()

    # Process message and reaction events through the message handler
    if event in MESSAGE_EVENTS:
        await handler(payload)

    # Keep the GROUPS table in sync when group-related events happen. This runs
    # in the background, at most once every few minutes (WhatsApp rate-limits it).
    if is_group_sync_event(event):
        group_sync.request()

    return "ok"
