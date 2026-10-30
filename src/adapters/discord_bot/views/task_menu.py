import logging
from typing import Any
from uuid import UUID

import discord

from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import (
    EPHEMERAL_MENU_TIMEOUT_SECONDS,
    TOAST_DURATION_STANDARD,
)
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.task_builder import TaskDetailsModal
from src.adapters.discord_bot.views.task_select_project import (
    TaskProjectSearchModal,
    TaskSelectProjectView,
)
from src.domain.enums import TaskStatus
from src.domain.models import Project, Task
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

TaskCreateModal = TaskDetailsModal

logger = logging.getLogger("dgg_pm.views.task_menu")


def _extract_user_ids(text: str | None) -> list[int]:
    import re

    if not text:
        return []
    ids = re.findall(r"\d{4,20}", text)
    return [int(uid) for uid in set(ids)]


def build_task_board_embed(
    tasks: list[Task],
    total_count: int,
    project_label: str,
    status_label: str,
    assignee_label: str,
) -> discord.Embed:
    """Build standardized Task Board embed with active filters and task summary cards."""
    embed = discord.Embed(
        title=f"Task Board • {total_count} Tasks Active",
        description=(
            "> **Active Board Filters**\n"
            f"> • **Project Scope**: `{project_label}`\n"
            f"> • **Status**: `{status_label}`\n"
            f"> • **Assignee**: {assignee_label}"
        ),
        color=discord.Color.brand_green() if total_count > 0 else discord.Color.dark_grey(),
    )

    if not tasks:
        embed.add_field(
            name="No Tasks Found",
            value="No tasks match the active filter combination.\nUse **`New Task`** to create one.",
            inline=False,
        )
    else:
        from src.adapters.discord_bot.views.task_embed import get_task_jump_url

        for t in tasks:
            icon = "🟢" if t.is_completed else ("🟡" if t.status == TaskStatus.IN_PROGRESS else "⚪")
            assignee_str = f"<@{t.assignee_discord_id}>" if t.assignee_discord_id else "*Unassigned*"
            due_str = f" • Due: {t.due_at.strftime('%b %d')}" if t.due_at else ""
            jump_url = get_task_jump_url(t)
            link_bullet = f"• [**Open Task Workspace**]({jump_url})\n" if jump_url else ""
            embed.add_field(
                name=f"{icon} [{t.short_id}] {t.title}",
                value=(
                    f"{link_bullet}• **Assignee**: {assignee_str}{due_str}\n"
                    f"• **Priority**: `{t.priority.value.upper()}`"
                ),
                inline=False,
            )

    embed.set_footer(text="dgg-pm • Interactive Task Board")
    return embed


_UNSET = object()


class TaskMenuView(BaseView):
    """Control Center View for Task Operations and Real-time Multi-dimensional Filtering."""

    def __init__(
        self,
        task_service: TaskService,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        projects: list[Project] | None = None,
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        initial_project_id: UUID | Any | None = _UNSET,
        initial_assignee_id: int | None = None,
        search_query: str = "",
        auth_service: AuthService | None = None,
        initial_interaction: discord.Interaction | None = None,
        can_create_standalone: bool | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.task_service = task_service
        self.project_service = project_service
        self.squad_service = squad_service
        self.projects = projects or []
        self.current_channel_id = current_channel_id
        self.parent_channel_id = parent_channel_id
        self.search_query = search_query
        self.selected_assignee_id: int | None = initial_assignee_id
        self.status_filter_value: str = "active"
        self.auth_service = auth_service or (AuthService(project_service, squad_service) if squad_service else None)

        if can_create_standalone is not None:
            self.can_create_standalone = can_create_standalone
        elif initial_interaction and initial_interaction.user:
            self.can_create_standalone = AuthService.is_server_manager(initial_interaction.user)
        else:
            self.can_create_standalone = True

        # Determine channel-bound projects
        channel_ids = {cid for cid in (self.current_channel_id, self.parent_channel_id) if cid}
        self.channel_projects = [
            p for p in self.projects if p.discord_channel_id and p.discord_channel_id in channel_ids
        ]

        if initial_project_id is not _UNSET:
            self.selected_project_id = initial_project_id
        elif len(self.channel_projects) == 1:
            self.selected_project_id = self.channel_projects[0].id
        else:
            self.selected_project_id = None

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Action Buttons
        new_task_label = "New Project Task"
        if self.selected_project_id:
            proj = next((p for p in self.projects if p.id == self.selected_project_id), None)
            if proj:
                new_task_label = f"New Task [{proj.prefix}]"[:30]

        self.new_task_btn = discord.ui.Button(
            label=new_task_label,
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.new_task_btn.callback = self._on_new_task_clicked
        self.add_item(self.new_task_btn)

        if self.can_create_standalone:
            self.standalone_btn = discord.ui.Button(
                label="Standalone Task",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.standalone_btn.callback = self._on_standalone_clicked
            self.add_item(self.standalone_btn)

        self.search_scope_btn = discord.ui.Button(
            label="Search Scope",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.search_scope_btn.callback = self._on_search_scope_clicked
        self.add_item(self.search_scope_btn)

        self.reset_btn = discord.ui.Button(
            label="Reset Filters",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.reset_btn.callback = self._on_reset_filters_clicked
        self.add_item(self.reset_btn)

        if self.squad_service:
            self.hub_btn = discord.ui.Button(
                label="PM Menu",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.hub_btn.callback = self._on_hub_clicked
            self.add_item(self.hub_btn)

        # Row 1: Project Scope Filter Dropdown
        project_options = [
            discord.SelectOption(
                label="All Projects (Global Scope)",
                value="all",
                default=(self.selected_project_id is None),
            )
        ]

        channel_ids = {cid for cid in (self.current_channel_id, self.parent_channel_id) if cid}

        if self.search_query:
            q = self.search_query.lower()
            filtered = [
                p
                for p in self.projects
                if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
            ]
        else:

            def sort_key(p: Project) -> tuple[int, str]:
                is_chan = 0 if (p.discord_channel_id and p.discord_channel_id in channel_ids) else 1
                return (is_chan, p.name.lower())

            filtered = sorted(self.projects, key=sort_key)

        for p in filtered[:24]:
            is_chan = bool(p.discord_channel_id and p.discord_channel_id in channel_ids)
            chan_tag = " (This Channel)" if is_chan else ""
            project_options.append(
                discord.SelectOption(
                    label=f"[{p.prefix}] {p.name}{chan_tag}"[:100],
                    value=str(p.id),
                    default=(self.selected_project_id == p.id),
                )
            )

        self.project_select = discord.ui.Select(
            placeholder="Filter by project scope...",
            options=project_options,
            row=1,
        )
        self.project_select.callback = self._on_project_filter_changed
        self.add_item(self.project_select)

        # Row 2: Status Filter Select
        status_options = [
            discord.SelectOption(
                label="Active Tasks (In Progress & Not Started)",
                value="active",
                default=(self.status_filter_value == "active"),
            ),
            discord.SelectOption(
                label="In Progress",
                value="inProgress",
                emoji="🟡",
                default=(self.status_filter_value == "inProgress"),
            ),
            discord.SelectOption(
                label="Not Started",
                value="notStarted",
                emoji="⚪",
                default=(self.status_filter_value == "notStarted"),
            ),
            discord.SelectOption(
                label="Completed",
                value="completed",
                emoji="🟢",
                default=(self.status_filter_value == "completed"),
            ),
            discord.SelectOption(
                label="All Statuses (including Completed)",
                value="all",
                default=(self.status_filter_value == "all"),
            ),
        ]
        self.status_select = discord.ui.Select(
            placeholder="Filter by status...",
            options=status_options,
            row=2,
        )
        self.status_select.callback = self._on_status_filter_changed
        self.add_item(self.status_select)

        # Row 3: Assignee User Select Picker
        member_defaults = [discord.Object(id=self.selected_assignee_id)] if self.selected_assignee_id else []
        self.assignee_select = discord.ui.UserSelect(
            placeholder="Filter by assignee / member (or clear)...",
            row=3,
            min_values=0,
            max_values=1,
            default_values=member_defaults,
        )
        self.assignee_select.callback = self._on_assignee_filter_changed
        self.add_item(self.assignee_select)

        # Row 4: Clear Member Filter Button
        self.clear_member_btn = discord.ui.Button(
            label="Clear Assignee Filter",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        self.clear_member_btn.callback = self._on_clear_member_clicked
        self.add_item(self.clear_member_btn)

    @property
    def selected_status(self) -> TaskStatus | None:
        if self.status_filter_value in ("inProgress", "notStarted", "completed"):
            return TaskStatus(self.status_filter_value)
        return None

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.squad_service:
            embed, view = DiscordViewRouter.build_hub_view(
                self.project_service,
                self.squad_service,
                self.task_service,
                getattr(self, "user_service", None),
                self.auth_service,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_new_task_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        # If a project is currently selected/scoped, directly open TaskCreateModal pre-bound to this project!
        if self.selected_project_id:
            project = next((p for p in self.projects if p.id == self.selected_project_id), None)
            if not project:
                project = await self.project_service.get_by_id(self.selected_project_id)

            if project:
                # Check permission
                if self.auth_service and not await self.auth_service.can_create_task_in_project(
                    interaction.user, project.id
                ):
                    await interaction.response.send_message(
                        f"❌ You do not have permission to create tasks in project **{project.name}**.",
                        ephemeral=True,
                    )
                    from src.adapters.discord_bot.menu_manager import menu_manager

                    menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
                    return

                target_channel = None
                if project.discord_channel_id and interaction.guild:
                    target_channel = interaction.guild.get_channel(project.discord_channel_id)
                elif interaction.channel:
                    target_channel = interaction.channel

                modal = TaskCreateModal(
                    self.task_service,
                    project=project,
                    target_channel=target_channel,
                    auth_service=self.auth_service,
                )
                await interaction.response.send_modal(modal)
                return

        # Otherwise (Global Scope), open the project selection view
        if not self.projects:
            self.projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)

        allowed_projects = []
        for p in self.projects:
            if not self.auth_service or await self.auth_service.can_create_task_in_project(interaction.user, p.id):
                allowed_projects.append(p)

        if not allowed_projects:
            if self.auth_service and await self.auth_service.can_create_task_in_project(interaction.user, None):
                modal = TaskCreateModal(
                    self.task_service,
                    project=None,
                    target_channel=interaction.channel,
                    auth_service=self.auth_service,
                )
                await interaction.response.send_modal(modal)
                return

            await interaction.response.send_message(
                "❌ You do not have permission to create tasks in any active projects. "
                "Contact a Project Lead or server manager to be added to a project squad.",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
            return

        has_channel_projects = bool(self.channel_projects)
        view = TaskSelectProjectView(
            allowed_projects,
            self.task_service,
            self.project_service,
            self.squad_service,
            current_channel_id=self.current_channel_id,
            parent_channel_id=self.parent_channel_id,
            auth_service=self.auth_service,
            initial_interaction=interaction,
            filter_to_channel=has_channel_projects,
        )
        desc = (
            "Choose which active project in this channel to create the task inside:\n"
            "• Use **`Show All Projects`** or **`Search Projects`** to pick from any project."
            if has_channel_projects
            else (
                "Choose which active project to create the task inside:\n"
                "• Projects bound to this channel are listed at the top (`📍`).\n"
                "• Use **`🔍 Search Projects`** to quickly filter across all projects."
            )
        )
        embed = discord.Embed(
            title="📁 Select Project Container",
            description=desc,
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_standalone_clicked(self, interaction: discord.Interaction) -> None:
        if self.auth_service and not await self.auth_service.can_create_task_in_project(interaction.user, None):
            await interaction.response.send_message(
                "❌ Only Server Managers and Squad Leads can create standalone tasks. "
                "Please create a task inside an active project container.",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
            return

        modal = TaskCreateModal(
            self.task_service,
            project=None,
            target_channel=interaction.channel,
            auth_service=self.auth_service,
        )
        await interaction.response.send_modal(modal)

    async def _on_search_scope_clicked(self, interaction: discord.Interaction) -> None:
        modal = TaskProjectSearchModal(self._apply_scope_search, current_query=self.search_query)
        await interaction.response.send_modal(modal)

    async def _apply_scope_search(self, interaction: discord.Interaction, query: str) -> None:
        self.search_query = query.strip()
        if self.search_query:
            q = self.search_query.lower()
            matches = [
                p
                for p in self.projects
                if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
            ]
            if matches:
                self.selected_project_id = matches[0].id
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _on_project_filter_changed(self, interaction: discord.Interaction) -> None:
        val = self.project_select.values[0]
        self.selected_project_id = None if val == "all" else UUID(val)
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _on_status_filter_changed(self, interaction: discord.Interaction) -> None:
        self.status_filter_value = self.status_select.values[0]
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _on_assignee_filter_changed(self, interaction: discord.Interaction) -> None:
        self.selected_assignee_id = self.assignee_select.values[0].id if self.assignee_select.values else None
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _on_clear_member_clicked(self, interaction: discord.Interaction) -> None:
        self.selected_assignee_id = None
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _on_reset_filters_clicked(self, interaction: discord.Interaction) -> None:
        self.search_query = ""
        if self.channel_projects:
            self.selected_project_id = self.channel_projects[0].id
        else:
            self.selected_project_id = None
        self.status_filter_value = "active"
        self.selected_assignee_id = None
        self._rebuild_items()
        await self._render_filtered_board(interaction)

    async def _render_filtered_board(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        filter_status = None
        exclude_completed = False
        if self.status_filter_value == "active":
            exclude_completed = True
            status_label = "Active Tasks (In Progress & Not Started)"
        elif self.status_filter_value == "all":
            status_label = "All Statuses (including Completed)"
        else:
            filter_status = TaskStatus(self.status_filter_value)
            status_label = filter_status.value

        tasks, total = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=self.selected_project_id,
            assignee_discord_id=self.selected_assignee_id,
            status=filter_status,
            include_archived=False,
            exclude_completed=exclude_completed,
            limit=15,
        )

        project_label = "All Projects (Global Scope)"
        channel_ids = {cid for cid in (self.current_channel_id, self.parent_channel_id) if cid}

        if self.selected_project_id:
            match = next((p for p in self.projects if p.id == self.selected_project_id), None)
            if match:
                is_chan = bool(match.discord_channel_id and match.discord_channel_id in channel_ids)
                tag = " (This Channel)" if is_chan else ""
                project_label = f"[{match.prefix}] {match.name}{tag}"
            else:
                p = await self.project_service.get_by_id(self.selected_project_id)
                if p:
                    project_label = f"[{p.prefix}] {p.name}"

        assignee_label = f"<@{self.selected_assignee_id}>" if self.selected_assignee_id else "All Members"

        embed = build_task_board_embed(
            tasks=tasks,
            total_count=total,
            project_label=project_label,
            status_label=status_label,
            assignee_label=assignee_label,
        )
        await interaction.response.edit_message(embed=embed, view=self)


def build_task_menu_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Task Operations Control Center",
        description=(
            "Create, filter, and manage tasks with zero typing.\n\n"
            "**Task Types**:\n"
            "• **`New Project Task`**: Tied to a project container with automatic channel/thread routing\n"
            "• **`Standalone Task`**: Ad-hoc, one-off task in this channel (default `TASK-#` prefix)\n\n"
            "**Live Board Controls**:\n"
            "• **`Project Scope`**: Focus board on a specific project or global server scope\n"
            "• **`Search Scope`**: Search across all server projects to instantly switch scope\n"
            "• **`Status Filter`**: Filter by progress status (In Progress, Not Started, Completed)\n"
            "• **`Assignee Filter`**: Filter to tasks assigned to a specific squad member\n"
            "• **`Reset Filters`**: Reset to channel default or global board view"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="dgg-pm • Zero-typing task operations")
    return embed


__all__ = [
    "TaskCreateModal",
    "TaskMenuView",
    "TaskProjectSearchModal",
    "TaskSelectProjectView",
    "build_task_board_embed",
    "build_task_menu_embed",
]
