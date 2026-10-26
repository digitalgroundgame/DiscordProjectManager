"""Port interface for resolving guild member profiles, roles, and ActorPrincipals."""

from __future__ import annotations

from typing import Protocol

from src.domain.actor import ActorPrincipal


class MemberDirectoryPort(Protocol):
    """Port for querying member identities, roles, and administrative statuses across guilds."""

    async def get_member_principal(self, guild_id: int, user_id: int) -> ActorPrincipal | None:
        """Resolves an ActorPrincipal for a given user in a guild, or None if member not found."""
        ...

    async def get_member_display_name(self, guild_id: int, user_id: int) -> str | None:
        """Resolves a member's display name or username in a guild."""
        ...
