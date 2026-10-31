from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS, TOAST_DURATION_STANDARD
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.views.project.wizard_embeds import build_project_draft_embed
from src.adapters.discord_bot.views.project.wizard_inputs import (
    ProjectChannelSelectView,
    ProjectCreateModal,
)
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ProjectProvisionSpec,
)
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    pass

logger = logging.getLogger("dgg_pm.views.project")


class ProjectCreateDraftView(BaseView):
    """Interactive button controls displayed on the ephemeral project draft card.

    Allows users to select an optional Squad Discord Role and an optional Project Lead
    prior to creating the project container and binding the channel/forum.
    """

    def __init__(
        self,
        project_service: ProjectService,
        channel: discord.ForumChannel | discord.TextChannel | discord.Thread,
        name: str,
        prefix: str | None = None,
        description: str | None = None,
        category: str | None = None,
        role: discord.Role | None = None,
        roles: list[discord.Role] | None = None,
        lead: discord.Member | discord.User | None = None,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: Any = None,
        initial_interaction: discord.Interaction | None = None,
        project_workspace: IProjectDiscordWorkspace | None = None,
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.project_service = project_service
        self.channel = channel
        self.name = name
        self.prefix = prefix
        self.description = description
        self.category = category
        self.roles: list[discord.Role] = list(roles or [])
        if role and role not in self.roles:
            self.roles.insert(0, role)
        self.role = self.roles[0] if self.roles else None
        self.lead = lead
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        client = getattr(initial_interaction, "client", None)
        client_workspace = getattr(client, "project_workspace", None)
        if isinstance(client_workspace, IProjectDiscordWorkspace):
            self.project_workspace = client_workspace
        else:
            self.project_workspace = project_workspace or DiscordProjectWorkspaceAdapter(
                client,
                project_service,
                squad_service,
                task_service,
                user_service,
            )

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Select Discord Role(s) (Optional)
        self.role_select = discord.ui.RoleSelect(
            placeholder="Select Squad Role(s) (Optional)...",
            min_values=1,
            max_values=25,
            row=0,
        )
        self.role_select.callback = self._on_role_selected
        self.add_item(self.role_select)

        # Row 1: Select Project Lead User (Optional)
        self.lead_select = discord.ui.UserSelect(
            placeholder="Select Project Lead (Optional)...",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.lead_select.callback = self._on_lead_selected
        self.add_item(self.lead_select)

        # Row 2: Action Buttons
        self.create_btn = discord.ui.Button(
            label="Create Project",
            style=discord.ButtonStyle.success,
            row=2,
        )
        self.create_btn.callback = self._on_confirm_clicked
        self.add_item(self.create_btn)

        if self.roles or self.role:
            self.clear_role_btn = discord.ui.Button(
                label="Clear Squad Roles",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            self.clear_role_btn.callback = self._on_clear_role_clicked
            self.add_item(self.clear_role_btn)

        if self.lead:
            self.clear_lead_btn = discord.ui.Button(
                label="Clear Lead",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            self.clear_lead_btn.callback = self._on_clear_lead_clicked
            self.add_item(self.clear_lead_btn)

        self.edit_btn = discord.ui.Button(
            label="Edit Details",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.edit_btn.callback = self._on_edit_details_clicked
        self.add_item(self.edit_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

    async def _on_role_selected(self, interaction: discord.Interaction) -> None:
        self.roles = list(self.role_select.values)
        self.role = self.roles[0] if self.roles else None
        self._rebuild_items()
        embed = build_project_draft_embed(
            name=self.name,
            channel=self.channel,
            prefix=self.prefix,
            description=self.description,
            category=self.category,
            role=self.role,
            roles=self.roles,
            lead=self.lead,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_role_clicked(self, interaction: discord.Interaction) -> None:
        self.roles = []
        self.role = None
        self._rebuild_items()
        embed = build_project_draft_embed(
            name=self.name,
            channel=self.channel,
            prefix=self.prefix,
            description=self.description,
            category=self.category,
            role=None,
            roles=[],
            lead=self.lead,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_lead_selected(self, interaction: discord.Interaction) -> None:
        self.lead = self.lead_select.values[0]
        self._rebuild_items()
        embed = build_project_draft_embed(
            name=self.name,
            channel=self.channel,
            prefix=self.prefix,
            description=self.description,
            category=self.category,
            role=self.role,
            roles=self.roles,
            lead=self.lead,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_lead_clicked(self, interaction: discord.Interaction) -> None:
        self.lead = None
        self._rebuild_items()
        embed = build_project_draft_embed(
            name=self.name,
            channel=self.channel,
            prefix=self.prefix,
            description=self.description,
            category=self.category,
            role=self.role,
            roles=self.roles,
            lead=self.lead,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_edit_details_clicked(self, interaction: discord.Interaction) -> None:
        modal = ProjectCreateModal(
            project_service=self.project_service,
            channel=self.channel,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            draft_view=self,
            initial_name=self.name,
            initial_prefix=self.prefix or "",
            initial_desc=self.description or "",
            initial_cat=self.category or "",
        )
        await interaction.response.send_modal(modal)

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        try:
            embed = discord.Embed(
                title="Project Creation Cancelled",
                description="Project draft was discarded.",
                color=discord.Color.dark_grey(),
            )
            from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

            attach_dismissal_footer(embed, delay=TOAST_DURATION_STANDARD)
            await interaction.response.edit_message(embed=embed, view=None)

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
        except Exception as e:
            logger.debug("Error cancelling project draft: %s", e)

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This action must be run in a Discord server.", ephemeral=True)
            return

        try:
            ref = await self.project_workspace.provision_project(
                ProjectProvisionSpec(
                    guild_id=interaction.guild.id,
                    name=self.name,
                    prefix=self.prefix,
                    description=self.description,
                    channel=self.channel,
                    role=self.role,
                    roles=self.roles,
                    lead_discord_id=self.lead.id if self.lead else None,
                    category=self.category,
                )
            )
            project = ref.project

            is_forum = isinstance(self.channel, discord.ForumChannel)
            chan_type_label = "Forum Post Board" if is_forum else "Text Channel"
            tag_note = ""

            if ref.tags_created > 0:
                tag_note = f" • Setup {ref.tags_created} PM tags"
            if ref.control_hub_thread_id or ref.channel_id:
                tag_note += " • Pinned Control Hub created"

            lead_str = f"<@{project.lead_discord_id}>" if project.lead_discord_id else "*Unassigned*"
            role_ids = project.discord_role_ids or ([project.discord_role_id] if project.discord_role_id else [])
            role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*Public / All Members*"

            embed = discord.Embed(
                title=f"Project Created: {project.name} (`{project.prefix}`)",
                description=project.description or "No description provided.",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Bound Channel",
                value=f"<#{project.discord_channel_id}> ({chan_type_label}{tag_note})",
                inline=False,
            )
            embed.add_field(name="Squad Roles", value=role_str, inline=True)
            embed.add_field(name="Project Lead", value=lead_str, inline=True)
            if project.category:
                embed.add_field(name="Category", value=project.category, inline=True)
            embed.set_footer(text=f"Project ID: {project.id}")
            from src.adapters.discord_bot.menu_manager import attach_dismissal_footer, menu_manager

            attach_dismissal_footer(embed, delay=TOAST_DURATION_STANDARD)
            await interaction.response.edit_message(embed=embed, view=None)

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating project '{self.name}'", logger, ephemeral=True)


__all__ = [
    "ProjectChannelSelectView",
    "ProjectCreateDraftView",
    "ProjectCreateModal",
    "build_project_draft_embed",
]
