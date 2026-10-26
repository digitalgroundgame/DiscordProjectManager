from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from src.services.task_service import parse_prerequisite_short_ids

logger = logging.getLogger("dgg_pm.cogs.autocomplete")


class AutocompleteMixin:
    """Shared autocomplete resolvers for slash commands."""

    async def project_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            choices = [
                app_commands.Choice(name=f"{p.name} ({p.prefix})", value=p.name)
                for p in projects
                if not current or current.lower() in p.name.lower() or current.lower() in p.prefix.lower()
            ]
            return choices[:25]
        except Exception:
            return []

    async def task_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            raw_current = current or ""
            if "," in raw_current:
                prefix_parts = [p.strip() for p in raw_current.split(",")[:-1] if p.strip()]
                search_term = raw_current.split(",")[-1].strip()
                prefix_str = ", ".join(prefix_parts) + ", " if prefix_parts else ""
                already_picked = {p.upper() for p in parse_prerequisite_short_ids(prefix_parts)}
            else:
                search_term = raw_current.strip()
                prefix_str = ""
                already_picked = set()

            project_id = None
            ns = getattr(interaction, "namespace", None)
            proj_name = getattr(ns, "project_name", None) if ns else None
            if isinstance(proj_name, str) and proj_name.strip() and self.project_service:
                try:
                    proj = await self.project_service.get_by_name(interaction.guild.id, proj_name.strip())
                    if proj:
                        project_id = proj.id
                except Exception:
                    project_id = None

            list_kwargs: dict[str, Any] = {
                "guild_id": interaction.guild.id,
                "exclude_completed": False,
                "limit": 50,
            }
            if project_id is not None:
                list_kwargs["project_id"] = project_id

            tasks, _ = await self.task_service.list_tasks(**list_kwargs)
            if search_term:
                q = search_term.lower().lstrip("#[")
                tasks = [t for t in tasks if q in t.short_id.lower() or q in t.title.lower()]

            filtered = [t for t in tasks if t.short_id.upper() not in already_picked][:25]
            return [
                app_commands.Choice(
                    name=f"{prefix_str}[{t.short_id}] {t.title}"[:100],
                    value=f"{prefix_str}{t.short_id}"[:100],
                )
                for t in filtered
            ]
        except Exception:
            return []

    async def squad_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        try:
            squads = await self.squad_service.list_squads(interaction.guild.id)
            return [
                app_commands.Choice(name=s.name, value=s.name)
                for s in squads
                if not current or current.lower() in s.name.lower()
            ][:25]
        except Exception:
            return []
