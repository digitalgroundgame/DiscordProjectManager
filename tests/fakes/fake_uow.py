"""In-memory fake implementation of IUnitOfWork for application use-case unit tests."""

from __future__ import annotations

from typing import Any

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


class FakeUnitOfWork(IUnitOfWork):
    """In-memory Unit of Work test double.

    Manages transaction lifecycle (committed, rolled_back) and provides
    injected or stubbed repositories without requiring SQLAlchemy or SQLite.
    """

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

        self.committed: bool = False
        self.rolled_back: bool = False
        self.is_active: bool = False

    async def __aenter__(self) -> FakeUnitOfWork:
        self.is_active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.is_active = False
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
