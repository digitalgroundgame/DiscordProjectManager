from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseView
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.user_service import UserService

from src.adapters.discord_bot.views.project.archive import (
    ProjectArchiveSelectView,
    ProjectRestoreSelectView,
    build_archive_select_embed,
    build_restore_select_embed,
)
from src.adapters.discord_bot.views.project.governance import (
    ProjectLeadSelectView,
    ProjectRoleSelectView,
)
from src.adapters.discord_bot.views.project.rebuild import ProjectRebuildSelectView
from src.adapters.discord_bot.views.project.roster import (
    ProjectActiveListView,
    build_active_projects_embed,
)
from src.adapters.discord_bot.views.project.wizard import ProjectChannelSelectView

logger = logging.getLogger("dgg_pm.views.project")


class ProjectMenuView(BaseView):
    """Control Center View for Project Operations."""

    @classmethod
    async def create(
        cls,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        is_server_manager: bool | None = None,
        user_service: UserService | None = None,
        return_to: str = "dashboard",
        auth_service: AuthService | None = None,
    ) -> ProjectMenuView:
        effective_user = user or (initial_interaction.user if initial_interaction else None)
        guild_id = None
        if initial_interaction and initial_interaction.guild:
            guild_id = initial_interaction.guild.id
        elif hasattr(effective_user, "guild") and getattr(effective_user, "guild", None):
            guild_id = getattr(effective_user.guild, "id", None)
        if is_server_manager is None and auth_service and effective_user and guild_id:
            is_server_manager = await auth_service.can_manage_projects(effective_user, guild_id)
        return cls(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            initial_interaction=initial_interaction,
            user=user,
            is_server_manager=is_server_manager,
            user_service=user_service,
            return_to=return_to,
            auth_service=auth_service,
        )

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        is_server_manager: bool | None = None,
        user_service: UserService | None = None,
        return_to: str = "dashboard",
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to
        self.auth_service = auth_service
        self._initial_interaction = initial_interaction

        effective_user = user or (initial_interaction.user if initial_interaction else None)
        if is_server_manager is not None:
            self.is_server_manager = is_server_manager
        elif effective_user is not None:
            from src.services.auth_service import AuthService

            self.is_server_manager = AuthService.is_server_manager(effective_user)
        else:
            self.is_server_manager = True

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        if self.is_server_manager:
            self.new_project_btn = discord.ui.Button(
                label="Create Project",
                style=discord.ButtonStyle.primary,
                row=0,
            )
            self.new_project_btn.callback = self._on_new_project_clicked
            self.add_item(self.new_project_btn)
        else:
            self.new_project_btn = None

        # Row 0: Active Projects (Always visible)
        self.list_projects_btn = discord.ui.Button(
            label="Active Projects",
            style=discord.ButtonStyle.secondary if self.is_server_manager else discord.ButtonStyle.primary,
            row=0,
        )
        self.list_projects_btn.callback = self._on_list_projects_clicked
        self.add_item(self.list_projects_btn)

        if self.is_server_manager:
            self.set_role_btn = discord.ui.Button(
                label="Set Squad Role",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.set_role_btn.callback = self._on_set_role_clicked
            self.add_item(self.set_role_btn)

            self.set_lead_btn = discord.ui.Button(
                label="Set Project Lead",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.set_lead_btn.callback = self._on_set_lead_clicked
            self.add_item(self.set_lead_btn)

            self.archive_btn = discord.ui.Button(
                label="Archive Project",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.archive_btn.callback = self._on_archive_clicked
            self.add_item(self.archive_btn)

            self.restore_btn = discord.ui.Button(
                label="Restore Project",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.restore_btn.callback = self._on_restore_clicked
            self.add_item(self.restore_btn)

            self.rebuild_btn = discord.ui.Button(
                label="Rebuild Workspace",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            self.rebuild_btn.callback = self._on_rebuild_clicked
            self.add_item(self.rebuild_btn)
        else:
            self.set_role_btn = None
            self.set_lead_btn = None
            self.archive_btn = None
            self.restore_btn = None
            self.rebuild_btn = None

        if self.task_service:
            self.tech_tree_btn = discord.ui.Button(
                label="Visual Graph",
                style=discord.ButtonStyle.secondary,
                row=2 if self.is_server_manager else 0,
            )
            self.tech_tree_btn.callback = self._on_tech_tree_clicked
            self.add_item(self.tech_tree_btn)

            self.hub_btn = discord.ui.Button(
                label="PM Main Menu",
                style=discord.ButtonStyle.secondary,
                row=2 if self.is_server_manager else 0,
            )
            self.hub_btn.callback = self._on_hub_clicked
            self.add_item(self.hub_btn)
        else:
            self.tech_tree_btn = None
            self.hub_btn = None

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

    async def _on_tech_tree_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not self.task_service:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("ℹ️ No active projects found in this server.", ephemeral=True)
            return

        from src.services.auth_service import AuthService

        auth_service = AuthService(self.project_service, self.squad_service)
        accessible_projects = []
        for p in projects:
            if await auth_service.can_view_project(interaction.user, p.id):
                accessible_projects.append(p)

        if not accessible_projects:
            await interaction.response.send_message(
                "❌ You do not have permission to view any project visual graphs.\n"
                "You must hold a project squad's Discord role, be a Project Lead, or be a server manager.",
                ephemeral=True,
            )
            return

        if len(accessible_projects) == 1:
            target_proj = accessible_projects[0]
            await interaction.response.defer(ephemeral=True)
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=target_proj.id,
                orientation="lr",
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            embed = discord.Embed(
                title=f"Visual Graph: [{target_proj.prefix}] {target_proj.name}",
                description="Showing dependency graph in **Horizontal (Left to Right)** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            view = DiscordViewRouter.build_tech_tree_viewer(
                self.task_service, target_proj, current_orientation="lr", auth_service=auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
            return

        view = DiscordViewRouter.build_tech_tree_project_select(
            self.task_service, self.project_service, accessible_projects, orientation="lr", auth_service=auth_service
        )
        await interaction.response.send_message(
            "Choose a project to view its Visual Graph visualization:",
            view=view,
            ephemeral=True,
        )

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            _, count = (
                await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
            )
            current_pref = (
                await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                if self.user_service
                else None
            )
            embed, view = DiscordViewRouter.build_admin_dashboard(
                guild=interaction.guild,
                user=interaction.user,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
                active_projects=projects,
                active_tasks_count=count,
                current_pref=current_pref,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            if self.task_service:
                embed, view = DiscordViewRouter.build_hub_view(
                    self.project_service,
                    self.squad_service,
                    self.task_service,
                    self.user_service,
                    auth_service=self.auth_service,
                )
                await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_new_project_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectChannelSelectView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to=self.return_to,
        )
        embed = discord.Embed(
            title="Create Project: Select Forum Channel",
            description=(
                "Choose a **Forum Channel** to bind as the project's task board.\n\n"
                "Tasks become organized forum post cards with native Discord tag filtering "
                "and an interactive pinned Control Hub."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_list_projects_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found in this server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        embed = build_active_projects_embed(projects, page=0, total_count=len(projects))
        view = ProjectActiveListView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_role_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found. Create a project first!", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectRoleSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Map Squad Discord Role to Project",
            description="Select a project and choose a Discord role as its dedicated contributor squad:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_lead_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found. Create a project first!", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectLeadSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Designate Project Lead",
            description="Select a project and designate a member as the Project Lead with management permissions:",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_archive_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects available to archive.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectArchiveSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        total_pages = max(1, math.ceil(len(projects) / ProjectArchiveSelectView.PAGE_SIZE))
        embed = build_archive_select_embed(len(projects), 0, total_pages)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_restore_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        all_proj = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
        archived = [p for p in all_proj if p.is_archived]
        if not archived:
            await interaction.response.send_message("No archived projects to restore.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectRestoreSelectView(
            archived,
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        total_pages = max(1, math.ceil(len(archived) / ProjectRestoreSelectView.PAGE_SIZE))
        embed = build_restore_select_embed(len(archived), 0, total_pages)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_rebuild_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        all_proj = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
        if not all_proj:
            await interaction.response.send_message("No projects found to rebuild.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        client = getattr(interaction, "client", None)
        workspace = getattr(client, "project_workspace", None)
        if not workspace:
            workspace = DiscordProjectWorkspaceAdapter(
                bot=client,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
            )

        view = ProjectRebuildSelectView(
            all_proj,
            project_service=self.project_service,
            project_workspace=workspace,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Rebuild Project Discord Workspace",
            description=(
                "Select a project to restore and reconcile its Discord presence.\n\n"
                "• **Forum Channel:** Verified or auto-provisioned\n"
                "• **Tags & Hub:** Standard tags and Pinned Control Hub refreshed\n"
                "• **Task Threads:** Intact threads synced, missing threads re-created\n"
                "• **Archive Invariant:** Completed tasks preserved and archived"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


def build_project_menu_embed(is_server_manager: bool = True) -> discord.Embed:
    embed = discord.Embed(
        title="Project Management Hub",
        color=discord.Color.dark_theme(),
    )
    if is_server_manager:
        embed.description = (
            "> **Project Administration & Squad Routing**\n"
            "> Manage project containers, channel bindings, squad roles, and project leads.\n\n"
            "• **`Create Project`**: Create a project container bound to a Forum channel\n"
            "• **`Active Projects`**: View all running projects, squad roles, and designated leads\n"
            "• **`Set Squad Role`**: Map a Discord role as the project's contributor squad\n"
            "• **`Set Project Lead`**: Designate the project owner / lead with elevated permissions\n"
            "• **`Archive Project`**: Soft-delete a completed project\n"
            "• **`Restore Project`**: Bring back an archived project\n"
            "• **`Rebuild Workspace`**: Reconstruct forum channel, tags, hub, and task threads"
        )
    else:
        embed.description = (
            "> **Active Project Containers**\n"
            "> Browse active project containers, bound channels, and designated squad roles.\n\n"
            "• **`Active Projects`**: View all running projects, squad roles, and designated leads"
        )
    embed.set_footer(text="dgg-pm • Discord-Native Project Management")
    return embed
