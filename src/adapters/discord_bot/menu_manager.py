from __future__ import annotations

import asyncio
import inspect
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    pass

from src.adapters.discord_bot.ui_constants import (
    EPHEMERAL_MENU_TIMEOUT_SECONDS,
    TOAST_COLOR_MAP,
    TOAST_DISMISS_PREFIX,
    TOAST_DURATION_EXTENDED,
    TOAST_DURATION_STANDARD,
    ToastType,
)

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
        delay: float = TOAST_DURATION_STANDARD,
    ) -> None:
        """Schedules auto-dismissal of an ephemeral toast message after delay (default: TOAST_DURATION_STANDARD)."""
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


# Regex matching leading status emojis to prevent duplicate emoji/color signaling
_LEADING_STATUS_EMOJI_RE = re.compile(
    r"^[\s\u200b]*[✅❌⚠️ℹ️🗑️⚙️🔄🟡🟢🔴⏳🚀⏱️\ufe0e\ufe0f]+\s*",
    re.UNICODE,
)


def strip_status_emojis(text: str) -> str:
    """Strips leading status emojis from toast messages to ensure clean presentation in color-coded embeds."""
    return _LEADING_STATUS_EMOJI_RE.sub("", text).strip()


def build_toast_embed(
    message: str,
    toast_type: ToastType = ToastType.INFO,
    delay: float = TOAST_DURATION_STANDARD,
    *,
    base_time: datetime | None = None,
    prefix: str = TOAST_DISMISS_PREFIX,
    title: str | None = None,
) -> discord.Embed:
    """Builds a compact, color-coded ephemeral toast embed with auto-dismiss countdown.

    Eliminates emoji clutter in favor of standard status color bars:
    - SUCCESS: Green (Operation succeeded)
    - ERROR: Red (Validation error / exception)
    - WARNING: Gold (Cautionary notice / bypassed condition)
    - INFO: Blurple (Informational status)
    """
    clean_message = strip_status_emojis(message)
    color = TOAST_COLOR_MAP.get(toast_type, discord.Color.blurple())
    embed = discord.Embed(description=clean_message, color=color)
    if title:
        embed.title = strip_status_emojis(title)
    return attach_dismissal_notice(embed, delay=delay, base_time=base_time, prefix=prefix)


def attach_dismissal_notice(
    embed: discord.Embed,
    delay: float = TOAST_DURATION_STANDARD,
    *,
    base_time: datetime | None = None,
    prefix: str = TOAST_DISMISS_PREFIX,
) -> discord.Embed:
    """Appends dynamic Discord relative countdown to embed description where Markdown is parsed and rendered."""
    base = base_time or datetime.now(UTC)
    target_ts = int((base + timedelta(seconds=delay)).timestamp())
    timer_str = f"<t:{target_ts}:R>"
    notice = f"*{prefix} {timer_str}*"

    if embed.description:
        embed.description = f"{embed.description}\n\n{notice}"
    else:
        embed.description = notice

    return embed


# Alias to maintain backwards compatibility
attach_dismissal_footer = attach_dismissal_notice


def format_toast_message(
    text: str,
    delay: float = TOAST_DURATION_STANDARD,
    *,
    base_time: datetime | None = None,
    prefix: str = TOAST_DISMISS_PREFIX,
) -> str:
    """Appends dynamic Discord relative countdown to text-only toasts."""
    base = base_time or datetime.now(UTC)
    target_ts = int((base + timedelta(seconds=delay)).timestamp())
    timer_str = f"<t:{target_ts}:R>"
    return f"{text}\n\n*{prefix} {timer_str}*"


__all__ = [
    "EPHEMERAL_MENU_TIMEOUT_SECONDS",
    "TOAST_DISMISS_PREFIX",
    "TOAST_DURATION_EXTENDED",
    "TOAST_DURATION_STANDARD",
    "MenuSessionManager",
    "ToastType",
    "attach_dismissal_footer",
    "attach_dismissal_notice",
    "build_toast_embed",
    "format_toast_message",
    "get_menu_manager",
    "set_menu_manager",
    "strip_status_emojis",
]
