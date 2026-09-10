import logging
from uuid import UUID

import discord
from discord.ext import commands

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.views.hub_menu import PmHubView
from src.adapters.discord_bot.views.task_modals import TaskEditModal, TaskNoteModal
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ITaskDiscordWorkspace,
)
from src.config import settings
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService, TeamService
from src.services.task_service import StaleVersionError, TaskService
from src.services.user_service import UserService
from src.utils.date_parser import get_due_date_from_preset

logger = logging.getLogger("dgg_pm.bot")


class DggPmBot(commands.Bot):
    def __init__(
        self,
        task_service: TaskService,
        project_service: ProjectService,
        team_service: TeamService | None = None,
        squad_service: SquadService | None = None,
        user_service: UserService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        project_workspace: IProjectDiscordWorkspace | None = None,
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
        self.squad_service = squad_service or team_service
        self.team_service = self.squad_service
        self.user_service = user_service
        self.auth_service = AuthService(project_service, self.squad_service)
        self.workspace = workspace or DiscordTaskWorkspaceAdapter(
            bot=self,
            task_service=task_service,
            project_service=project_service,
            auth_service=self.auth_service,
        )
        self.project_workspace = project_workspace or DiscordProjectWorkspaceAdapter(
            bot=self,
            project_service=project_service,
            team_service=self.squad_service,
            task_service=task_service,
            user_service=user_service,
            auth_service=self.auth_service,
        )

    async def setup_hook(self) -> None:
        """Invoked when bot is starting up before login."""
        # Load unified /pm command group cog
        await self.add_cog(
            PmCog(
                self,
                self.project_service,
                self.team_service,
                self.task_service,
                self.auth_service,
                self.user_service,
                workspace=self.workspace,
                project_workspace=self.project_workspace,
            )
        )
        logger.info("Loaded Discord cog: PmCog (unified /pm namespace)")

        # Register persistent views
        self.add_view(
            PmHubView(
                project_service=self.project_service,
                team_service=self.team_service,
                task_service=self.task_service,
                user_service=self.user_service,
                auth_service=self.auth_service,
            )
        )

        # Sync application slash commands
        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=settings.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to development guild %s", settings.DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            logger.info("Synced slash commands globally.")

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
                name="tasks with /help-pm",
            )
        )

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Auto-prune team lead records if the corresponding Discord role is removed from the member."""
        if not self.team_service:
            return

        before_roles = {r.id for r in getattr(before, "roles", []) if hasattr(r, "id")}
        after_roles = {r.id for r in getattr(after, "roles", []) if hasattr(r, "id")}
        removed_roles = before_roles - after_roles

        if not removed_roles:
            return

        for role_id in removed_roles:
            try:
                team = await self.team_service.get_by_role_id(after.guild.id, role_id)
                if team:
                    if await self.team_service.is_team_lead(team.id, after.id):
                        await self.team_service.remove_team_lead(team.id, after.id)
                        logger.info(
                            "Auto-pruned team lead record for user %s from team '%s' due to Discord role removal",
                            after.id,
                            team.name,
                        )
            except Exception as e:
                logger.warning("Error auto-pruning team lead on member update for user %s: %s", after.id, e)

    async def on_member_remove(self, member: discord.Member) -> None:
        """Auto-prune team lead records across all teams in the guild if a member leaves the server."""
        if not self.team_service:
            return

        try:
            teams = await self.team_service.list_teams(member.guild.id)
            for team in teams:
                if await self.team_service.is_team_lead(team.id, member.id):
                    await self.team_service.remove_team_lead(team.id, member.id)
                    logger.info(
                        "Auto-pruned team lead record for user %s from team '%s' because member left the server",
                        member.id,
                        team.name,
                    )
        except Exception as e:
            logger.warning("Error auto-pruning team leads on member remove for user %s: %s", member.id, e)

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
    ) -> None:
        """Syncs the Discord thread state (applied tags, archive/unarchive, rename) for a task."""
        await self.workspace.sync_workspace(
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
        if interaction.response.is_done():
            return

        task = await self.task_service.get_by_id(task_id)
        if not task:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Task not found in database.", ephemeral=True)
            return

        # Prevent cross-guild access: only allow acting on tasks that belong to the
        # guild the interaction occurred in (if it occurred in a guild at all).
        if interaction.guild_id is not None and task.guild_id != interaction.guild_id:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ This task does not belong to this server.", ephemeral=True)
            return

        # Check authorization
        if not await self.auth_service.can_mutate_task(interaction.user, task):
            msg = (
                "❌ You do not have permission to modify this task. "
                "You must be the assignee, creator, a member of the project team, or a server manager."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return

        if action == "note":
            modal = TaskNoteModal(
                task_id=task_id,
                short_id=task.short_id,
                task_service=self.task_service,
                auth_service=self.auth_service,
            )
            await interaction.response.send_modal(modal)
            return

        if action == "edit":
            modal = TaskEditModal(
                task=task,
                task_service=self.task_service,
                auth_service=self.auth_service,
            )
            await interaction.response.send_modal(modal)
            return

        if action == "deps":
            try:
                sibling_tasks = []
                if task.project_id:
                    sibling_tasks, _ = await self.task_service.list_tasks(
                        guild_id=interaction.guild.id,
                        project_id=task.project_id,
                        include_archived=False,
                        limit=50,
                    )
                prerequisites, dependents = await self.task_service.get_task_dependencies(task_id)
                await self.workspace.render_task_controls(
                    interaction=interaction,
                    task=task,
                    panel="dependencies",
                    prerequisites=prerequisites,
                    dependents=dependents,
                    sibling_tasks=sibling_tasks,
                )
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "opening task dependencies", logger, ephemeral=True)
                return

        if action == "controls":
            try:
                await self.workspace.render_task_controls(
                    interaction=interaction,
                    task=task,
                    panel="quick_controls",
                )
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "opening task controls", logger, ephemeral=True)
                return

        if action == "unassign":
            try:
                updated_task = await self.task_service.update_assignee(
                    task_id=task_id,
                    new_assignee_id=None,
                    actor_discord_id=interaction.user.id,
                )
                await self.workspace.refresh_action_card(interaction, updated_task)
                await self.workspace.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "unassigning task", logger, ephemeral=True)
                return

        if action == "priority":
            values = interaction.data.get("values", [])
            if values:
                try:
                    new_priority = PriorityLevel(values[0])
                    updated_task = await self.task_service.update_priority(
                        task_id=task_id,
                        new_priority=new_priority,
                        actor_discord_id=interaction.user.id,
                    )
                    await self.workspace.refresh_action_card(interaction, updated_task)
                    await self.workspace.sync_workspace(updated_task)
                    return
                except Exception as e:
                    await send_interaction_error(interaction, e, "updating task priority", logger, ephemeral=True)
                    return

        if action == "assignee":
            values = interaction.data.get("values", [])
            try:
                new_assignee_id = int(values[0]) if values else None
                if new_assignee_id and self.auth_service:
                    await self.auth_service.require_task_assignee_eligibility(
                        interaction.guild, new_assignee_id, task.project_id
                    )
                updated_task = await self.task_service.update_assignee(
                    task_id=task_id,
                    new_assignee_id=new_assignee_id,
                    actor_discord_id=interaction.user.id,
                )
                await self.workspace.refresh_action_card(interaction, updated_task)
                await self.workspace.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "updating task assignee", logger, ephemeral=True)
                return

        if action == "due":
            values = interaction.data.get("values", [])
            if values:
                try:
                    due_at, is_clear = get_due_date_from_preset(values[0])
                    updated_task = await self.task_service.update_details(
                        task_id=task_id,
                        actor_discord_id=interaction.user.id,
                        due_at=due_at,
                        clear_due_at=is_clear,
                    )
                    await self.workspace.refresh_action_card(interaction, updated_task)
                    await self.workspace.sync_workspace(updated_task)
                    return
                except Exception as e:
                    await send_interaction_error(interaction, e, "updating task due date", logger, ephemeral=True)
                    return

        if action == "watchers":
            values = interaction.data.get("values", [])
            try:
                watchers = [int(uid) for uid in values] if values else []
                updated_task = await self.task_service.update_details(
                    task_id=task_id,
                    actor_discord_id=interaction.user.id,
                    watchers=watchers,
                )
                await self.workspace.refresh_action_card(interaction, updated_task)
                await self.workspace.sync_workspace(updated_task)
                return
            except Exception as e:
                await send_interaction_error(interaction, e, "updating task watchers", logger, ephemeral=True)
                return

        target_status = TaskStatus.IN_PROGRESS if action in ("start", "reopen") else TaskStatus.COMPLETED
        if action == "notstarted":
            target_status = TaskStatus.NOT_STARTED
        try:
            note_action = "reopened" if action == "reopen" else f"updated to {target_status.value}"
            updated_task = await self.task_service.update_status(
                task_id=task_id,
                new_status=target_status,
                expected_version=task.version,
                actor_discord_id=interaction.user.id,
                notes=f"Status {note_action} via button",
            )
            await self.workspace.refresh_action_card(interaction, updated_task)
            await self.workspace.sync_workspace(updated_task)

        except StaleVersionError:
            latest_task = await self.task_service.get_by_id(task_id)
            if latest_task:
                await self.workspace.refresh_action_card(interaction, latest_task)
                await self.workspace.sync_workspace(latest_task)
                await interaction.followup.send(
                    "⚠️ This task was already modified by another team member. The card has been refreshed.",
                    ephemeral=True,
                )
            else:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Task no longer exists.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Task no longer exists.", ephemeral=True)
        except Exception as e:
            await send_interaction_error(interaction, e, "processing task button action", logger, ephemeral=True)
