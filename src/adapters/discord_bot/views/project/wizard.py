from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    ProjectProvisionSpec,
)
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.project")


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
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
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
            embed, view = DiscordViewRouter.build_project_menu(
                self.project_service,
                self.squad_service,
                self.task_service,
                user_service=self.user_service,
                initial_interaction=interaction,
                return_to=self.return_to,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=view)
