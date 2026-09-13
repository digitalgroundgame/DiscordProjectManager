"""Unit test suite for Gantt Chart Pillow & Mermaid renderers."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from PIL import Image

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.timeline import ProjectTimeline
from src.services.gantt_render import MermaidGanttRenderer, PillowGanttRenderer


def _make_task(
    short_id: str,
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.NOT_STARTED,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    start_at: datetime | None = None,
    due_at: datetime | None = None,
    completed_at: datetime | None = None,
    assignee_id: int | None = None,
) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=uuid4(),
        guild_id=123456789,
        short_id=short_id,
        title=title,
        status=status,
        priority=priority,
        creator_discord_id=999,
        assignee_discord_id=assignee_id,
        start_at=start_at,
        due_at=due_at,
        completed_at=completed_at,
        created_at=now,
    )


class TestPillowGanttRenderer:
    def test_render_empty_timeline(self):
        timeline = ProjectTimeline.build([])
        renderer = PillowGanttRenderer()
        buf = renderer.render(timeline, title="Empty Project")

        assert isinstance(buf, io.BytesIO)
        img = Image.open(buf)
        assert img.format == "PNG"
        assert img.width > 0 and img.height > 0

    def test_render_linear_chain(self):
        base_time = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        t1 = _make_task(
            "LIN-1",
            title="Setup Foundation",
            status=TaskStatus.COMPLETED,
            start_at=base_time,
            completed_at=base_time + timedelta(days=2),
        )
        t2 = _make_task(
            "LIN-2",
            title="Build Architecture",
            status=TaskStatus.IN_PROGRESS,
            priority=PriorityLevel.HIGH,
        )
        t3 = _make_task(
            "LIN-3",
            title="Deploy Release",
            status=TaskStatus.NOT_STARTED,
            priority=PriorityLevel.LOW,
        )

        timeline = ProjectTimeline.build([t1, t2, t3], dependencies=[(t2.id, t1.id), (t3.id, t2.id)])
        renderer = PillowGanttRenderer()
        buf = renderer.render(timeline, title="Linear Pipeline")

        assert isinstance(buf, io.BytesIO)
        img = Image.open(buf)
        assert img.format == "PNG"
        assert img.width >= 800
        assert img.height >= 200

    def test_adaptive_scaling_header_modes(self):
        base_time = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        renderer = PillowGanttRenderer()

        # 1. Daily Scale (< 21 days)
        short_tasks = [
            _make_task("D-1", start_at=base_time, due_at=base_time + timedelta(days=10)),
        ]
        tl_short = ProjectTimeline.build(short_tasks)
        buf_short = renderer.render(tl_short, title="Daily Project")
        assert isinstance(buf_short, io.BytesIO)

        # 2. Weekly Scale (21 to 90 days)
        med_tasks = [
            _make_task("W-1", start_at=base_time, due_at=base_time + timedelta(days=45)),
        ]
        tl_med = ProjectTimeline.build(med_tasks)
        buf_med = renderer.render(tl_med, title="Weekly Project")
        assert isinstance(buf_med, io.BytesIO)

        # 3. Monthly Scale (> 90 days)
        long_tasks = [
            _make_task("M-1", start_at=base_time, due_at=base_time + timedelta(days=150)),
        ]
        tl_long = ProjectTimeline.build(long_tasks)
        buf_long = renderer.render(tl_long, title="Monthly Project")
        assert isinstance(buf_long, io.BytesIO)


class TestMermaidGanttRenderer:
    def test_mermaid_gantt_output(self):
        base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        t1 = _make_task(
            "M-1",
            title="Design Phase",
            status=TaskStatus.COMPLETED,
            start_at=base_time,
            completed_at=base_time + timedelta(days=3),
        )
        t2 = _make_task(
            "M-2",
            title="Implementation Phase",
            status=TaskStatus.IN_PROGRESS,
        )

        timeline = ProjectTimeline.build([t1, t2], dependencies=[(t2.id, t1.id)])
        renderer = MermaidGanttRenderer()
        output = renderer.render(timeline)

        assert "gantt" in output
        assert "dateFormat YYYY-MM-DD" in output
        assert "[M-1] Design Phase" in output
        assert "[M-2] Implementation Phase" in output
        assert "after M-1" in output
