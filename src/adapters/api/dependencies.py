"""FastAPI dependencies for resolving application services and ActorPrincipal from app state."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request

from src.adapters.api.session import SESSION_COOKIE_NAME, verify_session_token
from src.config import DEFAULT_DEV_SESSION_SECRET, Settings, get_settings
from src.domain.actor import ActorPrincipal
from src.ports.member_directory import MemberDirectoryPort
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.workspace_service import WorkspaceService


def get_active_settings(request: Request) -> Settings:
    """Resolves active Settings from FastAPI application state or fallback configuration."""
    return getattr(request.app.state, "settings", None) or get_settings()


def resolve_session_secret(settings: Settings) -> str:
    """Resolves the session secret key, refusing empty or default secrets outside development."""
    is_dev = settings.ENVIRONMENT.strip().lower() == "development"
    secret = (settings.SESSION_SECRET_KEY or "").strip()
    if not is_dev and (not secret or secret == DEFAULT_DEV_SESSION_SECRET):
        raise HTTPException(status_code=503, detail="Session secret key is not configured.")
    return secret or DEFAULT_DEV_SESSION_SECRET


def get_project_service(request: Request) -> ProjectService:
    service = getattr(request.app.state, "project_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Project service is not configured")


def get_task_service(request: Request) -> TaskService:
    service = getattr(request.app.state, "task_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Task service is not configured")


def get_squad_service(request: Request) -> SquadService:
    service = getattr(request.app.state, "squad_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Squad service is not configured")


def get_workspace_service(request: Request) -> WorkspaceService:
    service = getattr(request.app.state, "workspace_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Workspace service is not configured")


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Auth service is not configured")


def get_member_directory(request: Request) -> MemberDirectoryPort | None:
    return getattr(request.app.state, "member_directory", None)


def get_session_data(request: Request) -> dict[str, Any]:
    """Extracts and validates the signed session cookie from the incoming HTTP request."""
    settings = get_active_settings(request)
    secret = resolve_session_secret(settings)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session_data = verify_session_token(token, secret)
    if session_data is None or not session_data.get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required. Please sign in via Discord.")
    return session_data


def resolve_target_guild_id(
    request: Request,
    explicit_guild_id: int | None = None,
    session_data: dict[str, Any] | None = None,
) -> int:
    """Determines the active Discord guild_id from query param, Settings, or session."""
    if explicit_guild_id:
        return int(explicit_guild_id)
    settings = get_active_settings(request)
    if settings.DISCORD_GUILD_ID:
        return int(settings.DISCORD_GUILD_ID)
    if session_data and session_data.get("guild_id"):
        return int(session_data["guild_id"])
    default_guild = getattr(request.app.state, "default_guild_id", None)
    if default_guild:
        return int(default_guild)
    return 0


async def get_current_principal(
    request: Request,
    guild_id: int | None = Query(default=None),
) -> ActorPrincipal:
    """Resolves the authenticated user's live ActorPrincipal in the target Discord server."""
    session_data = get_session_data(request)
    user_id = int(session_data["user_id"])
    target_guild_id = resolve_target_guild_id(request, explicit_guild_id=guild_id, session_data=session_data)

    member_directory = get_member_directory(request)
    if member_directory is not None and target_guild_id:
        principal = await member_directory.get_member_principal(target_guild_id, user_id)
        if principal is not None:
            return principal

    settings = get_active_settings(request)
    if (
        session_data.get("is_dev_fallback")
        and settings.ENVIRONMENT.strip().lower() == "development"
        and settings.DEV_AUTH_FALLBACK_ENABLED
    ):
        raw_roles = session_data.get("role_ids") or []
        return ActorPrincipal(
            user_id=user_id,
            guild_id=target_guild_id or None,
            role_ids=frozenset(int(r) for r in raw_roles),
            is_guild_admin=bool(session_data.get("is_guild_admin", True)),
        )

    raise HTTPException(
        status_code=403,
        detail="Access denied: You must be a member of the configured Discord server to view project workspaces.",
    )
