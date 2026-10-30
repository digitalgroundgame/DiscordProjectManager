"""Modal and channel select components for project creation wizard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS, TOAST_DURATION_STANDARD
from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.adapters.discord_bot.views.project.wizard_embeds import build_project_draft_embed

if TYPE_CHECKING:
    from src.adapters.discord_bot.views.project.wizard import ProjectCreateDraftView
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService


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
                    format_toast_message("❌ Project name cannot be empty.", delay=TOAST_DURATION_STANDARD),
                    ephemeral=True,
                    wait=True,
                )
                menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_STANDARD)
            elif self.parent_view:
                if hasattr(self.parent_view, "_rebuild_items"):
                    self.parent_view._rebuild_items()
                embed = self.parent_view.build_embed() if hasattr(self.parent_view, "build_embed") else None
                if embed:
                    await interaction.response.edit_message(embed=embed, view=self.parent_view)
                else:
                    await interaction.response.edit_message(view=self.parent_view)
                toast = await interaction.followup.send(
                    format_toast_message("❌ Project name cannot be empty.", delay=TOAST_DURATION_STANDARD),
                    ephemeral=True,
                    wait=True,
                )
                menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_STANDARD)
            else:
                await interaction.response.send_message("❌ Project name cannot be empty.", ephemeral=True)
                menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
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
            from src.adapters.discord_bot.views.project.wizard import ProjectCreateDraftView

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
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.project_service = project_service
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service
        self.return_to = return_to
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
                format_toast_message("❌ Selected channel must be a Forum Channel.", delay=TOAST_DURATION_STANDARD),
                ephemeral=True,
                wait=True,
            )
            menu_manager.schedule_toast_dismissal(toast, delay=TOAST_DURATION_STANDARD)
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

            menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)
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
