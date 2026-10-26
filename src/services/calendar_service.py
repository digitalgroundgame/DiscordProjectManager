from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from src.domain.standard_mappings import to_rfc5545_vcalendar
from src.ports.unit_of_work import IUnitOfWork, NullUnitOfWork

if TYPE_CHECKING:
    from src.ports.repositories import ITaskRepo, IUserCalendarTokenRepo

logger = logging.getLogger("dgg_pm.services.calendar")


class CalendarService:
    """Application service for managing user iCalendar subscription feeds and token lifecycles."""

    def __init__(
        self,
        calendar_token_repo: IUserCalendarTokenRepo,
        task_repo: ITaskRepo,
        uow: IUnitOfWork | None = None,
        base_url: str | None = None,
    ) -> None:
        self.token_repo = calendar_token_repo
        self.task_repo = task_repo
        self.uow = uow
        self.base_url = base_url

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[IUnitOfWork]:
        if self.uow is not None:
            async with self.uow as active_uow:
                yield active_uow
        else:
            yield NullUnitOfWork(calendar_tokens=self.token_repo, tasks=self.task_repo)

    async def get_or_create_token(self, user_discord_id: int) -> str:
        """Retrieves existing calendar token for a user or creates a new cryptographically secure token."""
        async with self._transaction() as uow:
            record = await uow.calendar_tokens.get_by_user_id(user_discord_id)
            if record:
                return record.token

            token = secrets.token_urlsafe(32)
            created = await uow.calendar_tokens.create_or_regenerate_token(user_discord_id, token)
            logger.info("Generated new calendar feed token for user %s", user_discord_id)
            return created.token

    async def regenerate_token(self, user_discord_id: int) -> str:
        """Revokes any existing token and generates a fresh token for the user."""
        async with self._transaction() as uow:
            token = secrets.token_urlsafe(32)
            created = await uow.calendar_tokens.create_or_regenerate_token(user_discord_id, token)
            logger.info("Regenerated calendar feed token for user %s", user_discord_id)
            return created.token

    async def revoke_token(self, user_discord_id: int) -> bool:
        """Revokes (deletes) the calendar token for a user."""
        async with self._transaction() as uow:
            revoked = await uow.calendar_tokens.revoke_token(user_discord_id)
            if revoked:
                logger.info("Revoked calendar feed token for user %s", user_discord_id)
            return revoked

    async def get_user_id_by_token(self, token: str) -> int | None:
        """Validates a calendar token and returns the associated Discord user ID."""
        record = await self.token_repo.get_by_token(token)
        if not record:
            return None
        return record.user_discord_id

    async def get_calendar_feed(
        self,
        token: str,
        format_type: str = "event",
    ) -> str | None:
        """Fetches active tasks for the token holder and serializes into RFC 5545 format.

        Returns None if token is invalid or unassociated with a user.
        """
        user_discord_id = await self.get_user_id_by_token(token)
        if user_discord_id is None:
            return None

        tasks = await self.task_repo.list_user_calendar_tasks(user_discord_id)
        return to_rfc5545_vcalendar(
            tasks=tasks,
            cal_name=f"DGG-PM Tasks ({user_discord_id})",
            format_type=format_type,
        )

    @staticmethod
    def build_feed_urls(token: str, base_url: str | None = None) -> dict[str, str]:
        """Builds webcal:// and https:// URLs for calendar subscription given a token."""
        if not base_url:
            from src.config import get_settings

            base_url = get_settings().PUBLIC_BASE_URL
        raw_base = (base_url or "http://localhost:8000").rstrip("/")
        feed_path = f"/calendar/{token}.ics"
        http_url = f"{raw_base}{feed_path}"

        # Build webcal:// protocol scheme URL
        if raw_base.startswith("https://"):
            webcal_base = "webcal://" + raw_base[len("https://") :]
        elif raw_base.startswith("http://"):
            webcal_base = "webcal://" + raw_base[len("http://") :]
        else:
            webcal_base = f"webcal://{raw_base}"
        webcal_url = f"{webcal_base}{feed_path}"
        subscribe_url = f"{raw_base}/calendar/{token}/subscribe"

        return {
            "https_url": http_url,
            "webcal_url": webcal_url,
            "subscribe_url": subscribe_url,
            "todo_https_url": f"{http_url}?format=todo",
            "todo_webcal_url": f"{webcal_url}?format=todo",
            "todo_subscribe_url": f"{subscribe_url}?format=todo",
        }
