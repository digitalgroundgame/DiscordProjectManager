"""Pytest fixtures for In-Process E2E User Journey tests."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest_asyncio.fixture(scope="function")
async def e2e_env(async_engine: AsyncEngine) -> AsyncGenerator[E2ETestEnvironment]:
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    env = E2ETestEnvironment(engine=async_engine, session_factory=session_factory)
    await env.initialize()
    yield env


@pytest_asyncio.fixture(scope="function")
async def admin_actor(e2e_env: E2ETestEnvironment) -> UserJourneyActor:
    return e2e_env.create_actor("AdminUser", is_admin=True)


@pytest_asyncio.fixture(scope="function")
async def lead_actor(e2e_env: E2ETestEnvironment) -> UserJourneyActor:
    return e2e_env.create_actor("SquadLeadUser", is_admin=False)


@pytest_asyncio.fixture(scope="function")
async def member_actor(e2e_env: E2ETestEnvironment) -> UserJourneyActor:
    return e2e_env.create_actor("SquadMemberUser", is_admin=False)


@pytest_asyncio.fixture(scope="function")
async def outsider_actor(e2e_env: E2ETestEnvironment) -> UserJourneyActor:
    return e2e_env.create_actor("OutsiderUser", is_admin=False)
