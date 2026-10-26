from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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


class IUnitOfWork(ABC):
    """Port defining the Unit of Work interface for managing atomic database transactions."""

    tasks: ITaskRepo
    projects: IProjectRepo
    squads: ISquadRepo
    outbox: IOutboxRepo
    user_prefs: IUserPreferenceRepo
    guild_lead_roles: IGuildLeadRoleRepository
    guild_lead_users: IGuildLeadUserRepository
    calendar_tokens: IUserCalendarTokenRepo

    @abstractmethod
    async def __aenter__(self) -> IUnitOfWork:
        """Enters the transaction context."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exits the transaction context, committing on success or rolling back on error."""

    @abstractmethod
    async def commit(self) -> None:
        """Commits the active transaction."""

    @abstractmethod
    async def rollback(self) -> None:
        """Rolls back the active transaction."""

    @property
    @abstractmethod
    def session(self) -> Any:
        """Returns the active transaction session object (e.g. AsyncSession)."""


class NullUnitOfWork(IUnitOfWork):
    """Fallback Unit of Work when no transactional Unit of Work is injected."""

    def __init__(
        self,
        tasks: ITaskRepo | None = None,
        projects: IProjectRepo | None = None,
        squads: ISquadRepo | None = None,
        outbox: IOutboxRepo | None = None,
        user_prefs: IUserPreferenceRepo | None = None,
        guild_lead_roles: IGuildLeadRoleRepository | None = None,
        guild_lead_users: IGuildLeadUserRepository | None = None,
        calendar_tokens: IUserCalendarTokenRepo | None = None,
    ) -> None:
        self.tasks = tasks  # type: ignore[assignment]
        self.projects = projects  # type: ignore[assignment]
        self.squads = squads  # type: ignore[assignment]
        self.outbox = outbox  # type: ignore[assignment]
        self.user_prefs = user_prefs  # type: ignore[assignment]
        self.guild_lead_roles = guild_lead_roles  # type: ignore[assignment]
        self.guild_lead_users = guild_lead_users  # type: ignore[assignment]
        self.calendar_tokens = calendar_tokens  # type: ignore[assignment]

    async def __aenter__(self) -> NullUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    @property
    def session(self) -> Any:
        return None
