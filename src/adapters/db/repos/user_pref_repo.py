from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.repos.calendar_token_repo import PostgresUserCalendarTokenRepo
from src.adapters.db.tables import UserPreferenceTable
from src.domain.enums import NotificationPreference
from src.domain.models import UserPreference
from src.ports.repositories import IUserPreferenceRepo


class PostgresUserPreferenceRepo(BasePostgresRepo, IUserPreferenceRepo):
    async def get_preference(self, guild_id: int, user_discord_id: int) -> UserPreference | None:
        async with self._get_session() as session:
            stmt = select(UserPreferenceTable).where(
                UserPreferenceTable.guild_id == guild_id,
                UserPreferenceTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            return UserPreference(
                guild_id=row.guild_id,
                user_discord_id=row.user_discord_id,
                notify_preference=NotificationPreference(row.notify_preference),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def set_preference(
        self,
        guild_id: int,
        user_discord_id: int,
        notify_preference: NotificationPreference,
    ) -> UserPreference:
        async with self._get_session() as session:
            now = datetime.now(UTC)
            # Use SQLite or Postgres UPSERT depending on backend dialect
            dialect_name = ""
            if session.bind:
                dialect_name = session.bind.dialect.name
            elif self._session_factory and hasattr(self._session_factory, "kw"):
                engine = self._session_factory.kw.get("bind")
                dialect_name = engine.dialect.name if engine else ""

            if dialect_name == "postgresql":
                stmt = pg_insert(UserPreferenceTable).values(
                    guild_id=guild_id,
                    user_discord_id=user_discord_id,
                    notify_preference=notify_preference.value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["guild_id", "user_discord_id"],
                    set_={
                        "notify_preference": notify_preference.value,
                        "updated_at": now,
                    },
                )
            else:
                stmt = sqlite_insert(UserPreferenceTable).values(
                    guild_id=guild_id,
                    user_discord_id=user_discord_id,
                    notify_preference=notify_preference.value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["guild_id", "user_discord_id"],
                    set_={
                        "notify_preference": notify_preference.value,
                        "updated_at": now,
                    },
                )

            await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return UserPreference(
                guild_id=guild_id,
                user_discord_id=user_discord_id,
                notify_preference=notify_preference,
                created_at=now,
                updated_at=now,
            )

    async def get_preferences_bulk(
        self,
        guild_id: int,
        user_ids: list[int],
    ) -> dict[int, NotificationPreference]:
        if not user_ids:
            return {}
        async with self._get_session() as session:
            stmt = select(UserPreferenceTable).where(
                UserPreferenceTable.guild_id == guild_id,
                UserPreferenceTable.user_discord_id.in_(user_ids),
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            result = {uid: NotificationPreference.DM for uid in user_ids}
            for r in rows:
                try:
                    result[r.user_discord_id] = NotificationPreference(r.notify_preference)
                except ValueError:
                    result[r.user_discord_id] = NotificationPreference.DM
            return result


__all__ = ["PostgresUserCalendarTokenRepo", "PostgresUserPreferenceRepo"]
