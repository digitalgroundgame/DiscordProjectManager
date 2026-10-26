"""Embed builders for forum control hub."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import discord

if TYPE_CHECKING:
    from src.domain.models import Project, Squad


def _format_squad_roles(squads: list[Squad] | None) -> str:
    """Formats list of squad roles as Discord mentions or fallback labels."""
    if not squads:
        return "**Squad Role**: *Open (No role required)*"

    role_mentions: list[str] = []
    for s in squads:
        if s.discord_role_id and s.discord_role_id > 0:
            role_mentions.append(f"<@&{s.discord_role_id}>")
        else:
            role_mentions.append(f"**{s.name}**")

    if len(role_mentions) == 1:
        return f"**Squad Role**: {role_mentions[0]}"
    return f"**Squad Roles**: {', '.join(role_mentions)}"


def build_hub_welcome_embed(
    channel_name: str | None = None,
    bound_projects: list[Project] | None = None,
    project_squads_map: dict[UUID, list[Squad]] | None = None,
) -> discord.Embed:
    if channel_name:
        title = f"#{channel_name} • Forum Control Hub"
    else:
        title = "Forum Control Hub"

    embed = discord.Embed(
        title=title,
        color=discord.Color.dark_theme(),
    )

    if bound_projects:
        if len(bound_projects) == 1:
            p = bound_projects[0]
            squads = (project_squads_map or {}).get(p.id)
            role_line = _format_squad_roles(squads)
            desc_text = p.description or "Active contributor task feed and discussion board."
            desc = (
                f"**[{p.prefix}] {p.name}**\n"
                f"> {role_line}\n"
                f"> *{desc_text}*\n\n"
                "Click any button below to manage tasks, explore dependencies, or view projects."
            )
        else:
            project_blocks: list[str] = []
            for p in bound_projects:
                squads = (project_squads_map or {}).get(p.id)
                role_line = _format_squad_roles(squads)
                desc_text = f"> *{p.description}*\n" if p.description else ""
                project_blocks.append(f"**[{p.prefix}] {p.name}**\n> {role_line}\n{desc_text}".rstrip())

            projects_summary = "\n\n".join(project_blocks)
            desc = (
                f"### Bound Projects in this Channel\n\n"
                f"{projects_summary}\n\n"
                "Click any button below to manage tasks, explore dependencies, or view projects."
            )
    else:
        desc = (
            "> Welcome to **dgg-pm**! Interactive project and task management.\n\n"
            "Click any button below to launch a private interactive workspace."
        )

    embed.description = desc
    embed.add_field(
        name="New Task",
        value="Create a new task card within this forum channel.",
        inline=True,
    )
    embed.add_field(
        name="Visual Graph",
        value="Render the visual dependency DAG diagram.",
        inline=True,
    )
    embed.add_field(
        name="My Tasks",
        value="Filter to your personalized assigned tasks and deadlines.",
        inline=True,
    )
    embed.add_field(
        name="Projects Hub",
        value="Browse project settings, squads, and project leads.",
        inline=True,
    )
    embed.set_footer(text="dgg-pm • Discord-Native Project Management")
    return embed


__all__ = [
    "_format_squad_roles",
    "build_hub_welcome_embed",
]
