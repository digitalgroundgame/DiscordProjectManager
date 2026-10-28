from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
from src.config import get_settings

logger = logging.getLogger("dgg_pm.db")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine_and_session_factory(
    database_url: str | None = None,
    echo: bool = False,
    pool_pre_ping: bool = True,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build a standalone AsyncEngine and async_sessionmaker."""
    url = database_url or get_settings().DATABASE_URL
    eng: AsyncEngine = create_async_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=pool_pre_ping,
    )
    factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return eng, factory


def get_engine() -> AsyncEngine:
    """Return the cached default AsyncEngine, creating it lazily if needed."""
    global _engine, _session_factory
    if _engine is None:
        _engine, _session_factory = create_engine_and_session_factory()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached default sessionmaker, creating it lazily if needed."""
    global _engine, _session_factory
    if _session_factory is None:
        _engine, _session_factory = create_engine_and_session_factory()
    return _session_factory


def set_engine_and_session_factory(
    eng: AsyncEngine | None,
    factory: async_sessionmaker[AsyncSession] | None,
) -> None:
    """Assign explicit engine and sessionmaker from composition root or tests."""
    global _engine, _session_factory
    _engine = eng
    _session_factory = factory


def _resolve_engine(provided: AsyncEngine | None = None) -> AsyncEngine:
    if provided is not None:
        return provided
    module_dict = sys.modules[__name__].__dict__
    if "engine" in module_dict and module_dict["engine"] is not None:
        return module_dict["engine"]
    return get_engine()


def _resolve_session_factory(
    provided: async_sessionmaker[AsyncSession] | None = None,
) -> async_sessionmaker[AsyncSession]:
    if provided is not None:
        return provided
    module_dict = sys.modules[__name__].__dict__
    if "async_session_factory" in module_dict and module_dict["async_session_factory"] is not None:
        return module_dict["async_session_factory"]
    return get_session_factory()


def __getattr__(name: str) -> Any:
    """Provide lazy backwards compatibility for 'from src.adapters.db.session import engine, async_session_factory'."""
    if name == "engine":
        return get_engine()
    if name == "async_session_factory":
        return get_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_alembic_config() -> Config:
    """Returns an Alembic Config object configured with absolute project paths."""
    project_root = Path(__file__).resolve().parents[3]
    ini_path = project_root / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(project_root / "src" / "adapters" / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    return cfg


async def run_migrations(
    engine: AsyncEngine | None = None,
    max_retries: int = 15,
    retry_interval: float = 1.0,
) -> None:
    """Applies Alembic migrations to head with connection retries, auto-stamp support, and drift verification."""
    cfg = get_alembic_config()
    target_engine = _resolve_engine(engine)

    for attempt in range(1, max_retries + 1):
        try:
            # Note: Pre-flight table inspection uses the app's shared engine, while Alembic's
            # env.py creates its own connection pool during command execution. This results in two
            # connection pools touching the database in quick succession during startup.
            async with target_engine.connect() as conn:
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


async def init_db(
    engine: AsyncEngine | None = None,
    auto_run_migrations: bool | None = None,
    max_retries: int = 15,
    retry_interval: float = 1.0,
) -> None:
    """Initializes the database schema with Alembic migrations if enabled, or creates tables."""
    target_engine = _resolve_engine(engine)
    if target_engine.dialect.name == "sqlite":
        async with target_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    should_migrate = auto_run_migrations if auto_run_migrations is not None else get_settings().AUTO_RUN_MIGRATIONS
    if should_migrate:
        await run_migrations(engine=target_engine, max_retries=max_retries, retry_interval=retry_interval)
    else:
        logger.info("AUTO_RUN_MIGRATIONS is disabled; skipping automatic migration on startup.")


async def close_db(engine: AsyncEngine | None = None) -> None:
    """Disposes of the database connection pool."""
    global _engine
    target_engine = _resolve_engine(engine) if (engine or _engine) else None
    if target_engine is not None:
        await target_engine.dispose()
        if target_engine is _engine:
            _engine = None


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession]:
    """Context manager providing an isolated async database session."""
    factory = _resolve_session_factory(session_factory)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
