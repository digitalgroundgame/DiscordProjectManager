from collections.abc import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import __version__
from src.adapters.db.postgres_repo import PostgresTaskRepo, PostgresUserCalendarTokenRepo
from src.adapters.db.session import get_session
from src.adapters.db.tables import OutboxEventTable, ProjectTable, TaskTable
from src.config import settings
from src.services.calendar_service import CalendarService

api_app = FastAPI(
    title="dgg-pm Service API",
    description="Health, metrics, and calendar subscription feeds for Discord-Native Task Management Platform",
    version=__version__,
)

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Dependency yielding an active database session."""
    async with get_session() as session:
        yield session


def require_metrics_permission(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Optionally guards /metrics behind a bearer token when API_METRICS_TOKEN is set."""
    if not settings.API_METRICS_TOKEN:
        return
    if credentials is None or credentials.credentials != settings.API_METRICS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


@api_app.get("/healthz")
async def health_check() -> dict:
    """Liveness & Readiness probe checking database connectivity."""
    try:
        async with get_session() as session:
            # Simple query to verify DB is responsive
            await session.execute(select(1))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connectivity check failed: {e}") from e


@api_app.get("/metrics", dependencies=[Depends(require_metrics_permission)])
async def metrics() -> dict:
    """Basic platform operational metrics."""
    try:
        async with get_session() as session:
            task_count_stmt = select(func.count()).select_from(TaskTable)
            task_res = await session.execute(task_count_stmt)
            total_tasks = task_res.scalar() or 0

            proj_count_stmt = select(func.count()).select_from(ProjectTable)
            proj_res = await session.execute(proj_count_stmt)
            total_projects = proj_res.scalar() or 0

            outbox_pending_stmt = (
                select(func.count()).select_from(OutboxEventTable).where(OutboxEventTable.status == "PENDING")
            )
            outbox_res = await session.execute(outbox_pending_stmt)
            pending_outbox = outbox_res.scalar() or 0

        return {
            "total_tasks": total_tasks,
            "total_projects": total_projects,
            "pending_outbox_events": pending_outbox,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_app.get("/calendar/{token}.ics")
@api_app.get("/calendar/{token}")
async def get_calendar_feed(
    token: str,
    format: str = Query(default="event"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Personalized RFC 5545 iCalendar subscription feed for assigned tasks."""
    format_lower = format.lower().strip()
    if format_lower not in ("event", "todo", "both"):
        format_lower = "event"

    token_repo = PostgresUserCalendarTokenRepo(session)
    task_repo = PostgresTaskRepo(session)
    calendar_service = CalendarService(token_repo, task_repo)

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
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Redirects https:// requests to webcal:// for 1-click subscription from clients like Discord."""
    clean_token = token[:-4] if token.endswith(".ics") else token
    token_repo = PostgresUserCalendarTokenRepo(session)
    calendar_service = CalendarService(token_repo, PostgresTaskRepo(session))
    user_id = await calendar_service.get_user_id_by_token(clean_token)
    if user_id is None:
        raise HTTPException(status_code=404, detail="Invalid or expired calendar subscription token.")

    urls = calendar_service.build_feed_urls(clean_token)
    target = urls["todo_webcal_url"] if format.lower().strip() == "todo" else urls["webcal_url"]
    return RedirectResponse(url=target, status_code=307)
