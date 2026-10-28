"""Discord member display name resolver utilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

MemberResolver = Callable[[int], str | None] | Mapping[int, str] | Any


def resolve_discord_member_name(user_id: int | None, resolver: Any = None) -> str | None:
    """Resolves a Discord user snowflake ID to a display name or username.

    Supports dictionaries, callables, discord.Guild, discord.Client, or interaction objects.
    """
    if not user_id or resolver is None:
        return None

    # 1. Dictionary mapping: {user_id: name}
    if isinstance(resolver, Mapping):
        val = resolver.get(user_id)
        if val:
            return str(val)

    # 2. Discord Guild or object with get_member
    if hasattr(resolver, "get_member") and not isinstance(resolver, type):
        try:
            member = resolver.get_member(user_id)
            if member:
                name = getattr(member, "display_name", None) or getattr(member, "name", None)
                if isinstance(name, str):
                    return name
                elif name and not isinstance(name, type) and hasattr(name, "__str__"):
                    return str(name)
        except Exception:
            pass

    # 3. Discord Client or interaction with client / get_user
    client = getattr(resolver, "client", None) if not hasattr(resolver, "get_user") else resolver
    if client and hasattr(client, "get_user") and not isinstance(client, type):
        try:
            user = client.get_user(user_id)
            if user:
                name = getattr(user, "display_name", None) or getattr(user, "name", None)
                if isinstance(name, str):
                    return name
                elif name and not isinstance(name, type) and hasattr(name, "__str__"):
                    return str(name)
        except Exception:
            pass

    # 4. Custom Callable: fn(user_id) -> str
    if callable(resolver):
        try:
            val = resolver(user_id)
            if val:
                return str(val)
        except Exception:
            pass

    return None


resolve_member_name = resolve_discord_member_name

__all__ = ["MemberResolver", "resolve_discord_member_name", "resolve_member_name"]
