"""Unit test suite for ProjectTimeline domain module and CPM scheduling engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.tech_tree import TechTreeNodeState
from src.domain.timeline import ProjectTimeline


def _make_task(
    short_id: str,
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.NOT_STARTED,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    start_at: datetime | None = None,
    due_at: datetime | None = None,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
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
        created_at=created_at or now,
    )


class TestProjectTimelineConstruction:
    def test_empty_timeline(self):
        timeline = ProjectTimeline.build(tasks=[], dependencies=[])
        assert len(timeline.tasks) == 0
        assert timeline.start_date is None
        assert timeline.end_date is None
        assert timeline.total_duration_days == 0

    def test_single_task_explicit_dates(self):
        base_time = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        end_time = datetime(2026, 9, 5, 17, 0, tzinfo=UTC)
        task = _make_task("PRJ-1", start_at=base_time, due_at=end_time)

        timeline = ProjectTimeline.build([task])
        assert len(timeline.tasks) == 1
        t_task = timeline.task("PRJ-1")
        assert t_task.short_id == "PRJ-1"
        assert t_task.start_date == base_time
        assert t_task.end_date == end_time
        assert t_task.duration_days == 4
        assert not t_task.is_inferred_start
        assert not t_task.is_inferred_end

    def test_single_task_priority_fallback_durations(self):
        base_time = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

        high_task = _make_task("PRJ-H", priority=PriorityLevel.HIGH, start_at=base_time)
        normal_task = _make_task("PRJ-N", priority=PriorityLevel.NORMAL, start_at=base_time)
        low_task = _make_task("PRJ-L", priority=PriorityLevel.LOW, start_at=base_time)

        timeline = ProjectTimeline.build([high_task, normal_task, low_task])

        assert timeline.task("PRJ-H").duration_days == 2
        assert timeline.task("PRJ-N").duration_days == 4
        assert timeline.task("PRJ-L").duration_days == 7
        assert timeline.task("PRJ-N").is_inferred_end

    def test_cpm_dependency_chain_scheduling(self):
        base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

        # Task 1: starts at base_time, runs for 2 days (High priority)
        t1 = _make_task("PRJ-1", priority=PriorityLevel.HIGH, start_at=base_time)
        # Task 2: depends on Task 1, no explicit dates (Normal priority = 4 days)
        t2 = _make_task("PRJ-2", priority=PriorityLevel.NORMAL)
        # Task 3: depends on Task 2, no explicit dates (High priority = 2 days)
        t3 = _make_task("PRJ-3", priority=PriorityLevel.HIGH)

        dependencies = [
            (t2.id, t1.id),  # t2 depends on t1
            (t3.id, t2.id),  # t3 depends on t2
        ]

        timeline = ProjectTimeline.build([t1, t2, t3], dependencies=dependencies)

        tt1 = timeline.task("PRJ-1")
        tt2 = timeline.task("PRJ-2")
        tt3 = timeline.task("PRJ-3")

        assert tt1.start_date == base_time
        assert tt1.end_date == base_time + timedelta(days=2)

        # t2 starts immediately after t1 finishes
        assert tt2.start_date == tt1.end_date
        assert tt2.duration_days == 4
        assert tt2.end_date == tt2.start_date + timedelta(days=4)
        assert tt2.is_inferred_start

        # t3 starts immediately after t2 finishes
        assert tt3.start_date == tt2.end_date
        assert tt3.duration_days == 2
        assert tt3.end_date == tt3.start_date + timedelta(days=2)
        assert tt3.is_inferred_start

        assert timeline.start_date == base_time
        assert timeline.end_date == tt3.end_date
        assert timeline.total_duration_days == 8

    def test_completed_task_uses_actual_completion_date(self):
        created_time = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        completed_time = datetime(2026, 9, 3, 14, 0, tzinfo=UTC)
        due_time = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)

        task = _make_task(
            "PRJ-DONE",
            status=TaskStatus.COMPLETED,
            created_at=created_time,
            due_at=due_time,
            completed_at=completed_time,
        )

        timeline = ProjectTimeline.build([task])
        t_task = timeline.task("PRJ-DONE")

        assert t_task.state == TechTreeNodeState.COMPLETE
        assert t_task.end_date == completed_time
        assert not t_task.is_inferred_end
