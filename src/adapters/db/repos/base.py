from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class BasePostgresRepo:
    def __init__(self, session_or_factory: AsyncSession | async_sessionmaker[AsyncSession]):
        if isinstance(session_or_factory, AsyncSession):
            self._session = session_or_factory
            self._session_factory = None
        else:
            self._session = None
            self._session_factory = session_or_factory

    def _should_commit(self, session: AsyncSession | None) -> bool:
        return session is None and self._session is None

    @asynccontextmanager
    async def _get_session(self, session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession]:
        if session is not None:
            yield session
        elif self._session is not None:
            yield self._session
        else:
            async with self._session_factory() as s:
                yield s
