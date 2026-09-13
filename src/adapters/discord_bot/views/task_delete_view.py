"""Interactive confirmation view for permanent task deletion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.models import Task

if TYPE_CHECKING:
    from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_delete_view")


def build_task_delete_confirm_embed(
    task: Task,
    *,
    prerequisites: list[Task] | None = None,
    dependents: list[Task] | None = None,
) -> discord.Embed:
    """Builds an ephemeral confirmation embed detailing the task and severed dependencies."""
    embed = discord.Embed(
        title=f"🗑️ Confirm Deletion: [{task.short_id}]",
        description=(
            f"Are you sure you want to permanently delete **[{task.short_id}]** `{task.title}`?\n\n"
            "⚠️ **Warning: This action is permanent and cannot be undone.**\n"
            "• The task record, history, and watchers will be permanently purged.\n"
            "• The associated Discord thread workspace will be deleted.\n"
            "• Scheduled outbox notifications/reminders will be cancelled.\n"
            "• All linked dependencies will be severed."
        ),
        color=discord.Color.red(),
    )

    embed.add_field(name="Status", value=f"`{task.status.value}`", inline=True)
    embed.add_field(name="Priority", value=f"`{task.priority.value}`", inline=True)

    assignee_str = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "*Unassigned*"
    embed.add_field(name="Assignee", value=assignee_str, inline=True)

    if prerequisites:
        prereq_str = "\n".join(f"• **[{p.short_id}]** {p.title}" for p in prerequisites[:5])
        if len(prerequisites) > 5:
            prereq_str += f"\n*...and {len(prerequisites) - 5} more*"
    else:
        prereq_str = "*None*"
    embed.add_field(name="Severed Prerequisites (This task depends on)", value=prereq_str, inline=False)

    if dependents:
        dep_str = "\n".join(f"• **[{d.short_id}]** {d.title}" for d in dependents[:5])
        if len(dependents) > 5:
            dep_str += f"\n*...and {len(dependents) - 5} more*"
    else:
        dep_str = "*None*"
    embed.add_field(name="Severed Dependents (Tasks depending on this)", value=dep_str, inline=False)

    return embed


class TaskDeleteConfirmView(BaseView):
    """Interactive confirmation view before performing permanent task deletion."""

    def __init__(
        self,
        task: Task,
        author_id: int,
        task_service: TaskService,
        *,
        auth_service: AuthService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        bot: discord.Client | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.task = task
        self.author_id = author_id
        self.task_service = task_service
        self.auth_service = auth_service
        self.workspace = workspace
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            if self.auth_service:
                can_del = await self.auth_service.can_delete_task(interaction.user, self.task)
                if can_del:
                    return True
            await interaction.response.send_message(
                "❌ Only the command invoker or an authorized manager/lead may interact with this deletion card.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm Delete",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="btn_confirm_task_delete",
    )
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            # Double check permission at moment of deletion
            if self.auth_service:
                await self.auth_service.require_task_deletion(interaction.user, self.task)

            deleted = await self.task_service.delete_task(
                task_id=self.task.id,
                actor_discord_id=interaction.user.id,
            )

            if not deleted:
                await interaction.response.edit_message(
                    content=f"⚠️ Task **[{self.task.short_id}]** was not found or has already been deleted.",
                    embed=None,
                    view=None,
                )
                self.stop()
                return

            effective_workspace = self.workspace or getattr(self.bot, "workspace", None)
            if effective_workspace:
                try:
                    await effective_workspace.delete_workspace(
                        deleted,
                        actor_discord_id=interaction.user.id,
                    )
                except Exception as ws_err:
                    logger.warning("Error cleaning workspace for deleted task %s: %s", self.task.short_id, ws_err)

            msg = f"🗑️ Task **[{self.task.short_id}]** (`{self.task.title}`) was permanently deleted."
            await interaction.response.edit_message(content=msg, embed=None, view=None)
            self.stop()
        except Exception as e:
            logger.exception("Failed during task delete confirmation: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Failed to delete task: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Failed to delete task: {e}", ephemeral=True)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        custom_id="btn_cancel_task_delete",
    )
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=f"Task deletion for **[{self.task.short_id}]** was cancelled.",
            embed=None,
            view=None,
        )
        self.stop()
