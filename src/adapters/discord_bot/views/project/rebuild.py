from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS
from src.adapters.discord_bot.views.base_view import BaseView
from src.adapters.discord_bot.workspace_protocol import (
    IProjectDiscordWorkspace,
    RebuildProgress,
)
from src.domain.models import Project
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.project")


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
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=interaction)
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

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
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
        timeout: float | None = EPHEMERAL_MENU_TIMEOUT_SECONDS,
    ):
        super().__init__(timeout=timeout, initial_interaction=initial_interaction)
        self.projects = projects
        self.project_service = project_service
        self.project_workspace = project_workspace
        self.squad_service = squad_service
        self.task_service = task_service
        self.user_service = user_service

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
        embed, view = DiscordViewRouter.build_project_menu(
            self.project_service,
            self.squad_service,
            self.task_service,
            user_service=self.user_service,
            initial_interaction=interaction,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)
