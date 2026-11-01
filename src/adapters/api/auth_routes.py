"""Discord OAuth2 authentication and session routes for the web dashboard portal."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from src.adapters.api.dependencies import (
    get_active_settings,
    get_current_principal,
    get_member_directory,
    get_session_data,
)
from src.adapters.api.session import (
    DEFAULT_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    create_session_token,
)
from src.config import Settings
from src.domain.actor import ActorPrincipal

DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_OAUTH_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
DISCORD_USERS_ME_URL = "https://discord.com/api/v10/users/@me"

auth_router = APIRouter(tags=["auth"])


class DiscordOAuthClientPort(Protocol):
    """Protocol for exchanging a Discord OAuth2 authorization code for user identity."""

    async def exchange_code_for_user(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, Any]: ...


class AiohttpDiscordOAuthClient:
    """Default Discord OAuth2 HTTP client using aiohttp."""

    async def exchange_code_for_user(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        form_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with aiohttp.ClientSession() as session:
            async with session.post(DISCORD_OAUTH_TOKEN_URL, data=form_data, headers=headers) as token_resp:
                if token_resp.status != 200:
                    raise HTTPException(
                        status_code=401,
                        detail=f"Discord OAuth2 token exchange failed (status {token_resp.status}).",
                    )
                token_payload = await token_resp.json()

            access_token = token_payload.get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail="Discord OAuth2 response missing access_token.")

            auth_headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(DISCORD_USERS_ME_URL, headers=auth_headers) as me_resp:
                if me_resp.status != 200:
                    raise HTTPException(
                        status_code=401,
                        detail=f"Failed to fetch Discord user profile (status {me_resp.status}).",
                    )
                user_data = await me_resp.json()
                return dict(user_data)


def _resolve_redirect_uri(settings: Settings) -> str:
    if settings.DISCORD_OAUTH_REDIRECT_URI:
        return settings.DISCORD_OAUTH_REDIRECT_URI.strip()
    base = (settings.PUBLIC_BASE_URL or "http://localhost:8000").rstrip("/")
    return f"{base}/auth/callback"


def _resolve_oauth_client(request: Request) -> DiscordOAuthClientPort:
    client = getattr(request.app.state, "oauth_client", None)
    if client is not None:
        return client
    return AiohttpDiscordOAuthClient()


@auth_router.get("/auth/login")
async def auth_login(
    request: Request,
    user_id: int = Query(default=1),
    username: str = Query(default="dev-admin"),
    is_guild_admin: bool = Query(default=True),
) -> RedirectResponse:
    """Initiates Discord OAuth2 sign-in, or establishes a local dev session in development mode."""
    settings = get_active_settings(request)
    secret = settings.SESSION_SECRET_KEY or "dev-session-secret-change-in-production"

    # Zero-config development mode fallback when DISCORD_CLIENT_SECRET is not set
    if not settings.DISCORD_CLIENT_SECRET and settings.ENVIRONMENT.lower() == "development":
        token = create_session_token(
            {
                "user_id": int(user_id),
                "username": username,
                "display_name": username,
                "avatar_url": None,
                "guild_id": settings.DISCORD_GUILD_ID,
                "is_dev_fallback": True,
                "is_guild_admin": bool(is_guild_admin),
                "role_ids": [],
            },
            secret_key=secret,
        )
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=DEFAULT_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return response

    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Discord OAuth2 is not configured (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET are required).",
        )

    redirect_uri = _resolve_redirect_uri(settings)
    query = urlencode(
        {
            "client_id": settings.DISCORD_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify",
        }
    )
    return RedirectResponse(url=f"{DISCORD_OAUTH_AUTHORIZE_URL}?{query}", status_code=302)


@auth_router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str = Query(...),
) -> RedirectResponse:
    """Completes Discord OAuth2 flow, establishes a signed session cookie, and redirects to /dashboard."""
    settings = get_active_settings(request)
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Discord OAuth2 is not configured.")

    oauth_client = _resolve_oauth_client(request)
    redirect_uri = _resolve_redirect_uri(settings)
    user_data = await oauth_client.exchange_code_for_user(
        code=code,
        client_id=settings.DISCORD_CLIENT_ID,
        client_secret=settings.DISCORD_CLIENT_SECRET,
        redirect_uri=redirect_uri,
    )

    raw_user_id = user_data.get("id")
    if not raw_user_id:
        raise HTTPException(status_code=401, detail="Discord OAuth2 profile missing user id.")

    user_id = int(raw_user_id)
    username = str(user_data.get("username") or f"user-{user_id}")
    display_name = str(user_data.get("global_name") or username)
    avatar_hash = user_data.get("avatar")
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png" if avatar_hash else None

    secret = settings.SESSION_SECRET_KEY or "dev-session-secret-change-in-production"
    token = create_session_token(
        {
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "avatar_url": avatar_url,
            "guild_id": settings.DISCORD_GUILD_ID,
            "is_dev_fallback": False,
        },
        secret_key=secret,
    )

    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=DEFAULT_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@auth_router.get("/auth/logout")
@auth_router.post("/auth/logout")
async def auth_logout() -> RedirectResponse:
    """Clears the session cookie and redirects to the portal login view."""
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


@auth_router.get("/api/me")
async def get_current_user_profile(
    request: Request,
    principal: ActorPrincipal = Depends(get_current_principal),
    session_data: dict[str, Any] = Depends(get_session_data),
) -> dict[str, Any]:
    """Returns the authenticated user's identity and resolved guild permission principal."""
    member_directory = get_member_directory(request)
    display_name = session_data.get("display_name") or session_data.get("username") or f"User {principal.user_id}"
    if member_directory and principal.guild_id:
        resolved_name = await member_directory.get_member_display_name(principal.guild_id, principal.user_id)
        if resolved_name:
            display_name = resolved_name

    auth_service = getattr(request.app.state, "auth_service", None)
    can_manage_projects = principal.is_guild_admin
    if auth_service is not None and principal.guild_id:
        can_manage_projects = await auth_service.can_manage_projects(principal, guild_id=principal.guild_id)

    return {
        "user_id": principal.user_id,
        "user_id_str": str(principal.user_id),
        "username": session_data.get("username") or f"user-{principal.user_id}",
        "display_name": display_name,
        "avatar_url": session_data.get("avatar_url"),
        "guild_id": principal.guild_id,
        "guild_id_str": str(principal.guild_id) if principal.guild_id else None,
        "role_ids": sorted(principal.role_ids),
        "is_guild_admin": principal.is_guild_admin,
        "can_manage_projects": can_manage_projects,
        "is_dev_fallback": bool(session_data.get("is_dev_fallback", False)),
    }
