"""Interactive views for restoring and reactivating archived projects."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.forum_helpers import ensure_pinned_hub_post
from src.adapters.discord_bot.views.project.roster import ProjectSearchModal
from src.domain.enums import TaskStatus
from src.domain.models import Project

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.project.restore")


class ProjectRestoreConfirmView(BaseView):
    """Interactive confirmation view prior to restoring an archived project."""

    def __init__(
        self,
        project: Project,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.project = project
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service

        self.confirm_btn = discord.ui.Button(
            label="Confirm Restore",
            style=discord.ButtonStyle.success,
            row=0,
        )
        self.confirm_btn.callback = self._on_confirm_clicked
        self.add_item(self.confirm_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        try:
            restored = await self.project_service.unarchive_project(self.project.id)
            if not restored:
                restored = self.project

            if self.task_service and interaction.guild and hasattr(interaction.client, "sync_task_thread"):
                restored_tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id,
                    project_id=self.project.id,
                    include_archived=False,
                    limit=500,
                )
                for t in restored_tasks:
                    if t.discord_thread_id and t.status != TaskStatus.COMPLETED:
                        await interaction.client.sync_task_thread(t, action="unarchive")

            hub_warning: str | None = None
            if interaction.guild and self.project.discord_channel_id:
                chan = interaction.guild.get_channel(self.project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        _ok, hub_msg = await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            squad_service=self.squad_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                        if "Warning:" in hub_msg:
                            hub_warning = hub_msg.split("Warning:", 1)[1].rstrip(".)").strip()
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project restore: %s", he)

            embed, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
            )
            warning_note = f"\n\n⚠️ **Notice:** {hub_warning}" if hub_warning else ""
            embed.description = (
                f"**Project Restored!**\n"
                f"Project **{restored.name} (`{restored.prefix}`)** has been reactivated."
                f"{warning_note}\n\n" + (embed.description or "")
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"restoring project '{self.project.name}'", logger, ephemeral=True
            )

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


def build_restore_select_embed(
    total_count: int,
    page: int,
    total_pages: int,
    query: str = "",
) -> discord.Embed:
    filter_note = f"\n**Search Filter Active:** `{query}`" if query else ""
    embed = discord.Embed(
        title="Restore Project",
        description=(
            f"Select an archived project below to restore:{filter_note}\n\n"
            f"• **Archived Projects Available:** `{total_count}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            "Selecting a project will open a confirmation screen."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Use Search or Page buttons to browse larger collections.")
    return embed


class ProjectRestoreSelectView(BaseView):
    """Interactive select menu to choose an archived project to restore with search and pagination."""

    PAGE_SIZE = 25

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        current_page: int = 0,
        query: str = "",
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.all_projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.current_page = current_page
        self.query = query
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()

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
        total_pages = max(1, math.ceil(total_count / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.PAGE_SIZE
        page_projects = self._filtered_projects[start_idx : start_idx + self.PAGE_SIZE]

        # Row 0: Select dropdown (up to 25 options)
        if page_projects:
            options = [
                discord.SelectOption(
                    label=f"{p.name} ({p.prefix})"[:100],
                    value=str(p.id),
                    description=(p.description[:90] if p.description else "Archived Project"),
                )
                for p in page_projects
            ]
            self.select = discord.ui.Select(
                placeholder=f"Select project to restore (Page {self.current_page + 1}/{total_pages})...",
                options=options,
                min_values=1,
                max_values=1,
                row=0,
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        # Row 1: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query:
            clear_btn = discord.ui.Button(
                label="Clear Filter",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 2: Pagination (if > 1 page)
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
        modal = ProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
            embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        project_id = UUID(self.select.values[0])
        project = next((p for p in self.all_projects if p.id == project_id), None)
        if not project and interaction.guild:
            project = await self.project_service.get_by_id(project_id)

        if not project:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        chan_note = f"\n• **Bound Channel:** <#{project.discord_channel_id}>" if project.discord_channel_id else ""

        embed = discord.Embed(
            title="Confirm Project Restoration",
            description=(
                f"Are you sure you want to restore project **{project.name} (`{project.prefix}`)**?\n"
                f"{chan_note}\n"
                "• **Project Status:** Reactivated and visible across project selectors.\n"
                "• **Active Tasks:** Incomplete tasks will be unarchived.\n"
                "• **Discord Threads:** Associated task threads will be reopened."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Click 'Confirm Restore' to execute or 'Cancel' to abort.")

        view = ProjectRestoreConfirmView(
            project=project,
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)
