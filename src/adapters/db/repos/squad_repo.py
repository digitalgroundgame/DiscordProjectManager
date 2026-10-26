from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import SquadMemberTable, SquadTable
from src.domain.enums import SquadRoleType
from src.domain.models import Squad, SquadMember
from src.ports.repositories import ISquadRepo


def _to_domain_squad(row: SquadTable) -> Squad:
    return Squad(
        id=row.id,
        guild_id=row.guild_id,
        name=row.name,
        discord_role_id=row.discord_role_id,
        created_at=row.created_at,
    )


def _to_domain_squad_member(row: SquadMemberTable) -> SquadMember:
    return SquadMember(
        squad_id=row.squad_id,
        user_discord_id=row.user_discord_id,
        role_type=SquadRoleType(row.role_type),
        created_at=row.created_at,
    )


class PostgresSquadRepo(BasePostgresRepo, ISquadRepo):
    async def create(self, squad: Squad) -> Squad:
        async with self._get_session() as session:
            row = SquadTable(
                id=squad.id,
                guild_id=squad.guild_id,
                name=squad.name,
                discord_role_id=squad.discord_role_id,
                created_at=squad.created_at,
            )
            session.add(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return squad

    async def get_by_id(self, squad_id: UUID) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(SquadTable.id == squad_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def get_by_name(self, guild_id: int, name: str) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(
                SquadTable.guild_id == guild_id,
                func.upper(SquadTable.name) == name.strip().upper(),
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def get_by_role_id(self, guild_id: int, role_id: int) -> Squad | None:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(
                SquadTable.guild_id == guild_id,
                SquadTable.discord_role_id == role_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_squad(row) if row else None

    async def add_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        async with self._get_session() as session:
            row = SquadMemberTable(
                squad_id=squad_id,
                user_discord_id=user_discord_id,
                role_type=SquadRoleType.LEAD.value,
            )
            await session.merge(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        async with self._get_session() as session:
            stmt = delete(SquadMemberTable).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.user_discord_id == user_discord_id,
            )
            await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def list_squad_leads(self, squad_id: UUID) -> list[int]:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable.user_discord_id).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.role_type == SquadRoleType.LEAD.value,
            )
            res = await session.execute(stmt)
            return [int(uid) for uid in res.scalars().all()]

    async def assign_member(self, member: SquadMember) -> None:
        async with self._get_session() as session:
            row = SquadMemberTable(
                squad_id=member.squad_id,
                user_discord_id=member.user_discord_id,
                role_type=member.role_type.value,
                created_at=member.created_at,
            )
            await session.merge(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def is_squad_lead(self, squad_id: UUID, user_discord_id: int) -> bool:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable).where(
                SquadMemberTable.squad_id == squad_id,
                SquadMemberTable.user_discord_id == user_discord_id,
                SquadMemberTable.role_type == SquadRoleType.LEAD.value,
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none() is not None

    async def list_squads(self, guild_id: int) -> list[Squad]:
        async with self._get_session() as session:
            stmt = select(SquadTable).where(SquadTable.guild_id == guild_id).order_by(SquadTable.name.asc())
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_squad(r) for r in rows]

    async def list_members(self, squad_id: UUID) -> list[SquadMember]:
        async with self._get_session() as session:
            stmt = select(SquadMemberTable).where(SquadMemberTable.squad_id == squad_id)
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_squad_member(r) for r in rows]
