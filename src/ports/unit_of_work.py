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
