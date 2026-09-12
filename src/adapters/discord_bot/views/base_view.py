"""Base view class providing universal error handling and interaction safety."""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("dgg_pm.adapters.discord_bot.views.base_view")


class BaseView(discord.ui.View):
    """Base interactive View providing standardized error handling across component callbacks."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        """Standardized error handler intercepting unhandled exceptions during interactions."""
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        guild_id = getattr(getattr(interaction, "guild", None), "id", None)
        channel_id = getattr(getattr(interaction, "channel", None), "id", None)
        custom_id = getattr(item, "custom_id", None)
        if not custom_id and hasattr(interaction, "data") and isinstance(interaction.data, dict):
            custom_id = interaction.data.get("custom_id")

        logger.error(
            "Unhandled exception in view %s (item: %s, custom_id: %s) [user_id=%s, guild_id=%s, channel_id=%s]: %s",
            self.__class__.__name__,
            item.__class__.__name__ if item else None,
            custom_id,
            user_id,
            guild_id,
            channel_id,
            error,
            exc_info=error,
        )

        msg = "⚠️ An unexpected error occurred while processing this action. Please try again later."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=10.0)
        except Exception as send_err:
            logger.exception("Failed to send interaction error response: %s", send_err)
