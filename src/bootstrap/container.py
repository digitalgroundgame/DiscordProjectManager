from __future__ import annotations

import logging
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.adapters.api.app import api_app
from src.adapters.db.observability_adapter import SqlAlchemySystemMetricsAdapter
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
from src.adapters.db.session import (
    close_db,
    create_engine_and_session_factory,
    init_db,
    set_engine_and_session_factory,
)
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.adapters.discord_bot.actor_mapper import DiscordMemberDirectory
from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.discord_notifier import DiscordNotifier
from src.adapters.discord_bot.menu_manager import MenuSessionManager, set_menu_manager
from src.adapters.rendering import PillowTechTreeRenderer
from src.adapters.worker.outbox_worker import OutboxWorker
from src.config import Settings, get_settings, set_settings
from src.ports.member_directory import MemberDirectoryPort
from src.services.auth_service import AuthService
from src.services.calendar_service import CalendarService
from src.services.observability_service import ObservabilityService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService
from src.services.workspace_service import WorkspaceService

logger = logging.getLogger("dgg_pm.bootstrap")


class AppContainer:
    """Composition Root holding and managing application dependencies and lifecycles."""

    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        task_repo: PostgresTaskRepo,
        project_repo: PostgresProjectRepo,
        squad_repo: PostgresSquadRepo,
        outbox_repo: PostgresOutboxRepo,
        user_pref_repo: PostgresUserPreferenceRepo,
        guild_lead_role_repo: PostgresGuildLeadRoleRepository,
        guild_lead_user_repo: PostgresGuildLeadUserRepository,
        calendar_token_repo: PostgresUserCalendarTokenRepo,
        uow: SqlAlchemyUnitOfWork,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        tree_renderer: PillowTechTreeRenderer,
        metrics_adapter: SqlAlchemySystemMetricsAdapter,
        project_service: ProjectService,
        squad_service: SquadService,
        outbox_service: OutboxService,
        task_service: TaskService,
        user_service: UserService,
        calendar_service: CalendarService,
        observability_service: ObservabilityService,
        menu_manager: MenuSessionManager,
        bot: DggPmBot,
        notifier: DiscordNotifier,
        worker: OutboxWorker,
        api_app: FastAPI,
        uvicorn_server: uvicorn.Server,
        auth_service: AuthService | None = None,
        member_directory: MemberDirectoryPort | None = None,
        workspace_service: WorkspaceService | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.session_factory = session_factory
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.squad_repo = squad_repo
        self.outbox_repo = outbox_repo
        self.user_pref_repo = user_pref_repo
        self.guild_lead_role_repo = guild_lead_role_repo
        self.guild_lead_user_repo = guild_lead_user_repo
        self.calendar_token_repo = calendar_token_repo
        self.uow = uow
        self.uow_factory = uow_factory
        self.tree_renderer = tree_renderer
        self.metrics_adapter = metrics_adapter
        self.project_service = project_service
        self.squad_service = squad_service
        self.outbox_service = outbox_service
        self.task_service = task_service
        self.user_service = user_service
        self.calendar_service = calendar_service
        self.observability_service = observability_service
        self.workspace_service = workspace_service
        self.menu_manager = menu_manager
        self.bot = bot
        self.notifier = notifier
        self.worker = worker
        self.api_app = api_app
        self.uvicorn_server = uvicorn_server
        self.auth_service = auth_service
        self.member_directory = member_directory

    async def init_db(self, max_retries: int = 15, retry_interval: float = 1.0) -> None:
        """Initialize database schema with migrations if enabled."""
        await init_db(
            engine=self.engine,
            auto_run_migrations=self.settings.AUTO_RUN_MIGRATIONS,
            max_retries=max_retries,
            retry_interval=retry_interval,
        )

    async def close(self) -> None:
        """Gracefully release and dispose of all managed resources."""
        logger.info("Disposing container resources...")
        self.worker.stop()
        if hasattr(self.bot, "is_closed") and not self.bot.is_closed():
            await self.bot.close()
        await close_db(self.engine)
        logger.info("Container resources cleanly disposed.")


def create_container(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    menu_manager: MenuSessionManager | None = None,
) -> AppContainer:
    """Build and wire the explicit application composition root."""
    resolved_settings = settings or get_settings()
    set_settings(resolved_settings)

    if engine is None or session_factory is None:
        eng, factory = create_engine_and_session_factory(
            database_url=resolved_settings.DATABASE_URL,
            echo=resolved_settings.DEBUG,
            pool_pre_ping=True,
        )
    else:
        eng, factory = engine, session_factory

    set_engine_and_session_factory(eng, factory)

    resolved_menu_manager = menu_manager or MenuSessionManager()
    set_menu_manager(resolved_menu_manager)

    # 1. Driven Repositories & Adapters
    task_repo = PostgresTaskRepo(factory)
    project_repo = PostgresProjectRepo(factory)
    squad_repo = PostgresSquadRepo(factory)
    outbox_repo = PostgresOutboxRepo(factory)
    user_pref_repo = PostgresUserPreferenceRepo(factory)
    guild_lead_role_repo = PostgresGuildLeadRoleRepository(factory)
    guild_lead_user_repo = PostgresGuildLeadUserRepository(factory)
    calendar_token_repo = PostgresUserCalendarTokenRepo(factory)

    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)  # noqa: E731
    uow = uow_factory()

    tree_renderer = PillowTechTreeRenderer()
    metrics_adapter = SqlAlchemySystemMetricsAdapter(factory)

    # 2. Application Services
    project_service = ProjectService(project_repo, uow=uow)
    squad_service = SquadService(squad_repo, uow=uow)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(
        task_repo,
        project_service,
        outbox_service,
        uow=uow,
        tree_renderer=tree_renderer,
    )
    user_service = UserService(user_pref_repo, uow=uow)
    calendar_service = CalendarService(
        calendar_token_repo,
        task_repo,
        uow=uow,
        base_url=resolved_settings.PUBLIC_BASE_URL,
    )
    observability_service = ObservabilityService(metrics_adapter)
    workspace_service = WorkspaceService(
        project_repo=project_repo,
        task_repo=task_repo,
        squad_repo=squad_repo,
        uow=uow,
    )

    # 3. FastAPI App Wiring
    api_app.state.calendar_service = calendar_service
    api_app.state.observability_service = observability_service
    api_app.state.settings = resolved_settings

    # 4. Discord Bot, Member Directory & Auth Service Wiring
    bot = DggPmBot(
        task_service=task_service,
        project_service=project_service,
        squad_service=squad_service,
        user_service=user_service,
        calendar_service=calendar_service,
        outbox_service=outbox_service,
        guild_lead_role_repo=guild_lead_role_repo,
        guild_lead_user_repo=guild_lead_user_repo,
        settings=resolved_settings,
        menu_manager=resolved_menu_manager,
    )
    member_directory = DiscordMemberDirectory(bot)
    auth_service = AuthService(
        project_service=project_service,
        squad_service=squad_service,
        guild_lead_role_repo=guild_lead_role_repo,
        guild_lead_user_repo=guild_lead_user_repo,
        member_directory=member_directory,
        uow=uow,
    )
    bot.auth_service = auth_service
    bot.member_directory = member_directory
    if bot.workspace:
        bot.workspace.auth_service = auth_service
    if bot.project_workspace:
        bot.project_workspace.auth_service = auth_service

    notifier = DiscordNotifier(
        bot,
        user_service=user_service,
        workspace=bot.workspace,
        task_service=task_service,
    )

    # 5. Outbox Worker Wiring
    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=notifier,
        poll_interval=resolved_settings.OUTBOX_POLL_INTERVAL_SECONDS,
        batch_size=resolved_settings.OUTBOX_BATCH_SIZE,
        max_retries=resolved_settings.OUTBOX_MAX_RETRIES,
        backoff_cap_seconds=resolved_settings.OUTBOX_BACKOFF_CAP_SECONDS,
        max_retention_hours=resolved_settings.OUTBOX_MAX_RETENTION_HOURS,
    )

    # 6. Uvicorn Server Configuration
    uvicorn_config = uvicorn.Config(
        app=api_app,
        host=resolved_settings.API_HOST,
        port=resolved_settings.API_PORT,
        log_level="warning",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    return AppContainer(
        settings=resolved_settings,
        engine=eng,
        session_factory=factory,
        task_repo=task_repo,
        project_repo=project_repo,
        squad_repo=squad_repo,
        outbox_repo=outbox_repo,
        user_pref_repo=user_pref_repo,
        guild_lead_role_repo=guild_lead_role_repo,
        guild_lead_user_repo=guild_lead_user_repo,
        calendar_token_repo=calendar_token_repo,
        uow=uow,
        uow_factory=uow_factory,
        tree_renderer=tree_renderer,
        metrics_adapter=metrics_adapter,
        project_service=project_service,
        squad_service=squad_service,
        outbox_service=outbox_service,
        task_service=task_service,
        user_service=user_service,
        calendar_service=calendar_service,
        observability_service=observability_service,
        menu_manager=resolved_menu_manager,
        bot=bot,
        notifier=notifier,
        worker=worker,
        api_app=api_app,
        uvicorn_server=uvicorn_server,
        auth_service=auth_service,
        member_directory=member_directory,
        workspace_service=workspace_service,
    )
