from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.domain.enums import EventType, OutboxStatus
from src.domain.models import OutboxEvent, Task
from src.ports.repositories import IOutboxRepo


class OutboxService:
    def __init__(self, outbox_repo: IOutboxRepo):
        self.outbox_repo = outbox_repo

    async def enqueue_event(
        self,
        event_type: EventType,
        idempotency_key: str,
        payload: dict[str, Any],
        scheduled_for: datetime | None = None,
        outbox_repo: IOutboxRepo | None = None,
        session: Any | None = None,
    ) -> OutboxEvent:
        event = OutboxEvent(
            idempotency_key=idempotency_key,
            event_type=event_type,
            payload=payload,
            status=OutboxStatus.PENDING,
            scheduled_for=scheduled_for or datetime.now(UTC),
        )
        repo = outbox_repo or self.outbox_repo
        return await repo.enqueue(event, session=session)

    async def schedule_task_reminders(
        self,
        task: Task,
        outbox_repo: IOutboxRepo | None = None,
        session: Any | None = None,
    ) -> list[OutboxEvent]:
        """Schedules tiered reminders (T-24h, T-1h, Due) for a task if due_at is set."""
        if not task.due_at or task.is_completed or task.is_archived:
            return []

        now = datetime.now(UTC)
        due_utc = task.due_at.astimezone(UTC)
        scheduled_events: list[OutboxEvent] = []

        base_payload = {
            "task_id": str(task.id),
            "short_id": task.short_id,
            "title": task.title,
            "guild_id": task.guild_id,
            "assignee_discord_id": task.assignee_discord_id,
            "discord_thread_id": task.discord_thread_id,
            "discord_message_id": task.discord_message_id,
            "due_at": due_utc.isoformat(),
        }

        # T-24h reminder
        t_24h = due_utc - timedelta(hours=24)
        if t_24h > now:
            evt = await self.enqueue_event(
                event_type=EventType.TASK_DUE_REMINDER,
                idempotency_key=f"task_due:{task.id}:24h",
                payload={**base_payload, "reminder_type": "24h"},
                scheduled_for=t_24h,
                outbox_repo=outbox_repo,
                session=session,
            )
            scheduled_events.append(evt)

        # T-1h reminder
        t_1h = due_utc - timedelta(hours=1)
        if t_1h > now:
            evt = await self.enqueue_event(
                event_type=EventType.TASK_DUE_REMINDER,
                idempotency_key=f"task_due:{task.id}:1h",
                payload={**base_payload, "reminder_type": "1h"},
                scheduled_for=t_1h,
                outbox_repo=outbox_repo,
                session=session,
            )
            scheduled_events.append(evt)

        # Due time alert
        if due_utc > now:
            evt = await self.enqueue_event(
                event_type=EventType.TASK_DUE_REMINDER,
                idempotency_key=f"task_due:{task.id}:due",
                payload={**base_payload, "reminder_type": "due"},
                scheduled_for=due_utc,
                outbox_repo=outbox_repo,
                session=session,
            )
            scheduled_events.append(evt)

        return scheduled_events

    async def cancel_task_reminders(
        self,
        task_id: UUID,
        outbox_repo: IOutboxRepo | None = None,
        session: Any | None = None,
    ) -> int:
        repo = outbox_repo or self.outbox_repo
        return await repo.cancel_task_reminders(task_id, session=session)

    async def reclaim_failed_events(
        self,
        max_age_hours: float = 24.0,
        outbox_repo: IOutboxRepo | None = None,
    ) -> int:
        """Reclaims failed outbox events within the lookback window to enable automatic recovery."""
        repo = outbox_repo or self.outbox_repo
        return await repo.reclaim_failed_events(max_age_hours=max_age_hours)
