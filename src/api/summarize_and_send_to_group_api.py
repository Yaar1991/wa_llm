import logging
from typing import Annotated, Dict, Any
from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from whatsapp import WhatsAppClient
from summarize_and_send_to_groups import summarize_and_send_to_groups
from .deps import get_db_async_session, get_whatsapp

# Create router for send summaries to groups endpoints
router = APIRouter()

# Configure logger for this module
logger = logging.getLogger(__name__)


@router.post("/summarize_and_send_to_groups")
async def trigger_summarize_and_send_to_groups(
    session: Annotated[AsyncSession, Depends(get_db_async_session)],
    whatsapp: Annotated[WhatsAppClient, Depends(get_whatsapp)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Dict[str, Any]:
    """
    Run the scheduled summary job now, for all enabled groups.

    It will:
    1. Find all enabled groups
    2. Collect new messages since the last summary (excluding bot messages)
    3. Generate summaries with the local LLM for groups with enough new messages
    4. Send each summary to its group
    5. Update the last_summary_sync timestamp

    Returns a success message upon completion.
    """
    try:
        logger.info("Starting manual send summaries to groups sync via API")

        # Execute the send summaries to groups sync process
        await summarize_and_send_to_groups(settings, session, whatsapp)

        logger.info("send summaries to groups sync completed successfully")

        return {
            "status": "success",
            "message": "send summaries to groups sync completed successfully",
        }

    except Exception as e:
        logger.error(f"Error during send summaries to groups sync: {str(e)}")
        # Re-raise the exception to let FastAPI handle it with proper error response
        raise
