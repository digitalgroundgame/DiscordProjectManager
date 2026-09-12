"""Base view and modal classes providing universal error handling and interaction safety."""

from __future__ import annotations

import logging
from typing import Any

import discord

from src.domain.exceptions import StaleVersionError

logger = logging.getLogger("dgg_pm.adapters.discord_bot.views.base_view")


async def _handle_component_error(
    interaction: discord.Interaction,
    error: Exception,
    component_type: str,
    class_name: str,
    item: discord.ui.Item[Any] | None = None,
) -> None:
    """Standardized unhandled error dispatch across interactive views and modals."""
    user_id = getattr(getattr(interaction, "user", None), "id", None)
    guild_id = getattr(interaction, "guild_id", None)
    if not isinstance(guild_id, (int, str)) and getattr(interaction, "guild", None):
        guild_id = getattr(interaction.guild, "id", guild_id)

    channel_id = getattr(interaction, "channel_id", None)
    if not isinstance(channel_id, (int, str)) and getattr(interaction, "channel", None):
        channel_id = getattr(interaction.channel, "id", channel_id)
    custom_id = getattr(item, "custom_id", None)
    if not custom_id and hasattr(interaction, "data") and isinstance(interaction.data, dict):
        custom_id = interaction.data.get("custom_id")

    logger.error(
        "Unhandled exception in %s %s (item: %s, custom_id: %s) [user_id=%s, guild_id=%s, channel_id=%s]: %s",
        component_type,
        class_name,
        item.__class__.__name__ if item else None,
        custom_id,
        user_id,
        guild_id,
        channel_id,
        error,
        exc_info=error,
    )

    if isinstance(error, StaleVersionError):
        msg = "⚠️ This task was already modified by another user. Please refresh the card and try again."
    else:
        msg = "⚠️ An unexpected error occurred while processing this action. Please try again later."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as send_err:
        logger.exception("Failed to send interaction error response: %s", send_err)


class BaseView(discord.ui.View):
    """Base interactive View providing standardized error handling across component callbacks."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        """Standardized error handler intercepting unhandled exceptions during interactions."""
        await _handle_component_error(
            interaction=interaction,
            error=error,
            component_type="view",
            class_name=self.__class__.__name__,
            item=item,
        )


class BaseModal(discord.ui.Modal):
    """Base interactive Modal providing standardized error handling across modal submissions."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        """Standardized error handler intercepting unhandled exceptions during modal submissions."""
        await _handle_component_error(
            interaction=interaction,
            error=error,
            component_type="modal",
            class_name=self.__class__.__name__,
            item=None,
        )
