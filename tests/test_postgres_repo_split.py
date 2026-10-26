from __future__ import annotations

import pytest
from sqlalchemy import select

from src.adapters.db.repos import (
    BasePostgresRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
)
from src.adapters.db.tables import ProjectSquadTable, ProjectTable, SquadTable
from src.domain.models import Project, Task


def test_repos_modular_imports() -> None:
    """Verify that all repositories can be imported from their per-aggregate modules."""
    from src.adapters.db.repos.calendar_token_repo import PostgresUserCalendarTokenRepo as TR
    from src.adapters.db.repos.governance_repo import PostgresGuildLeadRoleRepository as RR
    from src.adapters.db.repos.governance_repo import PostgresGuildLeadUserRepository as UR
    from src.adapters.db.repos.outbox_repo import PostgresOutboxRepo as OR
    from src.adapters.db.repos.project_repo import PostgresProjectRepo as PR
    from src.adapters.db.repos.squad_repo import PostgresSquadRepo as SR
    from src.adapters.db.repos.task_repo import PostgresTaskRepo as TaskR
    from src.adapters.db.repos.user_pref_repo import PostgresUserPreferenceRepo as UPR

    assert issubclass(PR, BasePostgresRepo)
    assert issubclass(TaskR, BasePostgresRepo)
    assert issubclass(SR, BasePostgresRepo)
    assert issubclass(OR, BasePostgresRepo)
    assert issubclass(UPR, BasePostgresRepo)
    assert issubclass(RR, BasePostgresRepo)
    assert issubclass(UR, BasePostgresRepo)
    assert issubclass(TR, BasePostgresRepo)


@pytest.mark.asyncio
async def test_postgres_project_repo_does_not_mutate_squad_table(db_session) -> None:
    """PostgresProjectRepo.create should only mutate ProjectTable and ProjectSquadTable, never SquadTable."""
    project_repo = PostgresProjectRepo(db_session)

    # 1. Calling create with role IDs where no squad exists in SquadTable
    project = Project(
        guild_id=12345,
        name="Modular DB Test",
        prefix="MOD",
        discord_role_ids=[99901, 99902],
    )
    created = await project_repo.create(project)
    assert created.id is not None

    # Verify SquadTable has 0 rows (no synthetic Squad-{rid} rows created)
    squad_res = await db_session.execute(select(SquadTable).where(SquadTable.guild_id == 12345))
    squads = squad_res.scalars().all()
    assert len(squads) == 0

    # Verify ProjectTable has the created project
    proj_res = await db_session.execute(select(ProjectTable).where(ProjectTable.id == created.id))
    proj_row = proj_res.scalar_one_or_none()
    assert proj_row is not None
    assert proj_row.prefix == "MOD"


@pytest.mark.asyncio
async def test_postgres_project_repo_links_existing_squad(db_session) -> None:
    """PostgresProjectRepo.create links existing squads without creating new ones."""
    squad_repo = PostgresSquadRepo(db_session)
    project_repo = PostgresProjectRepo(db_session)

    # Pre-create a squad
    from src.domain.models import Squad

    squad = await squad_repo.create(Squad(guild_id=12345, name="Core Devs", discord_role_id=88801))

    # Create project linking this squad's role
    project = Project(
        guild_id=12345,
        name="Linked Project",
        prefix="LNK",
        discord_role_ids=[88801, 88802],  # 88802 doesn't exist
    )
    created = await project_repo.create(project)

    # Verify ProjectSquadTable only links the existing squad
    links_res = await db_session.execute(select(ProjectSquadTable).where(ProjectSquadTable.project_id == created.id))
    links = links_res.scalars().all()
    assert len(links) == 1
    assert links[0].squad_id == squad.id


@pytest.mark.asyncio
async def test_postgres_project_repo_set_archived_does_not_mutate_task_table(db_session) -> None:
    """PostgresProjectRepo.set_archived must not cascade to TaskTable directly."""
    project_repo = PostgresProjectRepo(db_session)
    task_repo = PostgresTaskRepo(db_session)

    # 1. Create project & task
    project = await project_repo.create(Project(guild_id=12345, name="Alpha", prefix="ALP"))
    task = await task_repo.create(
        Task(
            guild_id=12345,
            project_id=project.id,
            task_number=1,
            short_id="ALP-1",
            title="Subtask Alpha",
            creator_discord_id=101,
        )
    )

    # 2. Archive project directly on repo
    await project_repo.set_archived(project.id, is_archived=True)

    # 3. Verify task is NOT archived (isolation maintained)
    refreshed_task = await task_repo.get_by_id(task.id)
    assert refreshed_task is not None
    assert refreshed_task.archived_at is None
    assert not refreshed_task.is_archived


@pytest.mark.asyncio
async def test_project_service_orchestrates_squad_provisioning_and_task_archival(services, db_session) -> None:
    """ProjectService orchestrates squad creation and task archival cascade via IUnitOfWork."""
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    guild_id = 98765

    # 1. Create project with discord_role_id -> ProjectService auto-provisions squad via uow.squads
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Orchestrated Project",
        prefix="ORC",
        discord_role_id=55501,
    )

    # Verify squad was created
    squad = await squad_srv.get_by_role_id(guild_id, 55501)
    assert squad is not None
    assert squad.name == "Squad-55501"

    # 2. Create task under this project
    task = await task_srv.create_task(
        guild_id=guild_id,
        project_id=project.id,
        title="Cascade task",
        creator_discord_id=101,
    )
    assert not task.is_archived

    # 3. Archive project via ProjectService -> cascades to tasks via uow.tasks.set_archived_by_project
    archived_project = await proj_srv.archive_project(project.id)
    assert archived_project.is_archived

    # Verify task was archived via cascade
    refreshed_task = await task_srv.get_by_id(task.id)
    assert refreshed_task.is_archived
