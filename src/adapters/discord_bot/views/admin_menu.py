"""Admin & Project Management Workspace Dashboard for dgg-pm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.project_menu import (
    ProjectChannelSelectView,
    ProjectMenuView,
    build_project_menu_embed,
)
from src.adapters.discord_bot.views.settings_menu import (
    UserSettingsView,
    build_settings_embed,
)
from src.domain.enums import NotificationPreference
from src.services.auth_service import AuthService

if TYPE_CHECKING:
    from src.domain.models import Project
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.admin_menu")


def build_pm_dashboard_embed(
    guild: discord.Guild | None,
    user: discord.Member | discord.User,
    active_projects: list[Project] | None = None,
    active_tasks_count: int = 0,
    current_pref: NotificationPreference = NotificationPreference.DM,
    is_server_manager: bool = False,
    is_server_admin: bool | None = None,
) -> discord.Embed:
    """Builds the main Project Management & Administration Workspace embed for /pm menu."""
    guild_name = guild.name if guild else "Server"
    proj_count = len(active_projects) if active_projects else 0

    pref_labels = {
        NotificationPreference.DM: "DM Only",
        NotificationPreference.CHANNEL: "Channel Ping",
        NotificationPreference.BOTH: "Both (DM + Channel)",
        NotificationPreference.NONE: "Silent / None",
    }
    pref_str = pref_labels.get(current_pref, current_pref.value)

    effective_admin = is_server_admin if is_server_admin is not None else is_server_manager
    if effective_admin:
        role_badge = "Server Administrator / Manager"
    elif is_server_manager:
        role_badge = "Team Lead / Project Manager"
    else:
        role_badge = "Project Contributor"

    embed = discord.Embed(
        title="Project Management Control Center",
        description=(
            f"> Server: **{guild_name}** • Access: **{role_badge}**\n\n"
            "Central administration portal for project containers, squad role mappings, "
            "server-wide metrics, and personal notification preferences."
        ),
        color=discord.Color.dark_theme(),
    )

    embed.add_field(
        name="Active Projects",
        value=f"**{proj_count}** active",
        inline=True,
    )
    embed.add_field(
        name="Active Tasks",
        value=f"**{active_tasks_count}** open",
        inline=True,
    )
    embed.add_field(
        name="Notifications",
        value=f"`{pref_str}`",
        inline=True,
    )

    if is_server_manager:
        actions = [
            "• **`Create Project`**: Launch the multi-step project creation wizard.",
            "• **`Projects`**: Manage channels, assign squad roles, set leads, archive.",
        ]
        if effective_admin:
            actions.append("• **`Lead Roles`**: Assign or view authorized Team Lead Discord roles.")
        actions.extend(
            [
                "• **`Server Overview`**: Server-wide project status & completion metrics.",
                "• **`Settings`**: Configure personal notification preferences.",
            ]
        )
        embed.add_field(
            name="Management Actions",
            value="\n".join(actions),
            inline=False,
        )
    else:
        embed.add_field(
            name="Available Workspace Actions",
            value=(
                "• **`Projects`**: Browse active projects and mapped channels.\n"
                "• **`Server Overview`**: View server-wide project progress.\n"
                "• **`Settings`**: Update your task assignment notification preferences."
            ),
            inline=False,
        )

    embed.set_footer(text="dgg-pm • Discord-Native Project Management")
    return embed


class PmDashboardOverviewView(BaseView):
    """View displaying server-wide overview with a Back to Dashboard button."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self._initial_interaction = initial_interaction

        self.back_btn = discord.ui.Button(
            label="Back to Control Center",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
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
            initial_interaction=interaction,
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


class PmDashboardView(BaseView):
    """Interactive administration and project management dashboard view for /pm menu."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        is_server_manager: bool | None = None,
        is_server_admin: bool | None = None,
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.auth_service = auth_service
        self._initial_interaction = initial_interaction

        effective_user = user or (initial_interaction.user if initial_interaction else None)
        if is_server_manager is not None:
            self.is_server_manager = is_server_manager
        elif effective_user is not None:
            self.is_server_manager = AuthService.is_server_manager(effective_user)
        else:
            self.is_server_manager = True

        if is_server_admin is not None:
            self.is_server_admin = is_server_admin
        elif effective_user is not None:
            self.is_server_admin = AuthService.is_server_manager(effective_user)
        else:
            self.is_server_admin = self.is_server_manager

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Core Management Buttons
        if self.is_server_manager:
            self.new_proj_btn = discord.ui.Button(
                label="Create Project",
                style=discord.ButtonStyle.success,
                row=0,
            )
            self.new_proj_btn.callback = self._on_new_project_clicked
            self.add_item(self.new_proj_btn)
        else:
            self.new_proj_btn = None

        if self.is_server_admin:
            self.lead_roles_btn = discord.ui.Button(
                label="Team Leads",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.lead_roles_btn.callback = self._on_lead_roles_clicked
            self.add_item(self.lead_roles_btn)
        else:
            self.lead_roles_btn = None

        self.projects_btn = discord.ui.Button(
            label="Projects",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.projects_btn.callback = self._on_projects_clicked
        self.add_item(self.projects_btn)

        self.overview_btn = discord.ui.Button(
            label="Server Overview",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.overview_btn.callback = self._on_overview_clicked
        self.add_item(self.overview_btn)

        # Row 1: Settings & Guides
        if self.user_service:
            self.settings_btn = discord.ui.Button(
                label="My Settings",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.settings_btn.callback = self._on_settings_clicked
            self.add_item(self.settings_btn)

        self.guides_btn = discord.ui.Button(
            label="Guides",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.guides_btn.callback = self._on_guides_clicked
        self.add_item(self.guides_btn)

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.unregister_menu(self._initial_interaction)
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    async def _on_new_project_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        view = ProjectChannelSelectView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to="dashboard",
        )
        embed = discord.Embed(
            title="📁 Create Project: Select Forum Channel",
            description=(
                "Select the target Forum Channel where this project will live.\n"
                "Standard status tags and an interactive pinned Control Hub will be created automatically."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_lead_roles_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        view = TeamLeadsAdminView(
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            auth_service=self.auth_service,
            initial_interaction=interaction,
        )
        embed = await view.build_embed(interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_projects_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to="dashboard",
            is_server_manager=self.is_server_manager,
            auth_service=self.auth_service,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_settings_clicked(self, interaction: discord.Interaction) -> None:
        if not self.user_service or not interaction.guild:
            await interaction.response.send_message("❌ User settings service is not available.", ephemeral=True)
            return

        current_pref = await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
        view = UserSettingsView(
            user_service=self.user_service,
            current_pref=current_pref,
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            initial_interaction=interaction,
            return_to="dashboard",
        )
        embed = build_settings_embed(interaction.user, current_pref)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_overview_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer()
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            squads = await self.squad_service.list_squads(interaction.guild.id) if self.squad_service else []

            embed = discord.Embed(
                title=f"📊 Server Project Management Overview • {interaction.guild.name}",
                description=f"**{len(projects)}** Active Projects • **{len(squads)}** Contributor Squads",
                color=discord.Color.blurple(),
            )

            if projects:
                lines = []
                for p in projects[:15]:
                    chan_str = f"<#{p.discord_channel_id}>" if p.discord_channel_id else "*No channel bound*"
                    role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
                    role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*No squad role*"
                    lead_str = f"<@{p.lead_discord_id}>" if p.lead_discord_id else "*No lead*"
                    lines.append(
                        f"• **[{p.prefix}] {p.name}**\n  Channel: {chan_str} | Squad: {role_str} | Lead: {lead_str}"
                    )
                embed.add_field(name="Active Projects", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Active Projects", value="*No active projects found.*", inline=False)

            if squads:
                t_lines = [f"• **{s.name}** (<@&{s.discord_role_id}>)" for s in squads[:10]]
                embed.add_field(name="Contributor Squads", value="\n".join(t_lines), inline=False)

            embed.set_footer(text="dgg-pm • Server Project Management Overview")
            view = PmDashboardOverviewView(
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
            )
            await interaction.edit_original_response(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(interaction, e, "generating server overview", logger, ephemeral=True)

    async def _on_guides_clicked(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📖 DGG-PM Workspace & Command Guide",
            description=(
                "**Core Slash Commands**:\n"
                "• `/pm menu`: Open the Project Management Control Center.\n"
                "• `/pm settings` or `/pm notifications`: View and adjust your personal notification delivery.\n"
                "• `/task-new`: Create a new task in any active project container.\n"
                "• `/task-list`: Browse open and completed tasks with multi-field filters.\n"
                "• `/task-depend` & `/task-undepend`: Manage task prerequisites and DAG dependencies.\n"
                "• `/tree`: Render Civilization-style Tech Tree dependency diagrams.\n"
                "• `/project-create`: Create a project and bind it to a Forum channel.\n"
                "• `/pm project role`: Map a Discord role to a project container.\n\n"
                "**Interactive Views**:\n"
                "• In Forum channels, check the pinned **Control Hub** post to create tasks and view tech trees!\n"
                "• All modal inputs and buttons run ephemerally to keep channels clean."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="dgg-pm • Built for Discord")
        view = PmDashboardOverviewView(
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)
