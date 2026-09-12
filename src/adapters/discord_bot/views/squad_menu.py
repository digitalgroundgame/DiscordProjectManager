from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
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

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
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
    ):
        super().__init__(timeout=180)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction

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

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

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


class SquadMemberAssignView(BaseView):
    """View to select a squad, pick a user, choose a role type, and confirm assignment."""

    PAGE_SIZE = 25

    def __init__(
        self,
        squads: list[Squad],
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        current_page: int = 0,
        query: str = "",
    ):
        super().__init__(timeout=180)
        self.all_squads = list(squads)
        self.squads = {s.id: s for s in self.all_squads}
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction
        self.current_page = current_page
        self.query = query

        self.selected_squad_id: UUID = squads[0].id if squads else UUID(int=0)
        self.selected_user: discord.Member | discord.User | None = None
        self.selected_role_type_str: str = "member"

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        if self.query:
            q = self.query.lower()
            filtered_squads = [s for s in self.all_squads if q in s.name.lower()]
        else:
            filtered_squads = self.all_squads

        total_pages = max(1, math.ceil(len(filtered_squads) / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.PAGE_SIZE
        page_squads = filtered_squads[start_idx : start_idx + self.PAGE_SIZE]

        if not self.selected_squad_id and page_squads:
            self.selected_squad_id = page_squads[0].id
        elif page_squads and not any(s.id == self.selected_squad_id for s in page_squads):
            self.selected_squad_id = page_squads[0].id

        # Row 0: Select Squad Dropdown
        squad_options = [
            discord.SelectOption(
                label=s.name[:100],
                value=str(s.id),
                description=f"Role: {s.discord_role_id}"[:50],
                default=(s.id == self.selected_squad_id),
            )
            for s in page_squads
        ]
        placeholder = (
            f"Select Target Squad (Page {self.current_page + 1}/{total_pages})..."
            if total_pages > 1
            else "Select Target Squad..."
        )
        self.squad_select = discord.ui.Select(
            placeholder=placeholder,
            options=squad_options or [discord.SelectOption(label="No Squads Found", value="none")],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.squad_select.callback = self._on_squad_changed
        self.add_item(self.squad_select)

        # Row 1: Select User / Member
        self.user_select = discord.ui.UserSelect(
            placeholder="Select Discord Member...",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.user_select.callback = self._on_user_selected
        self.add_item(self.user_select)

        # Row 2: Select Role Type (Member, Lead, Remove Lead)
        role_type_options = [
            discord.SelectOption(
                label="Squad Member (Regular)",
                value="member",
                description="Assign member to squad roster",
                default=(self.selected_role_type_str == "member"),
            ),
            discord.SelectOption(
                label="Squad Lead (Elevated)",
                value="lead",
                description="Designate lead with squad management permissions",
                default=(self.selected_role_type_str == "lead"),
            ),
            discord.SelectOption(
                label="Remove Lead Status",
                value="remove_lead",
                description="Demote from Squad Lead back to regular member",
                default=(self.selected_role_type_str == "remove_lead"),
            ),
        ]
        self.role_select = discord.ui.Select(
            placeholder="Select Role Type...",
            options=role_type_options,
            min_values=1,
            max_values=1,
            row=2,
        )
        self.role_select.callback = self._on_role_type_selected
        self.add_item(self.role_select)

        # Row 3: Action Buttons
        self.confirm_btn = discord.ui.Button(
            label="Confirm Assignment",
            style=discord.ButtonStyle.primary,
            row=3,
        )
        self.confirm_btn.callback = self._on_confirm_clicked
        self.add_item(self.confirm_btn)

        if total_pages > 1 or self.query:
            self.search_btn = discord.ui.Button(
                label="Search Squads",
                style=discord.ButtonStyle.secondary,
                row=3,
            )
            self.search_btn.callback = self._on_search_squads_clicked
            self.add_item(self.search_btn)

            if self.query:
                self.clear_search_btn = discord.ui.Button(
                    label="Clear Filter",
                    style=discord.ButtonStyle.secondary,
                    row=3,
                )
                self.clear_search_btn.callback = self._on_clear_search_clicked
                self.add_item(self.clear_search_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

        # Row 4: Pagination buttons (only when multiple pages)
        if total_pages > 1:
            self.prev_btn = discord.ui.Button(
                label="◀ Previous Squads",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=4,
            )
            self.prev_btn.callback = self._on_prev_clicked
            self.add_item(self.prev_btn)

            self.next_btn = discord.ui.Button(
                label="Next Squads ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=4,
            )
            self.next_btn.callback = self._on_next_clicked
            self.add_item(self.next_btn)

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    async def _on_squad_changed(self, interaction: discord.Interaction) -> None:
        if self.squad_select.values and self.squad_select.values[0] != "none":
            self.selected_squad_id = UUID(self.squad_select.values[0])
            for opt in self.squad_select.options:
                opt.default = opt.value == str(self.selected_squad_id)
        await interaction.response.defer()

    async def _on_user_selected(self, interaction: discord.Interaction) -> None:
        self.selected_user = self.user_select.values[0]
        await interaction.response.defer()

    async def _on_role_type_selected(self, interaction: discord.Interaction) -> None:
        val = self.role_select.values[0]
        self.selected_role_type_str = val
        for opt in self.role_select.options:
            opt.default = opt.value == val
        await interaction.response.defer()

    _on_role_selected = _on_role_type_selected

    async def _on_search_squads_clicked(self, interaction: discord.Interaction) -> None:
        modal = SquadSearchModal(self._apply_squad_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_squad_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._rebuild_items()
        filter_note = f" (Filter: `{self.query}`)" if self.query else ""
        embed = discord.Embed(
            title=f"Assign Squad Member{filter_note}",
            description="Select a squad, pick a Discord member, choose Member or Lead, and confirm:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_search_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._rebuild_items()
        embed = discord.Embed(
            title="Assign Squad Member",
            description="Select a squad, pick a Discord member, choose Member or Lead, and confirm:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        filtered_squads = [s for s in self.all_squads if not self.query or self.query.lower() in s.name.lower()]
        total_pages = max(1, math.ceil(len(filtered_squads) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        view = SquadMenuView(
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_squad_menu_embed(view.can_create_squads, view.can_assign_members)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        if not self.selected_squad_id:
            await interaction.response.send_message("❌ Please select a squad first.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        if not self.selected_user:
            if self.user_select.values:
                self.selected_user = self.user_select.values[0]
            else:
                await interaction.response.send_message("❌ Please select a Discord member first.", ephemeral=True)
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
                return

        action_type = self.selected_role_type_str
        squad = self.squads.get(self.selected_squad_id)

        if not squad:
            await interaction.response.send_message("❌ Squad not found.", ephemeral=True)
            return

        # Check authorization
        if not AuthService.is_server_manager(interaction.user):
            is_lead = await self.squad_service.is_squad_lead(self.selected_squad_id, interaction.user.id)
            if not is_lead:
                await interaction.response.send_message(
                    "❌ You do not have permission to manage this squad's roster. "
                    "You must be a Squad Lead or a server manager.",
                    ephemeral=True,
                )
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
                return

        # Validate that the user actually has the squad's Discord role for add/verify
        user = self.selected_user
        if action_type != "remove_lead" and interaction.guild:
            member = interaction.guild.get_member(user.id) if hasattr(interaction.guild, "get_member") else user
            member = member or user
            if hasattr(member, "roles"):
                has_role = any(r.id == squad.discord_role_id for r in member.roles)
                if not has_role:
                    await interaction.response.send_message(
                        f"❌ <@{user.id}> is not part of squad **{squad.name}** "
                        f"(missing role <@&{squad.discord_role_id}>).\n"
                        f"Please assign them the Discord role first.",
                        ephemeral=True,
                    )
                    from src.adapters.discord_bot.menu_manager import menu_manager

                    menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
                    return

        try:
            if action_type == "remove_lead":
                await self.squad_service.remove_squad_lead(self.selected_squad_id, self.selected_user.id)
                success_msg = f"Removed Squad Lead status from <@{self.selected_user.id}> for **{squad.name}**."
            elif action_type == "lead":
                await self.squad_service.add_squad_lead(self.selected_squad_id, self.selected_user.id)
                success_msg = f"Designated <@{self.selected_user.id}> as **Squad Lead** for **{squad.name}**."
            else:
                await self.squad_service.assign_member(
                    squad_id=self.selected_squad_id,
                    user_discord_id=self.selected_user.id,
                    role_type=SquadRoleType.MEMBER,
                )
                success_msg = f"Verified <@{self.selected_user.id}> as **Squad Member** for **{squad.name}**."

            view = SquadMenuView(
                self.squad_service,
                self.project_service,
                self.task_service,
                initial_interaction=interaction,
            )
            embed = build_squad_menu_embed(view.can_create_squads, view.can_assign_members)
            embed.description = f"{success_msg}\n\n" + (embed.description or "")
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"updating squad settings for '{squad.name}'", logger, ephemeral=True
            )


class SquadRosterDetailView(BaseView):
    """Interactive view allowing inspection of squad members for any squad."""

    SQUAD_PAGE_SIZE = 25
    MEMBER_PAGE_SIZE = 20

    def __init__(
        self,
        squads: list[Squad],
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        squad_page: int = 0,
        squad_query: str = "",
        selected_squad_id: UUID | None = None,
        member_page: int = 0,
        member_query: str = "",
    ):
        super().__init__(timeout=180)
        self.all_squads = list(squads)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self.squads = {s.id: s for s in self.all_squads}
        self._initial_interaction = initial_interaction

        self.squad_page = squad_page
        self.squad_query = squad_query
        self.selected_squad_id = selected_squad_id
        self.member_page = member_page
        self.member_query = member_query

        self._cached_members: list[Any] = []
        self._rebuild_items()

    def _get_filtered_squads(self) -> list[Squad]:
        if not self.squad_query:
            return self.all_squads
        q = self.squad_query.lower()
        return [s for s in self.all_squads if q in s.name.lower()]

    def _build_roster_embed(self, squad: Squad, members: list[Any]) -> discord.Embed:
        leads = [m for m in members if m.role_type == SquadRoleType.LEAD]
        regular = [m for m in members if m.role_type == SquadRoleType.MEMBER]

        if self.member_query:
            q = self.member_query.lower()
            filtered_regular = [m for m in regular if q in str(m.user_discord_id).lower()]
        else:
            filtered_regular = regular

        total_member_pages = max(1, math.ceil(len(filtered_regular) / self.MEMBER_PAGE_SIZE))
        if self.member_page >= total_member_pages:
            self.member_page = max(0, total_member_pages - 1)

        start = self.member_page * self.MEMBER_PAGE_SIZE
        paged_regular = filtered_regular[start : start + self.MEMBER_PAGE_SIZE]

        desc_parts = [
            f"**Discord Role:** <@&{squad.discord_role_id}>",
            f"**Total Members:** `{len(members)}` (Leads: `{len(leads)}`, Members: `{len(regular)}`)",
        ]
        if total_member_pages > 1:
            desc_parts.append(f"**Member Page:** `{self.member_page + 1}/{total_member_pages}`")
        if self.member_query:
            desc_parts.append(f"🔍 **Member Search:** `{self.member_query}` ({len(filtered_regular)} matching)")

        embed = discord.Embed(
            title=f"Squad Roster: {squad.name}",
            description="\n".join(desc_parts),
            color=discord.Color.blurple(),
        )

        leads_str = "\n".join(f"• <@{m.user_discord_id}> (Squad Lead)" for m in leads) or "*None assigned*"
        if len(leads_str) > 1024:
            leads_str = leads_str[:1000] + "\n... [truncated]"
        embed.add_field(name=f"Squad Leads ({len(leads)})", value=leads_str, inline=False)

        page_label = f" (Page {self.member_page + 1}/{total_member_pages})" if total_member_pages > 1 else ""
        members_str = "\n".join(f"• <@{m.user_discord_id}>" for m in paged_regular) or (
            "*No matching members found*" if self.member_query else "*None assigned*"
        )
        if len(members_str) > 1024:
            members_str = members_str[:1000] + "\n... [truncated]"
        embed.add_field(
            name=f"Verified Members ({len(filtered_regular)}){page_label}",
            value=members_str,
            inline=False,
        )
        return embed

    def _rebuild_items(self) -> None:
        self.clear_items()
        filtered_squads = self._get_filtered_squads()
        total_squad_pages = max(1, math.ceil(len(filtered_squads) / self.SQUAD_PAGE_SIZE))
        if self.squad_page >= total_squad_pages:
            self.squad_page = max(0, total_squad_pages - 1)

        start_squad = self.squad_page * self.SQUAD_PAGE_SIZE
        page_squads = filtered_squads[start_squad : start_squad + self.SQUAD_PAGE_SIZE]

        # Row 0: Select dropdown
        options = [
            discord.SelectOption(
                label=s.name[:100],
                value=str(s.id),
                description=f"Role: {s.discord_role_id}"[:50],
                default=(s.id == self.selected_squad_id),
            )
            for s in page_squads
        ]
        placeholder = (
            f"Select Squad to Inspect (Page {self.squad_page + 1}/{total_squad_pages})..."
            if total_squad_pages > 1
            else "Select Squad to Inspect Members..."
        )
        self.select = discord.ui.Select(
            placeholder=placeholder,
            options=options or [discord.SelectOption(label="No Squads Found", value="none")],
            row=0,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        # Row 1: Squad pagination and Search controls
        if total_squad_pages > 1 or self.squad_query:
            self.search_squad_btn = discord.ui.Button(
                label="Search Squads",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.search_squad_btn.callback = self._on_search_squads_clicked
            self.add_item(self.search_squad_btn)

            if self.squad_query:
                self.clear_squad_btn = discord.ui.Button(
                    label="Clear Squad Filter",
                    style=discord.ButtonStyle.secondary,
                    row=1,
                )
                self.clear_squad_btn.callback = self._on_clear_squad_filter_clicked
                self.add_item(self.clear_squad_btn)

            if total_squad_pages > 1:
                self.prev_squad_btn = discord.ui.Button(
                    label="◀ Prev Squads",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.squad_page <= 0),
                    row=1,
                )
                self.prev_squad_btn.callback = self._on_prev_squad_clicked
                self.add_item(self.prev_squad_btn)

                self.next_squad_btn = discord.ui.Button(
                    label="Next Squads ▶",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.squad_page >= total_squad_pages - 1),
                    row=1,
                )
                self.next_squad_btn.callback = self._on_next_squad_clicked
                self.add_item(self.next_squad_btn)

        # Row 2: Member pagination and search controls
        regular_members = [m for m in self._cached_members if m.role_type == SquadRoleType.MEMBER]
        if self.member_query:
            q = self.member_query.lower()
            regular_members = [m for m in regular_members if q in str(m.user_discord_id).lower()]
        total_member_pages = max(1, math.ceil(len(regular_members) / self.MEMBER_PAGE_SIZE))

        if self.selected_squad_id and (total_member_pages > 1 or self.member_query):
            self.prev_member_btn = discord.ui.Button(
                label="◀ Prev Members",
                style=discord.ButtonStyle.primary,
                disabled=(self.member_page <= 0),
                row=2,
            )
            self.prev_member_btn.callback = self._on_prev_members_clicked
            self.add_item(self.prev_member_btn)

            self.next_member_btn = discord.ui.Button(
                label="Next Members ▶",
                style=discord.ButtonStyle.primary,
                disabled=(self.member_page >= total_member_pages - 1),
                row=2,
            )
            self.next_member_btn.callback = self._on_next_members_clicked
            self.add_item(self.next_member_btn)

            self.search_member_btn = discord.ui.Button(
                label="Search Member",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            self.search_member_btn.callback = self._on_search_member_clicked
            self.add_item(self.search_member_btn)

            if self.member_query:
                self.clear_member_btn = discord.ui.Button(
                    label="Clear Member Filter",
                    style=discord.ButtonStyle.secondary,
                    row=2,
                )
                self.clear_member_btn.callback = self._on_clear_member_filter_clicked
                self.add_item(self.clear_member_btn)

        # Navigation: Back button
        back_row = (
            3
            if (self.selected_squad_id and (total_member_pages > 1 or self.member_query))
            else (2 if (total_squad_pages > 1 or self.squad_query) else 1)
        )
        self.back_btn = discord.ui.Button(
            label="Back to Squad Menu",
            style=discord.ButtonStyle.secondary,
            row=back_row,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    async def _on_select(self, interaction: discord.Interaction) -> None:
        val = self.select.values[0]
        if val == "none":
            await interaction.response.defer()
            return
        squad_id = UUID(val)
        squad = self.squads.get(squad_id)
        if not squad:
            await interaction.response.send_message("❌ Squad not found.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        self.selected_squad_id = squad.id
        self.member_page = 0
        self.member_query = ""
        self._cached_members = await self.squad_service.list_members(squad.id)

        embed = self._build_roster_embed(squad, self._cached_members)
        self._rebuild_items()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_members_clicked(self, interaction: discord.Interaction) -> None:
        if self.member_page > 0:
            self.member_page -= 1
            squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
            if squad:
                embed = self._build_roster_embed(squad, self._cached_members)
                self._rebuild_items()
                await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_members_clicked(self, interaction: discord.Interaction) -> None:
        squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
        if not squad:
            return
        regular_members = [m for m in self._cached_members if m.role_type == SquadRoleType.MEMBER]
        if self.member_query:
            q = self.member_query.lower()
            regular_members = [m for m in regular_members if q in str(m.user_discord_id).lower()]
        total_pages = max(1, math.ceil(len(regular_members) / self.MEMBER_PAGE_SIZE))
        if self.member_page < total_pages - 1:
            self.member_page += 1
            embed = self._build_roster_embed(squad, self._cached_members)
            self._rebuild_items()
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_search_member_clicked(self, interaction: discord.Interaction) -> None:
        modal = SquadMemberSearchModal(self._apply_member_search, current_query=self.member_query)
        await interaction.response.send_modal(modal)

    async def _apply_member_search(self, interaction: discord.Interaction, query: str) -> None:
        self.member_query = query
        self.member_page = 0
        squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
        if squad:
            embed = self._build_roster_embed(squad, self._cached_members)
            self._rebuild_items()
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_member_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.member_query = ""
        self.member_page = 0
        squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
        if squad:
            embed = self._build_roster_embed(squad, self._cached_members)
            self._rebuild_items()
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_search_squads_clicked(self, interaction: discord.Interaction) -> None:
        modal = SquadSearchModal(self._apply_squad_search, current_query=self.squad_query)
        await interaction.response.send_modal(modal)

    async def _apply_squad_search(self, interaction: discord.Interaction, query: str) -> None:
        self.squad_query = query
        self.squad_page = 0
        self._rebuild_items()
        squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
        if squad and self._cached_members:
            embed = self._build_roster_embed(squad, self._cached_members)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def _on_clear_squad_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.squad_query = ""
        self.squad_page = 0
        self._rebuild_items()
        squad = self.squads.get(self.selected_squad_id) if self.selected_squad_id else None
        if squad and self._cached_members:
            embed = self._build_roster_embed(squad, self._cached_members)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def _on_prev_squad_clicked(self, interaction: discord.Interaction) -> None:
        if self.squad_page > 0:
            self.squad_page -= 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_next_squad_clicked(self, interaction: discord.Interaction) -> None:
        filtered_squads = self._get_filtered_squads()
        total_pages = max(1, math.ceil(len(filtered_squads) / self.SQUAD_PAGE_SIZE))
        if self.squad_page < total_pages - 1:
            self.squad_page += 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

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
    ):
        super().__init__(timeout=180)
        self.all_squads = list(squads)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self.current_page = current_page
        self._initial_interaction = initial_interaction

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
    ):
        super().__init__(timeout=180)
        self.squad_service = squad_service
        self.project_service = project_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to
        self._initial_interaction = initial_interaction

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

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed
            from src.domain.enums import NotificationPreference

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
                else NotificationPreference.DM
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
                current_pref=current_pref,
                is_server_manager=view.is_server_manager,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            from src.adapters.discord_bot.views.hub_menu import PmHubView, build_hub_welcome_embed

            if self.project_service and self.task_service:
                view = PmHubView(self.project_service, self.squad_service, self.task_service, self.user_service)
                embed = build_hub_welcome_embed()
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

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
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

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
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


def build_squad_menu_embed(
    can_create_squads: bool = True,
    can_assign_members: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title="Squad Management Hub",
        color=discord.Color.dark_theme(),
    )
    bullets = []
    if can_create_squads:
        bullets.append("• **`Create Squad`**: Define a new squad and map it to a Discord role")
    if can_assign_members:
        bullets.append("• **`Assign Member`**: Pick a user and assign them as a Squad Member or Lead")
    bullets.append("• **`Squad Roster`**: View all squads and inspect verified members and leads")

    if can_create_squads or can_assign_members:
        desc = (
            "> **Contributor Squads & Permission Mappings**\n"
            "> Configure functional squads, map Discord roles, and assign squad leads.\n\n" + "\n".join(bullets)
        )
    else:
        desc = "> **Server Squad Rosters**\n> Inspect configured functional squads and rosters.\n\n" + "\n".join(
            bullets
        )

    embed.description = desc
    embed.set_footer(text="dgg-pm • Discord-Native Squad Management")
    return embed
