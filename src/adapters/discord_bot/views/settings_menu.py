from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.enums import NotificationPreference

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.settings_menu")


def build_settings_embed(user: discord.User | discord.Member, current_pref: NotificationPreference) -> discord.Embed:
    pref_labels = {
        NotificationPreference.DM: "Direct Messages (DM Only)",
        NotificationPreference.CHANNEL: "Channel Ping (In-Thread @mention)",
        NotificationPreference.BOTH: "Both (DM + Channel @mention)",
        NotificationPreference.NONE: "Silent / None (No direct pings)",
    }

    embed = discord.Embed(
        title=f"Notification Preferences • @{user.display_name}",
        description=(
            f"> **Current Active Mode**: `{pref_labels.get(current_pref, current_pref.value)}`\n\n"
            "Select how you want to receive task assignments, updates, and deadline reminders:\n\n"
            "• 🔒 **`DM Only` (Recommended for Privacy)**:\n"
            "  Delivered directly to your private direct messages with the bot. Permanent and only visible to you.\n\n"
            "• 🌐 **`Channel Ping` (Public Workspace)**:\n"
            "  An `@mention` posted inside the task thread. Visible to other members with channel access.\n"
            "  *(Note: Discord does not permit background bot notifications to be ephemeral in channels.)*\n\n"
            "• 🔔 **`Both` (DM + Thread Ping)**:\n"
            "  Sends a private DM to your inbox plus an in-thread mention for maximum visibility.\n\n"
            "• 🔕 **`Silent`**:\n"
            "  No direct pings or DMs (track tasks manually on the Control Hub or task list)."
        ),
        color=discord.Color.dark_theme(),
    )
    embed.set_footer(text="dgg-pm • Personal Notification Settings • Tip: Use DM Only for private alerts")
    return embed


class UserSettingsView(BaseView):
    """Interactive view allowing a member to toggle their personal notification preferences."""

    def __init__(
        self,
        user_service: UserService,
        current_pref: NotificationPreference,
        project_service: ProjectService | None = None,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        return_to: str = "dashboard",
    ):
        super().__init__(timeout=180)
        self.user_service = user_service
        self.current_pref = current_pref
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.return_to = return_to
        self._initial_interaction = initial_interaction

        self._refresh_buttons()

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

    def _refresh_buttons(self) -> None:
        self.clear_items()

        # Row 0: Preference Buttons (Selected is vibrant green success style)
        dm_btn = discord.ui.Button(
            label="DM Only",
            style=discord.ButtonStyle.success
            if self.current_pref == NotificationPreference.DM
            else discord.ButtonStyle.secondary,
            row=0,
        )
        dm_btn.callback = lambda i: self._on_pref_clicked(i, NotificationPreference.DM)
        self.add_item(dm_btn)

        chan_btn = discord.ui.Button(
            label="Channel Ping",
            style=discord.ButtonStyle.success
            if self.current_pref == NotificationPreference.CHANNEL
            else discord.ButtonStyle.secondary,
            row=0,
        )
        chan_btn.callback = lambda i: self._on_pref_clicked(i, NotificationPreference.CHANNEL)
        self.add_item(chan_btn)

        both_btn = discord.ui.Button(
            label="Both (DM + Ping)",
            style=discord.ButtonStyle.success
            if self.current_pref == NotificationPreference.BOTH
            else discord.ButtonStyle.secondary,
            row=0,
        )
        both_btn.callback = lambda i: self._on_pref_clicked(i, NotificationPreference.BOTH)
        self.add_item(both_btn)

        none_btn = discord.ui.Button(
            label="Silent",
            style=discord.ButtonStyle.success
            if self.current_pref == NotificationPreference.NONE
            else discord.ButtonStyle.secondary,
            row=0,
        )
        none_btn.callback = lambda i: self._on_pref_clicked(i, NotificationPreference.NONE)
        self.add_item(none_btn)

        # Row 1: Test & Back to Hub
        test_btn = discord.ui.Button(
            label="Test Notification",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        test_btn.callback = self._on_test_clicked
        self.add_item(test_btn)

        if self.project_service and self.squad_service and self.task_service:
            back_btn = discord.ui.Button(
                label="PM Main Menu",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            back_btn.callback = self._on_hub_clicked
            self.add_item(back_btn)

    async def _on_test_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        pref = self.current_pref
        if pref == NotificationPreference.NONE:
            await interaction.response.send_message(
                "**Your notification preference is set to Silent / None.**\nNo test notification was sent.",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        test_embed = discord.Embed(
            title="dgg-pm Notification Test",
            description=(
                f"Hello <@{interaction.user.id}>! This is a test notification from **{interaction.guild.name}**.\n\n"
                f"• **Delivery Mode**: `{pref.value.upper()}`\n"
                "• Your task assignments, status updates, and deadline reminders will arrive via this method."
            ),
            color=discord.Color.green(),
        )
        test_embed.set_footer(text="Control notifications via /pm settings")

        results: list[str] = []

        # Test DM delivery
        if pref in (NotificationPreference.DM, NotificationPreference.BOTH):
            try:
                await interaction.user.send(embed=test_embed)
                results.append(
                    "✅ **Direct Message (DM):** Successfully delivered to your DM inbox! Check your direct messages."
                )
            except discord.Forbidden:
                results.append(
                    "❌ **Direct Message (DM):** Delivery failed! DMs are blocked by your Discord privacy settings.\n"
                    "   *(Tip: Enable 'Allow direct messages' in Server Privacy Settings or switch to Channel Ping.)*"
                )
            except Exception as e:
                logger.exception("Unexpected error sending test DM: %s", e)
                results.append("⚠️ **Direct Message (DM):** Delivery failed due to an unexpected error.")

        # Test Channel delivery
        if pref in (NotificationPreference.CHANNEL, NotificationPreference.BOTH):
            results.append(
                f"📢 **Channel Ping:** Mentions (`<@{interaction.user.id}>`) will ping you inside task threads\n"
                "   *(Visible to thread members; background bot notifications cannot be ephemeral).*"
            )

        status_msg = "\n\n".join(results)
        await interaction.response.send_message(status_msg, ephemeral=True)
        from src.adapters.discord_bot.menu_manager import menu_manager

        menu_manager.schedule_toast_dismissal(interaction, delay=15.0)

    async def _on_pref_clicked(self, interaction: discord.Interaction, new_pref: NotificationPreference) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        try:
            await self.user_service.set_preference(
                guild_id=interaction.guild.id,
                user_discord_id=interaction.user.id,
                notify_preference=new_pref,
            )
            self.current_pref = new_pref
            self._refresh_buttons()
            embed = build_settings_embed(interaction.user, self.current_pref)
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            await send_interaction_error(interaction, e, "saving notification preference", logger, ephemeral=True)

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed

            projects = (
                await self.project_service.list_projects(interaction.guild.id, include_archived=False)
                if self.project_service
                else []
            )
            _, count = (
                await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
            )
            view = PmDashboardView(
                self.project_service,
                self.squad_service,
                self.task_service,
                self.user_service,
                initial_interaction=interaction,
            )
            embed = build_pm_dashboard_embed(
                guild=interaction.guild,
                user=interaction.user,
                active_projects=projects,
                active_tasks_count=count,
                current_pref=self.current_pref,
                is_server_manager=view.is_server_manager,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            from src.adapters.discord_bot.views.hub_menu import PmHubView, build_hub_welcome_embed

            if self.project_service and self.squad_service and self.task_service:
                view = PmHubView(
                    self.project_service,
                    self.squad_service,
                    self.task_service,
                    self.user_service,
                )
                embed = build_hub_welcome_embed()
                await interaction.response.edit_message(content=None, embed=embed, view=view)
