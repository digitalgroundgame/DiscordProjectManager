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
    https_url = urls["https_url"]
    todo_https_url = urls["todo_https_url"]

    embed = discord.Embed(
        title=f"Personal Calendar Feed • @{user.display_name}",
        description=(
            "Synchronize your assigned tasks and due dates directly with your personal calendar "
            "applications (Google Calendar, Apple Calendar, Microsoft Outlook, OmniFocus).\n\n"
            "### Universal Calendar Subscription URL (.ics)\n"
            "Copy and paste this URL into your calendar application:\n"
            f"```{https_url}```\n\n"
            "**How to Add:**\n"
            "• **Google Calendar**: Click **Other calendars (+)** ➔ **From URL** ➔ paste link\n"
            "• **Apple Calendar**: Click **File** ➔ **New Calendar Subscription...** ➔ paste link\n"
            "• **Microsoft Outlook**: Click **Add calendar** ➔ **Subscribe from web** ➔ paste link\n\n"
            "### To-Do & Task Apps (Apple Reminders, OmniFocus)\n"
            "For RFC 5545 `VTODO` format, use:\n"
            f"```{todo_https_url}```\n\n"
            "> **Security Notice**: This feed contains a private, cryptographically secure token. "
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

        # Direct link button to the universal .ics feed
        ics_button = discord.ui.Button(
            label="Open Calendar Feed (.ics)",
            style=discord.ButtonStyle.link,
            url=urls["https_url"],
        )
        self.add_item(ics_button)

        # Regenerate Link button
        regen_button = discord.ui.Button(
            label="Regenerate Link",
            style=discord.ButtonStyle.danger,
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
                "**Calendar link regenerated!** Any previously subscribed calendar feeds have been "
                "revoked and will no longer synchronize."
            )
            await interaction.response.edit_message(
                content=notice,
                embed=embed,
                view=self,
            )
        except Exception as e:
            await send_interaction_error(interaction, e, "regenerating calendar feed link", logger, ephemeral=True)
