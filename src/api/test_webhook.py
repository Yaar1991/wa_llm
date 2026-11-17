from unittest.mock import AsyncMock, MagicMock

import pytest

from api import webhook as webhook_api
from gowa_sdk.webhooks import WebhookEnvelope


@pytest.mark.asyncio
async def test_webhook_calls_handler_for_message_event():
    payload = WebhookEnvelope.model_validate(
        {"event": "message", "payload": {"id": "m1"}}
    )
    handler = AsyncMock()
    group_sync = MagicMock()

    result = await webhook_api.webhook(payload, handler, group_sync)

    assert result == "ok"
    handler.assert_awaited_once_with(payload)
    group_sync.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["group.participants", "Group.Joined"])
async def test_webhook_requests_group_sync_for_group_events(event: str):
    payload = WebhookEnvelope.model_validate(
        {"event": event, "payload": {"chat_id": "120363@g.us"}}
    )
    handler = AsyncMock()
    group_sync = MagicMock()

    result = await webhook_api.webhook(payload, handler, group_sync)

    assert result == "ok"
    handler.assert_not_awaited()
    group_sync.request.assert_called_once_with()
