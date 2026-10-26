from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import (
    ProjectTable,
    TaskDependencyTable,
    TaskHistoryTable,
    TaskTable,
    TaskWatcherTable,
)
from src.domain.enums import PriorityLevel, TaskHistoryAction, TaskStatus
from src.domain.models import Task, TaskHistory
from src.ports.repositories import ITaskRepo


def _to_domain_task(row: TaskTable) -> Task:
    watchers = [w.user_discord_id for w in row.watchers] if row.watchers else []
    return Task(
        id=row.id,
        guild_id=row.guild_id,
        project_id=row.project_id,
        task_number=row.task_number,
        short_id=row.short_id,
        version=row.version,
        title=row.title,
        body=row.body,
        status=TaskStatus(row.status),
        priority=PriorityLevel(row.priority),
        creator_discord_id=row.creator_discord_id,
        assignee_discord_id=row.assignee_discord_id,
        due_at=row.due_at,
        completed_at=row.completed_at,
        discord_message_id=row.discord_message_id,
        discord_thread_id=row.discord_thread_id,
        metadata_json=row.metadata_json or {},
        watchers=watchers,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresTaskRepo(BasePostgresRepo, ITaskRepo):
    async def create(self, task: Task, session: AsyncSession | None = None) -> Task:
        async with self._get_session(session) as sess:
            row = TaskTable(
                id=task.id,
                guild_id=task.guild_id,
                project_id=task.project_id,
                task_number=task.task_number,
                short_id=task.short_id,
                version=task.version,
                title=task.title,
                body=task.body,
                status=task.status.value,
                priority=task.priority.value,
                creator_discord_id=task.creator_discord_id,
                assignee_discord_id=task.assignee_discord_id,
                due_at=task.due_at,
                completed_at=task.completed_at,
                discord_message_id=task.discord_message_id,
                discord_thread_id=task.discord_thread_id,
                metadata_json=task.metadata_json,
                archived_at=task.archived_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            sess.add(row)

            for watcher_id in task.watchers:
                watcher_row = TaskWatcherTable(
                    task_id=task.id,
                    user_discord_id=watcher_id,
                    created_at=task.created_at,
                )
                sess.add(watcher_row)

            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return task

    async def get_by_id(self, task_id: UUID) -> Task | None:
        async with self._get_session() as session:
            stmt = select(TaskTable).options(selectinload(TaskTable.watchers)).where(TaskTable.id == task_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_task(row) if row else None

    async def get_by_short_id(self, guild_id: int, short_id: str) -> Task | None:
        async with self._get_session() as session:
            stmt = (
                select(TaskTable)
                .options(selectinload(TaskTable.watchers))
                .where(
                    TaskTable.guild_id == guild_id,
                    func.upper(TaskTable.short_id) == short_id.upper().strip(),
                )
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_task(row) if row else None

    async def get_by_thread_id(self, guild_id: int, thread_id: int) -> Task | None:
        async with self._get_session() as session:
            stmt = (
                select(TaskTable)
                .options(selectinload(TaskTable.watchers))
                .where(
                    TaskTable.guild_id == guild_id,
                    TaskTable.discord_thread_id == thread_id,
                )
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_task(row) if row else None

    async def update_status_cas(
        self,
        task_id: UUID,
        expected_version: int,
        new_status: TaskStatus,
        completed_at: datetime | None,
        session: AsyncSession | None = None,
    ) -> Task | None:
        async with self._get_session(session) as sess:
            now = datetime.now(UTC)
            stmt = (
                update(TaskTable)
                .where(TaskTable.id == task_id, TaskTable.version == expected_version)
                .values(
                    status=new_status.value,
                    completed_at=completed_at,
                    version=expected_version + 1,
                    updated_at=now,
                )
                .returning(TaskTable)
            )
            res = await sess.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            fetch_stmt = select(TaskTable).options(selectinload(TaskTable.watchers)).where(TaskTable.id == task_id)
            fetch_res = await sess.execute(fetch_stmt)
            updated_row = fetch_res.scalar_one_or_none()
            return _to_domain_task(updated_row) if updated_row else None

    async def update_task(
        self,
        task_id: UUID,
        title: str | None = None,
        body: str | None = None,
        clear_body: bool = False,
        priority: PriorityLevel | None = None,
        assignee_discord_id: int | None = None,
        clear_assignee: bool = False,
        due_at: datetime | None = None,
        clear_due_at: bool = False,
        watchers: list[int] | None = None,
        session: AsyncSession | None = None,
    ) -> Task | None:
        async with self._get_session(session) as sess:
            values: dict[str, Any] = {
                "version": TaskTable.version + 1,
                "updated_at": datetime.now(UTC),
            }
            if title is not None:
                values["title"] = title.strip()
            if clear_body:
                values["body"] = None
            elif body is not None:
                values["body"] = body.strip() if body else None
            if priority is not None:
                values["priority"] = priority.value
            if clear_assignee:
                values["assignee_discord_id"] = None
            elif assignee_discord_id is not None:
                values["assignee_discord_id"] = assignee_discord_id
            if clear_due_at:
                values["due_at"] = None
            elif due_at is not None:
                values["due_at"] = due_at

            stmt = update(TaskTable).where(TaskTable.id == task_id).values(**values).returning(TaskTable)
            res = await sess.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None

            # If watchers were explicitly provided, synchronize watchers table
            if watchers is not None:
                clean_watchers = list(set(watchers))
                del_stmt = delete(TaskWatcherTable).where(TaskWatcherTable.task_id == task_id)
                await sess.execute(del_stmt)
                now = datetime.now(UTC)
                for uid in clean_watchers:
                    sess.add(
                        TaskWatcherTable(
                            task_id=task_id,
                            user_discord_id=uid,
                            created_at=now,
                        )
                    )
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()

            fetch_stmt = select(TaskTable).options(selectinload(TaskTable.watchers)).where(TaskTable.id == task_id)
            fetch_res = await sess.execute(fetch_stmt)
            updated_row = fetch_res.scalar_one_or_none()
            return _to_domain_task(updated_row) if updated_row else None

    async def update_discord_message(
        self,
        task_id: UUID,
        discord_message_id: int,
        discord_thread_id: int | None,
    ) -> None:
        def _parse_id(val: Any) -> int | None:
            if isinstance(val, int):
                return val
            if isinstance(val, str) and val.isdigit():
                return int(val)
            return None

        async with self._get_session() as session:
            msg_id = _parse_id(discord_message_id)
            th_id = _parse_id(discord_thread_id)

            vals: dict[str, Any] = {
                "discord_message_id": msg_id,
                "discord_thread_id": th_id,
                "updated_at": datetime.now(UTC),
            }

            stmt = update(TaskTable).where(TaskTable.id == task_id).values(**vals)
            await session.execute(stmt)
            await session.commit()

    async def list_tasks(
        self,
        guild_id: int,
        project_id: UUID | None = None,
        assignee_discord_id: int | None = None,
        status: TaskStatus | None = None,
        include_archived: bool = False,
        exclude_completed: bool = False,
        overdue_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        async with self._get_session() as session:
            filters = [TaskTable.guild_id == guild_id]
            if not include_archived:
                filters.append(TaskTable.archived_at.is_(None))
            if project_id:
                filters.append(TaskTable.project_id == project_id)
            if assignee_discord_id:
                filters.append(TaskTable.assignee_discord_id == assignee_discord_id)
            if status:
                filters.append(TaskTable.status == status.value)
            elif exclude_completed:
                filters.append(TaskTable.status != TaskStatus.COMPLETED.value)
            if overdue_only:
                now = datetime.now(UTC)
                filters.append(TaskTable.due_at.is_not(None))
                filters.append(TaskTable.due_at < now)
                filters.append(TaskTable.status != TaskStatus.COMPLETED.value)
                filters.append(TaskTable.archived_at.is_(None))

            # Count total
            count_stmt = select(func.count()).select_from(TaskTable).where(*filters)
            total_res = await session.execute(count_stmt)
            total_count = total_res.scalar() or 0

            # Fetch records
            stmt = (
                select(TaskTable)
                .options(selectinload(TaskTable.watchers))
                .where(*filters)
                .order_by(TaskTable.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_task(r) for r in rows], total_count

    async def list_user_calendar_tasks(
        self,
        user_discord_id: int,
    ) -> list[Task]:
        async with self._get_session() as session:
            stmt = (
                select(TaskTable)
                .join(ProjectTable, TaskTable.project_id == ProjectTable.id)
                .options(selectinload(TaskTable.watchers))
                .where(
                    TaskTable.assignee_discord_id == user_discord_id,
                    TaskTable.status != TaskStatus.COMPLETED.value,
                    TaskTable.archived_at.is_(None),
                    ProjectTable.archived_at.is_(None),
                )
                .order_by(
                    TaskTable.due_at.is_(None),
                    TaskTable.due_at.asc(),
                    TaskTable.created_at.asc(),
                )
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_task(r) for r in rows]

    async def search_for_autocomplete(
        self,
        guild_id: int,
        query: str,
        project_id: UUID | None = None,
        limit: int = 25,
    ) -> list[Task]:
        async with self._get_session() as session:
            filters = [
                TaskTable.guild_id == guild_id,
                TaskTable.archived_at.is_(None),
                TaskTable.status != TaskStatus.COMPLETED.value,
            ]
            if project_id:
                filters.append(TaskTable.project_id == project_id)

            clean_query = query.strip().upper()
            if clean_query:
                filters.append(
                    or_(
                        func.upper(TaskTable.short_id).like(f"%{clean_query}%"),
                        func.upper(TaskTable.title).like(f"%{clean_query}%"),
                    )
                )

            stmt = (
                select(TaskTable)
                .options(selectinload(TaskTable.watchers))
                .where(*filters)
                .order_by(TaskTable.created_at.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_task(r) for r in rows]

    async def set_archived(self, task_id: UUID, is_archived: bool, session: AsyncSession | None = None) -> Task | None:
        async with self._get_session(session) as sess:
            now = datetime.now(UTC) if is_archived else None
            stmt = (
                update(TaskTable)
                .where(TaskTable.id == task_id)
                .values(archived_at=now, updated_at=datetime.now(UTC))
                .returning(TaskTable)
            )
            res = await sess.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            fetch_stmt = select(TaskTable).options(selectinload(TaskTable.watchers)).where(TaskTable.id == task_id)
            fetch_res = await sess.execute(fetch_stmt)
            updated_row = fetch_res.scalar_one_or_none()
            return _to_domain_task(updated_row) if updated_row else None

    async def set_archived_by_project(
        self, project_id: UUID, is_archived: bool, session: AsyncSession | None = None
    ) -> int:
        async with self._get_session(session) as sess:
            now = datetime.now(UTC) if is_archived else None
            stmt = (
                update(TaskTable)
                .where(TaskTable.project_id == project_id)
                .values(archived_at=now, updated_at=datetime.now(UTC))
            )
            res = await sess.execute(stmt)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return res.rowcount or 0

    async def delete(self, task_id: UUID, session: AsyncSession | None = None) -> bool:
        async with self._get_session(session) as sess:
            # Purge dependencies where this task is either dependent or prerequisite
            dep_stmt = delete(TaskDependencyTable).where(
                or_(
                    TaskDependencyTable.task_id == task_id,
                    TaskDependencyTable.depends_on_task_id == task_id,
                )
            )
            await sess.execute(dep_stmt)

            # Purge watchers
            watchers_stmt = delete(TaskWatcherTable).where(TaskWatcherTable.task_id == task_id)
            await sess.execute(watchers_stmt)

            # Purge history
            history_stmt = delete(TaskHistoryTable).where(TaskHistoryTable.task_id == task_id)
            await sess.execute(history_stmt)

            # Delete task record
            stmt = delete(TaskTable).where(TaskTable.id == task_id)
            res = await sess.execute(stmt)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return bool(res.rowcount and res.rowcount > 0)

    async def add_history(self, history: TaskHistory, session: AsyncSession | None = None) -> TaskHistory:
        async with self._get_session(session) as sess:
            row = TaskHistoryTable(
                id=history.id,
                task_id=history.task_id,
                actor_discord_id=history.actor_discord_id,
                action=history.action.value,
                old_status=history.old_status.value if history.old_status else None,
                new_status=history.new_status.value if history.new_status else None,
                notes=history.notes,
                created_at=history.created_at,
            )
            sess.add(row)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return history

    async def get_history(self, task_id: UUID) -> list[TaskHistory]:
        async with self._get_session() as session:
            stmt = (
                select(TaskHistoryTable)
                .where(TaskHistoryTable.task_id == task_id)
                .order_by(TaskHistoryTable.created_at.asc())
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [
                TaskHistory(
                    id=r.id,
                    task_id=r.task_id,
                    actor_discord_id=r.actor_discord_id,
                    action=TaskHistoryAction(r.action),
                    old_status=TaskStatus(r.old_status) if r.old_status else None,
                    new_status=TaskStatus(r.new_status) if r.new_status else None,
                    notes=r.notes,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def add_dependency(
        self, task_id: UUID, depends_on_task_id: UUID, session: AsyncSession | None = None
    ) -> bool:
        async with self._get_session(session) as sess:
            exists_stmt = select(TaskDependencyTable).where(
                TaskDependencyTable.task_id == task_id,
                TaskDependencyTable.depends_on_task_id == depends_on_task_id,
            )
            res = await sess.execute(exists_stmt)
            if res.scalar_one_or_none():
                return True
            row = TaskDependencyTable(
                task_id=task_id,
                depends_on_task_id=depends_on_task_id,
                created_at=datetime.now(UTC),
            )
            sess.add(row)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return True

    async def remove_dependency(
        self, task_id: UUID, depends_on_task_id: UUID, session: AsyncSession | None = None
    ) -> bool:
        async with self._get_session(session) as sess:
            stmt = delete(TaskDependencyTable).where(
                TaskDependencyTable.task_id == task_id,
                TaskDependencyTable.depends_on_task_id == depends_on_task_id,
            )
            res = await sess.execute(stmt)
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return res.rowcount > 0

    async def get_prerequisite_ids(self, task_id: UUID, session: AsyncSession | None = None) -> list[UUID]:
        async with self._get_session(session) as sess:
            stmt = select(TaskDependencyTable.depends_on_task_id).where(TaskDependencyTable.task_id == task_id)
            res = await sess.execute(stmt)
            return list(res.scalars().all())

    async def get_dependent_ids(self, task_id: UUID, session: AsyncSession | None = None) -> list[UUID]:
        async with self._get_session(session) as sess:
            stmt = select(TaskDependencyTable.task_id).where(TaskDependencyTable.depends_on_task_id == task_id)
            res = await sess.execute(stmt)
            return list(res.scalars().all())

    async def get_dependencies_for_tasks(
        self, task_ids: list[UUID], session: AsyncSession | None = None
    ) -> list[tuple[UUID, UUID]]:
        if not task_ids:
            return []
        async with self._get_session(session) as sess:
            stmt = select(TaskDependencyTable.task_id, TaskDependencyTable.depends_on_task_id).where(
                or_(
                    TaskDependencyTable.task_id.in_(task_ids),
                    TaskDependencyTable.depends_on_task_id.in_(task_ids),
                )
            )
            res = await sess.execute(stmt)
            return [(row[0], row[1]) for row in res.all()]

    async def get_all_guild_dependencies(
        self, guild_id: int, session: AsyncSession | None = None
    ) -> list[tuple[UUID, UUID]]:
        async with self._get_session(session) as sess:
            stmt = (
                select(TaskDependencyTable.task_id, TaskDependencyTable.depends_on_task_id)
                .join(TaskTable, TaskTable.id == TaskDependencyTable.task_id)
                .where(TaskTable.guild_id == guild_id)
            )
            res = await sess.execute(stmt)
            return [(row[0], row[1]) for row in res.all()]
