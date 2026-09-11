import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.adapters.db.tables import Base
from src.config import settings

logger = logging.getLogger("dgg_pm.db")

# Global async engine
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
)

# Async session factory
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_alembic_config() -> Config:
    """Returns an Alembic Config object configured with absolute project paths."""
    project_root = Path(__file__).resolve().parents[3]
    ini_path = project_root / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(project_root / "src" / "adapters" / "db" / "migrations"))
    return cfg


async def run_migrations(max_retries: int = 15, retry_interval: float = 1.0) -> None:
    """Applies Alembic migrations to head with connection retries, auto-stamp support, and drift verification."""
    cfg = get_alembic_config()

    for attempt in range(1, max_retries + 1):
        try:
            # Note: Pre-flight table inspection uses the app's shared engine, while Alembic's
            # env.py creates its own connection pool during command execution. This results in two
            # connection pools touching the database in quick succession during startup.
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
                has_app_tables = "projects" in tables or "tasks" in tables
                has_alembic_table = "alembic_version" in tables
                has_version_rows = False
                if has_alembic_table:
                    res = await conn.execute(text("SELECT version_num FROM alembic_version"))
                    has_version_rows = len(res.fetchall()) > 0

            # Safe auto-stamp for existing unversioned databases lacking an alembic_version row.
            # Stamping targets the baseline initial revision (e.g. '0001') rather than 'head',
            # ensuring any subsequent migrations are applied rather than skipped.
            if has_app_tables and not has_version_rows:
                script_dir = ScriptDirectory.from_config(cfg)
                base_revision = script_dir.get_base() or "0001"
                logger.info(
                    "Existing unversioned database schema detected; stamping to baseline revision '%s'...",
                    base_revision,
                )
                await asyncio.to_thread(command.stamp, cfg, base_revision)
                logger.info("Database successfully stamped to baseline revision '%s'.", base_revision)

            logger.info("Applying database migrations to 'head'...")
            await asyncio.to_thread(command.upgrade, cfg, "head")
            logger.info("Database migrations applied successfully.")

            # Verification pass: check for schema drift against ORM models
            try:
                await asyncio.to_thread(command.check, cfg)
                logger.info("Database schema verification passed: no drift detected against ORM models.")
            except Exception as check_err:
                logger.warning(
                    "Database schema drift detected between database and ORM models: %s. "
                    "Run 'alembic check' or generate a reconciliation revision.",
                    check_err,
                )
            return
        except Exception as e:
            if attempt == max_retries:
                logger.error("Failed to run database migrations after %d attempts: %s", max_retries, e)
                raise
            logger.warning(
                "Database not ready yet (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt,
                max_retries,
                e,
                retry_interval,
            )
            await asyncio.sleep(retry_interval)


async def init_db(max_retries: int = 15, retry_interval: float = 1.0) -> None:
    """Initializes the database schema with Alembic migrations if enabled, or creates tables."""
    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    if settings.AUTO_RUN_MIGRATIONS:
        await run_migrations(max_retries=max_retries, retry_interval=retry_interval)
    else:
        logger.info("AUTO_RUN_MIGRATIONS is disabled; skipping automatic migration on startup.")


async def close_db() -> None:
    """Disposes of the database connection pool."""
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Context manager providing an isolated async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
