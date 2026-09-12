"""Unified /pm slash command group for DGG-PM."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.forum_helpers import ensure_pinned_hub_post, setup_forum_tags
from src.adapters.discord_bot.views.hub_menu import build_hub_welcome_embed
from src.adapters.discord_bot.views.project_menu import RebuildConfirmView
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_task_history_embed,
)
from src.adapters.discord_bot.views.task_list_view import TaskListView, build_page_embed
from src.adapters.discord_bot.views.task_modals import parse_natural_date
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ITaskDiscordWorkspace,
    ProjectProvisionSpec,
)
from src.domain.enums import NotificationPreference, PriorityLevel, TaskStatus
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService, TeamService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.cogs.pm")


def extract_user_ids(text: str | None) -> list[int]:
    """Extracts Discord user snowflake IDs from mentions (<@123456789>) or raw IDs."""
    if not text:
        return []
    ids = re.findall(r"\d{4,20}", text)
    return [int(uid) for uid in set(ids)]


class PmCog(commands.GroupCog, group_name="pm", group_description="DGG-PM Project Management"):
    """Unified /pm command group consolidating task, project, squad, and settings operations."""

    task_group = app_commands.Group(name="task", description="Task operations, assignments, and status")
    project_group = app_commands.Group(name="project", description="Project container and channel operations")
    squad_group = app_commands.Group(name="squad", description="Functional squad and squad lead management")
    team_group = squad_group

    def __init__(
        self,
        bot: commands.Bot,
        project_service: ProjectService | None = None,
        team_service: TeamService | None = None,
        task_service: TaskService | None = None,
        auth_service: AuthService | None = None,
        user_service: UserService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        project_workspace: IProjectDiscordWorkspace | None = None,
        squad_service: SquadService | None = None,
    ):
        self.bot = bot
        self.project_service = project_service
        self.squad_service = squad_service or team_service
        self.team_service = self.squad_service
        self.task_service = task_service
        self.auth_service = auth_service or (
            AuthService(project_service, self.squad_service) if project_service and self.squad_service else None
        )
        self.user_service = user_service
        from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
        from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

        if workspace is not None:
            self.workspace = workspace
        elif isinstance(getattr(bot, "workspace", None), ITaskDiscordWorkspace):
            self.workspace = bot.workspace
        elif task_service and project_service:
            self.workspace = DiscordTaskWorkspaceAdapter(bot, task_service, project_service, self.auth_service)
        else:
            self.workspace = getattr(bot, "workspace", None)

        if project_workspace is not None:
            self.project_workspace = project_workspace
        elif isinstance(getattr(bot, "project_workspace", None), IProjectDiscordWorkspace):
            self.project_workspace = bot.project_workspace
        elif project_service:
            self.project_workspace = DiscordProjectWorkspaceAdapter(
                bot,
                project_service,
                team_service,
                task_service,
                user_service,
                self.auth_service,
            )
        else:
            self.project_workspace = getattr(bot, "project_workspace", None)

    # ==========================================
    # Autocomplete Helpers
    # ==========================================
    async def project_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            choices = [
                app_commands.Choice(name=f"{p.name} ({p.prefix})", value=p.name)
                for p in projects
                if not current or current.lower() in p.name.lower() or current.lower() in p.prefix.lower()
            ]
            return choices[:25]
        except Exception:
            return []

    async def task_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            tasks, _ = await self.task_service.list_tasks(
                guild_id=interaction.guild.id,
                search_query=current if current else None,
                exclude_completed=False,
                limit=25,
            )
            return [
                app_commands.Choice(
                    name=f"[{t.short_id}] {t.title}"[:100],
                    value=t.short_id,
                )
                for t in tasks
            ]
        except Exception:
            return []

    async def team_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            teams = await self.team_service.list_teams(interaction.guild.id)
            return [
                app_commands.Choice(name=t.name, value=t.name)
                for t in teams
                if not current or current.lower() in t.name.lower()
            ][:25]
        except Exception:
            return []

    # ==========================================
    # Root /pm Commands
    # ==========================================
    @app_commands.command(name="menu", description="Open the Project Management Administration & Control Center.")
    async def menu(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return
        try:
            from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed

            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            _, count = (
                await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
            )
            current_pref = (
                await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                if self.user_service
                else NotificationPreference.DM
            )

            view = PmDashboardView(
                project_service=self.project_service,
                team_service=self.team_service,
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
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
        except Exception as e:
            await send_interaction_error(interaction, e, "opening PM control center", logger, ephemeral=True)

    @app_commands.command(name="help", description="View help guide for DGG-PM slash commands and workflows.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = build_hub_welcome_embed()
            embed.title = "📖 DGG-PM User Guide & Commands"
            embed.description = (
                "**DGG-PM** is a collision-free project management bot.\n\n"
                "**Core Slash Commands (`/pm`):**\n"
                "• `/pm menu`: Open the Project Management Control Center\n"
                "• `/pm settings` or `/pm notifications`: Configure personal notification delivery\n"
                "• `/pm task create [project] [title]`: Create a task & thread\n"
                "• `/pm task list [project] [user] [status]`: View paginated active tasks\n"
                "• `/pm task assign [task] [assignee]`: Assign a task to a member\n"
                "• `/pm task status [task] [status]`: Update task status\n"
                "• `/pm task history [task]`: View task audit trail\n"
                "• `/pm project create [name] [prefix] [channel]`: Create project & bind forum\n"
                "• `/pm project list`: View all active projects\n"
                "• `/pm team lead [action] [team] [user]`: Designate/remove team lead\n"
                "• `/pm team list`: View server squad rosters and leads\n"
                "• `/pm post-hub [channel]`: Post and pin interactive PM Hub in forum/channel\n"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "opening help guide", logger, ephemeral=True)

    async def _handle_settings(self, interaction: discord.Interaction, notify_preference: str | None = None) -> None:
        if not self.user_service:
            await interaction.response.send_message("❌ User settings service is not enabled.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            if notify_preference:
                pref_enum = NotificationPreference(notify_preference)
                await self.user_service.set_preference(
                    guild_id=interaction.guild.id,
                    user_discord_id=interaction.user.id,
                    notify_preference=pref_enum,
                )
                await interaction.followup.send(
                    f"✅ Notification preference updated to **{pref_enum.value.upper()}**.",
                    ephemeral=True,
                )
            else:
                from src.adapters.discord_bot.views.settings_menu import UserSettingsView, build_settings_embed

                current_pref = await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                view = UserSettingsView(
                    user_service=self.user_service,
                    current_pref=current_pref,
                    project_service=self.project_service,
                    team_service=self.team_service,
                    task_service=self.task_service,
                )
                embed = build_settings_embed(interaction.user, current_pref)
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)

            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "updating settings", logger, ephemeral=True)

    @app_commands.command(name="settings", description="Configure your personal notification delivery preference.")
    @app_commands.describe(notify_preference="Choose where you receive task assignments and updates")
    @app_commands.choices(
        notify_preference=[
            app_commands.Choice(name="Direct Message (DM - Private & Permanent)", value="dm"),
            app_commands.Choice(name="Thread Channel Ping (Public In-Thread)", value="channel"),
            app_commands.Choice(name="Both (DM + Channel Ping)", value="both"),
            app_commands.Choice(name="Silent / None (No Alerts)", value="silent"),
        ]
    )
    async def settings(self, interaction: discord.Interaction, notify_preference: str | None = None) -> None:
        await self._handle_settings(interaction, notify_preference=notify_preference)

    @app_commands.command(
        name="notifications",
        description="Configure your personal notification delivery preference (DM, Channel, Both, Silent).",
    )
    @app_commands.describe(notify_preference="Choose where you receive task assignments and updates")
    @app_commands.choices(
        notify_preference=[
            app_commands.Choice(name="Direct Message (DM - Private & Permanent)", value="dm"),
            app_commands.Choice(name="Thread Channel Ping (Public In-Thread)", value="channel"),
            app_commands.Choice(name="Both (DM + Channel Ping)", value="both"),
            app_commands.Choice(name="Silent / None (No Alerts)", value="silent"),
        ]
    )
    async def notifications(self, interaction: discord.Interaction, notify_preference: str | None = None) -> None:
        await self._handle_settings(interaction, notify_preference=notify_preference)

    @app_commands.command(
        name="notification",
        description="Configure your personal notification delivery preference (DM, Channel, Both, Silent).",
    )
    @app_commands.describe(notify_preference="Choose where you receive task assignments and updates")
    @app_commands.choices(
        notify_preference=[
            app_commands.Choice(name="Direct Message (DM - Private & Permanent)", value="dm"),
            app_commands.Choice(name="Thread Channel Ping (Public In-Thread)", value="channel"),
            app_commands.Choice(name="Both (DM + Channel Ping)", value="both"),
            app_commands.Choice(name="Silent / None (No Alerts)", value="silent"),
        ]
    )
    async def notification(self, interaction: discord.Interaction, notify_preference: str | None = None) -> None:
        await self._handle_settings(interaction, notify_preference=notify_preference)

    @app_commands.command(
        name="post-hub",
        description="Post and pin an interactive Project Management Control Hub in a forum or text channel.",
    )
    @app_commands.describe(channel="Target channel to post and pin the PM Hub")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post_hub(
        self,
        interaction: discord.Interaction,
        channel: discord.ForumChannel | discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, (discord.ForumChannel, discord.TextChannel)):
            await interaction.response.send_message(
                "❌ Target must be a Forum Channel or Text Channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            _ok, msg = await ensure_pinned_hub_post(
                channel=target_channel,
                project_service=self.project_service,
                team_service=self.team_service,
                task_service=self.task_service,
                user_service=self.user_service,
            )
            await interaction.followup.send(msg, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "posting pinned PM hub", logger, ephemeral=True)

    # ==========================================
    # Task Subgroup: /pm task <cmd>
    # ==========================================
    @task_group.command(name="create", description="Create a new task and thread in a project container.")
    @app_commands.describe(
        project_name="Target project name",
        title="Brief task summary",
        assignee="Discord member to assign (must hold team role)",
        due="Natural due date (e.g. 'tomorrow 5pm')",
        priority="Task priority level",
        cc="Watcher members to notify (@mentions or IDs)",
        description="Optional detailed markdown description",
    )
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="Low", value="low"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def task_create(
        self,
        interaction: discord.Interaction,
        project_name: str,
        title: str,
        assignee: discord.Member | None = None,
        due: str | None = None,
        priority: str = "normal",
        cc: str | None = None,
        description: str | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_creation(interaction.user, project.id)
            if assignee:
                await self.auth_service.require_task_assignee_eligibility(interaction.guild, assignee, project.id)

            due_at = parse_natural_date(due) if due else None
            watchers = extract_user_ids(cc)
            p_level = (
                PriorityLevel(priority.lower())
                if priority.lower() in PriorityLevel._value2member_map_
                else PriorityLevel.NORMAL
            )

            task = await self.task_service.create_task(
                guild_id=interaction.guild.id,
                title=title,
                creator_discord_id=interaction.user.id,
                project_id=project.id,
                assignee_discord_id=assignee.id if assignee else None,
                due_at=due_at,
                priority=p_level,
                body=description,
                watchers=watchers,
            )

            if self.workspace:
                ref = await self.workspace.provision_workspace(
                    task,
                    project=project,
                    target_container=interaction.channel,
                    preferred_channel_id=interaction.channel_id,
                )
                await self.task_service.update_discord_message_ids(
                    task.id,
                    discord_message_id=ref.message_id,
                    discord_thread_id=ref.thread_id,
                )
                if ref.channel_id != interaction.channel_id:
                    await interaction.followup.send(
                        f"✅ Created task **[{task.short_id}] {task.title}** in <#{ref.channel_id}>"
                        + f" with thread <#{ref.thread_id}>"
                    )
                else:
                    await interaction.followup.send(
                        f"✅ Task **[{task.short_id}]** created in project **{project.name}**.",
                        embed=build_task_embed(task, project_name=project.name),
                    )
            else:
                await interaction.followup.send(
                    f"✅ Task **[{task.short_id}]** created in project **{project.name}**.",
                    embed=build_task_embed(task, project_name=project.name),
                )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"creating task in project '{project_name}'", logger, ephemeral=True
            )

    @task_group.command(name="list", description="Display filtered active tasks with interactive pagination.")
    @app_commands.describe(
        project_name="Filter by project name",
        user="Filter by assignee",
        status="Filter by execution status",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Active (In Progress & Not Started)", value="active"),
            app_commands.Choice(name="In Progress", value="inProgress"),
            app_commands.Choice(name="Not Started", value="notStarted"),
            app_commands.Choice(name="Completed", value="completed"),
            app_commands.Choice(name="All (including Completed)", value="all"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def task_list(
        self,
        interaction: discord.Interaction,
        project_name: str | None = None,
        user: discord.Member | None = None,
        status: str | None = None,
    ) -> None:
        if not interaction.guild:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            project_id = None
            title_parts = []
            if project_name:
                project = await self.project_service.get_by_name(interaction.guild.id, project_name)
                if not project:
                    await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                    return
                project_id = project.id
                title_parts.append(f"Project: {project.name}")

            if user:
                title_parts.append(f"Assignee: {user.display_name}")

            exclude_completed = False
            filter_status = None
            if status == "all":
                filter_status = None
                exclude_completed = False
                title_parts.append("Status: All")
            elif status in ("inProgress", "notStarted", "completed"):
                filter_status = TaskStatus(status)
                exclude_completed = False
                title_parts.append(f"Status: {filter_status.value}")
            else:
                filter_status = None
                exclude_completed = True
                if status == "active":
                    title_parts.append("Status: Active")

            tasks, total_count = await self.task_service.list_tasks(
                guild_id=interaction.guild.id,
                project_id=project_id,
                assignee_discord_id=user.id if user else None,
                status=filter_status,
                include_archived=False,
                exclude_completed=exclude_completed,
                limit=100,
            )

            title_context = "Tasks (" + ", ".join(title_parts) + ")" if title_parts else "Active Tasks"
            embed = build_page_embed(tasks, 0, total_count, title_context)
            view = TaskListView(tasks, total_count, title_context)

            await interaction.followup.send(embed=embed, view=view)
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
        except Exception as e:
            await send_interaction_error(interaction, e, "listing tasks", logger, ephemeral=True)

    @task_group.command(name="history", description="View the full audit trail and status history of a task.")
    @app_commands.describe(task="Task identifier (search by short ID or title)")
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_history(self, interaction: discord.Interaction, task: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            history = await self.task_service.get_history(task_entity.id)
            embed = build_task_history_embed(task_entity, history)
            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"retrieving history for task '{task}'", logger, ephemeral=True
            )

    @task_group.command(name="assign", description="Assign or reassign a task to a team member.")
    @app_commands.describe(
        task="Short ID of the task (e.g. APP-1)",
        assignee="Discord member to assign (or omit to unassign)",
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_assign(
        self,
        interaction: discord.Interaction,
        task: str,
        assignee: discord.Member | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            if assignee:
                await self.auth_service.require_task_assignee_eligibility(
                    interaction.guild, assignee, task_entity.project_id
                )

            new_assignee_id = assignee.id if assignee else None
            updated_task = await self.task_service.update_assignee(
                task_id=task_entity.id,
                new_assignee_id=new_assignee_id,
                actor_discord_id=interaction.user.id,
            )

            msg = (
                f"👤 Assigned **[{updated_task.short_id}]** to <@{new_assignee_id}>."
                if new_assignee_id
                else f"👤 Unassigned **[{updated_task.short_id}]**."
            )
            embed = build_task_embed(updated_task)
            if self.workspace:
                await self.workspace.sync_workspace(updated_task)
            elif hasattr(self.bot, "sync_root_task_message"):
                res = self.bot.sync_root_task_message(updated_task)
                if hasattr(res, "__await__"):
                    await res

            await interaction.followup.send(msg, embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"assigning task '{task}'", logger, ephemeral=True)

    @task_group.command(name="status", description="Update the execution status of a task.")
    @app_commands.describe(
        task="Short ID of the task",
        status="New task status",
        notes="Optional transition notes",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="⏳ Not Started", value="not_started"),
            app_commands.Choice(name="🟡 In Progress", value="in_progress"),
            app_commands.Choice(name="✅ Completed", value="completed"),
        ]
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_status(
        self,
        interaction: discord.Interaction,
        task: str,
        status: str,
        notes: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            new_status = TaskStatus(status)
            updated_task = await self.task_service.update_status(
                task_id=task_entity.id,
                new_status=new_status,
                expected_version=task_entity.version,
                actor_discord_id=interaction.user.id,
                notes=notes,
            )

            msg = f"🔄 Status updated to **{new_status.value.upper()}** for **[{updated_task.short_id}]**."
            embed = build_task_embed(updated_task)
            if self.workspace:
                await self.workspace.sync_workspace(updated_task)
            elif hasattr(self.bot, "sync_root_task_message"):
                res = self.bot.sync_root_task_message(updated_task)
                if hasattr(res, "__await__"):
                    await res

            await interaction.followup.send(msg, embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"updating status for task '{task}'", logger, ephemeral=True)

    @task_group.command(name="archive", description="Archive a completed or obsolete task.")
    @app_commands.describe(task="Short ID of the task to archive")
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_archive(self, interaction: discord.Interaction, task: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            updated = await self.task_service.archive_task(
                task_id=task_entity.id,
                actor_discord_id=interaction.user.id,
            )
            if updated and self.workspace:
                await self.workspace.sync_workspace(updated, sync_archive=True)
            elif updated and hasattr(self.bot, "sync_task_thread"):
                res = self.bot.sync_task_thread(updated, action="archive")
                if hasattr(res, "__await__"):
                    await res
            await interaction.followup.send(f"📦 Archived task **[{updated.short_id}]** (`{updated.title}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"archiving task '{task}'", logger, ephemeral=True)

    @task_group.command(name="unarchive", description="Restore an archived task.")
    @app_commands.describe(task="Short ID of the task to restore")
    async def task_unarchive(self, interaction: discord.Interaction, task: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            updated = await self.task_service.unarchive_task(
                task_id=task_entity.id,
                actor_discord_id=interaction.user.id,
            )
            if updated and self.workspace:
                await self.workspace.sync_workspace(updated, sync_archive=True)
            elif updated and hasattr(self.bot, "sync_task_thread"):
                res = self.bot.sync_task_thread(updated, action="unarchive")
                if hasattr(res, "__await__"):
                    await res
            await interaction.followup.send(f"📂 Restored task **[{updated.short_id}]** (`{updated.title}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"restoring task '{task}'", logger, ephemeral=True)

    @task_group.command(name="watchers", description="Manage watcher subscribers on a task.")
    @app_commands.describe(
        task="Short ID of the task",
        action="Action to perform",
        member="Target Discord member",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Watcher", value="add"),
            app_commands.Choice(name="Remove Watcher", value="remove"),
            app_commands.Choice(name="Clear All Watchers", value="clear"),
        ]
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_watchers(
        self,
        interaction: discord.Interaction,
        task: str,
        action: str,
        member: discord.Member | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            target_user_id = member.id if member else interaction.user.id
            is_self_service = (action in ("add", "remove")) and (target_user_id == interaction.user.id)
            if not is_self_service:
                await self.auth_service.require_task_mutation(interaction.user, task_entity)

            current_watchers = list(task_entity.watchers)
            if action == "add":
                if target_user_id not in current_watchers:
                    current_watchers.append(target_user_id)
            elif action == "remove":
                if target_user_id in current_watchers:
                    current_watchers.remove(target_user_id)
            elif action == "clear":
                current_watchers = []

            updated_task = await self.task_service.update_details(
                task_id=task_entity.id,
                actor_discord_id=interaction.user.id,
                watchers=current_watchers,
            )

            msg = f"👀 Updated watchers for **[{updated_task.short_id}]** ({len(updated_task.watchers)} total)."
            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"updating watchers for task '{task}'", logger, ephemeral=True)

    @task_group.command(
        name="depend",
        description="Add a prerequisite dependency: this task requires another task to finish first.",
    )
    @app_commands.describe(
        task="The dependent task that is blocked (e.g. INF-2)",
        depends_on="The prerequisite task that must finish first (e.g. INF-1)",
    )
    @app_commands.autocomplete(task=task_autocomplete, depends_on=task_autocomplete)
    async def task_depend(
        self,
        interaction: discord.Interaction,
        task: str,
        depends_on: str,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            target_task = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not target_task:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, target_task)

            await self.task_service.add_dependency(
                guild_id=interaction.guild.id,
                task_short_id=task,
                depends_on_short_id=depends_on,
                actor_discord_id=interaction.user.id,
            )
            await interaction.followup.send(
                f"🔗 Linked dependency: **`[{task}]`** now depends on **`[{depends_on}]`** finishing first.",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"linking dependency for '{task}'", logger, ephemeral=True)

    @task_group.command(
        name="undepend",
        description="Remove a prerequisite dependency from a task.",
    )
    @app_commands.describe(
        task="The dependent task (e.g. INF-2)",
        depends_on="The prerequisite task to unblock/unlink (e.g. INF-1)",
    )
    @app_commands.autocomplete(task=task_autocomplete, depends_on=task_autocomplete)
    async def task_undepend(
        self,
        interaction: discord.Interaction,
        task: str,
        depends_on: str,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            target_task = await self.task_service.get_by_short_id(interaction.guild.id, task)
            if not target_task:
                await interaction.followup.send(f"❌ Task '{task}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_mutation(interaction.user, target_task)

            removed = await self.task_service.remove_dependency(
                guild_id=interaction.guild.id,
                task_short_id=task,
                depends_on_short_id=depends_on,
                actor_discord_id=interaction.user.id,
            )
            if removed:
                await interaction.followup.send(
                    f"🔓 Unlinked dependency: **`[{task}]`** no longer depends on **`[{depends_on}]`**.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"ℹ️ **`[{task}]`** did not depend on **`[{depends_on}]`**.",
                    ephemeral=True,
                )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"unlinking dependency for '{task}'", logger, ephemeral=True)

    # ==========================================
    # Project Subgroup: /pm project <cmd>
    @project_group.command(name="create", description="Create a project container, bind channel, and map team role.")
    @app_commands.describe(
        name="Project Name (e.g. Mobile App)",
        prefix="Short uppercase task prefix (e.g. MOB)",
        role="Discord server role representing the squad working on this project",
        role_2="Optional second Discord server role representing another squad",
        role_3="Optional third Discord server role representing another squad",
        channel="Discord Forum Channel to bind for tasks",
        description="Optional markdown project overview",
        category="Optional organizational category",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_create(
        self,
        interaction: discord.Interaction,
        name: str,
        prefix: str,
        role: discord.Role,
        role_2: discord.Role | None = None,
        role_3: discord.Role | None = None,
        channel: discord.ForumChannel | None = None,
        description: str | None = None,
        category: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        if channel is not None and not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message(
                "❌ Projects must be bound to a Discord Forum Channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            roles = [r for r in [role, role_2, role_3] if r is not None]
            ref = await self.project_workspace.provision_project(
                ProjectProvisionSpec(
                    guild_id=interaction.guild.id,
                    name=name,
                    prefix=prefix,
                    role=role,
                    roles=roles,
                    channel=channel,
                    description=description,
                    category=category,
                )
            )
            project = ref.project

            hub_note = ""
            if ref.channel_id:
                hub_note = " • 📌 Pinned Control Hub created"

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles)
            embed = discord.Embed(
                title=f"📁 Project Created: {project.name} (`{project.prefix}`)",
                description=project.description or "*No description provided.*",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Task ID Prefix", value=f"`{project.prefix}-#`", inline=True)
            embed.add_field(name="Assigned Squad(s)", value=role_mentions, inline=True)
            if project.discord_channel_id:
                embed.add_field(name="Bound Channel", value=f"<#{project.discord_channel_id}>{hub_note}", inline=True)

            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating project '{name}'", logger, ephemeral=True)

    @project_group.command(name="list", description="List all active projects in this server.")
    async def project_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            if not projects:
                await interaction.followup.send("📁 No active projects found. Use `/pm project create` to set one up.")
                return

            embed = discord.Embed(title="📁 Server Projects", color=discord.Color.blue())
            for p in projects:
                chan_str = f"<#{p.discord_channel_id}>" if p.discord_channel_id else "*No channel bound*"
                role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
                role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*No squad role*"
                embed.add_field(
                    name=f"**{p.name}** (`{p.prefix}`)",
                    value=f"Channel: {chan_str}\nSquads: {role_str}\nStatus: Active",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "listing projects", logger, ephemeral=True)

    @project_group.command(name="tree", description="Render tech-tree dependency graph for a project.")
    @app_commands.describe(
        project_name="Name of the project to visualize",
        orientation="Layout orientation: horizontal (lr) or vertical (tb)",
    )
    @app_commands.choices(
        orientation=[
            app_commands.Choice(name="Horizontal (Left to Right)", value="lr"),
            app_commands.Choice(name="Vertical (Top to Bottom)", value="tb"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_tree(
        self,
        interaction: discord.Interaction,
        project_name: str,
        orientation: app_commands.Choice[str] | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_project_view(interaction.user, project.id)

            orient_val = orientation.value if orientation else "lr"
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=project.id,
                orientation=orient_val,
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            orient_label = "Horizontal (Left to Right)" if orient_val == "lr" else "Vertical (Top to Bottom)"
            embed = discord.Embed(
                title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
                description=f"Showing dependency graph in **{orient_label}** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            from src.adapters.discord_bot.views.tree_view import TechTreeViewer

            view = TechTreeViewer(
                self.task_service, project, current_orientation=orient_val, auth_service=self.auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"rendering tech tree for '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="archive", description="Archive a project container.")
    @app_commands.describe(project_name="Name of the project to archive")
    @app_commands.autocomplete(project_name=project_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_archive(self, interaction: discord.Interaction, project_name: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.project_service.archive_project(project.id)

            if hasattr(self.bot, "sync_task_thread") and self.task_service:
                tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id, project_id=project.id, status=None, include_archived=True
                )
                for t in tasks:
                    if t.discord_thread_id:
                        try:
                            await self.bot.sync_task_thread(t, action="archive")
                        except Exception as te:
                            logger.warning("Could not sync task thread on project archive for task %s: %s", t.id, te)

            if interaction.guild and project.discord_channel_id:
                chan = interaction.guild.get_channel(project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            team_service=self.team_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project archive: %s", he)

            await interaction.followup.send(f"📦 Archived project **{project.name}** (`{project.prefix}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"archiving project '{project_name}'", logger, ephemeral=True)

    @project_group.command(name="unarchive", description="Restore an archived project container.")
    @app_commands.describe(project_name="Name of the project to restore")
    @app_commands.autocomplete(project_name=project_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_unarchive(self, interaction: discord.Interaction, project_name: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.project_service.unarchive_project(project.id)

            if hasattr(self.bot, "sync_task_thread") and self.task_service:
                tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id, project_id=project.id, status=None, include_archived=True
                )
                for t in tasks:
                    if t.discord_thread_id:
                        try:
                            await self.bot.sync_task_thread(t, action="unarchive")
                        except Exception as te:
                            logger.warning("Could not sync task thread on project unarchive for task %s: %s", t.id, te)

            if interaction.guild and project.discord_channel_id:
                chan = interaction.guild.get_channel(project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            team_service=self.team_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project unarchive: %s", he)

            await interaction.followup.send(f"📂 Restored project **{project.name}** (`{project.prefix}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"restoring project '{project_name}'", logger, ephemeral=True)

    @project_group.command(name="role", description="Map or unmap a Discord team role on a project container.")
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            team = await self.team_service.get_or_create_team_for_role(interaction.guild.id, role.id, role.name)

            if action == "add":
                await self.project_service.assign_team_to_project(project_id=project.id, team_id=team.id)
                msg = f"🔗 Mapped role **{role.name}** (<@&{role.id}>) to project **{project.name}**."
            else:
                await self.project_service.remove_team_from_project(project_id=project.id, team_id=team.id)
                msg = f"✂️ Unmapped role **{role.name}** from project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing project role for '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="lead", description="Designate or remove a Team Lead for a project's squads.")
    @app_commands.describe(
        project_name="Target project name",
        user="Discord member to designate or remove as Team Lead",
        action="Action to perform (Add or Remove Lead)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Team Lead", value="add"),
            app_commands.Choice(name="Remove Team Lead", value="remove"),
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

            teams = await self.project_service.list_teams_for_project(project.id)
            if not teams:
                await interaction.followup.send(
                    f"❌ Project '{project.name}' has no assigned team roles.", ephemeral=True
                )
                return

            # Check auth: server manager or active lead
            is_manager = self.auth_service.is_server_manager(interaction.user)
            if not is_manager:
                is_any_lead = False
                for t in teams:
                    if await self.auth_service.can_manage_team_leads(interaction.user, t.id):
                        is_any_lead = True
                        break
                if not is_any_lead:
                    await interaction.followup.send(
                        "❌ You do not have permission to manage team leads for this project.",
                        ephemeral=True,
                    )
                    return

            user_roles = {r.id for r in getattr(user, "roles", []) if hasattr(r, "id")}
            matching_teams = [t for t in teams if t.discord_role_id in user_roles] if action == "add" else teams

            if action == "add":
                if not matching_teams:
                    role_mentions = ", ".join(f"<@&{t.discord_role_id}>" for t in teams)
                    await interaction.followup.send(
                        f"❌ <@{user.id}> does not hold any of project **{project.name}**'s assigned roles "
                        f"({role_mentions}).\nPlease assign them the Discord role first.",
                        ephemeral=True,
                    )
                    return
                for t in matching_teams:
                    await self.team_service.add_team_lead(t.id, user.id)
                msg = f"⭐ Designated <@{user.id}> as **Team Lead** for project **{project.name}**."
            else:
                for t in teams:
                    await self.team_service.remove_team_lead(t.id, user.id)
                msg = f"✅ Removed Team Lead status from <@{user.id}> for project **{project.name}**."

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
        team_name="Name of the functional squad",
        action="Map (assign) or Unmap (remove) squad",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map Squad to Project", value="map"),
            app_commands.Choice(name="Unmap Squad from Project", value="unmap"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete, team_name=team_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_squad(
        self,
        interaction: discord.Interaction,
        project_name: str,
        team_name: str,
        action: str = "map",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squad = await self.squad_service.get_by_name(interaction.guild.id, team_name)
            if not squad:
                await interaction.followup.send(f"❌ Squad '{team_name}' not found.", ephemeral=True)
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

    # Backward-compatible alias
    project_team = project_squad

    @project_group.command(
        name="setup-forum",
        description="Automatically configure standard project management tags on a Forum Channel.",
    )
    @app_commands.describe(forum="The Discord Forum Channel to configure with PM tags")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_setup_forum(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            tags_added, total_tags, err = await setup_forum_tags(forum)
            if err:
                await interaction.followup.send(f"❌ Failed to configure tags in <#{forum.id}>: {err}", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🏷️ Forum Tags Configured: #{forum.name}",
                description=(
                    f"Successfully verified and updated tags in <#{forum.id}>!\n\n"
                    f"• **New Tags Added:** `{tags_added}`\n"
                    f"• **Total Available Tags:** `{total_tags}` / 20\n\n"
                    "**Standard Tags Managed:**\n"
                    "• **Status:** `⏳ Not Started`, `🟡 In Progress`, `✅ Completed`\n"
                    "• **Priority:** `🔴 High Priority`, `🟡 Normal Priority`, `🟢 Low Priority`\n"
                    "• **Project:** One `📁 <Project Name>` tag per project bound to this forum"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"configuring forum tags for <#{forum.id}>", logger, ephemeral=True
            )

    @project_group.command(
        name="rebuild",
        description="Rebuild and reconcile a project's Discord forum, tags, hub, and task threads.",
    )
    @app_commands.describe(
        project_name="Name of the project to rebuild",
        forum="Optional target Forum Channel to rebind to (auto-creates if omitted and missing)",
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def project_rebuild(
        self,
        interaction: discord.Interaction,
        project_name: str,
        forum: discord.ForumChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        project = await self.project_service.get_by_name(interaction.guild.id, project_name)
        if not project:
            # Check if archived
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
            project = next((p for p in projects if p.name.lower() == project_name.lower()), None)
            if not project:
                await interaction.response.send_message(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

        # Fetch task count for preview
        task_count = 0
        if self.task_service:
            _, task_count = await self.task_service.list_tasks(
                guild_id=interaction.guild.id, project_id=project.id, include_archived=True
            )

        channel_status = (
            f"Rebind to <#{forum.id}>"
            if forum
            else (
                f"Existing: <#{project.discord_channel_id}>"
                if project.discord_channel_id and interaction.guild.get_channel(project.discord_channel_id)
                else "Auto-create new ForumChannel"
            )
        )

        embed = discord.Embed(
            title=f"⚠️ Confirm Workspace Rebuild: [{project.prefix}] {project.name}",
            description=(
                f"Are you sure you want to rebuild the Discord workspace for **{project.name}**?\n\n"
                f"**Planned Actions:**\n"
                f"• **Channel Target:** {channel_status}\n"
                f"• **Forum Tags:** Configure standard status, priority, and project tags\n"
                f"• **Control Hub:** Mount and pin interactive Control Hub post\n"
                f"• **Task Threads:** Reconcile **{task_count}** tasks "
                "(recreate missing threads and enforce Archive Invariant)\n"
                f"• **Project Status:** {'Unarchive and restore' if project.is_archived else 'Active'}\n\n"
                "⚠️ *This will pace Discord API calls to respect rate limits.*"
            ),
            color=discord.Color.orange(),
        )
        view = RebuildConfirmView(
            project_workspace=self.project_workspace,
            project=project,
            target_channel=forum,
            interaction=interaction,
            task_count=task_count,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==========================================
    # Squad Subgroup: /pm squad <cmd>
    # ==========================================
    @squad_group.command(name="create", description="Create a functional squad mapped to a Discord role.")
    @app_commands.describe(
        role="Discord server role representing the squad",
        team_name="Optional custom squad name",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def squad_create(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        team_name: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            name = team_name.strip() if team_name else role.name
            squad = await self.squad_service.create_squad(
                guild_id=interaction.guild.id,
                name=name,
                discord_role_id=role.id,
            )
            embed = discord.Embed(
                title=f"👥 Squad Created: {squad.name}",
                description=f"Mapped Discord Role: <@&{squad.discord_role_id}>",
                color=discord.Color.teal(),
            )
            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"creating squad for role '{role.name}'", logger, ephemeral=True
            )

    team_create = squad_create

    @squad_group.command(name="lead", description="Designate or remove a Squad Lead for a functional squad.")
    @app_commands.describe(
        action="Action to perform (Add or Remove Squad Lead)",
        team_name="Name of the functional squad",
        user="Discord member to designate or remove as Squad Lead",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Squad Lead", value="add"),
            app_commands.Choice(name="Remove Squad Lead", value="remove"),
        ]
    )
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def squad_lead(
        self,
        interaction: discord.Interaction,
        action: str,
        team_name: str,
        user: discord.Member,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            squad = await self.squad_service.get_by_name(interaction.guild.id, team_name)
            if not squad:
                await interaction.followup.send(f"❌ Squad '{team_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_squad_lead_management(interaction.user, squad.id)

            if action == "add":
                if hasattr(user, "roles"):
                    has_role = any(r.id == squad.discord_role_id for r in user.roles)
                    if not has_role:
                        await interaction.followup.send(
                            f"❌ <@{user.id}> is not part of squad (team) **{squad.name}** "
                            f"(missing role <@&{squad.discord_role_id}>).\n"
                            f"Please assign them the Discord role first.",
                            ephemeral=True,
                        )
                        return
                await self.squad_service.add_squad_lead(squad.id, user.id)
                msg = (
                    f"⭐ Designated <@{user.id}> as **Squad Lead** for squad "
                    f"**{squad.name}** (<@&{squad.discord_role_id}>)."
                )
            else:
                await self.squad_service.remove_squad_lead(squad.id, user.id)
                msg = f"✅ Removed Squad Lead status from <@{user.id}> for squad **{squad.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing squad lead for '{team_name}'", logger, ephemeral=True
            )

    team_lead = squad_lead

    @squad_group.command(name="list", description="List all functional squads and live Discord role rosters.")
    async def squad_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            squads = await self.squad_service.list_squads(interaction.guild.id)
            if not squads:
                await interaction.followup.send("👥 No squads created yet. Use `/pm squad create` to set one up.")
                return

            embed = discord.Embed(title="👥 Server Squads & Rosters", color=discord.Color.teal())
            for s in squads:
                leads = await self.squad_service.list_squad_leads(s.id)
                role = interaction.guild.get_role(s.discord_role_id) if hasattr(interaction.guild, "get_role") else None
                role_members = getattr(role, "members", []) if role else []
                role_member_ids = {m.id for m in role_members}

                # Auto-prune leads who lost the Discord role
                if role is not None:
                    valid_leads = []
                    for uid in leads:
                        if uid not in role_member_ids:
                            await self.squad_service.remove_squad_lead(s.id, uid)
                        else:
                            valid_leads.append(uid)
                    leads = valid_leads

                lead_strs = [f"<@{uid}>" for uid in leads]
                if lead_strs:
                    lead_line = f"⭐ **Squad Lead**: {', '.join(lead_strs)}"
                else:
                    lead_line = "⭐ **Squad Lead**: None designated"

                other_members = [f"<@{m.id}>" for m in role_members if m.id not in leads]
                member_count = len(role_members)
                if other_members:
                    sample = ", ".join(other_members[:8])
                    if len(other_members) > 8:
                        sample += f" *(+{len(other_members) - 8} more)*"
                    members_line = f"👤 **Members ({member_count})**: {sample}"
                elif role_members:
                    members_line = f"👤 **Members ({member_count})**: (All leads)"
                else:
                    members_line = f"👤 **Members ({member_count})**: None with role <@&{s.discord_role_id}>"

                field_val = f"Role: <@&{s.discord_role_id}>\n{lead_line}\n{members_line}"
                embed.add_field(name=f"**{s.name}**", value=field_val, inline=False)

            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "listing squads", logger, ephemeral=True)

    team_list = squad_list

    @app_commands.command(name="tree", description="Render tech-tree dependency graph for a project.")
    @app_commands.describe(
        project_name="Name of the project to visualize",
        orientation="Layout orientation: horizontal (lr) or vertical (tb)",
    )
    @app_commands.choices(
        orientation=[
            app_commands.Choice(name="Horizontal (Left to Right)", value="lr"),
            app_commands.Choice(name="Vertical (Top to Bottom)", value="tb"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def pm_tree(
        self,
        interaction: discord.Interaction,
        project_name: str,
        orientation: app_commands.Choice[str] | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_project_view(interaction.user, project.id)

            orient_val = orientation.value if orientation else "lr"
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=project.id,
                orientation=orient_val,
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            orient_label = "Horizontal (Left to Right)" if orient_val == "lr" else "Vertical (Top to Bottom)"
            embed = discord.Embed(
                title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
                description=f"Showing dependency graph in **{orient_label}** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            from src.adapters.discord_bot.views.tree_view import TechTreeViewer

            view = TechTreeViewer(
                self.task_service, project, current_orientation=orient_val, auth_service=self.auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"rendering tech tree for '{project_name}'", logger, ephemeral=True
            )
