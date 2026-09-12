from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.db.tables import ProjectSquadTable, ProjectTable, TaskDependencyTable, TaskTable
from src.domain.enums import PriorityLevel, TaskStatus
from src.services.seed.manifest_loader import (
    ProjectSeedSpec,
    SeedManifest,
    SquadSeedSpec,
    TaskSeedSpec,
)
from src.services.seed.seed_service import seed_database


@pytest.mark.asyncio
async def test_seed_database_entities_and_dependencies(async_engine):
    guild_id = 9988776655
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    manifest = SeedManifest(
        squads=[
            SquadSeedSpec(name="Backend Squad", role_name="Backend Role"),
        ],
        projects=[
            ProjectSeedSpec(
                name="Platform Core",
                prefix="CORE",
                category="Engineering",
                description="Core backend services",
                squads=["Backend Squad"],
            ),
            ProjectSeedSpec(
                name="Legacy Monolith",
                prefix="LEG",
                category="Legacy",
                description="Old system",
                archived=True,
            ),
        ],
        tasks=[
            TaskSeedSpec(
                slug="t1_db",
                title="Configure DB",
                project="CORE",
                priority=PriorityLevel.HIGH,
                status=TaskStatus.COMPLETED,
                body="Pool setup",
            ),
            TaskSeedSpec(
                slug="t2_auth",
                title="Auth Service",
                project="CORE",
                priority=PriorityLevel.HIGH,
                status=TaskStatus.IN_PROGRESS,
                days_offset=3,
                body="JWT setup",
                prerequisites=["t1_db"],
            ),
        ],
    )

    role_mapping = {"Backend Role": 11223344}

    result = await seed_database(
        manifest=manifest,
        guild_id=guild_id,
        session_factory=session_factory,
        role_mapping=role_mapping,
        actor_discord_id=10001,
        reset=True,
    )

    assert result.created_tasks_count == 2
    assert "CORE" in result.projects_by_prefix
    assert "LEG" in result.projects_by_prefix
    assert "t1_db" in result.tasks_by_slug
    assert "t2_auth" in result.tasks_by_slug

    core_proj = result.projects_by_prefix["CORE"]
    leg_proj = result.projects_by_prefix["LEG"]
    assert core_proj.is_archived is False
    assert leg_proj.is_archived is True

    t1 = result.tasks_by_slug["t1_db"]
    t2 = result.tasks_by_slug["t2_auth"]
    assert t1.status == TaskStatus.COMPLETED
    assert t2.status == TaskStatus.IN_PROGRESS

    # Verify directly against database tables
    async with session_factory() as session:
        # Check projects
        projs = (await session.execute(select(ProjectTable).where(ProjectTable.guild_id == guild_id))).scalars().all()
        assert len(projs) == 2

        # Check project squad link
        ps = (await session.execute(select(ProjectSquadTable))).scalars().all()
        assert len(ps) == 1

        # Check tasks
        tasks = (await session.execute(select(TaskTable).where(TaskTable.guild_id == guild_id))).scalars().all()
        assert len(tasks) == 2

        # Check task dependency
        deps = (await session.execute(select(TaskDependencyTable))).scalars().all()
        assert len(deps) == 1
        assert deps[0].task_id == t2.id
        assert deps[0].depends_on_task_id == t1.id
