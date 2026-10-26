from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.task_builder_embeds import build_task_draft_embed
from src.adapters.discord_bot.views.task_builder_inputs import (
    DraftPrerequisiteSelectView,
    TaskCustomDueModal,
    TaskDetailsModal,
)
from src.adapters.discord_bot.views.task_embed import (
    get_task_jump_url,
)
from src.domain.enums import PriorityLevel
from src.domain.models import Project, Task
from src.ports.workspace import TaskWorkspacePort
from src.utils.date_parser import get_due_date_from_preset

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_builder")


class TaskCreateDraftView(BaseView):
    """Interactive multi-step Task Creation Builder (Non-modal interactive configuration).

    Allows users to pick assignees with native Discord member autocomplete (UserSelect),
    set due dates with 1-click quick presets, adjust priority, add watchers, configure
    prerequisites, and confirm task creation without typing commands or IDs.
    """

    def __init__(
        self,
        task_service: TaskService,
        project: Project | None = None,
        title: str = "",
        description: str | None = None,
        target_channel: discord.ForumChannel | discord.TextChannel | discord.Thread | None = None,
        assignee_id: int | None = None,
        priority: PriorityLevel = PriorityLevel.NORMAL,
        due_at: datetime | None = None,
        watchers: list[int] | None = None,
        auth_service: AuthService | None = None,
        prerequisite_short_ids: list[str] | None = None,
        workspace: Any = None,
    ):
        super().__init__(timeout=300)
        self.task_service = task_service
        self.project = project
        self.title = title
        self.description = description
        self.target_channel = target_channel
        self.assignee_id = assignee_id
        self.priority = priority
        self.due_at = due_at
        self.watchers = watchers or []
        self.auth_service = auth_service
        self.prerequisite_short_ids = prerequisite_short_ids or []
        self.workspace = workspace
        self._initial_interaction: discord.Interaction | None = None

        self._rebuild_items()

    def _resolve_effective_workspace(self, interaction: discord.Interaction) -> Any:
        if self.workspace is not None:
            return self.workspace
        if self.task_service and getattr(self.task_service, "workspace", None):
            return self.task_service.workspace
        client = getattr(interaction, "client", None)
        client_ws = getattr(client, "workspace", None) if client else None
        if isinstance(client_ws, TaskWorkspacePort):
            return client_ws
        from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

        return DiscordTaskWorkspaceAdapter(
            bot=client,
            task_service=self.task_service,
            project_service=getattr(self.task_service, "project_service", None),
            auth_service=self.auth_service,
        )

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Action Buttons
        self.create_btn = discord.ui.Button(
            label="Create Task",
            style=discord.ButtonStyle.success,
            row=0,
        )
        self.create_btn.callback = self._on_confirm_clicked
        self.add_item(self.create_btn)

        self.edit_btn = discord.ui.Button(
            label="Edit Title / Body",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.edit_btn.callback = self._on_edit_details_clicked
        self.add_item(self.edit_btn)

        if self.project or self.prerequisite_short_ids:
            prereq_label = f"Prereqs ({len(self.prerequisite_short_ids)})"
            self.prereqs_btn = discord.ui.Button(
                label=prereq_label,
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.prereqs_btn.callback = self._on_prereqs_clicked
            self.add_item(self.prereqs_btn)

        if self.assignee_id:
            self.unassign_btn = discord.ui.Button(
                label="Unassign",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.unassign_btn.callback = self._on_unassign_clicked
            self.add_item(self.unassign_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            row=0,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

        # Row 1: Assignee Native Member Select Picker
        assignee_defaults = [discord.Object(id=self.assignee_id)] if self.assignee_id else []
        self.assignee_select = discord.ui.UserSelect(
            placeholder="Select assignee member (or clear)...",
            min_values=0,
            max_values=1,
            row=1,
            default_values=assignee_defaults,
        )
        self.assignee_select.callback = self._on_assignee_selected
        self.add_item(self.assignee_select)

        # Row 2: Due Date Quick Presets Select Dropdown
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
            placeholder="Pick due date preset...",
            options=due_options,
            row=2,
        )
        self.due_select.callback = self._on_due_selected
        self.add_item(self.due_select)

        # Row 3: Priority Selector Dropdown
        priority_options = [
            discord.SelectOption(
                label="High Priority",
                value="high",
                default=(self.priority == PriorityLevel.HIGH),
            ),
            discord.SelectOption(
                label="Normal Priority",
                value="normal",
                default=(self.priority == PriorityLevel.NORMAL),
            ),
            discord.SelectOption(
                label="Low Priority",
                value="low",
                default=(self.priority == PriorityLevel.LOW),
            ),
        ]
        self.priority_select = discord.ui.Select(
            placeholder="Select priority level...",
            options=priority_options,
            row=3,
        )
        self.priority_select.callback = self._on_priority_selected
        self.add_item(self.priority_select)

        # Row 4: Watchers Native Member Multi-Select Picker
        watcher_defaults = [discord.Object(id=uid) for uid in self.watchers] if self.watchers else []
        self.watchers_select = discord.ui.UserSelect(
            placeholder="Pick watchers / CC (optional, up to 10 or clear)...",
            min_values=0,
            max_values=10,
            row=4,
            default_values=watcher_defaults,
        )
        self.watchers_select.callback = self._on_watchers_selected
        self.add_item(self.watchers_select)

    async def _on_assignee_selected(self, interaction: discord.Interaction) -> None:
        self.assignee_id = self.assignee_select.values[0].id if self.assignee_select.values else None
        self._rebuild_items()
        embed = build_task_draft_embed(
            title=self.title,
            description=self.description,
            project=self.project,
            assignee_id=self.assignee_id,
            priority=self.priority,
            due_at=self.due_at,
            watchers=self.watchers,
            target_channel=self.target_channel or interaction.channel,
            prerequisite_short_ids=self.prerequisite_short_ids,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_unassign_clicked(self, interaction: discord.Interaction) -> None:
        self.assignee_id = None
        self._rebuild_items()
        embed = build_task_draft_embed(
            title=self.title,
            description=self.description,
            project=self.project,
            assignee_id=self.assignee_id,
            priority=self.priority,
            due_at=self.due_at,
            watchers=self.watchers,
            target_channel=self.target_channel or interaction.channel,
            prerequisite_short_ids=self.prerequisite_short_ids,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_due_selected(self, interaction: discord.Interaction) -> None:
        val = self.due_select.values[0]
        if val == "custom":
            modal = TaskCustomDueModal(self)
            await interaction.response.send_modal(modal)
            return

        parsed, is_clear = get_due_date_from_preset(val)
        self.due_at = None if is_clear else parsed
        self._rebuild_items()
        embed = build_task_draft_embed(
            title=self.title,
            description=self.description,
            project=self.project,
            assignee_id=self.assignee_id,
            priority=self.priority,
            due_at=self.due_at,
            watchers=self.watchers,
            target_channel=self.target_channel or interaction.channel,
            prerequisite_short_ids=self.prerequisite_short_ids,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_priority_selected(self, interaction: discord.Interaction) -> None:
        val = self.priority_select.values[0]
        self.priority = PriorityLevel(val)
        self._rebuild_items()
        embed = build_task_draft_embed(
            title=self.title,
            description=self.description,
            project=self.project,
            assignee_id=self.assignee_id,
            priority=self.priority,
            due_at=self.due_at,
            watchers=self.watchers,
            target_channel=self.target_channel or interaction.channel,
            prerequisite_short_ids=self.prerequisite_short_ids,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_watchers_selected(self, interaction: discord.Interaction) -> None:
        self.watchers = [u.id for u in self.watchers_select.values] if self.watchers_select.values else []
        self._rebuild_items()
        embed = build_task_draft_embed(
            title=self.title,
            description=self.description,
            project=self.project,
            assignee_id=self.assignee_id,
            priority=self.priority,
            due_at=self.due_at,
            watchers=self.watchers,
            target_channel=self.target_channel or interaction.channel,
            prerequisite_short_ids=self.prerequisite_short_ids,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prereqs_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        tasks, _ = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=self.project.id if self.project else None,
            include_archived=False,
            limit=50,
        )
        if not tasks:
            await interaction.response.send_message(
                "ℹ️ There are no existing tasks in this project yet to link as prerequisites.",
                ephemeral=True,
            )
            return

        view = DraftPrerequisiteSelectView(self, tasks)
        embed = view._build_picker_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_edit_details_clicked(self, interaction: discord.Interaction) -> None:
        modal = TaskDetailsModal(
            task_service=self.task_service,
            project=self.project,
            target_channel=self.target_channel,
            auth_service=self.auth_service,
            draft_view=self,
            initial_title=self.title,
            initial_desc=self.description or "",
        )
        await interaction.response.send_modal(modal)

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🚫 Task Creation Cancelled",
            description="The task draft was discarded.",
            color=discord.Color.dark_grey(),
        )
        from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

        attach_dismissal_footer(embed, delay=3.0)
        await interaction.response.edit_message(embed=embed, view=None)
        menu_manager.schedule_toast_dismissal(interaction, delay=3.0)

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        try:
            if self.auth_service:
                await self.auth_service.require_task_creation(
                    interaction.user, self.project.id if self.project else None
                )
                if self.assignee_id:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, self.assignee_id, self.project.id if self.project else None
                    )

            target_chan = self.target_channel or interaction.channel

            # If target_chan is a Thread (e.g. clicked inside Pinned Control Hub), route to parent ForumChannel
            if isinstance(target_chan, discord.Thread):
                parent = getattr(target_chan, "parent", None)
                if not parent and getattr(target_chan, "parent_id", None) and hasattr(interaction.guild, "get_channel"):
                    parent = interaction.guild.get_channel(target_chan.parent_id)
                if isinstance(parent, discord.ForumChannel):
                    target_chan = parent

            # If not already a ForumChannel, check if project is bound to a Forum/Text channel
            if not isinstance(target_chan, discord.ForumChannel) and self.project and self.project.discord_channel_id:
                if hasattr(interaction.guild, "get_channel"):
                    proj_chan = interaction.guild.get_channel(self.project.discord_channel_id)
                    if isinstance(proj_chan, (discord.ForumChannel, discord.TextChannel)):
                        target_chan = proj_chan

            ws = self._resolve_effective_workspace(interaction)
            task, ref = await self.task_service.create_task_with_workspace(
                guild_id=interaction.guild.id,
                title=self.title,
                creator_discord_id=interaction.user.id,
                project_id=self.project.id if self.project else None,
                project_name=self.project.name if self.project else None,
                assignee_discord_id=self.assignee_id,
                due_at=self.due_at,
                priority=self.priority,
                body=self.description,
                watchers=self.watchers,
                prerequisite_short_ids=self.prerequisite_short_ids,
                target_container=target_chan,
                workspace=ws,
            )

            prereqs: list[Task] = []
            if hasattr(self.task_service, "get_task_dependencies"):
                try:
                    deps_res = await self.task_service.get_task_dependencies(task.id)
                    if isinstance(deps_res, tuple) and len(deps_res) == 2:
                        prereqs = list(deps_res[0] or [])
                except Exception:
                    prereqs = []

            jump_url = ref.jump_url if ref else get_task_jump_url(task)
            link_bullet = f"• [🔗 **Open Task Workspace**]({jump_url})\n" if jump_url else ""
            assignee_str = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "Unassigned"
            proj_name = self.project.name if self.project else "Standalone"
            prereq_ids_list = [p.short_id for p in prereqs] or list(self.prerequisite_short_ids)
            prereq_line = (
                f"\n• **Prerequisites**: {', '.join(f'`[{sid}]`' for sid in prereq_ids_list)}"
                if prereq_ids_list
                else ""
            )

            success_embed = discord.Embed(
                title=f"✅ Task Created: [{task.short_id}] {task.title}",
                description=(
                    f"{link_bullet}"
                    f"• **Project Scope**: `{proj_name}`\n"
                    f"• **Assignee**: {assignee_str}\n"
                    f"• **Priority**: `{task.priority.value.upper()}`\n"
                    f"• **Status**: `{task.status.value}`"
                    f"{prereq_line}"
                ),
                color=discord.Color.green(),
            )
            success_embed.set_footer(text=f"Task UUID: {task.id}")
            from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

            attach_dismissal_footer(success_embed, delay=8.0)
            await interaction.response.edit_message(embed=success_embed, view=None)

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating task '{self.title}'", logger, ephemeral=True)


__all__ = [
    "DraftPrerequisiteSelectView",
    "TaskCreateDraftView",
    "TaskCustomDueModal",
    "TaskDetailsModal",
    "build_task_draft_embed",
]
