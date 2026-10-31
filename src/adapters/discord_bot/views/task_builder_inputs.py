"""Task builder modals and prerequisite selection views."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.ui_constants import (
    EPHEMERAL_MENU_TIMEOUT_SECONDS,
    TOAST_DURATION_EXTENDED,
)
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.task_builder_embeds import build_task_draft_embed
from src.domain.enums import PriorityLevel, TaskStatus
from src.services.task_service import parse_inline_dependencies
from src.utils.date_parser import parse_natural_date

if TYPE_CHECKING:
    from src.adapters.discord_bot.views.task_builder import TaskCreateDraftView
    from src.domain.models import Project, Task
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_builder_inputs")


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
            prerequisite_short_ids=getattr(self.target_view, "prerequisite_short_ids", None),
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        val = self.due_input.value.strip()
        parsed = parse_natural_date(val)
        if not parsed:
            self.target_view._rebuild_items()
            embed = self._render_parent_embed(interaction)
            await interaction.response.edit_message(embed=embed, view=self.target_view)
            from src.adapters.discord_bot.menu_manager import build_toast_embed, menu_manager
            from src.adapters.discord_bot.ui_constants import ToastType

            embed_toast = build_toast_embed(
                f"Could not parse date expression: `{val}`. Please try expressions like `friday 5pm` or `tomorrow`.",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
            )
            toast = await interaction.followup.send(
                embed=embed_toast,
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_EXTENDED)
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
        parent_view: BaseView | None = None,
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
        self.parent_view = parent_view

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

    async def _send_modal_error(self, interaction: discord.Interaction, error_text: str) -> None:
        from src.adapters.discord_bot.menu_manager import build_toast_embed, menu_manager
        from src.adapters.discord_bot.ui_constants import ToastType

        toast_embed = build_toast_embed(error_text, toast_type=ToastType.ERROR, delay=TOAST_DURATION_EXTENDED)

        if self.draft_view:
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
            await interaction.response.edit_message(embed=embed, view=self.draft_view)
            toast = await interaction.followup.send(
                embed=toast_embed,
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_EXTENDED)
        elif self.parent_view:
            if hasattr(self.parent_view, "_rebuild_items"):
                self.parent_view._rebuild_items()
            embed = self.parent_view.build_embed() if hasattr(self.parent_view, "build_embed") else None
            if embed:
                await interaction.response.edit_message(embed=embed, view=self.parent_view)
            else:
                await interaction.response.edit_message(view=self.parent_view)
            toast = await interaction.followup.send(
                embed=toast_embed,
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_EXTENDED)
        else:
            await interaction.response.send_message(embed=toast_embed, ephemeral=True)
            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_EXTENDED)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_input.value.strip()
        if not title:
            await self._send_modal_error(interaction, "Task title cannot be empty.")
            return

        desc = self.desc_input.value.strip() or None
        inline_prereqs = parse_inline_dependencies(desc) if desc else []

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
            if inline_prereqs and not self.draft_view.prerequisite_short_ids:
                self.draft_view.prerequisite_short_ids = inline_prereqs
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
            await interaction.response.edit_message(embed=embed, view=self.draft_view)
        else:
            # Initial submission: launch interactive draft builder
            from src.adapters.discord_bot.views.task_builder import TaskCreateDraftView

            draft_view = TaskCreateDraftView(
                task_service=self.task_service,
                project=self.project,
                title=title,
                description=desc,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
                prerequisite_short_ids=inline_prereqs,
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
                prerequisite_short_ids=inline_prereqs,
            )
            await interaction.response.send_message(embed=embed, view=draft_view, ephemeral=True)


class DraftPrerequisiteSelectView(BaseView):
    """Interactive in-place view to pick prerequisite tasks for a new task draft."""

    def __init__(
        self,
        draft_view: TaskCreateDraftView,
        sibling_tasks: list[Task],
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
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


__all__ = [
    "DraftPrerequisiteSelectView",
    "TaskCustomDueModal",
    "TaskDetailsModal",
]
