from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.menu_manager import build_toast_embed, get_menu_manager
from src.adapters.discord_bot.ui_constants import (
    TOAST_DURATION_EXTENDED,
    TOAST_DURATION_STANDARD,
    ToastType,
)
from src.adapters.discord_bot.views.base_view import BaseModal
from src.adapters.discord_bot.views.forum_helpers import unarchive_thread_if_needed
from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
from src.domain.enums import TaskStatus
from src.domain.models import Task
from src.services.task_service import TaskService
from src.utils.date_parser import parse_natural_date

if TYPE_CHECKING:
    from src.services.auth_service import AuthService

logger = logging.getLogger("dgg_pm.views.task_modals")


def _extract_user_ids(text: str | None) -> list[int]:
    if not text:
        return []
    ids = re.findall(r"\d{4,20}", text)
    return [int(uid) for uid in set(ids)]


class TaskNoteModal(BaseModal):
    def __init__(
        self,
        task_id: UUID,
        short_id: str,
        task_service: TaskService,
        auth_service: AuthService | None = None,
    ):
        super().__init__(title=f"Add Progress Note: {short_id}")
        self.task_id = task_id
        self.short_id = short_id
        self.task_service = task_service
        self.auth_service = auth_service

        self.note_input = discord.ui.TextInput(
            label="Progress Update / Note",
            style=discord.TextStyle.paragraph,
            placeholder="Type your notes, blockers, or execution updates here...",
            required=True,
            max_length=1500,
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        note_text = self.note_input.value.strip()
        if not note_text:
            embed = build_toast_embed(
                "Note cannot be empty.",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            get_menu_manager().schedule_toast_dismissal(interaction, delay=TOAST_DURATION_EXTENDED)
            return

        try:
            task = await self.task_service.get_by_id(self.task_id)
            if self.auth_service and task:
                await self.auth_service.require_task_mutation(interaction.user, task)

            await self.task_service.add_note(
                task_id=self.task_id,
                actor_discord_id=interaction.user.id,
                note_text=note_text,
            )
            thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
            keep_archived = (task.status == TaskStatus.COMPLETED or task.is_archived) if task else False
            async with unarchive_thread_if_needed(thread, keep_archived=keep_archived):
                embed = build_toast_embed(
                    f"Note added to **{self.short_id}**.",
                    toast_type=ToastType.SUCCESS,
                    delay=TOAST_DURATION_STANDARD,
                )
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True,
                )
                get_menu_manager().schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"adding note to task '{self.short_id}'", logger, ephemeral=True
            )


class TaskEditModal(BaseModal):
    """Modal for editing task title, description, due date, and watchers."""

    def __init__(
        self,
        task: Task,
        task_service: TaskService,
        auth_service: AuthService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
    ):
        super().__init__(title=f"Edit Task: {task.short_id}")
        self.task_id = task.id
        self.short_id = task.short_id
        self.task_service = task_service
        self.auth_service = auth_service
        self.workspace = workspace

        self.title_input = discord.ui.TextInput(
            label="Task Title",
            default=task.title,
            required=True,
            max_length=100,
        )
        self.add_item(self.title_input)

        self.body_input = discord.ui.TextInput(
            label="Description / Body",
            style=discord.TextStyle.paragraph,
            default=task.body or "",
            placeholder="Detailed requirements or instructions...",
            required=False,
            max_length=1500,
        )
        self.add_item(self.body_input)

        due_default = ""
        if task.due_at:
            due_default = task.due_at.strftime("%Y-%m-%d %H:%M")

        self.due_input = discord.ui.TextInput(
            label="Due Date (e.g. 'tomorrow', 'clear')",
            default=due_default,
            placeholder="e.g. tomorrow, in 3 days, friday 5pm, 2026-04-15",
            required=False,
            max_length=60,
        )
        self.add_item(self.due_input)

        watchers_default = " ".join(f"<@{uid}>" for uid in task.watchers) if task.watchers else ""
        self.cc_input = discord.ui.TextInput(
            label="Watchers (CC) Mentions or IDs",
            default=watchers_default,
            placeholder="e.g. @alice @bob",
            required=False,
            max_length=500,
        )
        self.add_item(self.cc_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_input.value.strip()
        if not title:
            embed = build_toast_embed(
                "Task title cannot be empty.",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            get_menu_manager().schedule_toast_dismissal(interaction, delay=TOAST_DURATION_EXTENDED)
            return

        body_val = self.body_input.value.strip()
        clear_body = body_val == "" or body_val.lower() in ("clear", "none", "remove")
        body = None if clear_body else body_val

        due_val = self.due_input.value.strip()
        clear_due = due_val.lower() in ("clear", "none", "remove", "null", "unset")
        due_at = None if clear_due else parse_natural_date(due_val)

        watchers_val = self.cc_input.value.strip()
        watchers = _extract_user_ids(watchers_val) if watchers_val else []

        try:
            if self.auth_service:
                task = await self.task_service.get_by_id(self.task_id)
                if task:
                    await self.auth_service.require_task_mutation(interaction.user, task)

            updated_task = await self.task_service.update_details(
                task_id=self.task_id,
                actor_discord_id=interaction.user.id,
                title=title,
                body=body,
                clear_body=clear_body,
                due_at=due_at,
                clear_due_at=clear_due,
                watchers=watchers,
            )

            ws = self.workspace if isinstance(self.workspace, ITaskDiscordWorkspace) else None
            if not ws and "workspace" in vars(interaction.client):
                client_ws = getattr(interaction.client, "workspace", None)
                if isinstance(client_ws, ITaskDiscordWorkspace):
                    ws = client_ws
            if not ws:
                from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

                ws = DiscordTaskWorkspaceAdapter(
                    bot=interaction.client,
                    task_service=self.task_service,
                    project_service=getattr(self.task_service, "project_service", None),
                )

            if interaction.message:
                await ws.refresh_action_card(interaction, updated_task)
            else:
                embed = build_toast_embed(
                    f"Updated **[{updated_task.short_id}] {updated_task.title}**.",
                    toast_type=ToastType.SUCCESS,
                    delay=TOAST_DURATION_STANDARD,
                )
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True,
                )
                get_menu_manager().schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)

            sync_res = await ws.sync_workspace(
                updated_task, sync_title=True, sync_archive=False, sync_starter_card=True
            )
            title_deferred = bool(getattr(sync_res, "title_deferred", False))
            if not title_deferred and "sync_task_thread" in vars(interaction.client):
                legacy_res = await interaction.client.sync_task_thread(
                    updated_task, sync_title=True, sync_archive=False
                )
                title_deferred = bool(getattr(legacy_res, "title_deferred", False))

            if title_deferred:
                notice = build_toast_embed(
                    "Task title updated. (Note: Discord limits thread renames to 2 per 10 minutes; "
                    "thread title will reflect changes shortly).",
                    toast_type=ToastType.INFO,
                    delay=TOAST_DURATION_EXTENDED,
                )
                try:
                    toast = await interaction.followup.send(embed=notice, ephemeral=True, wait=True)
                    get_menu_manager().schedule_toast_dismissal(toast, delay=TOAST_DURATION_EXTENDED)
                except Exception:
                    pass
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"updating details for task '{self.short_id}'", logger, ephemeral=True
            )


class TaskQuickEditTitleModal(BaseModal):
    """Modal to edit title and description inside TaskQuickControlsView with staged preview."""

    def __init__(self, target_view: Any):
        super().__init__(title="Edit Task Details")
        self.target_view = target_view

        current_title = getattr(target_view, "staged_title", "")
        current_body = getattr(target_view, "staged_body", "") or ""

        self.title_input = discord.ui.TextInput(
            label="Task Title",
            default=current_title,
            required=True,
            max_length=100,
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label="Description / Body (Optional)",
            style=discord.TextStyle.paragraph,
            default=current_body,
            placeholder="Detailed requirements or instructions...",
            required=False,
            max_length=1500,
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_input.value.strip()
        if not title:
            embed = build_toast_embed(
                "Task title cannot be empty.",
                toast_type=ToastType.ERROR,
                delay=TOAST_DURATION_EXTENDED,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            get_menu_manager().schedule_toast_dismissal(interaction, delay=TOAST_DURATION_EXTENDED)
            return

        body = self.desc_input.value.strip() or None
        if hasattr(self.target_view, "update_text_content"):
            await self.target_view.update_text_content(interaction, title=title, body=body)
        else:
            self.target_view.staged_title = title
            self.target_view.staged_body = body
            self.target_view._rebuild_items()
            embed = self.target_view._build_embed()
            await interaction.response.edit_message(embed=embed, view=self.target_view)
