from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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
                if isinstance(target, (discord.WebhookMessage, discord.Message)):
                    res = target.delete()
                    if inspect.isawaitable(res):
                        await res
                elif isinstance(target, discord.Interaction):
                    res = target.delete_original_response()
                    if inspect.isawaitable(res):
                        await res
                elif "delete" in getattr(target, "__dict__", {}):
                    res = target.delete()
                    if inspect.isawaitable(res):
                        await res
                elif "delete_original_response" in getattr(target, "__dict__", {}):
                    res = target.delete_original_response()
                    if inspect.isawaitable(res):
                        await res
                elif hasattr(target, "delete_original_response") and callable(target.delete_original_response):
                    res = target.delete_original_response()
                    if inspect.isawaitable(res):
                        await res
                elif hasattr(target, "delete") and callable(target.delete):
                    res = target.delete()
                    if inspect.isawaitable(res):
                        await res
            except Exception as e:
                logger.debug("Could not auto-dismiss toast: %s", e)

        task = asyncio.create_task(_dismiss())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


_default_menu_manager: MenuSessionManager | None = None


def get_menu_manager() -> MenuSessionManager:
    """Return the active MenuSessionManager instance, creating it lazily if not set."""
    global _default_menu_manager
    if _default_menu_manager is None:
        _default_menu_manager = MenuSessionManager()
    return _default_menu_manager


def set_menu_manager(manager: MenuSessionManager | None) -> None:
    """Explicitly assign the active MenuSessionManager instance from container or test."""
    global _default_menu_manager
    _default_menu_manager = manager


def __getattr__(name: str) -> Any:
    """Provide lazy backwards compatibility for 'from src.adapters.discord_bot.menu_manager import menu_manager'."""
    if name == "menu_manager":
        return get_menu_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def attach_dismissal_notice(
    embed: discord.Embed,
    delay: float = 8.0,
    *,
    base_time: datetime | None = None,
    prefix: str = "⏱️ Auto-dismisses",
) -> discord.Embed:
    """Appends dynamic Discord relative countdown to embed description where Markdown is parsed and rendered."""
    base = base_time or datetime.now(UTC)
    target_ts = int((base + timedelta(seconds=delay)).timestamp())
    timer_str = f"<t:{target_ts}:R>"
    notice = f"{prefix} {timer_str}"

    if embed.description:
        embed.description = f"{embed.description}\n\n{notice}"
    else:
        embed.description = notice

    return embed


# Alias to maintain backwards compatibility
attach_dismissal_footer = attach_dismissal_notice


def format_toast_message(
    text: str,
    delay: float = 8.0,
    *,
    base_time: datetime | None = None,
    prefix: str = "⏱️ Auto-dismisses",
) -> str:
    """Appends dynamic Discord relative countdown to text-only toasts."""
    base = base_time or datetime.now(UTC)
    target_ts = int((base + timedelta(seconds=delay)).timestamp())
    timer_str = f"<t:{target_ts}:R>"
    return f"{text}\n\n*{prefix} {timer_str}*"
