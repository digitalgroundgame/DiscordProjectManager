"""Concrete Discord Task Workspace & Thread Lifecycle implementation adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

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
    ITaskDiscordWorkspace,
    TaskControlPanel,
    TaskWorkspaceRef,
)
from src.domain.enums import TaskStatus
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
    ):
        self.bot = bot
        self.task_service = task_service
        self.project_service = project_service
        self.auth_service = auth_service or (AuthService(project_service=project_service) if project_service else None)

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
            return False

        if not isinstance(thread, discord.Thread):
            return False

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
                    keep_archived = task.status == TaskStatus.COMPLETED or task.is_archived
                    async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
                        if isinstance(thread.parent, discord.ForumChannel):
                            thread_content = build_thread_workspace_content(task)
                            await _maybe_await(root_msg.edit(content=thread_content, embed=fresh_embed))
                        else:
                            await _maybe_await(root_msg.edit(embed=fresh_embed))
            except Exception as e:
                logger.debug("Failed to sync starter embed for task %s: %s", task.short_id, e)

        # 2. Sync Thread Attributes (tags, title, archive state)
        edit_kwargs: dict[str, object] = {}

        if sync_tags and isinstance(thread.parent, discord.ForumChannel):
            tags_to_apply = resolve_forum_tags(
                thread.parent,
                task=task,
                project_name=resolved_proj_name,
                existing_tags=getattr(thread, "applied_tags", None),
            )
            edit_kwargs["applied_tags"] = tags_to_apply

        if sync_title:
            expected_name = f"[{task.short_id}] {task.title}"
            if len(expected_name) > 100:
                expected_name = expected_name[:97] + "..."
            if thread.name != expected_name:
                edit_kwargs["name"] = expected_name

        if sync_archive:
            is_done = task.status == TaskStatus.COMPLETED or task.is_archived
            if is_done and not getattr(thread, "archived", False):
                edit_kwargs["archived"] = True
            elif not is_done and getattr(thread, "archived", False):
                edit_kwargs["archived"] = False

        if edit_kwargs and hasattr(thread, "edit"):
            try:
                await _maybe_await(thread.edit(**edit_kwargs))
            except Exception as e:
                logger.warning(
                    "Failed to edit thread state for task %s (%s): %s",
                    task.short_id,
                    task.discord_thread_id,
                    e,
                )

        return True

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
