from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.adapters.db.tables import (
    GuildLeadRoleTable,
    GuildLeadUserTable,
    OutboxEventTable,
    ProjectSquadTable,
    ProjectTable,
    SquadMemberTable,
    SquadTable,
    TaskDependencyTable,
    TaskHistoryTable,
    TaskTable,
    TaskWatcherTable,
    UserPreferenceTable,
)
from src.domain.enums import (
    EventType,
    NotificationPreference,
    OutboxStatus,
    PriorityLevel,
    SquadRoleType,
    TaskHistoryAction,
    TaskStatus,
)
from src.domain.models import (
    OutboxEvent,
    Project,
    ProjectSquad,
    Squad,
    SquadMember,
    Task,
    TaskHistory,
    UserPreference,
)
from src.ports.repositories import (
    IGuildLeadRoleRepository,
    IGuildLeadUserRepository,
    IOutboxRepo,
    IProjectRepo,
    ISquadRepo,
    ITaskRepo,
    IUserPreferenceRepo,
)


class BasePostgresRepo:
    def __init__(self, session_or_factory: AsyncSession | async_sessionmaker[AsyncSession]):
        if isinstance(session_or_factory, AsyncSession):
            self._session = session_or_factory
            self._session_factory = None
        else:
            self._session = None
            self._session_factory = session_or_factory

    def _should_commit(self, session: AsyncSession | None) -> bool:
        return session is None and self._session is None

    @asynccontextmanager
    async def _get_session(self, session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession]:
        if session is not None:
            yield session
        elif self._session is not None:
            yield self._session
        else:
            async with self._session_factory() as s:
                yield s


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


def _to_domain_project(row: ProjectTable) -> Project:
    role_ids: list[int] = []
    squads = getattr(row, "squads", []) or []
    for ps in squads:
        squad = getattr(ps, "squad", None)
        if squad and getattr(squad, "discord_role_id", None) is not None:
            rid = squad.discord_role_id
            if rid not in role_ids:
                role_ids.append(rid)

    return Project(
        id=row.id,
        guild_id=row.guild_id,
        name=row.name,
        prefix=row.prefix,
        next_task_number=row.next_task_number,
        description=row.description,
        discord_channel_id=row.discord_channel_id,
        discord_role_ids=role_ids,
        lead_discord_id=row.lead_discord_id,
        category=row.category,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_domain_squad(row: SquadTable) -> Squad:
    return Squad(
        id=row.id,
        guild_id=row.guild_id,
        name=row.name,
        discord_role_id=row.discord_role_id,
        created_at=row.created_at,
    )


def _to_domain_squad_member(row: SquadMemberTable) -> SquadMember:
    return SquadMember(
        squad_id=row.squad_id,
        user_discord_id=row.user_discord_id,
        role_type=SquadRoleType(row.role_type),
        created_at=row.created_at,
    )


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


class PostgresProjectRepo(BasePostgresRepo, IProjectRepo):
    async def create(self, project: Project) -> Project:
        async with self._get_session() as session:
            row = ProjectTable(
                id=project.id,
                guild_id=project.guild_id,
                name=project.name,
                prefix=project.prefix.upper(),
                next_task_number=project.next_task_number,
                description=project.description,
                discord_channel_id=project.discord_channel_id,
                lead_discord_id=project.lead_discord_id,
                category=project.category,
                archived_at=project.archived_at,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            session.add(row)

            # Map any initial squads/roles passed in project.discord_role_ids
            for rid in project.discord_role_ids:
                squad_stmt = select(SquadTable).where(
                    SquadTable.guild_id == project.guild_id,
                    SquadTable.discord_role_id == rid,
                )
                squad_res = await session.execute(squad_stmt)
                squad_row = squad_res.scalar_one_or_none()
                if not squad_row:
                    squad_row = SquadTable(
                        guild_id=project.guild_id,
                        name=f"Squad-{rid}",
                        discord_role_id=rid,
                    )
                    session.add(squad_row)
                    await session.flush()
                pt_row = ProjectSquadTable(project_id=project.id, squad_id=squad_row.id)
                session.add(pt_row)

            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return project

    async def get_by_id(self, project_id: UUID) -> Project | None:
        async with self._get_session() as session:
            stmt = select(ProjectTable).where(ProjectTable.id == project_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_name(self, guild_id: int, name: str) -> Project | None:
        async with self._get_session() as session:
            stmt = select(ProjectTable).where(
                ProjectTable.guild_id == guild_id,
                func.upper(ProjectTable.name) == name.strip().upper(),
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_prefix(self, guild_id: int, prefix: str) -> Project | None:
        async with self._get_session() as session:
            stmt = select(ProjectTable).where(
                ProjectTable.guild_id == guild_id,
                func.upper(ProjectTable.prefix) == prefix.strip().upper(),
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_channel_id(self, guild_id: int, channel_id: int) -> Project | None:
        async with self._get_session() as session:
            stmt = select(ProjectTable).where(
                ProjectTable.guild_id == guild_id,
                ProjectTable.discord_channel_id == channel_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def increment_task_number_atomic(
        self, project_id: UUID, session: AsyncSession | None = None
    ) -> tuple[int, str]:
        async with self._get_session(session) as sess:
            now = datetime.now(UTC)
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(
                    next_task_number=ProjectTable.next_task_number + 1,
                    updated_at=now,
                )
                .returning(ProjectTable.next_task_number - 1, ProjectTable.prefix)
            )
            res = await sess.execute(stmt)
            row = res.first()
            if not row:
                raise ValueError(f"Project with ID {project_id} not found for atomic counter update")
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return row[0], row[1]

    async def list_projects(self, guild_id: int, include_archived: bool = False) -> list[Project]:
        async with self._get_session() as session:
            filters = [ProjectTable.guild_id == guild_id]
            if not include_archived:
                filters.append(ProjectTable.archived_at.is_(None))
            stmt = select(ProjectTable).where(*filters).order_by(ProjectTable.name.asc())
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_project(r) for r in rows]

    async def set_archived(self, project_id: UUID, is_archived: bool) -> Project | None:
        async with self._get_session() as session:
            now = datetime.now(UTC) if is_archived else None
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(archived_at=now, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None

            # Cascade archival to associated tasks
            task_stmt = (
                update(TaskTable)
                .where(TaskTable.project_id == project_id)
                .values(archived_at=now, updated_at=datetime.now(UTC))
            )
            await session.execute(task_stmt)
            await session.commit()
            return _to_domain_project(row)

    async def update_channel_id(self, project_id: UUID, discord_channel_id: int | None) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(discord_channel_id=discord_channel_id, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            await session.commit()
            return _to_domain_project(row)

    async def update_role_id(self, project_id: UUID, discord_role_id: int | None) -> Project | None:
        async with self._get_session() as session:
            proj_stmt = select(ProjectTable).where(ProjectTable.id == project_id)
            res = await session.execute(proj_stmt)
            proj_row = res.scalar_one_or_none()
            if not proj_row:
                return None

            del_stmt = delete(ProjectSquadTable).where(ProjectSquadTable.project_id == project_id)
            await session.execute(del_stmt)

            if discord_role_id is not None:
                squad_stmt = select(SquadTable).where(
                    SquadTable.guild_id == proj_row.guild_id,
                    SquadTable.discord_role_id == discord_role_id,
                )
                squad_res = await session.execute(squad_stmt)
                squad_row = squad_res.scalar_one_or_none()
                if not squad_row:
                    squad_row = SquadTable(
                        guild_id=proj_row.guild_id,
                        name=f"Squad-{discord_role_id}",
                        discord_role_id=discord_role_id,
                    )
                    session.add(squad_row)
                    await session.flush()

                pt_row = ProjectSquadTable(project_id=project_id, squad_id=squad_row.id)
                await session.merge(pt_row)

            proj_row.updated_at = datetime.now(UTC)
            await session.commit()
            return await self.get_by_id(project_id)

    async def update_lead_id(self, project_id: UUID, lead_discord_id: int | None) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(lead_discord_id=lead_discord_id, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            await session.commit()
            return _to_domain_project(row)

    async def assign_squad(self, project_squad: ProjectSquad) -> None:
        async with self._get_session() as session:
            row = ProjectSquadTable(
                project_id=project_squad.project_id,
                squad_id=project_squad.squad_id,
                start_date=project_squad.start_date,
                timeline=project_squad.timeline,
            )
            await session.merge(row)
            await session.commit()

    async def remove_squad(self, project_id: UUID, squad_id: UUID) -> None:
        async with self._get_session() as session:
            stmt = delete(ProjectSquadTable).where(
                ProjectSquadTable.project_id == project_id,
                ProjectSquadTable.squad_id == squad_id,
            )
            await session.execute(stmt)
            await session.commit()

    async def list_squads_for_project(self, project_id: UUID) -> list[Squad]:
        async with self._get_session() as session:
            stmt = (
                select(SquadTable)
                .join(ProjectSquadTable, ProjectSquadTable.squad_id == SquadTable.id)
                .where(ProjectSquadTable.project_id == project_id)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_squad(r) for r in rows]


class PostgresSquadRepo(BasePostgresRepo, ISquadRepo):
    async def create(self, squad: Squad) -> Squad:
        async with self._get_session() as session:
            row = SquadTable(
                id=squad.id,
                guild_id=squad.guild_id,
                name=squad.name,
                discord_role_id=squad.discord_role_id,
                created_at=squad.created_at,
            )
            session.add(row)
            await session.commit()
            return squad

    async def get_by_id(self, squad_id: UUID) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(SquadTable.id == squad_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def get_by_name(self, guild_id: int, name: str) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(
                SquadTable.guild_id == guild_id,
                func.upper(SquadTable.name) == name.strip().upper(),
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def get_by_role_id(self, guild_id: int, role_id: int) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(
                SquadTable.guild_id == guild_id,
                SquadTable.discord_role_id == role_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def add_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        async with self._get_session() as session:
            row = SquadMemberTable(
                squad_id=squad_id,
                user_discord_id=user_discord_id,
                role_type=SquadRoleType.LEAD.value,
            )
            await session.merge(row)
            await session.commit()

    async def remove_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        async with self._get_session() as session:
            stmt = delete(SquadMemberTable).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.user_discord_id == user_discord_id,
            )
            await session.execute(stmt)
            await session.commit()

    async def list_squad_leads(self, squad_id: UUID) -> list[int]:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable.user_discord_id).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.role_type == SquadRoleType.LEAD.value,
            )
            res = await session.execute(stmt)
            return [int(uid) for uid in res.scalars().all()]

    async def assign_member(self, member: SquadMember) -> None:
        async with self._get_session() as session:
            row = SquadMemberTable(
                squad_id=member.squad_id,
                user_discord_id=member.user_discord_id,
                role_type=member.role_type.value,
                created_at=member.created_at,
            )
            await session.merge(row)
            await session.commit()

    async def is_squad_lead(self, squad_id: UUID, user_discord_id: int) -> bool:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.user_discord_id == user_discord_id,
                SquadMemberTable.role_type == SquadRoleType.LEAD.value,
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none() is not None

    async def list_squads(self, guild_id: int) -> list[Squad]:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(SquadTable.guild_id == guild_id).order_by(SquadTable.name.asc())
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_squad(r) for r in rows]

    async def list_members(self, squad_id: UUID) -> list[SquadMember]:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable).where(SquadMemberTable.squad_id == squad_id)
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_squad_member(r) for r in rows]


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


class PostgresUserPreferenceRepo(BasePostgresRepo, IUserPreferenceRepo):
    async def get_preference(self, guild_id: int, user_discord_id: int) -> UserPreference | None:
        async with self._get_session() as session:
            stmt = select(UserPreferenceTable).where(
                UserPreferenceTable.guild_id == guild_id,
                UserPreferenceTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            return UserPreference(
                guild_id=row.guild_id,
                user_discord_id=row.user_discord_id,
                notify_preference=NotificationPreference(row.notify_preference),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def set_preference(
        self,
        guild_id: int,
        user_discord_id: int,
        notify_preference: NotificationPreference,
    ) -> UserPreference:
        async with self._get_session() as session:
            now = datetime.now(UTC)
            # Use SQLite or Postgres UPSERT depending on backend dialect
            dialect_name = ""
            if session.bind:
                dialect_name = session.bind.dialect.name
            elif self._session_factory and hasattr(self._session_factory, "kw"):
                engine = self._session_factory.kw.get("bind")
                dialect_name = engine.dialect.name if engine else ""

            if dialect_name == "postgresql":
                stmt = pg_insert(UserPreferenceTable).values(
                    guild_id=guild_id,
                    user_discord_id=user_discord_id,
                    notify_preference=notify_preference.value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["guild_id", "user_discord_id"],
                    set_={
                        "notify_preference": notify_preference.value,
                        "updated_at": now,
                    },
                )
            else:
                stmt = sqlite_insert(UserPreferenceTable).values(
                    guild_id=guild_id,
                    user_discord_id=user_discord_id,
                    notify_preference=notify_preference.value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["guild_id", "user_discord_id"],
                    set_={
                        "notify_preference": notify_preference.value,
                        "updated_at": now,
                    },
                )

            await session.execute(stmt)
            await session.commit()
            return UserPreference(
                guild_id=guild_id,
                user_discord_id=user_discord_id,
                notify_preference=notify_preference,
                created_at=now,
                updated_at=now,
            )

    async def get_preferences_bulk(
        self,
        guild_id: int,
        user_ids: list[int],
    ) -> dict[int, NotificationPreference]:
        if not user_ids:
            return {}
        async with self._get_session() as session:
            stmt = select(UserPreferenceTable).where(
                UserPreferenceTable.guild_id == guild_id,
                UserPreferenceTable.user_discord_id.in_(user_ids),
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            result = {uid: NotificationPreference.DM for uid in user_ids}
            for r in rows:
                try:
                    result[r.user_discord_id] = NotificationPreference(r.notify_preference)
                except ValueError:
                    result[r.user_discord_id] = NotificationPreference.DM
            return result


class PostgresGuildLeadRoleRepository(BasePostgresRepo, IGuildLeadRoleRepository):
    async def add_lead_role(self, guild_id: int, discord_role_id: int) -> None:
        async with self._get_session() as session:
            stmt = select(GuildLeadRoleTable).where(
                GuildLeadRoleTable.guild_id == guild_id,
                GuildLeadRoleTable.discord_role_id == discord_role_id,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                return
            row = GuildLeadRoleTable(guild_id=guild_id, discord_role_id=discord_role_id)
            session.add(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_lead_role(self, guild_id: int, discord_role_id: int) -> bool:
        async with self._get_session() as session:
            stmt = delete(GuildLeadRoleTable).where(
                GuildLeadRoleTable.guild_id == guild_id,
                GuildLeadRoleTable.discord_role_id == discord_role_id,
            )
            res = await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return bool(res.rowcount and res.rowcount > 0)

    async def list_lead_role_ids(self, guild_id: int) -> set[int]:
        async with self._get_session() as session:
            stmt = select(GuildLeadRoleTable.discord_role_id).where(GuildLeadRoleTable.guild_id == guild_id)
            res = await session.execute(stmt)
            return set(res.scalars().all())


class PostgresGuildLeadUserRepository(BasePostgresRepo, IGuildLeadUserRepository):
    async def add_lead_user(self, guild_id: int, user_discord_id: int) -> None:
        async with self._get_session() as session:
            stmt = select(GuildLeadUserTable).where(
                GuildLeadUserTable.guild_id == guild_id,
                GuildLeadUserTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                return
            row = GuildLeadUserTable(guild_id=guild_id, user_discord_id=user_discord_id)
            session.add(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_lead_user(self, guild_id: int, user_discord_id: int) -> bool:
        async with self._get_session() as session:
            stmt = delete(GuildLeadUserTable).where(
                GuildLeadUserTable.guild_id == guild_id,
                GuildLeadUserTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return bool(res.rowcount and res.rowcount > 0)

    async def list_lead_user_ids(self, guild_id: int) -> set[int]:
        async with self._get_session() as session:
            stmt = select(GuildLeadUserTable.user_discord_id).where(GuildLeadUserTable.guild_id == guild_id)
            res = await session.execute(stmt)
            return set(res.scalars().all())
