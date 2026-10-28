"""Task project selection view and search modal."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.task_builder import TaskDetailsModal
from src.domain.models import Project

TaskCreateModal = TaskDetailsModal

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_select_project")


class TaskProjectSearchModal(BaseModal):
    """Modal to enter a search query for filtering or setting project scope."""

    def __init__(self, on_search_callback: Callable[[discord.Interaction, str], Any], current_query: str = ""):
        super().__init__(title="Search Projects")
        self.on_search_callback = on_search_callback

        self.query_input = discord.ui.TextInput(
            label="Project Name, Prefix, or Category",
            placeholder="e.g. IOS, Cloud, Security, Backend...",
            default=current_query,
            required=False,
            max_length=100,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.query_input.value.strip()
        await self.on_search_callback(interaction, query)


class TaskSelectProjectView(BaseView):
    """Paginated and searchable view to select which project container to create a task in."""

    PAGE_SIZE = 25

    def __init__(
        self,
        projects: list[Project],
        task_service: TaskService,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        auth_service: AuthService | None = None,
        current_page: int = 0,
        query: str = "",
        initial_interaction: discord.Interaction | None = None,
        filter_to_channel: bool = False,
    ):
        super().__init__(timeout=180)
        self.all_projects = projects
        self.task_service = task_service
        self.project_service = project_service
        self.squad_service = squad_service
        self.current_channel_id = current_channel_id
        self.parent_channel_id = parent_channel_id
        self.auth_service = auth_service
        self.current_page = current_page
        self.query = query
        self.filter_to_channel = filter_to_channel
        self._initial_interaction = initial_interaction
        self._filtered_projects = self._filter_and_sort_projects()
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

    def _filter_and_sort_projects(self) -> list[Project]:
        channel_ids = {cid for cid in (self.current_channel_id, self.parent_channel_id) if cid}

        projects = self.all_projects
        if self.filter_to_channel and channel_ids:
            projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        def sort_key(p: Project) -> tuple[int, str]:
            is_chan = 0 if (p.discord_channel_id and p.discord_channel_id in channel_ids) else 1
            return (is_chan, p.name.lower())

        sorted_proj = sorted(projects, key=sort_key)
        if not self.query:
            return sorted_proj
        q = self.query.lower()
        return [
            p
            for p in sorted_proj
            if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
        ]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_count = len(self._filtered_projects)
        total_pages = max(1, math.ceil(total_count / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.PAGE_SIZE
        page_projects = self._filtered_projects[start_idx : start_idx + self.PAGE_SIZE]

        channel_ids = {cid for cid in (self.current_channel_id, self.parent_channel_id) if cid}

        # Row 0: Select dropdown (up to 25 items)
        if page_projects:
            options = []
            for p in page_projects:
                is_this_chan = bool(p.discord_channel_id and p.discord_channel_id in channel_ids)
                chan_tag = " (This Channel)" if is_this_chan else ""
                options.append(
                    discord.SelectOption(
                        label=f"{p.name} ({p.prefix}){chan_tag}"[:100],
                        value=str(p.id),
                        description=(
                            f"Channel: #{p.discord_channel_id}"
                            if p.discord_channel_id
                            else (p.description[:90] if p.description else "Project Container")
                        ),
                    )
                )
            self.select = discord.ui.Select(
                placeholder=f"Select Project (Page {self.current_page + 1}/{total_pages})...",
                options=options,
                row=0,
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        # Row 1: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search Projects",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query or self.filter_to_channel:
            clear_btn = discord.ui.Button(
                label="Clear Filter" if self.query else "Show All Projects",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Task Menu",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 2: Pagination buttons
        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=2,
            )
            prev_btn.callback = self._on_prev_clicked
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=2,
            )
            next_btn.callback = self._on_next_clicked
            self.add_item(next_btn)

    async def _on_search_clicked(self, interaction: discord.Interaction) -> None:
        modal = TaskProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_and_sort_projects()
        self._rebuild_items()
        filter_note = f" (Filter: `{self.query}`)" if self.query else ""
        embed = discord.Embed(
            title=f"Select Project Container{filter_note}",
            description="Choose which active project to create the task inside:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.filter_to_channel = False
        self.current_page = 0
        self._filtered_projects = self._filter_and_sort_projects()
        self._rebuild_items()
        embed = discord.Embed(
            title="📁 Select Project Container",
            description="Choose which active project to create the task inside:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            filter_note = f" (Filter: `{self.query}`)" if self.query else ""
            embed = discord.Embed(
                title=f"📁 Select Project Container{filter_note}",
                description="Choose which active project to create the task inside:",
                color=discord.Color.blurple(),
            )
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            filter_note = f" (Filter: `{self.query}`)" if self.query else ""
            embed = discord.Embed(
                title=f"📁 Select Project Container{filter_note}",
                description="Choose which active project to create the task inside:",
                color=discord.Color.blurple(),
            )
            await interaction.response.edit_message(embed=embed, view=self)

    def build_embed(self) -> discord.Embed:
        filter_note = f" (Filter: `{self.query}`)" if self.query else ""
        return discord.Embed(
            title=f"📁 Select Project Container{filter_note}",
            description="Choose which active project to create the task inside:",
            color=discord.Color.blurple(),
        )

    async def _on_select(self, interaction: discord.Interaction) -> None:
        project_id_str = self.select.values[0]
        project = next((p for p in self.all_projects if str(p.id) == project_id_str), None)
        if not project and interaction.guild:
            project = await self.project_service.get_by_id(UUID(project_id_str))

        if not project:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        target_channel = None
        if project.discord_channel_id and interaction.guild:
            target_channel = interaction.guild.get_channel(project.discord_channel_id)

        modal = TaskCreateModal(
            self.task_service,
            project=project,
            target_channel=target_channel,
            auth_service=self.auth_service,
            parent_interaction=interaction,
            parent_view=self,
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        from src.adapters.discord_bot.views.task_menu import TaskMenuView

        projects = (
            await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            if interaction.guild
            else []
        )
        channel_id = self.current_channel_id or (interaction.channel.id if interaction.channel else None)
        parent_id = self.parent_channel_id or getattr(interaction.channel, "parent_id", None)

        view = TaskMenuView(
            self.task_service,
            self.project_service,
            self.squad_service,
            projects=projects,
            current_channel_id=channel_id,
            parent_channel_id=parent_id,
            initial_interaction=interaction,
        )
        await view._render_filtered_board(interaction)


__all__ = [
    "TaskProjectSearchModal",
    "TaskSelectProjectView",
]
