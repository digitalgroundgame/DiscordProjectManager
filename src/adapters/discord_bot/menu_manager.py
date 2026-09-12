from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import discord

if TYPE_CHECKING:
    pass

logger = logging.getLogger("dgg_pm.menu_manager")


class MenuSessionManager:
    """Manages active ephemeral menu sessions and auto-dismissal of ephemeral toasts."""

    def __init__(self) -> None:
        self._active_menus: dict[tuple[int, int], discord.Interaction] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def register_menu(self, interaction: discord.Interaction) -> None:
        """Registers an active menu for a user and automatically dismisses any previous menu they opened."""
        if not interaction.guild_id or not interaction.user:
            return

        key = (interaction.guild_id, interaction.user.id)
        prev_interaction = self._active_menus.get(key)
        if prev_interaction and prev_interaction != interaction:
            try:
                await prev_interaction.delete_original_response()
            except Exception as e:
                logger.debug("Could not dismiss previous menu: %s", e)

        self._active_menus[key] = interaction

    def unregister_menu(self, interaction: discord.Interaction) -> None:
        """Unregisters an active menu."""
        if not interaction.guild_id or not interaction.user:
            return
        key = (interaction.guild_id, interaction.user.id)
        if self._active_menus.get(key) == interaction:
            self._active_menus.pop(key, None)

    def schedule_toast_dismissal(
        self,
        target: discord.Interaction | discord.WebhookMessage | discord.Message | Any,
        delay: float = 6.0,
    ) -> None:
        """Schedules auto-dismissal of an ephemeral toast message after delay (default: 6s)."""
        if target is None:
            return

        async def _dismiss() -> None:
            await asyncio.sleep(delay)
            try:
                has_delete = hasattr(target, "delete") and callable(target.delete)
                has_delete_orig = hasattr(target, "delete_original_response") and callable(
                    target.delete_original_response
                )

                # If target only has delete() (e.g. WebhookMessage / Message)
                # or if target.delete is an AsyncMock while delete_original_response is not
                is_mock_delete = isinstance(getattr(target, "delete", None), AsyncMock)
                is_mock_orig = isinstance(getattr(target, "delete_original_response", None), AsyncMock)

                if has_delete and (
                    not has_delete_orig
                    or (discord and isinstance(target, (discord.WebhookMessage, discord.Message)))
                    or (is_mock_delete and not is_mock_orig)
                ):
                    res = target.delete()
                    if inspect.isawaitable(res):
                        await res
                elif has_delete_orig:
                    res = target.delete_original_response()
                    if inspect.isawaitable(res):
                        await res
            except Exception as e:
                logger.debug("Could not auto-dismiss toast: %s", e)

        task = asyncio.create_task(_dismiss())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


menu_manager = MenuSessionManager()
