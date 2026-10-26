"""Team Leads Administration View for dgg-pm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.enums import NotificationPreference
from src.services.auth_service import AuthService

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.admin_team_leads")


class TeamLeadsAdminView(BaseView):
    """View allowing Server Managers to assign, view, and remove Team Leads (roles and members) for the guild."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.auth_service = auth_service
        self._initial_interaction = initial_interaction
        self.selected_role_id: int | None = None
        self.selected_user_id: int | None = None
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Select Discord Role
        self.role_select = discord.ui.RoleSelect(
            placeholder="Select a Discord role to assign or remove...",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.role_select.callback = self._on_role_selected
        self.add_item(self.role_select)

        # Row 1: Role Action buttons
        self.assign_btn = discord.ui.Button(
            label="Assign Role",
            style=discord.ButtonStyle.success,
            row=1,
        )
        self.assign_btn.callback = self._on_assign_clicked
        self.add_item(self.assign_btn)
        self.assign_role_btn = self.assign_btn

        self.remove_btn = discord.ui.Button(
            label="Remove Role",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        self.remove_btn.callback = self._on_remove_clicked
        self.add_item(self.remove_btn)
        self.remove_role_btn = self.remove_btn

        # Row 2: Select Discord User
        self.user_select = discord.ui.UserSelect(
            placeholder="Select a Discord member to assign or remove...",
            min_values=1,
            max_values=1,
            row=2,
        )
        self.user_select.callback = self._on_user_selected
        self.add_item(self.user_select)

        # Row 3: User Action buttons
        self.assign_user_btn = discord.ui.Button(
            label="Assign Member",
            style=discord.ButtonStyle.success,
            row=3,
        )
        self.assign_user_btn.callback = self._on_assign_user_clicked
        self.add_item(self.assign_user_btn)

        self.remove_user_btn = discord.ui.Button(
            label="Remove Member",
            style=discord.ButtonStyle.danger,
            row=3,
        )
        self.remove_user_btn.callback = self._on_remove_user_clicked
        self.add_item(self.remove_user_btn)

        # Row 4: Navigation
        self.back_btn = discord.ui.Button(
            label="Back to Dashboard",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

    async def _on_role_selected(self, interaction: discord.Interaction) -> None:
        selected_values = getattr(self.role_select, "values", []) or getattr(self.role_select, "_values", [])
        if selected_values:
            val = selected_values[0]
            self.selected_role_id = int(val.id) if hasattr(val, "id") else int(val)
        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_user_selected(self, interaction: discord.Interaction) -> None:
        selected_values = getattr(self.user_select, "values", []) or getattr(self.user_select, "_values", [])
        if selected_values:
            val = selected_values[0]
            self.selected_user_id = int(val.id) if hasattr(val, "id") else int(val)
        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def build_embed(self, guild: discord.Guild | None) -> discord.Embed:
        guild_id = guild.id if guild else None
        role_ids: set[int] = set()
        user_ids: set[int] = set()
        if self.auth_service and guild_id:
            role_ids = await self.auth_service.list_guild_lead_roles(guild_id)
            user_ids = await self.auth_service.list_guild_lead_users(guild_id)

        embed = discord.Embed(
            title="👥 Authorized Team Leads",
            description=(
                "Members holding any of the configured roles or designated individually can create and manage "
                "projects and squads without requiring server-wide `Manage Server` permissions.\n\n"
            ),
            color=discord.Color.blue(),
        )

        if role_ids:
            role_lines = []
            for rid in sorted(role_ids):
                role = guild.get_role(rid) if guild else None
                if role:
                    role_lines.append(f"• **@{role.name}** (`{rid}`)")
                else:
                    role_lines.append(f"• <@&{rid}> (`{rid}`)")
            embed.add_field(
                name=f"Configured Roles ({len(role_ids)})",
                value="\n".join(role_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="Configured Roles (0)",
                value="*No team lead roles configured yet. Use the role picker to assign one.*",
                inline=False,
            )

        if user_ids:
            user_lines = []
            for uid in sorted(user_ids):
                member = guild.get_member(uid) if guild else None
                if member:
                    user_lines.append(f"• **@{member.display_name}** (`{uid}`)")
                else:
                    user_lines.append(f"• <@{uid}> (`{uid}`)")
            embed.add_field(
                name=f"Configured Members ({len(user_ids)})",
                value="\n".join(user_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="Configured Members (0)",
                value="*No individual team lead members configured yet. Use the member picker to assign one.*",
                inline=False,
            )

        if self.selected_role_id:
            role = guild.get_role(self.selected_role_id) if guild else None
            role_name = f"@{role.name}" if role else f"<@&{self.selected_role_id}>"
            status = "Already Configured" if self.selected_role_id in role_ids else "Not Configured"
            embed.add_field(
                name="Selected Role",
                value=f"**{role_name}** (`{self.selected_role_id}`) — *{status}*",
                inline=False,
            )

        if self.selected_user_id:
            member = guild.get_member(self.selected_user_id) if guild else None
            user_name = f"@{member.display_name}" if member else f"<@{self.selected_user_id}>"
            status = "Already Configured" if self.selected_user_id in user_ids else "Not Configured"
            embed.add_field(
                name="Selected Member",
                value=f"**{user_name}** (`{self.selected_user_id}`) — *{status}*",
                inline=False,
            )

        embed.set_footer(text="dgg-pm • Team Lead Administration")
        return embed

    async def _on_assign_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        if not AuthService.is_server_manager(interaction.user):
            await interaction.response.send_message(
                "❌ Only Discord Server Managers can configure team leads.", ephemeral=True
            )
            return

        selected_values = getattr(self.role_select, "values", []) or getattr(self.role_select, "_values", [])
        if selected_values and not self.selected_role_id:
            val = selected_values[0]
            self.selected_role_id = int(val.id) if hasattr(val, "id") else int(val)

        if not self.selected_role_id:
            await interaction.response.send_message("⚠️ Please select a role from the dropdown first.", ephemeral=True)
            return

        if self.auth_service:
            await self.auth_service.add_guild_lead_role(interaction.guild.id, self.selected_role_id)

        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_remove_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        if not AuthService.is_server_manager(interaction.user):
            await interaction.response.send_message(
                "❌ Only Discord Server Managers can configure team leads.", ephemeral=True
            )
            return

        selected_values = getattr(self.role_select, "values", []) or getattr(self.role_select, "_values", [])
        if selected_values and not self.selected_role_id:
            val = selected_values[0]
            self.selected_role_id = int(val.id) if hasattr(val, "id") else int(val)

        if not self.selected_role_id:
            await interaction.response.send_message("⚠️ Please select a role from the dropdown first.", ephemeral=True)
            return

        if self.auth_service:
            await self.auth_service.remove_guild_lead_role(interaction.guild.id, self.selected_role_id)

        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_assign_user_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        if not AuthService.is_server_manager(interaction.user):
            await interaction.response.send_message(
                "❌ Only Discord Server Managers can configure team leads.", ephemeral=True
            )
            return

        selected_values = getattr(self.user_select, "values", []) or getattr(self.user_select, "_values", [])
        if selected_values and not self.selected_user_id:
            val = selected_values[0]
            self.selected_user_id = int(val.id) if hasattr(val, "id") else int(val)

        if not self.selected_user_id:
            await interaction.response.send_message("⚠️ Please select a member from the dropdown first.", ephemeral=True)
            return

        if self.auth_service:
            await self.auth_service.add_guild_lead_user(interaction.guild.id, self.selected_user_id)

        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_remove_user_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        if not AuthService.is_server_manager(interaction.user):
            await interaction.response.send_message(
                "❌ Only Discord Server Managers can configure team leads.", ephemeral=True
            )
            return

        selected_values = getattr(self.user_select, "values", []) or getattr(self.user_select, "_values", [])
        if selected_values and not self.selected_user_id:
            val = selected_values[0]
            self.selected_user_id = int(val.id) if hasattr(val, "id") else int(val)

        if not self.selected_user_id:
            await interaction.response.send_message("⚠️ Please select a member from the dropdown first.", ephemeral=True)
            return

        if self.auth_service:
            await self.auth_service.remove_guild_lead_user(interaction.guild.id, self.selected_user_id)

        embed = await self.build_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        from src.adapters.discord_bot.views.admin_menu import (
            PmDashboardView,
            build_pm_dashboard_embed,
        )

        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        _, count = await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
        current_pref = (
            await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
            if self.user_service
            else NotificationPreference.DM
        )

        view = PmDashboardView(
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            auth_service=self.auth_service,
            initial_interaction=interaction,
            user=interaction.user,
        )
        embed = build_pm_dashboard_embed(
            guild=interaction.guild,
            user=interaction.user,
            active_projects=projects,
            active_tasks_count=count,
            current_pref=current_pref,
            is_server_manager=view.is_server_manager,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


LeadRolesAdminView = TeamLeadsAdminView


__all__ = [
    "LeadRolesAdminView",
    "TeamLeadsAdminView",
]
