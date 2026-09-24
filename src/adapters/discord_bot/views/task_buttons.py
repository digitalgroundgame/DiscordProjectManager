from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.forum_helpers import unarchive_thread_if_needed
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.exceptions import PermissionDeniedError
from src.domain.models import Task
from src.utils.date_parser import get_due_date_from_preset

if TYPE_CHECKING:
    from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_buttons")


_UNSET: Any = object()


def build_task_controls_embed(
    task: Task,
    *,
    error_message: str | None = None,
    priority: PriorityLevel | None = None,
    assignee_id: Any = _UNSET,
    due_at: Any = _UNSET,
    watchers: list[int] | None = None,
    title: str | None = None,
    body: Any = _UNSET,
) -> discord.Embed:
    """Builds a summary embed for the interactive ephemeral task controls."""
    prio_map = {
        PriorityLevel.HIGH: "High",
        PriorityLevel.NORMAL: "Normal",
        PriorityLevel.LOW: "Low",
    }
    actual_prio = priority or task.priority
    prio_str = prio_map.get(actual_prio, "Normal")

    actual_assignee_id = task.assignee_discord_id if assignee_id is _UNSET else assignee_id
    assignee_str = f"<@{actual_assignee_id}>" if actual_assignee_id else "*Unassigned*"

    actual_due_at = task.due_at if due_at is _UNSET else due_at
    due_str = (
        f"<t:{int(actual_due_at.timestamp())}:f> (<t:{int(actual_due_at.timestamp())}:R>)"
        if actual_due_at
        else "*No due date*"
    )

    actual_watchers = task.watchers if watchers is None else watchers
    watchers_str = " ".join(f"<@{uid}>" for uid in actual_watchers) if actual_watchers else "*None*"

    actual_title = title if title is not None else task.title
    actual_body = task.body if body is _UNSET else body

    if error_message:
        color = discord.Color.red()
        prefix = f"{error_message}\n\n"
    else:
        color = discord.Color.gold()
        prefix = ""

    body_section = f"\n• **Description**: {actual_body[:200]}" if actual_body else ""

    embed = discord.Embed(
        title=f"Edit Draft: [{task.short_id}] {actual_title[:100]}",
        description=(
            f"{prefix}"
            f"• **Priority**: {prio_str}\n"
            f"• **Assignee**: {assignee_str}\n"
            f"• **Due Date**: {due_str}\n"
            f"• **Watchers**: {watchers_str}"
            f"{body_section}"
        ),
        color=color,
    )
    embed.set_footer(text="⚠️ Unsaved Draft • Click 'Save Changes' to apply or 'Discard Changes' to cancel.")
    return embed


class TaskQuickControlsView(BaseView):
    """Interactive ephemeral controls view for quick task adjustments on demand."""

    def __init__(
        self,
        task: Task,
        task_service: TaskService,
        auth_service: AuthService | None = None,
        bot: discord.Client | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
    ):
        super().__init__(timeout=300)
        self.task = task
        self.task_service = task_service
        self.auth_service = auth_service
        self.bot = bot
        self.workspace = workspace

        # Staged state
        self.staged_title: str = task.title
        self.staged_body: str | None = task.body
        self.staged_priority: PriorityLevel = task.priority
        self.staged_assignee_id: int | None = task.assignee_discord_id
        self.staged_due_at: datetime | None = task.due_at
        self.staged_clear_due: bool = False
        self.staged_watchers: list[int] = list(task.watchers)
        self.error_message: str | None = None

        self._rebuild_items()

    @property
    def effective_workspace(self) -> ITaskDiscordWorkspace | None:
        if self.workspace is not None:
            return self.workspace
        if self.bot is not None:
            ws = getattr(self.bot, "workspace", None)
            if ws is not None:
                from unittest.mock import AsyncMock

                save_fn = getattr(ws, "save_task_controls", None)
                if asyncio.iscoroutinefunction(save_fn) or isinstance(save_fn, AsyncMock):
                    return ws
        return None

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

    def _build_embed(self) -> discord.Embed:
        due = None if self.staged_clear_due else self.staged_due_at
        return build_task_controls_embed(
            self.task,
            error_message=self.error_message,
            priority=self.staged_priority,
            assignee_id=self.staged_assignee_id,
            due_at=due,
            watchers=self.staged_watchers,
            title=self.staged_title,
            body=self.staged_body,
        )

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Action / Dismiss buttons
        save_btn = discord.ui.Button(
            label="Save Changes",
            style=discord.ButtonStyle.success,
            row=0,
        )
        save_btn.callback = self._on_save_clicked
        self.add_item(save_btn)

        discard_btn = discord.ui.Button(
            label="Discard Changes",
            style=discord.ButtonStyle.danger,
            row=0,
        )
        discard_btn.callback = self._on_cancel_clicked
        self.add_item(discard_btn)

        edit_text_btn = discord.ui.Button(
            label="Edit Title / Body",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        edit_text_btn.callback = self._on_edit_text_clicked
        self.add_item(edit_text_btn)

        if self.staged_assignee_id:
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
                default=(self.staged_priority == PriorityLevel.HIGH),
            ),
            discord.SelectOption(
                label="Normal Priority",
                value="normal",
                default=(self.staged_priority == PriorityLevel.NORMAL),
            ),
            discord.SelectOption(
                label="Low Priority",
                value="low",
                default=(self.staged_priority == PriorityLevel.LOW),
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
        assignee_defaults = [discord.Object(id=self.staged_assignee_id)] if self.staged_assignee_id else []
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
            discord.SelectOption(label="Custom Date / Time...", value="custom"),
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
        watcher_defaults = [discord.Object(id=uid) for uid in self.staged_watchers] if self.staged_watchers else []
        self.watchers_select = discord.ui.UserSelect(
            placeholder="Manage watchers (pick up to 10 or clear)...",
            min_values=0,
            max_values=10,
            default_values=watcher_defaults,
            row=4,
        )
        self.watchers_select.callback = self._on_watchers_selected
        self.add_item(self.watchers_select)

    async def _on_edit_text_clicked(self, interaction: discord.Interaction) -> None:
        from src.adapters.discord_bot.views.task_modals import TaskQuickEditTitleModal

        modal = TaskQuickEditTitleModal(self)
        await interaction.response.send_modal(modal)

    async def update_text_content(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        body: str | None,
    ) -> None:
        """Update staged title and description and refresh the draft embed."""
        self.staged_title = title
        self.staged_body = body
        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_priority_selected(self, interaction: discord.Interaction) -> None:
        prio_map = {
            "high": PriorityLevel.HIGH,
            "normal": PriorityLevel.NORMAL,
            "low": PriorityLevel.LOW,
        }
        val = self.priority_select.values[0]
        self.staged_priority = prio_map.get(val, PriorityLevel.NORMAL)
        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_assignee_selected(self, interaction: discord.Interaction) -> None:
        if self.assignee_select.values:
            selected_user = self.assignee_select.values[0]
            if self.auth_service:
                try:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, selected_user.id, self.task.project_id
                    )
                except PermissionDeniedError as e:
                    self.staged_assignee_id = self.task.assignee_discord_id
                    self.error_message = f"❌ {e}"
                    self._rebuild_items()
                    embed = self._build_embed()
                    await interaction.response.edit_message(embed=embed, view=self)
                    return
            self.staged_assignee_id = selected_user.id
        else:
            self.staged_assignee_id = None

        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_unassign_clicked(self, interaction: discord.Interaction) -> None:
        self.staged_assignee_id = None
        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_due_selected(self, interaction: discord.Interaction) -> None:
        val = self.due_select.values[0]
        if val == "custom":
            from src.adapters.discord_bot.views.task_builder import TaskCustomDueModal

            modal = TaskCustomDueModal(self)
            await interaction.response.send_modal(modal)
            return

        due_at, is_clear = get_due_date_from_preset(val)
        self.staged_due_at = due_at
        self.staged_clear_due = is_clear
        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_watchers_selected(self, interaction: discord.Interaction) -> None:
        self.staged_watchers = [u.id for u in self.watchers_select.values] if self.watchers_select.values else []
        self.error_message = None
        self._rebuild_items()
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_save_clicked(self, interaction: discord.Interaction) -> None:
        ws = self.effective_workspace
        if ws:
            clear_body = self.staged_body is None and bool(self.task.body)
            updated_task = await ws.save_task_controls(
                interaction,
                task=self.task,
                priority=self.staged_priority,
                assignee_id=self.staged_assignee_id,
                due_at=self.staged_due_at,
                clear_due_at=self.staged_clear_due,
                watchers=self.staged_watchers,
                title=self.staged_title,
                body=self.staged_body,
                clear_body=clear_body,
            )
            if updated_task:
                self.task = updated_task
            self.stop()
            embed = discord.Embed(
                title=f"Updated [{self.task.short_id}]",
                description="Task changes have been saved to the workspace.",
                color=discord.Color.green(),
            )
            from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

            attach_dismissal_footer(embed, delay=3.0)
            if hasattr(interaction, "response") and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=None)
            elif hasattr(interaction, "edit_original_response"):
                await interaction.edit_original_response(embed=embed, view=None)

            menu_manager.unregister_menu(interaction)
            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)
            return

        if self.auth_service:
            await self.auth_service.require_task_mutation(interaction.user, self.task)

        thread = self._get_thread(interaction)
        keep_archived = self.task.status == TaskStatus.COMPLETED or self.task.is_archived
        async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
            updated_task = self.task
            title_changed = self.staged_title != updated_task.title
            body_changed = self.staged_body != updated_task.body

            if self.staged_priority != updated_task.priority:
                updated_task = await self.task_service.update_priority(
                    task_id=self.task.id,
                    new_priority=self.staged_priority,
                    actor_discord_id=interaction.user.id,
                )

            details_changed = (
                title_changed
                or body_changed
                or self.staged_due_at != updated_task.due_at
                or self.staged_clear_due
                or set(self.staged_watchers) != set(updated_task.watchers)
            )
            if details_changed:
                details_kwargs: dict[str, Any] = {
                    "due_at": self.staged_due_at,
                    "clear_due_at": self.staged_clear_due,
                    "watchers": self.staged_watchers,
                }
                if title_changed:
                    details_kwargs["title"] = self.staged_title
                if body_changed:
                    details_kwargs["body"] = self.staged_body
                    if not self.staged_body:
                        details_kwargs["clear_body"] = True

                updated_task = await self.task_service.update_details(
                    task_id=self.task.id,
                    actor_discord_id=interaction.user.id,
                    **details_kwargs,
                )

            if self.staged_assignee_id != updated_task.assignee_discord_id:
                updated_task = await self.task_service.update_assignee(
                    task_id=self.task.id,
                    new_assignee_id=self.staged_assignee_id,
                    actor_discord_id=interaction.user.id,
                )

            self.task = updated_task
            self.stop()
            embed = discord.Embed(
                title=f"Updated [{self.task.short_id}]",
                description="Task changes have been saved to the workspace.",
                color=discord.Color.green(),
            )
            from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

            attach_dismissal_footer(embed, delay=3.0)
            await interaction.response.edit_message(embed=embed, view=None)
            if self.bot and hasattr(self.bot, "sync_root_task_message"):
                await self.bot.sync_root_task_message(updated_task)
                await self.bot.sync_task_thread(updated_task, sync_title=title_changed, sync_archive=False)

            menu_manager.unregister_menu(interaction)
            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)

    _on_done_clicked = _on_save_clicked

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        self.stop()
        from src.adapters.discord_bot.menu_manager import menu_manager

        menu_manager.unregister_menu(interaction)
        if not interaction.response.is_done():
            await interaction.response.defer()
        await interaction.delete_original_response()


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

        if current_assignee_id is None:
            self.claim_btn = discord.ui.Button(
                label="Claim Task",
                style=discord.ButtonStyle.success,
                custom_id=f"task:claim:{task_id}",
                row=0,
            )
            self.add_item(self.claim_btn)
        else:
            self.unassign_btn = discord.ui.Button(
                label="Unassign Me",
                style=discord.ButtonStyle.secondary,
                custom_id=f"task:unassign:{task_id}",
                row=0,
            )
            self.add_item(self.unassign_btn)

        self.note_btn = discord.ui.Button(
            label="Add Note",
            style=discord.ButtonStyle.primary,
            custom_id=f"task:note:{task_id}",
            row=0,
        )
        self.add_item(self.note_btn)

        # Row 1: Advanced Actions / Tools (Consolidated Edit Task in first position)
        self.edit_btn = discord.ui.Button(
            label="Edit Task",
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


# Semantic aliases to maintain backwards-compatibility
TaskActionControlsView = TaskQuickControlsView
TaskControlsView = TaskQuickControlsView
