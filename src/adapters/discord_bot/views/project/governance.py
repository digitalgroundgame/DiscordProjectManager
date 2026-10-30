from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS, TOAST_DURATION_STANDARD
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.project.project_roles import ProjectRoleSelectView
from src.domain.models import Project, Squad
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    pass

logger = logging.getLogger("dgg_pm.views.project")


class ProjectAssignTimelineModal(BaseModal):
    """Modal to specify timeline when assigning a squad to a project."""

    def __init__(
        self,
        project_service: ProjectService,
        project: Project,
        squad: Squad,
        squad_service: SquadService,
        task_service: TaskService | None = None,
    ):
        super().__init__(title=f"Assign Squad to [{project.prefix}]"[:45])
        self.project_service = project_service
        self.project = project
        self.squad = squad
        self.squad_service = squad_service
        self.task_service = task_service

        self.timeline_input = discord.ui.TextInput(
            label="Target Timeline (Optional)",
            placeholder="e.g. Q3 2026, 6 weeks, Sprint 1-4",
            required=False,
            max_length=100,
        )
        self.add_item(self.timeline_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        timeline = self.timeline_input.value.strip() or None
        try:
            await self.project_service.assign_squad_to_project(
                project_id=self.project.id,
                squad_id=self.squad.id,
                timeline=timeline,
            )
            embed = discord.Embed(
                title="Squad Mapped to Project",
                description=(
                    f"Successfully mapped squad **{self.squad.name}** (<@&{self.squad.discord_role_id}>) "
                    f"to project **{self.project.name}** (`{self.project.prefix}`)."
                    + (f"\n\n• **Target Timeline:** `{timeline}`" if timeline else "")
                ),
                color=discord.Color.green(),
            )
            _, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction,
                e,
                f"assigning squad '{self.squad.name}' to project '{self.project.name}'",
                logger,
                ephemeral=True,
            )


class ProjectAssignSquadView(BaseView):
    """Interactive view to map a squad to a project with optional timeline."""

    def __init__(
        self,
        projects: list[Project],
        squads: list[Squad],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        squad_page: int = 0,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.projects = projects
        self.squads = squads
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.squad_page = squad_page

        self.selected_project_id: UUID = projects[0].id if projects else UUID(int=0)
        self.selected_squad_id: UUID = squads[0].id if squads else UUID(int=0)

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Select Project
        proj_options = [
            discord.SelectOption(
                label=f"{p.name} ({p.prefix})"[:100],
                value=str(p.id),
                description=(p.description[:50] if p.description else "Active Project"),
                default=(p.id == self.selected_project_id),
            )
            for i, p in enumerate(self.projects[:25])
        ]
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options or [discord.SelectOption(label="No Projects", value="none")],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Squad (Paged if >25)
        total_squad_pages = max(1, math.ceil(len(self.squads) / 25))
        if self.squad_page >= total_squad_pages:
            self.squad_page = max(0, total_squad_pages - 1)
        start_t = self.squad_page * 25
        page_squads = self.squads[start_t : start_t + 25]

        if not any(s.id == self.selected_squad_id for s in page_squads) and page_squads:
            self.selected_squad_id = page_squads[0].id

        squad_options = [
            discord.SelectOption(
                label=s.name[:100],
                value=str(s.id),
                description=f"Discord Role: @{s.discord_role_id}"[:50],
                default=(s.id == self.selected_squad_id),
            )
            for i, s in enumerate(page_squads)
        ]
        placeholder = (
            f"Select Squad (Page {self.squad_page + 1}/{total_squad_pages})..."
            if total_squad_pages > 1
            else "Select Squad..."
        )
        self.squad_select = discord.ui.Select(
            placeholder=placeholder,
            options=squad_options or [discord.SelectOption(label="No Squads", value="none")],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.squad_select.callback = self._on_squad_changed
        self.add_item(self.squad_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Map Squad (Quick)",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_quick_clicked
        self.add_item(self.assign_btn)

        self.timeline_btn = discord.ui.Button(
            label="Set Timeline & Map...",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.timeline_btn.callback = self._on_timeline_clicked
        self.add_item(self.timeline_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

        # Row 3: Squad Pagination buttons if total_squad_pages > 1
        if total_squad_pages > 1:
            self.prev_squad_btn = discord.ui.Button(
                label="◀ Prev Squads",
                style=discord.ButtonStyle.secondary,
                disabled=(self.squad_page <= 0),
                row=3,
            )
            self.prev_squad_btn.callback = self._on_prev_squad_clicked
            self.add_item(self.prev_squad_btn)

            self.next_squad_btn = discord.ui.Button(
                label="Next Squads ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.squad_page >= total_squad_pages - 1),
                row=3,
            )
            self.next_squad_btn.callback = self._on_next_squad_clicked
            self.add_item(self.next_squad_btn)

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    def _get_selected_squad(self) -> Squad | None:
        return next((s for s in self.squads if s.id == self.selected_squad_id), None)

    async def _on_prev_squad_clicked(self, interaction: discord.Interaction) -> None:
        if self.squad_page > 0:
            self.squad_page -= 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_next_squad_clicked(self, interaction: discord.Interaction) -> None:
        total_squad_pages = max(1, math.ceil(len(self.squads) / 25))
        if self.squad_page < total_squad_pages - 1:
            self.squad_page += 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        if self.proj_select.values and self.proj_select.values[0] != "none":
            self.selected_project_id = UUID(self.proj_select.values[0])
            for opt in self.proj_select.options:
                opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.edit_message(view=self)

    async def _on_squad_changed(self, interaction: discord.Interaction) -> None:
        if self.squad_select.values and self.squad_select.values[0] != "none":
            self.selected_squad_id = UUID(self.squad_select.values[0])
            for opt in self.squad_select.options:
                opt.default = opt.value == str(self.selected_squad_id)
        await interaction.response.edit_message(view=self)

    async def _on_assign_quick_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        squad = self._get_selected_squad()
        if not proj or not squad:
            await interaction.response.send_message("❌ Selection error.", ephemeral=True)
            return

        try:
            await self.project_service.assign_squad_to_project(
                project_id=proj.id,
                squad_id=squad.id,
                timeline=None,
            )
            embed = discord.Embed(
                title="Squad Mapped to Project",
                description=(
                    f"Successfully mapped squad **{squad.name}** (<@&{squad.discord_role_id}>) "
                    f"to project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.green(),
            )
            embed, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"assigning squad '{squad.name}' to project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_timeline_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        squad = self._get_selected_squad()
        if not proj or not squad:
            await interaction.response.send_message("❌ Selection error.", ephemeral=True)
            return

        modal = ProjectAssignTimelineModal(
            project_service=self.project_service,
            project=proj,
            squad=squad,
            squad_service=self.squad_service,
            task_service=self.task_service,
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectLeadSelectView(BaseView):
    """Interactive view to designate or clear a Project Lead for a project."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service

        self.selected_project_id: UUID = projects[0].id
        self.selected_user: discord.Member | discord.User | None = None

        # Row 0: Select Project
        proj_options = [
            discord.SelectOption(
                label=f"{p.name} ({p.prefix})"[:100],
                value=str(p.id),
                description=(f"Lead: @{p.lead_discord_id}" if p.lead_discord_id else "Unassigned")[:50],
                default=(i == 0),
            )
            for i, p in enumerate(projects[:25])
        ]
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Lead Member
        self.user_select = discord.ui.UserSelect(
            placeholder="Select Project Lead...",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.user_select.callback = self._on_user_selected
        self.add_item(self.user_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Designate Lead",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_clicked
        self.add_item(self.assign_btn)

        self.clear_btn = discord.ui.Button(
            label="Clear Lead",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.clear_btn.callback = self._on_clear_clicked
        self.add_item(self.clear_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        self.selected_project_id = UUID(self.proj_select.values[0])
        for opt in self.proj_select.options:
            opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.defer()

    async def _on_user_selected(self, interaction: discord.Interaction) -> None:
        self.selected_user = self.user_select.values[0]
        await interaction.response.defer()

    async def _on_assign_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        if not self.selected_user:
            if self.user_select.values:
                self.selected_user = self.user_select.values[0]
            else:
                await interaction.response.send_message(
                    "❌ Please select a Discord member to designate as Lead.", ephemeral=True
                )
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
                return

        try:
            await self.project_service.set_project_lead(proj.id, self.selected_user.id)
            embed = discord.Embed(
                title="Project Lead Designated",
                description=(
                    f"Designated <@{self.selected_user.id}> as the **Project Lead** for "
                    f"project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.green(),
            )
            embed, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"designating lead for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_clear_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        try:
            await self.project_service.set_project_lead(proj.id, None)
            embed = discord.Embed(
                title="Project Lead Cleared",
                description=f"Project **{proj.name}** (`{proj.prefix}`) has no designated Project Lead.",
                color=discord.Color.dark_grey(),
            )
            embed, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"clearing lead for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


__all__ = [
    "ProjectAssignSquadView",
    "ProjectAssignTimelineModal",
    "ProjectLeadSelectView",
    "ProjectRoleSelectView",
]
