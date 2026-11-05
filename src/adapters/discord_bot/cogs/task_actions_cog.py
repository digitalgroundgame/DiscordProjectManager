from __future__ import annotations

import logging

import discord
from discord import app_commands

from src.adapters.discord_bot.cogs.autocomplete import AutocompleteMixin
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.menu_manager import send_toast
from src.adapters.discord_bot.ui_constants import ToastType
from src.adapters.discord_bot.views.task_delete_view import (
    TaskDeleteConfirmView,
    build_task_delete_confirm_embed,
)

logger = logging.getLogger("dgg_pm.cogs.task_actions")

task_autocomplete = AutocompleteMixin.task_autocomplete


class TaskActionsMixin(AutocompleteMixin):
    """Task lifecycle actions: archive, unarchive, delete, watchers, and dependencies."""

    task_group = app_commands.Group(name="task", description="Task operations, assignments, and status")

    @task_group.command(name="archive", description="Archive a completed or obsolete task.")
    @app_commands.describe(task="Short ID of the task to archive (omit if inside thread)")
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_archive(self, interaction: discord.Interaction, task: str | None = None) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
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
            await send_toast(
                interaction,
                f"Archived task **[{updated.short_id}]** (`{updated.title}`).",
                toast_type=ToastType.SUCCESS,
            )
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"archiving task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="unarchive", description="Restore an archived task.")
    @app_commands.describe(task="Short ID of the task to restore (omit if inside thread)")
    async def task_unarchive(self, interaction: discord.Interaction, task: str | None = None) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
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
            await send_toast(
                interaction,
                f"Restored task **[{updated.short_id}]** (`{updated.title}`).",
                toast_type=ToastType.SUCCESS,
            )
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"restoring task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="delete", description="Permanently delete a task with interactive confirmation.")
    @app_commands.describe(task="Short ID of the task to delete (omit if inside thread)")
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_delete(self, interaction: discord.Interaction, task: str | None = None) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
                return

            await self.auth_service.require_task_deletion(interaction.user, task_entity)

            prereqs, dependents = await self.task_service.get_task_dependencies(task_entity.id)
            embed = build_task_delete_confirm_embed(
                task_entity,
                prerequisites=prereqs,
                dependents=dependents,
            )
            view = TaskDeleteConfirmView(
                task=task_entity,
                author_id=interaction.user.id,
                task_service=self.task_service,
                auth_service=self.auth_service,
                workspace=self.workspace,
                bot=self.bot,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"deleting task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="watchers", description="Manage watcher subscribers on a task.")
    @app_commands.describe(
        action="Action to perform",
        task="Short ID of the task (omit if inside thread)",
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
        action: str,
        task: str | None = None,
        member: discord.Member | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
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

            await send_toast(
                interaction,
                f"Updated watchers for **[{updated_task.short_id}]** ({len(updated_task.watchers)} total).",
                toast_type=ToastType.SUCCESS,
            )
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"updating watchers for task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(
        name="depend",
        description="Add a prerequisite dependency: this task requires another task to finish first.",
    )
    @app_commands.describe(
        depends_on="The prerequisite task that must finish first (e.g. INF-1)",
        task="The dependent task that is blocked (omit if inside thread)",
    )
    @app_commands.autocomplete(task=task_autocomplete, depends_on=task_autocomplete)
    async def task_depend(
        self,
        interaction: discord.Interaction,
        depends_on: str,
        task: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            target_task = await self._resolve_task_context(interaction, task)
            if not target_task:
                return

            await self.auth_service.require_task_mutation(interaction.user, target_task)

            await self.task_service.add_dependency(
                guild_id=interaction.guild.id,
                task_short_id=target_task.short_id,
                depends_on_short_id=depends_on,
                actor_discord_id=interaction.user.id,
            )
            await send_toast(
                interaction,
                f"Linked dependency: **`[{target_task.short_id}]`** "
                f"now depends on **`[{depends_on}]`** finishing first.",
                toast_type=ToastType.SUCCESS,
            )
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"linking dependency for '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(
        name="undepend",
        description="Remove a prerequisite dependency from a task.",
    )
    @app_commands.describe(
        depends_on="The prerequisite task to unblock/unlink (e.g. INF-1)",
        task="The dependent task (omit if inside thread)",
    )
    @app_commands.autocomplete(task=task_autocomplete, depends_on=task_autocomplete)
    async def task_undepend(
        self,
        interaction: discord.Interaction,
        depends_on: str,
        task: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            target_task = await self._resolve_task_context(interaction, task)
            if not target_task:
                return

            await self.auth_service.require_task_mutation(interaction.user, target_task)

            removed = await self.task_service.remove_dependency(
                guild_id=interaction.guild.id,
                task_short_id=target_task.short_id,
                depends_on_short_id=depends_on,
                actor_discord_id=interaction.user.id,
            )
            if removed:
                await send_toast(
                    interaction,
                    f"Unlinked dependency: **`[{target_task.short_id}]`** no longer depends on **`[{depends_on}]`**.",
                    toast_type=ToastType.SUCCESS,
                )
            else:
                await send_toast(
                    interaction,
                    f"**`[{target_task.short_id}]`** did not depend on **`[{depends_on}]`**.",
                    toast_type=ToastType.INFO,
                )
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"unlinking dependency for '{task or 'current thread'}'", logger, ephemeral=True
            )
