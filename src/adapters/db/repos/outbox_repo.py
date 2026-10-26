from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import OutboxEventTable
from src.domain.enums import EventType, OutboxStatus
from src.domain.models import OutboxEvent
from src.ports.repositories import IOutboxRepo


def _to_domain_outbox(row: OutboxEventTable) -> OutboxEvent:
    return OutboxEvent(
        id=row.id,
        idempotency_key=row.idempotency_key,
        event_type=EventType(row.event_type),
        payload=row.payload or {},
        status=OutboxStatus(row.status),
        retry_count=row.retry_count,
        scheduled_for=row.scheduled_for,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


class PostgresOutboxRepo(BasePostgresRepo, IOutboxRepo):
    async def enqueue(self, event: OutboxEvent, session: AsyncSession | None = None) -> OutboxEvent:
        async with self._get_session(session) as sess:
            bind = sess.bind
            dialect_name = bind.dialect.name if bind else ""

            if dialect_name == "postgresql":
                stmt = (
                    pg_insert(OutboxEventTable)
                    .values(
                        id=event.id,
                        idempotency_key=event.idempotency_key,
                        event_type=event.event_type.value,
                        payload=event.payload,
                        status=event.status.value,
                        retry_count=event.retry_count,
                        scheduled_for=event.scheduled_for,
                        created_at=event.created_at,
                        processed_at=event.processed_at,
                    )
                    .on_conflict_do_update(
                        index_elements=[OutboxEventTable.idempotency_key],
                        set_={
                            "event_type": event.event_type.value,
                            "payload": event.payload,
                            "status": event.status.value,
                            "retry_count": event.retry_count,
                            "scheduled_for": event.scheduled_for,
                            "processed_at": event.processed_at,
                        },
                    )
                )
                await sess.execute(stmt)
                if self._should_commit(session):
                    await sess.commit()
                else:
                    await sess.flush()
                return event

            if dialect_name == "sqlite":
                stmt = (
                    sqlite_insert(OutboxEventTable)
                    .values(
                        id=event.id,
                        idempotency_key=event.idempotency_key,
                        event_type=event.event_type.value,
                        payload=event.payload,
                        status=event.status.value,
                        retry_count=event.retry_count,
                        scheduled_for=event.scheduled_for,
                        created_at=event.created_at,
                        processed_at=event.processed_at,
                    )
                    .on_conflict_do_update(
                        index_elements=[OutboxEventTable.idempotency_key],
                        set_={
                            "event_type": event.event_type.value,
                            "payload": event.payload,
                            "status": event.status.value,
                            "retry_count": event.retry_count,
                            "scheduled_for": event.scheduled_for,
                            "processed_at": event.processed_at,
                        },
                    )
                )
                await sess.execute(stmt)
                if self._should_commit(session):
                    await sess.commit()
                else:
                    await sess.flush()
                return event

            # Fallback
            stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == event.idempotency_key)
            res = await sess.execute(stmt)
            existing = res.scalar_one_or_none()
            if existing:
                existing.payload = event.payload
                existing.status = event.status.value
                existing.scheduled_for = event.scheduled_for
                existing.retry_count = event.retry_count
                existing.processed_at = event.processed_at
            else:
                row = OutboxEventTable(
                    id=event.id,
                    idempotency_key=event.idempotency_key,
                    event_type=event.event_type.value,
                    payload=event.payload,
                    status=event.status.value,
                    retry_count=event.retry_count,
                    scheduled_for=event.scheduled_for,
                    created_at=event.created_at,
                    processed_at=event.processed_at,
                )
                sess.add(row)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return event

    async def fetch_pending_batch(self, limit: int = 10) -> list[OutboxEvent]:
        async with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                select(OutboxEventTable)
                .where(
                    OutboxEventTable.status == OutboxStatus.PENDING.value,
                    OutboxEventTable.scheduled_for <= now,
                )
                .order_by(OutboxEventTable.scheduled_for.asc())
                .limit(limit)
            )

            bind = session.bind
            dialect_name = bind.dialect.name if bind else ""
            if dialect_name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            res = await session.execute(stmt)
            rows = res.scalars().all()

            for r in rows:
                r.status = OutboxStatus.PROCESSING.value
            await session.commit()

            return [_to_domain_outbox(r) for r in rows]

    async def mark_processed(self, event_id: UUID) -> None:
        async with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                update(OutboxEventTable)
                .where(OutboxEventTable.id == event_id)
                .values(
                    status=OutboxStatus.PROCESSED.value,
                    processed_at=now,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def reschedule_or_fail(
        self,
        event_id: UUID,
        retry_count: int,
        next_scheduled_for: datetime,
        failed: bool = False,
    ) -> None:
        async with self._get_session() as session:
            status_val = OutboxStatus.FAILED.value if failed else OutboxStatus.PENDING.value
            stmt = (
                update(OutboxEventTable)
                .where(OutboxEventTable.id == event_id)
                .values(
                    status=status_val,
                    retry_count=retry_count,
                    scheduled_for=next_scheduled_for,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def cancel_task_reminders(self, task_id: UUID, session: AsyncSession | None = None) -> int:
        async with self._get_session(session) as sess:
            prefix = f"task_due:{task_id}:"
            stmt = (
                update(OutboxEventTable)
                .where(
                    OutboxEventTable.idempotency_key.like(f"{prefix}%"),
                    OutboxEventTable.status == OutboxStatus.PENDING.value,
                )
                .values(status=OutboxStatus.CANCELLED.value)
            )
            res = await sess.execute(stmt)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return res.rowcount or 0

    async def reclaim_stale_processing(self) -> int:
        """Resets PROCESSING events back to PENDING (crash/stall recovery).

        Events are marked PROCESSING before dispatch and committed, so a hard crash
        between fetch and mark_processed strands them forever. On worker startup (or
        after a stall) we reclaim them so they get redelivered.
        """
        async with self._get_session() as session:
            stmt = (
                update(OutboxEventTable)
                .where(OutboxEventTable.status == OutboxStatus.PROCESSING.value)
                .values(status=OutboxStatus.PENDING.value)
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount or 0

    async def reclaim_failed_events(self, max_age_hours: float = 24.0) -> int:
        """Transitions recent FAILED events back to PENDING with reset retry count within lookback window.

        Enables automatic recovery following prolonged Discord outages where events exhausted
        their retry threshold and entered FAILED state. Reclaiming them makes them available
        for normal worker polling and delivery once connectivity is restored.
        """
        async with self._get_session() as session:
            now = datetime.now(UTC)
            cutoff = now - timedelta(hours=max_age_hours)
            stmt = (
                update(OutboxEventTable)
                .where(
                    OutboxEventTable.status == OutboxStatus.FAILED.value,
                    OutboxEventTable.created_at >= cutoff,
                )
                .values(
                    status=OutboxStatus.PENDING.value,
                    retry_count=0,
                    scheduled_for=now,
                )
                .execution_options(synchronize_session=False)
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount or 0
