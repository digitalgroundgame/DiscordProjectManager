"""Interactive confirmation view for starting or completing blocked tasks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.menu_manager import build_toast_embed, get_menu_manager
from src.adapters.discord_bot.ui_constants import (
    EPHEMERAL_MENU_TIMEOUT_SECONDS,
    TOAST_DURATION_EXTENDED,
    TOAST_DURATION_STANDARD,
    ToastType,
)
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


def _is_interaction_done(interaction: discord.Interaction) -> bool:
    if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
        res = interaction.response.is_done()
        if isinstance(res, bool):
            return res
    return False


async def _safe_edit_toast(
    interaction: discord.Interaction,
    text: str,
    toast_type: ToastType = ToastType.INFO,
    delay: float = TOAST_DURATION_STANDARD,
) -> None:
    embed = build_toast_embed(text, toast_type=toast_type, delay=delay)
    try:
        if not _is_interaction_done(interaction) and hasattr(interaction, "response"):
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        elif hasattr(interaction, "edit_original_response"):
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        get_menu_manager().schedule_toast_dismissal(interaction, delay=delay)
    except discord.errors.Forbidden as fe:
        if getattr(fe, "code", None) == 50083:
            logger.debug("Cannot edit interaction in archived thread: %s", fe)
        else:
            raise


async def _safe_send_toast(
    interaction: discord.Interaction,
    text: str,
    toast_type: ToastType = ToastType.ERROR,
    delay: float = TOAST_DURATION_EXTENDED,
) -> None:
    embed = build_toast_embed(text, toast_type=toast_type, delay=delay)
    try:
        if not _is_interaction_done(interaction) and hasattr(interaction, "response"):
            await interaction.response.send_message(embed=embed, ephemeral=True)
            get_menu_manager().schedule_toast_dismissal(interaction, delay=delay)
        elif hasattr(interaction, "followup"):
            toast = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
            get_menu_manager().schedule_toast_dismissal(toast, delay=delay)
    except discord.errors.Forbidden as fe:
        if getattr(fe, "code", None) == 50083:
            logger.debug("Cannot send interaction toast in archived thread: %s", fe)
        else:
            raise


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
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
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
            await _safe_send_toast(
                interaction,
                "Only the command invoker or an authorized manager/lead may interact with this confirmation card.",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
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

            msg = (
                f"**Dependency Warning Bypassed**\n"
                f"Status updated to **{self.target_status.value.upper()}** for **[{self.task.short_id}]** "
                f"despite unresolved blockers: {blocker_ids}."
            )
            await _safe_edit_toast(interaction, msg, toast_type=ToastType.SUCCESS, delay=TOAST_DURATION_STANDARD)
            self.stop()

            effective_workspace = self.workspace or getattr(self.bot, "workspace", None)
            if effective_workspace:
                try:
                    await effective_workspace.sync_workspace(updated_task)
                except Exception as ws_err:
                    logger.warning("Error syncing workspace on blocked bypass: %s", ws_err)
        except StaleVersionError:
            await _safe_edit_toast(
                interaction,
                f"Task **[{self.task.short_id}]** was modified by another user. Please retry.",
                toast_type=ToastType.WARNING,
                delay=TOAST_DURATION_EXTENDED,
            )
            self.stop()
        except Exception as e:
            logger.exception("Error confirming blocked task transition: %s", e)
            await _safe_send_toast(
                interaction,
                f"Failed to update status: {e}",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_cancel_blocked_transition",
    )
    async def cancel_transition(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        msg = f"Status update to **{self.target_status.value.upper()}** for **[{self.task.short_id}]** was cancelled."
        await _safe_edit_toast(interaction, msg, toast_type=ToastType.INFO, delay=TOAST_DURATION_STANDARD)
        self.stop()
