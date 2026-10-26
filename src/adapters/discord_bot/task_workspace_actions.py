"""Task Workspace action handling and interactive controls helper."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.forum_helpers import unarchive_thread_if_needed
from src.adapters.discord_bot.views.task_buttons import (
    TaskQuickControlsView,
    build_task_controls_embed,
)
from src.adapters.discord_bot.views.task_dependency_view import (
    TaskDependencyView,
    build_dependency_embed,
)
from src.adapters.discord_bot.views.task_embed import (
    build_task_history_embed,
)
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.exceptions import StaleVersionError
from src.domain.models import Task, TaskHistory
from src.ports.workspace import _UNSET, TaskControlPanel

if TYPE_CHECKING:
    from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

logger = logging.getLogger("dgg_pm.task_workspace_actions")


async def _maybe_await(res: Any) -> Any:
    """Awaits an object if it is an awaitable or coroutine."""
    if hasattr(res, "__await__"):
        return await res
    return res


async def render_task_controls_panel(
    adapter: DiscordTaskWorkspaceAdapter,
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
            task_service=adapter.task_service,
            auth_service=adapter.auth_service,
            bot=adapter.bot,
            workspace=adapter,
        )
        embed = build_task_controls_embed(task)
        await _maybe_await(interaction.response.send_message(embed=embed, view=view, ephemeral=True))
    elif panel == "dependencies":
        view = TaskDependencyView(
            task_service=adapter.task_service,
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


async def handle_task_action(
    adapter: DiscordTaskWorkspaceAdapter,
    interaction: discord.Interaction,
    action: str,
    task_id: UUID,
) -> None:
    """Handles dynamic component interactions triggered from the Task Action Card."""
    if hasattr(interaction, "response") and interaction.response.is_done():
        return

    task = await adapter.task_service.get_by_id(task_id)
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
    if adapter.auth_service and not await adapter.auth_service.can_mutate_task(interaction.user, task):
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
        modal = DiscordViewRouter.build_task_note_modal(
            task_id=task_id,
            short_id=task.short_id,
            task_service=adapter.task_service,
            auth_service=adapter.auth_service,
        )
        await _maybe_await(interaction.response.send_modal(modal))
        return

    if action in ("edit", "controls"):
        try:
            await adapter.render_task_controls(
                interaction=interaction,
                task=task,
                panel="quick_controls",
            )
            return
        except Exception as e:
            await send_interaction_error(interaction, e, "opening task controls", logger, ephemeral=True)
            return

    if action == "deps":
        try:
            sibling_tasks = []
            if task.project_id and interaction.guild:
                sibling_tasks, _ = await adapter.task_service.list_tasks(
                    guild_id=interaction.guild.id,
                    project_id=task.project_id,
                    include_archived=False,
                    limit=50,
                )
            prerequisites, dependents = await adapter.task_service.get_task_dependencies(task_id)
            await adapter.render_task_controls(
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

            if adapter.auth_service and interaction.guild:
                await adapter.auth_service.require_task_assignee_eligibility(
                    interaction.guild, interaction.user.id, task.project_id
                )

            updated_task = await adapter.task_service.update_assignee(
                task_id=task_id,
                new_assignee_id=interaction.user.id,
                actor_discord_id=interaction.user.id,
            )
            await adapter.refresh_action_card(interaction, updated_task)
            await adapter.sync_workspace(updated_task)
            return
        except Exception as e:
            await send_interaction_error(interaction, e, "claiming task", logger, ephemeral=True)
            return

    if action == "unassign":
        try:
            updated_task = await adapter.task_service.update_assignee(
                task_id=task_id,
                new_assignee_id=None,
                actor_discord_id=interaction.user.id,
            )
            await adapter.refresh_action_card(interaction, updated_task)
            await adapter.sync_workspace(updated_task)
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
                updated_task = await adapter.task_service.update_priority(
                    task_id=task_id,
                    new_priority=new_priority,
                    actor_discord_id=interaction.user.id,
                )
                await adapter.refresh_action_card(interaction, updated_task)
                await adapter.sync_workspace(updated_task)
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
            if new_assignee_id and adapter.auth_service and interaction.guild:
                await adapter.auth_service.require_task_assignee_eligibility(
                    interaction.guild, new_assignee_id, task.project_id
                )
            updated_task = await adapter.task_service.update_assignee(
                task_id=task_id,
                new_assignee_id=new_assignee_id,
                actor_discord_id=interaction.user.id,
            )
            await adapter.refresh_action_card(interaction, updated_task)
            await adapter.sync_workspace(updated_task)
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
                updated_task = await adapter.task_service.update_details(
                    task_id=task_id,
                    actor_discord_id=interaction.user.id,
                    due_at=due_at,
                    clear_due_at=is_clear,
                )
                await adapter.refresh_action_card(interaction, updated_task)
                await adapter.sync_workspace(updated_task)
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
            updated_task = await adapter.task_service.update_details(
                task_id=task_id,
                actor_discord_id=interaction.user.id,
                watchers=watchers,
            )
            await adapter.refresh_action_card(interaction, updated_task)
            await adapter.sync_workspace(updated_task)
            return
        except Exception as e:
            await send_interaction_error(interaction, e, "updating task watchers", logger, ephemeral=True)
            return

    target_status = TaskStatus.IN_PROGRESS if action in ("start", "reopen") else TaskStatus.COMPLETED
    if action == "notstarted":
        target_status = TaskStatus.NOT_STARTED

    if action in ("start", "complete"):
        incomplete = await adapter.task_service.get_unresolved_prerequisites(task.id)
        if incomplete:
            embed, view = DiscordViewRouter.build_task_blocked_confirm_view(
                task=task,
                target_status=target_status,
                incomplete_prereqs=incomplete,
                author_id=interaction.user.id,
                task_service=adapter.task_service,
                auth_service=adapter.auth_service,
                workspace=adapter,
                bot=adapter.bot,
            )
            if hasattr(interaction, "response") and not interaction.response.is_done():
                await _maybe_await(interaction.response.send_message(embed=embed, view=view, ephemeral=True))
            elif hasattr(interaction, "followup"):
                await _maybe_await(interaction.followup.send(embed=embed, view=view, ephemeral=True))
            return

    try:
        note_action = "reopened" if action == "reopen" else f"updated to {target_status.value}"
        updated_task = await adapter.task_service.update_status(
            task_id=task_id,
            new_status=target_status,
            expected_version=task.version,
            actor_discord_id=interaction.user.id,
            notes=f"Status {note_action} via button",
        )
        await adapter.refresh_action_card(interaction, updated_task)
        await adapter.sync_workspace(updated_task)

    except StaleVersionError:
        latest_task = await adapter.task_service.get_by_id(task_id)
        if latest_task:
            await adapter.refresh_action_card(interaction, latest_task)
            await adapter.sync_workspace(latest_task)
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


async def save_task_controls_state(
    adapter: DiscordTaskWorkspaceAdapter,
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
    if adapter.auth_service:
        await adapter.auth_service.require_task_mutation(interaction.user, task)

    thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
    if not thread and task.discord_thread_id:
        try:
            chan = adapter.bot.get_channel(task.discord_thread_id) if hasattr(adapter.bot, "get_channel") else None
            if not chan and hasattr(adapter.bot, "fetch_channel"):
                chan = await _maybe_await(adapter.bot.fetch_channel(task.discord_thread_id))
            if isinstance(chan, discord.Thread):
                thread = chan
        except Exception:
            thread = None

    keep_archived = task.status == TaskStatus.COMPLETED or task.is_archived
    async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
        updated_task = task
        actor_id = getattr(interaction.user, "id", None)
        title_changed = False

        if priority is not None and priority != updated_task.priority:
            updated_task = await adapter.task_service.update_priority(
                task_id=task.id,
                new_priority=priority,
                actor_discord_id=actor_id,
            )

        details_kwargs: dict[str, Any] = {}
        if title is not None and title != updated_task.title:
            details_kwargs["title"] = title
            title_changed = True
        if body is not None and body != updated_task.body:
            details_kwargs["body"] = body
        if clear_body:
            details_kwargs["clear_body"] = True
        if due_at is not _UNSET and due_at != updated_task.due_at:
            details_kwargs["due_at"] = due_at
        if clear_due_at:
            details_kwargs["clear_due_at"] = True
        if watchers is not None and set(watchers) != set(updated_task.watchers):
            details_kwargs["watchers"] = watchers

        if details_kwargs:
            updated_task = await adapter.task_service.update_details(
                task_id=task.id,
                actor_discord_id=actor_id,
                **details_kwargs,
            )

        if assignee_id is not _UNSET and assignee_id != updated_task.assignee_discord_id:
            if assignee_id is not None and adapter.auth_service and interaction.guild:
                await adapter.auth_service.require_task_assignee_eligibility(
                    interaction.guild, assignee_id, task.project_id
                )
            updated_task = await adapter.task_service.update_assignee(
                task_id=task.id,
                new_assignee_id=assignee_id,
                actor_discord_id=actor_id,
            )

        await adapter.sync_workspace(
            updated_task,
            sync_title=title_changed,
            sync_tags=True,
            sync_archive=False,
            sync_starter_card=True,
        )
        return updated_task
