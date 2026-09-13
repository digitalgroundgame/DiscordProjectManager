import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.config import settings
from src.ports.notifier import INotificationDispatcher
from src.ports.repositories import IOutboxRepo

logger = logging.getLogger("dgg_pm.outbox_worker")


class OutboxWorker:
    def __init__(
        self,
        outbox_repo: IOutboxRepo,
        notifier: INotificationDispatcher,
        poll_interval: float = settings.OUTBOX_POLL_INTERVAL_SECONDS,
        batch_size: int = settings.OUTBOX_BATCH_SIZE,
        max_retries: int = settings.OUTBOX_MAX_RETRIES,
        backoff_cap_seconds: float = settings.OUTBOX_BACKOFF_CAP_SECONDS,
        max_retention_hours: float = settings.OUTBOX_MAX_RETENTION_HOURS,
    ):
        self.outbox_repo = outbox_repo
        self.notifier = notifier
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.backoff_cap_seconds = backoff_cap_seconds
        self.max_retention_hours = max_retention_hours
        self._running = False
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Starts the worker loop."""
        self._running = True
        self._stop_event.clear()

        # Crash recovery: events marked PROCESSING before a previous crash/restart
        # are stranded forever otherwise. Reclaim them so they get redelivered.
        try:
            reclaimed = await self.outbox_repo.reclaim_stale_processing()
            if reclaimed:
                logger.warning("Reclaimed %d stale PROCESSING outbox event(s) from previous run.", reclaimed)
        except Exception as e:
            logger.warning("Could not reclaim stale PROCESSING outbox events on startup: %s", e)

        logger.info(
            "Outbox worker started. Polling: %ss, batch: %s, max retries: %s, backoff cap: %ss, retention: %sh",
            self.poll_interval,
            self.batch_size,
            self.max_retries,
            self.backoff_cap_seconds,
            self.max_retention_hours,
        )

        while self._running:
            try:
                processed_count = await self.process_batch()
                if processed_count == 0:
                    # No pending jobs, wait for interval or stop event
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                logger.info("Outbox worker received cancellation signal.")
                break
            except Exception as e:
                logger.exception("Unexpected error in outbox worker main loop: %s", e)
                await asyncio.sleep(self.poll_interval)

        logger.info("Outbox worker stopped.")

    def stop(self) -> None:
        """Signals the worker loop to stop gracefully."""
        self._running = False
        self._stop_event.set()

    async def process_batch(self) -> int:
        """Fetches and dispatches a batch of pending events."""
        events = await self.outbox_repo.fetch_pending_batch(limit=self.batch_size)
        if not events:
            return 0

        logger.info("Processing outbox batch of %d events.", len(events))
        for event in events:
            if not self._running:
                break
            await self._process_single_event(event)

        return len(events)

    async def _process_single_event(self, event) -> None:
        try:
            await self.notifier.dispatch_event(event)
            await self.outbox_repo.mark_processed(event.id)
            logger.debug("Successfully processed outbox event %s (%s)", event.id, event.event_type)
        except Exception as exc:
            # Check for Discord 429 Rate Limit
            retry_after = getattr(exc, "retry_after", None)
            is_429 = getattr(exc, "status", None) == 429 or retry_after is not None

            now = datetime.now(UTC)
            new_retry = event.retry_count + 1

            # Ensure event.created_at is timezone-aware for retention evaluation
            created_at = getattr(event, "created_at", None)
            if created_at is not None:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                retention_expired = (now - created_at) >= timedelta(hours=self.max_retention_hours)
            else:
                retention_expired = False

            retry_exceeded = new_retry >= self.max_retries
            failed = retry_exceeded or retention_expired

            if is_429:
                delay = float(retry_after) if retry_after else 5.0
                next_time = now + timedelta(seconds=delay)
                remaining = max(0, self.max_retries - new_retry)
                if failed:
                    logger.error(
                        "Discord 429 rate limit exceeded permanent threshold for event %s (attempt %d/%d): %s",
                        event.id,
                        new_retry,
                        self.max_retries,
                        exc,
                    )
                else:
                    logger.warning(
                        "Discord 429 rate limit hit for event %s (attempt %d/%d, %d remaining). Backing off for %.2fs",
                        event.id,
                        new_retry,
                        self.max_retries,
                        remaining,
                        delay,
                    )
                await self.outbox_repo.reschedule_or_fail(
                    event.id,
                    retry_count=new_retry,
                    next_scheduled_for=next_time,
                    failed=failed,
                )
                if not failed:
                    await asyncio.sleep(delay)
            else:
                backoff_seconds = min(self.backoff_cap_seconds, 2**new_retry * 5.0)
                next_time = now + timedelta(seconds=backoff_seconds)
                remaining = max(0, self.max_retries - new_retry)
                if failed:
                    logger.error(
                        "Outbox event %s permanently marked FAILED after %d attempts (max %d, retention: %.1fh): %s",
                        event.id,
                        new_retry,
                        self.max_retries,
                        self.max_retention_hours,
                        exc,
                    )
                else:
                    logger.warning(
                        "Failed to dispatch outbox event %s (attempt %d/%d, %d remaining): %s. Rescheduled for +%.1fs",
                        event.id,
                        new_retry,
                        self.max_retries,
                        remaining,
                        exc,
                        backoff_seconds,
                    )
                await self.outbox_repo.reschedule_or_fail(
                    event.id,
                    retry_count=new_retry,
                    next_scheduled_for=next_time,
                    failed=failed,
                )
