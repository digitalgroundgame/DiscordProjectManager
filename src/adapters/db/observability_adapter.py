from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import OutboxEventTable, ProjectTable, TaskTable
from src.domain.enums import OutboxStatus
from src.ports.observability import SystemMetricsQueryPort, SystemMetricsSnapshot


class SqlAlchemySystemMetricsAdapter(BasePostgresRepo, SystemMetricsQueryPort):
    def __init__(self, session_or_factory: AsyncSession | async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_or_factory)

    async def get_metrics_snapshot(self) -> SystemMetricsSnapshot:
        async with self._get_session() as session:
            task_count_stmt = select(func.count()).select_from(TaskTable)
            task_res = await session.execute(task_count_stmt)
            total_tasks = task_res.scalar() or 0

            proj_count_stmt = select(func.count()).select_from(ProjectTable)
            proj_res = await session.execute(proj_count_stmt)
            total_projects = proj_res.scalar() or 0

            outbox_pending_stmt = (
                select(func.count())
                .select_from(OutboxEventTable)
                .where(OutboxEventTable.status == OutboxStatus.PENDING.value)
            )
            outbox_res = await session.execute(outbox_pending_stmt)
            pending_outbox = outbox_res.scalar() or 0

            return SystemMetricsSnapshot(
                total_tasks=total_tasks,
                total_projects=total_projects,
                pending_outbox_events=pending_outbox,
            )

    async def check_health(self) -> bool:
        async with self._get_session() as session:
            await session.execute(select(1))
            return True
