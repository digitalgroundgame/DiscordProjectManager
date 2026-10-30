from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS, TOAST_DURATION_STANDARD
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.squad_embeds import build_squad_menu_embed
from src.adapters.discord_bot.views.squad_member_assign_view import SquadMemberAssignView
from src.adapters.discord_bot.views.squad_roster_view import SquadRosterDetailView
from src.domain.enums import SquadRoleType
from src.domain.models import Squad
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.squad_menu")


class SquadSearchModal(BaseModal):
    """Modal to search for squads by name or keyword."""

    def __init__(self, callback_fn, current_query: str = ""):
        super().__init__(title="Search Squads")
        self.callback_fn = callback_fn
        self.query_input = discord.ui.TextInput(
            label="Squad Name or Keyword",
            placeholder="e.g. Core, Frontend, DevOps",
            default=current_query,
            required=False,
            max_length=50,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.callback_fn(interaction, self.query_input.value.strip())


class SquadMemberSearchModal(BaseModal):
    """Modal to search for a specific member by Discord User ID or mention within a squad roster."""

    def __init__(self, callback_fn, current_query: str = ""):
        super().__init__(title="Search Squad Members")
        self.callback_fn = callback_fn
        self.query_input = discord.ui.TextInput(
            label="Discord User ID or Keyword",
            placeholder="e.g. 123456789 or user ID fragment",
            default=current_query,
            required=False,
            max_length=50,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.callback_fn(interaction, self.query_input.value.strip())


class SquadCreateModalWithName(BaseModal):
    """Modal with pre-selected role where name defaults to role name."""

    def __init__(self, squad_service: SquadService, selected_role: discord.Role):
        super().__init__(title=f"Create Squad: @{selected_role.name[:25]}")
        self.squad_service = squad_service
        self.selected_role = selected_role

        self.name_input = discord.ui.TextInput(
            label="Squad Name (Defaults to Role Name)",
            default=selected_role.name,
            required=True,
            max_length=100,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            return

        name = self.name_input.value.strip() or self.selected_role.name
        try:
            squad = await self.squad_service.create_squad(
                guild_id=interaction.guild.id,
                name=name,
                discord_role_id=self.selected_role.id,
            )
            embed = discord.Embed(
                title=f"Squad Created: {squad.name}",
                description=f"Mapped to Discord role <@&{squad.discord_role_id}>",
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"Squad ID: {squad.id}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating squad '{name}'", logger, ephemeral=True)


class SquadCreateRoleSelectView(BaseView):
    """Native Discord RoleSelect picker for zero-typing squad creation."""

    def __init__(
        self,
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service

        self.role_select = discord.ui.RoleSelect(
            placeholder="Select Discord Server Role for Squad...",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.role_select.callback = self._on_role_selected
        self.add_item(self.role_select)

        self.back_btn = discord.ui.Button(
            label="Back to Squad Menu",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

    async def _on_role_selected(self, interaction: discord.Interaction) -> None:
        selected_role = self.role_select.values[0]
        modal = SquadCreateModalWithName(self.squad_service, selected_role)
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = SquadMenuView(
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_squad_menu_embed(view.can_create_squads, view.can_assign_members)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class SquadOverviewListView(BaseView):
    """Paginated overview of all squads configured in the server."""

    PAGE_SIZE = 10

    def __init__(
        self,
        squads: list[Squad],
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        current_page: int = 0,
        initial_interaction: discord.Interaction | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.all_squads = list(squads)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self.current_page = current_page

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_pages = max(1, math.ceil(len(self.all_squads) / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        # Row 0: Action buttons
        self.inspect_btn = discord.ui.Button(
            label="Inspect Detailed Roster",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.inspect_btn.callback = self._on_inspect_clicked
        self.add_item(self.inspect_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Squad Menu",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

        # Row 1: Pagination buttons if multiple pages
        if total_pages > 1:
            self.prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=1,
            )
            self.prev_btn.callback = self._on_prev_clicked
            self.add_item(self.prev_btn)

            self.next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=1,
            )
            self.next_btn.callback = self._on_next_clicked
            self.add_item(self.next_btn)

    async def build_embed(self) -> discord.Embed:
        total_pages = max(1, math.ceil(len(self.all_squads) / self.PAGE_SIZE))
        start_idx = self.current_page * self.PAGE_SIZE
        page_squads = self.all_squads[start_idx : start_idx + self.PAGE_SIZE]

        page_str = f" (Page {self.current_page + 1}/{total_pages})" if total_pages > 1 else ""
        embed = discord.Embed(
            title=f"Server Squads ({len(self.all_squads)}){page_str}",
            description="Select a squad below or click **Inspect Detailed Roster** to view leads and members:",
            color=discord.Color.blurple(),
        )
        for s in page_squads:
            members = await self.squad_service.list_members(s.id)
            leads_count = sum(1 for m in members if m.role_type == SquadRoleType.LEAD)
            members_count = sum(1 for m in members if m.role_type == SquadRoleType.MEMBER)
            embed.add_field(
                name=s.name[:256],
                value=f"• Role: <@&{s.discord_role_id}>\n• Leads: {leads_count} | Members: {members_count}"[:1024],
                inline=False,
            )
        return embed

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            embed = await self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self.all_squads) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = await self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_inspect_clicked(self, interaction: discord.Interaction) -> None:
        view = SquadRosterDetailView(
            squads=self.all_squads,
            squad_service=self.squad_service,
            project_service=self.project_service,
            task_service=self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Inspect Squad Roster",
            description="Select a squad from the dropdown below to inspect its verified members and leads:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = SquadMenuView(
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_squad_menu_embed(view.can_create_squads, view.can_assign_members)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class SquadMenuView(BaseView):
    """Control Center View for Contributor Squad Operations."""

    def __init__(
        self,
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        can_create_squads: bool | None = None,
        can_assign_members: bool | None = None,
        user_service: UserService | None = None,
        return_to: str = "dashboard",
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to

        effective_user = user or (initial_interaction.user if initial_interaction else None)
        if can_create_squads is not None:
            self.can_create_squads = can_create_squads
        elif effective_user is not None:
            self.can_create_squads = AuthService.is_server_manager(effective_user)
        else:
            self.can_create_squads = True

        if can_assign_members is not None:
            self.can_assign_members = can_assign_members
        elif effective_user is not None:
            self.can_assign_members = AuthService.is_server_manager(effective_user)
        else:
            self.can_assign_members = True

        # Row 0: Primary Management Controls
        if self.can_create_squads:
            self.create_squad_btn = discord.ui.Button(
                label="Create Squad",
                style=discord.ButtonStyle.primary,
                row=0,
            )
            self.create_squad_btn.callback = self._on_create_squad_clicked
            self.add_item(self.create_squad_btn)
        else:
            self.create_squad_btn = None

        if self.can_assign_members:
            self.assign_member_btn = discord.ui.Button(
                label="Assign Member",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.assign_member_btn.callback = self._on_assign_member_clicked
            self.add_item(self.assign_member_btn)
        else:
            self.assign_member_btn = None

        # Row 0: Squad Roster (Always visible)
        self.list_squads_btn = discord.ui.Button(
            label="Squad Roster",
            style=discord.ButtonStyle.secondary
            if (self.can_create_squads or self.can_assign_members)
            else discord.ButtonStyle.primary,
            row=0,
        )
        self.list_squads_btn.callback = self._on_list_squads_clicked
        self.roster_btn = self.list_squads_btn
        self.add_item(self.list_squads_btn)

        if self.project_service and self.task_service:
            self.hub_btn = discord.ui.Button(
                label="PM Main Menu",
                style=discord.ButtonStyle.secondary,
                row=1 if (self.can_create_squads or self.can_assign_members) else 0,
            )
            self.hub_btn.callback = self._on_hub_clicked
            self.add_item(self.hub_btn)
        else:
            self.hub_btn = None

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            projects = (
                await self.project_service.list_projects(interaction.guild.id, include_archived=False)
                if self.project_service
                else []
            )
            _, count = (
                await self.task_service.list_tasks(interaction.guild.id, limit=1) if self.task_service else ([], 0)
            )
            current_pref = (
                await self.user_service.get_preference(interaction.guild.id, interaction.user.id)
                if self.user_service
                else None
            )
            embed, view = DiscordViewRouter.build_admin_dashboard(
                guild=interaction.guild,
                user=interaction.user,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
                active_projects=projects,
                active_tasks_count=count,
                current_pref=current_pref,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            if self.project_service and self.task_service:
                embed, view = DiscordViewRouter.build_hub_view(
                    self.project_service, self.squad_service, self.task_service, self.user_service
                )
                await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_create_squad_clicked(self, interaction: discord.Interaction) -> None:
        view = SquadCreateRoleSelectView(
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Create New Squad",
            description="Select the Discord Server Role below to map to this squad container:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_assign_member_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        squads = await self.squad_service.list_squads(interaction.guild.id)
        if not squads:
            await interaction.response.send_message(
                "No squads found. Click **Create Squad** to set one up first!",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
            return
        view = SquadMemberAssignView(
            squads,
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Assign Squad Member",
            description="Select a squad, pick a Discord member, choose Member or Lead, and confirm:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_list_squads_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        squads = await self.squad_service.list_squads(interaction.guild.id)
        if not squads:
            await interaction.response.send_message("No squads configured in this server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
            return

        view = SquadOverviewListView(
            squads,
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = await view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)


__all__ = [
    "SquadCreateModalWithName",
    "SquadCreateRoleSelectView",
    "SquadMemberAssignView",
    "SquadMemberSearchModal",
    "SquadMenuView",
    "SquadOverviewListView",
    "SquadRosterDetailView",
    "SquadSearchModal",
    "build_squad_menu_embed",
]
