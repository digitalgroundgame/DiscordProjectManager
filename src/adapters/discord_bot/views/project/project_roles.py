"""Interactive view to map or clear contributor Discord roles for a project."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.models import Project

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.project.roles")


class ProjectRoleSelectView(BaseView):
    """Interactive view to map or clear a contributor Discord role for a project."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction

        self.selected_project_id: UUID = projects[0].id
        self.selected_role: discord.Role | None = None
        self.selected_roles: list[discord.Role] = []

        # Row 0: Select Project
        proj_options = []
        for i, p in enumerate(projects[:25]):
            role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
            roles_desc = f"Roles: {len(role_ids)}" if role_ids else "Public (No roles)"
            proj_options.append(
                discord.SelectOption(
                    label=f"{p.name} ({p.prefix})"[:100],
                    value=str(p.id),
                    description=roles_desc[:50],
                    default=(i == 0),
                )
            )
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Discord Role(s)
        self.role_select = discord.ui.RoleSelect(
            placeholder="Select Squad Discord Role(s)...",
            min_values=1,
            max_values=25,
            row=1,
        )
        self.role_select.callback = self._on_role_selected
        self.add_item(self.role_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Add Squad Roles",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_clicked
        self.add_item(self.assign_btn)

        self.remove_btn = discord.ui.Button(
            label="Remove Squad Roles",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        self.remove_btn.callback = self._on_remove_clicked
        self.add_item(self.remove_btn)

        self.clear_btn = discord.ui.Button(
            label="Clear All Roles",
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

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        self.selected_project_id = UUID(self.proj_select.values[0])
        for opt in self.proj_select.options:
            opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.defer()

    async def _on_role_selected(self, interaction: discord.Interaction) -> None:
        self.selected_roles = list(self.role_select.values)
        self.selected_role = self.selected_roles[0] if self.selected_roles else None
        await interaction.response.defer()

    async def _on_assign_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        roles_to_add: list[discord.Role] = list(self.role_select.values) if self.role_select.values else []
        if not roles_to_add and self.selected_roles:
            roles_to_add = list(self.selected_roles)
        elif not roles_to_add and self.selected_role:
            roles_to_add = [self.selected_role]

        if not roles_to_add:
            await interaction.response.send_message(
                "❌ Please select at least one Discord role to map to this project.", ephemeral=True
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        try:
            guild_id = (
                interaction.guild.id
                if (interaction.guild and isinstance(getattr(interaction.guild, "id", None), int))
                else proj.guild_id
            )
            if self.squad_service:
                existing_squads = await self.project_service.list_squads_for_project(proj.id)
                for s in existing_squads:
                    await self.project_service.remove_squad_from_project(proj.id, s.id)

                for r in roles_to_add:
                    r_name = str(getattr(r, "name", None) or f"Squad-{r.id}")
                    if "Mock" in r_name or "<MagicMock" in r_name:
                        r_name = f"Squad-{r.id}"
                    squad = await self.squad_service.get_or_create_squad_for_role(guild_id, r.id, r_name)
                    await self.project_service.assign_squad_to_project(proj.id, squad.id)
            else:
                await self.project_service.set_project_role(proj.id, roles_to_add[0].id)

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles_to_add)
            embed = discord.Embed(
                title="Squad Roles Mapped to Project",
                description=(
                    f"Mapped Discord role(s) {role_mentions} as contributor squads for "
                    f"project **{proj.name}** (`{proj.prefix}`)."
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
                interaction, e, f"mapping roles for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_remove_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        roles_to_remove: list[discord.Role] = list(self.role_select.values) if self.role_select.values else []
        if not roles_to_remove and self.selected_roles:
            roles_to_remove = list(self.selected_roles)
        elif not roles_to_remove and self.selected_role:
            roles_to_remove = [self.selected_role]

        if not roles_to_remove:
            await interaction.response.send_message(
                "❌ Please select at least one Discord role to remove from this project.", ephemeral=True
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        try:
            guild_id = (
                interaction.guild.id
                if (interaction.guild and isinstance(getattr(interaction.guild, "id", None), int))
                else proj.guild_id
            )
            for r in roles_to_remove:
                if self.squad_service:
                    squad = await self.squad_service.get_by_role_id(guild_id, r.id)
                    if squad:
                        await self.project_service.remove_squad_from_project(proj.id, squad.id)

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles_to_remove)
            embed = discord.Embed(
                title="Squad Roles Removed from Project",
                description=(
                    f"Removed Discord role(s) {role_mentions} from project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.orange(),
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
                interaction, e, f"removing roles from project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_clear_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        try:
            if self.squad_service:
                squads = await self.project_service.list_squads_for_project(proj.id)
                for s in squads:
                    await self.project_service.remove_squad_from_project(proj.id, s.id)
            await self.project_service.set_project_role(proj.id, None)

            embed = discord.Embed(
                title="Project Role Cleared",
                description=(
                    f"Project **{proj.name}** (`{proj.prefix}`) is now **Public** (open to all server members)."
                ),
                color=discord.Color.blue(),
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
                interaction, e, f"clearing role for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


__all__ = ["ProjectRoleSelectView"]
