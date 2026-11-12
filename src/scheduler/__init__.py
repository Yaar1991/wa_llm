"""In-app cron scheduler for the summary job."""

import logging
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Settings

logger = logging.getLogger(__name__)

SUMMARY_JOB_ID = "summarize_groups"


def create_summary_scheduler(
    settings: Settings, job: Callable[[], Awaitable[None]]
) -> AsyncIOScheduler | None:
    """
    Build a scheduler that runs `job` on SUMMARY_CRON in SUMMARY_TIMEZONE.

    Returns None when SUMMARY_CRON is empty (scheduling disabled).
    The caller starts it (inside a running event loop) and shuts it down.
    """
    if not settings.summary_cron.strip():
        logger.info("SUMMARY_CRON is empty; scheduled summaries are disabled")
        return None

    tz = ZoneInfo(settings.summary_timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        job,
        CronTrigger.from_crontab(settings.summary_cron, timezone=tz),
        id=SUMMARY_JOB_ID,
        max_instances=1,
        coalesce=True,
        # If the app was down at the scheduled time, still run within an hour.
        misfire_grace_time=3600,
    )
    return scheduler
