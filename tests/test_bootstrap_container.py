"""Unit tests for explicit Composition Root (src/bootstrap/container.py)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.bootstrap.container import AppContainer, create_container
from src.config import Settings


@pytest.mark.asyncio
async def test_container_wires_all_services_and_adapters(
    async_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    """Verify that create_container wires all repositories, services, UoW, and bot client."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    custom_settings = Settings(
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DEBUG=False,
        SYNC_COMMANDS_ON_STARTUP=False,
    )

    container = create_container(
        settings=custom_settings,
        engine=async_engine,
        session_factory=session_factory,
    )

    assert isinstance(container, AppContainer)
    assert container.settings is custom_settings
    assert container.engine is async_engine
    assert container.session_factory is session_factory

    # Verify Repositories
    assert container.task_repo is not None
    assert container.project_repo is not None
    assert container.squad_repo is not None
    assert container.outbox_repo is not None
    assert container.user_pref_repo is not None
    assert container.guild_lead_role_repo is not None
    assert container.guild_lead_user_repo is not None
    assert container.calendar_token_repo is not None

    # Verify Services
    assert container.project_service is not None
    assert container.squad_service is not None
    assert container.outbox_service is not None
    assert container.task_service is not None
    assert container.user_service is not None
    assert container.calendar_service is not None
    assert container.observability_service is not None

    # Verify Adapters & Driving Infrastructure
    assert container.uow is not None
    assert container.tree_renderer is not None
    assert container.metrics_adapter is not None
    assert container.menu_manager is not None
    assert container.bot is not None
    assert container.notifier is not None
    assert container.worker is not None
    assert container.api_app is not None
    assert container.uvicorn_server is not None

    # Verify Bot Wiring
    assert container.bot.task_service is container.task_service
    assert container.bot.project_service is container.project_service
    assert container.bot.squad_service is container.squad_service
    assert container.bot.user_service is container.user_service
    assert container.bot.calendar_service is container.calendar_service
    assert container.bot.outbox_service is container.outbox_service


@pytest.mark.asyncio
async def test_container_lifecycle_init_db_and_close() -> None:
    """Verify that container lifecycle methods run cleanly with standalone engine."""
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    container = create_container(
        settings=Settings(
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
            AUTO_RUN_MIGRATIONS=False,
        ),
        engine=test_engine,
        session_factory=session_factory,
    )

    # Database initialization
    await container.init_db()

    # Worker stop and container disposal
    await container.close()


@pytest.mark.asyncio
async def test_container_injects_custom_settings() -> None:
    """Verify that passing explicit settings overrides environment defaults cleanly."""
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    custom_token = "explicit_test_token_12345"
    custom_settings = Settings(
        DISCORD_BOT_TOKEN=custom_token,
        SYNC_COMMANDS_ON_STARTUP=False,
        API_PORT=9999,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )

    container = create_container(
        settings=custom_settings,
        engine=test_engine,
        session_factory=session_factory,
    )
    try:
        assert container.settings.DISCORD_BOT_TOKEN == custom_token
        assert container.settings.API_PORT == 9999
        assert container.bot.settings.DISCORD_BOT_TOKEN == custom_token
    finally:
        await container.close()
