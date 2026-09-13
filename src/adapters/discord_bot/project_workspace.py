"""Concrete Discord Project Workspace and Control Hub lifecycle adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.forum_helpers import (
    ensure_pinned_hub_post,
    ensure_project_tag,
    setup_forum_tags,
)
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ITaskDiscordWorkspace,
    ProjectProvisionSpec,
    ProjectWorkspaceRef,
    RebuildProgress,
    RebuildWorkspaceResult,
)
from src.domain.exceptions import ProjectNotFoundError
from src.domain.models import Project, Squad

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


class DiscordProjectWorkspaceAdapter(IProjectDiscordWorkspace):
    """Deep implementation adapter managing Project Discord presence, tags, and Control Hubs."""

    def __init__(
        self,
        bot: discord.Client,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        task_workspace: ITaskDiscordWorkspace | None = None,
    ):
        self.bot = bot
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.auth_service = auth_service
        self.task_workspace = task_workspace

    @property
    def _effective_task_workspace(self) -> ITaskDiscordWorkspace | None:
        if self.task_workspace is not None:
            return self.task_workspace
        if isinstance(getattr(self.bot, "workspace", None), ITaskDiscordWorkspace):
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
        project = await self.project_service.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.")

        # If project is archived, unarchive it
        if project.is_archived:
            unarchived = await self.project_service.unarchive_project(project.id)
            if unarchived:
                project = unarchived

        forum_created = False
        resolved_channel = None
        warnings: list[str] = []

        if progress_callback:
            await _maybe_await(
                progress_callback(
                    RebuildProgress(
                        step="channel",
                        current=1,
                        total=4,
                        message=f"Resolving channel for project [{project.prefix}] {project.name}...",
                    )
                )
            )

        # 1. Target channel resolution
        if target_channel is not None:
            raw_channel = await self._resolve_channel(target_channel)
            validated = self._validate_channel_type(raw_channel)
            if validated:
                resolved_channel = validated
                await self.project_service.update_project_channel(project.id, validated.id)
                project = await self.project_service.get_by_id(project.id) or project
        else:
            if project.discord_channel_id:
                chan = None
                if hasattr(guild, "get_channel"):
                    chan = guild.get_channel(project.discord_channel_id)
                if not chan and hasattr(guild, "fetch_channel"):
                    try:
                        chan = await _maybe_await(guild.fetch_channel(project.discord_channel_id))
                    except Exception:
                        chan = None
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    resolved_channel = chan

            if not resolved_channel:
                # Auto-create ForumChannel
                target_category = None
                guild_categories = getattr(guild, "categories", [])
                if project.category:
                    target_category = next(
                        (c for c in guild_categories if c.name.lower() == project.category.lower()), None
                    )
                if not target_category:
                    target_category = next(
                        (c for c in guild_categories if "dgg-pm" in c.name.lower() or "projects" in c.name.lower()),
                        None,
                    )

                overwrites = {}
                role_ids = project.discord_role_ids or ([project.discord_role_id] if project.discord_role_id else [])
                for rid in role_ids:
                    role_obj = guild.get_role(rid) if hasattr(guild, "get_role") else None
                    if role_obj:
                        overwrites[role_obj] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            send_messages_in_threads=True,
                            create_public_threads=True,
                            attach_files=True,
                            embed_links=True,
                        )
                if project.lead_discord_id:
                    lead_member = guild.get_member(project.lead_discord_id) if hasattr(guild, "get_member") else None
                    if lead_member:
                        overwrites[lead_member] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            send_messages_in_threads=True,
                            create_public_threads=True,
                            manage_threads=True,
                        )

                clean_name = f"📁-{project.name.lower().replace(' ', '-')}"
                try:
                    create_kwargs: dict[str, Any] = {
                        "name": clean_name,
                        "category": target_category,
                        "reason": f"dgg-pm workspace rebuild for project {project.name}",
                    }
                    if overwrites:
                        create_kwargs["overwrites"] = overwrites
                    create_fn = getattr(guild, "create_forum", None) or getattr(guild, "create_forum_channel", None)
                    if not create_fn:
                        raise AttributeError("Target Discord guild does not support creating forum channels.")
                    new_forum = await _maybe_await(create_fn(**create_kwargs))
                    resolved_channel = new_forum
                    forum_created = True
                    await self.project_service.update_project_channel(project.id, new_forum.id)
                    project = await self.project_service.get_by_id(project.id) or project
                except Exception as e:
                    logger.error("Failed to auto-create ForumChannel for project %s: %s", project.id, e)
                    warnings.append(f"Could not auto-create Forum Channel: {e}")

        # 2. Forum tags & Control Hub
        tags_created = 0
        hub_rebuilt = False
        if resolved_channel and isinstance(resolved_channel, discord.ForumChannel):
            if progress_callback:
                await _maybe_await(
                    progress_callback(
                        RebuildProgress(
                            step="tags",
                            current=2,
                            total=4,
                            message=f"Configuring standard PM tags in #{resolved_channel.name}...",
                        )
                    )
                )
            added, _total, tag_err = await setup_forum_tags(resolved_channel)
            tags_created += added
            if tag_err:
                warnings.append(tag_err)
            proj_tag_err = await ensure_project_tag(resolved_channel, project.name)
            if proj_tag_err:
                warnings.append(proj_tag_err)

        if resolved_channel:
            if progress_callback:
                await _maybe_await(
                    progress_callback(
                        RebuildProgress(
                            step="hub",
                            current=3,
                            total=4,
                            message="Mounting pinned Control Hub post...",
                        )
                    )
                )
            try:
                hub_ok, hub_status = await ensure_pinned_hub_post(
                    channel=resolved_channel,
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    task_service=self.task_service,
                    user_service=self.user_service,
                    project_name=project.name,
                )
                hub_rebuilt = hub_ok
                if not hub_ok:
                    warnings.append(hub_status)
            except Exception as e:
                warnings.append(f"Could not mount Control Hub: {e}")

        # 3. Task Thread Reconciliation
        tasks_reconciled = 0
        tasks_recreated = 0
        tasks_archived = 0

        task_ws = self._effective_task_workspace
        if resolved_channel and self.task_service and task_ws:
            tasks, _ = await self.task_service.list_tasks(
                guild_id=guild.id,
                project_id=project.id,
                limit=1000,
                include_archived=True,
            )
            tasks.sort(key=lambda t: t.task_number)
            total_tasks = len(tasks)

            for idx, task in enumerate(tasks, start=1):
                if progress_callback:
                    await _maybe_await(
                        progress_callback(
                            RebuildProgress(
                                step="tasks",
                                current=idx,
                                total=total_tasks,
                                message=f"Reconciling task [{task.short_id}] ({idx}/{total_tasks})...",
                            )
                        )
                    )

                # Check if thread exists in Discord
                thread = None
                if task.discord_thread_id:
                    if hasattr(guild, "get_channel"):
                        thread = guild.get_channel(task.discord_thread_id)
                    if not thread and hasattr(self.bot, "get_channel"):
                        thread = self.bot.get_channel(task.discord_thread_id)
                    if not thread and hasattr(guild, "fetch_channel"):
                        try:
                            thread = await _maybe_await(guild.fetch_channel(task.discord_thread_id))
                        except Exception:
                            thread = None

                if thread and isinstance(thread, discord.Thread):
                    try:
                        sync_res = await task_ws.sync_workspace(
                            task,
                            project=project,
                            project_name=project.name,
                            sync_title=True,
                            sync_tags=True,
                            sync_archive=True,
                            sync_starter_card=True,
                        )
                        tasks_reconciled += 1
                        if hasattr(sync_res, "title_deferred") and sync_res.title_deferred:
                            warnings.append(
                                f"Task {task.short_id} title rename deferred due to Discord rate limits "
                                "(cooldown active)."
                            )
                    except Exception as e:
                        logger.warning("Failed to sync workspace for task %s: %s", task.short_id, e)
                        warnings.append(f"Failed to sync task {task.short_id}: {e}")
                else:
                    try:
                        ref = await task_ws.provision_workspace(
                            task,
                            project=project,
                            target_container=resolved_channel,
                        )
                        await self.task_service.update_discord_message_ids(
                            task_id=task.id,
                            discord_message_id=ref.message_id,
                            discord_thread_id=ref.thread_id,
                        )
                        tasks_recreated += 1

                        # Archive Invariant: If task is completed or archived, immediately archive the thread
                        if task.is_completed or task.is_archived:
                            created_thread = None
                            if hasattr(self.bot, "get_channel"):
                                created_thread = self.bot.get_channel(ref.thread_id)
                            if not created_thread and hasattr(guild, "get_channel"):
                                created_thread = guild.get_channel(ref.thread_id)
                            if not created_thread and hasattr(guild, "fetch_channel"):
                                try:
                                    created_thread = await _maybe_await(guild.fetch_channel(ref.thread_id))
                                except Exception:
                                    created_thread = None

                            if created_thread and hasattr(created_thread, "edit"):
                                await _maybe_await(created_thread.edit(archived=True))
                                tasks_archived += 1
                    except Exception as e:
                        logger.error("Failed to provision thread workspace for task %s: %s", task.short_id, e)
                        warnings.append(f"Failed to rebuild task {task.short_id}: {e}")

                if idx < total_tasks:
                    await asyncio.sleep(0.4)

        channel_id = resolved_channel.id if resolved_channel else (project.discord_channel_id or 0)
        return RebuildWorkspaceResult(
            project=project,
            channel_id=channel_id,
            forum_created=forum_created,
            tags_created=tags_created,
            hub_rebuilt=hub_rebuilt,
            tasks_reconciled=tasks_reconciled,
            tasks_recreated=tasks_recreated,
            tasks_archived=tasks_archived,
            warnings=tuple(warnings),
        )
