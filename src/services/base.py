from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from src.ports.unit_of_work import IUnitOfWork, NullUnitOfWork


class TransactionalService:
    """Base mixin providing unified _transaction scope management across application services."""

    uow: IUnitOfWork | None

    @asynccontextmanager
    async def _transaction(self, **fallback_kwargs: Any) -> AsyncGenerator[IUnitOfWork]:
        """Manages an atomic transaction scope via UnitOfWork if configured, or NullUnitOfWork fallback."""
        if getattr(self, "uow", None) is not None:
            async with self.uow as active_uow:  # type: ignore[union-attr]
                yield active_uow
        else:
            yield NullUnitOfWork(**fallback_kwargs)
