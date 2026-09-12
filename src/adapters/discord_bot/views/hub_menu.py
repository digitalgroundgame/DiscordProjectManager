from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.views.project_menu import ProjectMenuView, build_project_menu_embed
from src.adapters.discord_bot.views.task_menu import (
    TaskCreateModal,
    TaskMenuView,
    TaskSelectProjectView,
    build_task_board_embed,
)
from src.domain.models import Project, Squad
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.task_service import TaskService
from src.services.team_service import TeamService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.hub_menu")


class HubTaskProjectSelectView(discord.ui.View):
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

        options = []
        for p in channel_projects[:24]:
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

        self.selected_project_val: str | None = (
            str(channel_projects[0].id) if channel_projects else ("standalone" if allow_standalone else None)
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

    async def _open_modal(self, interaction: discord.Interaction, val: str) -> None:
        parent_inter = self._initial_interaction or interaction
        if val == "standalone":
            modal = TaskCreateModal(
                task_service=self.task_service,
                project=None,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
                parent_interaction=parent_inter,
            )
        else:
            selected_proj = next((p for p in self.channel_projects if str(p.id) == val), None)
            if not selected_proj:
                await interaction.response.send_message("❌ Selected project not found.", ephemeral=True)
                return
            modal = TaskCreateModal(
                task_service=self.task_service,
                project=selected_proj,
                target_channel=self.target_channel,
                auth_service=self.auth_service,
                parent_interaction=parent_inter,
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


class HubBoardProjectSelectView(discord.ui.View):
    """Ephemeral project scope selector displayed when clicking 'My Tasks' in a multi-project forum."""

    def __init__(
        self,
        task_service: TaskService,
        project_service: ProjectService,
        team_service: TeamService | None,
        projects: list[Project],
        channel_projects: list[Project],
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        assignee_id: int | None = None,
    ):
        super().__init__(timeout=120)
        self.task_service = task_service
        self.project_service = project_service
        self.team_service = team_service
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

        view = TaskMenuView(
            self.task_service,
            self.project_service,
            self.team_service,
            projects=self.projects,
            current_channel_id=self.current_channel_id,
            parent_channel_id=self.parent_channel_id,
            initial_project_id=selected_id,
            initial_assignee_id=self.assignee_id,
        )
        assignee_label = f"<@{self.assignee_id}> (My Tasks)" if self.assignee_id else "All Members"
        embed = build_task_board_embed(
            tasks=tasks,
            total_count=total,
            project_label=project_label,
            status_label="Active Tasks (In Progress & Not Started)",
            assignee_label=assignee_label,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class PmHubView(discord.ui.View):
    """Master Hub View allowing seamless opening of private interactive sessions for each user.

    All button interactions respond ephemerally or open modals, ensuring the public pinned
    control post in the forum/channel is never modified or disrupted by individual user clicks.
    """

    def __init__(
        self,
        project_service: ProjectService,
        team_service: TeamService,
        task_service: TaskService,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=None)
        self.project_service = project_service
        self.team_service = team_service
        self.task_service = task_service
        self.user_service = user_service
        self.auth_service = auth_service or AuthService(project_service, team_service)

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
                team_service=self.team_service,
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

        # Refresh pinned control hub message in case it is out of date
        await self._refresh_hub_message(interaction, projects=projects)

        channel_id = interaction.channel.id if interaction.channel else None
        parent_id = getattr(interaction.channel, "parent_id", None)
        channel_ids = {cid for cid in (channel_id, parent_id) if cid}
        channel_projects = [p for p in projects if p.discord_channel_id and p.discord_channel_id in channel_ids]

        target_channel = interaction.channel
        if isinstance(target_channel, discord.Thread):
            parent = getattr(target_channel, "parent", None)
            if not parent and getattr(target_channel, "parent_id", None):
                parent = interaction.guild.get_channel(target_channel.parent_id)
            if isinstance(parent, discord.ForumChannel):
                target_channel = parent

        auth_srv = AuthService(self.project_service, self.team_service) if self.team_service else None
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
            modal = TaskCreateModal(
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
        else:
            # 0 channel projects
            if len(allowed_projects) == 1:
                matched_proj = allowed_projects[0]
                if matched_proj.discord_channel_id and not isinstance(target_channel, discord.ForumChannel):
                    proj_chan = interaction.guild.get_channel(matched_proj.discord_channel_id)
                    if proj_chan:
                        target_channel = proj_chan
                modal = TaskCreateModal(
                    task_service=self.task_service,
                    project=matched_proj,
                    target_channel=target_channel,
                    auth_service=auth_srv,
                )
                await interaction.response.send_modal(modal)
            elif len(allowed_projects) == 0:
                if can_create_standalone:
                    modal = TaskCreateModal(
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
                view = TaskSelectProjectView(
                    projects=allowed_projects,
                    task_service=self.task_service,
                    project_service=self.project_service,
                    team_service=self.team_service,
                    current_channel_id=channel_id,
                    parent_channel_id=parent_id,
                    auth_service=auth_srv,
                    initial_interaction=interaction,
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
                team_service=self.team_service,
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

        view = TaskMenuView(
            self.task_service,
            self.project_service,
            self.team_service,
            projects=projects,
            current_channel_id=channel_id,
            parent_channel_id=parent_id,
            initial_project_id=selected_project_id,
            initial_assignee_id=interaction.user.id,
            initial_interaction=interaction,
        )
        embed = build_task_board_embed(
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
        view = ProjectMenuView(
            self.project_service,
            self.team_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
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
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            embed = discord.Embed(
                title=f"Visual Graph: [{target_project.prefix}] {target_project.name}",
                description="Showing dependency graph in **Horizontal (Left to Right)** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            from src.adapters.discord_bot.views.tree_view import TechTreeViewer

            view = TechTreeViewer(
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

        from src.adapters.discord_bot.views.tree_view import TechTreeProjectSelectView

        view = TechTreeProjectSelectView(
            self.task_service, self.project_service, projects, orientation="lr", auth_service=self.auth_service
        )
        await interaction.response.send_message(
            "Choose a project to view its Visual Graph visualization:",
            view=view,
            ephemeral=True,
        )


def _format_squad_roles(squads: list[Squad] | None) -> str:
    """Formats list of squad roles as Discord mentions or fallback labels."""
    if not squads:
        return "**Squad Role**: *Open (No role required)*"

    role_mentions: list[str] = []
    for s in squads:
        if s.discord_role_id and s.discord_role_id > 0:
            role_mentions.append(f"<@&{s.discord_role_id}>")
        else:
            role_mentions.append(f"**{s.name}**")

    if len(role_mentions) == 1:
        return f"**Squad Role**: {role_mentions[0]}"
    return f"**Squad Roles**: {', '.join(role_mentions)}"


def build_hub_welcome_embed(
    channel_name: str | None = None,
    bound_projects: list[Project] | None = None,
    project_squads_map: dict[UUID, list[Squad]] | None = None,
) -> discord.Embed:
    if channel_name:
        title = f"#{channel_name} • Forum Control Hub"
    else:
        title = "Forum Control Hub"

    embed = discord.Embed(
        title=title,
        color=discord.Color.dark_theme(),
    )

    if bound_projects:
        if len(bound_projects) == 1:
            p = bound_projects[0]
            squads = (project_squads_map or {}).get(p.id)
            role_line = _format_squad_roles(squads)
            desc_text = p.description or "Active contributor task feed and discussion board."
            desc = (
                f"**[{p.prefix}] {p.name}**\n"
                f"> {role_line}\n"
                f"> *{desc_text}*\n\n"
                "Click any button below to manage tasks, explore dependencies, or view projects."
            )
        else:
            project_blocks: list[str] = []
            for p in bound_projects:
                squads = (project_squads_map or {}).get(p.id)
                role_line = _format_squad_roles(squads)
                desc_text = f"> *{p.description}*\n" if p.description else ""
                project_blocks.append(f"**[{p.prefix}] {p.name}**\n> {role_line}\n{desc_text}".rstrip())

            projects_summary = "\n\n".join(project_blocks)
            desc = (
                f"### Bound Projects in this Channel\n\n"
                f"{projects_summary}\n\n"
                "Click any button below to manage tasks, explore dependencies, or view projects."
            )
    else:
        desc = (
            "> Welcome to **dgg-pm**! Interactive project and task management.\n\n"
            "Click any button below to launch a private interactive workspace."
        )

    embed.description = desc
    embed.add_field(
        name="New Task",
        value="Create a new task card within this forum channel.",
        inline=True,
    )
    embed.add_field(
        name="Visual Graph",
        value="Render the visual dependency DAG diagram.",
        inline=True,
    )
    embed.add_field(
        name="My Tasks",
        value="Filter to your personalized assigned tasks and deadlines.",
        inline=True,
    )
    embed.add_field(
        name="Projects Hub",
        value="Browse project settings, squads, and project leads.",
        inline=True,
    )
    embed.set_footer(text="dgg-pm • Discord-Native Project Management")
    return embed
