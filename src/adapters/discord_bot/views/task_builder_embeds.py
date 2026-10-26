"""Task draft builder embeds."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord

from src.domain.enums import PriorityLevel

if TYPE_CHECKING:
    from src.domain.models import Project


def build_task_draft_embed(
    title: str,
    description: str | None = None,
    project: Project | None = None,
    assignee_id: int | None = None,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    due_at: datetime | None = None,
    watchers: list[int] | None = None,
    target_channel: discord.abc.GuildChannel | discord.Thread | None = None,
    prerequisite_short_ids: list[str] | None = None,
) -> discord.Embed:
    """Builds a live preview embed of the task currently being configured in the builder."""
    proj_display = f"[{project.prefix}] {project.name}" if project else "Standalone Task (Ad-hoc)"
    assignee_display = f"<@{assignee_id}>" if assignee_id else "*Unassigned (Click member picker below)*"

    if priority == PriorityLevel.HIGH:
        priority_display = "High"
    elif priority == PriorityLevel.LOW:
        priority_display = "Low"
    else:
        priority_display = "Normal"

    if due_at:
        due_ts = int(due_at.astimezone(UTC).timestamp())
        due_display = f"<t:{due_ts}:f> (<t:{due_ts}:R>)"
    else:
        due_display = "*No due date (Click preset picker below)*"

    watchers_display = " ".join(f"<@{uid}>" for uid in watchers) if watchers else "*None*"
    prereqs_display = ", ".join(f"`[{p}]`" for p in prerequisite_short_ids) if prerequisite_short_ids else "*None*"

    chan_name = getattr(target_channel, "name", None)
    chan_display = f"#{chan_name}" if chan_name else "Current Channel"

    embed = discord.Embed(
        title=f"Task Draft: {title[:90]}",
        description=(
            "Configure optional assignee, deadline, and priority using the pickers below, "
            "then click **`Create Task`** to publish.\n\n"
            f"• **Project Scope**: {proj_display}\n"
            f"• **Assignee**: {assignee_display}\n"
            f"• **Priority**: {priority_display}\n"
            f"• **Due Date**: {due_display}\n"
            f"• **Prerequisites**: {prereqs_display}\n"
            f"• **Watchers (CC)**: {watchers_display}\n"
            f"• **Publish Target**: `{chan_display}`"
        ),
        color=discord.Color.blurple(),
    )

    if description:
        embed.add_field(
            name="Description / Details",
            value=description[:1000] + ("..." if len(description) > 1000 else ""),
            inline=False,
        )

    embed.set_footer(text="dgg-pm • Interactive Task Creation Builder")
    return embed


__all__ = ["build_task_draft_embed"]
