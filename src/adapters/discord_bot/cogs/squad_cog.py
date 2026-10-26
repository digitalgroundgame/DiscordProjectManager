from __future__ import annotations

import logging

import discord
from discord import app_commands

from src.adapters.discord_bot.cogs.autocomplete import AutocompleteMixin
from src.adapters.discord_bot.error_handler import send_interaction_error

logger = logging.getLogger("dgg_pm.cogs.squad")

project_autocomplete = AutocompleteMixin.project_autocomplete
squad_autocomplete = AutocompleteMixin.squad_autocomplete


class SquadCommandsMixin(AutocompleteMixin):
    """Squad and team roster management commands."""

    project_group = app_commands.Group(name="project", description="Project container and channel operations")

    @project_group.command(name="role", description="Map or unmap a Discord squad role on a project container.")
    @app_commands.describe(
        project_name="Target project name",
        role="Discord role representing the functional squad",
        action="Map (add) or Unmap (remove) role from project",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map Role to Project", value="add"),
            app_commands.Choice(name="Unmap Role from Project", value="remove"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_role(
        self,
        interaction: discord.Interaction,
        project_name: str,
        role: discord.Role,
        action: str = "add",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squad = await self.squad_service.get_or_create_squad_for_role(interaction.guild.id, role.id, role.name)

            if action == "add":
                await self.project_service.assign_squad_to_project(project_id=project.id, squad_id=squad.id)
                msg = f"🔗 Mapped role **{role.name}** (<@&{role.id}>) to project **{project.name}**."
            else:
                await self.project_service.remove_squad_from_project(project_id=project.id, squad_id=squad.id)
                msg = f"✂️ Unmapped role **{role.name}** from project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing project role for '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="lead", description="Designate or remove a Squad Lead for a project's squads.")
    @app_commands.describe(
        project_name="Target project name",
        user="Discord member to designate or remove as Squad Lead",
        action="Action to perform (Add or Remove Lead)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Squad Lead", value="add"),
            app_commands.Choice(name="Remove Squad Lead", value="remove"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_lead(
        self,
        interaction: discord.Interaction,
        project_name: str,
        user: discord.Member,
        action: str = "add",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squads = await self.project_service.list_squads_for_project(project.id)
            if not squads:
                await interaction.followup.send(f"❌ Project '{project.name}' has no assigned squads.", ephemeral=True)
                return

            # Check auth: server manager or active lead
            is_manager = self.auth_service.is_server_manager(interaction.user)
            if not is_manager:
                is_any_lead = False
                for s in squads:
                    if await self.auth_service.can_manage_squad_leads(interaction.user, s.id):
                        is_any_lead = True
                        break
                if not is_any_lead:
                    await interaction.followup.send(
                        "❌ You do not have permission to manage squad leads for this project.",
                        ephemeral=True,
                    )
                    return

            user_roles = {r.id for r in getattr(user, "roles", []) if hasattr(r, "id")}
            matching_squads = [s for s in squads if s.discord_role_id in user_roles] if action == "add" else squads

            if action == "add":
                if not matching_squads:
                    role_mentions = ", ".join(f"<@&{s.discord_role_id}>" for s in squads)
                    await interaction.followup.send(
                        f"❌ <@{user.id}> does not hold any of project **{project.name}**'s assigned roles "
                        f"({role_mentions}).\nPlease assign them the Discord role first.",
                        ephemeral=True,
                    )
                    return
                for s in matching_squads:
                    await self.squad_service.add_squad_lead(s.id, user.id)
                msg = f"⭐ Designated <@{user.id}> as **Squad Lead** for project **{project.name}**."
            else:
                for s in squads:
                    await self.squad_service.remove_squad_lead(s.id, user.id)
                msg = f"✅ Removed Squad Lead status from <@{user.id}> for project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing lead for project '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="squad", description="Map or unmap a functional squad to a project container.")
    @app_commands.describe(
        project_name="Target project name",
        squad_name="Name of the functional squad",
        action="Map (assign) or Unmap (remove) squad",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map Squad to Project", value="map"),
            app_commands.Choice(name="Unmap Squad from Project", value="unmap"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete, squad_name=squad_autocomplete)
    async def project_squad(
        self,
        interaction: discord.Interaction,
        project_name: str,
        squad_name: str,
        action: str = "map",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squad = await self.squad_service.get_by_name(interaction.guild.id, squad_name)
            if not squad:
                await interaction.followup.send(f"❌ Squad '{squad_name}' not found.", ephemeral=True)
                return

            if action == "map":
                await self.project_service.assign_squad_to_project(project_id=project.id, squad_id=squad.id)
                msg = f"🔗 Mapped squad **{squad.name}** (<@&{squad.discord_role_id}>) to project **{project.name}**."
            else:
                await self.project_service.remove_squad_from_project(project_id=project.id, squad_id=squad.id)
                msg = f"✂️ Unmapped squad **{squad.name}** from project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"mapping squad to project '{project_name}'", logger, ephemeral=True
            )
