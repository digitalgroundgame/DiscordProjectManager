"""Interactive view to inspect squad members and leads."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.squad_embeds import build_squad_menu_embed
from src.domain.enums import SquadRoleType
from src.domain.models import Squad

if TYPE_CHECKING:
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.squad_roster")


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
        from src.adapters.discord_bot.views.squad_menu import SquadMemberSearchModal

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
        from src.adapters.discord_bot.views.squad_menu import SquadSearchModal

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
        from src.adapters.discord_bot.views.squad_menu import SquadMenuView

        view = SquadMenuView(
            self.squad_service,
            self.project_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_squad_menu_embed(view.can_create_squads, view.can_assign_members)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


__all__ = ["SquadRosterDetailView"]
