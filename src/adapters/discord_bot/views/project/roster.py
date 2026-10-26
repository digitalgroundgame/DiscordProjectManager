from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.domain.models import Project
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    pass

logger = logging.getLogger("dgg_pm.views.project")


class ProjectSearchModal(BaseModal):
    """Modal to enter a search query for filtering projects."""

    def __init__(self, on_search_callback: Callable[[discord.Interaction, str], Any], current_query: str = ""):
        super().__init__(title="Search Projects")
        self.on_search_callback = on_search_callback

        self.query_input = discord.ui.TextInput(
            label="Project Name, Prefix, or Category",
            placeholder="e.g. INF, Security, Mobile, DevOps...",
            default=current_query,
            required=False,
            max_length=100,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.query_input.value.strip()
        await self.on_search_callback(interaction, query)


def build_active_projects_embed(
    projects: list[Project],
    page: int,
    total_count: int,
    query: str = "",
    page_size: int = 8,
) -> discord.Embed:
    total_pages = max(1, math.ceil(total_count / page_size))
    start_idx = page * page_size
    page_projects = projects[start_idx : start_idx + page_size]

    filter_str = f" • Filter: `{query}`" if query else ""
    embed = discord.Embed(
        title=f"Active Projects ({total_count}){filter_str}",
        color=discord.Color.blurple(),
    )
    if not page_projects:
        embed.description = "*No active projects match your search.*"
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        return embed

    for p in page_projects:
        chan_str = f"<#{p.discord_channel_id}>" if p.discord_channel_id else "No channel"
        role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
        role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*Public / All Members*"
        lead_str = f"<@{p.lead_discord_id}>" if p.lead_discord_id else "*Unassigned*"
        desc_str = p.description or "No description"
        cat_str = f" • [{p.category}]" if p.category else ""
        embed.add_field(
            name=f"{p.name} (`{p.prefix}`){cat_str}",
            value=(
                f"• Channel: {chan_str}\n"
                f"• Squad: {role_str} | Lead: {lead_str}\n"
                f"• Next Task: #{p.next_task_number}\n"
                f"• {desc_str}"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Page {page + 1} of {total_pages} • Total: {total_count} projects")
    return embed


class ProjectActiveListView(BaseView):
    """Paginated and searchable view for active projects."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        current_page: int = 0,
        query: str = "",
        page_size: int = 8,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.all_projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.current_page = current_page
        self.query = query
        self.page_size = page_size
        self._initial_interaction = initial_interaction
        self._filtered_projects = self._filter_projects()
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

    def _filter_projects(self) -> list[Project]:
        if not self.query:
            return self.all_projects
        q = self.query.lower()
        return [
            p
            for p in self.all_projects
            if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
        ]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_count = len(self._filtered_projects)
        total_pages = max(1, math.ceil(total_count / self.page_size))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        # Row 0: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query:
            clear_btn = discord.ui.Button(
                label="Clear Filter",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 1: Pagination buttons
        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=1,
            )
            prev_btn.callback = self._on_prev_clicked
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=1,
            )
            next_btn.callback = self._on_next_clicked
            self.add_item(next_btn)

    async def _on_search_clicked(self, interaction: discord.Interaction) -> None:
        modal = ProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        embed = build_active_projects_embed(
            self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        embed = build_active_projects_embed(
            self._filtered_projects, self.current_page, len(self._filtered_projects), page_size=self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            embed = build_active_projects_embed(
                self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
            )
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.page_size))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = build_active_projects_embed(
                self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
            )
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)
