"""Integration tests for TaskService Project Timeline rendering and start_at support."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image


@pytest.mark.asyncio
async def test_service_render_project_timeline_integration(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877667

    project = await proj_srv.create_project(guild_id=guild_id, name="Timeline Test App", prefix="TIME")
    now = datetime.now(UTC)
    t1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Architecture Setup",
        creator_discord_id=1001,
        project_id=project.id,
        start_at=now,
        due_at=now + timedelta(days=3),
    )
    assert t1.start_at is not None

    t2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Implementation",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[t1.short_id],
    )

    buf = await task_srv.render_project_timeline(guild_id=guild_id, project_id=project.id)
    assert isinstance(buf, io.BytesIO)
    img = Image.open(buf)
    assert img.format == "PNG"
    assert img.width > 0 and img.height > 0

    mermaid_str = await task_srv.export_project_timeline_mermaid(guild_id=guild_id, project_id=project.id)
    assert "gantt" in mermaid_str
    assert f"[{t1.short_id}]" in mermaid_str
    assert f"[{t2.short_id}]" in mermaid_str
