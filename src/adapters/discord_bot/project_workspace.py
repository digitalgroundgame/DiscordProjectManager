"""Concrete Discord Project Workspace and Control Hub lifecycle adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.project_rebuilder import rebuild_project_workspace
from src.adapters.discord_bot.views.forum_helpers import (
    ensure_pinned_hub_post,
    ensure_project_tag,
    setup_forum_tags,
)
from src.domain.exceptions import ProjectNotFoundError
from src.domain.models import Project, Squad
from src.ports.workspace import (
    ProjectProvisionSpec,
    ProjectWorkspacePort,
    ProjectWorkspaceRef,
    RebuildWorkspaceResult,
    TaskWorkspacePort,
)

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.project_workspace")


async def _maybe_await(res: Any) -> Any:
    """Awaits an object if it is an awaitable or coroutine."""
    if hasattr(res, "__await__"):
        return await res
    return res


class DiscordProjectWorkspaceAdapter(ProjectWorkspacePort):
    """Deep implementation adapter managing Project Discord presence, tags, and Control Hubs."""

    def __init__(
        self,
        bot: discord.Client,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        task_workspace: TaskWorkspacePort | None = None,
    ):
        self.bot = bot
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.auth_service = auth_service
        self.task_workspace = task_workspace

    @property
    def _effective_task_workspace(self) -> TaskWorkspacePort | None:
        if self.task_workspace is not None:
            return self.task_workspace
        if isinstance(getattr(self.bot, "workspace", None), TaskWorkspacePort):
            return self.bot.workspace
        if self.task_service and self.project_service:
            from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

            return DiscordTaskWorkspaceAdapter(self.bot, self.task_service, self.project_service, self.auth_service)
        return None

    async def _resolve_channel(
        self,
        channel_input: discord.abc.GuildChannel | discord.Thread | int | None,
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        """Resolves target channel object from snowflake ID or channel instance."""
        if channel_input is None:
            return None
        if isinstance(channel_input, (discord.abc.GuildChannel, discord.Thread)):
            return channel_input

        if isinstance(channel_input, int):
            chan = None
            if hasattr(self.bot, "get_channel"):
                chan = self.bot.get_channel(channel_input)
            if not chan and hasattr(self.bot, "fetch_channel"):
                try:
                    chan = await _maybe_await(self.bot.fetch_channel(channel_input))
                except Exception:
                    chan = None
            if isinstance(chan, (discord.abc.GuildChannel, discord.Thread)):
                return chan

        return None

    def _validate_channel_type(
        self,
        channel: discord.abc.GuildChannel | discord.Thread | None,
    ) -> discord.ForumChannel | discord.TextChannel | None:
        """Validates that a channel is a supported container (ForumChannel or TextChannel)."""
        if channel is None:
            return None
        if isinstance(channel, discord.Thread):
            # If a thread was passed (e.g. clicked inside a thread), check if its parent is a ForumChannel
            parent = getattr(channel, "parent", None)
            if isinstance(parent, discord.ForumChannel):
                return parent
            raise ValueError(
                f"Projects cannot be bound to a Thread Workspace (<#{channel.id}>). "
                "Please select a Discord Forum Channel."
            )
        if isinstance(channel, (discord.ForumChannel, discord.TextChannel)):
            return channel
        raise ValueError(
            f"Channel <#{channel.id}> of type {type(channel).__name__} is not supported. "
            "Projects must be bound to a Discord Forum Channel."
        )

    async def provision_project(self, spec: ProjectProvisionSpec) -> ProjectWorkspaceRef:
        """Atomically provisions a Project, Squad role binding, Forum tags, and pinned Control Hub."""
        resolved_channel = await self._resolve_channel(spec.channel)
        validated_channel = self._validate_channel_type(resolved_channel)
        channel_id = (
            validated_channel.id if validated_channel else (spec.channel if isinstance(spec.channel, int) else None)
        )

        # Resolve all roles
        spec_roles: list[discord.Role | int] = list(spec.roles or [])
        if spec.role is not None and spec.role not in spec_roles:
            spec_roles.insert(0, spec.role)

        role_info_list: list[tuple[int, str]] = []
        for r in spec_roles:
            if isinstance(r, discord.Role):
                role_info_list.append((r.id, r.name))
            elif isinstance(r, int):
                role_info_list.append((r, f"Squad-{r}"))

        role_ids = [rid for rid, _ in role_info_list]

        # 1. Persist Project in Database
        project = await self.project_service.create_project(
            guild_id=spec.guild_id,
            name=spec.name,
            prefix=spec.prefix,
            description=spec.description,
            discord_channel_id=channel_id,
            discord_role_ids=role_ids,
            lead_discord_id=spec.lead_discord_id,
            category=spec.category,
        )

        # 2. 1:1 Squad Role Mapping
        squad: Squad | None = None
        if self.squad_service:
            for rid, rname in role_info_list:
                try:
                    s = await self.squad_service.get_or_create_squad_for_role(
                        guild_id=spec.guild_id,
                        role_id=rid,
                        role_name=rname,
                    )
                    await self.project_service.assign_squad_to_project(project_id=project.id, squad_id=s.id)
                    if squad is None:
                        squad = s
                except Exception as e:
                    logger.warning("Failed to map squad %s for project %s: %s", rid, project.id, e)

        # 3. Discord Workspace Tag Setup and Control Hub Mounting
        tags_created = 0
        warnings: list[str] = []
        hub_thread_id: int | None = None
        hub_msg_id: int | None = None
        jump_url: str | None = None

        if validated_channel:
            if isinstance(validated_channel, discord.ForumChannel):
                added, _total, tag_err = await setup_forum_tags(validated_channel)
                tags_created += added
                if tag_err:
                    warnings.append(tag_err)
                proj_tag_err = await ensure_project_tag(validated_channel, project.name)
                if proj_tag_err:
                    warnings.append(proj_tag_err)

            # Ensure Pinned Control Hub
            try:
                hub_ok, hub_status = await ensure_pinned_hub_post(
                    channel=validated_channel,
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    task_service=self.task_service,
                    user_service=self.user_service,
                    project_name=project.name,
                )
                if not hub_ok:
                    warnings.append(hub_status)
            except Exception as e:
                logger.warning("Failed to mount Control Hub in %s: %s", validated_channel.id, e)
                warnings.append(f"Could not mount Control Hub: {e}")

            # Derive Jump URL and IDs
            guild_id = getattr(validated_channel.guild, "id", spec.guild_id)
            if isinstance(validated_channel, discord.ForumChannel):
                # Search for the pinned control hub thread
                hub_thread = None
                threads = getattr(validated_channel, "threads", [])
                for t in threads:
                    if "Control Hub" in t.name or "Management Hub" in t.name:
                        hub_thread = t
                        break
                if hub_thread:
                    hub_thread_id = hub_thread.id
                    jump_url = getattr(
                        hub_thread, "jump_url", f"https://discord.com/channels/{guild_id}/{hub_thread.id}"
                    )
                else:
                    import re

                    m = re.search(r"<#(\d+)>", hub_status)
                    if m:
                        hub_thread_id = int(m.group(1))
                        jump_url = f"https://discord.com/channels/{guild_id}/{hub_thread_id}"
            elif isinstance(validated_channel, discord.TextChannel):
                jump_url = f"https://discord.com/channels/{guild_id}/{validated_channel.id}"

        return ProjectWorkspaceRef(
            project=project,
            squad=squad,
            channel_id=channel_id,
            control_hub_thread_id=hub_thread_id,
            control_hub_message_id=hub_msg_id,
            tags_created=tags_created,
            jump_url=jump_url,
            warnings=tuple(warnings),
        )

    async def sync_control_hub(
        self,
        channel: discord.abc.GuildChannel | int,
        *,
        project_id: UUID | None = None,
        guild_id: int | None = None,
    ) -> ProjectWorkspaceRef | None:
        """Synchronizes or repairs the pinned Control Hub post and standard tags in a channel."""
        resolved = await self._resolve_channel(channel)
        validated = self._validate_channel_type(resolved)
        if not validated:
            return None

        project: Project | None = None
        if project_id:
            project = await self.project_service.get_by_id(project_id)
        if not project and validated:
            project = await self.project_service.get_by_channel_id(
                guild_id=getattr(validated.guild, "id", guild_id or 0),
                channel_id=validated.id,
            )

        tags_created = 0
        warnings: list[str] = []
        if isinstance(validated, discord.ForumChannel):
            added, _total, tag_err = await setup_forum_tags(validated)
            tags_created += added
            if tag_err:
                warnings.append(tag_err)
            if project:
                proj_tag_err = await ensure_project_tag(validated, project.name)
                if proj_tag_err:
                    warnings.append(proj_tag_err)

        proj_name = project.name if project else None
        try:
            hub_ok, hub_status = await ensure_pinned_hub_post(
                channel=validated,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                project_name=proj_name,
            )
            if not hub_ok:
                warnings.append(hub_status)
        except Exception as e:
            warnings.append(f"Failed to refresh Control Hub: {e}")

        if not project:
            return None

        jump_url = f"https://discord.com/channels/{getattr(validated.guild, 'id', 0)}/{validated.id}"
        return ProjectWorkspaceRef(
            project=project,
            channel_id=validated.id,
            tags_created=tags_created,
            jump_url=jump_url,
            warnings=tuple(warnings),
        )

    async def rebind_channel(
        self,
        project_id: UUID,
        new_channel: discord.abc.GuildChannel | int | None,
    ) -> ProjectWorkspaceRef:
        """Migrates a Project to a new channel, updating DB links, tags, and Control Hubs."""
        project = await self.project_service.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.")

        resolved = await self._resolve_channel(new_channel)
        validated = self._validate_channel_type(resolved)
        new_channel_id = validated.id if validated else (new_channel if isinstance(new_channel, int) else None)

        # Update channel in DB
        updated_project = await self.project_service.update_project_channel(project_id, new_channel_id)
        if not updated_project:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' could not be updated.")

        tags_created = 0
        warnings: list[str] = []
        jump_url: str | None = None

        if validated:
            if isinstance(validated, discord.ForumChannel):
                added, _total, tag_err = await setup_forum_tags(validated)
                tags_created += added
                if tag_err:
                    warnings.append(tag_err)
                proj_tag_err = await ensure_project_tag(validated, updated_project.name)
                if proj_tag_err:
                    warnings.append(proj_tag_err)

            try:
                hub_ok, hub_status = await ensure_pinned_hub_post(
                    channel=validated,
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    task_service=self.task_service,
                    user_service=self.user_service,
                    project_name=updated_project.name,
                )
                if not hub_ok:
                    warnings.append(hub_status)
            except Exception as e:
                warnings.append(f"Could not mount Control Hub in new channel: {e}")

            jump_url = f"https://discord.com/channels/{getattr(validated.guild, 'id', 0)}/{validated.id}"

        return ProjectWorkspaceRef(
            project=updated_project,
            channel_id=new_channel_id,
            tags_created=tags_created,
            jump_url=jump_url,
            warnings=tuple(warnings),
        )

    async def rebuild_workspace(
        self,
        project_id: UUID,
        *,
        guild: discord.Guild,
        target_channel: discord.abc.GuildChannel | int | None = None,
        progress_callback: Any | None = None,
    ) -> RebuildWorkspaceResult:
        """Reconstructs and reconciles a Project's Discord presence from database state."""
        return await rebuild_project_workspace(
            self,
            project_id=project_id,
            guild=guild,
            target_channel=target_channel,
            progress_callback=progress_callback,
        )
