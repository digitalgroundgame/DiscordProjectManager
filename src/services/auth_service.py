"""Role and squad-based authorization service for dgg-pm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.domain.exceptions import PermissionDeniedError
from src.domain.models import Project, Task

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService

logger = logging.getLogger("dgg_pm.services.auth")


class AuthService:
    """Evaluates role, squad, and server permissions for task mutations and project operations."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
    ):
        self.project_service = project_service
        self.squad_service = squad_service

    @staticmethod
    def is_server_manager(user: discord.Member | discord.User) -> bool:
        """Checks if user has Discord administrator or manage_guild permissions."""
        perms = getattr(user, "guild_permissions", None)
        if perms is not None:
            admin = getattr(perms, "administrator", False)
            manage_guild = getattr(perms, "manage_guild", False)
            return bool(admin or manage_guild)
        return False

    async def is_project_lead(self, user: discord.Member | discord.User, project_id: UUID | None) -> bool:
        """Checks if the user is designated as the Project Lead or is a server manager."""
        if not project_id:
            return False
        if self.is_server_manager(user):
            return True
        project = await self.project_service.get_by_id(project_id)
        if not project or not project.lead_discord_id:
            return False
        user_id = getattr(user, "id", None)
        return bool(user_id and user_id == project.lead_discord_id)

    async def get_project_role_ids(self, project: Project | None) -> set[int]:
        """Resolves all mapped Discord role IDs for a project across direct squad roles and mapped squads."""
        if not project:
            return set()
        role_ids = set(project.discord_role_ids)
        if project.discord_role_id:
            role_ids.add(project.discord_role_id)
        if self.squad_service:
            squads = await self.project_service.list_squads_for_project(project.id)
            for s in squads:
                role_ids.add(s.discord_role_id)
        return role_ids

    async def can_mutate_task(self, user: discord.Member | discord.User, task: Task) -> bool:
        """Determines if a user is authorized to edit, reassign, update status, archive, or add notes to a task.

        Authorized if:
        1. User is a Discord Server Manager (manage_guild / administrator).
        2. User is the task assignee.
        3. User is the task creator.
        4. User is the designated Project Lead for the task's project.
        5. User holds any of the project's mapped squad Discord Roles.
        """
        # 1. Server Manager bypass
        if self.is_server_manager(user):
            return True

        user_id = getattr(user, "id", None)

        # 2. Task Assignee
        if task.assignee_discord_id is not None and user_id == task.assignee_discord_id:
            return True

        # 3. Task Creator
        if user_id is not None and user_id == task.creator_discord_id:
            return True

        # 4. Project Lead / Contributor Role
        if task.project_id:
            project = await self.project_service.get_by_id(task.project_id)
            if project:
                if project.lead_discord_id and user_id == project.lead_discord_id:
                    return True
                project_roles = await self.get_project_role_ids(project)
                if project_roles:
                    roles = getattr(user, "roles", [])
                    user_role_ids = {r.id for r in roles if hasattr(r, "id")}
                    if any(rid in user_role_ids for rid in project_roles):
                        return True

        return False

    async def require_task_mutation(self, user: discord.Member | discord.User, task: Task) -> None:
        """Raises PermissionDeniedError if the user is not authorized to mutate the task."""
        if not await self.can_mutate_task(user, task):
            raise PermissionDeniedError(
                "You do not have permission to modify this task. "
                "You must be the assignee, creator, project lead, a squad member, or a server manager."
            )

    async def can_create_task_in_project(
        self,
        user: discord.Member | discord.User,
        project_id: UUID | None,
        guild_id: int | None = None,
    ) -> bool:
        """Determines if a user is authorized to create tasks in a project container or standalone.

        - Server Managers can create tasks in any project and standalone.
        - Standalone tasks (project_id=None): restricted to Server Managers and Squad Leads.
        - Project Leads can create tasks in their projects.
        - If a project has a mapped squad Discord role: user must hold that role.
        - If a project has no role mapping and no mapped squads: open to all members.
        """
        # Server Manager bypass
        if self.is_server_manager(user):
            return True

        user_id = getattr(user, "id", None)

        if not project_id:
            # Standalone tasks are restricted to Server Managers (handled above) and Squad Leads
            if self.squad_service and user_id:
                guild = getattr(user, "guild", None)
                target_guild_id = (
                    guild.id if (guild and hasattr(guild, "id") and isinstance(guild.id, int)) else guild_id
                )
                if target_guild_id and isinstance(target_guild_id, int):
                    squads = await self.squad_service.list_squads(target_guild_id)
                    for s in squads:
                        if await self.squad_service.is_squad_lead(s.id, user_id):
                            return True
            return False

        project = await self.project_service.get_by_id(project_id)
        if not project:
            return False

        if project.lead_discord_id and user_id == project.lead_discord_id:
            return True

        project_roles = await self.get_project_role_ids(project)
        if project_roles:
            roles = getattr(user, "roles", [])
            user_role_ids = {r.id for r in roles if hasattr(r, "id")}
            return any(rid in user_role_ids for rid in project_roles)

        # If project has no squad roles mapped, open to all server members
        return True

    async def require_task_creation(
        self,
        user: discord.Member | discord.User,
        project_id: UUID | None,
        guild_id: int | None = None,
    ) -> None:
        """Raises PermissionDeniedError if the user cannot create tasks in the project."""
        if not await self.can_create_task_in_project(user, project_id, guild_id=guild_id):
            if not project_id:
                raise PermissionDeniedError(
                    "You do not have permission to create standalone tasks. "
                    "You must be a Squad Lead or a server manager, or create the task inside an active project."
                )
            raise PermissionDeniedError(
                "You do not have permission to create tasks in this project. "
                "You must hold the project squad's Discord role, be the Project Lead, or be a server manager."
            )

    async def can_view_project(self, user: discord.Member | discord.User, project_id: UUID) -> bool:
        """Determines if a user is authorized to view a project's details, task list, or visual tech tree graph.

        Authorized if:
        1. User is a Discord Server Manager (manage_guild / administrator).
        2. User is the designated Project Lead.
        3. User holds any of the project's mapped squad Discord Roles.
        4. If the project has NO role mapping and NO assigned squads: open to all server members.
        """
        if self.is_server_manager(user):
            return True

        user_id = getattr(user, "id", None)
        project = await self.project_service.get_by_id(project_id)
        if not project:
            return False

        if project.lead_discord_id and user_id == project.lead_discord_id:
            return True

        project_roles = await self.get_project_role_ids(project)
        if project_roles:
            roles = getattr(user, "roles", [])
            user_role_ids = {r.id for r in roles if hasattr(r, "id")}
            return any(rid in user_role_ids for rid in project_roles)

        # If project has no squad roles mapped, open to all server members
        return True

    async def require_project_view(self, user: discord.Member | discord.User, project_id: UUID) -> None:
        """Raises PermissionDeniedError if the user cannot view the project or its visual graph."""
        if not await self.can_view_project(user, project_id):
            raise PermissionDeniedError(
                "You do not have permission to view this project's visual graph. "
                "You must hold the project squad's Discord role, be the Project Lead, or be a server manager."
            )

    async def can_assign_task_to_user(
        self,
        guild: discord.Guild | None,
        target_user: discord.Member | discord.User | int | None,
        project_id: UUID | None,
    ) -> bool:
        """Determines if a target user is eligible to be assigned to a task.

        - If target_user is None (unassigning): always eligible (True).
        - If standalone task (project_id is None): any guild member is eligible.
        - If target_user is the Project Lead: always eligible.
        - If project has mapped squad roles: target user must hold at least one of those Discord roles.
        - If project has no mapped roles / squads: any guild member is eligible.
        """
        if target_user is None or not project_id:
            return True

        project = await self.project_service.get_by_id(project_id)
        if not project:
            return True

        user_id = target_user if isinstance(target_user, int) else getattr(target_user, "id", None)
        if project.lead_discord_id and user_id == project.lead_discord_id:
            return True

        member: Any = None
        if hasattr(target_user, "roles"):
            member = target_user
        elif guild and user_id:
            if hasattr(guild, "get_member"):
                member = guild.get_member(user_id)
            if member is None and hasattr(guild, "fetch_member"):
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None

        if not member:
            return False

        project_roles = await self.get_project_role_ids(project)
        if project_roles:
            roles = getattr(member, "roles", [])
            user_role_ids = {r.id for r in roles if hasattr(r, "id")}
            return any(rid in user_role_ids for rid in project_roles)

        return True

    async def require_task_assignee_eligibility(
        self,
        guild: discord.Guild | None,
        target_user: discord.Member | discord.User | int | None,
        project_id: UUID | None,
    ) -> None:
        """Raises PermissionDeniedError if the target user is not eligible for assignment."""
        if not await self.can_assign_task_to_user(guild, target_user, project_id):
            user_id = target_user.id if hasattr(target_user, "id") else target_user
            project = await self.project_service.get_by_id(project_id) if project_id else None
            role_names: list[str] = []
            if project:
                project_roles = await self.get_project_role_ids(project)
                for rid in sorted(project_roles):
                    role = guild.get_role(rid) if (guild and hasattr(guild, "get_role")) else None
                    if role:
                        role_names.append(f"@{role.name}")
                    elif self.squad_service:
                        squad = await self.squad_service.get_by_role_id(project.guild_id, rid)
                        if squad:
                            role_names.append(f"@{squad.name}")
                        else:
                            role_names.append(f"<@&{rid}>")
                    else:
                        role_names.append(f"<@&{rid}>")

            roles_str = ", ".join(role_names) if role_names else "a squad role"
            proj_str = f"project '{project.name}'" if project else "this project"
            raise PermissionDeniedError(
                f"<@{user_id}> does not hold an eligible squad Discord role for {proj_str}. "
                f"Required role(s): {roles_str}. Only members with these roles or the Project Lead can be assigned."
            )

    async def can_manage_squad_leads(self, user: discord.Member | discord.User, squad_id: UUID) -> bool:
        """Determines if a user can designate or remove squad leads.

        Authorized if:
        1. User has Server Manager permissions (manage_guild / administrator).
        2. User is designated as a Squad Lead in database AND currently holds the squad's Discord role.
        """
        if self.is_server_manager(user):
            return True

        user_id = getattr(user, "id", None)
        if user_id is None or not isinstance(user_id, int):
            return False

        if not self.squad_service:
            return False

        is_lead = await self.squad_service.is_squad_lead(squad_id, user_id)
        if not is_lead:
            return False

        # Verify that the user still currently holds the squad's Discord role
        if hasattr(user, "roles"):
            squad = await self.squad_service.get_by_id(squad_id)
            if squad:
                user_role_ids = {r.id for r in getattr(user, "roles", []) if hasattr(r, "id")}
                if squad.discord_role_id not in user_role_ids:
                    return False

        return True

    async def require_squad_lead_management(self, user: discord.Member | discord.User, squad_id: UUID) -> None:
        """Raises PermissionDeniedError if the user cannot manage squad leads."""
        if not await self.can_manage_squad_leads(user, squad_id):
            raise PermissionDeniedError(
                "You do not have permission to manage squad leads for this squad. "
                "You must be a Squad Lead or a server manager."
            )

    async def can_manage_squad_roster(self, user: discord.Member | discord.User, squad_id: UUID) -> bool:
        return await self.can_manage_squad_leads(user, squad_id)

    async def require_squad_roster_management(self, user: discord.Member | discord.User, squad_id: UUID) -> None:
        await self.require_squad_lead_management(user, squad_id)
