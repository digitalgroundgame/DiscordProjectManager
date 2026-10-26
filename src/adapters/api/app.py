from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import __version__
from src.config import settings
from src.services.calendar_service import CalendarService
from src.services.observability_service import ObservabilityService

api_app = FastAPI(
    title="dgg-pm Service API",
    description="Health, metrics, and calendar subscription feeds for Discord-Native Task Management Platform",
    version=__version__,
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


def get_db_session():
    """Deprecated backwards-compatibility stub. Direct DB sessions are no longer used in API handlers."""
    return None


def require_metrics_permission(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Optionally guards /metrics behind a bearer token when API_METRICS_TOKEN is set."""
    if not settings.API_METRICS_TOKEN:
        return
    if credentials is None or credentials.credentials != settings.API_METRICS_TOKEN:
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
    token: str,
    format: str = Query(default="event"),
    calendar_service: CalendarService = Depends(get_calendar_service),
) -> RedirectResponse:
    """Redirects https:// requests to webcal:// for 1-click subscription from clients like Discord."""
    clean_token = token[:-4] if token.endswith(".ics") else token
    user_id = await calendar_service.get_user_id_by_token(clean_token)
    if user_id is None:
        raise HTTPException(status_code=404, detail="Invalid or expired calendar subscription token.")

    urls = calendar_service.build_feed_urls(clean_token)
    target = urls["todo_webcal_url"] if format.lower().strip() == "todo" else urls["webcal_url"]
    return RedirectResponse(url=target, status_code=307)
