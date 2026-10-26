"""Pure domain entity representing an authenticated actor and their permission principal."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActorPrincipal(BaseModel):
    """Domain representation of a caller/member within a guild context.

    Decoupled from vendor-specific Discord Member / User SDK types.
    """

    user_id: int
    guild_id: int | None = None
    role_ids: frozenset[int] = Field(default_factory=frozenset)
    is_guild_admin: bool = False

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    @property
    def id(self) -> int:
        """Alias for compatibility with discord.Member.id."""
        return self.user_id

    @property
    def roles(self) -> list[Any]:
        """Duck-typing helper returning objects with an .id attribute for compatibility."""

        class _RoleDuck:
            def __init__(self, role_id: int) -> None:
                self.id = role_id

        return [_RoleDuck(rid) for rid in self.role_ids]

    @property
    def guild_permissions(self) -> Any:
        """Duck-typing helper returning permissions for compatibility."""

        class _PermDuck:
            def __init__(self, is_admin: bool) -> None:
                self.administrator = is_admin
                self.manage_guild = is_admin

        return _PermDuck(self.is_guild_admin)
