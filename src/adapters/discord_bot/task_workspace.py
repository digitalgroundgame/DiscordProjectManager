"""Concrete Discord Task Workspace & Thread Lifecycle implementation adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.thread_rate_limiter import ThreadRenameRateLimiter
from src.adapters.discord_bot.views.forum_helpers import (
    resolve_forum_tags,
    unarchive_thread_if_needed,
)
from src.adapters.discord_bot.views.task_buttons import (
    TaskActionView,
    TaskQuickControlsView,
    build_task_controls_embed,
)
from src.adapters.discord_bot.views.task_dependency_view import (
    TaskDependencyView,
    build_dependency_embed,
)
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_task_history_embed,
    build_thread_workspace_content,
)
from src.adapters.discord_bot.workspace_protocol import (
    _UNSET,
    ITaskDiscordWorkspace,
    SyncWorkspaceResult,
    TaskControlPanel,
    TaskWorkspaceRef,
)
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.exceptions import StaleVersionError
from src.domain.models import Project, Task, TaskHistory
from src.services.auth_service import AuthService

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.task_workspace")


async def _maybe_await(res: Any) -> Any:
    """Awaits an object if it is an awaitable or coroutine."""
    if hasattr(res, "__await__"):
        return await res
    return res


class DiscordTaskWorkspaceAdapter(ITaskDiscordWorkspace):
    """Deep implementation adapter managing Task Discord presence and thread lifecycles."""

    def __init__(
        self,
        bot: discord.Client,
        task_service: TaskService,
        project_service: ProjectService | None = None,
        auth_service: AuthService | None = None,
        rename_limiter: ThreadRenameRateLimiter | None = None,
    ):
        self.bot = bot
        self.task_service = task_service
        self.project_service = project_service
        self.auth_service = auth_service or (AuthService(project_service=project_service) if project_service else None)
        self.rename_limiter = rename_limiter or ThreadRenameRateLimiter()

    async def _resolve_channel(
        self,
        target_container: discord.abc.GuildChannel | discord.Thread | int | None = None,
        preferred_channel_id: int | None = None,
        project: Project | None = None,
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        """Resolves target channel/thread from parameters or project configuration."""
        fallback_chan = (
            target_container if isinstance(target_container, (discord.abc.GuildChannel, discord.Thread)) else None
        )

        # 1. If project has a designated channel, try resolving it first
        if project and project.discord_channel_id:
            chan = None
            if hasattr(self.bot, "get_channel"):
                chan = self.bot.get_channel(project.discord_channel_id)
            if not isinstance(chan, (discord.abc.GuildChannel, discord.Thread)) and hasattr(self.bot, "fetch_channel"):
                try:
                    chan = await _maybe_await(self.bot.fetch_channel(project.discord_channel_id))
                except Exception:
                    chan = None
            if isinstance(chan, (discord.abc.GuildChannel, discord.Thread)):
                if isinstance(chan, discord.Thread) and isinstance(getattr(chan, "parent", None), discord.ForumChannel):
                    return chan.parent
                return chan

        # 2. If target_container was passed directly as a channel
        if fallback_chan:
            if isinstance(fallback_chan, discord.Thread) and isinstance(
                getattr(fallback_chan, "parent", None), discord.ForumChannel
            ):
                return fallback_chan.parent
            return fallback_chan

        # 3. Try preferred_channel_id or integer target_container
        target_id = preferred_channel_id or (target_container if isinstance(target_container, int) else None)
        if target_id:
            chan = None
            if hasattr(self.bot, "get_channel"):
                chan = self.bot.get_channel(target_id)
            if not isinstance(chan, (discord.abc.GuildChannel, discord.Thread)) and hasattr(self.bot, "fetch_channel"):
                try:
                    chan = await _maybe_await(self.bot.fetch_channel(target_id))
                except Exception:
                    chan = None
            if isinstance(chan, (discord.abc.GuildChannel, discord.Thread)):
                if isinstance(chan, discord.Thread) and isinstance(getattr(chan, "parent", None), discord.ForumChannel):
                    return chan.parent
                return chan

        return None

    async def provision_workspace(
        self,
        task: Task,
        *,
        project: Project | None = None,
        target_container: discord.abc.GuildChannel | discord.Thread | int | None = None,
        preferred_channel_id: int | None = None,
    ) -> TaskWorkspaceRef:
        """Provisions a new Discord Thread Workspace and mounts the Task Action Card."""
        channel = await self._resolve_channel(
            target_container=target_container,
            preferred_channel_id=preferred_channel_id,
            project=project,
        )
        if not channel:
            raise ValueError(f"Could not resolve a valid Discord Forum or Text channel for task [{task.short_id}].")

        thread_name = f"[{task.short_id}] {task.title}"
        if len(thread_name) > 100:
            thread_name = thread_name[:97] + "..."

        project_name = project.name if project else None
        view = TaskActionView(
            task_id=task.id,
            current_status=task.status,
            current_priority=task.priority,
            task_service=self.task_service,
            current_assignee_id=task.assignee_discord_id,
            current_watchers=task.watchers,
        )

        if isinstance(channel, discord.ForumChannel):
            applied_tags = resolve_forum_tags(
                channel,
                task=task,
                project_name=project_name,
            )
            embed = build_task_embed(task, project_name=project_name)
            content = build_thread_workspace_content(task)

            create_kwargs = {
                "name": thread_name,
                "content": content,
                "embed": embed,
                "view": view,
                "auto_archive_duration": 10080,
            }
            if applied_tags:
                create_kwargs["applied_tags"] = applied_tags

            thread_res = await _maybe_await(channel.create_thread(**create_kwargs))
            thread = (
                thread_res.thread
                if hasattr(thread_res, "thread") and not hasattr(thread_res, "_mock_name")
                else (getattr(thread_res, "thread", None) or thread_res)
            )
            msg = getattr(thread_res, "message", None) or getattr(thread, "starter_message", None)
            msg_id = getattr(msg, "id", None) or getattr(thread, "id", channel.id)

            jump_url = (
                getattr(msg, "jump_url", None)
                or getattr(thread, "jump_url", None)
                or f"https://discord.com/channels/{getattr(channel.guild, 'id', 0)}/{getattr(thread, 'id', 0)}"
            )

            return TaskWorkspaceRef(
                thread_id=getattr(thread, "id", 0),
                message_id=msg_id,
                channel_id=channel.id,
                jump_url=jump_url,
            )

        elif isinstance(channel, discord.TextChannel):
            embed = build_task_embed(task, project_name=project_name)
            msg = await _maybe_await(channel.send(embed=embed, view=view))
            thread = await _maybe_await(msg.create_thread(name=thread_name, auto_archive_duration=10080))

            workspace_content = build_thread_workspace_content(task)
            await _maybe_await(thread.send(content=workspace_content))

            jump_url = getattr(
                msg,
                "jump_url",
                f"https://discord.com/channels/{getattr(channel.guild, 'id', 0)}/{channel.id}/{getattr(msg, 'id', 0)}",
            )
            return TaskWorkspaceRef(
                thread_id=getattr(thread, "id", 0),
                message_id=getattr(msg, "id", 0),
                channel_id=channel.id,
                jump_url=jump_url,
            )

        raise ValueError(f"Target container {channel} of type {type(channel)} is not supported for task workspaces.")

    async def sync_workspace(
        self,
        task: Task,
        *,
        project: Project | None = None,
        project_name: str | None = None,
        sync_title: bool = False,
        sync_tags: bool = True,
        sync_archive: bool = True,
        sync_starter_card: bool = True,
    ) -> bool:
        """Synchronizes an existing Thread Workspace with the current Task domain model state."""
        if not task.discord_thread_id:
            return False

        try:
            thread = None
            if hasattr(self.bot, "get_channel"):
                thread = self.bot.get_channel(task.discord_thread_id)
            if not thread and hasattr(self.bot, "fetch_channel"):
                thread = await _maybe_await(self.bot.fetch_channel(task.discord_thread_id))
        except Exception as e:
            logger.debug("Could not fetch thread %s for task %s: %s", task.discord_thread_id, task.short_id, e)
            return SyncWorkspaceResult(success=False)

        if not isinstance(thread, discord.Thread):
            return SyncWorkspaceResult(success=False)

        resolved_proj_name = project_name or (project.name if project else None)
        if not resolved_proj_name and task.project_id and self.project_service:
            try:
                p = await self.project_service.get_by_id(task.project_id)
                if p:
                    resolved_proj_name = p.name
            except Exception:
                pass

        # 1. Sync Starter Message Embed / Card
        if sync_starter_card and task.discord_message_id:
            try:
                root_msg = None
                if isinstance(thread.parent, discord.ForumChannel):
                    root_msg = thread.starter_message
                    if not root_msg and hasattr(thread, "fetch_message"):
                        root_msg = await _maybe_await(thread.fetch_message(task.discord_message_id))
                elif thread.parent and hasattr(thread.parent, "fetch_message"):
                    root_msg = await _maybe_await(thread.parent.fetch_message(task.discord_message_id))

                if root_msg and hasattr(root_msg, "edit"):
                    fresh_embed = build_task_embed(task, project_name=resolved_proj_name)
                    fresh_view = TaskActionView(
                        task_id=task.id,
                        current_status=task.status,
                        current_priority=task.priority,
                        task_service=self.task_service,
                        current_assignee_id=task.assignee_discord_id,
                        current_watchers=task.watchers,
                    )
                    keep_archived = task.status == TaskStatus.COMPLETED or task.is_archived
                    async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
                        if isinstance(thread.parent, discord.ForumChannel):
                            thread_content = build_thread_workspace_content(task)
                            await _maybe_await(
                                root_msg.edit(content=thread_content, embed=fresh_embed, view=fresh_view)
                            )
                        else:
                            await _maybe_await(root_msg.edit(embed=fresh_embed, view=fresh_view))
            except Exception as e:
                logger.debug("Failed to sync starter embed for task %s: %s", task.short_id, e)

        # 2. Sync Thread Attributes (tags, title, archive state)
        edit_kwargs: dict[str, object] = {}
        title_renamed = False
        title_deferred = False
        cooldown_remaining = 0.0

        if sync_tags and isinstance(thread.parent, discord.ForumChannel):
            tags_to_apply = resolve_forum_tags(
                thread.parent,
                task=task,
                project_name=resolved_proj_name,
                existing_tags=getattr(thread, "applied_tags", None),
            )
            edit_kwargs["applied_tags"] = tags_to_apply

        expected_name = None
        if sync_title:
            expected_name = f"[{task.short_id}] {task.title}"
            if len(expected_name) > 100:
                expected_name = expected_name[:97] + "..."
            if getattr(thread, "name", None) != expected_name:
                can_rename, _ = self.rename_limiter.can_rename(thread.id)
                if can_rename:
                    edit_kwargs["name"] = expected_name
                else:
                    rename_res = await self.rename_limiter.request_rename(thread, expected_name)
                    title_deferred = rename_res.deferred
                    cooldown_remaining = rename_res.cooldown_remaining_seconds

        if sync_archive:
            is_done = task.status == TaskStatus.COMPLETED or task.is_archived
            if is_done and not getattr(thread, "archived", False):
                edit_kwargs["archived"] = True
            elif not is_done and getattr(thread, "archived", False):
                edit_kwargs["archived"] = False

        if edit_kwargs and hasattr(thread, "edit"):
            try:
                await _maybe_await(thread.edit(**edit_kwargs))
                if "name" in edit_kwargs:
                    self.rename_limiter.record_rename(thread.id)
                    title_renamed = True
            except Exception as e:
                status = getattr(e, "status", None)
                retry_after = getattr(e, "retry_after", None)
                if "name" in edit_kwargs and (status == 429 or retry_after is not None):
                    backoff = float(retry_after) if retry_after else 600.0
                    self.rename_limiter.record_rate_limit(thread.id, backoff)
                    rename_res = await self.rename_limiter.request_rename(thread, edit_kwargs["name"])
                    title_deferred = rename_res.deferred
                    cooldown_remaining = rename_res.cooldown_remaining_seconds

                    remaining_kwargs = {k: v for k, v in edit_kwargs.items() if k != "name"}
                    if remaining_kwargs:
                        try:
                            await _maybe_await(thread.edit(**remaining_kwargs))
                        except Exception as rem_err:
                            logger.warning(
                                "Failed to edit thread attributes without name for task %s: %s",
                                task.short_id,
                                rem_err,
                            )
                else:
                    logger.warning(
                        "Failed to edit thread state for task %s (%s): %s",
                        task.short_id,
                        task.discord_thread_id,
                        e,
                    )

        return SyncWorkspaceResult(
            success=True,
            title_renamed=title_renamed,
            title_deferred=title_deferred,
            cooldown_remaining_seconds=cooldown_remaining,
        )

    async def refresh_action_card(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        project: Project | None = None,
        project_name: str | None = None,
    ) -> None:
        """Refreshes the interactive Task Action Card in response to a Discord component interaction."""
        resolved_proj_name = project_name or (project.name if project else None)
        if not resolved_proj_name and task.project_id and self.project_service:
            try:
                p = await self.project_service.get_by_id(task.project_id)
                if p:
                    resolved_proj_name = p.name
            except Exception:
                pass

        new_view = TaskActionView(
            task_id=task.id,
            current_status=task.status,
            current_priority=task.priority,
            task_service=self.task_service,
            current_assignee_id=task.assignee_discord_id,
            current_watchers=task.watchers,
        )

        thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
        keep_archived = task.status == TaskStatus.COMPLETED or task.is_archived

        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            if isinstance(interaction.channel, discord.Thread):
                content = build_thread_workspace_content(task)
                if isinstance(interaction.channel.parent, discord.ForumChannel):
                    new_embed = build_task_embed(task, project_name=resolved_proj_name)
                    if hasattr(interaction, "response") and not interaction.response.is_done():
                        await _maybe_await(
                            interaction.response.edit_message(content=content, embed=new_embed, view=new_view)
                        )
                else:
                    if hasattr(interaction, "response") and not interaction.response.is_done():
                        await _maybe_await(
                            interaction.response.edit_message(content=content, embed=None, view=new_view)
                        )
            else:
                new_embed = build_task_embed(task, project_name=resolved_proj_name)
                if hasattr(interaction, "response") and not interaction.response.is_done():
                    await _maybe_await(interaction.response.edit_message(embed=new_embed, view=new_view))

    async def post_activity(
        self,
        task: Task,
        content: str,
        *,
        embed: discord.Embed | None = None,
        rearchive_if_completed: bool = True,
    ) -> discord.Message | None:
        """Posts an activity update or note into the Task's Thread Workspace."""
        if not task.discord_thread_id:
            return None

        try:
            thread = None
            if hasattr(self.bot, "get_channel"):
                thread = self.bot.get_channel(task.discord_thread_id)
            if not thread and hasattr(self.bot, "fetch_channel"):
                thread = await _maybe_await(self.bot.fetch_channel(task.discord_thread_id))
        except Exception as e:
            logger.debug("Could not fetch thread %s for activity post: %s", task.discord_thread_id, e)
            return None

        if not isinstance(thread, discord.Thread):
            return None

        is_completed = task.status == TaskStatus.COMPLETED or task.is_archived
        was_archived = getattr(thread, "archived", False)

        async with unarchive_thread_if_needed(
            thread, keep_archived=(rearchive_if_completed and (is_completed or was_archived))
        ):
            msg = None
            if hasattr(thread, "send"):
                msg = await _maybe_await(thread.send(content=content, embed=embed))
            if (
                rearchive_if_completed
                and (is_completed or was_archived)
                and not getattr(thread, "archived", False)
                and hasattr(thread, "edit")
            ):
                try:
                    await _maybe_await(thread.edit(archived=True))
                except Exception:
                    pass
            return msg

    async def delete_workspace(
        self,
        task: Task,
        *,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Permanently deletes or archives the Discord thread workspace and logs audit notification."""
        thread = None
        if task.discord_thread_id:
            try:
                if hasattr(self.bot, "get_channel"):
                    thread = self.bot.get_channel(task.discord_thread_id)
                if not thread and hasattr(self.bot, "fetch_channel"):
                    thread = await _maybe_await(self.bot.fetch_channel(task.discord_thread_id))
            except Exception as e:
                logger.debug("Could not fetch thread %s for task deletion: %s", task.discord_thread_id, e)
                thread = None

        if thread and isinstance(thread, discord.Thread):
            try:
                if hasattr(thread, "delete"):
                    await _maybe_await(thread.delete())
            except Exception as e:
                logger.warning(
                    "Failed to delete thread %s for task %s, attempting archive fallback: %s",
                    task.discord_thread_id,
                    task.short_id,
                    e,
                )
                try:
                    actor_str = f" by <@{actor_discord_id}>" if actor_discord_id else ""
                    notice = f"⚠️ **This task was permanently deleted{actor_str}.** This thread is now closed."
                    if hasattr(thread, "send"):
                        await _maybe_await(thread.send(notice))
                    del_name = f"[DELETED] {task.short_id}"
                    if hasattr(thread, "edit"):
                        await _maybe_await(thread.edit(name=del_name, archived=True, locked=True))
                except Exception as fallback_err:
                    logger.error(
                        "Failed fallback archiving thread %s: %s",
                        task.discord_thread_id,
                        fallback_err,
                    )

        # Audit notification to project workspace activity log
        if task.project_id and self.project_service:
            try:
                project = await self.project_service.get_by_id(task.project_id)
                if project and project.discord_channel_id:
                    proj_chan = None
                    if hasattr(self.bot, "get_channel"):
                        proj_chan = self.bot.get_channel(project.discord_channel_id)
                    if not proj_chan and hasattr(self.bot, "fetch_channel"):
                        proj_chan = await _maybe_await(self.bot.fetch_channel(project.discord_channel_id))

                    if proj_chan:
                        from datetime import UTC, datetime

                        audit_embed = discord.Embed(
                            title="🗑️ Task Deleted",
                            description=f"Task **[{task.short_id}]** (`{task.title}`) was permanently deleted.",
                            color=discord.Color.red(),
                            timestamp=datetime.now(UTC),
                        )
                        if actor_discord_id:
                            audit_embed.add_field(name="Deleted By", value=f"<@{actor_discord_id}>", inline=True)
                        if task.status:
                            audit_embed.add_field(name="Status", value=str(task.status.value), inline=True)

                        if isinstance(proj_chan, discord.TextChannel):
                            await _maybe_await(proj_chan.send(embed=audit_embed))
                        elif isinstance(proj_chan, discord.ForumChannel):
                            hub_thread = None
                            threads = getattr(proj_chan, "threads", [])
                            for t in threads:
                                if "Control Hub" in t.name or "Management Hub" in t.name or "Hub" in t.name:
                                    hub_thread = t
                                    break
                            if hub_thread and hasattr(hub_thread, "send"):
                                await _maybe_await(hub_thread.send(embed=audit_embed))
            except Exception as e:
                logger.warning("Failed to dispatch delete audit notification to project channel: %s", e)

        return True

    async def render_task_controls(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        panel: TaskControlPanel = "quick_controls",
        prerequisites: list[Task] | None = None,
        dependents: list[Task] | None = None,
        history: list[TaskHistory] | None = None,
        sibling_tasks: list[Task] | None = None,
    ) -> None:
        """Renders interactive ephemeral control panels (Quick Controls, Dependencies, Audit Trail)."""
        if panel == "quick_controls":
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
            view = TaskQuickControlsView(
                task=task,
                task_service=self.task_service,
                auth_service=self.auth_service,
                bot=self.bot,
                workspace=self,
            )
            embed = build_task_controls_embed(task)
            await _maybe_await(interaction.response.send_message(embed=embed, view=view, ephemeral=True))
        elif panel == "dependencies":
            view = TaskDependencyView(
                task_service=self.task_service,
                task=task,
                sibling_tasks=sibling_tasks or [],
                prerequisites=prerequisites or [],
                dependents=dependents or [],
            )
            embed = build_dependency_embed(task, prerequisites or [], dependents or [])
            await _maybe_await(interaction.response.send_message(embed=embed, view=view, ephemeral=True))
        elif panel == "history":
            embed = build_task_history_embed(task, history or [])
            await _maybe_await(interaction.response.send_message(embed=embed, ephemeral=True))

    async def handle_action(
        self,
        interaction: discord.Interaction,
        action: str,
        task_id: UUID,
    ) -> None:
        """Handles dynamic component interactions triggered from the Task Action Card."""
        if hasattr(interaction, "response") and interaction.response.is_done():
            return

        task = await self.task_service.get_by_id(task_id)
        if not task:
            if hasattr(interaction, "response") and not interaction.response.is_done():
                await _maybe_await(interaction.response.send_message("❌ Task not found in database.", ephemeral=True))
            return

        # Prevent cross-guild access
        if interaction.guild_id is not None and task.guild_id != interaction.guild_id:
            if hasattr(interaction, "response") and not interaction.response.is_done():
                await _maybe_await(
                    interaction.response.send_message("❌ This task does not belong to this server.", ephemeral=True)
                )
            return

        # Check authorization
        if self.auth_service and not await self.auth_service.can_mutate_task(interaction.user, task):
            msg = (
                "❌ You do not have permission to modify this task. "
                "You must be the assignee, creator, a member of the project squad, or a server manager."
            )
            if hasattr(interaction, "response") and not interaction.response.is_done():
                await _maybe_await(interaction.response.send_message(msg, ephemeral=True))
            elif hasattr(interaction, "followup"):
                await _maybe_await(interaction.followup.send(msg, ephemeral=True))
            return

        if action == "note":
            from src.adapters.discord_bot.views.task_modals import TaskNoteModal

            modal = TaskNoteModal(
                task_id=task_id,
                short_id=task.short_id,
                task_service=self.task_service,
                auth_service=self.auth_service,
            )
            await _maybe_await(interaction.response.send_modal(modal))
            return

        if action == "edit":
            from src.adapters.discord_bot.views.task_modals import TaskEditModal

            modal = TaskEditModal(
                task=task,
                task_service=self.task_service,
                auth_service=self.auth_service,
            )
            await _maybe_await(interaction.response.send_modal(modal))
            return

        if action == "deps":
            try:
                sibling_tasks = []
                if task.project_id and interaction.guild:
                    sibling_tasks, _ = await self.task_service.list_tasks(
                        guild_id=interaction.guild.id,
                        project_id=task.project_id,
                        include_archived=False,
                        limit=50,
                    )
                prerequisites, dependents = await self.task_service.get_task_dependencies(task_id)
                await self.render_task_controls(
                    interaction=interaction,
                    task=task,
                    panel="dependencies",
                    prerequisites=prerequisites,
                    dependents=dependents,
                    sibling_tasks=sibling_tasks,
                )
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "opening task dependencies", logger, ephemeral=True)
                return

        if action == "controls":
            try:
                await self.render_task_controls(
                    interaction=interaction,
                    task=task,
                    panel="quick_controls",
                )
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "opening task controls", logger, ephemeral=True)
                return

        if action == "claim":
            try:
                if task.assignee_discord_id and task.assignee_discord_id != interaction.user.id:
                    if hasattr(interaction, "response") and not interaction.response.is_done():
                        await _maybe_await(
                            interaction.response.send_message(
                                f"❌ Task is already claimed by <@{task.assignee_discord_id}>.",
                                ephemeral=True,
                            )
                        )
                    return
                elif task.assignee_discord_id == interaction.user.id:
                    if hasattr(interaction, "response") and not interaction.response.is_done():
                        await _maybe_await(
                            interaction.response.send_message(
                                "ℹ️ You are already assigned to this task.",
                                ephemeral=True,
                            )
                        )
                    return

                if self.auth_service and interaction.guild:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, interaction.user.id, task.project_id
                    )

                updated_task = await self.task_service.update_assignee(
                    task_id=task_id,
                    new_assignee_id=interaction.user.id,
                    actor_discord_id=interaction.user.id,
                )
                await self.refresh_action_card(interaction, updated_task)
                await self.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "claiming task", logger, ephemeral=True)
                return

        if action == "unassign":
            try:
                updated_task = await self.task_service.update_assignee(
                    task_id=task_id,
                    new_assignee_id=None,
                    actor_discord_id=interaction.user.id,
                )
                await self.refresh_action_card(interaction, updated_task)
                await self.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "unassigning task", logger, ephemeral=True)
                return

        if action == "priority":
            values = (
                interaction.data.get("values", [])
                if hasattr(interaction, "data") and isinstance(interaction.data, dict)
                else []
            )
            if values:
                try:
                    new_priority = PriorityLevel(values[0])
                    updated_task = await self.task_service.update_priority(
                        task_id=task_id,
                        new_priority=new_priority,
                        actor_discord_id=interaction.user.id,
                    )
                    await self.refresh_action_card(interaction, updated_task)
                    await self.sync_workspace(updated_task)
                    return
                except Exception as e:
                    await send_interaction_error(interaction, e, "updating task priority", logger, ephemeral=True)
                    return

        if action == "assignee":
            values = (
                interaction.data.get("values", [])
                if hasattr(interaction, "data") and isinstance(interaction.data, dict)
                else []
            )
            try:
                new_assignee_id = int(values[0]) if values else None
                if new_assignee_id and self.auth_service and interaction.guild:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, new_assignee_id, task.project_id
                    )
                updated_task = await self.task_service.update_assignee(
                    task_id=task_id,
                    new_assignee_id=new_assignee_id,
                    actor_discord_id=interaction.user.id,
                )
                await self.refresh_action_card(interaction, updated_task)
                await self.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "updating task assignee", logger, ephemeral=True)
                return

        if action == "due":
            values = (
                interaction.data.get("values", [])
                if hasattr(interaction, "data") and isinstance(interaction.data, dict)
                else []
            )
            if values:
                try:
                    from src.utils.date_parser import get_due_date_from_preset

                    due_at, is_clear = get_due_date_from_preset(values[0])
                    updated_task = await self.task_service.update_details(
                        task_id=task_id,
                        actor_discord_id=interaction.user.id,
                        due_at=due_at,
                        clear_due_at=is_clear,
                    )
                    await self.refresh_action_card(interaction, updated_task)
                    await self.sync_workspace(updated_task)
                    return
                except Exception as e:
                    await send_interaction_error(interaction, e, "updating task due date", logger, ephemeral=True)
                    return

        if action == "watchers":
            values = (
                interaction.data.get("values", [])
                if hasattr(interaction, "data") and isinstance(interaction.data, dict)
                else []
            )
            try:
                watchers = [int(uid) for uid in values] if values else []
                updated_task = await self.task_service.update_details(
                    task_id=task_id,
                    actor_discord_id=interaction.user.id,
                    watchers=watchers,
                )
                await self.refresh_action_card(interaction, updated_task)
                await self.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "updating task watchers", logger, ephemeral=True)
                return

        target_status = TaskStatus.IN_PROGRESS if action in ("start", "reopen") else TaskStatus.COMPLETED
        if action == "notstarted":
            target_status = TaskStatus.NOT_STARTED
        try:
            note_action = "reopened" if action == "reopen" else f"updated to {target_status.value}"
            updated_task = await self.task_service.update_status(
                task_id=task_id,
                new_status=target_status,
                expected_version=task.version,
                actor_discord_id=interaction.user.id,
                notes=f"Status {note_action} via button",
            )
            await self.refresh_action_card(interaction, updated_task)
            await self.sync_workspace(updated_task)

        except StaleVersionError:
            latest_task = await self.task_service.get_by_id(task_id)
            if latest_task:
                await self.refresh_action_card(interaction, latest_task)
                await self.sync_workspace(latest_task)
                if hasattr(interaction, "followup"):
                    await _maybe_await(
                        interaction.followup.send(
                            "⚠️ This task was already modified by another squad member. The card has been refreshed.",
                            ephemeral=True,
                        )
                    )
            else:
                if hasattr(interaction, "response") and not interaction.response.is_done():
                    await _maybe_await(interaction.response.send_message("❌ Task no longer exists.", ephemeral=True))
                elif hasattr(interaction, "followup"):
                    await _maybe_await(interaction.followup.send("❌ Task no longer exists.", ephemeral=True))
        except Exception as e:
            await send_interaction_error(interaction, e, "processing task button action", logger, ephemeral=True)

    async def save_task_controls(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        priority: PriorityLevel | None = None,
        assignee_id: Any = _UNSET,
        due_at: Any = _UNSET,
        clear_due_at: bool = False,
        watchers: list[int] | None = None,
    ) -> Task | None:
        """Applies staged task control adjustments atomically, syncing thread tags and action card."""
        if self.auth_service:
            await self.auth_service.require_task_mutation(interaction.user, task)

        thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
        if not thread and task.discord_thread_id:
            try:
                chan = self.bot.get_channel(task.discord_thread_id) if hasattr(self.bot, "get_channel") else None
                if not chan and hasattr(self.bot, "fetch_channel"):
                    chan = await _maybe_await(self.bot.fetch_channel(task.discord_thread_id))
                if isinstance(chan, discord.Thread):
                    thread = chan
            except Exception:
                thread = None

        keep_archived = task.status == TaskStatus.COMPLETED or task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = task
            actor_id = getattr(interaction.user, "id", None)

            if priority is not None and priority != updated_task.priority:
                updated_task = await self.task_service.update_priority(
                    task_id=task.id,
                    new_priority=priority,
                    actor_discord_id=actor_id,
                )

            details_kwargs: dict[str, Any] = {}
            if due_at is not _UNSET and due_at != updated_task.due_at:
                details_kwargs["due_at"] = due_at
            if clear_due_at:
                details_kwargs["clear_due_at"] = True
            if watchers is not None and set(watchers) != set(updated_task.watchers):
                details_kwargs["watchers"] = watchers

            if details_kwargs:
                updated_task = await self.task_service.update_details(
                    task_id=task.id,
                    actor_discord_id=actor_id,
                    **details_kwargs,
                )

            if assignee_id is not _UNSET and assignee_id != updated_task.assignee_discord_id:
                if assignee_id is not None and self.auth_service and interaction.guild:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, assignee_id, task.project_id
                    )
                updated_task = await self.task_service.update_assignee(
                    task_id=task.id,
                    new_assignee_id=assignee_id,
                    actor_discord_id=actor_id,
                )

            await self.sync_workspace(
                updated_task,
                sync_title=False,
                sync_tags=True,
                sync_archive=False,
                sync_starter_card=True,
            )
            return updated_task
