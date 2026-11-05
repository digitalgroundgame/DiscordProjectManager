"""Interactive view to assign members and leads to squads."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.ui_constants import (
    EPHEMERAL_MENU_TIMEOUT_SECONDS,
    TOAST_DURATION_STANDARD,
)
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.squad_embeds import build_squad_menu_embed
from src.domain.enums import SquadRoleType
from src.domain.models import Squad
from src.services.auth_service import AuthService

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.squad_member_assign")


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
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
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
        from src.adapters.discord_bot.views.squad_menu import SquadSearchModal

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
        from src.adapters.discord_bot.views.squad_menu import SquadMenuView

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

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
            return

        if not self.selected_user:
            if self.user_select.values:
                self.selected_user = self.user_select.values[0]
            else:
                await interaction.response.send_message("❌ Please select a Discord member first.", ephemeral=True)
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
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

                menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
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

                    menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
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

            from src.adapters.discord_bot.views.squad_menu import SquadMenuView

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


__all__ = ["SquadMemberAssignView"]
