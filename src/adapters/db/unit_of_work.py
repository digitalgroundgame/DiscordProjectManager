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


class _ContextRepoDescriptor:
    """Descriptor resolving the active task-local transaction repository or fallback instance."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._fallback_attr = f"_fallback_{name}"

    def __get__(self, instance: SqlAlchemyUnitOfWork | None, owner: Any = None) -> Any:
        if instance is None:
            return self
        ctx = instance._context_var.get()
        if ctx is not None:
            return getattr(ctx, self._name)
        return getattr(instance, self._fallback_attr)

    def __set__(self, instance: SqlAlchemyUnitOfWork, value: Any) -> None:
        ctx = instance._context_var.get()
        if ctx is not None:
            setattr(ctx, self._name, value)
        else:
            setattr(instance, self._fallback_attr, value)


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """SQLAlchemy async implementation of the Unit of Work pattern.

    Uses task-local ContextVar isolation so concurrent coroutines receive distinct
    sessions and nested 'async with uow:' blocks participate in a single re-entrant transaction.
    """

    tasks: ITaskRepo = _ContextRepoDescriptor("tasks")  # type: ignore[assignment]
    projects: IProjectRepo = _ContextRepoDescriptor("projects")  # type: ignore[assignment]
    squads: ISquadRepo = _ContextRepoDescriptor("squads")  # type: ignore[assignment]
    outbox: IOutboxRepo = _ContextRepoDescriptor("outbox")  # type: ignore[assignment]
    user_prefs: IUserPreferenceRepo = _ContextRepoDescriptor("user_prefs")  # type: ignore[assignment]
    guild_lead_roles: IGuildLeadRoleRepository = _ContextRepoDescriptor("guild_lead_roles")  # type: ignore[assignment]
    guild_lead_users: IGuildLeadUserRepository = _ContextRepoDescriptor("guild_lead_users")  # type: ignore[assignment]
    calendar_tokens: IUserCalendarTokenRepo = _ContextRepoDescriptor("calendar_tokens")  # type: ignore[assignment]

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
