import pytest

from src.adapters.discord_bot.thread_rate_limiter import ThreadRenameRateLimiter


def test_thread_rename_rate_limiter_sliding_window():
    limiter = ThreadRenameRateLimiter(window_seconds=600.0, max_renames=2)
    thread_id = 123456789
    base_time = 1000.0

    # 1st rename allowed immediately
    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time)
    assert can_rename is True
    assert wait_time == 0.0
    limiter.record_rename(thread_id, now=base_time)

    # 2nd rename allowed 30 seconds later
    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time + 30.0)
    assert can_rename is True
    assert wait_time == 0.0
    limiter.record_rename(thread_id, now=base_time + 30.0)

    # 3rd rename throttled within the 600s window (at +60s)
    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time + 60.0)
    assert can_rename is False
    # Oldest rename was at base_time, so wait time is base_time + 600 - (base_time + 60) = 540s
    assert wait_time == 540.0

    # Once oldest rename falls out of the 600s window (at base_time + 601s)
    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time + 601.0)
    assert can_rename is True
    assert wait_time == 0.0


def test_thread_rename_rate_limiter_record_rate_limit():
    limiter = ThreadRenameRateLimiter(window_seconds=600.0, max_renames=2)
    thread_id = 999
    base_time = 2000.0

    # Simulate Discord returning 429 with retry_after=120s
    limiter.record_rate_limit(thread_id, retry_after=120.0, now=base_time)

    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time)
    assert can_rename is False
    assert wait_time == 120.0

    # 121 seconds later, rate limit is cleared
    can_rename, wait_time = limiter.can_rename(thread_id, now=base_time + 121.0)
    assert can_rename is True
    assert wait_time == 0.0


@pytest.mark.asyncio
async def test_thread_rename_debouncing_and_deferred_execution():
    from unittest.mock import AsyncMock, MagicMock

    limiter = ThreadRenameRateLimiter(window_seconds=600.0, max_renames=2)
    thread_id = 555
    mock_thread = MagicMock()
    mock_thread.id = thread_id
    mock_thread.name = "[T-1] Initial Title"
    mock_thread.edit = AsyncMock()

    # 1st rename: succeeds immediately
    res1 = await limiter.request_rename(mock_thread, "[T-1] Title V1")
    assert res1.executed is True
    assert res1.deferred is False
    mock_thread.edit.assert_awaited_once_with(name="[T-1] Title V1")
    mock_thread.name = "[T-1] Title V1"

    # 2nd rename: succeeds immediately
    mock_thread.edit.reset_mock()
    res2 = await limiter.request_rename(mock_thread, "[T-1] Title V2")
    assert res2.executed is True
    assert res2.deferred is False
    mock_thread.edit.assert_awaited_once_with(name="[T-1] Title V2")
    mock_thread.name = "[T-1] Title V2"

    # 3rd rename: throttled and deferred
    mock_thread.edit.reset_mock()
    res3 = await limiter.request_rename(mock_thread, "[T-1] Title V3", max_delay_override=0.05)
    assert res3.executed is False
    assert res3.deferred is True
    assert res3.cooldown_remaining_seconds > 0
    mock_thread.edit.assert_not_awaited()

    # 4th rename: debounces V3 and stages V4 instead
    res4 = await limiter.request_rename(mock_thread, "[T-1] Title V4 Final", max_delay_override=0.05)
    assert res4.executed is False
    assert res4.deferred is True

    # Wait for the deferred task (0.05s) to complete
    import asyncio

    await asyncio.sleep(0.1)

    # Verifies only V4 Final was applied (debounced V3)
    mock_thread.edit.assert_awaited_once_with(name="[T-1] Title V4 Final")


@pytest.mark.asyncio
async def test_thread_rename_http_429_backoff_handling():
    from unittest.mock import AsyncMock, MagicMock

    limiter = ThreadRenameRateLimiter(window_seconds=600.0, max_renames=2)
    thread_id = 777
    mock_thread = MagicMock()
    mock_thread.id = thread_id
    mock_thread.name = "[T-1] Initial Title"

    # Simulate Discord raising 429
    class MockDiscordHTTPException(Exception):
        def __init__(self):
            self.status = 429
            self.retry_after = 45.0

    mock_thread.edit = AsyncMock(side_effect=MockDiscordHTTPException())

    res = await limiter.request_rename(mock_thread, "[T-1] New Title", max_delay_override=0.05)
    assert res.executed is False
    assert res.deferred is True
    assert res.cooldown_remaining_seconds == 45.0

    # Thread is now on cooldown
    can_rename, wait = limiter.can_rename(thread_id)
    assert can_rename is False
    assert wait > 0.0
