"""Integration tests validating Alembic migrations and PostgreSQL-specific features against live Postgres.

Tests cover:
1. Reversible Alembic migration lifecycle (upgrade head -> drift check -> downgrade base -> re-upgrade).
2. PostgreSQL dialect upserts (pg_insert ... ON CONFLICT DO UPDATE) for preferences and outbox events.
3. Concurrency locking using SELECT ... FOR UPDATE SKIP LOCKED across concurrent workers.
4. Foreign key constraint enforcement and cascade deletions.
5. Composite unique constraints scoped to Discord guild IDs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.adapters.db.repos.outbox_repo import PostgresOutboxRepo
from src.adapters.db.repos.project_repo import PostgresProjectRepo
from src.adapters.db.repos.task_repo import PostgresTaskRepo
from src.adapters.db.repos.user_pref_repo import PostgresUserPreferenceRepo
from src.adapters.db.session import get_alembic_config
from src.adapters.db.tables import OutboxEventTable, ProjectTable
from src.domain.enums import NotificationPreference, OutboxStatus, TaskStatus
from src.domain.models import EventType, OutboxEvent, Project, Task


def _get_postgres_admin_url() -> str:
    raw_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if raw_url:
        normalized = raw_url.replace("postgresql+asyncpg://", "postgresql://")
        parsed = urlparse(normalized)
        user = parsed.username or "postgres"
        password = parsed.password or "postgrespassword"
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        return f"postgresql://{user}:{password}@{host}:{port}/postgres"
    return "postgresql://postgres:postgrespassword@localhost:5432/postgres"


def _is_postgres_available() -> bool:
    try:
        admin_url = _get_postgres_admin_url()
        loop = asyncio.new_event_loop()
        try:
            conn = loop.run_until_complete(asyncio.wait_for(asyncpg.connect(admin_url), timeout=2.0))
            loop.run_until_complete(conn.close())
            return True
        finally:
            loop.close()
    except Exception:
        return False


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not _is_postgres_available(), reason="Live PostgreSQL container is not reachable"),
]


@pytest_asyncio.fixture
async def pg_test_database() -> AsyncGenerator[tuple[str, str]]:
    """Provisions a temporary, fully isolated PostgreSQL database and drops it upon teardown."""
    admin_url = _get_postgres_admin_url()
    db_name = f"dgg_pm_test_{uuid.uuid4().hex[:10]}"
    parsed = urlparse(admin_url)
    user = parsed.username or "postgres"
    password = parsed.password or "postgrespassword"
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    # Create temporary database
    admin_conn = await asyncpg.connect(admin_url)
    await admin_conn.execute(f'CREATE DATABASE "{db_name}";')
    await admin_conn.close()

    asyncpg_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    yield asyncpg_url, db_name

    # Teardown: terminate remaining connections and drop database
    admin_conn = await asyncpg.connect(admin_url)
    await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE);')
    await admin_conn.close()


@pytest_asyncio.fixture
async def pg_migrated_env(
    pg_test_database: tuple[str, str],
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession], str]]:
    """Provides a PostgreSQL database migrated to 'head' with an initialized async session factory."""
    db_url, _ = pg_test_database

    cfg = get_alembic_config()
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_section_option("alembic", "sqlalchemy.url", db_url)

    # Migrate up to head
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    yield engine, session_factory, db_url

    await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_migrations_round_trip_and_drift(pg_test_database: tuple[str, str]) -> None:
    """Validates that Alembic migrations apply cleanly to head, exhibit zero drift, and revert to base cleanly."""
    db_url, _ = pg_test_database
    cfg = get_alembic_config()
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_section_option("alembic", "sqlalchemy.url", db_url)

    # 1. Upgrade to head
    await asyncio.to_thread(command.upgrade, cfg, "head")

    # 2. Check for schema drift against ORM models
    await asyncio.to_thread(command.check, cfg)

    # Verify tables exist
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    await engine.dispose()

    expected_tables = {
        "projects",
        "squads",
        "squad_members",
        "project_squads",
        "tasks",
        "task_watchers",
        "task_history",
        "task_dependencies",
        "outbox_events",
        "user_preferences",
        "guild_lead_roles",
        "guild_lead_users",
        "user_calendar_tokens",
        "alembic_version",
    }
    assert expected_tables.issubset(tables), f"Missing tables after upgrade: {expected_tables - tables}"

    # 3. Downgrade all migrations back to base
    await asyncio.to_thread(command.downgrade, cfg, "base")

    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        tables_after_downgrade = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    await engine.dispose()

    # Domain tables should be completely dropped; only alembic_version may remain
    assert not (expected_tables - {"alembic_version"}).intersection(tables_after_downgrade)

    # 4. Re-upgrade back to head
    await asyncio.to_thread(command.upgrade, cfg, "head")


@pytest.mark.asyncio
async def test_postgres_upsert_on_conflict(
    pg_migrated_env: tuple[AsyncEngine, async_sessionmaker[AsyncSession], str],
) -> None:
    """Validates PostgreSQL-specific ON CONFLICT DO UPDATE clauses in UserPreference and Outbox repositories."""
    _, session_factory, _ = pg_migrated_env

    # 1. Test User Preference upsert
    user_repo = PostgresUserPreferenceRepo(session_factory)
    guild_id = 12345
    user_id = 67890

    # Initial insert
    p1 = await user_repo.set_preference(guild_id, user_id, NotificationPreference.CHANNEL)
    assert p1.notify_preference == NotificationPreference.CHANNEL

    # Upsert with new preference
    p2 = await user_repo.set_preference(guild_id, user_id, NotificationPreference.DM)
    assert p2.notify_preference == NotificationPreference.DM

    fetched = await user_repo.get_preference(guild_id, user_id)
    assert fetched is not None
    assert fetched.notify_preference == NotificationPreference.DM

    # 2. Test Outbox event upsert on idempotent key conflict
    outbox_repo = PostgresOutboxRepo(session_factory)
    now = datetime.now(UTC)
    idempotent_key = f"key-test-{uuid.uuid4()}"

    event1 = OutboxEvent(
        id=uuid.uuid4(),
        idempotency_key=idempotent_key,
        event_type=EventType.TASK_CREATED,
        payload={"step": 1},
        status=OutboxStatus.PENDING,
        retry_count=0,
        scheduled_for=now,
        created_at=now,
        processed_at=None,
    )
    await outbox_repo.enqueue(event1)

    # Enqueue again with updated payload and status
    event2 = OutboxEvent(
        id=uuid.uuid4(),
        idempotency_key=idempotent_key,
        event_type=EventType.TASK_UPDATED,
        payload={"step": 2},
        status=OutboxStatus.PROCESSING,
        retry_count=1,
        scheduled_for=now,
        created_at=now,
        processed_at=None,
    )
    await outbox_repo.enqueue(event2)

    # Verify that the event was updated without duplicate key violation
    async with session_factory() as session:
        res = await session.execute(select(OutboxEventTable).where(OutboxEventTable.idempotency_key == idempotent_key))
        fetched_row = res.scalar_one_or_none()
        assert fetched_row is not None
        assert fetched_row.event_type == EventType.TASK_UPDATED.value
        assert fetched_row.payload == {"step": 2}


@pytest.mark.asyncio
async def test_postgres_concurrent_outbox_skip_locked(
    pg_migrated_env: tuple[AsyncEngine, async_sessionmaker[AsyncSession], str],
) -> None:
    """Validates that PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED prevents double-processing across workers."""
    _, session_factory, _ = pg_migrated_env
    repo = PostgresOutboxRepo(session_factory)

    # Enqueue 6 pending outbox events
    now = datetime.now(UTC)
    for i in range(6):
        event = OutboxEvent(
            id=uuid.uuid4(),
            idempotency_key=f"worker-test-{uuid.uuid4()}-{i}",
            event_type=EventType.TASK_CREATED,
            payload={"idx": i},
            status=OutboxStatus.PENDING,
            retry_count=0,
            scheduled_for=now,
            created_at=now,
            processed_at=None,
        )
        await repo.enqueue(event)

    # Simulate two workers polling batches concurrently
    async def run_worker() -> list[OutboxEvent]:
        return await repo.fetch_pending_batch(limit=4)

    batch1, batch2 = await asyncio.gather(run_worker(), run_worker())

    ids_worker1 = {e.id for e in batch1}
    ids_worker2 = {e.id for e in batch2}

    # Asserts that concurrent workers obtained completely disjoint event sets
    overlap = ids_worker1.intersection(ids_worker2)
    assert not overlap, f"Workers acquired overlapping locked rows: {overlap}"
    assert len(batch1) + len(batch2) == 6, f"Expected 6 total processed, got {len(batch1) + len(batch2)}"


@pytest.mark.asyncio
async def test_postgres_foreign_key_and_cascade_constraints(
    pg_migrated_env: tuple[AsyncEngine, async_sessionmaker[AsyncSession], str],
) -> None:
    """Validates foreign key enforcement and cascade deletion in PostgreSQL."""
    _, session_factory, _ = pg_migrated_env
    proj_repo = PostgresProjectRepo(session_factory)
    task_repo = PostgresTaskRepo(session_factory)

    # 1. Enforce that tasks cannot reference a non-existent project
    invalid_task = Task(
        guild_id=9999,
        project_id=uuid.uuid4(),
        creator_discord_id=111,
        task_number=1,
        short_id="INV-1",
        title="Invalid Task",
        status=TaskStatus.NOT_STARTED,
    )
    with pytest.raises(IntegrityError):
        await task_repo.create(invalid_task)

    # 2. Create valid project and task
    project = Project(
        guild_id=9999,
        name=f"Cascade Project {uuid.uuid4().hex[:6]}",
        prefix="CAS",
    )
    created_proj = await proj_repo.create(project)
    assert created_proj.id is not None

    valid_task = Task(
        guild_id=9999,
        project_id=created_proj.id,
        creator_discord_id=111,
        task_number=1,
        short_id="CAS-1",
        title="Cascade Task",
        status=TaskStatus.NOT_STARTED,
    )
    created_task = await task_repo.create(valid_task)
    assert created_task.id is not None

    # Verify task exists
    assert await task_repo.get_by_id(created_task.id) is not None

    # Delete project via SQLAlchemy session and verify cascade delete on tasks
    async with session_factory() as session:
        await session.execute(delete(ProjectTable).where(ProjectTable.id == created_proj.id))
        await session.commit()

    # The task should have been automatically cascaded and removed
    orphaned_task = await task_repo.get_by_id(created_task.id)
    assert orphaned_task is None, "Task was not cascaded upon Project deletion"


@pytest.mark.asyncio
async def test_postgres_unique_constraints_and_guild_scoping(
    pg_migrated_env: tuple[AsyncEngine, async_sessionmaker[AsyncSession], str],
) -> None:
    """Validates that PostgreSQL unique constraints respect composite keys (guild_id + prefix / name)."""
    _, session_factory, _ = pg_migrated_env
    proj_repo = PostgresProjectRepo(session_factory)

    guild_a = 50001
    guild_b = 50002

    # Project 1 in Guild A
    p1 = Project(guild_id=guild_a, name="Alpha Project", prefix="ALP")
    await proj_repo.create(p1)

    # Duplicate prefix in same Guild A must violate unique constraint
    p_dup_prefix = Project(guild_id=guild_a, name="Different Name", prefix="ALP")
    with pytest.raises(IntegrityError):
        await proj_repo.create(p_dup_prefix)

    # Same prefix in Guild B must succeed (composite constraint uq_project_guild_prefix)
    p_guild_b = Project(guild_id=guild_b, name="Guild B Project", prefix="ALP")
    created_guild_b = await proj_repo.create(p_guild_b)
    assert created_guild_b.id is not None
