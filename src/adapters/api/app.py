from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import __version__
from src.adapters.api.auth_routes import auth_router
from src.adapters.api.dashboard_routes import dashboard_router
from src.config import get_settings
from src.services.calendar_service import CalendarService
from src.services.observability_service import ObservabilityService

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}

api_app = FastAPI(
    title="dgg-pm Service API",
    description="Health, metrics, calendar feeds, and Web Dashboard Portal for Discord-Native Task Management",
    version=__version__,
)
api_app.include_router(auth_router)
api_app.include_router(dashboard_router)


_PORTAL_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https://cdn.discordapp.com data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)
_PORTAL_SECURITY_HEADERS = {
    "Content-Security-Policy": _PORTAL_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@api_app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirects root requests to the Web Dashboard Portal."""
    return RedirectResponse(url="/dashboard", status_code=307)


@api_app.get("/dashboard", response_class=HTMLResponse)
async def portal_dashboard() -> HTMLResponse:
    """Serves the responsive Web Dashboard & Portal HTML shell."""
    index_path = _STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Dashboard portal index not found.")
    return HTMLResponse(
        content=index_path.read_text(encoding="utf-8"),
        headers=_PORTAL_SECURITY_HEADERS,
    )


@api_app.get("/static/{asset_name}", include_in_schema=False)
async def portal_static_asset(asset_name: str) -> Response:
    """Serves static CSS and ES module assets for the Web Dashboard Portal."""
    if "/" in asset_name or "\\" in asset_name or ".." in asset_name:
        raise HTTPException(status_code=404, detail="Static asset not found.")
    asset_path = (_STATIC_DIR / asset_name).resolve()
    if not asset_path.is_file() or asset_path.parent != _STATIC_DIR.resolve():
        raise HTTPException(status_code=404, detail="Static asset not found.")
    media_type = _STATIC_MEDIA_TYPES.get(asset_path.suffix.lower(), "application/octet-stream")
    return Response(
        content=asset_path.read_text(encoding="utf-8"),
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


_bearer_scheme = HTTPBearer(auto_error=False)


def get_observability_service() -> ObservabilityService:
    """FastAPI dependency resolving the ObservabilityService instance from application state."""
    service = getattr(api_app.state, "observability_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Observability service is not configured")


def get_calendar_service() -> CalendarService:
    """FastAPI dependency resolving the CalendarService instance from application state."""
    service = getattr(api_app.state, "calendar_service", None)
    if service is not None:
        return service
    raise HTTPException(status_code=503, detail="Calendar service is not configured")


def require_metrics_permission(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Optionally guards /metrics behind a bearer token when API_METRICS_TOKEN is set."""
    active_settings = getattr(api_app.state, "settings", None) or get_settings()
    if not active_settings.API_METRICS_TOKEN:
        return
    if credentials is None or credentials.credentials != active_settings.API_METRICS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


@api_app.get("/healthz")
async def health_check(
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    """Liveness & Readiness probe checking database connectivity."""
    try:
        ok = await observability_service.check_health()
        if not ok:
            raise HTTPException(status_code=503, detail="Database connectivity check failed")
        return {"status": "ok", "database": "connected"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connectivity check failed: {e}") from e


@api_app.get("/metrics", dependencies=[Depends(require_metrics_permission)])
async def metrics(
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    """Basic platform operational metrics."""
    try:
        snapshot = await observability_service.get_metrics()
        return {
            "total_tasks": snapshot.total_tasks,
            "total_projects": snapshot.total_projects,
            "pending_outbox_events": snapshot.pending_outbox_events,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_app.get("/calendar/{token}.ics")
@api_app.get("/calendar/{token}")
async def get_calendar_feed(
    token: str,
    format: str = Query(default="event"),
    calendar_service: CalendarService = Depends(get_calendar_service),
) -> Response:
    """Personalized RFC 5545 iCalendar subscription feed for assigned tasks."""
    format_lower = format.lower().strip()
    if format_lower not in ("event", "todo", "both"):
        format_lower = "event"

    clean_token = token[:-4] if token.endswith(".ics") else token
    ics_content = await calendar_service.get_calendar_feed(clean_token, format_type=format_lower)
    if ics_content is None:
        raise HTTPException(
            status_code=404,
            detail="Invalid or expired calendar subscription token.",
        )

    return Response(
        content=ics_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="tasks.ics"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@api_app.get("/calendar/{token}/subscribe")
async def subscribe_calendar_redirect(
    request: Request,
    token: str,
    format: str = Query(default="event"),
    calendar_service: CalendarService = Depends(get_calendar_service),
) -> Response:
    """Redirects https:// requests to webcal:// for 1-click subscription from clients like Discord,
    or serves the RFC 5545 iCalendar stream directly when fetched by Google Calendar or crawlers."""
    clean_token = token[:-4] if token.endswith(".ics") else token
    user_id = await calendar_service.get_user_id_by_token(clean_token)
    if user_id is None:
        raise HTTPException(status_code=404, detail="Invalid or expired calendar subscription token.")

    format_lower = format.lower().strip()
    if format_lower not in ("event", "todo", "both", "gcal"):
        format_lower = "event"

    urls = calendar_service.build_feed_urls(clean_token)

    if format_lower == "gcal":
        return RedirectResponse(url=urls["gcal_url"], status_code=307)

    user_agent = request.headers.get("user-agent", "").lower()
    accept_header = request.headers.get("accept", "").lower()

    # Google Calendar's fetch bot (Google-Calendar-Importer / Feedfetcher-Google) cannot follow redirects
    # to the webcal:// pseudo-protocol. If fetched by Google or clients requesting text/calendar,
    # serve the iCalendar feed directly.
    if any(bot in user_agent for bot in ("google", "feedfetcher", "calendarbot")) or "text/calendar" in accept_header:
        feed_format = "event" if format_lower not in ("todo", "both") else format_lower
        ics_content = await calendar_service.get_calendar_feed(clean_token, format_type=feed_format)
        if ics_content is None:
            raise HTTPException(status_code=404, detail="Invalid or expired calendar subscription token.")
        return Response(
            content=ics_content,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": 'inline; filename="tasks.ics"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    target = urls["todo_webcal_url"] if format_lower == "todo" else urls["webcal_url"]
    return RedirectResponse(url=target, status_code=307)
