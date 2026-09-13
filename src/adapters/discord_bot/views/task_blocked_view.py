"""Interactive confirmation view for starting or completing blocked tasks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.enums import TaskStatus
from src.domain.exceptions import StaleVersionError
from src.domain.models import Task

if TYPE_CHECKING:
    from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_blocked_view")


def build_task_blocked_confirm_embed(
    task: Task,
    target_status: TaskStatus,
    incomplete_prereqs: list[Task],
) -> discord.Embed:
    """Builds an ephemeral warning embed detailing the blocked status transition and unresolved blockers."""
    embed = discord.Embed(
        title=f"⚠️ Unresolved Dependencies: [{task.short_id}]",
        description=(
            f"You are attempting to set **[{task.short_id}]** `{task.title}` to **{target_status.value.upper()}**, "
            f"but this task requires the following prerequisite task(s) to be completed first:\n\n"
        ),
        color=discord.Color.gold(),
    )

    blocker_lines = []
    for p in incomplete_prereqs[:10]:
        status_badge = f"`{p.status.value.replace('_', ' ').title()}`"
        assignee_str = f" • <@{p.assignee_discord_id}>" if p.assignee_discord_id else ""
        blocker_lines.append(f"• **[{p.short_id}]** {p.title} ({status_badge}{assignee_str})")

    if len(incomplete_prereqs) > 10:
        blocker_lines.append(f"*...and {len(incomplete_prereqs) - 10} more*")

    embed.add_field(
        name="Unresolved Blockers",
        value="\n".join(blocker_lines),
        inline=False,
    )

    embed.set_footer(text="Click 'Proceed Anyway' to bypass the dependency guard or 'Cancel' to abort.")
    return embed


class TaskBlockedConfirmView(BaseView):
    """Interactive confirmation view prompting users before starting or completing a blocked task."""

    def __init__(
        self,
        task: Task,
        target_status: TaskStatus,
        incomplete_prereqs: list[Task],
        author_id: int,
        task_service: TaskService,
        *,
        auth_service: AuthService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
        bot: discord.Client | None = None,
        notes: str | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.task = task
        self.target_status = target_status
        self.incomplete_prereqs = incomplete_prereqs
        self.author_id = author_id
        self.task_service = task_service
        self.auth_service = auth_service
        self.workspace = workspace
        self.bot = bot
        self.notes = notes

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            if self.auth_service:
                can_bypass = await self.auth_service.can_bypass_dependencies(interaction.user, self.task)
                if can_bypass:
                    return True
            await interaction.response.send_message(
                "❌ Only the command invoker or an authorized manager/lead may interact with this confirmation card.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Proceed Anyway",
        style=discord.ButtonStyle.danger,
        emoji="⚠️",
        custom_id="btn_confirm_blocked_transition",
    )
    async def confirm_proceed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            if self.auth_service:
                await self.auth_service.require_dependency_bypass(interaction.user, self.task)

            blocker_ids = ", ".join(f"[{p.short_id}]" for p in self.incomplete_prereqs)
            note = f"Status updated to {self.target_status.value} (bypassed unresolved blockers: {blocker_ids})"
            if self.notes:
                note += f" - {self.notes}"

            updated_task = await self.task_service.update_status(
                task_id=self.task.id,
                new_status=self.target_status,
                expected_version=self.task.version,
                actor_discord_id=interaction.user.id,
                notes=note,
            )

            effective_workspace = self.workspace or getattr(self.bot, "workspace", None)
            if effective_workspace:
                try:
                    await effective_workspace.refresh_action_card(interaction, updated_task)
                    await effective_workspace.sync_workspace(updated_task)
                except Exception as ws_err:
                    logger.warning("Error syncing workspace on blocked bypass: %s", ws_err)

            msg = (
                f"⚠️ **Dependency Warning Bypassed**\n"
                f"Status updated to **{self.target_status.value.upper()}** for **[{self.task.short_id}]** "
                f"despite unresolved blockers: {blocker_ids}."
            )
            await interaction.response.edit_message(content=msg, embed=None, view=None)
            self.stop()
        except StaleVersionError:
            await interaction.response.edit_message(
                content=f"⚠️ Task **[{self.task.short_id}]** was modified by another user. Please retry.",
                embed=None,
                view=None,
            )
            self.stop()
        except Exception as e:
            logger.exception("Error confirming blocked task transition: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Failed to update status: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Failed to update status: {e}", ephemeral=True)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        custom_id="btn_cancel_blocked_transition",
    )
    async def cancel_transition(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        msg = f"Status update to **{self.target_status.value.upper()}** for **[{self.task.short_id}]** was cancelled."
        await interaction.response.edit_message(
            content=msg,
            embed=None,
            view=None,
        )
        self.stop()
