from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.db.postgres_repo import (
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserPreferenceRepo,
)
from src.ports.repositories import (
    IOutboxRepo,
    IProjectRepo,
    ISquadRepo,
    ITaskRepo,
    ITeamRepo,
    IUserPreferenceRepo,
)
from src.ports.unit_of_work import IUnitOfWork

logger = logging.getLogger("dgg_pm.adapters.uow")


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy async implementation of the Unit of Work pattern."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self.tasks: ITaskRepo = None  # type: ignore[assignment]
        self.projects: IProjectRepo = None  # type: ignore[assignment]
        self.squads: ISquadRepo = None  # type: ignore[assignment]
        self.teams: ITeamRepo = None  # type: ignore[assignment]
        self.outbox: IOutboxRepo = None  # type: ignore[assignment]
        self.user_prefs: IUserPreferenceRepo = None  # type: ignore[assignment]

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.tasks = PostgresTaskRepo(self._session)
        self.projects = PostgresProjectRepo(self._session)
        self.squads = PostgresSquadRepo(self._session)
        self.teams = self.squads
        self.outbox = PostgresOutboxRepo(self._session)
        self.user_prefs = PostgresUserPreferenceRepo(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._session is None:
            return

        try:
            if exc_type is not None:
                logger.debug("Rolling back unit of work due to exception: %s", exc_val)
                await self.rollback()
            else:
                await self.commit()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        if self._session is not None:
            await self._session.commit()

    async def rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork session accessed outside of an active async context ('async with uow:').")
        return self._session
