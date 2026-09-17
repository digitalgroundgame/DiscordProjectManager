from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.forum_helpers import ensure_pinned_hub_post
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ProjectProvisionSpec,
    RebuildProgress,
)
from src.domain.enums import TaskStatus
from src.domain.models import Project, Squad
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.project_menu")


def build_project_draft_embed(
    name: str,
    channel: discord.ForumChannel | discord.TextChannel | discord.Thread,
    prefix: str | None = None,
    description: str | None = None,
    category: str | None = None,
    role: discord.Role | None = None,
    roles: list[discord.Role] | None = None,
    lead: discord.Member | discord.User | None = None,
) -> discord.Embed:
    """Builds a live preview embed of the project currently being configured in the builder."""
    is_forum = isinstance(channel, discord.ForumChannel)
    chan_type = "Forum Post Board" if is_forum else "Text Channel"
    chan_name = getattr(channel, "name", str(getattr(channel, "id", "channel")))

    all_roles = list(roles or [])
    if role and role not in all_roles:
        all_roles.insert(0, role)

    role_display = ", ".join(f"<@&{r.id}>" for r in all_roles) if all_roles else "*Public / All Members (Optional)*"
    lead_display = f"<@{lead.id}>" if lead else "*Unassigned (Optional)*"
    prefix_display = f"`{prefix}`" if prefix else "*Auto-generated*"
    cat_display = category if category else "*None*"

    embed = discord.Embed(
        title=f"Project Setup: {name[:90]}",
        description=(
            "Configure the contributor squad role(s) and optional project lead using the pickers below, "
            "then click **`Create Project`** to initialize.\n\n"
            f"• **Bound Channel**: <#{channel.id}> (`#{chan_name}` • {chan_type})\n"
            f"• **Key Prefix**: {prefix_display}\n"
            f"• **Squad Roles (Optional)**: {role_display}\n"
            f"• **Project Lead (Optional)**: {lead_display}\n"
            f"• **Category**: {cat_display}"
        ),
        color=discord.Color.blurple(),
    )
    if description:
        embed.add_field(
            name="Description / Mission",
            value=description[:1000] + ("..." if len(description) > 1000 else ""),
            inline=False,
        )
    embed.set_footer(text="dgg-pm • Interactive Project Creation Builder")
    return embed


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
    ):
        super().__init__(timeout=300)
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
        self._initial_interaction = initial_interaction
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

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
            pass
        except Exception:
            pass

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
            style=discord.ButtonStyle.danger,
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

            attach_dismissal_footer(embed, delay=3.0)
            await interaction.response.edit_message(embed=embed, view=None)

            menu_manager.schedule_toast_dismissal(interaction, delay=3.0)
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

            attach_dismissal_footer(embed, delay=8.0)
            await interaction.response.edit_message(embed=embed, view=None)

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating project '{self.name}'", logger, ephemeral=True)


class ProjectCreateModal(BaseModal):
    def __init__(
        self,
        project_service: ProjectService,
        channel: discord.ForumChannel | discord.TextChannel | discord.Thread,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: Any = None,
        draft_view: ProjectCreateDraftView | None = None,
        initial_name: str = "",
        initial_prefix: str = "",
        initial_desc: str = "",
        initial_cat: str = "",
        parent_view: BaseView | None = None,
    ):
        self.project_service = project_service
        self.channel = channel
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.draft_view = draft_view
        self.parent_view = parent_view

        raw_name = getattr(channel, "name", None)
        if isinstance(raw_name, str) and raw_name.strip():
            chan_name = raw_name.strip()
        elif hasattr(channel, "id"):
            chan_name = str(channel.id)
        else:
            chan_name = "channel"

        is_forum = isinstance(channel, discord.ForumChannel)
        type_prefix = "Forum" if is_forum else "Channel"

        if draft_view:
            title_str = "Edit Project Details"
        else:
            title_str = f"New Project in #{chan_name}"

        # Max title length in Discord modal is 45 characters
        if len(title_str) > 45:
            title_str = title_str[:42] + "..."

        super().__init__(title=title_str)

        name_label = f"Project Name ({type_prefix} #{chan_name})"
        if len(name_label) > 45:
            name_label = name_label[:42] + "..."

        name_placeholder = f"e.g. Mobile Redesign (bound to #{chan_name})"
        if len(name_placeholder) > 100:
            name_placeholder = name_placeholder[:97] + "..."

        self.name_input = discord.ui.TextInput(
            label=name_label,
            placeholder=name_placeholder,
            default=initial_name,
            required=True,
            max_length=100,
        )
        self.add_item(self.name_input)

        self.prefix_input = discord.ui.TextInput(
            label="Key Prefix (Optional, 2-5 uppercase chars)",
            placeholder="e.g. MOB, INF (Leave empty for auto-generated)",
            default=initial_prefix,
            required=False,
            max_length=10,
        )
        self.add_item(self.prefix_input)

        self.desc_input = discord.ui.TextInput(
            label="Description (Optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Mission statement, architecture goals, or deliverables...",
            default=initial_desc,
            required=False,
            max_length=1000,
        )
        self.add_item(self.desc_input)

        self.cat_input = discord.ui.TextInput(
            label="Category (Optional)",
            placeholder="e.g. Engineering, Marketing, Operations",
            default=initial_cat,
            required=False,
            max_length=50,
        )
        self.add_item(self.cat_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This action must be run in a Discord server.", ephemeral=True)
            return

        name = self.name_input.value.strip()
        if not name:
            from src.adapters.discord_bot.menu_manager import format_toast_message, menu_manager

            if self.draft_view:
                self.draft_view._rebuild_items()
                embed = build_project_draft_embed(
                    name=self.draft_view.name,
                    channel=self.draft_view.channel,
                    prefix=self.draft_view.prefix,
                    description=self.draft_view.description,
                    category=self.draft_view.category,
                    role=self.draft_view.role,
                    roles=self.draft_view.roles,
                    lead=self.draft_view.lead,
                )
                await interaction.response.edit_message(embed=embed, view=self.draft_view)
                toast = await interaction.followup.send(
                    format_toast_message("❌ Project name cannot be empty.", delay=8.0),
                    ephemeral=True,
                    wait=True,
                )
                menu_manager.schedule_toast_dismissal(toast, delay=8.0)
            elif self.parent_view:
                if hasattr(self.parent_view, "_rebuild_items"):
                    self.parent_view._rebuild_items()
                embed = self.parent_view.build_embed() if hasattr(self.parent_view, "build_embed") else None
                if embed:
                    await interaction.response.edit_message(embed=embed, view=self.parent_view)
                else:
                    await interaction.response.edit_message(view=self.parent_view)
                toast = await interaction.followup.send(
                    format_toast_message("❌ Project name cannot be empty.", delay=8.0),
                    ephemeral=True,
                    wait=True,
                )
                menu_manager.schedule_toast_dismissal(toast, delay=8.0)
            else:
                await interaction.response.send_message("❌ Project name cannot be empty.", ephemeral=True)
                menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        prefix = self.prefix_input.value.strip() or None
        desc = self.desc_input.value.strip() or None
        cat = self.cat_input.value.strip() or None

        if self.draft_view:
            self.draft_view.name = name
            self.draft_view.prefix = prefix
            self.draft_view.description = desc
            self.draft_view.category = cat
            self.draft_view._rebuild_items()
            embed = build_project_draft_embed(
                name=name,
                channel=self.channel,
                prefix=prefix,
                description=desc,
                category=cat,
                role=self.draft_view.role,
                lead=self.draft_view.lead,
            )
            await interaction.response.edit_message(embed=embed, view=self.draft_view)
        else:
            draft_view = ProjectCreateDraftView(
                project_service=self.project_service,
                channel=self.channel,
                name=name,
                prefix=prefix,
                description=desc,
                category=cat,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
            )
            embed = build_project_draft_embed(
                name=name,
                channel=self.channel,
                prefix=prefix,
                description=desc,
                category=cat,
                role=None,
                lead=None,
            )
            await interaction.response.send_message(embed=embed, view=draft_view, ephemeral=True)


class ProjectChannelSelectView(BaseView):
    """Interactive view allowing the user to select a Forum channel for a new project."""

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user_service: UserService | None = None,
        return_to: str = "projects",
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to
        self._initial_interaction = initial_interaction
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.channel_select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.forum],
            placeholder="Select target Forum channel...",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.channel_select.callback = self._on_channel_selected
        self.add_item(self.channel_select)

        self.current_chan_btn = discord.ui.Button(
            label="Use Current Forum Channel",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.current_chan_btn.callback = self._on_current_channel_clicked
        self.add_item(self.current_chan_btn)

        self.back_btn = discord.ui.Button(
            label="Back",
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

    async def _on_channel_selected(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        selected = self.channel_select.values[0]
        chan = interaction.guild.get_channel(selected.id) if hasattr(selected, "id") else selected
        if not chan or not isinstance(chan, discord.ForumChannel):
            self._rebuild_items()
            await interaction.response.edit_message(view=self)
            from src.adapters.discord_bot.menu_manager import format_toast_message, menu_manager

            toast = await interaction.followup.send(
                format_toast_message("❌ Selected channel must be a Forum Channel.", delay=8.0),
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=8.0)
            return

        modal = ProjectCreateModal(
            self.project_service,
            channel=chan,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            parent_view=self,
        )
        await interaction.response.send_modal(modal)

    async def _on_current_channel_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.channel, discord.ForumChannel):
            await interaction.response.send_message(
                "❌ Current channel is not a Forum Channel. Please select a Forum Channel from the dropdown above.",
                ephemeral=True,
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        modal = ProjectCreateModal(
            self.project_service,
            channel=interaction.channel,
            squad_service=self.squad_service,
            task_service=self.task_service,
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed
            from src.domain.enums import NotificationPreference

            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
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
            view = ProjectMenuView(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
                user_service=self.user_service,
                return_to=self.return_to,
            )
            embed = build_project_menu_embed(view.is_server_manager)
            await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectSearchModal(BaseModal):
    """Modal to enter a search query for filtering projects."""

    def __init__(self, on_search_callback: Callable[[discord.Interaction, str], Any], current_query: str = ""):
        super().__init__(title="Search Projects")
        self.on_search_callback = on_search_callback

        self.query_input = discord.ui.TextInput(
            label="Project Name, Prefix, or Category",
            placeholder="e.g. INF, Security, Mobile, DevOps...",
            default=current_query,
            required=False,
            max_length=100,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.query_input.value.strip()
        await self.on_search_callback(interaction, query)


def build_active_projects_embed(
    projects: list[Project],
    page: int,
    total_count: int,
    query: str = "",
    page_size: int = 8,
) -> discord.Embed:
    total_pages = max(1, math.ceil(total_count / page_size))
    start_idx = page * page_size
    page_projects = projects[start_idx : start_idx + page_size]

    filter_str = f" • Filter: `{query}`" if query else ""
    embed = discord.Embed(
        title=f"Active Projects ({total_count}){filter_str}",
        color=discord.Color.blurple(),
    )
    if not page_projects:
        embed.description = "*No active projects match your search.*"
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        return embed

    for p in page_projects:
        chan_str = f"<#{p.discord_channel_id}>" if p.discord_channel_id else "No channel"
        role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
        role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*Public / All Members*"
        lead_str = f"<@{p.lead_discord_id}>" if p.lead_discord_id else "*Unassigned*"
        desc_str = p.description or "No description"
        cat_str = f" • [{p.category}]" if p.category else ""
        embed.add_field(
            name=f"{p.name} (`{p.prefix}`){cat_str}",
            value=(
                f"• Channel: {chan_str}\n"
                f"• Squad: {role_str} | Lead: {lead_str}\n"
                f"• Next Task: #{p.next_task_number}\n"
                f"• {desc_str}"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Page {page + 1} of {total_pages} • Total: {total_count} projects")
    return embed


class ProjectActiveListView(BaseView):
    """Paginated and searchable view for active projects."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        current_page: int = 0,
        query: str = "",
        page_size: int = 8,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.all_projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.current_page = current_page
        self.query = query
        self.page_size = page_size
        self._initial_interaction = initial_interaction
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()

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

    def _filter_projects(self) -> list[Project]:
        if not self.query:
            return self.all_projects
        q = self.query.lower()
        return [
            p
            for p in self.all_projects
            if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
        ]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_count = len(self._filtered_projects)
        total_pages = max(1, math.ceil(total_count / self.page_size))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        # Row 0: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query:
            clear_btn = discord.ui.Button(
                label="Clear Filter",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 1: Pagination buttons
        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=1,
            )
            prev_btn.callback = self._on_prev_clicked
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=1,
            )
            next_btn.callback = self._on_next_clicked
            self.add_item(next_btn)

    async def _on_search_clicked(self, interaction: discord.Interaction) -> None:
        modal = ProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        embed = build_active_projects_embed(
            self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        embed = build_active_projects_embed(
            self._filtered_projects, self.current_page, len(self._filtered_projects), page_size=self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            embed = build_active_projects_embed(
                self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
            )
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.page_size))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = build_active_projects_embed(
                self._filtered_projects, self.current_page, len(self._filtered_projects), self.query, self.page_size
            )
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectArchiveConfirmView(BaseView):
    """Interactive confirmation view prior to archiving a project and active tasks."""

    def __init__(
        self,
        project: Project,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.project = project
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self._initial_interaction = initial_interaction

        self.confirm_btn = discord.ui.Button(
            label="Confirm Archive",
            style=discord.ButtonStyle.danger,
            row=0,
        )
        self.confirm_btn.callback = self._on_confirm_clicked
        self.add_item(self.confirm_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

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

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        try:
            tasks = []
            if self.task_service and interaction.guild:
                tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id,
                    project_id=self.project.id,
                    include_archived=False,
                    limit=500,
                )

            archived = await self.project_service.archive_project(self.project.id)
            if not archived:
                archived = self.project

            if hasattr(interaction.client, "sync_task_thread"):
                for t in tasks:
                    if t.discord_thread_id:
                        await interaction.client.sync_task_thread(t, action="archive")

            if interaction.guild and self.project.discord_channel_id:
                chan = interaction.guild.get_channel(self.project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            squad_service=self.squad_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project archive: %s", he)

            view = ProjectMenuView(
                self.project_service,
                self.squad_service,
                self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
            )
            embed = build_project_menu_embed(view.is_server_manager)
            embed.description = (
                f"**Project Archived!**\n"
                f"Project **{archived.name} (`{archived.prefix}`)** and its active tasks have been archived.\n\n"
                + (embed.description or "")
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"archiving project '{self.project.name}'", logger, ephemeral=True
            )

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectRestoreConfirmView(BaseView):
    """Interactive confirmation view prior to restoring an archived project."""

    def __init__(
        self,
        project: Project,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.project = project
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self._initial_interaction = initial_interaction

        self.confirm_btn = discord.ui.Button(
            label="Confirm Restore",
            style=discord.ButtonStyle.success,
            row=0,
        )
        self.confirm_btn.callback = self._on_confirm_clicked
        self.add_item(self.confirm_btn)

        self.cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.cancel_btn.callback = self._on_cancel_clicked
        self.add_item(self.cancel_btn)

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

    async def _on_confirm_clicked(self, interaction: discord.Interaction) -> None:
        try:
            restored = await self.project_service.unarchive_project(self.project.id)
            if not restored:
                restored = self.project

            if self.task_service and interaction.guild and hasattr(interaction.client, "sync_task_thread"):
                restored_tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id,
                    project_id=self.project.id,
                    include_archived=False,
                    limit=500,
                )
                for t in restored_tasks:
                    if t.discord_thread_id and t.status != TaskStatus.COMPLETED:
                        await interaction.client.sync_task_thread(t, action="unarchive")

            if interaction.guild and self.project.discord_channel_id:
                chan = interaction.guild.get_channel(self.project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            squad_service=self.squad_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project restore: %s", he)

            view = ProjectMenuView(
                self.project_service,
                self.squad_service,
                self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
            )
            embed = build_project_menu_embed(view.is_server_manager)
            embed.description = (
                f"**Project Restored!**\n"
                f"Project **{restored.name} (`{restored.prefix}`)** has been reactivated.\n\n"
                + (embed.description or "")
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"restoring project '{self.project.name}'", logger, ephemeral=True
            )

    async def _on_cancel_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


def build_archive_select_embed(
    total_count: int,
    page: int,
    total_pages: int,
    query: str = "",
) -> discord.Embed:
    filter_note = f"\n**Search Filter Active:** `{query}`" if query else ""
    embed = discord.Embed(
        title="Archive Project",
        description=(
            f"Select an active project below to archive:{filter_note}\n\n"
            f"• **Available Projects:** `{total_count}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            "Selecting a project will open a confirmation screen."
        ),
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Use Search or Page buttons to browse larger collections.")
    return embed


class ProjectArchiveSelectView(BaseView):
    """Interactive select menu to choose an active project to archive with search and pagination."""

    PAGE_SIZE = 25

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        current_page: int = 0,
        query: str = "",
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.all_projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.current_page = current_page
        self.query = query
        self._initial_interaction = initial_interaction
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()

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

    def _filter_projects(self) -> list[Project]:
        if not self.query:
            return self.all_projects
        q = self.query.lower()
        return [
            p
            for p in self.all_projects
            if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
        ]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_count = len(self._filtered_projects)
        total_pages = max(1, math.ceil(total_count / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.PAGE_SIZE
        page_projects = self._filtered_projects[start_idx : start_idx + self.PAGE_SIZE]

        # Row 0: Select dropdown (up to 25 options)
        if page_projects:
            options = [
                discord.SelectOption(
                    label=f"{p.name} ({p.prefix})"[:100],
                    value=str(p.id),
                    description=(p.description[:90] if p.description else "Active Project"),
                )
                for p in page_projects
            ]
            self.select = discord.ui.Select(
                placeholder=f"Select project to archive (Page {self.current_page + 1}/{total_pages})...",
                options=options,
                min_values=1,
                max_values=1,
                row=0,
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        # Row 1: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query:
            clear_btn = discord.ui.Button(
                label="Clear Filter",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 2: Pagination (if > 1 page)
        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=2,
            )
            prev_btn.callback = self._on_prev_clicked
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=2,
            )
            next_btn.callback = self._on_next_clicked
            self.add_item(next_btn)

    async def _on_search_clicked(self, interaction: discord.Interaction) -> None:
        modal = ProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_archive_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_archive_select_embed(len(self._filtered_projects), self.current_page, total_pages)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
            embed = build_archive_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = build_archive_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        project_id = UUID(self.select.values[0])
        project = next((p for p in self.all_projects if p.id == project_id), None)
        if not project and interaction.guild:
            project = await self.project_service.get_by_id(project_id)

        if not project:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        task_count_note = ""
        if self.task_service and interaction.guild:
            _tasks, total_tasks = await self.task_service.list_tasks(
                guild_id=interaction.guild.id,
                project_id=project_id,
                include_archived=False,
                limit=500,
            )
            task_count_note = f"\n• **Active Tasks:** `{total_tasks}` task(s) will be archived."

        chan_note = f"\n• **Channel:** <#{project.discord_channel_id}>" if project.discord_channel_id else ""

        embed = discord.Embed(
            title="Confirm Project Archival",
            description=(
                f"Are you sure you want to archive project **{project.name} (`{project.prefix}`)**?\n"
                f"{chan_note}"
                f"{task_count_note}\n"
                "• **Discord Threads:** Associated task discussion threads will be archived.\n\n"
                "This will hide the project from active selectors and board views."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Click 'Confirm Archive' to execute or 'Cancel' to abort.")

        view = ProjectArchiveConfirmView(
            project=project,
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


def build_restore_select_embed(
    total_count: int,
    page: int,
    total_pages: int,
    query: str = "",
) -> discord.Embed:
    filter_note = f"\n**Search Filter Active:** `{query}`" if query else ""
    embed = discord.Embed(
        title="Restore Project",
        description=(
            f"Select an archived project below to restore:{filter_note}\n\n"
            f"• **Archived Projects Available:** `{total_count}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            "Selecting a project will open a confirmation screen."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Use Search or Page buttons to browse larger collections.")
    return embed


class ProjectRestoreSelectView(BaseView):
    """Interactive select menu to choose an archived project to restore with search and pagination."""

    PAGE_SIZE = 25

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        current_page: int = 0,
        query: str = "",
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.all_projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.current_page = current_page
        self.query = query
        self._initial_interaction = initial_interaction
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()

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

    def _filter_projects(self) -> list[Project]:
        if not self.query:
            return self.all_projects
        q = self.query.lower()
        return [
            p
            for p in self.all_projects
            if q in p.name.lower() or q in p.prefix.lower() or (p.category and q in p.category.lower())
        ]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_count = len(self._filtered_projects)
        total_pages = max(1, math.ceil(total_count / self.PAGE_SIZE))
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)

        start_idx = self.current_page * self.PAGE_SIZE
        page_projects = self._filtered_projects[start_idx : start_idx + self.PAGE_SIZE]

        # Row 0: Select dropdown (up to 25 options)
        if page_projects:
            options = [
                discord.SelectOption(
                    label=f"{p.name} ({p.prefix})"[:100],
                    value=str(p.id),
                    description=(p.description[:90] if p.description else "Archived Project"),
                )
                for p in page_projects
            ]
            self.select = discord.ui.Select(
                placeholder=f"Select project to restore (Page {self.current_page + 1}/{total_pages})...",
                options=options,
                min_values=1,
                max_values=1,
                row=0,
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        # Row 1: Search, Clear, Back
        search_btn = discord.ui.Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        search_btn.callback = self._on_search_clicked
        self.add_item(search_btn)

        if self.query:
            clear_btn = discord.ui.Button(
                label="Clear Filter",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            clear_btn.callback = self._on_clear_filter_clicked
            self.add_item(clear_btn)

        back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._on_back_clicked
        self.add_item(back_btn)

        # Row 2: Pagination (if > 1 page)
        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page <= 0),
                row=2,
            )
            prev_btn.callback = self._on_prev_clicked
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                row=2,
            )
            next_btn.callback = self._on_next_clicked
            self.add_item(next_btn)

    async def _on_search_clicked(self, interaction: discord.Interaction) -> None:
        modal = ProjectSearchModal(self._apply_search, current_query=self.query)
        await interaction.response.send_modal(modal)

    async def _apply_search(self, interaction: discord.Interaction, query: str) -> None:
        self.query = query
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_clear_filter_clicked(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.current_page = 0
        self._filtered_projects = self._filter_projects()
        self._rebuild_items()
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_prev_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._rebuild_items()
            total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
            embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next_clicked(self, interaction: discord.Interaction) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_projects) / self.PAGE_SIZE))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._rebuild_items()
            embed = build_restore_select_embed(len(self._filtered_projects), self.current_page, total_pages, self.query)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        project_id = UUID(self.select.values[0])
        project = next((p for p in self.all_projects if p.id == project_id), None)
        if not project and interaction.guild:
            project = await self.project_service.get_by_id(project_id)

        if not project:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        chan_note = f"\n• **Bound Channel:** <#{project.discord_channel_id}>" if project.discord_channel_id else ""

        embed = discord.Embed(
            title="Confirm Project Restoration",
            description=(
                f"Are you sure you want to restore project **{project.name} (`{project.prefix}`)**?\n"
                f"{chan_note}\n"
                "• **Project Status:** Reactivated and visible across project selectors.\n"
                "• **Active Tasks:** Incomplete tasks will be unarchived.\n"
                "• **Discord Threads:** Associated task threads will be reopened."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Click 'Confirm Restore' to execute or 'Cancel' to abort.")

        view = ProjectRestoreConfirmView(
            project=project,
            project_service=self.project_service,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class RebuildConfirmView(BaseView):
    """Ephemeral confirmation view for project workspace rebuilds."""

    def __init__(
        self,
        project_workspace: IProjectDiscordWorkspace,
        project: Project,
        target_channel: discord.ForumChannel | None,
        interaction: discord.Interaction,
        task_count: int,
        return_to: str = "dashboard",
    ):
        super().__init__(timeout=120)
        self.project_workspace = project_workspace
        self.project = project
        self.target_channel = target_channel
        self.original_interaction = interaction
        self.task_count = task_count
        self.return_to = return_to

    @discord.ui.button(label="Confirm Rebuild", style=discord.ButtonStyle.danger, emoji="🚀")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        await interaction.response.edit_message(
            content=f"⚙️ **Rebuilding workspace for [{self.project.prefix}] {self.project.name}...**",
            view=self,
        )

        async def progress_callback(progress: RebuildProgress) -> None:
            try:
                embed = discord.Embed(
                    title=f"⚙️ Rebuilding Workspace: [{self.project.prefix}] {self.project.name}",
                    description=(
                        f"> {progress.message}\n\n"
                        f"**Step:** `{progress.step.upper()}` ({progress.current}/{progress.total})"
                    ),
                    color=discord.Color.gold(),
                )
                await interaction.edit_original_response(content=None, embed=embed, view=None)
            except Exception:
                pass

        try:
            result = await self.project_workspace.rebuild_workspace(
                self.project.id,
                guild=interaction.guild,
                target_channel=self.target_channel,
                progress_callback=progress_callback,
            )
            embed = discord.Embed(
                title=f"✅ Workspace Rebuilt: [{result.project.prefix}] {result.project.name}",
                description=(
                    f"Successfully reconstructed Discord presence for **{result.project.name}**!\n\n"
                    f"• **Channel:** <#{result.channel_id}>\n"
                    f"• **Forum Created:** {'Yes' if result.forum_created else 'No (Existing channel)'}\n"
                    f"• **Tags Configured:** `{result.tags_created}`\n"
                    f"• **Control Hub:** {'Mounted / Active' if result.hub_rebuilt else 'Not mounted'}\n"
                    f"• **Tasks Reconciled:** `{result.tasks_reconciled}`\n"
                    f"• **Tasks Recreated:** `{result.tasks_recreated}`\n"
                    f"• **Historical Threads Archived:** `{result.tasks_archived}`"
                ),
                color=discord.Color.green(),
            )
            if result.warnings:
                embed.add_field(name="⚠️ Warnings", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"rebuilding workspace for {self.project.name}", logger, ephemeral=True
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=f"❌ Workspace rebuild for **{self.project.name}** was cancelled.",
            embed=None,
            view=None,
        )


class ProjectRebuildSelectView(BaseView):
    """Interactive select menu to choose a project to rebuild."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        project_workspace: IProjectDiscordWorkspace,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.projects = projects
        self.project_service = project_service
        self.project_workspace = project_workspace
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self._initial_interaction = initial_interaction

        options = [
            discord.SelectOption(
                label=f"[{p.prefix}] {p.name[:50]}",
                value=str(p.id),
                description=f"{'Archived' if p.is_archived else 'Active'} • Channel: #{p.discord_channel_id or 'None'}"[
                    :100
                ],
            )
            for p in self.projects[:25]
        ]
        self.select = discord.ui.Select(
            placeholder="Select a project container to rebuild...",
            options=options or [discord.SelectOption(label="No Projects", value="none")],
            row=0,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=1)
        self.back_btn.callback = self._on_back
        self.add_item(self.back_btn)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self.select.values or self.select.values[0] == "none":
            return
        proj_id = UUID(self.select.values[0])
        proj = next((p for p in self.projects if p.id == proj_id), None)
        if not proj or not interaction.guild:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        task_count = 0
        if self.task_service:
            _, task_count = await self.task_service.list_tasks(
                guild_id=interaction.guild.id, project_id=proj.id, include_archived=True
            )

        channel_status = (
            f"Existing: <#{proj.discord_channel_id}>"
            if proj.discord_channel_id and interaction.guild.get_channel(proj.discord_channel_id)
            else "Auto-create new ForumChannel"
        )
        embed = discord.Embed(
            title=f"⚠️ Confirm Workspace Rebuild: [{proj.prefix}] {proj.name}",
            description=(
                f"Are you sure you want to rebuild the Discord workspace for **{proj.name}**?\n\n"
                f"**Planned Actions:**\n"
                f"• **Channel Target:** {channel_status}\n"
                f"• **Forum Tags:** Configure standard status, priority, and project tags\n"
                f"• **Control Hub:** Mount and pin interactive Control Hub post\n"
                f"• **Task Threads:** Reconcile **{task_count}** tasks "
                "(recreate missing threads and enforce Archive Invariant)\n"
                f"• **Project Status:** {'Unarchive and restore' if proj.is_archived else 'Active'}\n\n"
                "⚠️ *This will pace Discord API calls to respect rate limits.*"
            ),
            color=discord.Color.orange(),
        )
        view = RebuildConfirmView(
            project_workspace=self.project_workspace,
            project=proj,
            target_channel=None,
            interaction=interaction,
            task_count=task_count,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectAssignTimelineModal(BaseModal):
    """Modal to specify timeline when assigning a squad to a project."""

    def __init__(
        self,
        project_service: ProjectService,
        project: Project,
        squad: Squad,
        squad_service: SquadService,
        task_service: TaskService | None = None,
    ):
        super().__init__(title=f"Assign Squad to [{project.prefix}]"[:45])
        self.project_service = project_service
        self.project = project
        self.squad = squad
        self.squad_service = squad_service
        self.task_service = task_service

        self.timeline_input = discord.ui.TextInput(
            label="Target Timeline (Optional)",
            placeholder="e.g. Q3 2026, 6 weeks, Sprint 1-4",
            required=False,
            max_length=100,
        )
        self.add_item(self.timeline_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        timeline = self.timeline_input.value.strip() or None
        try:
            await self.project_service.assign_squad_to_project(
                project_id=self.project.id,
                squad_id=self.squad.id,
                timeline=timeline,
            )
            embed = discord.Embed(
                title="Squad Mapped to Project",
                description=(
                    f"Successfully mapped squad **{self.squad.name}** (<@&{self.squad.discord_role_id}>) "
                    f"to project **{self.project.name}** (`{self.project.prefix}`)."
                    + (f"\n\n• **Target Timeline:** `{timeline}`" if timeline else "")
                ),
                color=discord.Color.green(),
            )
            view = ProjectMenuView(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction,
                e,
                f"assigning squad '{self.squad.name}' to project '{self.project.name}'",
                logger,
                ephemeral=True,
            )


class ProjectAssignSquadView(BaseView):
    """Interactive view to map a squad to a project with optional timeline."""

    def __init__(
        self,
        projects: list[Project],
        squads: list[Squad],
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        squad_page: int = 0,
    ):
        super().__init__(timeout=180)
        self.projects = projects
        self.squads = squads
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction
        self.squad_page = squad_page

        self.selected_project_id: UUID = projects[0].id if projects else UUID(int=0)
        self.selected_squad_id: UUID = squads[0].id if squads else UUID(int=0)

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: Select Project
        proj_options = [
            discord.SelectOption(
                label=f"{p.name} ({p.prefix})"[:100],
                value=str(p.id),
                description=(p.description[:50] if p.description else "Active Project"),
                default=(p.id == self.selected_project_id),
            )
            for i, p in enumerate(self.projects[:25])
        ]
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options or [discord.SelectOption(label="No Projects", value="none")],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Squad (Paged if >25)
        total_squad_pages = max(1, math.ceil(len(self.squads) / 25))
        if self.squad_page >= total_squad_pages:
            self.squad_page = max(0, total_squad_pages - 1)
        start_t = self.squad_page * 25
        page_squads = self.squads[start_t : start_t + 25]

        if not any(s.id == self.selected_squad_id for s in page_squads) and page_squads:
            self.selected_squad_id = page_squads[0].id

        squad_options = [
            discord.SelectOption(
                label=s.name[:100],
                value=str(s.id),
                description=f"Discord Role: @{s.discord_role_id}"[:50],
                default=(s.id == self.selected_squad_id),
            )
            for i, s in enumerate(page_squads)
        ]
        placeholder = (
            f"Select Squad (Page {self.squad_page + 1}/{total_squad_pages})..."
            if total_squad_pages > 1
            else "Select Squad..."
        )
        self.squad_select = discord.ui.Select(
            placeholder=placeholder,
            options=squad_options or [discord.SelectOption(label="No Squads", value="none")],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.squad_select.callback = self._on_squad_changed
        self.add_item(self.squad_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Map Squad (Quick)",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_quick_clicked
        self.add_item(self.assign_btn)

        self.timeline_btn = discord.ui.Button(
            label="Set Timeline & Map...",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.timeline_btn.callback = self._on_timeline_clicked
        self.add_item(self.timeline_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.back_btn.callback = self._on_back_clicked
        self.add_item(self.back_btn)

        # Row 3: Squad Pagination buttons if total_squad_pages > 1
        if total_squad_pages > 1:
            self.prev_squad_btn = discord.ui.Button(
                label="◀ Prev Squads",
                style=discord.ButtonStyle.secondary,
                disabled=(self.squad_page <= 0),
                row=3,
            )
            self.prev_squad_btn.callback = self._on_prev_squad_clicked
            self.add_item(self.prev_squad_btn)

            self.next_squad_btn = discord.ui.Button(
                label="Next Squads ▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.squad_page >= total_squad_pages - 1),
                row=3,
            )
            self.next_squad_btn.callback = self._on_next_squad_clicked
            self.add_item(self.next_squad_btn)

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

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    def _get_selected_squad(self) -> Squad | None:
        return next((s for s in self.squads if s.id == self.selected_squad_id), None)

    async def _on_prev_squad_clicked(self, interaction: discord.Interaction) -> None:
        if self.squad_page > 0:
            self.squad_page -= 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_next_squad_clicked(self, interaction: discord.Interaction) -> None:
        total_squad_pages = max(1, math.ceil(len(self.squads) / 25))
        if self.squad_page < total_squad_pages - 1:
            self.squad_page += 1
            self._rebuild_items()
            await interaction.response.edit_message(view=self)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        if self.proj_select.values and self.proj_select.values[0] != "none":
            self.selected_project_id = UUID(self.proj_select.values[0])
            for opt in self.proj_select.options:
                opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.edit_message(view=self)

    async def _on_squad_changed(self, interaction: discord.Interaction) -> None:
        if self.squad_select.values and self.squad_select.values[0] != "none":
            self.selected_squad_id = UUID(self.squad_select.values[0])
            for opt in self.squad_select.options:
                opt.default = opt.value == str(self.selected_squad_id)
        await interaction.response.edit_message(view=self)

    async def _on_assign_quick_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        squad = self._get_selected_squad()
        if not proj or not squad:
            await interaction.response.send_message("❌ Selection error.", ephemeral=True)
            return

        try:
            await self.project_service.assign_squad_to_project(
                project_id=proj.id,
                squad_id=squad.id,
                timeline=None,
            )
            embed = discord.Embed(
                title="Squad Mapped to Project",
                description=(
                    f"Successfully mapped squad **{squad.name}** (<@&{squad.discord_role_id}>) "
                    f"to project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.green(),
            )
            view = ProjectMenuView(
                self.project_service,
                self.squad_service,
                self.task_service,
                initial_interaction=interaction,
            )
            embed = build_project_menu_embed(view.is_server_manager)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"assigning squad '{squad.name}' to project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_timeline_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        squad = self._get_selected_squad()
        if not proj or not squad:
            await interaction.response.send_message("❌ Selection error.", ephemeral=True)
            return

        modal = ProjectAssignTimelineModal(
            project_service=self.project_service,
            project=proj,
            squad=squad,
            squad_service=self.squad_service,
            task_service=self.task_service,
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectRoleSelectView(BaseView):
    """Interactive view to map or clear a contributor Discord role for a project."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction

        self.selected_project_id: UUID = projects[0].id
        self.selected_role: discord.Role | None = None
        self.selected_roles: list[discord.Role] = []

        # Row 0: Select Project
        proj_options = []
        for i, p in enumerate(projects[:25]):
            role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
            roles_desc = f"Roles: {len(role_ids)}" if role_ids else "Public (No roles)"
            proj_options.append(
                discord.SelectOption(
                    label=f"{p.name} ({p.prefix})"[:100],
                    value=str(p.id),
                    description=roles_desc[:50],
                    default=(i == 0),
                )
            )
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Discord Role(s)
        self.role_select = discord.ui.RoleSelect(
            placeholder="Select Squad Discord Role(s)...",
            min_values=1,
            max_values=25,
            row=1,
        )
        self.role_select.callback = self._on_role_selected
        self.add_item(self.role_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Add Squad Roles",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_clicked
        self.add_item(self.assign_btn)

        self.remove_btn = discord.ui.Button(
            label="Remove Squad Roles",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        self.remove_btn.callback = self._on_remove_clicked
        self.add_item(self.remove_btn)

        self.clear_btn = discord.ui.Button(
            label="Clear All Roles",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.clear_btn.callback = self._on_clear_clicked
        self.add_item(self.clear_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=2,
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

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        self.selected_project_id = UUID(self.proj_select.values[0])
        for opt in self.proj_select.options:
            opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.defer()

    async def _on_role_selected(self, interaction: discord.Interaction) -> None:
        self.selected_roles = list(self.role_select.values)
        self.selected_role = self.selected_roles[0] if self.selected_roles else None
        await interaction.response.defer()

    async def _on_assign_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        roles_to_add: list[discord.Role] = list(self.role_select.values) if self.role_select.values else []
        if not roles_to_add and self.selected_roles:
            roles_to_add = list(self.selected_roles)
        elif not roles_to_add and self.selected_role:
            roles_to_add = [self.selected_role]

        if not roles_to_add:
            await interaction.response.send_message(
                "❌ Please select at least one Discord role to map to this project.", ephemeral=True
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        try:
            guild_id = (
                interaction.guild.id
                if (interaction.guild and isinstance(getattr(interaction.guild, "id", None), int))
                else proj.guild_id
            )
            if self.squad_service:
                existing_squads = await self.project_service.list_squads_for_project(proj.id)
                for s in existing_squads:
                    await self.project_service.remove_squad_from_project(proj.id, s.id)

                for r in roles_to_add:
                    r_name = str(getattr(r, "name", None) or f"Squad-{r.id}")
                    if "Mock" in r_name or "<MagicMock" in r_name:
                        r_name = f"Squad-{r.id}"
                    squad = await self.squad_service.get_or_create_squad_for_role(guild_id, r.id, r_name)
                    await self.project_service.assign_squad_to_project(proj.id, squad.id)
            else:
                await self.project_service.set_project_role(proj.id, roles_to_add[0].id)

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles_to_add)
            embed = discord.Embed(
                title="Squad Roles Mapped to Project",
                description=(
                    f"Mapped Discord role(s) {role_mentions} as contributor squads for "
                    f"project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.green(),
            )
            view = ProjectMenuView(
                self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"mapping roles for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_remove_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        roles_to_remove: list[discord.Role] = list(self.role_select.values) if self.role_select.values else []
        if not roles_to_remove and self.selected_roles:
            roles_to_remove = list(self.selected_roles)
        elif not roles_to_remove and self.selected_role:
            roles_to_remove = [self.selected_role]

        if not roles_to_remove:
            await interaction.response.send_message(
                "❌ Please select at least one Discord role to remove from this project.", ephemeral=True
            )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        try:
            guild_id = (
                interaction.guild.id
                if (interaction.guild and isinstance(getattr(interaction.guild, "id", None), int))
                else proj.guild_id
            )
            for r in roles_to_remove:
                if self.squad_service:
                    squad = await self.squad_service.get_by_role_id(guild_id, r.id)
                    if squad:
                        await self.project_service.remove_squad_from_project(proj.id, squad.id)

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles_to_remove)
            embed = discord.Embed(
                title="Squad Roles Removed from Project",
                description=(
                    f"Removed Discord role(s) {role_mentions} from project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.orange(),
            )
            view = ProjectMenuView(
                self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"removing roles from project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_clear_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        try:
            if self.squad_service:
                squads = await self.project_service.list_squads_for_project(proj.id)
                for s in squads:
                    await self.project_service.remove_squad_from_project(proj.id, s.id)
            await self.project_service.set_project_role(proj.id, None)

            embed = discord.Embed(
                title="Project Role Cleared",
                description=(
                    f"Project **{proj.name}** (`{proj.prefix}`) is now **Public** (open to all server members)."
                ),
                color=discord.Color.blue(),
            )
            view = ProjectMenuView(
                self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
            )
            embed = build_project_menu_embed(view.is_server_manager)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"clearing role for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectLeadSelectView(BaseView):
    """Interactive view to designate or clear a Project Lead for a project."""

    def __init__(
        self,
        projects: list[Project],
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.projects = projects
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self._initial_interaction = initial_interaction

        self.selected_project_id: UUID = projects[0].id
        self.selected_user: discord.Member | discord.User | None = None

        # Row 0: Select Project
        proj_options = [
            discord.SelectOption(
                label=f"{p.name} ({p.prefix})"[:100],
                value=str(p.id),
                description=(f"Lead: @{p.lead_discord_id}" if p.lead_discord_id else "Unassigned")[:50],
                default=(i == 0),
            )
            for i, p in enumerate(projects[:25])
        ]
        self.proj_select = discord.ui.Select(
            placeholder="Select Project...",
            options=proj_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.proj_select.callback = self._on_project_changed
        self.add_item(self.proj_select)

        # Row 1: Select Lead Member
        self.user_select = discord.ui.UserSelect(
            placeholder="Select Project Lead...",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.user_select.callback = self._on_user_selected
        self.add_item(self.user_select)

        # Row 2: Action Buttons
        self.assign_btn = discord.ui.Button(
            label="Designate Lead",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.assign_btn.callback = self._on_assign_clicked
        self.add_item(self.assign_btn)

        self.clear_btn = discord.ui.Button(
            label="Clear Lead",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.clear_btn.callback = self._on_clear_clicked
        self.add_item(self.clear_btn)

        self.back_btn = discord.ui.Button(
            label="Back to Project Menu",
            style=discord.ButtonStyle.secondary,
            row=2,
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

    def _get_selected_project(self) -> Project | None:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    async def _on_project_changed(self, interaction: discord.Interaction) -> None:
        self.selected_project_id = UUID(self.proj_select.values[0])
        for opt in self.proj_select.options:
            opt.default = opt.value == str(self.selected_project_id)
        await interaction.response.defer()

    async def _on_user_selected(self, interaction: discord.Interaction) -> None:
        self.selected_user = self.user_select.values[0]
        await interaction.response.defer()

    async def _on_assign_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        if not self.selected_user:
            if self.user_select.values:
                self.selected_user = self.user_select.values[0]
            else:
                await interaction.response.send_message(
                    "❌ Please select a Discord member to designate as Lead.", ephemeral=True
                )
                from src.adapters.discord_bot.menu_manager import menu_manager

                menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
                return

        try:
            await self.project_service.set_project_lead(proj.id, self.selected_user.id)
            embed = discord.Embed(
                title="Project Lead Designated",
                description=(
                    f"Designated <@{self.selected_user.id}> as the **Project Lead** for "
                    f"project **{proj.name}** (`{proj.prefix}`)."
                ),
                color=discord.Color.green(),
            )
            view = ProjectMenuView(
                self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
            )
            embed = build_project_menu_embed(view.is_server_manager)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"designating lead for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_clear_clicked(self, interaction: discord.Interaction) -> None:
        proj = self._get_selected_project()
        if not proj:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        try:
            await self.project_service.set_project_lead(proj.id, None)
            embed = discord.Embed(
                title="Project Lead Cleared",
                description=f"Project **{proj.name}** (`{proj.prefix}`) has no designated Project Lead.",
                color=discord.Color.dark_grey(),
            )
            view = ProjectMenuView(
                self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
            )
            embed = build_project_menu_embed(view.is_server_manager)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"clearing lead for project '{proj.name}'", logger, ephemeral=True
            )

    async def _on_back_clicked(self, interaction: discord.Interaction) -> None:
        view = ProjectMenuView(
            self.project_service, self.squad_service, self.task_service, initial_interaction=interaction
        )
        embed = build_project_menu_embed(view.is_server_manager)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ProjectMenuView(BaseView):
    """Control Center View for Project Operations."""

    @classmethod
    async def create(
        cls,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        is_server_manager: bool | None = None,
        user_service: UserService | None = None,
        return_to: str = "dashboard",
        auth_service: AuthService | None = None,
    ) -> ProjectMenuView:
        effective_user = user or (initial_interaction.user if initial_interaction else None)
        guild_id = None
        if initial_interaction and initial_interaction.guild:
            guild_id = initial_interaction.guild.id
        elif hasattr(effective_user, "guild") and getattr(effective_user, "guild", None):
            guild_id = getattr(effective_user.guild, "id", None)
        if is_server_manager is None and auth_service and effective_user and guild_id:
            is_server_manager = await auth_service.can_manage_projects(effective_user, guild_id)
        return cls(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            initial_interaction=initial_interaction,
            user=user,
            is_server_manager=is_server_manager,
            user_service=user_service,
            return_to=return_to,
            auth_service=auth_service,
        )

    def __init__(
        self,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        is_server_manager: bool | None = None,
        user_service: UserService | None = None,
        return_to: str = "dashboard",
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=180)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to
        self.auth_service = auth_service
        self._initial_interaction = initial_interaction

        effective_user = user or (initial_interaction.user if initial_interaction else None)
        if is_server_manager is not None:
            self.is_server_manager = is_server_manager
        elif effective_user is not None:
            from src.services.auth_service import AuthService

            self.is_server_manager = AuthService.is_server_manager(effective_user)
        else:
            self.is_server_manager = True

        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()

        if self.is_server_manager:
            self.new_project_btn = discord.ui.Button(
                label="Create Project",
                style=discord.ButtonStyle.primary,
                row=0,
            )
            self.new_project_btn.callback = self._on_new_project_clicked
            self.add_item(self.new_project_btn)
        else:
            self.new_project_btn = None

        # Row 0: Active Projects (Always visible)
        self.list_projects_btn = discord.ui.Button(
            label="Active Projects",
            style=discord.ButtonStyle.secondary if self.is_server_manager else discord.ButtonStyle.primary,
            row=0,
        )
        self.list_projects_btn.callback = self._on_list_projects_clicked
        self.add_item(self.list_projects_btn)

        if self.is_server_manager:
            self.set_role_btn = discord.ui.Button(
                label="Set Squad Role",
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            self.set_role_btn.callback = self._on_set_role_clicked
            self.add_item(self.set_role_btn)

            self.set_lead_btn = discord.ui.Button(
                label="Set Project Lead",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.set_lead_btn.callback = self._on_set_lead_clicked
            self.add_item(self.set_lead_btn)

            self.archive_btn = discord.ui.Button(
                label="Archive Project",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.archive_btn.callback = self._on_archive_clicked
            self.add_item(self.archive_btn)

            self.restore_btn = discord.ui.Button(
                label="Restore Project",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            self.restore_btn.callback = self._on_restore_clicked
            self.add_item(self.restore_btn)

            self.rebuild_btn = discord.ui.Button(
                label="Rebuild Workspace",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            self.rebuild_btn.callback = self._on_rebuild_clicked
            self.add_item(self.rebuild_btn)
        else:
            self.set_role_btn = None
            self.set_lead_btn = None
            self.archive_btn = None
            self.restore_btn = None
            self.rebuild_btn = None

        if self.task_service:
            self.tech_tree_btn = discord.ui.Button(
                label="Visual Graph",
                style=discord.ButtonStyle.secondary,
                row=2 if self.is_server_manager else 0,
            )
            self.tech_tree_btn.callback = self._on_tech_tree_clicked
            self.add_item(self.tech_tree_btn)

            self.hub_btn = discord.ui.Button(
                label="PM Main Menu",
                style=discord.ButtonStyle.secondary,
                row=2 if self.is_server_manager else 0,
            )
            self.hub_btn.callback = self._on_hub_clicked
            self.add_item(self.hub_btn)
        else:
            self.tech_tree_btn = None
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

    async def _on_tech_tree_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not self.task_service:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("ℹ️ No active projects found in this server.", ephemeral=True)
            return

        from src.services.auth_service import AuthService

        auth_service = AuthService(self.project_service, self.squad_service)
        accessible_projects = []
        for p in projects:
            if await auth_service.can_view_project(interaction.user, p.id):
                accessible_projects.append(p)

        if not accessible_projects:
            await interaction.response.send_message(
                "❌ You do not have permission to view any project visual graphs.\n"
                "You must hold a project squad's Discord role, be a Project Lead, or be a server manager.",
                ephemeral=True,
            )
            return

        if len(accessible_projects) == 1:
            target_proj = accessible_projects[0]
            await interaction.response.defer(ephemeral=True)
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=target_proj.id,
                orientation="lr",
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            embed = discord.Embed(
                title=f"Visual Graph: [{target_proj.prefix}] {target_proj.name}",
                description="Showing dependency graph in **Horizontal (Left to Right)** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            from src.adapters.discord_bot.views.tree_view import TechTreeViewer

            view = TechTreeViewer(self.task_service, target_proj, current_orientation="lr", auth_service=auth_service)
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
            return

        from src.adapters.discord_bot.views.tree_view import TechTreeProjectSelectView

        view = TechTreeProjectSelectView(
            self.task_service, self.project_service, accessible_projects, orientation="lr", auth_service=auth_service
        )
        await interaction.response.send_message(
            "Choose a project to view its Visual Graph visualization:",
            view=view,
            ephemeral=True,
        )

    async def _on_hub_clicked(self, interaction: discord.Interaction) -> None:
        if self.return_to == "dashboard" and interaction.guild:
            from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed
            from src.domain.enums import NotificationPreference

            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
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

            if self.task_service:
                view = PmHubView(
                    self.project_service,
                    self.squad_service,
                    self.task_service,
                    self.user_service,
                    auth_service=self.auth_service,
                )
                embed = build_hub_welcome_embed()
                await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_new_project_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run in a Discord server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectChannelSelectView(
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
            user_service=self.user_service,
            return_to=self.return_to,
        )
        embed = discord.Embed(
            title="Create Project: Select Forum Channel",
            description=(
                "Choose a **Forum Channel** to bind as the project's task board.\n\n"
                "Tasks become organized forum post cards with native Discord tag filtering "
                "and an interactive pinned Control Hub."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_list_projects_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found in this server.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        embed = build_active_projects_embed(projects, page=0, total_count=len(projects))
        view = ProjectActiveListView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_role_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found. Create a project first!", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectRoleSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Map Squad Discord Role to Project",
            description="Select a project and choose a Discord role as its dedicated contributor squad:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_lead_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects found. Create a project first!", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectLeadSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Designate Project Lead",
            description="Select a project and designate a member as the Project Lead with management permissions:",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_archive_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
        if not projects:
            await interaction.response.send_message("No active projects available to archive.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectArchiveSelectView(
            projects,
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        total_pages = max(1, math.ceil(len(projects) / ProjectArchiveSelectView.PAGE_SIZE))
        embed = build_archive_select_embed(len(projects), 0, total_pages)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_restore_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        all_proj = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
        archived = [p for p in all_proj if p.is_archived]
        if not archived:
            await interaction.response.send_message("No archived projects to restore.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return
        view = ProjectRestoreSelectView(
            archived,
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        total_pages = max(1, math.ceil(len(archived) / ProjectRestoreSelectView.PAGE_SIZE))
        embed = build_restore_select_embed(len(archived), 0, total_pages)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_rebuild_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        all_proj = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
        if not all_proj:
            await interaction.response.send_message("No projects found to rebuild.", ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
            return

        client = getattr(interaction, "client", None)
        workspace = getattr(client, "project_workspace", None)
        if not workspace:
            workspace = DiscordProjectWorkspaceAdapter(
                bot=client,
                project_service=self.project_service,
                squad_service=self.squad_service,
                task_service=self.task_service,
                user_service=self.user_service,
            )

        view = ProjectRebuildSelectView(
            all_proj,
            project_service=self.project_service,
            project_workspace=workspace,
            squad_service=self.squad_service,
            task_service=self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        embed = discord.Embed(
            title="Rebuild Project Discord Workspace",
            description=(
                "Select a project to restore and reconcile its Discord presence.\n\n"
                "• **Forum Channel:** Verified or auto-provisioned\n"
                "• **Tags & Hub:** Standard tags and Pinned Control Hub refreshed\n"
                "• **Task Threads:** Intact threads synced, missing threads re-created\n"
                "• **Archive Invariant:** Completed tasks preserved and archived"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


def build_project_menu_embed(is_server_manager: bool = True) -> discord.Embed:
    embed = discord.Embed(
        title="Project Management Hub",
        color=discord.Color.dark_theme(),
    )
    if is_server_manager:
        embed.description = (
            "> **Project Administration & Squad Routing**\n"
            "> Manage project containers, channel bindings, squad roles, and project leads.\n\n"
            "• **`Create Project`**: Create a project container bound to a Forum channel\n"
            "• **`Active Projects`**: View all running projects, squad roles, and designated leads\n"
            "• **`Set Squad Role`**: Map a Discord role as the project's contributor squad\n"
            "• **`Set Project Lead`**: Designate the project owner / lead with elevated permissions\n"
            "• **`Archive Project`**: Soft-delete a completed project\n"
            "• **`Restore Project`**: Bring back an archived project\n"
            "• **`Rebuild Workspace`**: Reconstruct forum channel, tags, hub, and task threads"
        )
    else:
        embed.description = (
            "> **Active Project Containers**\n"
            "> Browse active project containers, bound channels, and designated squad roles.\n\n"
            "• **`Active Projects`**: View all running projects, squad roles, and designated leads"
        )
    embed.set_footer(text="dgg-pm • Discord-Native Project Management")
    return embed
