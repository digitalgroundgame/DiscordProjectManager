from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import UserCalendarTokenTable
from src.domain.models import UserCalendarToken
from src.ports.repositories import IUserCalendarTokenRepo


class PostgresUserCalendarTokenRepo(BasePostgresRepo, IUserCalendarTokenRepo):
    async def get_by_user_id(self, user_discord_id: int) -> UserCalendarToken | None:
        async with self._get_session() as session:
            stmt = select(UserCalendarTokenTable).where(UserCalendarTokenTable.user_discord_id == user_discord_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            return UserCalendarToken(
                user_discord_id=row.user_discord_id,
                token=row.token,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def get_by_token(self, token: str) -> UserCalendarToken | None:
        async with self._get_session() as session:
            stmt = select(UserCalendarTokenTable).where(UserCalendarTokenTable.token == token)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            return UserCalendarToken(
                user_discord_id=row.user_discord_id,
                token=row.token,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def create_or_regenerate_token(self, user_discord_id: int, token: str) -> UserCalendarToken:
        now = datetime.now(UTC)
        async with self._get_session() as session:
            stmt = select(UserCalendarTokenTable).where(UserCalendarTokenTable.user_discord_id == user_discord_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row:
                row.token = token
                row.updated_at = now
            else:
                row = UserCalendarTokenTable(
                    user_discord_id=user_discord_id,
                    token=token,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)

            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

            return UserCalendarToken(
                user_discord_id=row.user_discord_id,
                token=row.token,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def revoke_token(self, user_discord_id: int) -> bool:
        async with self._get_session() as session:
            stmt = delete(UserCalendarTokenTable).where(UserCalendarTokenTable.user_discord_id == user_discord_id)
            res = await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return bool(res.rowcount and res.rowcount > 0)
