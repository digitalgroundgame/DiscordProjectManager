from __future__ import annotations

import logging
from contextvars import ContextVar
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


class _UnitOfWorkTransactionContext:
    """Task-local transaction state holding active session, repos, and recursion depth."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.depth: int = 1
        self.rollback_requested: bool = False
        self.tasks: ITaskRepo = PostgresTaskRepo(session)
        self.projects: IProjectRepo = PostgresProjectRepo(session)
        self.squads: ISquadRepo = PostgresSquadRepo(session)
        self.outbox: IOutboxRepo = PostgresOutboxRepo(session)
        self.user_prefs: IUserPreferenceRepo = PostgresUserPreferenceRepo(session)
        self.guild_lead_roles: IGuildLeadRoleRepository = PostgresGuildLeadRoleRepository(session)
        self.guild_lead_users: IGuildLeadUserRepository = PostgresGuildLeadUserRepository(session)
        self.calendar_tokens: IUserCalendarTokenRepo = PostgresUserCalendarTokenRepo(session)


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy async implementation of the Unit of Work pattern.

    Uses task-local ContextVar isolation so concurrent coroutines receive distinct
    sessions and nested 'async with uow:' blocks participate in a single re-entrant transaction.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._context_var: ContextVar[_UnitOfWorkTransactionContext | None] = ContextVar(
            f"uow_ctx_{id(self)}", default=None
        )
        # Fallback repositories bound to session_factory when accessed outside 'async with uow:'
        self._fallback_tasks: ITaskRepo = PostgresTaskRepo(session_factory)
        self._fallback_projects: IProjectRepo = PostgresProjectRepo(session_factory)
        self._fallback_squads: ISquadRepo = PostgresSquadRepo(session_factory)
        self._fallback_outbox: IOutboxRepo = PostgresOutboxRepo(session_factory)
        self._fallback_user_prefs: IUserPreferenceRepo = PostgresUserPreferenceRepo(session_factory)
        self._fallback_guild_lead_roles: IGuildLeadRoleRepository = PostgresGuildLeadRoleRepository(session_factory)
        self._fallback_guild_lead_users: IGuildLeadUserRepository = PostgresGuildLeadUserRepository(session_factory)
        self._fallback_calendar_tokens: IUserCalendarTokenRepo = PostgresUserCalendarTokenRepo(session_factory)

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @property
    def tasks(self) -> ITaskRepo:
        ctx = self._context_var.get()
        return ctx.tasks if ctx is not None else self._fallback_tasks

    @tasks.setter
    def tasks(self, value: ITaskRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.tasks = value
        else:
            self._fallback_tasks = value

    @property
    def projects(self) -> IProjectRepo:
        ctx = self._context_var.get()
        return ctx.projects if ctx is not None else self._fallback_projects

    @projects.setter
    def projects(self, value: IProjectRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.projects = value
        else:
            self._fallback_projects = value

    @property
    def squads(self) -> ISquadRepo:
        ctx = self._context_var.get()
        return ctx.squads if ctx is not None else self._fallback_squads

    @squads.setter
    def squads(self, value: ISquadRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.squads = value
        else:
            self._fallback_squads = value

    @property
    def outbox(self) -> IOutboxRepo:
        ctx = self._context_var.get()
        return ctx.outbox if ctx is not None else self._fallback_outbox

    @outbox.setter
    def outbox(self, value: IOutboxRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.outbox = value
        else:
            self._fallback_outbox = value

    @property
    def user_prefs(self) -> IUserPreferenceRepo:
        ctx = self._context_var.get()
        return ctx.user_prefs if ctx is not None else self._fallback_user_prefs

    @user_prefs.setter
    def user_prefs(self, value: IUserPreferenceRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.user_prefs = value
        else:
            self._fallback_user_prefs = value

    @property
    def guild_lead_roles(self) -> IGuildLeadRoleRepository:
        ctx = self._context_var.get()
        return ctx.guild_lead_roles if ctx is not None else self._fallback_guild_lead_roles

    @guild_lead_roles.setter
    def guild_lead_roles(self, value: IGuildLeadRoleRepository) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.guild_lead_roles = value
        else:
            self._fallback_guild_lead_roles = value

    @property
    def guild_lead_users(self) -> IGuildLeadUserRepository:
        ctx = self._context_var.get()
        return ctx.guild_lead_users if ctx is not None else self._fallback_guild_lead_users

    @guild_lead_users.setter
    def guild_lead_users(self, value: IGuildLeadUserRepository) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.guild_lead_users = value
        else:
            self._fallback_guild_lead_users = value

    @property
    def calendar_tokens(self) -> IUserCalendarTokenRepo:
        ctx = self._context_var.get()
        return ctx.calendar_tokens if ctx is not None else self._fallback_calendar_tokens

    @calendar_tokens.setter
    def calendar_tokens(self, value: IUserCalendarTokenRepo) -> None:
        ctx = self._context_var.get()
        if ctx is not None:
            ctx.calendar_tokens = value
        else:
            self._fallback_calendar_tokens = value

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        ctx = self._context_var.get()
        if ctx is None:
            session = self._session_factory()
            new_ctx = _UnitOfWorkTransactionContext(session)
            self._context_var.set(new_ctx)
        else:
            ctx.depth += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        ctx = self._context_var.get()
        if ctx is None:
            return

        ctx.depth -= 1
        if exc_type is not None:
            ctx.rollback_requested = True

        if ctx.depth <= 0:
            try:
                if ctx.rollback_requested:
                    logger.debug("Rolling back unit of work due to exception: %s", exc_val)
                    await ctx.session.rollback()
                else:
                    await ctx.session.commit()
            finally:
                await ctx.session.close()
                self._context_var.set(None)

    async def commit(self) -> None:
        ctx = self._context_var.get()
        if ctx is not None and ctx.session is not None:
            await ctx.session.commit()

    async def rollback(self) -> None:
        ctx = self._context_var.get()
        if ctx is not None and ctx.session is not None:
            ctx.rollback_requested = True
            await ctx.session.rollback()

    @property
    def session(self) -> AsyncSession:
        ctx = self._context_var.get()
        if ctx is None or ctx.session is None:
            raise RuntimeError("UnitOfWork session accessed outside of an active async context ('async with uow:').")
        return ctx.session
