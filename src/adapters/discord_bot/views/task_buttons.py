from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.forum_helpers import unarchive_thread_if_needed
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.utils.date_parser import get_due_date_from_preset

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_buttons")


def build_task_controls_embed(task: Task) -> discord.Embed:
    """Builds a summary embed for the interactive ephemeral task controls."""
    prio_map = {
        PriorityLevel.HIGH: "High",
        PriorityLevel.NORMAL: "Normal",
        PriorityLevel.LOW: "Low",
    }
    prio_str = prio_map.get(task.priority, "Normal")
    assignee_str = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "*Unassigned*"
    due_str = (
        f"<t:{int(task.due_at.timestamp())}:f> (<t:{int(task.due_at.timestamp())}:R>)"
        if task.due_at
        else "*No due date*"
    )
    watchers_str = " ".join(f"<@{uid}>" for uid in task.watchers) if task.watchers else "*None*"

    embed = discord.Embed(
        title=f"Quick Controls: [{task.short_id}] {task.title[:70]}",
        description=(
            "Use the dropdowns below to quickly adjust priority, assignee, due date, or watchers.\n\n"
            f"• **Priority**: {prio_str}\n"
            f"• **Assignee**: {assignee_str}\n"
            f"• **Due Date**: {due_str}\n"
            f"• **Watchers**: {watchers_str}"
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Changes apply immediately to the task workspace.")
    return embed


class TaskQuickControlsView(BaseView):
    """Interactive ephemeral controls view for quick task adjustments on demand."""

    def __init__(
        self,
        task: Task,
        task_service: TaskService,
        auth_service: AuthService | None = None,
        bot: discord.Client | None = None,
    ):
        super().__init__(timeout=300)
        self.task = task
        self.task_service = task_service
        self.auth_service = auth_service
        self.bot = bot
        self._rebuild_items()

    def _get_thread(self, interaction: discord.Interaction) -> discord.Thread | None:
        if isinstance(interaction.channel, discord.Thread):
            return interaction.channel
        if self.bot and self.task.discord_thread_id:
            try:
                ch = self.bot.get_channel(self.task.discord_thread_id)
                if isinstance(ch, discord.Thread):
                    return ch
            except Exception:
                pass
        return None

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Action / Dismiss buttons
        done_btn = discord.ui.Button(
            label="Done",
            style=discord.ButtonStyle.success,
            row=0,
        )
        done_btn.callback = self._on_done_clicked
        self.add_item(done_btn)

        if self.task.assignee_discord_id:
            unassign_btn = discord.ui.Button(
                label="Unassign",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            unassign_btn.callback = self._on_unassign_clicked
            self.add_item(unassign_btn)

        # Row 1: Priority Dropdown
        priority_options = [
            discord.SelectOption(
                label="High Priority",
                value="high",
                default=(self.task.priority == PriorityLevel.HIGH),
            ),
            discord.SelectOption(
                label="Normal Priority",
                value="normal",
                default=(self.task.priority == PriorityLevel.NORMAL),
            ),
            discord.SelectOption(
                label="Low Priority",
                value="low",
                default=(self.task.priority == PriorityLevel.LOW),
            ),
        ]
        self.priority_select = discord.ui.Select(
            placeholder="Change priority...",
            options=priority_options,
            row=1,
        )
        self.priority_select.callback = self._on_priority_selected
        self.add_item(self.priority_select)

        # Row 2: Assignee User Select Picker
        assignee_defaults = [discord.Object(id=self.task.assignee_discord_id)] if self.task.assignee_discord_id else []
        self.assignee_select = discord.ui.UserSelect(
            placeholder="Reassign member (or clear)...",
            min_values=0,
            max_values=1,
            default_values=assignee_defaults,
            row=2,
        )
        self.assignee_select.callback = self._on_assignee_selected
        self.add_item(self.assignee_select)

        # Row 3: Due Date Quick Presets Dropdown
        due_options = [
            discord.SelectOption(label="Today (EOD 5:00 PM)", value="today"),
            discord.SelectOption(label="Tomorrow (EOD 5:00 PM)", value="tomorrow"),
            discord.SelectOption(label="In 2 Days", value="2days"),
            discord.SelectOption(label="In 3 Days", value="3days"),
            discord.SelectOption(label="In 1 Week", value="1week"),
            discord.SelectOption(label="In 2 Weeks", value="2weeks"),
            discord.SelectOption(label="In 1 Month", value="1month"),
            discord.SelectOption(label="Clear Due Date", value="clear"),
        ]
        self.due_select = discord.ui.Select(
            placeholder="Set due date...",
            options=due_options,
            row=3,
        )
        self.due_select.callback = self._on_due_selected
        self.add_item(self.due_select)

        # Row 4: Watchers User Select Picker (Multi-Select)
        watcher_defaults = [discord.Object(id=uid) for uid in self.task.watchers] if self.task.watchers else []
        self.watchers_select = discord.ui.UserSelect(
            placeholder="Manage watchers (pick up to 10 or clear)...",
            min_values=0,
            max_values=10,
            default_values=watcher_defaults,
            row=4,
        )
        self.watchers_select.callback = self._on_watchers_selected
        self.add_item(self.watchers_select)

    async def _on_priority_selected(self, interaction: discord.Interaction) -> None:
        val = self.priority_select.values[0]
        new_priority = PriorityLevel(val)
        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = await self.task_service.update_priority(
                task_id=self.task.id,
                new_priority=new_priority,
                actor_discord_id=interaction.user.id,
            )
            self.task = updated_task
            self._rebuild_items()
            embed = build_task_controls_embed(self.task)
            await interaction.response.edit_message(embed=embed, view=self)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_archive=False)

    async def _on_assignee_selected(self, interaction: discord.Interaction) -> None:
        if self.assignee_select.values:
            selected_user = self.assignee_select.values[0]
            if self.auth_service:
                await self.auth_service.require_task_assignee_eligibility(
                    interaction.guild, selected_user.id, self.task.project_id
                )
            new_assignee_id = selected_user.id
        else:
            new_assignee_id = None

        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = await self.task_service.update_assignee(
                task_id=self.task.id,
                new_assignee_id=new_assignee_id,
                actor_discord_id=interaction.user.id,
            )
            self.task = updated_task
            self._rebuild_items()
            embed = build_task_controls_embed(self.task)
            await interaction.response.edit_message(embed=embed, view=self)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_archive=False)

    async def _on_unassign_clicked(self, interaction: discord.Interaction) -> None:
        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = await self.task_service.update_assignee(
                task_id=self.task.id,
                new_assignee_id=None,
                actor_discord_id=interaction.user.id,
            )
            self.task = updated_task
            self._rebuild_items()
            embed = build_task_controls_embed(self.task)
            await interaction.response.edit_message(embed=embed, view=self)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_archive=False)

    async def _on_due_selected(self, interaction: discord.Interaction) -> None:
        val = self.due_select.values[0]
        due_at, is_clear = get_due_date_from_preset(val)
        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = await self.task_service.update_details(
                task_id=self.task.id,
                actor_discord_id=interaction.user.id,
                due_at=due_at,
                clear_due_at=is_clear,
            )
            self.task = updated_task
            self._rebuild_items()
            embed = build_task_controls_embed(self.task)
            await interaction.response.edit_message(embed=embed, view=self)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_archive=False)

    async def _on_watchers_selected(self, interaction: discord.Interaction) -> None:
        watchers = [u.id for u in self.watchers_select.values] if self.watchers_select.values else []
        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = await self.task_service.update_details(
                task_id=self.task.id,
                actor_discord_id=interaction.user.id,
                watchers=watchers,
            )
            self.task = updated_task
            self._rebuild_items()
            embed = build_task_controls_embed(self.task)
            await interaction.response.edit_message(embed=embed, view=self)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_archive=False)

    async def _on_done_clicked(self, interaction: discord.Interaction) -> None:
        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            self.stop()
            embed = discord.Embed(
                title=f"Updated [{self.task.short_id}]",
                description="Task changes have been saved to the workspace.",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)


class TaskActionView(BaseView):
    """Persistent interactive view for task embed action buttons.

    All interactions are centrally handled by DggPmBot._handle_dynamic_task_button
    via on_interaction to prevent double-acknowledgment race conditions and ensure
    persistent functionality across bot restarts.
    """

    def __init__(
        self,
        task_id: UUID,
        current_status: TaskStatus,
        task_service: TaskService | None = None,
        current_priority: PriorityLevel = PriorityLevel.NORMAL,
        current_assignee_id: int | None = None,
        current_watchers: list[int] | None = None,
    ):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.task_service = task_service
        self.current_status = current_status
        self.current_priority = current_priority
        self.current_assignee_id = current_assignee_id
        self.current_watchers = current_watchers or []

        # Row 0: Primary Action Buttons
        if current_status == TaskStatus.COMPLETED:
            self.reopen_btn = discord.ui.Button(
                label="Reopen / Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"task:reopen:{task_id}",
                row=0,
            )
            self.add_item(self.reopen_btn)
        elif current_status == TaskStatus.IN_PROGRESS:
            self.notstarted_btn = discord.ui.Button(
                label="Convert to Not Started",
                style=discord.ButtonStyle.danger,
                custom_id=f"task:notstarted:{task_id}",
                row=0,
            )
            self.add_item(self.notstarted_btn)
            self.complete_btn = discord.ui.Button(
                label="Complete",
                style=discord.ButtonStyle.success,
                custom_id=f"task:complete:{task_id}",
                row=0,
            )
            self.add_item(self.complete_btn)
        else:
            self.start_btn = discord.ui.Button(
                label="In Progress",
                style=discord.ButtonStyle.primary,
                custom_id=f"task:start:{task_id}",
                row=0,
            )
            self.add_item(self.start_btn)

            self.complete_btn = discord.ui.Button(
                label="Complete",
                style=discord.ButtonStyle.success,
                custom_id=f"task:complete:{task_id}",
                row=0,
            )
            self.add_item(self.complete_btn)

        self.note_btn = discord.ui.Button(
            label="Add Note",
            style=discord.ButtonStyle.primary,
            custom_id=f"task:note:{task_id}",
            row=0,
        )
        self.add_item(self.note_btn)

        # Row 1: Advanced Actions / Tools (Edit Details in first position)
        self.edit_btn = discord.ui.Button(
            label="Edit Details",
            style=discord.ButtonStyle.secondary,
            custom_id=f"task:edit:{task_id}",
            row=1,
        )
        self.add_item(self.edit_btn)

        self.deps_btn = discord.ui.Button(
            label="Dependencies",
            style=discord.ButtonStyle.secondary,
            custom_id=f"task:deps:{task_id}",
            row=1,
        )
        self.add_item(self.deps_btn)

        self.controls_btn = discord.ui.Button(
            label="Quick Controls",
            style=discord.ButtonStyle.secondary,
            custom_id=f"task:controls:{task_id}",
            row=1,
        )
        self.add_item(self.controls_btn)


class TaskLinkButtonView(BaseView):
    """View containing a 1-click link button to open the task in Discord."""

    def __init__(self, jump_url: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Open Task",
                style=discord.ButtonStyle.link,
                url=jump_url,
            )
        )


# Semantic alias to maintain backwards-compatibility with Issue #13 specification
TaskActionControlsView = TaskQuickControlsView
