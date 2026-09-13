import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root is in sys.path when executed directly as a script
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import discord  # noqa: E402
import uvicorn  # noqa: E402

from src.adapters.api.app import api_app  # noqa: E402
from src.adapters.db.postgres_repo import (  # noqa: E402
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserPreferenceRepo,
)
from src.adapters.db.session import async_session_factory, close_db, init_db  # noqa: E402
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.adapters.discord_bot.bot import DggPmBot  # noqa: E402
from src.adapters.discord_bot.discord_notifier import DiscordNotifier  # noqa: E402
from src.adapters.discord_bot.gateway_supervisor import start_bot_with_retry  # noqa: E402
from src.adapters.worker.outbox_worker import OutboxWorker  # noqa: E402
from src.config import settings  # noqa: E402
from src.services.outbox_service import OutboxService  # noqa: E402
from src.services.project_service import ProjectService  # noqa: E402
from src.services.squad_service import SquadService  # noqa: E402
from src.services.task_service import TaskService  # noqa: E402
from src.services.user_service import UserService  # noqa: E402

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dgg_pm.main")


async def run_app() -> None:
    logger.info("Initializing dgg-pm platform...")

    # 1. Initialize Database schema
    await init_db()
    logger.info("Database schema initialized.")

    # 2. Wire Hexagonal Repositories & Services with Session Pool Factory
    task_repo = PostgresTaskRepo(async_session_factory)
    project_repo = PostgresProjectRepo(async_session_factory)
    squad_repo = PostgresSquadRepo(async_session_factory)
    outbox_repo = PostgresOutboxRepo(async_session_factory)
    user_pref_repo = PostgresUserPreferenceRepo(async_session_factory)

    uow = SqlAlchemyUnitOfWork(async_session_factory)
    project_service = ProjectService(project_repo)
    squad_service = SquadService(squad_repo)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)
    user_service = UserService(user_pref_repo)

    # 3. Wire Discord Bot & Notifier
    bot = DggPmBot(
        task_service=task_service,
        project_service=project_service,
        squad_service=squad_service,
        user_service=user_service,
        outbox_service=outbox_service,
    )
    notifier = DiscordNotifier(
        bot,
        user_service=user_service,
        workspace=bot.workspace,
        task_service=task_service,
    )

    # 4. Wire Outbox Worker
    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=notifier,
        poll_interval=settings.OUTBOX_POLL_INTERVAL_SECONDS,
        batch_size=settings.OUTBOX_BATCH_SIZE,
        max_retries=settings.OUTBOX_MAX_RETRIES,
        backoff_cap_seconds=settings.OUTBOX_BACKOFF_CAP_SECONDS,
        max_retention_hours=settings.OUTBOX_MAX_RETENTION_HOURS,
    )

    # 5. Configure FastAPI Server
    uvicorn_config = uvicorn.Config(
        app=api_app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="warning",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    # 6. Lifecycle Events and Shutdown Handler
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    bg_tasks: set[asyncio.Task] = set()

    def handle_signal():
        logger.info("Received termination signal. Initiating graceful shutdown...")
        worker.stop()
        uvicorn_server.should_exit = True
        stop_event.set()
        if not bot.is_closed():
            close_task = asyncio.create_task(bot.close())
            bg_tasks.add(close_task)
            close_task.add_done_callback(bg_tasks.discard)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows support fallback
            pass

    # 7. Gather concurrent async tasks
    tasks = [
        asyncio.create_task(uvicorn_server.serve(), name="FastAPI-Health-Server"),
        asyncio.create_task(worker.start(), name="Outbox-Worker"),
    ]

    if settings.DISCORD_BOT_TOKEN:
        tasks.append(
            asyncio.create_task(
                start_bot_with_retry(
                    bot=bot,
                    token=settings.DISCORD_BOT_TOKEN,
                    stop_event=stop_event,
                    initial_delay=settings.GATEWAY_RETRY_INITIAL_DELAY_SECONDS,
                    max_delay=settings.GATEWAY_RETRY_MAX_DELAY_SECONDS,
                    backoff_factor=settings.GATEWAY_RETRY_BACKOFF_FACTOR,
                    jitter=settings.GATEWAY_RETRY_JITTER,
                    max_retries=settings.GATEWAY_MAX_RETRIES,
                ),
                name="Discord-Bot-Gateway",
            )
        )
    else:
        logger.warning("DISCORD_BOT_TOKEN is not set. Bot Gateway will not start. (API & Worker running)")

    has_fatal_error = False
    try:
        done, _pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for t in done:
            if t.exception():
                has_fatal_error = True
                exc = t.exception()
                if isinstance(exc, discord.errors.Forbidden) and getattr(exc, "code", None) == 50001:
                    logger.critical(
                        "Service task '%s' terminated: Bot is missing access to the Discord guild (code 50001).\n"
                        "Check your bot permissions and verify DISCORD_GUILD_ID.",
                        t.get_name(),
                    )
                elif isinstance(exc, discord.errors.LoginFailure):
                    logger.critical(
                        "Service task '%s' terminated: Invalid Discord Bot Token (LoginFailure).\n"
                        "Please verify DISCORD_BOT_TOKEN in your environment or .env configuration.",
                        t.get_name(),
                    )
                elif isinstance(exc, discord.errors.RateLimited):
                    logger.critical(
                        "Service task '%s' terminated: Discord API Rate Limited (retry_after=%s).\n"
                        "Backing off to avoid Cloudflare IP bans and Gateway session exhaustion.",
                        t.get_name(),
                        getattr(exc, "retry_after", "unknown"),
                    )
                else:
                    logger.error("Service task %s failed with exception: %s", t.get_name(), exc)
    finally:
        logger.info("Cleaning up platform resources...")
        worker.stop()
        if not bot.is_closed():
            await bot.close()
        await close_db()
        logger.info("Graceful shutdown complete.")

    if has_fatal_error:
        backoff_seconds = settings.STARTUP_CRASH_BACKOFF_SECONDS
        if backoff_seconds > 0:
            logger.warning(
                "Startup crash-loop backoff protection active: delaying exit by %.2fs to protect Gateway quota...",
                backoff_seconds,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff_seconds)
            except TimeoutError:
                pass
        sys.exit(1)


def main():
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        sys.exit(0)
    except SystemExit as exc:
        sys.exit(exc.code)


if __name__ == "__main__":
    main()
