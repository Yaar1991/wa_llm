import asyncio

import pytest

from whatsapp.group_sync import GroupSyncThrottle


@pytest.mark.asyncio
async def test_burst_of_requests_runs_one_sync():
    calls = 0

    async def sync() -> None:
        nonlocal calls
        calls += 1

    throttle = GroupSyncThrottle(sync, delay=0.02, min_interval=10)
    for _ in range(50):
        throttle.request()
    await asyncio.sleep(0.1)

    assert calls == 1


@pytest.mark.asyncio
async def test_next_sync_waits_for_min_interval():
    calls = 0

    async def sync() -> None:
        nonlocal calls
        calls += 1

    throttle = GroupSyncThrottle(sync, delay=0, min_interval=0.2)
    throttle.request()
    await asyncio.sleep(0.05)
    throttle.request()
    await asyncio.sleep(0.05)
    assert calls == 1 and throttle.pending

    await asyncio.sleep(0.2)
    assert calls == 2
    await throttle.aclose()


@pytest.mark.asyncio
async def test_failed_sync_is_logged_and_still_spaced_out(caplog):
    calls = 0

    async def sync() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("info query returned status 429: rate-overlimit")

    throttle = GroupSyncThrottle(sync, delay=0, min_interval=10)
    throttle.request()
    await asyncio.sleep(0.02)
    throttle.request()
    await asyncio.sleep(0.02)

    assert calls == 1
    assert "rate-overlimit" in caplog.text
    await throttle.aclose()
    assert not throttle.pending
