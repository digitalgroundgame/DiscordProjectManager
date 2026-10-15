from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseView

if TYPE_CHECKING:
    from src.services.calendar_service import CalendarService

logger = logging.getLogger("dgg_pm.views.calendar_feed")


def build_calendar_feed_embed(
    user: discord.User | discord.Member,
    urls: dict[str, str],
) -> discord.Embed:
    """Builds an informative embed displaying the user's private calendar subscription feed links."""
    webcal_url = urls["webcal_url"]
    https_url = urls["https_url"]
    todo_https_url = urls["todo_https_url"]

    embed = discord.Embed(
        title=f"🗓️ Personal Calendar Feed • @{user.display_name}",
        description=(
            "Synchronize your assigned tasks and due dates directly with your personal calendar "
            "applications (Google Calendar, Apple Calendar, Microsoft Outlook, OmniFocus).\n\n"
            "### 📲 One-Click Subscription (macOS, iOS, Windows)\n"
            f"Click the **Subscribe in Calendar** button below or open this link:\n"
            f"> [👉 **One-Click Calendar Subscription**]({webcal_url})\n\n"
            "### 🌐 Web Calendar Setup (Google Calendar & Outlook Web)\n"
            "To subscribe manually via Google Calendar or Outlook on the web:\n"
            "1. Copy this HTTPS calendar URL:\n"
            f"```{https_url}```\n"
            "2. In Google Calendar: click **Other calendars (+)** ➔ **From URL** and paste the link.\n"
            "3. In Outlook: click **Add calendar** ➔ **Subscribe from web** and paste the link.\n\n"
            "### 📝 Dedicated Task / To-Do Clients (Apple Reminders, OmniFocus)\n"
            f"For RFC 5545 `VTODO` format, use:\n"
            f"```{todo_https_url}```\n\n"
            "> 🔒 **Security Notice**: This feed contains a private, cryptographically secure token. "
            "Do not share this link. If compromised, you can revoke and regenerate it below at any time."
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text="dgg-pm • Live RFC 5545 iCalendar Subscription Feed")
    return embed


class CalendarFeedView(BaseView):
    """Interactive view displaying private calendar subscription feed links and token revocation."""

    def __init__(
        self,
        calendar_service: CalendarService,
        user_discord_id: int,
        token: str,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.calendar_service = calendar_service
        self.user_discord_id = user_discord_id
        self.token = token
        self._initial_interaction = initial_interaction

        self._refresh_components()

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.unregister_menu(self._initial_interaction)
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    def _refresh_components(self) -> None:
        self.clear_items()
        urls = self.calendar_service.build_feed_urls(self.token)

        # One-click subscription button (webcal://)
        sub_button = discord.ui.Button(
            label="Subscribe in Calendar",
            style=discord.ButtonStyle.link,
            url=urls["webcal_url"],
            emoji="📅",
        )
        self.add_item(sub_button)

        # Regenerate Link button
        regen_button = discord.ui.Button(
            label="Regenerate Link",
            style=discord.ButtonStyle.danger,
            emoji="🔄",
            custom_id="calendar_feed_regenerate",
        )
        regen_button.callback = self._on_regenerate_clicked
        self.add_item(regen_button)

    async def _on_regenerate_clicked(self, interaction: discord.Interaction) -> None:
        try:
            new_token = await self.calendar_service.regenerate_token(self.user_discord_id)
            self.token = new_token
            self._refresh_components()

            urls = self.calendar_service.build_feed_urls(new_token)
            embed = build_calendar_feed_embed(interaction.user, urls)

            notice = (
                "🔄 **Calendar link regenerated!** Any previously subscribed calendar feeds have been "
                "revoked and will no longer synchronize."
            )
            await interaction.response.edit_message(
                content=notice,
                embed=embed,
                view=self,
            )
        except Exception as e:
            await send_interaction_error(interaction, e, "regenerating calendar feed link", logger, ephemeral=True)
