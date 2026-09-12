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

    role_badge = "Server Administrator / Manager" if is_server_manager else "Project Contributor"

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
        embed.add_field(
            name="Management Actions",
            value=(
                "• **`New Project`**: Launch the multi-step project creation wizard.\n"
                "• **`Projects`**: Manage channels, assign squad roles, set leads, archive.\n"
                "• **`Server Overview`**: Server-wide project status & completion metrics.\n"
                "• **`Settings`**: Configure personal notification preferences."
            ),
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
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self._initial_interaction = initial_interaction

        effective_user = user or (initial_interaction.user if initial_interaction else None)
        if is_server_manager is not None:
            self.is_server_manager = is_server_manager
        elif effective_user is not None:
            self.is_server_manager = AuthService.is_server_manager(effective_user)
        else:
            self.is_server_manager = True

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Core Management Buttons
        if self.is_server_manager:
            self.new_proj_btn = discord.ui.Button(
                label="New Project",
                style=discord.ButtonStyle.success,
                row=0,
            )
            self.new_proj_btn.callback = self._on_new_project_clicked
            self.add_item(self.new_proj_btn)

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

    async def _on_projects_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to="dashboard",
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
                "• `/pm squad create`: Map a Discord role to a contributor squad.\n\n"
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
