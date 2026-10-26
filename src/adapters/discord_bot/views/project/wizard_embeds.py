"""Embed builders for project creation wizard."""

from __future__ import annotations

import discord


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
