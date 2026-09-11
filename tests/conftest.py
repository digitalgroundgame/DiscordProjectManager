import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.adapters.db.postgres_repo import (
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresTaskRepo,
    PostgresTeamRepo,
    PostgresUserPreferenceRepo,
)
from src.adapters.db.tables import Base
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.task_service import TaskService
from src.services.team_service import TeamService
from src.services.user_service import UserService

# In-memory SQLite async engine for lightning-fast testing
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


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
    task_repo = PostgresTaskRepo(db_session)
    project_repo = PostgresProjectRepo(db_session)
    team_repo = PostgresTeamRepo(db_session)
    outbox_repo = PostgresOutboxRepo(db_session)
    user_repo = PostgresUserPreferenceRepo(db_session)
    return {
        "task": task_repo,
        "project": project_repo,
        "team": team_repo,
        "outbox": outbox_repo,
        "user": user_repo,
    }


@pytest_asyncio.fixture(scope="function")
async def services(repos, async_engine: AsyncEngine):
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow = SqlAlchemyUnitOfWork(session_factory)
    project_service = ProjectService(repos["project"])
    team_service = TeamService(repos["team"])
    outbox_service = OutboxService(repos["outbox"])
    task_service = TaskService(repos["task"], project_service, outbox_service, uow=uow)
    user_service = UserService(repos["user"])
    return {
        "project": project_service,
        "team": team_service,
        "outbox": outbox_service,
        "task": task_service,
        "user": user_service,
        "uow": uow,
    }
