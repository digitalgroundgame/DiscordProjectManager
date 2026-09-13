"""Project Timeline domain module for DGG-PM.

Defines the core Project Timeline entity, hybrid Critical Path Method (CPM) scheduling,
priority-based duration fallbacks, and rendering delegation.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar
from uuid import UUID

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.tech_tree import MemberResolver, TechTree, TechTreeNode, TechTreeNodeState

__all__ = [
    "PRIORITY_DURATIONS",
    "ProjectTimeline",
    "TimelineRenderer",
    "TimelineTask",
]

# Standard fallback duration (in days) when explicit due dates are unset
PRIORITY_DURATIONS: dict[PriorityLevel, int] = {
    PriorityLevel.HIGH: 2,
    PriorityLevel.NORMAL: 4,
    PriorityLevel.LOW: 7,
}


@dataclass(frozen=True, slots=True)
class TimelineTask:
    """Projected read-only view of a Task scheduled along calendar time."""

    task: Task
    node: TechTreeNode
    start_date: datetime
    end_date: datetime
    duration_days: int
    is_inferred_start: bool
    is_inferred_end: bool

    @property
    def id(self) -> UUID:
        return self.task.id

    @property
    def short_id(self) -> str:
        return self.task.short_id

    @property
    def title(self) -> str:
        return self.task.title

    @property
    def status(self) -> TaskStatus:
        return self.task.status

    @property
    def priority(self) -> PriorityLevel:
        return self.task.priority

    @property
    def state(self) -> TechTreeNodeState:
        return self.node.state

    @property
    def assignee_name(self) -> str | None:
        return self.node.assignee_name

    @property
    def assignee_discord_id(self) -> int | None:
        return self.task.assignee_discord_id

    @property
    def prerequisite_ids(self) -> frozenset[UUID]:
        return self.node.prerequisite_ids

    @property
    def dependent_ids(self) -> frozenset[UUID]:
        return self.node.dependent_ids


T_RenderOutput = TypeVar("T_RenderOutput")


class TimelineRenderer(Protocol[T_RenderOutput]):
    """Seam protocol for swappable Project Timeline output renderers."""

    def render(self, timeline: ProjectTimeline, **kwargs: Any) -> T_RenderOutput: ...


class ProjectTimeline:
    """Domain model representing a project's tasks scheduled across calendar time."""

    def __init__(
        self,
        tree: TechTree,
        timeline_tasks: Sequence[TimelineTask],
    ) -> None:
        self._tree = tree
        self._tasks: list[TimelineTask] = list(timeline_tasks)
        self._by_id: dict[UUID, TimelineTask] = {t.id: t for t in self._tasks}
        self._by_short_id: dict[str, TimelineTask] = {t.short_id.upper(): t for t in self._tasks if t.short_id}

        if self._tasks:
            self._start_date: datetime | None = min(t.start_date for t in self._tasks)
            self._end_date: datetime | None = max(t.end_date for t in self._tasks)
            delta = self._end_date - self._start_date
            self._total_duration_days = max(1, delta.days)
        else:
            self._start_date = None
            self._end_date = None
            self._total_duration_days = 0

    @property
    def tasks(self) -> Sequence[TimelineTask]:
        return self._tasks

    @property
    def tree(self) -> TechTree:
        return self._tree

    @property
    def start_date(self) -> datetime | None:
        return self._start_date

    @property
    def end_date(self) -> datetime | None:
        return self._end_date

    @property
    def total_duration_days(self) -> int:
        return self._total_duration_days

    def task(self, key: UUID | str) -> TimelineTask:
        if isinstance(key, UUID):
            if key in self._by_id:
                return self._by_id[key]
            raise KeyError(f"Timeline task with ID '{key}' not found.")
        clean_key = str(key).strip().lstrip("#").upper()
        if clean_key in self._by_short_id:
            return self._by_short_id[clean_key]
        try:
            parsed = UUID(clean_key)
            if parsed in self._by_id:
                return self._by_id[parsed]
        except ValueError:
            pass
        raise KeyError(f"Timeline task '{key}' not found.")

    @classmethod
    def build(
        cls,
        tasks: Sequence[Task],
        dependencies: Sequence[tuple[UUID, UUID]] | None = None,
        *,
        member_resolver: MemberResolver | None = None,
    ) -> ProjectTimeline:
        """Constructs a ProjectTimeline with hybrid CPM scheduling."""
        tree = TechTree(tasks, dependencies, member_resolver=member_resolver)
        if not tasks:
            return cls(tree, [])

        # Topological sorting based on layers
        sorted_tasks = sorted(tasks, key=lambda t: tree.node(t.id).depth_layer)

        scheduled: dict[UUID, TimelineTask] = {}

        for task in sorted_tasks:
            node = tree.node(task.id)

            # 1. Determine Start Date
            if task.start_at is not None:
                start_dt = task.start_at if task.start_at.tzinfo else task.start_at.replace(tzinfo=UTC)
                is_inferred_start = False
            elif node.prerequisite_ids:
                # CPM: Start after all completed/scheduled prerequisites
                prereq_ends = [scheduled[pid].end_date for pid in node.prerequisite_ids if pid in scheduled]
                if prereq_ends:
                    start_dt = max(prereq_ends)
                else:
                    start_dt = task.created_at if task.created_at.tzinfo else task.created_at.replace(tzinfo=UTC)
                is_inferred_start = True
            else:
                start_dt = task.created_at if task.created_at.tzinfo else task.created_at.replace(tzinfo=UTC)
                is_inferred_start = True

            # 2. Determine End Date & Duration
            if task.is_completed and task.completed_at is not None:
                end_dt = task.completed_at if task.completed_at.tzinfo else task.completed_at.replace(tzinfo=UTC)
                is_inferred_end = False
                duration_days = max(1, (end_dt - start_dt).days)
            elif task.due_at is not None:
                due_dt = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)
                if due_dt > start_dt:
                    end_dt = due_dt
                    duration_days = max(1, (end_dt - start_dt).days)
                    is_inferred_end = False
                else:
                    duration_days = PRIORITY_DURATIONS.get(task.priority, 4)
                    end_dt = start_dt + timedelta(days=duration_days)
                    is_inferred_end = True
            else:
                duration_days = PRIORITY_DURATIONS.get(task.priority, 4)
                end_dt = start_dt + timedelta(days=duration_days)
                is_inferred_end = True

            scheduled[task.id] = TimelineTask(
                task=task,
                node=node,
                start_date=start_dt,
                end_date=end_dt,
                duration_days=duration_days,
                is_inferred_start=is_inferred_start,
                is_inferred_end=is_inferred_end,
            )

        return cls(tree, list(scheduled.values()))

    def to_png(self, title: str = "Project Timeline", subtitle: str | None = None) -> io.BytesIO:
        """Renders the Timeline into a Discord-native PNG byte buffer."""
        from src.services.gantt_render import PillowGanttRenderer

        renderer = PillowGanttRenderer()
        return renderer.render(self, title=title, subtitle=subtitle)

    def to_mermaid(self) -> str:
        """Renders the Timeline into a Mermaid markdown Gantt chart."""
        from src.services.gantt_render import MermaidGanttRenderer

        renderer = MermaidGanttRenderer()
        return renderer.render(self)
