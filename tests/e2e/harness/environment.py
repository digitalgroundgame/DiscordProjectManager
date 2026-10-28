"""E2E Test Environment wiring the full dgg-pm application stack.

Wires SQLite async session, real repositories, domain services, Unit of Work,
DggPmBot, PmCog, DiscordTaskWorkspaceAdapter, OutboxWorker, DiscordNotifier,
and the stateful SimulatedGuild server twin.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.adapters.db.postgres_repo import (
    PostgresGuildLeadRoleRepository,
    PostgresGuildLeadUserRepository,
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserCalendarTokenRepo,
    PostgresUserPreferenceRepo,
)
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.discord_notifier import DiscordNotifier
from src.adapters.rendering import PillowTechTreeRenderer
from src.adapters.worker.outbox_worker import OutboxWorker
from src.config import Settings
from src.services.calendar_service import CalendarService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService
from tests.e2e.harness.discord_twin import (
    SimulatedGuild,
    SimulatedMember,
    SimulatedRole,
)
from tests.e2e.harness.interaction_driver import UserJourneyActor

logger = logging.getLogger("dgg_pm.e2e.env")


class E2ETestEnvironment:
    """Full application runtime composition root for in-process E2E tests."""

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        guild_id: int = 1543430283250901023,
    ):
        self.engine = engine
        self.session_factory = session_factory
        self.guild_id = guild_id

        # Repositories & Unit of Work
        self.uow = SqlAlchemyUnitOfWork(session_factory)
        self.task_repo = PostgresTaskRepo(session_factory)
        self.project_repo = PostgresProjectRepo(session_factory)
        self.squad_repo = PostgresSquadRepo(session_factory)
        self.outbox_repo = PostgresOutboxRepo(session_factory)
        self.user_repo = PostgresUserPreferenceRepo(session_factory)
        self.guild_lead_role_repo = PostgresGuildLeadRoleRepository(session_factory)
        self.guild_lead_user_repo = PostgresGuildLeadUserRepository(session_factory)
        self.calendar_token_repo = PostgresUserCalendarTokenRepo(session_factory)

        # Services
        self.tree_renderer = PillowTechTreeRenderer()
        self.project_service = ProjectService(self.project_repo, uow=self.uow)
        self.squad_service = SquadService(self.squad_repo, uow=self.uow)
        self.outbox_service = OutboxService(self.outbox_repo)
        self.task_service = TaskService(
            self.task_repo,
            self.project_service,
            self.outbox_service,
            uow=self.uow,
            tree_renderer=self.tree_renderer,
        )
        self.user_service = UserService(self.user_repo, uow=self.uow)
        self.calendar_service = CalendarService(self.calendar_token_repo, self.task_repo, uow=self.uow)

        # Stateful Discord Server Twin
        self.guild = SimulatedGuild(id=self.guild_id, name="Test PM Server")

        # Bot and workspace adapters
        self.settings = Settings(
            ENVIRONMENT="test",
            DISCORD_GUILD_ID=self.guild_id,
            SYNC_COMMANDS_ON_STARTUP=False,
            OUTBOX_POLL_INTERVAL_SECONDS=0.05,
        )

        self.bot = DggPmBot(
            task_service=self.task_service,
            project_service=self.project_service,
            squad_service=self.squad_service,
            user_service=self.user_service,
            calendar_service=self.calendar_service,
            outbox_service=self.outbox_service,
            guild_lead_role_repo=self.guild_lead_role_repo,
            guild_lead_user_repo=self.guild_lead_user_repo,
            settings=self.settings,
        )

        # Bind bot to simulated server twin
        self.bot._guilds = {self.guild.id: self.guild}
        self.bot.get_guild = lambda gid: self.guild if gid == self.guild.id else None

        async def _fetch_guild(gid: int) -> SimulatedGuild:
            if gid == self.guild.id:
                return self.guild
            raise RuntimeError(f"Guild {gid} not found")

        self.bot.fetch_guild = _fetch_guild
        self.bot.get_channel = self.guild.get_channel
        self.bot.fetch_channel = self.guild.fetch_channel

        def _get_user(uid: int) -> SimulatedMember | None:
            return self.guild.get_member(uid)

        async def _fetch_user(uid: int) -> SimulatedMember:
            m = self.guild.get_member(uid)
            if m:
                return m
            raise discord.NotFound(None, f"User {uid} not found")

        self.bot.get_user = _get_user
        self.bot.fetch_user = _fetch_user

        self.auth_service = self.bot.auth_service
        self.workspace = self.bot.workspace
        self.project_workspace = self.bot.project_workspace

        # Outbox Worker & Discord Notifier
        self.notifier = DiscordNotifier(
            bot=self.bot,
            user_service=self.user_service,
            workspace=self.workspace,
            task_service=self.task_service,
        )
        self.worker = OutboxWorker(
            outbox_repo=self.outbox_repo,
            notifier=self.notifier,
            poll_interval=0.05,
            batch_size=20,
        )
        self.worker._running = True

    async def initialize(self) -> None:
        """Initializes bot cogs and command tree."""
        await self.bot.setup_hook()

    async def drain_outbox(self, max_batches: int = 20) -> int:
        """Processes and delivers pending outbox events through worker -> notifier."""
        total = 0
        for _ in range(max_batches):
            count = await self.worker.process_batch()
            total += count
            if count == 0:
                break
        return total

    def create_actor(
        self,
        name: str,
        *,
        roles: list[SimulatedRole] | None = None,
        is_admin: bool = False,
        default_channel: Any = None,
    ) -> UserJourneyActor:
        """Creates a simulated user persona with credentials bound to the test guild."""
        member = self.guild.create_member(
            name=name,
            roles=roles,
            is_admin=is_admin,
        )
        return UserJourneyActor(
            user=member,
            guild=self.guild,
            default_channel=default_channel,
            env=self,
        )
