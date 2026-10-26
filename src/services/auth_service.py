"""Role and squad-based authorization service for dgg-pm."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.domain.actor import ActorPrincipal
from src.domain.enums import TaskStatus
from src.domain.exceptions import PermissionDeniedError
from src.domain.models import Project, Task
from src.ports.unit_of_work import IUnitOfWork, NullUnitOfWork

if TYPE_CHECKING:
    from src.ports.member_directory import MemberDirectoryPort
    from src.ports.repositories import IGuildLeadRoleRepository, IGuildLeadUserRepository
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService

logger = logging.getLogger("dgg_pm.services.auth")


class AuthService:
    """Evaluates role, squad, and server permissions for task mutations and project operations."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        guild_lead_role_repo: IGuildLeadRoleRepository | None = None,
        guild_lead_user_repo: IGuildLeadUserRepository | None = None,
        member_directory: MemberDirectoryPort | None = None,
        uow: IUnitOfWork | None = None,
    ):
        self.project_service = project_service
        self.squad_service = squad_service
        self._guild_lead_role_repo = guild_lead_role_repo
        self._guild_lead_user_repo = guild_lead_user_repo
        self.member_directory = member_directory
        self.uow = uow

    @property
    def guild_lead_role_repo(self) -> IGuildLeadRoleRepository | None:
        if self._guild_lead_role_repo is not None:
            return self._guild_lead_role_repo
        if self.uow is not None:
            return getattr(self.uow, "guild_lead_roles", None)
        return None

    @guild_lead_role_repo.setter
    def guild_lead_role_repo(self, val: IGuildLeadRoleRepository | None) -> None:
        self._guild_lead_role_repo = val

    @property
    def guild_lead_user_repo(self) -> IGuildLeadUserRepository | None:
        if self._guild_lead_user_repo is not None:
            return self._guild_lead_user_repo
        if self.uow is not None:
            return getattr(self.uow, "guild_lead_users", None)
        return None

    @guild_lead_user_repo.setter
    def guild_lead_user_repo(self, val: IGuildLeadUserRepository | None) -> None:
        self._guild_lead_user_repo = val

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[IUnitOfWork]:
        if self.uow is not None:
            async with self.uow as active_uow:
                yield active_uow
        else:
            yield NullUnitOfWork(
                guild_lead_roles=self.guild_lead_role_repo,
                guild_lead_users=self.guild_lead_user_repo,
            )

    @staticmethod
    def to_principal(user: ActorPrincipal | Any, guild_id: int | None = None) -> ActorPrincipal:
        """Coerces an actor (ActorPrincipal, discord.Member, discord.User, or mock) into an ActorPrincipal."""
        if isinstance(user, ActorPrincipal):
            if guild_id is not None and user.guild_id is None:
                return ActorPrincipal(
                    user_id=user.user_id,
                    guild_id=guild_id,
                    role_ids=user.role_ids,
                    is_guild_admin=user.is_guild_admin,
                )
            return user

        user_id = getattr(user, "user_id", getattr(user, "id", None))
        resolved_guild_id = guild_id or getattr(getattr(user, "guild", None), "id", None)

        role_ids: set[int] = set()
        roles = getattr(user, "roles", None)
        if roles and isinstance(roles, (list, tuple, set)):
            for r in roles:
                rid = getattr(r, "id", None)
                if isinstance(rid, int):
                    role_ids.add(rid)

        perms = getattr(user, "guild_permissions", None)
        is_admin = False
        if perms is not None:
            is_admin = bool(getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False))

        return ActorPrincipal(
            user_id=int(user_id) if isinstance(user_id, int) else 0,
            guild_id=int(resolved_guild_id) if isinstance(resolved_guild_id, int) else None,
            role_ids=frozenset(role_ids),
            is_guild_admin=is_admin,
        )

    async def add_guild_lead_role(self, guild_id: int, discord_role_id: int) -> None:
        """Registers a Discord role as an authorized Team Lead role for the guild."""
        async with self._transaction() as uow:
            if uow.guild_lead_roles:
                await uow.guild_lead_roles.add_lead_role(guild_id, discord_role_id)

    async def remove_guild_lead_role(self, guild_id: int, discord_role_id: int) -> bool:
        """Removes a Discord role from authorized Team Lead roles for the guild."""
        async with self._transaction() as uow:
            if uow.guild_lead_roles:
                return await uow.guild_lead_roles.remove_lead_role(guild_id, discord_role_id)
            return False

    async def list_guild_lead_roles(self, guild_id: int) -> set[int]:
        """Returns all authorized Team Lead role IDs for the guild."""
        if self.guild_lead_role_repo:
            return await self.guild_lead_role_repo.list_lead_role_ids(guild_id)
        return set()

    async def add_guild_lead_user(self, guild_id: int, user_discord_id: int) -> None:
        """Registers a Discord user as an authorized Team Lead for the guild."""
        async with self._transaction() as uow:
            if uow.guild_lead_users:
                await uow.guild_lead_users.add_lead_user(guild_id, user_discord_id)

    async def remove_guild_lead_user(self, guild_id: int, user_discord_id: int) -> bool:
        """Removes a Discord user from authorized Team Leads for the guild."""
        async with self._transaction() as uow:
            if uow.guild_lead_users:
                return await uow.guild_lead_users.remove_lead_user(guild_id, user_discord_id)
            return False

    async def list_guild_lead_users(self, guild_id: int) -> set[int]:
        """Returns all authorized Team Lead user IDs for the guild."""
        if self.guild_lead_user_repo:
            return await self.guild_lead_user_repo.list_lead_user_ids(guild_id)
        return set()

    async def can_manage_projects(
        self,
        user: ActorPrincipal | Any,
        guild_id: int | None = None,
        guild: Any | None = None,
    ) -> bool:
        """Checks if user has authority to create and manage projects and squads:
        1. Discord Server Manager (manage_guild or administrator).
        2. Authorized Individual Team Lead members (guild_lead_users).
        3. Configured Team Lead roles (guild_lead_roles).
        4. Active Squad Leads in the guild.
        """
        if self.is_server_manager(user):
            return True

        target_guild_id = (
            guild_id
            or (guild.id if (guild and hasattr(guild, "id") and isinstance(guild.id, int)) else None)
            or getattr(getattr(user, "guild", None), "id", None)
            or (user.guild_id if isinstance(user, ActorPrincipal) else None)
        )
        if not target_guild_id:
            return False

        # 2. Check configured individual Team Lead members
        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
        if self.guild_lead_user_repo and user_id:
            lead_user_ids = await self.guild_lead_user_repo.list_lead_user_ids(target_guild_id)
            if user_id in lead_user_ids:
                return True

        # If user is missing roles, attempt to resolve member from directory
        member = user
        has_roles = False
        if isinstance(member, ActorPrincipal) and member.role_ids:
            has_roles = True
        elif hasattr(member, "roles") and getattr(member, "roles", None):
            has_roles = True

        if not has_roles and self.member_directory and user_id:
            resolved = await self.member_directory.get_member_principal(target_guild_id, user_id)
            if resolved:
                member = resolved
                has_roles = bool(resolved.role_ids)

        # 3. Check configured Team Lead roles
        if self.guild_lead_role_repo:
            lead_role_ids = await self.guild_lead_role_repo.list_lead_role_ids(target_guild_id)
            if lead_role_ids:
                if isinstance(member, ActorPrincipal):
                    user_role_ids = set(member.role_ids)
                else:
                    user_role_ids = {r.id for r in getattr(member, "roles", []) if hasattr(r, "id")}
                if any(rid in user_role_ids for rid in lead_role_ids):
                    return True

        # 4. Check if active Squad Lead in this guild
        if self.squad_service and user_id:
            squads = await self.squad_service.list_squads(target_guild_id)
            for s in squads:
                if await self.squad_service.is_squad_lead(s.id, user_id):
                    return True

        return False

    async def require_project_management(
        self,
        user: ActorPrincipal | Any,
        guild_id: int | None = None,
        guild: Any | None = None,
    ) -> None:
        """Raises PermissionDeniedError if the user cannot create or manage projects and squads."""
        if not await self.can_manage_projects(user, guild_id=guild_id, guild=guild):
            raise PermissionDeniedError(
                "You do not have permission to manage projects or squads. "
                "You must be a Discord Server Manager, an authorized Squad Lead, or hold an authorized Lead role.",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                guild_id=guild_id,
            )

    @staticmethod
    def is_server_manager(user: ActorPrincipal | Any) -> bool:
        """Checks if user has Discord administrator or manage_guild permissions."""
        if isinstance(user, ActorPrincipal):
            return user.is_guild_admin
        perms = getattr(user, "guild_permissions", None)
        if perms is not None:
            admin = getattr(perms, "administrator", False)
            manage_guild = getattr(perms, "manage_guild", False)
            return bool(admin or manage_guild)
        return False

    async def is_project_lead(self, user: ActorPrincipal | Any, project_id: UUID | None) -> bool:
        """Checks if the user is designated as the Project Lead or is a server manager."""
        if not project_id:
            return False
        if self.is_server_manager(user):
            return True
        project = await self.project_service.get_by_id(project_id)
        if not project or not project.lead_discord_id:
            return False
        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
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

    async def can_mutate_task(self, user: ActorPrincipal | Any, task: Task) -> bool:
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

        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)

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
                    if isinstance(user, ActorPrincipal):
                        user_role_ids = set(user.role_ids)
                    else:
                        roles = getattr(user, "roles", [])
                        user_role_ids = {r.id for r in roles if hasattr(r, "id")}
                    if any(rid in user_role_ids for rid in project_roles):
                        return True

        return False

    async def require_task_mutation(self, user: ActorPrincipal | Any, task: Task) -> None:
        """Raises PermissionDeniedError if the user is not authorized to mutate the task."""
        if not await self.can_mutate_task(user, task):
            raise PermissionDeniedError(
                "You do not have permission to modify this task. "
                "You must be the assignee, creator, project lead, a squad member, or a server manager.",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                project_id=task.project_id,
            )

    async def can_delete_task(self, user: ActorPrincipal | Any, task: Task) -> bool:
        """Determines if a user is authorized to delete a task.

        Authorized if:
        1. Server Manager (manage_guild or administrator).
        2. Designated Project Lead for the task's project.
        3. Original task creator, strictly if the task status is notStarted.
        """
        if self.is_server_manager(user):
            return True

        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
        if user_id is None:
            return False

        # Project Lead
        if task.project_id:
            project = await self.project_service.get_by_id(task.project_id)
            if project and project.lead_discord_id and user_id == project.lead_discord_id:
                return True

        # Task Creator (only if status is notStarted)
        if user_id == task.creator_discord_id and task.status == TaskStatus.NOT_STARTED:
            return True

        return False

    async def require_task_deletion(self, user: ActorPrincipal | Any, task: Task) -> None:
        """Raises PermissionDeniedError if the user is not authorized to delete the task."""
        if not await self.can_delete_task(user, task):
            raise PermissionDeniedError(
                "You do not have permission to delete this task. "
                "Only Server Managers, Project Leads, or the task creator (if not started) can delete tasks.",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                project_id=task.project_id,
            )

    async def can_bypass_dependencies(self, user: ActorPrincipal | Any, task: Task) -> bool:
        """Determines if a user has authority to bypass unresolved prerequisite blockers.

        Server Managers, designated Project Leads, and authorized task mutators
        can bypass with interactive confirmation.
        """
        if self.is_server_manager(user):
            return True
        if task.project_id and await self.is_project_lead(user, task.project_id):
            return True
        return await self.can_mutate_task(user, task)

    async def require_dependency_bypass(self, user: ActorPrincipal | Any, task: Task) -> None:
        """Raises PermissionDeniedError if the user is not authorized to bypass prerequisite blockers."""
        if not await self.can_bypass_dependencies(user, task):
            raise PermissionDeniedError(
                f"You do not have permission to bypass unresolved prerequisite blockers for [{task.short_id}].",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                project_id=task.project_id,
            )

    async def can_create_task_in_project(
        self,
        user: ActorPrincipal | Any,
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

        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)

        if not project_id:
            # Standalone tasks are restricted to Server Managers (handled above) and Squad Leads
            if self.squad_service and user_id:
                guild = getattr(user, "guild", None)
                target_guild_id = (
                    guild.id
                    if (guild and hasattr(guild, "id") and isinstance(guild.id, int))
                    else (guild_id or (user.guild_id if isinstance(user, ActorPrincipal) else None))
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
            if isinstance(user, ActorPrincipal):
                user_role_ids = set(user.role_ids)
            else:
                roles = getattr(user, "roles", [])
                user_role_ids = {r.id for r in roles if hasattr(r, "id")}
            return any(rid in user_role_ids for rid in project_roles)

        # If project has no squad roles mapped, open to all server members
        return True

    async def require_task_creation(
        self,
        user: ActorPrincipal | Any,
        project_id: UUID | None,
        guild_id: int | None = None,
    ) -> None:
        """Raises PermissionDeniedError if the user cannot create tasks in the project."""
        if not await self.can_create_task_in_project(user, project_id, guild_id=guild_id):
            user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
            if not project_id:
                raise PermissionDeniedError(
                    "You do not have permission to create standalone tasks (tasks outside an active project). "
                    "You must be a Squad Lead or a server manager, or create the task inside an active project.",
                    user_id=user_id,
                )
            raise PermissionDeniedError(
                "You do not have permission to create tasks in this project. "
                "You must hold the project squad's Discord role, be the Project Lead, or be a server manager.",
                user_id=user_id,
                project_id=project_id,
            )

    async def can_view_project(self, user: ActorPrincipal | Any, project_id: UUID) -> bool:
        """Determines if a user is authorized to view a project's details, task list, or visual tech tree graph.

        Authorized if:
        1. User is a Discord Server Manager (manage_guild / administrator).
        2. User is the designated Project Lead.
        3. User holds any of the project's mapped squad Discord Roles.
        4. If the project has NO role mapping and NO assigned squads: open to all server members.
        """
        if self.is_server_manager(user):
            return True

        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
        project = await self.project_service.get_by_id(project_id)
        if not project:
            return False

        if project.lead_discord_id and user_id == project.lead_discord_id:
            return True

        project_roles = await self.get_project_role_ids(project)
        if project_roles:
            if isinstance(user, ActorPrincipal):
                user_role_ids = set(user.role_ids)
            else:
                roles = getattr(user, "roles", [])
                user_role_ids = {r.id for r in roles if hasattr(r, "id")}
            return any(rid in user_role_ids for rid in project_roles)

        # If project has no squad roles mapped, open to all server members
        return True

    async def require_project_view(self, user: ActorPrincipal | Any, project_id: UUID) -> None:
        """Raises PermissionDeniedError if the user cannot view the project or its visual graph."""
        if not await self.can_view_project(user, project_id):
            raise PermissionDeniedError(
                "You do not have permission to view this project's visual graph. "
                "You must hold the project squad's Discord role, be the Project Lead, or be a server manager.",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                project_id=project_id,
            )

    async def can_assign_task_to_user(
        self,
        guild: Any | None,
        target_user: ActorPrincipal | Any | int | None,
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

        user_id = (
            target_user
            if isinstance(target_user, int)
            else (target_user.user_id if isinstance(target_user, ActorPrincipal) else getattr(target_user, "id", None))
        )
        if project.lead_discord_id and user_id == project.lead_discord_id:
            return True

        # Check if target_user already has roles or role_ids
        target_role_ids: set[int] | None = None
        if isinstance(target_user, ActorPrincipal):
            target_role_ids = set(target_user.role_ids)
        elif getattr(target_user, "roles", None) is not None:
            roles = target_user.roles
            if isinstance(roles, (list, tuple, set)):
                target_role_ids = {r.id for r in roles if hasattr(r, "id")}

        # If roles not on object, resolve via member_directory
        if target_role_ids is None and user_id:
            target_guild_id = project.guild_id or (guild.id if (guild and hasattr(guild, "id")) else None)
            if self.member_directory and target_guild_id:
                principal = await self.member_directory.get_member_principal(target_guild_id, user_id)
                if principal:
                    target_role_ids = set(principal.role_ids)

        project_roles = await self.get_project_role_ids(project)
        if not project_roles:
            return True

        if target_role_ids is None:
            return False

        return any(rid in target_role_ids for rid in project_roles)

    async def require_task_assignee_eligibility(
        self,
        guild: Any | None,
        target_user: ActorPrincipal | Any | int | None,
        project_id: UUID | None,
    ) -> None:
        """Raises PermissionDeniedError if the target user is not eligible for assignment."""
        if not await self.can_assign_task_to_user(guild, target_user, project_id):
            user_id = (
                target_user
                if isinstance(target_user, int)
                else (
                    target_user.user_id if isinstance(target_user, ActorPrincipal) else getattr(target_user, "id", None)
                )
            )
            project = await self.project_service.get_by_id(project_id) if project_id else None
            role_names: list[str] = []
            project_roles: set[int] = set()
            if project:
                project_roles = await self.get_project_role_ids(project)
                for rid in sorted(project_roles):
                    if self.squad_service:
                        squad = await self.squad_service.get_by_role_id(project.guild_id, rid)
                        if squad:
                            role_names.append(f"@{squad.name}")
                        else:
                            role_names.append(f"Role({rid})")
                    else:
                        role_names.append(f"Role({rid})")

            roles_str = ", ".join(role_names) if role_names else "a squad role"
            proj_str = f"project '{project.name}'" if project else "this project"
            user_label = f"User {user_id}" if user_id else "User"
            raise PermissionDeniedError(
                f"{user_label} does not hold an eligible squad Discord role for {proj_str}. "
                f"Required role(s): {roles_str}. Only members with these roles or the Project Lead can be assigned.",
                user_id=user_id,
                required_role_ids=project_roles,
                project_id=project_id,
            )

    async def can_manage_squad_leads(self, user: ActorPrincipal | Any, squad_id: UUID) -> bool:
        """Determines if a user can designate or remove squad leads.

        Authorized if:
        1. User has Server Manager permissions (manage_guild / administrator).
        2. User is designated as a Squad Lead in database AND currently holds the squad's Discord role.
        """
        if self.is_server_manager(user):
            return True

        user_id = user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None)
        if user_id is None or not isinstance(user_id, int):
            return False

        if not self.squad_service:
            return False

        is_lead = await self.squad_service.is_squad_lead(squad_id, user_id)
        if not is_lead:
            return False

        # Verify that the user still currently holds the squad's Discord role
        if isinstance(user, ActorPrincipal):
            user_role_ids = set(user.role_ids)
            squad = await self.squad_service.get_by_id(squad_id)
            if squad and squad.discord_role_id not in user_role_ids:
                return False
        elif hasattr(user, "roles"):
            squad = await self.squad_service.get_by_id(squad_id)
            if squad:
                user_role_ids = {r.id for r in getattr(user, "roles", []) if hasattr(r, "id")}
                if squad.discord_role_id not in user_role_ids:
                    return False

        return True

    async def require_squad_lead_management(self, user: ActorPrincipal | Any, squad_id: UUID) -> None:
        """Raises PermissionDeniedError if the user cannot manage squad leads."""
        if not await self.can_manage_squad_leads(user, squad_id):
            raise PermissionDeniedError(
                "You do not have permission to manage squad leads for this squad. "
                "You must be a Squad Lead or a server manager.",
                user_id=user.user_id if isinstance(user, ActorPrincipal) else getattr(user, "id", None),
                squad_id=squad_id,
            )

    async def can_manage_squad_roster(self, user: ActorPrincipal | Any, squad_id: UUID) -> bool:
        return await self.can_manage_squad_leads(user, squad_id)

    async def require_squad_roster_management(self, user: ActorPrincipal | Any, squad_id: UUID) -> None:
        await self.require_squad_lead_management(user, squad_id)
