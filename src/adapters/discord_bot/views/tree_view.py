from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.models import Project

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.tree_view")


class TechTreeViewer(BaseView):
    """Interactive view for displaying and switching orientation and mode of a project Tech Tree or Timeline."""

    def __init__(
        self,
        task_service: TaskService,
        project: Project,
        current_orientation: str = "lr",
        current_mode: str = "tree",
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=300)
        self.task_service = task_service
        self.project = project
        self.current_orientation = current_orientation
        self.current_mode = current_mode
        self.auth_service = auth_service

        # Mode toggles (Row 0)
        self.tree_btn = discord.ui.Button(
            label="Tech Tree",
            emoji="🌲",
            style=discord.ButtonStyle.primary if current_mode == "tree" else discord.ButtonStyle.secondary,
            row=0,
        )
        self.tree_btn.callback = self._on_tree_clicked
        self.add_item(self.tree_btn)

        self.timeline_btn = discord.ui.Button(
            label="Timeline",
            emoji="📊",
            style=discord.ButtonStyle.primary if current_mode == "timeline" else discord.ButtonStyle.secondary,
            row=0,
        )
        self.timeline_btn.callback = self._on_timeline_clicked
        self.add_item(self.timeline_btn)

        # Orientation toggles (Row 1)
        self.lr_btn = discord.ui.Button(
            label="Horizontal",
            emoji="↔️",
            style=discord.ButtonStyle.primary if current_orientation == "lr" else discord.ButtonStyle.secondary,
            disabled=current_mode != "tree",
            row=1,
        )
        self.lr_btn.callback = self._on_lr_clicked
        self.add_item(self.lr_btn)

        self.tb_btn = discord.ui.Button(
            label="Vertical",
            emoji="↕️",
            style=discord.ButtonStyle.primary if current_orientation == "tb" else discord.ButtonStyle.secondary,
            disabled=current_mode != "tree",
            row=1,
        )
        self.tb_btn.callback = self._on_tb_clicked
        self.add_item(self.tb_btn)

        self.mermaid_btn = discord.ui.Button(
            label="Export Mermaid",
            emoji="📝",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.mermaid_btn.callback = self._on_mermaid_clicked
        self.add_item(self.mermaid_btn)

    async def _on_tree_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_mode == "tree":
            await interaction.response.defer()
            return
        self.current_mode = "tree"
        await self._rerender(interaction)

    async def _on_timeline_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_mode == "timeline":
            await interaction.response.defer()
            return
        self.current_mode = "timeline"
        await self._rerender(interaction)

    async def _on_lr_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_orientation == "lr":
            await interaction.response.defer()
            return
        self.current_orientation = "lr"
        await self._rerender(interaction)

    async def _on_tb_clicked(self, interaction: discord.Interaction) -> None:
        if self.current_orientation == "tb":
            await interaction.response.defer()
            return
        self.current_orientation = "tb"
        await self._rerender(interaction)

    async def _on_mermaid_clicked(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        if self.current_mode == "timeline":
            code = await self.task_service.export_project_timeline_mermaid(
                guild_id=interaction.guild.id, project_id=self.project.id
            )
        else:
            tree = await self.task_service.get_project_tech_tree(
                guild_id=interaction.guild.id, project_id=self.project.id, member_resolver=interaction.guild
            )
            code = tree.to_mermaid(orientation=self.current_orientation)

        await interaction.followup.send(f"```mermaid\n{code}\n```", ephemeral=True)

    async def _rerender(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        if self.auth_service:
            if not await self.auth_service.can_view_project(interaction.user, self.project.id):
                await interaction.response.send_message(
                    "❌ You do not have permission to view this project's visual graph.\n"
                    "You must hold the project squad's Discord role, be the Project Lead, or be a server manager.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()

        # Update button styles
        self.tree_btn.style = (
            discord.ButtonStyle.primary if self.current_mode == "tree" else discord.ButtonStyle.secondary
        )
        self.timeline_btn.style = (
            discord.ButtonStyle.primary if self.current_mode == "timeline" else discord.ButtonStyle.secondary
        )
        self.lr_btn.disabled = self.current_mode != "tree"
        self.tb_btn.disabled = self.current_mode != "tree"
        self.lr_btn.style = (
            discord.ButtonStyle.primary if self.current_orientation == "lr" else discord.ButtonStyle.secondary
        )
        self.tb_btn.style = (
            discord.ButtonStyle.primary if self.current_orientation == "tb" else discord.ButtonStyle.secondary
        )

        if self.current_mode == "timeline":
            buf = await self.task_service.render_project_timeline(
                guild_id=interaction.guild.id,
                project_id=self.project.id,
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="project_timeline.png")
            embed = discord.Embed(
                title=f"📊 Timeline: [{self.project.prefix}] {self.project.name}",
                description="Showing project execution schedule across calendar time.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://project_timeline.png")
        else:
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=self.project.id,
                orientation=self.current_orientation,
                member_resolver=interaction.guild,
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            orient_label = (
                "Horizontal (Left to Right)" if self.current_orientation == "lr" else "Vertical (Top to Bottom)"
            )
            embed = discord.Embed(
                title=f"🌲 Tech Tree: [{self.project.prefix}] {self.project.name}",
                description=f"Showing dependency graph in **{orient_label}** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")

        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)


class TechTreeProjectSelectView(BaseView):
    """Dropdown selector to choose which project's tech tree to render."""

    def __init__(
        self,
        task_service: TaskService,
        project_service: ProjectService,
        projects: list[Project],
        orientation: str = "lr",
        auth_service: AuthService | None = None,
    ):
        super().__init__(timeout=120)
        self.task_service = task_service
        self.project_service = project_service
        self.projects = projects
        self.orientation = orientation
        self.auth_service = auth_service

        options: list[discord.SelectOption] = []
        for p in projects[:25]:
            desc = (
                (p.description[:85] + "...")
                if p.description and len(p.description) > 85
                else (p.description or f"Prefix: {p.prefix}")
            )
            options.append(
                discord.SelectOption(
                    label=f"[{p.prefix}] {p.name}"[:100],
                    value=str(p.id),
                    description=desc,
                    emoji="📁",
                )
            )

        if options:
            self.select = discord.ui.Select(
                placeholder="🌲 Select Project to View Tech Tree...",
                options=options,
                row=0,
            )
            self.select.callback = self._on_select_project
            self.add_item(self.select)

    async def _on_select_project(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        values = getattr(self.select, "values", None) or (interaction.data.get("values") if interaction.data else None)
        if not values:
            return
        selected_id = UUID(values[0])
        project = next((p for p in self.projects if p.id == selected_id), None)
        if not project:
            await interaction.response.send_message("❌ Project not found.", ephemeral=True)
            return

        if self.auth_service:
            if not await self.auth_service.can_view_project(interaction.user, project.id):
                await interaction.response.send_message(
                    f"❌ You do not have permission to view the visual graph for **{project.name}**.\n"
                    "You must hold the project squad's Discord role, be the Project Lead, or be a server manager.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()
        buf = await self.task_service.render_project_tree(
            guild_id=interaction.guild.id,
            project_id=project.id,
            orientation=self.orientation,
            member_resolver=interaction.guild,
        )
        file = discord.File(fp=buf, filename="tech_tree.png")
        orient_label = "Horizontal (Left to Right)" if self.orientation == "lr" else "Vertical (Top to Bottom)"
        embed = discord.Embed(
            title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
            description=f"Showing dependency graph in **{orient_label}** layout.",
            color=discord.Color.from_rgb(16, 152, 247),
        )
        embed.set_image(url="attachment://tech_tree.png")
        view = TechTreeViewer(
            self.task_service, project, current_orientation=self.orientation, auth_service=self.auth_service
        )
        await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
