from __future__ import annotations

from uuid import UUID

from src.domain.enums import SquadRoleType
from src.domain.exceptions import SquadAlreadyExistsError
from src.domain.models import Squad, SquadMember
from src.ports.repositories import ISquadRepo


class SquadService:
    def __init__(self, squad_repo: ISquadRepo):
        self.squad_repo = squad_repo

    async def create_squad(
        self,
        guild_id: int,
        name: str,
        discord_role_id: int,
    ) -> Squad:
        existing = await self.squad_repo.get_by_name(guild_id, name)
        if existing:
            raise SquadAlreadyExistsError(f"Squad with name '{name}' already exists in this server.")

        squad = Squad(
            guild_id=guild_id,
            name=name.strip(),
            discord_role_id=discord_role_id,
        )
        return await self.squad_repo.create(squad)

    async def get_by_id(self, squad_id: UUID) -> Squad | None:
        return await self.squad_repo.get_by_id(squad_id)

    async def get_by_name(self, guild_id: int, name: str) -> Squad | None:
        return await self.squad_repo.get_by_name(guild_id, name)

    async def get_by_role_id(self, guild_id: int, role_id: int) -> Squad | None:
        return await self.squad_repo.get_by_role_id(guild_id, role_id)

    async def get_or_create_squad_for_role(
        self,
        guild_id: int,
        role_id: int,
        role_name: str,
    ) -> Squad:
        """Finds an existing squad by role ID or name, or creates a new one."""
        squad = await self.squad_repo.get_by_role_id(guild_id, role_id)
        if squad:
            return squad

        squad_by_name = await self.squad_repo.get_by_name(guild_id, role_name.strip())
        if squad_by_name:
            return squad_by_name

        new_squad = Squad(
            guild_id=guild_id,
            name=role_name.strip(),
            discord_role_id=role_id,
        )
        return await self.squad_repo.create(new_squad)

    async def add_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        await self.squad_repo.add_squad_lead(squad_id, user_discord_id)

    async def remove_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        await self.squad_repo.remove_squad_lead(squad_id, user_discord_id)

    async def list_squad_leads(self, squad_id: UUID) -> list[int]:
        return await self.squad_repo.list_squad_leads(squad_id)

    async def is_squad_lead(self, squad_id: UUID, user_discord_id: int) -> bool:
        return await self.squad_repo.is_squad_lead(squad_id, user_discord_id)

    async def assign_member(
        self,
        squad_id: UUID,
        user_discord_id: int,
        role_type: SquadRoleType = SquadRoleType.MEMBER,
    ) -> None:
        member = SquadMember(
            squad_id=squad_id,
            user_discord_id=user_discord_id,
            role_type=role_type,
        )
        await self.squad_repo.assign_member(member)

    async def list_squads(self, guild_id: int) -> list[Squad]:
        return await self.squad_repo.list_squads(guild_id)

    async def list_members(self, squad_id: UUID) -> list[SquadMember]:
        return await self.squad_repo.list_members(squad_id)
