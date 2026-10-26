from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.adapters.api.app import api_app, get_observability_service, require_metrics_permission
from src.adapters.db.observability_adapter import SqlAlchemySystemMetricsAdapter
from src.config import settings
from src.ports.observability import SystemMetricsSnapshot
from src.services.observability_service import ObservabilityService


@pytest.mark.parametrize(
    ("token", "credentials", "expect_raise"),
    [
        # No token configured -> open endpoint, no auth needed
        ("", None, False),
        ("", HTTPAuthorizationCredentials(scheme="Bearer", credentials="anything"), False),
        # Token configured -> missing/invalid credentials rejected
        ("s3cr3t", None, True),
        ("s3cr3t", HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong"), True),
        # Token configured -> correct token accepted
        ("s3cr3t", HTTPAuthorizationCredentials(scheme="Bearer", credentials="s3cr3t"), False),
    ],
)
def test_metrics_permission(token, credentials, expect_raise):
    original = settings.API_METRICS_TOKEN
    settings.API_METRICS_TOKEN = token
    try:
        if expect_raise:
            with pytest.raises(HTTPException) as exc_info:
                require_metrics_permission(credentials)
            assert exc_info.value.status_code == 401
        else:
            require_metrics_permission(credentials)  # should not raise
    finally:
        settings.API_METRICS_TOKEN = original


def test_metrics_route_is_guarded():
    metrics_route = next(r for r in api_app.routes if getattr(r, "path", None) == "/metrics")
    guard_names = {d.dependency.__name__ for d in metrics_route.dependencies}
    assert "require_metrics_permission" in guard_names

    health_route = next(r for r in api_app.routes if getattr(r, "path", None) == "/healthz")
    assert not health_route.dependencies


@pytest.mark.asyncio
async def test_healthz_endpoint_healthy():
    class FakeHealthyMetricsPort:
        async def get_metrics_snapshot(self) -> SystemMetricsSnapshot:
            return SystemMetricsSnapshot(total_tasks=0, total_projects=0, pending_outbox_events=0)

        async def check_health(self) -> bool:
            return True

    svc = ObservabilityService(FakeHealthyMetricsPort())
    api_app.dependency_overrides[get_observability_service] = lambda: svc

    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/healthz")
            assert res.status_code == 200
            assert res.json() == {"status": "ok", "database": "connected"}
    finally:
        api_app.dependency_overrides.pop(get_observability_service, None)


@pytest.mark.asyncio
async def test_healthz_endpoint_unhealthy():
    class FakeUnhealthyMetricsPort:
        async def get_metrics_snapshot(self) -> SystemMetricsSnapshot:
            return SystemMetricsSnapshot(total_tasks=0, total_projects=0, pending_outbox_events=0)

        async def check_health(self) -> bool:
            return False

    svc = ObservabilityService(FakeUnhealthyMetricsPort())
    api_app.dependency_overrides[get_observability_service] = lambda: svc

    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/healthz")
            assert res.status_code == 503
    finally:
        api_app.dependency_overrides.pop(get_observability_service, None)


@pytest.mark.asyncio
async def test_metrics_endpoint():
    class FakeMetricsPort:
        async def get_metrics_snapshot(self) -> SystemMetricsSnapshot:
            return SystemMetricsSnapshot(total_tasks=42, total_projects=7, pending_outbox_events=3)

        async def check_health(self) -> bool:
            return True

    svc = ObservabilityService(FakeMetricsPort())
    api_app.dependency_overrides[get_observability_service] = lambda: svc

    original = settings.API_METRICS_TOKEN
    settings.API_METRICS_TOKEN = ""
    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/metrics")
            assert res.status_code == 200
            data = res.json()
            assert data["total_tasks"] == 42
            assert data["total_projects"] == 7
            assert data["pending_outbox_events"] == 3
    finally:
        settings.API_METRICS_TOKEN = original
        api_app.dependency_overrides.pop(get_observability_service, None)


@pytest.mark.asyncio
async def test_sqlalchemy_system_metrics_adapter(db_session, services):
    task_srv = services["task"]
    proj_srv = services["project"]

    adapter = SqlAlchemySystemMetricsAdapter(db_session)
    assert await adapter.check_health() is True

    snapshot = await adapter.get_metrics_snapshot()
    initial_tasks = snapshot.total_tasks
    initial_projs = snapshot.total_projects

    # Create a project and task
    p = await proj_srv.create_project(guild_id=123, name="Metrics Test Proj", prefix="MTR")
    await task_srv.create_task(guild_id=123, project_id=p.id, title="Metrics Task", creator_discord_id=1)

    updated_snapshot = await adapter.get_metrics_snapshot()
    assert updated_snapshot.total_projects == initial_projs + 1
    assert updated_snapshot.total_tasks == initial_tasks + 1
