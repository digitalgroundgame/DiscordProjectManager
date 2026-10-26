from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from src.adapters.discord_bot.cogs.admin_cog import AdminCommandsMixin
from src.adapters.discord_bot.cogs.autocomplete import AutocompleteMixin
from src.adapters.discord_bot.cogs.project_cog import ProjectCommandsMixin
from src.adapters.discord_bot.cogs.squad_cog import SquadCommandsMixin
from src.adapters.discord_bot.cogs.task_cog import TaskCommandsMixin
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.member_resolver import resolve_discord_member_name
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.calendar_feed_view import (
    CalendarFeedView,
    build_calendar_feed_embed,
)
from src.adapters.discord_bot.views.forum_helpers import ensure_pinned_hub_post
from src.adapters.discord_bot.views.hub_menu import build_hub_welcome_embed
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ITaskDiscordWorkspace,
)
from src.domain.enums import NotificationPreference
from src.services.auth_service import AuthService
from src.services.calendar_service import CalendarService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.cogs.pm")


def extract_user_ids(text: str | None) -> list[int]:
    """Extracts Discord user snowflake IDs from mentions (<@123456789>) or raw IDs."""
    if not text:
        return []
    ids = re.findall(r"\d{4,20}", text)
    return [int(uid) for uid in set(ids)]


def extract_short_ids(text: str | None) -> list[str]:
    """Extracts task short IDs (e.g. 'INF-1', 'PROJ-12') from a delimiter-separated string."""
    if not text:
        return []
    tokens = re.split(r"[\s,;]+", text.strip())
    results: list[str] = []
    for token in tokens:
        clean = token.strip().strip("`#[]()").upper()
        if clean and "-" in clean and len(clean) <= 30:
            if clean not in results:
                results.append(clean)
    return results


project_autocomplete = AutocompleteMixin.project_autocomplete


class PmCog(
    TaskCommandsMixin,
    ProjectCommandsMixin,
    SquadCommandsMixin,
    AdminCommandsMixin,
    AutocompleteMixin,
    commands.GroupCog,
    group_name="pm",
    group_description="DGG-PM Project Management",
):
    """Unified /pm command group consolidating task, project, squad, and settings operations."""

    def __init__(
        self,
        bot: commands.Bot,
        project_service: ProjectService | None = None,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        auth_service: AuthService | None = None,
        user_service: UserService | None = None,
        calendar_service: CalendarService | None = None,
        outbox_service: OutboxService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        project_workspace: IProjectDiscordWorkspace | None = None,
    ):
        self.bot = bot
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.auth_service = auth_service or (
            AuthService(project_service, self.squad_service) if project_service else None
        )
        self.user_service = user_service
        self.calendar_service = calendar_service or getattr(bot, "calendar_service", None)
        self.outbox_service = outbox_service or getattr(bot, "outbox_service", None)
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
                squad_service,
                task_service,
                user_service,
                self.auth_service,
            )
        else:
            self.project_workspace = getattr(bot, "project_workspace", None)

    # Root /pm Commands
    # ==========================================
    @app_commands.command(name="menu", description="Open the Project Management Administration & Control Center.")
    async def menu(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            _, count = (
                await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
            )
            current_pref = (
                await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                if self.user_service
                else NotificationPreference.DM
            )

            can_manage = (
                await self.auth_service.can_manage_projects(
                    interaction.user,
                    guild_id=interaction.guild.id,
                    guild=interaction.guild,
                )
                if self.auth_service
                else False
            )
            is_server_admin = self.auth_service.is_server_manager(interaction.user) if self.auth_service else False

            embed, view = DiscordViewRouter.build_admin_dashboard(
                guild=interaction.guild,
                user=interaction.user,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                auth_service=self.auth_service,
                initial_interaction=interaction,
                active_projects=projects,
                active_tasks_count=count,
                current_pref=current_pref,
                is_server_manager=can_manage,
                is_server_admin=is_server_admin,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
        except Exception as e:
            await send_interaction_error(interaction, e, "opening PM menu", logger, ephemeral=True)

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
                "• `/pm menu`: Open the Project Management Control Center\n"
                "• `/pm settings` or `/pm notifications`: Configure personal notification delivery\n"
                "• `/pm calendar`: Get personal RFC 5545 calendar feed URL (Google, Apple, Outlook)\n"
                "• `/pm task create [project] [title] [depends_on]`: Create a task & thread\n"
                "• `/pm task list [project] [user] [status]`: View paginated active tasks\n"
                "• `/pm task assign [task] [assignee]`: Assign a task to a member\n"
                "• `/pm task status [task] [status]`: Update task status\n"
                "• `/pm task history [task]`: View task audit trail\n"
                "• `/pm project create [name] [prefix] [channel]`: Create project & bind forum\n"
                "• `/pm project list`: View all active projects\n"
                "• `/pm project role [project] [action] [role]`: Map or unmap Discord role\n"
                "• `/pm project lead [project] [user] [action]`: Designate project squad lead\n"
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
                current_pref = await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                embed, view = DiscordViewRouter.build_user_settings_view(
                    user=interaction.user,
                    current_pref=current_pref,
                    user_service=self.user_service,
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    task_service=self.task_service,
                    calendar_service=self.calendar_service,
                )
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
        name="calendar",
        description="Get your private RFC 5545 calendar subscription feed URL for Google Calendar, Apple, Outlook.",
    )
    async def calendar(self, interaction: discord.Interaction) -> None:
        """Personal iCalendar subscription feed link generator & management."""
        if not self.calendar_service:
            await interaction.response.send_message(
                "❌ Calendar subscription service is currently unavailable.", ephemeral=True
            )
            return

        try:
            token = await self.calendar_service.get_or_create_token(interaction.user.id)
            urls = self.calendar_service.build_feed_urls(token)
            embed = build_calendar_feed_embed(interaction.user, urls)
            view = CalendarFeedView(
                calendar_service=self.calendar_service,
                user_discord_id=interaction.user.id,
                token=token,
                initial_interaction=interaction,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(interaction, e, "retrieving calendar feed", logger, ephemeral=True)

    @app_commands.command(
        name="post-hub",
        description="Post and pin an interactive Project Management Control Hub in a forum or text channel.",
    )
    @app_commands.describe(channel="Target channel to post and pin the PM Hub")
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
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            _ok, msg = await ensure_pinned_hub_post(
                channel=target_channel,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
            )
            await interaction.followup.send(msg, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "posting pinned PM hub", logger, ephemeral=True)

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
                member_resolver=lambda uid: resolve_discord_member_name(uid, interaction.guild),
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            orient_label = "Horizontal (Left to Right)" if orient_val == "lr" else "Vertical (Top to Bottom)"
            embed = discord.Embed(
                title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
                description=f"Showing dependency graph in **{orient_label}** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            view = DiscordViewRouter.build_tech_tree_viewer(
                self.task_service, project, current_orientation=orient_val, auth_service=self.auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"rendering tech tree for '{project_name}'", logger, ephemeral=True
            )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Handle errors raised during app command check evaluation or execution within PmCog."""
        cmd_name = interaction.command.qualified_name if interaction.command else "command"
        await send_interaction_error(
            interaction,
            error,
            f"executing '/{cmd_name}'",
            logger,
            ephemeral=True,
        )
