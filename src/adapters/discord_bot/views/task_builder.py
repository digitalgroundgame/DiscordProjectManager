from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.forum_helpers import resolve_forum_tags
from src.adapters.discord_bot.views.task_buttons import TaskActionView
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_thread_workspace_content,
    get_task_jump_url,
)
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Project, Task
from src.utils.date_parser import get_due_date_from_preset, parse_natural_date

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_builder")


def build_task_draft_embed(
    title: str,
    description: str | None = None,
    project: Project | None = None,
    assignee_id: int | None = None,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    due_at: datetime | None = None,
    watchers: list[int] | None = None,
    target_channel: discord.abc.GuildChannel | discord.Thread | None = None,
    prerequisite_short_ids: list[str] | None = None,
) -> discord.Embed:
    """Builds a live preview embed of the task currently being configured in the builder."""
    proj_display = f"[{project.prefix}] {project.name}" if project else "Standalone Task (Ad-hoc)"
    assignee_display = f"<@{assignee_id}>" if assignee_id else "*Unassigned (Click member picker below)*"

    if priority == PriorityLevel.HIGH:
        priority_display = "High"
    elif priority == PriorityLevel.LOW:
        priority_display = "Low"
    else:
        priority_display = "Normal"

    if due_at:
        due_ts = int(due_at.astimezone(UTC).timestamp())
        due_display = f"<t:{due_ts}:f> (<t:{due_ts}:R>)"
    else:
        due_display = "*No due date (Click preset picker below)*"

    watchers_display = " ".join(f"<@{uid}>" for uid in watchers) if watchers else "*None*"
    prereqs_display = ", ".join(f"`[{p}]`" for p in prerequisite_short_ids) if prerequisite_short_ids else "*None*"

    chan_name = getattr(target_channel, "name", None)
    chan_display = f"#{chan_name}" if chan_name else "Current Channel"

    embed = discord.Embed(
        title=f"Task Draft: {title[:90]}",
        description=(
            "Configure optional assignee, deadline, and priority using the pickers below, "
            "then click **`Create Task`** to publish.\n\n"
            f"• **Project Scope**: {proj_display}\n"
            f"• **Assignee**: {assignee_display}\n"
            f"• **Priority**: {priority_display}\n"
            f"• **Due Date**: {due_display}\n"
            f"• **Prerequisites**: {prereqs_display}\n"
            f"• **Watchers (CC)**: {watchers_display}\n"
            f"• **Publish Target**: `{chan_display}`"
        ),
        color=discord.Color.blurple(),
    )

    if description:
        embed.add_field(
            name="Description / Details",
            value=description[:1000] + ("..." if len(description) > 1000 else ""),
            inline=False,
        )

    embed.set_footer(text="dgg-pm • Interactive Task Creation Builder")
    return embed


class TaskCustomDueModal(BaseModal):
    """Modal for entering a custom natural-language due date/time."""

    def __init__(self, target_view: Any):
        super().__init__(title="Set Custom Due Date")
        self.target_view = target_view
        self.draft_view = target_view

        default_val = ""
        current_due = None
        if hasattr(target_view, "staged_due_at"):
            is_cleared = getattr(target_view, "staged_clear_due", False)
            current_due = target_view.staged_due_at if not is_cleared else None
        elif hasattr(target_view, "due_at"):
            current_due = target_view.due_at

        if current_due:
            default_val = current_due.strftime("%Y-%m-%d %H:%M")

        self.due_input = discord.ui.TextInput(
            label="Due Date / Time Expression",
            placeholder="e.g. friday 5pm, tomorrow 9am, next monday, 2026-06-30",
            default=default_val,
            required=True,
            max_length=60,
        )
        self.add_item(self.due_input)

    def _render_parent_embed(self, interaction: discord.Interaction) -> discord.Embed:
        if hasattr(self.target_view, "_build_embed"):
            return self.target_view._build_embed()
        return build_task_draft_embed(
            title=self.target_view.title,
            description=self.target_view.description,
            project=self.target_view.project,
            assignee_id=self.target_view.assignee_id,
            priority=self.target_view.priority,
            due_at=self.target_view.due_at,
            watchers=self.target_view.watchers,
            target_channel=self.target_view.target_channel or interaction.channel,
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        val = self.due_input.value.strip()
        parsed = parse_natural_date(val)
        if not parsed:
            self.target_view._rebuild_items()
            embed = self._render_parent_embed(interaction)
            await interaction.response.edit_message(embed=embed, view=self.target_view)
            from src.adapters.discord_bot.menu_manager import format_toast_message, menu_manager

            msg = format_toast_message(
                f"❌ Could not parse date expression: `{val}`. Please try expressions like `friday 5pm` or `tomorrow`.",
                delay=10.0,
            )
            toast = await interaction.followup.send(
                msg,
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=10.0)
            return

        if hasattr(self.target_view, "staged_due_at"):
            self.target_view.staged_due_at = parsed
            self.target_view.staged_clear_due = False
            self.target_view.error_message = None
        else:
            self.target_view.due_at = parsed

        self.target_view._rebuild_items()
        embed = self._render_parent_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=self.target_view)


class TaskDetailsModal(BaseModal):
    """Modal to enter task title and description."""

    def __init__(
        self,
        task_service: TaskService,
        project: Project | None = None,
        target_channel: discord.ForumChannel | discord.TextChannel | discord.Thread | None = None,
        auth_service: AuthService | None = None,
        draft_view: TaskCreateDraftView | None = None,
        initial_title: str = "",
        initial_desc: str = "",
        parent_interaction: discord.Interaction | None = None,
    ):
        if project:
            modal_title = f"New Task: [{project.prefix}] {project.name[:22]}"
            title_placeholder = f"Task for {project.name} (e.g. Implement OAuth2 login)"
        else:
            modal_title = "New Standalone Task (Ad-hoc)"
            title_placeholder = "Ad-hoc chore (e.g. Renew SSL, Update server banner)"

        if draft_view:
            modal_title = "Edit Task Details"

        super().__init__(title=modal_title[:45])
        self.task_service = task_service
        self.project = project
        self.target_channel = target_channel
        self.auth_service = auth_service
        self.draft_view = draft_view
        self.parent_interaction = parent_interaction

        self.title_input = discord.ui.TextInput(
            label="Task Title",
            placeholder=title_placeholder,
            default=initial_title,
            required=True,
            max_length=100,
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label="Description / Body (Optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Detailed requirements, acceptance criteria, or execution notes...",
            default=initial_desc,
            required=False,
            max_length=1500,
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_input.value.strip()
        if not title:
            await interaction.response.send_message("❌ Task title cannot be empty.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=10.0)
            return

        desc = self.desc_input.value.strip() or None

        if self.parent_interaction:
            try:
                if hasattr(self.parent_interaction, "delete_original_response"):
                    await self.parent_interaction.delete_original_response()
            except Exception:
                pass

        if self.draft_view:
            # Updating existing draft
            self.draft_view.title = title
            self.draft_view.description = desc
            self.draft_view._rebuild_items()
            embed = build_task_draft_embed(
                title=self.draft_view.title,
                description=self.draft_view.description,
                project=self.draft_view.project,
                assignee_id=self.draft_view.assignee_id,
                priority=self.draft_view.priority,
                due_at=self.draft_view.due_at,
                watchers=self.draft_view.watchers,
                target_channel=self.draft_view.target_channel or interaction.channel,
            )
            await interaction.response.edit_message(embed=embed, view=self.draft_view)
        else:
            # Initial submission: launch interactive draft builder
            draft_view = TaskCreateDraftView(
                task_service=self.task_service,
                project=self.project,
                title=title,
                description=desc,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
            )
            embed = build_task_draft_embed(
                title=title,
                description=desc,
                project=self.project,
                assignee_id=None,
                priority=PriorityLevel.NORMAL,
                due_at=None,
                watchers=None,
                target_channel=self.target_channel or interaction.channel,
            )
            await interaction.response.send_message(embed=embed, view=draft_view, ephemeral=True)


class DraftPrerequisiteSelectView(BaseView):
    """Interactive in-place view to pick prerequisite tasks for a new task draft."""

    def __init__(self, draft_view: TaskCreateDraftView, sibling_tasks: list[Task]):
        super().__init__(timeout=180)
        self.draft_view = draft_view
        self.sibling_tasks = sibling_tasks

        options: list[discord.SelectOption] = []
        for t in sibling_tasks[:25]:
            is_selected = t.short_id in draft_view.prerequisite_short_ids
            emoji = "🟢" if t.is_completed else ("🟡" if t.status == TaskStatus.IN_PROGRESS else "⚪")
            options.append(
                discord.SelectOption(
                    label=f"[{t.short_id}]"[:100],
                    value=t.short_id,
                    description=(t.title[:85] + "...") if len(t.title) > 85 else t.title,
                    default=is_selected,
                    emoji=emoji,
                )
            )

        if options:
            self.select = discord.ui.Select(
                placeholder="Select prerequisite tasks (blocks this task)...",
                min_values=0,
                max_values=len(options),
                options=options,
                row=0,
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        done_btn = discord.ui.Button(
            label="Done & Return to Draft",
            style=discord.ButtonStyle.success,
            row=1,
        )
        done_btn.callback = self._on_done
        self.add_item(done_btn)

        cancel_btn = discord.ui.Button(
            label="Back to Draft",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        cancel_btn.callback = self._on_done
        self.add_item(cancel_btn)

    def _build_picker_embed(self) -> discord.Embed:
        sel = self.draft_view.prerequisite_short_ids
        sel_text = ", ".join(f"`[{sid}]`" for sid in sel) if sel else "*None (Task can be started immediately)*"
        proj_label = (
            f"[{self.draft_view.project.prefix}] {self.draft_view.project.name}"
            if self.draft_view.project
            else "Project"
        )
        task_label = self.draft_view.title or "New Task"
        embed = discord.Embed(
            title=f"Configure Prerequisites: {task_label}",
            description=(
                f"Select tasks in **{proj_label}** that must be completed before **{task_label}** can be started.\n\n"
                f"**Currently Selected ({len(sel)}):**\n{sel_text}\n\n"
                "*Use the multi-select dropdown below, then click **Done & Return to Draft**.*"
            ),
            color=discord.Color.from_rgb(16, 152, 247),
        )
        return embed

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = (
            getattr(self.select, "values", None) or (interaction.data.get("values") if interaction.data else None) or []
        )
        self.draft_view.prerequisite_short_ids = list(values)
        if hasattr(self, "select"):
            for opt in self.select.options:
                opt.default = opt.value in self.draft_view.prerequisite_short_ids
        embed = self._build_picker_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_done(self, interaction: discord.Interaction) -> None:
        self.stop()
        self.draft_view._rebuild_items()
        embed = build_task_draft_embed(
            title=self.draft_view.title,
            description=self.draft_view.description,
            project=self.draft_view.project,
            assignee_id=self.draft_view.assignee_id,
            priority=self.draft_view.priority,
            due_at=self.draft_view.due_at,
            watchers=self.draft_view.watchers,
            target_channel=self.draft_view.target_channel or interaction.channel,
            prerequisite_short_ids=self.draft_view.prerequisite_short_ids,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self.draft_view)


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
        self._initial_interaction: discord.Interaction | None = None

        self._rebuild_items()

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

        if self.project:
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
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prereqs_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not self.project:
            return

        tasks, _ = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=self.project.id,
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

            task = await self.task_service.create_task(
                guild_id=interaction.guild.id,
                title=self.title,
                creator_discord_id=interaction.user.id,
                project_id=self.project.id if self.project else None,
                assignee_discord_id=self.assignee_id,
                due_at=self.due_at,
                priority=self.priority,
                body=self.description,
                watchers=self.watchers,
                prerequisite_short_ids=self.prerequisite_short_ids,
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

            embed = build_task_embed(task, project_name=self.project.name if self.project else None)

            msg = None
            if isinstance(target_chan, discord.ForumChannel):
                post_name = f"[{task.short_id}] {task.title[:90]}"
                thread_view = TaskActionView(
                    task_id=task.id,
                    current_status=task.status,
                    current_priority=task.priority,
                    task_service=self.task_service,
                    current_assignee_id=task.assignee_discord_id,
                    current_watchers=task.watchers,
                )
                applied_tags = resolve_forum_tags(
                    target_chan, task, project_name=self.project.name if self.project else None
                )
                thread_content = build_thread_workspace_content(task)

                res = await target_chan.create_thread(
                    name=post_name,
                    content=thread_content,
                    embed=embed,
                    view=thread_view,
                    applied_tags=applied_tags,
                    auto_archive_duration=10080,
                )
                thread = getattr(res, "thread", res)
                msg = getattr(res, "message", None)
                thread_id = getattr(thread, "id", None)
                msg_id = getattr(msg, "id", 0) if msg else 0
                await self.task_service.update_discord_message_ids(task.id, msg_id, thread_id)
                # Re-fetch task to get updated message/thread IDs for jump URL
                task = await self.task_service.get_by_id(task.id) or task
            elif isinstance(target_chan, discord.TextChannel):
                msg = await target_chan.send(embed=embed)
                try:
                    thread = await msg.create_thread(
                        name=f"[{task.short_id}] {task.title[:90]}",
                        auto_archive_duration=10080,
                    )
                    thread_view = TaskActionView(
                        task_id=task.id,
                        current_status=task.status,
                        current_priority=task.priority,
                        task_service=self.task_service,
                        current_assignee_id=task.assignee_discord_id,
                        current_watchers=task.watchers,
                    )
                    thread_content = build_thread_workspace_content(task)
                    await thread.send(content=thread_content, view=thread_view)
                    await self.task_service.update_discord_message_ids(task.id, msg.id, thread.id)
                    task = await self.task_service.get_by_id(task.id) or task
                except Exception:
                    await self.task_service.update_discord_message_ids(task.id, msg.id, None)
                    task = await self.task_service.get_by_id(task.id) or task
            elif isinstance(target_chan, discord.Thread):
                view = TaskActionView(
                    task_id=task.id,
                    current_status=task.status,
                    current_priority=task.priority,
                    task_service=self.task_service,
                    current_assignee_id=task.assignee_discord_id,
                    current_watchers=task.watchers,
                )
                msg = await target_chan.send(embed=embed, view=view)
                await self.task_service.update_discord_message_ids(task.id, msg.id, target_chan.id)
                task = await self.task_service.get_by_id(task.id) or task
            elif target_chan:
                msg = await target_chan.send(embed=embed)
                await self.task_service.update_discord_message_ids(task.id, msg.id, None)
                task = await self.task_service.get_by_id(task.id) or task

            jump_url = get_task_jump_url(task)
            link_bullet = f"• [🔗 **Open Task Workspace**]({jump_url})\n" if jump_url else ""
            assignee_str = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "Unassigned"
            proj_name = self.project.name if self.project else "Standalone"

            success_embed = discord.Embed(
                title=f"✅ Task Created: [{task.short_id}] {task.title}",
                description=(
                    f"{link_bullet}"
                    f"• **Project Scope**: `{proj_name}`\n"
                    f"• **Assignee**: {assignee_str}\n"
                    f"• **Priority**: `{task.priority.value.upper()}`\n"
                    f"• **Status**: `{task.status.value}`"
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
