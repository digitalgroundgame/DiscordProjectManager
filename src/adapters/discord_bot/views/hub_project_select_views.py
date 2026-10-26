"""Ephemeral project selector views for forum hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseView

if TYPE_CHECKING:
    from src.domain.models import Project
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.hub_project_select")


class HubTaskProjectSelectView(BaseView):
    """Ephemeral project selector displayed when clicking 'New Task' in a multi-project forum."""

    def __init__(
        self,
        task_service: TaskService,
        channel_projects: list[Project],
        target_channel: discord.ForumChannel | discord.TextChannel | discord.Thread | None,
        auth_service: AuthService | None = None,
        allow_standalone: bool = False,
    ):
        super().__init__(timeout=60)
        self.task_service = task_service
        self.channel_projects = channel_projects
        self.target_channel = target_channel
        self.auth_service = auth_service
        self.allow_standalone = allow_standalone
        self._initial_interaction: discord.Interaction | None = None
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.selected_project_val = (
            str(self.channel_projects[0].id)
            if self.channel_projects
            else ("standalone" if self.allow_standalone else None)
        )
        options = []
        for p in self.channel_projects[:24]:
            options.append(
                discord.SelectOption(
                    label=f"[{p.prefix}] {p.name}"[:100],
                    value=str(p.id),
                    description=(p.description[:90] if p.description else f"Prefix: {p.prefix}"),
                )
            )
        if self.allow_standalone:
            options.append(
                discord.SelectOption(
                    label="Standalone Task (No Project)",
                    value="standalone",
                    description="Create an ad-hoc unlinked chore/task",
                )
            )

        self.select = discord.ui.Select(
            placeholder="Select Project for New Task...",
            options=options,
            row=0,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.select_button = discord.ui.Button(
            label="Select & Open Modal",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.select_button.callback = self._on_select_button_clicked
        self.add_item(self.select_button)

        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.cancel_button.callback = self._on_cancel_clicked
        self.add_item(self.cancel_button)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title="New Task: Select Project",
            description="Choose which active project in this channel to create the task under:",
            color=discord.Color.blurple(),
        )

    async def _open_modal(self, interaction: discord.Interaction, val: str) -> None:
        parent_inter = self._initial_interaction or interaction
        if val == "standalone":
            modal = DiscordViewRouter.build_task_create_modal(
                task_service=self.task_service,
                project=None,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
                parent_interaction=parent_inter,
                parent_view=self,
            )
        else:
            selected_proj = next((p for p in self.channel_projects if str(p.id) == val), None)
            if not selected_proj:
                await interaction.response.send_message("❌ Selected project not found.", ephemeral=True)
                return
            modal = DiscordViewRouter.build_task_create_modal(
                task_service=self.task_service,
                project=selected_proj,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
                parent_interaction=parent_inter,
                parent_view=self,
            )
        await interaction.response.send_modal(modal)

    async def _on_select_button_clicked(self, interaction: discord.Interaction) -> None:
        val = self.selected_project_val or (str(self.channel_projects[0].id) if self.channel_projects else None)
        if not val:
            await interaction.response.send_message("❌ Please select a project.", ephemeral=True)
            return
        await self._open_modal(interaction, val)

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        try:
            embed = discord.Embed(
                title="Task Creation Cancelled",
                description="The project selector was closed.",
                color=discord.Color.dark_grey(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)
        except Exception as e:
            logger.debug("Error in cancel button: %s", e)

    async def on_timeout(self) -> None:
        try:
            if self._initial_interaction and hasattr(self._initial_interaction, "delete_original_response"):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    async def _on_select(self, interaction: discord.Interaction) -> None:
        val = self.select.values[0]
        self.selected_project_val = val
        await self._open_modal(interaction, val)


class HubBoardProjectSelectView(BaseView):
    """Ephemeral project scope selector displayed when clicking 'My Tasks' in a multi-project forum."""

    def __init__(
        self,
        task_service: TaskService,
        project_service: ProjectService,
        squad_service: SquadService | None,
        projects: list[Project],
        channel_projects: list[Project],
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        assignee_id: int | None = None,
    ):
        super().__init__(timeout=120)
        self.task_service = task_service
        self.project_service = project_service
        self.squad_service = squad_service
        self.projects = projects
        self.channel_projects = channel_projects
        self.current_channel_id = current_channel_id
        self.parent_channel_id = parent_channel_id
        self.assignee_id = assignee_id

        options = [
            discord.SelectOption(
                label="All Projects (Global Scope)",
                value="all",
                description="View tasks across all projects",
            )
        ]
        for p in channel_projects[:24]:
            options.append(
                discord.SelectOption(
                    label=f"[{p.prefix}] {p.name}"[:100],
                    value=str(p.id),
                    description=(p.description[:90] if p.description else f"Prefix: {p.prefix}"),
                )
            )

        self.select = discord.ui.Select(
            placeholder="Select Project Scope for Tasks...",
            options=options,
            row=0,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.cancel_button.callback = self._on_cancel_clicked
        self.add_item(self.cancel_button)

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        try:
            embed = discord.Embed(
                title="Selection Cancelled",
                description="The project scope selector was closed.",
                color=discord.Color.dark_grey(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)
        except Exception as e:
            logger.debug("Error in cancel button: %s", e)

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

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        val = self.select.values[0]
        selected_id = None if val == "all" else UUID(val)

        tasks, total = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=selected_id,
            assignee_discord_id=self.assignee_id,
            exclude_completed=True,
            limit=15,
        )

        project_label = "All Projects (Global Scope)"
        if selected_id:
            match = next((p for p in self.projects if p.id == selected_id), None)
            if match:
                project_label = f"[{match.prefix}] {match.name} (This Channel)"

        assignee_label = f"<@{self.assignee_id}> (My Tasks)" if self.assignee_id else "All Members"
        embed, view = DiscordViewRouter.build_task_menu(
            task_service=self.task_service,
            project_service=self.project_service,
            squad_service=self.squad_service,
            projects=self.projects,
            current_channel_id=self.current_channel_id,
            parent_channel_id=self.parent_channel_id,
            initial_project_id=selected_id,
            initial_assignee_id=self.assignee_id,
            tasks=tasks,
            total_count=total,
            project_label=project_label,
            status_label="Active Tasks (In Progress & Not Started)",
            assignee_label=assignee_label,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


__all__ = [
    "HubBoardProjectSelectView",
    "HubTaskProjectSelectView",
]
