from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.adapters.db.repos import (
    PostgresGuildLeadRoleRepository,
    PostgresGuildLeadUserRepository,
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserCalendarTokenRepo,
    PostgresUserPreferenceRepo,
)
from src.ports.repositories import (
    IGuildLeadRoleRepository,
    IGuildLeadUserRepository,
    IOutboxRepo,
    IProjectRepo,
    ISquadRepo,
    ITaskRepo,
    IUserCalendarTokenRepo,
    IUserPreferenceRepo,
)
from src.ports.unit_of_work import IUnitOfWork

logger = logging.getLogger("dgg_pm.adapters.uow")


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy async implementation of the Unit of Work pattern."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._bind_repos(session_factory)

    def _bind_repos(self, session_or_factory: AsyncSession | async_sessionmaker[AsyncSession]) -> None:
        self.tasks: ITaskRepo = PostgresTaskRepo(session_or_factory)
        self.projects: IProjectRepo = PostgresProjectRepo(session_or_factory)
        self.squads: ISquadRepo = PostgresSquadRepo(session_or_factory)
        self.outbox: IOutboxRepo = PostgresOutboxRepo(session_or_factory)
        self.user_prefs: IUserPreferenceRepo = PostgresUserPreferenceRepo(session_or_factory)
        self.guild_lead_roles: IGuildLeadRoleRepository = PostgresGuildLeadRoleRepository(session_or_factory)
        self.guild_lead_users: IGuildLeadUserRepository = PostgresGuildLeadUserRepository(session_or_factory)
        self.calendar_tokens: IUserCalendarTokenRepo = PostgresUserCalendarTokenRepo(session_or_factory)

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self._bind_repos(self._session)
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
            self._bind_repos(self._session_factory)

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
