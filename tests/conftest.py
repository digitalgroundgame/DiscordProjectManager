from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.adapters.db.postgres_repo import (
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserPreferenceRepo,
)
from src.adapters.db.tables import Base
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.rendering import PillowTechTreeRenderer
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService
from src.services.workspace_service import WorkspaceService

# In-memory SQLite async engine for lightning-fast testing
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


class QueryCounter:
    """Intercepts and counts SQLAlchemy SQL query emissions."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine.sync_engine if hasattr(engine, "sync_engine") else engine
        self.count: int = 0
        self.statements: list[str] = []
        self._listening: bool = False

    def _callback(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)

    def start(self) -> QueryCounter:
        if not self._listening:
            event.listen(self._engine, "before_cursor_execute", self._callback)
            self._listening = True
        return self

    def stop(self) -> None:
        if self._listening:
            event.remove(self._engine, "before_cursor_execute", self._callback)
            self._listening = False

    def clear(self) -> None:
        self.count = 0
        self.statements.clear()

    def __enter__(self) -> QueryCounter:
        self.clear()
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    async def __aenter__(self) -> QueryCounter:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)

    def assert_max_queries(self, max_queries: int) -> None:
        if self.count > max_queries:
            formatted = "\n".join(f"  [{i + 1}] {s.strip()}" for i, s in enumerate(self.statements))
            raise AssertionError(f"Expected at most {max_queries} SQL queries, but executed {self.count}:\n{formatted}")

    def assert_query_count(self, expected: int) -> None:
        if self.count != expected:
            formatted = "\n".join(f"  [{i + 1}] {s.strip()}" for i, s in enumerate(self.statements))
            raise AssertionError(f"Expected exactly {expected} SQL queries, but executed {self.count}:\n{formatted}")


@pytest.fixture(scope="function")
def query_counter(async_engine: AsyncEngine) -> QueryCounter:
    """Fixture that intercepts and counts SQL queries within a test."""
    counter = QueryCounter(async_engine)
    yield counter
    counter.stop()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_engine() -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_db(async_engine: AsyncEngine):
    yield
    async with async_engine.begin() as conn:
        for tbl in reversed(Base.metadata.sorted_tables):
            await conn.execute(delete(tbl))


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def repos(db_session: AsyncSession):
    from src.adapters.db.postgres_repo import (
        PostgresGuildLeadRoleRepository,
        PostgresGuildLeadUserRepository,
        PostgresUserCalendarTokenRepo,
    )

    task_repo = PostgresTaskRepo(db_session)
    project_repo = PostgresProjectRepo(db_session)
    squad_repo = PostgresSquadRepo(db_session)
    outbox_repo = PostgresOutboxRepo(db_session)
    user_repo = PostgresUserPreferenceRepo(db_session)
    guild_lead_role_repo = PostgresGuildLeadRoleRepository(db_session)
    guild_lead_user_repo = PostgresGuildLeadUserRepository(db_session)
    calendar_token_repo = PostgresUserCalendarTokenRepo(db_session)
    return {
        "task": task_repo,
        "project": project_repo,
        "squad": squad_repo,
        "outbox": outbox_repo,
        "user": user_repo,
        "guild_lead_role": guild_lead_role_repo,
        "guild_lead_user": guild_lead_user_repo,
        "calendar_token": calendar_token_repo,
    }


@pytest_asyncio.fixture(scope="function")
async def services(repos, async_engine: AsyncEngine):
    from src.services.calendar_service import CalendarService

    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow = SqlAlchemyUnitOfWork(session_factory)
    project_service = ProjectService(repos["project"], uow=uow)
    squad_service = SquadService(repos["squad"], uow=uow)
    outbox_service = OutboxService(repos["outbox"])
    task_service = TaskService(
        repos["task"],
        project_service,
        outbox_service,
        uow=uow,
        tree_renderer=PillowTechTreeRenderer(),
    )
    user_service = UserService(repos["user"], uow=uow)
    calendar_service = CalendarService(repos["calendar_token"], repos["task"], uow=uow)
    workspace_service = WorkspaceService(
        project_repo=repos["project"],
        task_repo=repos["task"],
        squad_repo=repos["squad"],
        uow=uow,
    )
    return {
        "project": project_service,
        "squad": squad_service,
        "outbox": outbox_service,
        "task": task_service,
        "user": user_service,
        "calendar": calendar_service,
        "workspace": workspace_service,
        "uow": uow,
    }
