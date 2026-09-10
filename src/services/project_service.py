from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from src.domain.exceptions import ProjectAlreadyExistsError, ProjectNotFoundError
from src.domain.models import Project, ProjectSquad, Squad, Team
from src.ports.repositories import IProjectRepo


class ProjectService:
    def __init__(self, project_repo: IProjectRepo):
        self.project_repo = project_repo

    @staticmethod
    def derive_default_prefix(name: str) -> str:
        """Derives a clean uppercase prefix from project name."""
        clean = re.sub(r"[^A-Za-z0-9\s]", "", name).strip()
        words = clean.split()
        if len(words) >= 2:
            prefix = "".join(w[0] for w in words[:4]).upper()
        else:
            prefix = clean[:4].upper()
        return prefix if prefix else "PRJ"

    async def create_project(
        self,
        guild_id: int,
        name: str,
        prefix: str | None = None,
        description: str | None = None,
        discord_channel_id: int | None = None,
        discord_role_id: int | None = None,
        discord_role_ids: list[int] | None = None,
        lead_discord_id: int | None = None,
        category: str | None = None,
    ) -> Project:
        # Check name uniqueness in guild
        existing_name = await self.project_repo.get_by_name(guild_id, name)
        if existing_name:
            raise ProjectAlreadyExistsError(f"Project with name '{name}' already exists in this server.")

        assigned_prefix = (prefix or self.derive_default_prefix(name)).upper()
        existing_prefix = await self.project_repo.get_by_prefix(guild_id, assigned_prefix)
        if existing_prefix:
            raise ProjectAlreadyExistsError(
                f"Project prefix '{assigned_prefix}' is already in use by project '{existing_prefix.name}'."
            )

        role_ids = list(discord_role_ids or [])
        if discord_role_id and discord_role_id not in role_ids:
            role_ids.insert(0, discord_role_id)

        project = Project(
            guild_id=guild_id,
            name=name.strip(),
            prefix=assigned_prefix,
            next_task_number=1,
            description=description.strip() if description else None,
            discord_channel_id=discord_channel_id,
            discord_role_ids=role_ids,
            lead_discord_id=lead_discord_id,
            category=category.strip() if category else None,
        )
        return await self.project_repo.create(project)

    async def get_by_id(self, project_id: UUID) -> Project | None:
        return await self.project_repo.get_by_id(project_id)

    async def get_by_name(self, guild_id: int, name: str) -> Project | None:
        return await self.project_repo.get_by_name(guild_id, name)

    async def get_by_channel_id(self, guild_id: int, channel_id: int) -> Project | None:
        return await self.project_repo.get_by_channel_id(guild_id, channel_id)

    async def list_projects(self, guild_id: int, include_archived: bool = False) -> list[Project]:
        return await self.project_repo.list_projects(guild_id, include_archived=include_archived)

    async def allocate_next_short_id(
        self,
        project_id: UUID,
        project_repo: IProjectRepo | None = None,
        session: Any = None,
    ) -> tuple[int, str]:
        """Atomically increments next_task_number and returns (task_number, short_id)."""
        repo = project_repo or self.project_repo
        try:
            task_num, prefix = await repo.increment_task_number_atomic(project_id, session=session)
        except ValueError as e:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.") from e
        short_id = f"{prefix}-{task_num}"
        return task_num, short_id

    async def archive_project(self, project_id: UUID) -> Project | None:
        return await self.project_repo.set_archived(project_id, is_archived=True)

    async def unarchive_project(self, project_id: UUID) -> Project | None:
        return await self.project_repo.set_archived(project_id, is_archived=False)

    async def update_project_channel(self, project_id: UUID, discord_channel_id: int | None) -> Project | None:
        return await self.project_repo.update_channel_id(project_id, discord_channel_id)

    async def set_project_role(self, project_id: UUID, discord_role_id: int | None) -> Project | None:
        """Updates or clears the mapped contributor Discord role for a project."""
        return await self.project_repo.update_role_id(project_id, discord_role_id)

    async def set_project_lead(self, project_id: UUID, lead_discord_id: int | None) -> Project | None:
        """Updates or clears the designated Project Lead Discord user for a project."""
        return await self.project_repo.update_lead_id(project_id, lead_discord_id)

    async def assign_squad_to_project(
        self,
        project_id: UUID,
        squad_id: UUID,
        start_date: datetime | None = None,
        timeline: str | None = None,
    ) -> None:
        ps = ProjectSquad(
            project_id=project_id,
            squad_id=squad_id,
            start_date=start_date,
            timeline=timeline,
        )
        await self.project_repo.assign_squad(ps)

    async def assign_team_to_project(
        self,
        project_id: UUID,
        team_id: UUID,
        start_date: datetime | None = None,
        timeline: str | None = None,
    ) -> None:
        await self.assign_squad_to_project(
            project_id=project_id,
            squad_id=team_id,
            start_date=start_date,
            timeline=timeline,
        )

    async def remove_squad_from_project(self, project_id: UUID, squad_id: UUID) -> None:
        await self.project_repo.remove_squad(project_id, squad_id)

    async def remove_team_from_project(self, project_id: UUID, team_id: UUID) -> None:
        await self.remove_squad_from_project(project_id, team_id)

    async def list_squads_for_project(self, project_id: UUID) -> list[Squad]:
        return await self.project_repo.list_squads_for_project(project_id)

    async def list_teams_for_project(self, project_id: UUID) -> list[Team]:
        return await self.list_squads_for_project(project_id)
