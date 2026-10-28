"""Adapter mapper translating Discord Interaction and Member SDK types into pure ActorPrincipals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.actor import ActorPrincipal
from src.ports.member_directory import MemberDirectoryPort

if TYPE_CHECKING:
    import discord


def actor_from_member(
    user: discord.Member | discord.User | Any,
    guild_id: int | None = None,
) -> ActorPrincipal:
    """Constructs an ActorPrincipal from a discord.Member, discord.User, or duck-typed object."""
    if isinstance(user, ActorPrincipal):
        if guild_id is not None and user.guild_id is None:
            return ActorPrincipal(
                user_id=user.user_id,
                guild_id=guild_id,
                role_ids=user.role_ids,
                is_guild_admin=user.is_guild_admin,
            )
        return user

    raw_id = getattr(user, "id", None)
    if not isinstance(raw_id, int):
        raw_id = getattr(user, "user_id", 0)
    user_id = int(raw_id) if isinstance(raw_id, int) else 0
    resolved_guild_id = guild_id or getattr(getattr(user, "guild", None), "id", None)

    # Roles
    role_ids: set[int] = set()
    if hasattr(user, "role_ids"):
        role_ids.update(user.role_ids)
    elif hasattr(user, "roles"):
        for r in getattr(user, "roles", []):
            if hasattr(r, "id"):
                role_ids.add(r.id)

    # Permissions
    is_admin = False
    if hasattr(user, "is_guild_admin"):
        is_admin = bool(user.is_guild_admin)
    else:
        perms = getattr(user, "guild_permissions", None)
        if perms is not None:
            is_admin = bool(getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False))

    return ActorPrincipal(
        user_id=int(user_id),
        guild_id=int(resolved_guild_id) if resolved_guild_id is not None else None,
        role_ids=frozenset(role_ids),
        is_guild_admin=is_admin,
    )


def actor_from_interaction(interaction: discord.Interaction | Any) -> ActorPrincipal:
    """Constructs an ActorPrincipal from a discord.Interaction."""
    guild_id = interaction.guild_id if hasattr(interaction, "guild_id") else None
    user = getattr(interaction, "user", None)
    if user is None:
        raise ValueError("Interaction has no user")
    return actor_from_member(user, guild_id=guild_id)


class DiscordMemberDirectory(MemberDirectoryPort):
    """Adapter implementing MemberDirectoryPort via Discord client caching and API fetching."""

    def __init__(self, bot: discord.Client | Any) -> None:
        self.bot = bot

    async def get_member_principal(self, guild_id: int, user_id: int) -> ActorPrincipal | None:
        if getattr(self.bot, "id", None) == guild_id and hasattr(self.bot, "get_member"):
            guild = self.bot
        else:
            guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
            if not guild and hasattr(self.bot, "fetch_guild"):
                try:
                    guild = await self.bot.fetch_guild(guild_id)
                except Exception:
                    guild = None

        if not guild:
            return None

        member = guild.get_member(user_id) if hasattr(guild, "get_member") else None
        if not member and hasattr(guild, "fetch_member"):
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None

        if not member:
            return None

        return actor_from_member(member, guild_id=guild_id)

    async def get_member_display_name(self, guild_id: int, user_id: int) -> str | None:
        guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
        if guild and hasattr(guild, "get_member"):
            member = guild.get_member(user_id)
            if member:
                return getattr(member, "display_name", getattr(member, "name", str(user_id)))
        return None
