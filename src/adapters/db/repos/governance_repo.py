from __future__ import annotations

from sqlalchemy import delete, select

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import GuildLeadRoleTable, GuildLeadUserTable
from src.ports.repositories import (
    IGuildLeadRoleRepository,
    IGuildLeadUserRepository,
)


class PostgresGuildLeadRoleRepository(BasePostgresRepo, IGuildLeadRoleRepository):
    async def add_lead_role(self, guild_id: int, discord_role_id: int) -> None:
        async with self._get_session() as session:
            stmt = select(GuildLeadRoleTable).where(
                GuildLeadRoleTable.guild_id == guild_id,
                GuildLeadRoleTable.discord_role_id == discord_role_id,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                return
            row = GuildLeadRoleTable(guild_id=guild_id, discord_role_id=discord_role_id)
            session.add(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_lead_role(self, guild_id: int, discord_role_id: int) -> bool:
        async with self._get_session() as session:
            stmt = delete(GuildLeadRoleTable).where(
                GuildLeadRoleTable.guild_id == guild_id,
                GuildLeadRoleTable.discord_role_id == discord_role_id,
            )
            res = await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return bool(res.rowcount and res.rowcount > 0)

    async def list_lead_role_ids(self, guild_id: int) -> set[int]:
        async with self._get_session() as session:
            stmt = select(GuildLeadRoleTable.discord_role_id).where(GuildLeadRoleTable.guild_id == guild_id)
            res = await session.execute(stmt)
            return set(res.scalars().all())


class PostgresGuildLeadUserRepository(BasePostgresRepo, IGuildLeadUserRepository):
    async def add_lead_user(self, guild_id: int, user_discord_id: int) -> None:
        async with self._get_session() as session:
            stmt = select(GuildLeadUserTable).where(
                GuildLeadUserTable.guild_id == guild_id,
                GuildLeadUserTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                return
            row = GuildLeadUserTable(guild_id=guild_id, user_discord_id=user_discord_id)
            session.add(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_lead_user(self, guild_id: int, user_discord_id: int) -> bool:
        async with self._get_session() as session:
            stmt = delete(GuildLeadUserTable).where(
                GuildLeadUserTable.guild_id == guild_id,
                GuildLeadUserTable.user_discord_id == user_discord_id,
            )
            res = await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return bool(res.rowcount and res.rowcount > 0)

    async def list_lead_user_ids(self, guild_id: int) -> set[int]:
        async with self._get_session() as session:
            stmt = select(GuildLeadUserTable.user_discord_id).where(GuildLeadUserTable.guild_id == guild_id)
            res = await session.execute(stmt)
            return set(res.scalars().all())
