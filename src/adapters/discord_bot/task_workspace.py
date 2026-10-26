"""Concrete Discord Task Workspace & Thread Lifecycle implementation adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.task_workspace_actions import (
    handle_task_action,
    render_task_controls_panel,
    save_task_controls_state,
)
from src.adapters.discord_bot.task_workspace_sync import sync_task_workspace_state
from src.adapters.discord_bot.thread_rate_limiter import ThreadRenameRateLimiter
from src.adapters.discord_bot.views.forum_helpers import (
    resolve_forum_tags,
    unarchive_thread_if_needed,
)
from src.adapters.discord_bot.views.task_buttons import (
    TaskActionView,
)
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_thread_workspace_content,
)
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Project, Task, TaskHistory
from src.ports.workspace import (
    _UNSET,
    SyncWorkspaceResult,
    TaskControlPanel,
    TaskWorkspacePort,
    TaskWorkspaceRef,
)
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


def _is_thread_archived_error(exc: Exception) -> bool:
    """Returns True if the exception is Discord HTTP 400 error code 50083 (Thread is archived)."""
    if getattr(exc, "code", None) == 50083:
        return True
    msg = str(exc)
    return "50083" in msg or "Thread is archived" in msg


class DiscordTaskWorkspaceAdapter(TaskWorkspacePort):
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

    async def _get_dependencies_safe(self, task_id: UUID) -> tuple[list[Task] | None, list[Task] | None]:
        """Safely retrieves prerequisites and dependents for a task if supported by task_service."""
        if not self.task_service or not hasattr(self.task_service, "get_task_dependencies"):
            return None, None
        try:
            res = await _maybe_await(self.task_service.get_task_dependencies(task_id))
            if isinstance(res, tuple) and len(res) == 2:
                prereqs, dependents = res
                return (
                    prereqs if isinstance(prereqs, list) and prereqs else None,
                    dependents if isinstance(dependents, list) and dependents else None,
                )
        except Exception:
            pass
        return None, None

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
        prereqs, dependents = await self._get_dependencies_safe(task.id)
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
            embed = build_task_embed(
                task,
                project_name=project_name,
                prerequisites=prereqs,
                dependents=dependents,
            )
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
            embed = build_task_embed(
                task,
                project_name=project_name,
                prerequisites=prereqs,
                dependents=dependents,
            )
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
    ) -> SyncWorkspaceResult:
        """Synchronizes an existing Thread Workspace with the current Task domain model state."""
        return await sync_task_workspace_state(
            self,
            task,
            project=project,
            project_name=project_name,
            sync_title=sync_title,
            sync_tags=sync_tags,
            sync_archive=sync_archive,
            sync_starter_card=sync_starter_card,
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

        prereqs, dependents = await self._get_dependencies_safe(task.id)
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
                    new_embed = build_task_embed(
                        task,
                        project_name=resolved_proj_name,
                        prerequisites=prereqs,
                        dependents=dependents,
                    )
                    if hasattr(interaction, "response") and interaction.response.is_done() is not True:
                        await _maybe_await(
                            interaction.response.edit_message(content=content, embed=new_embed, view=new_view)
                        )
                else:
                    if hasattr(interaction, "response") and interaction.response.is_done() is not True:
                        await _maybe_await(
                            interaction.response.edit_message(content=content, embed=None, view=new_view)
                        )
            else:
                new_embed = build_task_embed(
                    task,
                    project_name=resolved_proj_name,
                    prerequisites=prereqs,
                    dependents=dependents,
                )
                if hasattr(interaction, "response") and interaction.response.is_done() is not True:
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
                    thread.archived = True
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
        await render_task_controls_panel(
            self,
            interaction,
            task,
            panel=panel,
            prerequisites=prerequisites,
            dependents=dependents,
            history=history,
            sibling_tasks=sibling_tasks,
        )

    async def handle_action(
        self,
        interaction: discord.Interaction,
        action: str,
        task_id: UUID,
    ) -> None:
        """Handles dynamic component interactions triggered from the Task Action Card."""
        await handle_task_action(self, interaction, action, task_id)

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
        title: str | None = None,
        body: str | None = None,
        clear_body: bool = False,
    ) -> Task | None:
        """Applies staged task control adjustments atomically, syncing thread tags and action card."""
        return await save_task_controls_state(
            self,
            interaction,
            task,
            priority=priority,
            assignee_id=assignee_id,
            due_at=due_at,
            clear_due_at=clear_due_at,
            watchers=watchers,
            title=title,
            body=body,
            clear_body=clear_body,
        )
