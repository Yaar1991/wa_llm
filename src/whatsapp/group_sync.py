"""
Keep the `group` table in sync without flooding WhatsApp.

Fetching the group list is an "info query" that WhatsApp rate-limits (HTTP 429
"rate-overlimit"). Group events often arrive in bursts (e.g. after a
reconnect), so syncing once per event is not an option: a burst of requests is
collapsed into one sync, and syncs are spaced at least `min_interval` apart.
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Wait this long after the first event, so the rest of a burst is included.
DEFAULT_DELAY_SECONDS = 30.0
# Never sync more often than this.
DEFAULT_MIN_INTERVAL_SECONDS = 600.0


class GroupSyncThrottle:
    def __init__(
        self,
        sync: Callable[[], Awaitable[None]],
        delay: float = DEFAULT_DELAY_SECONDS,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
    ):
        self._sync = sync
        self._delay = delay
        self._min_interval = min_interval
        self._last_run: float | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def pending(self) -> bool:
        return self._task is not None and not self._task.done()

    def request(self) -> None:
        """Ask for a sync. Returns at once; repeated requests share one sync."""
        if self.pending:
            return
        self._task = asyncio.create_task(self._run_later())

    def _wait_seconds(self) -> float:
        wait = self._delay
        if self._last_run is not None:
            wait = max(wait, self._last_run + self._min_interval - time.monotonic())
        return wait

    async def _run_later(self) -> None:
        await asyncio.sleep(self._wait_seconds())
        # Counted even when it fails, so a rate-limited sync isn't retried at once.
        self._last_run = time.monotonic()
        try:
            await self._sync()
        except Exception as e:
            logger.warning(
                "Group sync failed (will retry on the next group event): %s", e
            )

    async def aclose(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
