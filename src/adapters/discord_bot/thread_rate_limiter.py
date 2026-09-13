import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenameResult:
    """Outcome of a thread rename request."""

    executed: bool
    deferred: bool
    cooldown_remaining_seconds: float = 0.0


class ThreadRenameRateLimiter:
    """Tracks and enforces Discord's 2-per-10-minute thread title modification rate limit."""

    def __init__(self, window_seconds: float = 600.0, max_renames: int = 2) -> None:
        self.window_seconds = window_seconds
        self.max_renames = max_renames
        # thread_id -> list of float timestamps of past renames
        self._history: dict[int, list[float]] = {}
        # thread_id -> absolute timestamp until which renames are blocked (e.g. from Discord 429)
        self._rate_limit_until: dict[int, float] = {}
        # thread_id -> latest requested title string
        self._pending_names: dict[int, str] = {}
        # thread_id -> active asyncio Task for deferred rename
        self._deferred_tasks: dict[int, asyncio.Task[None]] = {}

    def can_rename(self, thread_id: int, now: float | None = None) -> tuple[bool, float]:
        """Checks if a thread can be renamed immediately under the rate limit.

        Returns:
            tuple[bool, float]: (is_allowed, cooldown_remaining_seconds)
        """
        current_time = time.monotonic() if now is None else now

        # 1. Check explicit rate limit lockout (e.g. from Discord 429)
        lockout_until = self._rate_limit_until.get(thread_id, 0.0)
        if current_time < lockout_until:
            return False, lockout_until - current_time

        # 2. Check sliding window of past renames
        timestamps = self._history.get(thread_id, [])
        cutoff = current_time - self.window_seconds
        valid_timestamps = [t for t in timestamps if t > cutoff]
        self._history[thread_id] = valid_timestamps

        if len(valid_timestamps) < self.max_renames:
            return True, 0.0

        # Cooldown expires when the oldest timestamp falls outside the window
        oldest_ts = valid_timestamps[0]
        remaining = max(0.0, (oldest_ts + self.window_seconds) - current_time)
        return False, remaining

    def record_rename(self, thread_id: int, now: float | None = None) -> None:
        """Records a successful rename event for the thread."""
        current_time = time.monotonic() if now is None else now
        timestamps = self._history.setdefault(thread_id, [])
        cutoff = current_time - self.window_seconds
        valid_timestamps = [t for t in timestamps if t > cutoff]
        valid_timestamps.append(current_time)
        self._history[thread_id] = valid_timestamps

    def record_rate_limit(self, thread_id: int, retry_after: float, now: float | None = None) -> None:
        """Explicitly records an external rate limit (e.g. HTTP 429) lockout."""
        current_time = time.monotonic() if now is None else now
        self._rate_limit_until[thread_id] = current_time + retry_after

    async def request_rename(
        self,
        thread: Any,
        new_name: str,
        *,
        max_delay_override: float | None = None,
    ) -> RenameResult:
        """Requests a thread rename, executing immediately if permitted or debouncing/deferring if throttled."""
        thread_id = getattr(thread, "id", None)
        if thread_id is None:
            return RenameResult(executed=False, deferred=False)

        current_name = getattr(thread, "name", None)
        if current_name == new_name:
            # Already matches expected name
            return RenameResult(executed=True, deferred=False)

        can_execute, wait_time = self.can_rename(thread_id)

        if can_execute:
            try:
                res = thread.edit(name=new_name)
                if hasattr(res, "__await__"):
                    await res
                self.record_rename(thread_id)
                # Cancel any pending deferred task for this thread
                self._cancel_deferred_task(thread_id)
                self._pending_names.pop(thread_id, None)
                return RenameResult(executed=True, deferred=False)
            except Exception as e:
                # Check for Discord HTTP 429
                status = getattr(e, "status", None)
                retry_after = getattr(e, "retry_after", None)
                if status == 429 or retry_after is not None:
                    backoff = float(retry_after) if retry_after else self.window_seconds
                    logger.warning(
                        "Discord HTTP 429 on renaming thread %s. Backing off for %.1fs: %s",
                        thread_id,
                        backoff,
                        e,
                    )
                    self.record_rate_limit(thread_id, backoff)
                    wait_time = backoff
                else:
                    raise

        # Throttled: debounce and schedule deferred rename
        delay = wait_time if max_delay_override is None else min(wait_time, max_delay_override)
        self._pending_names[thread_id] = new_name
        self._cancel_deferred_task(thread_id)

        task = asyncio.create_task(self._run_deferred(thread, new_name, delay))
        self._deferred_tasks[thread_id] = task
        return RenameResult(executed=False, deferred=True, cooldown_remaining_seconds=wait_time)

    def _cancel_deferred_task(self, thread_id: int) -> None:
        existing = self._deferred_tasks.pop(thread_id, None)
        if existing and not existing.done():
            existing.cancel()

    async def _run_deferred(self, thread: Any, target_name: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)

            thread_id = getattr(thread, "id", None)
            # Check if pending title has changed (debounced)
            latest_pending = self._pending_names.get(thread_id)
            if latest_pending != target_name:
                return

            if getattr(thread, "name", None) != target_name:
                res = thread.edit(name=target_name)
                if hasattr(res, "__await__"):
                    await res
                self.record_rename(thread_id)
                logger.info("Deferred rename executed for thread %s -> %s", thread_id, target_name)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Deferred rename failed for thread %s: %s", getattr(thread, "id", None), e)
        finally:
            thread_id = getattr(thread, "id", None)
            if self._pending_names.get(thread_id) == target_name:
                self._pending_names.pop(thread_id, None)
            self._deferred_tasks.pop(thread_id, None)
