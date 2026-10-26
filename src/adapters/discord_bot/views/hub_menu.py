from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.member_resolver import resolve_discord_member_name
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.hub_embeds import (
    _format_squad_roles,
    build_hub_welcome_embed,
)
from src.adapters.discord_bot.views.hub_project_select_views import (
    HubBoardProjectSelectView,
    HubTaskProjectSelectView,
)
from src.domain.models import Project, Squad
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.hub_menu")
_hub_bg_tasks: set[asyncio.Task] = set()


class PmHubView(BaseView):
    """Master Hub View allowing seamless opening of private interactive sessions for each user.


    All button interactions respond ephemerally or open modals, ensuring the public pinned
    control post in the forum/channel is never modified or disrupted by individual user clicks.
    """

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=None)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        if auth_service is not None:
            self.auth_service = auth_service
        else:
            uow = getattr(task_service, "uow", None)
            self.auth_service = AuthService(
                project_service,
                squad_service,
                uow=uow,
            )

    async def _refresh_hub_message(
        self,
        interaction: discord.Interaction,
        projects: list[Project] | None = None,
    ) -> None:
        msg = getattr(interaction, "message", None)
        if not interaction.guild or not msg or not hasattr(msg, "edit"):
            return
        try:
            if projects is None:
                projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)

            channel_id = interaction.channel.id if interaction.channel else None
            parent_id = getattr(interaction.channel, "parent_id", None)
            channel_ids = {cid for cid in (channel_id, parent_id) if cid}
            channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

            channel_obj = interaction.channel
            if isinstance(channel_obj, discord.Thread) and channel_obj.parent:
                channel_name = channel_obj.parent.name
            else:
                channel_name = getattr(channel_obj, "name", None)

            project_squads_map: dict[UUID, list[Squad]] = {}
            for p in channel_projects:
                try:
                    squads = await self.project_service.list_squads_for_project(p.id)
                    project_squads_map[p.id] = squads
                except Exception as sq_err:
                    logger.debug("Could not fetch squads for project %s: %s", p.id, sq_err)

            updated_embed = build_hub_welcome_embed(
                channel_name=channel_name,
                bound_projects=channel_projects,
                project_squads_map=project_squads_map,
            )
            updated_view = PmHubView(
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                auth_service=self.auth_service,
            )

            edit_res = interaction.message.edit(embed=updated_embed, view=updated_view)
            if hasattr(edit_res, "__await__"):
                await edit_res
        except Exception as e:
            logger.debug("Could not refresh hub message: %s", e)

    @discord.ui.button(
        label="New Task",
        style=discord.ButtonStyle.success,
        row=0,
        custom_id="pm_hub:new_task",
    )
    async def new_task_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)

        # Refresh pinned control hub message in the background so interaction doesn't time out
        try:
            task = asyncio.create_task(self._refresh_hub_message(interaction, projects=projects))
            _hub_bg_tasks.add(task)
            task.add_done_callback(_hub_bg_tasks.discard)
        except Exception:
            pass

        channel_id = interaction.channel.id if interaction.channel else None
        parent_id = getattr(interaction.channel, "parent_id", None)
        channel_ids = {cid for cid in (channel_id, parent_id) if cid}
        all_channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        target_channel = interaction.channel
        if isinstance(target_channel, discord.Thread):
            parent = getattr(target_channel, "parent", None)
            if not parent and getattr(target_channel, "parent_id", None):
                parent = interaction.guild.get_channel(target_channel.parent_id)
            if isinstance(parent, discord.ForumChannel):
                target_channel = parent

        auth_srv = AuthService(self.project_service, self.squad_service) if self.squad_service else None
        can_create_standalone = await auth_srv.can_create_task_in_project(interaction.user, None) if auth_srv else True

        allowed_projects = []
        for p in projects:
            if not auth_srv or await auth_srv.can_create_task_in_project(interaction.user, p.id):
                allowed_projects.append(p)

        channel_projects = [p for p in allowed_projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        if len(channel_projects) == 1:
            matched_proj = channel_projects[0]
            if matched_proj.discord_channel_id and not isinstance(target_channel, discord.ForumChannel):
                proj_chan = interaction.guild.get_channel(matched_proj.discord_channel_id)
                if proj_chan:
                    target_channel = proj_chan
            modal = DiscordViewRouter.build_task_create_modal(
                task_service=self.task_service,
                project=matched_proj,
                target_channel=target_channel,
                auth_service=auth_srv,
            )
            await interaction.response.send_modal(modal)
        elif len(channel_projects) > 1:
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
            picker_view = HubTaskProjectSelectView(
                task_service=self.task_service,
                channel_projects=channel_projects,
                target_channel=target_channel,
                auth_service=auth_srv,
                allow_standalone=can_create_standalone,
            )
            picker_view._initial_interaction = interaction
            embed = discord.Embed(
                title="New Task: Select Project",
                description="Choose which active project in this channel to create the task under:",
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, view=picker_view, ephemeral=True)
        elif all_channel_projects:
            # Channel is bound to projects, but user lacks permission to create tasks in any of them
            from src.adapters.discord_bot.menu_manager import menu_manager

            proj_names = ", ".join(f"**{p.name}**" for p in all_channel_projects)
            await interaction.response.send_message(
                f"❌ You do not have permission to create tasks in this channel's projects ({proj_names}). "
                "Contact a Project Lead or server manager to be added to a project squad.",
                ephemeral=True,
            )
            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        else:
            # 0 channel projects (unbound channel)
            if len(allowed_projects) == 1:
                matched_proj = allowed_projects[0]
                if matched_proj.discord_channel_id and not isinstance(target_channel, discord.ForumChannel):
                    proj_chan = interaction.guild.get_channel(matched_proj.discord_channel_id)
                    if proj_chan:
                        target_channel = proj_chan
                modal = DiscordViewRouter.build_task_create_modal(
                    task_service=self.task_service,
                    project=matched_proj,
                    target_channel=target_channel,
                    auth_service=auth_srv,
                )
                await interaction.response.send_modal(modal)
            elif len(allowed_projects) == 0:
                if can_create_standalone:
                    modal = DiscordViewRouter.build_task_create_modal(
                        task_service=self.task_service,
                        project=None,
                        target_channel=interaction.channel,
                        auth_service=auth_srv,
                    )
                    await interaction.response.send_modal(modal)
                else:
                    await interaction.response.send_message(
                        "❌ You do not have permission to create tasks in any active projects. "
                        "Contact a Project Lead or server manager to be added to a project squad.",
                        ephemeral=True,
                    )
                    from src.adapters.discord_bot.menu_manager import menu_manager

                    menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            else:
                from src.adapters.discord_bot.menu_manager import menu_manager

                await menu_manager.register_menu(interaction)
                view = DiscordViewRouter.build_task_select_project_view(
                    projects=allowed_projects,
                    task_service=self.task_service,
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    current_channel_id=channel_id,
                    parent_channel_id=parent_id,
                    auth_service=auth_srv,
                    initial_interaction=interaction,
                    filter_to_channel=False,
                )
                embed = discord.Embed(
                    title="Select Project Container",
                    description=(
                        "Choose which active project to create the task inside:\n"
                        "• Use **`Search Projects`** to quickly filter across all projects."
                    ),
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="My Tasks",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="pm_hub:my_tasks",
    )
    async def tasks_tab(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        from src.adapters.discord_bot.menu_manager import menu_manager

        await menu_manager.register_menu(interaction)

        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        await self._refresh_hub_message(interaction, projects=projects)
        channel_id = interaction.channel.id if interaction.channel else None
        parent_id = getattr(interaction.channel, "parent_id", None)

        channel_ids = {cid for cid in (channel_id, parent_id) if cid}
        channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        if len(channel_projects) > 1:
            picker_view = HubBoardProjectSelectView(
                task_service=self.task_service,
                project_service=self.project_service,
                squad_service=self.squad_service,
                projects=projects,
                channel_projects=channel_projects,
                current_channel_id=channel_id,
                parent_channel_id=parent_id,
                assignee_id=interaction.user.id,
            )
            picker_view._initial_interaction = interaction
            embed = discord.Embed(
                title="My Tasks: Select Project Scope",
                description="Choose which project scope to view your assigned tasks for:",
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, view=picker_view, ephemeral=True)
            return

        selected_project_id = channel_projects[0].id if channel_projects else None
        tasks, total = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=selected_project_id,
            assignee_discord_id=interaction.user.id,
            exclude_completed=True,
            limit=15,
        )

        project_label = "All Projects (Global Scope)"
        if selected_project_id:
            match = channel_projects[0]
            project_label = f"[{match.prefix}] {match.name} (This Channel)"

        embed, view = DiscordViewRouter.build_task_menu(
            task_service=self.task_service,
            project_service=self.project_service,
            squad_service=self.squad_service,
            projects=projects,
            current_channel_id=channel_id,
            parent_channel_id=parent_id,
            initial_project_id=selected_project_id,
            initial_assignee_id=interaction.user.id,
            initial_interaction=interaction,
            tasks=tasks,
            total_count=total,
            project_label=project_label,
            status_label="Active Tasks (In Progress & Not Started)",
            assignee_label=f"<@{interaction.user.id}> (My Tasks)",
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="Projects Hub",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="pm_hub:projects",
    )
    async def projects_tab(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from src.adapters.discord_bot.menu_manager import menu_manager

        await self._refresh_hub_message(interaction)
        await menu_manager.register_menu(interaction)
        can_manage = (
            await self.auth_service.can_manage_projects(
                interaction.user,
                guild_id=interaction.guild.id,
                guild=interaction.guild,
            )
            if (self.auth_service and interaction.guild)
            else False
        )
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            is_server_manager=can_manage,
            auth_service=self.auth_service,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="Create Project",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="pm_hub:create_project",
    )
    async def create_project_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        can_manage = (
            await self.auth_service.can_manage_projects(
                interaction.user,
                guild_id=interaction.guild.id,
                guild=interaction.guild,
            )
            if self.auth_service
            else False
        )
        if not can_manage:
            await interaction.response.send_message(
                "❌ You do not have permission to create projects. "
                "Contact a Server Manager or hold an authorized Team Lead role.",
                ephemeral=True,
            )
            return

        from src.adapters.discord_bot.menu_manager import menu_manager

        await menu_manager.register_menu(interaction)

        embed, view = DiscordViewRouter.build_project_channel_select(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to="hub",
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="Visual Graph",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="pm_hub:tech_tree",
    )
    async def tech_tree_tab(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        await self._refresh_hub_message(interaction, projects=projects)
        if not projects:
            await interaction.response.send_message("ℹ️ No active projects found in this server.", ephemeral=True)
            return

        channel_id = interaction.channel.id if interaction.channel else None
        parent_id = getattr(interaction.channel, "parent_id", None)
        channel_ids = {cid for cid in (channel_id, parent_id) if cid}
        channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        target_project = None
        if len(channel_projects) == 1:
            target_project = channel_projects[0]
        elif len(projects) == 1:
            target_project = projects[0]

        if target_project:
            if self.auth_service:
                if not await self.auth_service.can_view_project(interaction.user, target_project.id):
                    await interaction.response.send_message(
                        f"❌ You do not have permission to view the visual graph for **{target_project.name}**.\n"
                        "You must hold the project squad's Discord role, be the Project Lead, or be a server manager.",
                        ephemeral=True,
                    )
                    return

            await interaction.response.defer(ephemeral=True)
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=target_project.id,
                orientation="lr",
                member_resolver=lambda uid: resolve_discord_member_name(uid, interaction.guild),
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            embed = discord.Embed(
                title=f"Visual Graph: [{target_project.prefix}] {target_project.name}",
                description="Showing dependency graph in **Horizontal (Left to Right)** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            view = DiscordViewRouter.build_tech_tree_viewer(
                self.task_service, target_project, current_orientation="lr", auth_service=self.auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
            return

        if self.auth_service:
            accessible_projects = []
            for p in projects:
                if await self.auth_service.can_view_project(interaction.user, p.id):
                    accessible_projects.append(p)
            projects = accessible_projects
            if not projects:
                await interaction.response.send_message(
                    "❌ You do not have permission to view any project visual graphs.\n"
                    "You must hold a project squad's Discord role, be a Project Lead, or be a server manager.",
                    ephemeral=True,
                )
                return

        view = DiscordViewRouter.build_tech_tree_project_select(
            self.task_service, self.project_service, projects, orientation="lr", auth_service=self.auth_service
        )
        await interaction.response.send_message(
            "Choose a project to view its Visual Graph visualization:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="⏰ Overdue",
        style=discord.ButtonStyle.danger,
        row=0,
        custom_id="pm_hub:overdue",
    )
    async def overdue_tab(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        from src.adapters.discord_bot.menu_manager import menu_manager

        await menu_manager.register_menu(interaction)

        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        await self._refresh_hub_message(interaction, projects=projects)
        channel_id = interaction.channel.id if interaction.channel else None
        parent_id = getattr(interaction.channel, "parent_id", None)

        channel_ids = {cid for cid in (channel_id, parent_id) if cid}
        channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]
        selected_project_id = channel_projects[0].id if channel_projects else None

        tasks, total = await self.task_service.list_tasks(
            guild_id=interaction.guild.id,
            project_id=selected_project_id,
            overdue_only=True,
            limit=15,
        )

        project_label = "All Projects (Global Scope)"
        if selected_project_id:
            match = channel_projects[0]
            project_label = f"[{match.prefix}] {match.name} (This Channel)"

        embed, view = DiscordViewRouter.build_task_menu(
            task_service=self.task_service,
            project_service=self.project_service,
            squad_service=self.squad_service,
            projects=projects,
            current_channel_id=channel_id,
            parent_channel_id=parent_id,
            initial_project_id=selected_project_id,
            initial_interaction=interaction,
            tasks=tasks,
            total_count=total,
            project_label=project_label,
            status_label="⏰ Overdue Tasks (Past Deadline & Incomplete)",
            assignee_label="All Members",
        )
        embed.title = f"⏰ Overdue Tasks ({total})"
        embed.color = discord.Color.red()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


__all__ = [
    "HubBoardProjectSelectView",
    "HubTaskProjectSelectView",
    "PmHubView",
    "_format_squad_roles",
    "build_hub_welcome_embed",
]
