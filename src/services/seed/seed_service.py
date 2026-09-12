from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.db.postgres_repo import PostgresOutboxRepo, PostgresProjectRepo, PostgresTaskRepo
from src.adapters.db.tables import (
    ProjectSquadTable,
    ProjectTable,
    SquadTable,
    TaskDependencyTable,
    TaskHistoryTable,
    TaskTable,
    TaskWatcherTable,
    UserPreferenceTable,
)
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.domain.enums import TaskStatus
from src.domain.models import Project, Squad, Task
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.seed.manifest_loader import SeedManifest, TaskSeedSpec
from src.services.task_service import TaskService


@dataclass
class SeedDbResult:
    projects_by_prefix: dict[str, Project] = field(default_factory=dict)
    tasks_by_slug: dict[str, Task] = field(default_factory=dict)
    squads_by_name: dict[str, Squad] = field(default_factory=dict)
    created_tasks_count: int = 0


async def purge_guild_database_records(guild_id: int, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session, session.begin():
        # Get task IDs for this guild
        task_ids_stmt = select(TaskTable.id).where(TaskTable.guild_id == guild_id)
        task_ids = (await session.execute(task_ids_stmt)).scalars().all()
        if task_ids:
            await session.execute(
                delete(TaskDependencyTable).where(
                    (TaskDependencyTable.task_id.in_(task_ids)) | (TaskDependencyTable.depends_on_task_id.in_(task_ids))
                )
            )
            await session.execute(delete(TaskWatcherTable).where(TaskWatcherTable.task_id.in_(task_ids)))
            await session.execute(delete(TaskHistoryTable).where(TaskHistoryTable.task_id.in_(task_ids)))
            await session.execute(delete(TaskTable).where(TaskTable.guild_id == guild_id))

        # Get project IDs for this guild
        proj_ids_stmt = select(ProjectTable.id).where(ProjectTable.guild_id == guild_id)
        proj_ids = (await session.execute(proj_ids_stmt)).scalars().all()
        if proj_ids:
            await session.execute(delete(ProjectSquadTable).where(ProjectSquadTable.project_id.in_(proj_ids)))
            await session.execute(delete(ProjectTable).where(ProjectTable.guild_id == guild_id))

        # Get squad IDs for this guild
        squad_ids_stmt = select(SquadTable.id).where(SquadTable.guild_id == guild_id)
        squad_ids = (await session.execute(squad_ids_stmt)).scalars().all()
        if squad_ids:
            await session.execute(delete(SquadTable).where(SquadTable.guild_id == guild_id))

        await session.execute(delete(UserPreferenceTable).where(UserPreferenceTable.guild_id == guild_id))


def _topological_sort_tasks(tasks: list[TaskSeedSpec]) -> list[TaskSeedSpec]:
    slug_map = {t.slug: t for t in tasks if t.slug}
    sorted_tasks: list[TaskSeedSpec] = []
    visited: set[str] = set()

    def dfs(t: TaskSeedSpec) -> None:
        slug = t.slug or t.title
        if slug in visited:
            return
        for prereq in t.prerequisites:
            if prereq in slug_map and prereq not in visited:
                dfs(slug_map[prereq])
        visited.add(slug)
        sorted_tasks.append(t)

    for task in tasks:
        dfs(task)

    return sorted_tasks


async def seed_database(
    manifest: SeedManifest,
    guild_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    role_mapping: dict[str, int] | None = None,
    actor_discord_id: int | None = None,
    reset: bool = True,
) -> SeedDbResult:
    if reset:
        await purge_guild_database_records(guild_id, session_factory)

    actor_id = actor_discord_id or 10001
    role_map = role_mapping or {}

    task_repo = PostgresTaskRepo(session_factory)
    project_repo = PostgresProjectRepo(session_factory)
    outbox_repo = PostgresOutboxRepo(session_factory)
    uow = SqlAlchemyUnitOfWork(session_factory)
    project_service = ProjectService(project_repo)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)

    result = SeedDbResult()

    # 1. Seed Squads
    squad_ids_by_name: dict[str, UUID] = {}
    async with session_factory() as session, session.begin():
        for squad_spec in manifest.squads:
            lookup_key = squad_spec.role_name or squad_spec.name
            role_id = role_map.get(lookup_key, 1000)
            sq_row = SquadTable(
                guild_id=guild_id,
                name=squad_spec.name,
                discord_role_id=role_id,
            )
            session.add(sq_row)
            await session.flush()
            squad_ids_by_name[squad_spec.name] = sq_row.id
            result.squads_by_name[squad_spec.name] = Squad(
                id=sq_row.id,
                guild_id=guild_id,
                name=sq_row.name,
                discord_role_id=role_id,
            )

    # 2. Seed Projects
    for proj_spec in manifest.projects:
        project = await project_service.create_project(
            guild_id=guild_id,
            name=proj_spec.name,
            prefix=proj_spec.prefix.upper(),
            description=proj_spec.description,
            category=proj_spec.category,
        )

        if proj_spec.archived:
            await project_service.archive_project(project.id)
            project.archived_at = datetime.now(UTC)

        # Associate squads
        if proj_spec.squads:
            async with session_factory() as session, session.begin():
                for s_name in proj_spec.squads:
                    if s_name in squad_ids_by_name:
                        session.add(
                            ProjectSquadTable(
                                project_id=project.id,
                                squad_id=squad_ids_by_name[s_name],
                            )
                        )

        result.projects_by_prefix[proj_spec.prefix.upper()] = project

    # 3. Seed Tasks (topologically sorted by dependencies)
    ordered_task_specs = _topological_sort_tasks(manifest.tasks)
    created_tasks_by_slug: dict[str, Task] = {}

    for task_spec in ordered_task_specs:
        proj = result.projects_by_prefix.get(task_spec.project.upper())
        if not proj:
            continue

        due_at = (
            datetime.now(UTC) + timedelta(days=task_spec.days_offset) if task_spec.days_offset is not None else None
        )

        task = await task_service.create_task(
            guild_id=guild_id,
            title=task_spec.title,
            creator_discord_id=actor_id,
            project_id=proj.id,
            due_at=due_at,
            priority=task_spec.priority,
            body=task_spec.body,
        )

        if task_spec.status != TaskStatus.NOT_STARTED:
            task = await task_service.update_status(
                task_id=task.id,
                new_status=task_spec.status,
                expected_version=task.version,
                actor_discord_id=actor_id,
            )

        if task_spec.slug:
            created_tasks_by_slug[task_spec.slug] = task
            result.tasks_by_slug[task_spec.slug] = task

        result.created_tasks_count += 1

    # 4. Seed Dependencies
    async with session_factory() as session, session.begin():
        for task_spec in manifest.tasks:
            if not task_spec.slug or not task_spec.prerequisites:
                continue
            child_task = created_tasks_by_slug.get(task_spec.slug)
            if not child_task:
                continue
            for prereq_slug in task_spec.prerequisites:
                parent_task = created_tasks_by_slug.get(prereq_slug)
                if parent_task:
                    session.add(
                        TaskDependencyTable(
                            task_id=child_task.id,
                            depends_on_task_id=parent_task.id,
                        )
                    )

    return result
