import asyncio
import logging
from typing import Any
from uuid import UUID

import discord
from discord.ext import commands

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.views.hub_menu import PmHubView
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ITaskDiscordWorkspace,
)
from src.config import settings
from src.domain.models import Task
from src.ports.repositories import IGuildLeadRoleRepository
from src.services.auth_service import AuthService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.bot")


class DggPmBot(commands.Bot):
    def __init__(
        self,
        task_service: TaskService | None = None,
        project_service: ProjectService | None = None,
        squad_service: SquadService | None = None,
        user_service: UserService | None = None,
        outbox_service: OutboxService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        project_workspace: IProjectDiscordWorkspace | None = None,
        guild_lead_role_repo: IGuildLeadRoleRepository | None = None,
        auth_service: AuthService | None = None,
    ):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        # Note: MessageContent intent is explicitly NOT required

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.task_service = task_service
        self.project_service = project_service
        self.squad_service = squad_service
        self.user_service = user_service
        self.outbox_service = outbox_service or (
            getattr(task_service, "outbox_service", None) if task_service else None
        )
        self.guild_lead_role_repo = guild_lead_role_repo
        self._background_tasks: set[asyncio.Task] = set()
        if auth_service is not None:
            self.auth_service = auth_service
        elif project_service and self.squad_service:
            self.auth_service = AuthService(
                project_service, self.squad_service, guild_lead_role_repo=guild_lead_role_repo
            )
        else:
            self.auth_service = None
        if workspace is not None:
            self.workspace = workspace
        elif task_service and project_service:
            self.workspace = DiscordTaskWorkspaceAdapter(
                bot=self,
                task_service=task_service,
                project_service=project_service,
                auth_service=self.auth_service,
            )
        else:
            self.workspace = None

        if project_workspace is not None:
            self.project_workspace = project_workspace
        elif project_service:
            self.project_workspace = DiscordProjectWorkspaceAdapter(
                bot=self,
                project_service=project_service,
                squad_service=self.squad_service,
                task_service=task_service,
                user_service=user_service,
                auth_service=self.auth_service,
            )
        else:
            self.project_workspace = None

        self.tree.on_error = self.on_tree_error

    async def on_tree_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        """Global fallback error handler for slash commands outside cogs or tree-level errors."""
        cmd_name = interaction.command.qualified_name if interaction.command else "command"
        await send_interaction_error(
            interaction,
            error,
            f"executing '/{cmd_name}'",
            logger,
            ephemeral=True,
        )

    async def setup_hook(self) -> None:
        """Invoked when bot is starting up before login."""
        # Load unified /pm command group cog
        await self.add_cog(
            PmCog(
                bot=self,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                auth_service=self.auth_service,
                user_service=self.user_service,
                outbox_service=self.outbox_service,
                workspace=self.workspace,
                project_workspace=self.project_workspace,
            )
        )
        logger.info("Loaded Discord cog: PmCog (unified /pm namespace)")

        # Register persistent views
        if self.project_service and self.squad_service and self.task_service and self.user_service:
            self.add_view(
                PmHubView(
                    project_service=self.project_service,
                    squad_service=self.squad_service,
                    task_service=self.task_service,
                    user_service=self.user_service,
                    auth_service=self.auth_service,
                )
            )

        # Sync application slash commands if configured
        if settings.SYNC_COMMANDS_ON_STARTUP:
            try:
                await self.sync_slash_commands(guild_id=settings.DISCORD_GUILD_ID)
            except discord.errors.Forbidden as exc:
                if exc.code == 50001:  # Missing Access
                    logger.critical(
                        "\n"
                        "================================================================================\n"
                        "DISCORD GATEWAY ERROR: Missing Access (403 Forbidden / Error Code 50001)\n"
                        "--------------------------------------------------------------------------------\n"
                        "The bot failed to sync slash commands because it lacks access to server %s.\n\n"
                        "Possible causes:\n"
                        "  1. The bot has NOT been invited to server ID %s yet.\n"
                        "  2. DISCORD_GUILD_ID in your .env does not match your server's actual ID.\n"
                        "  3. The bot was invited without the 'applications.commands' OAuth2 scope.\n"
                        "================================================================================",
                        settings.DISCORD_GUILD_ID or "(Global)",
                        settings.DISCORD_GUILD_ID or "(Global)",
                    )
                else:
                    logger.error("Forbidden (403) while syncing slash commands (code %s): %s", exc.code, exc)
                raise
        else:
            logger.info(
                "Skipping startup slash command synchronization (SYNC_COMMANDS_ON_STARTUP=False). "
                "Use '/pm admin sync' or 'python -m src.cli sync-commands' to sync on demand."
            )

    async def sync_slash_commands(self, guild_id: int | None = None) -> list[discord.app_commands.AppCommand]:
        """Synchronize slash commands globally or to a specific guild."""
        if guild_id:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d slash commands to development guild %s", len(synced), guild_id)
            return synced
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d slash commands globally.", len(synced))
            return synced

    def get_command_tree_summary(self) -> dict[str, Any]:
        """Inspect the command tree and extract a structured breakdown of commands and subcommands."""
        breakdown: dict[str, list[str]] = {}
        total = 0

        for cmd in self.tree.get_commands():
            if hasattr(cmd, "commands") and cmd.commands:
                for sub in cmd.commands:
                    if hasattr(sub, "commands") and sub.commands:
                        sub_list = [s2.name for s2 in sub.commands]
                        breakdown[f"/{cmd.name} {sub.name}"] = sub_list
                        total += len(sub_list)
                    else:
                        breakdown.setdefault(f"/{cmd.name}", []).append(sub.name)
                        total += 1
            else:
                breakdown.setdefault("root", []).append(f"/{cmd.name}")
                total += 1

        return {
            "total": total,
            "breakdown": breakdown,
        }

    def format_command_tree_summary(self) -> str:
        """Format command tree summary into a human-readable markdown message."""
        summary = self.get_command_tree_summary()
        total = summary["total"]
        lines = [f"**Command Breakdown ({total} executable commands):**"]
        for group, subs in summary["breakdown"].items():
            if group == "root":
                lines.append(f"• Root commands: {', '.join(f'`{s}`' for s in subs)}")
            else:
                lines.append(f"• `{group}` ({len(subs)}): {', '.join(f'`{s}`' for s in subs)}")
        return "\n".join(lines)

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (ID: %s) across %d guilds",
            self.user,
            self.user.id if self.user else "N/A",
            len(self.guilds),
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="tasks with /pm help",
            )
        )
        if self.outbox_service:
            task = asyncio.create_task(
                self._reconcile_failed_outbox_events(),
                name="Reconcile-Failed-Outbox",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _reconcile_failed_outbox_events(self, max_age_hours: float | None = None) -> int:
        """Reconcile and reclaim FAILED outbox events after gateway connection/reconnection."""
        try:
            if not self.outbox_service:
                return 0
            lookback = max_age_hours if max_age_hours is not None else settings.OUTBOX_RECLAIM_LOOKBACK_HOURS
            count = await self.outbox_service.reclaim_failed_events(max_age_hours=lookback)
            if count > 0:
                logger.info(
                    "Reconnected to Gateway: successfully reclaimed %d failed outbox event(s) "
                    "(lookback: %sh) for retry.",
                    count,
                    lookback,
                )
            return count
        except Exception as e:
            logger.warning("Failed to reclaim failed outbox events on gateway reconnect: %s", e)
            return 0

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Auto-prune squad lead records if the corresponding Discord role is removed from the member."""
        if not self.squad_service:
            return

        before_roles = {r.id for r in getattr(before, "roles", []) if hasattr(r, "id")}
        after_roles = {r.id for r in getattr(after, "roles", []) if hasattr(r, "id")}
        removed_roles = before_roles - after_roles

        if not removed_roles:
            return

        for role_id in removed_roles:
            try:
                squad = await self.squad_service.get_by_role_id(after.guild.id, role_id)
                if squad:
                    if await self.squad_service.is_squad_lead(squad.id, after.id):
                        await self.squad_service.remove_squad_lead(squad.id, after.id)
                        logger.info(
                            "Auto-pruned squad lead record for user %s from squad '%s' due to Discord role removal",
                            after.id,
                            squad.name,
                        )
            except Exception as e:
                logger.warning("Error auto-pruning squad lead on member update for user %s: %s", after.id, e)

    async def on_member_remove(self, member: discord.Member) -> None:
        """Auto-prune squad lead records across all squads in the guild if a member leaves the server."""
        if not self.squad_service:
            return

        try:
            squads = await self.squad_service.list_squads(member.guild.id)
            for squad in squads:
                if await self.squad_service.is_squad_lead(squad.id, member.id):
                    await self.squad_service.remove_squad_lead(squad.id, member.id)
                    logger.info(
                        "Auto-pruned squad lead record for user %s from squad '%s' because member left the server",
                        member.id,
                        squad.name,
                    )
        except Exception as e:
            logger.warning("Error auto-pruning squad leads on member remove for user %s: %s", member.id, e)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Global interaction dispatcher handling dynamic persistent task buttons across restarts."""
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id", "")
            if custom_id.startswith("task:"):
                # If interaction was already acknowledged (e.g. by in-memory View callback), skip
                if interaction.response.is_done():
                    return
                # Format: task:{action}:{task_uuid}
                parts = custom_id.split(":")
                if len(parts) == 3:
                    action, task_uuid_str = parts[1], parts[2]
                    try:
                        task_uuid = UUID(task_uuid_str)
                        await self._handle_dynamic_task_button(interaction, action, task_uuid)
                        return
                    except ValueError:
                        pass

    async def sync_root_task_message(self, task: Task) -> None:
        """Syncs the starter embed of a task thread or standalone message with the latest task state."""
        await self.workspace.sync_workspace(
            task,
            sync_starter_card=True,
            sync_tags=False,
            sync_archive=False,
        )

    async def sync_task_thread(
        self,
        task: Task,
        action: str | None = None,
        sync_title: bool = False,
        sync_archive: bool = True,
    ) -> Any:
        """Syncs the Discord thread state (applied tags, archive/unarchive, rename) for a task."""
        return await self.workspace.sync_workspace(
            task,
            sync_title=sync_title,
            sync_tags=True,
            sync_archive=sync_archive,
            sync_starter_card=False,
        )

    async def _update_interaction_view(
        self,
        interaction: discord.Interaction,
        updated_task: Task,
    ) -> None:
        """Updates the component view on interaction message without attaching a duplicate embed in threads."""
        await self.workspace.refresh_action_card(interaction, updated_task)

    async def _handle_dynamic_task_button(
        self,
        interaction: discord.Interaction,
        action: str,
        task_id: UUID,
    ) -> None:
        await self.workspace.handle_action(interaction, action, task_id)
