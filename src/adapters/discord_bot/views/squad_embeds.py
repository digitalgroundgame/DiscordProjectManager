"""Squad view embeds."""

from __future__ import annotations

import discord


def build_squad_menu_embed(
    can_create_squads: bool = True,
    can_assign_members: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title="Squad Management Hub",
        color=discord.Color.dark_theme(),
    )
    bullets = []
    if can_create_squads:
        bullets.append("• **`Create Squad`**: Define a new squad and map it to a Discord role")
    if can_assign_members:
        bullets.append("• **`Assign Member`**: Pick a user and assign them as a Squad Member or Lead")
    bullets.append("• **`Squad Roster`**: View all squads and inspect verified members and leads")

    if can_create_squads or can_assign_members:
        desc = (
            "> **Contributor Squads & Permission Mappings**\n"
            "> Configure functional squads, map Discord roles, and assign squad leads.\n\n" + "\n".join(bullets)
        )
    else:
        desc = "> **Server Squad Rosters**\n> Inspect configured functional squads and rosters.\n\n" + "\n".join(
            bullets
        )

    embed.description = desc
    embed.set_footer(text="dgg-pm • Discord-Native Squad Management")
    return embed


__all__ = ["build_squad_menu_embed"]
