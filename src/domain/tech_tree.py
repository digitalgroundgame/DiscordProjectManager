"""Tech Tree domain module for DGG-PM.

Defines the core Tech Tree DAG entity, node readiness state evaluation, cycle detection,
topological layering, and rendering protocols.
"""

from __future__ import annotations

import io
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar
from uuid import UUID

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task

__all__ = [
    "BottleneckInfo",
    "CircularDependencyError",
    "MemberResolver",
    "SelfDependencyError",
    "TaskNotFoundError",
    "TechTree",
    "TechTreeError",
    "TechTreeMetrics",
    "TechTreeNode",
    "TechTreeNodeState",
    "TechTreeOrientation",
    "TechTreeRenderer",
    "resolve_member_name",
]

# ---------------------------------------------------------------------------
# Enums & Value Types
# ---------------------------------------------------------------------------


class TechTreeNodeState(StrEnum):
    """Operational readiness state of a Task node within the Tech Tree DAG."""

    COMPLETE = "complete"  # Task is finished (is_completed is True)
    ACTIVE = "active"  # Task is currently in progress (status == TaskStatus.IN_PROGRESS)
    AVAILABLE = "available"  # All prerequisites complete; ready to start
    LOCKED = "locked"  # One or more prerequisites are not yet completed
    BLOCKED = "blocked"  # Explicitly marked as blocked in metadata_json


class TechTreeOrientation(StrEnum):
    """Visual rendering layout direction."""

    HORIZONTAL = "lr"  # Left to right (default)
    VERTICAL = "tb"  # Top to bottom


@dataclass(frozen=True, slots=True)
class TechTreeNode:
    """Projected read-only view of a Task node within the Tech Tree DAG."""

    id: UUID
    short_id: str
    title: str
    body: str
    status: TaskStatus
    priority: PriorityLevel
    state: TechTreeNodeState
    assignee_discord_id: int | None
    assignee_name: str | None
    depth_layer: int
    prerequisite_ids: frozenset[UUID]
    dependent_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class BottleneckInfo:
    """Analysis of an uncompleted Task blocking downstream tasks."""

    node: TechTreeNode
    blocked_dependents_count: int  # Total transitive downstream tasks blocked
    direct_dependents_count: int  # Direct dependents blocked


@dataclass(frozen=True, slots=True)
class TechTreeMetrics:
    """Summary metrics of a Project's Tech Tree."""

    total_tasks: int
    completed_tasks: int
    active_tasks: int
    available_tasks: int
    locked_tasks: int
    blocked_tasks: int
    percent_complete: int
    max_depth: int
    total_dependencies: int


# ---------------------------------------------------------------------------
# Error Modes
# ---------------------------------------------------------------------------


class TechTreeError(Exception):
    """Base domain exception for Tech Tree operations."""


class CircularDependencyError(TechTreeError):
    """Raised when adding a dependency violates the DAG invariant by creating a cycle."""

    def __init__(self, message: str, cycle_path: Sequence[str] | None = None):
        super().__init__(message)
        self.cycle_path = list(cycle_path or [])

    def format_cycle(self) -> str:
        return " ➔ ".join(self.cycle_path) if self.cycle_path else "Circular dependency"


class SelfDependencyError(TechTreeError):
    """Raised when a Task is instructed to depend upon itself."""


class TaskNotFoundError(TechTreeError):
    """Raised when a referenced Task identifier cannot be found in the Tech Tree."""


# ---------------------------------------------------------------------------
# Member Resolution Protocol & Adapter Helper
# ---------------------------------------------------------------------------

MemberResolver = Callable[[int], str | None] | Mapping[int, str]


def resolve_member_name(user_id: int | None, resolver: Any = None) -> str | None:
    """Resolves a user ID to a display name or username.

    Supports dictionaries/mappings and callables in the pure domain layer.
    """
    if not user_id or resolver is None:
        return None

    # 1. Dictionary mapping: {user_id: name}
    if isinstance(resolver, Mapping):
        val = resolver.get(user_id)
        if val:
            return str(val)

    # 2. Custom Callable: fn(user_id) -> str
    if callable(resolver):
        try:
            val = resolver(user_id)
            if val:
                return str(val)
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Rendering Seam Protocol
# ---------------------------------------------------------------------------

T_RenderOutput = TypeVar("T_RenderOutput")


class TechTreeRenderer(Protocol[T_RenderOutput]):
    """Seam protocol for swappable Tech Tree output renderers."""

    def render(self, tree: TechTree, **kwargs: Any) -> T_RenderOutput: ...


# ---------------------------------------------------------------------------
# Tech Tree Deep Domain Module
# ---------------------------------------------------------------------------


class TechTree:
    """Deep domain module representing a Project's Tech Tree DAG.

    Invariants:
      1. Acyclicity: An edge (prerequisite -> dependent) cannot introduce a cycle.
      2. Irreflexivity: A Task cannot depend on itself.
      3. State Exhaustiveness: Every Task has a deterministic TechTreeNodeState derived
         from its own status and prerequisite completion.
      4. Immutability: Analyzed node states and graph structures are immutable.
    """

    def __init__(
        self,
        tasks: Sequence[Task],
        dependencies: Sequence[tuple[UUID, UUID]] | None = None,
        *,
        member_resolver: MemberResolver | None = None,
    ) -> None:
        self._tasks: list[Task] = list(tasks)
        self._raw_dependencies: list[tuple[UUID, UUID]] = list(dependencies or [])
        self._member_resolver = member_resolver

        # Index tasks by ID and short_id
        self._by_id: dict[UUID, Task] = {t.id: t for t in self._tasks}
        self._by_short_id: dict[str, Task] = {t.short_id.upper(): t for t in self._tasks if t.short_id}

        # Build adjacency sets:
        # dependencies are stored as (dependent_id, prereq_id) in database
        self._prereqs_map: dict[UUID, set[UUID]] = {t.id: set() for t in self._tasks}
        self._dependents_map: dict[UUID, set[UUID]] = {t.id: set() for t in self._tasks}

        for dependent_id, prereq_id in self._raw_dependencies:
            if dependent_id in self._prereqs_map and prereq_id in self._dependents_map:
                self._prereqs_map[dependent_id].add(prereq_id)
                self._dependents_map[prereq_id].add(dependent_id)

        # Compute topological layers
        self._layers: dict[UUID, int] = self._compute_layers()

        # Compute evaluated nodes
        self._nodes: dict[UUID, TechTreeNode] = {}
        for t in self._tasks:
            prereq_ids = frozenset(self._prereqs_map.get(t.id, set()))
            dep_ids = frozenset(self._dependents_map.get(t.id, set()))
            state = self._evaluate_state(t, prereq_ids)

            assignee_name = None
            if t.assignee_discord_id:
                assignee_name = resolve_member_name(t.assignee_discord_id, self._member_resolver)
                if not assignee_name:
                    assignee_name = f"User {t.assignee_discord_id}"

            self._nodes[t.id] = TechTreeNode(
                id=t.id,
                short_id=t.short_id,
                title=t.title,
                body=t.body or "",
                status=t.status,
                priority=t.priority,
                state=state,
                assignee_discord_id=t.assignee_discord_id,
                assignee_name=assignee_name,
                depth_layer=self._layers.get(t.id, 0),
                prerequisite_ids=prereq_ids,
                dependent_ids=dep_ids,
            )

    @classmethod
    def build(
        cls,
        tasks: Sequence[Task],
        dependencies: Sequence[tuple[UUID, UUID]] | None = None,
        *,
        member_resolver: MemberResolver | None = None,
    ) -> TechTree:
        """Factory constructor building an analyzed TechTree instance."""
        return cls(tasks, dependencies, member_resolver=member_resolver)

    # -----------------------------------------------------------------------
    # Internal Graph Analysis
    # -----------------------------------------------------------------------

    def _evaluate_state(self, task: Task, prereq_ids: frozenset[UUID]) -> TechTreeNodeState:
        """Determines the TechTreeNodeState for a task based on its status and prerequisites."""
        if task.is_completed:
            return TechTreeNodeState.COMPLETE
        if task.status == TaskStatus.IN_PROGRESS:
            return TechTreeNodeState.ACTIVE
        if task.metadata_json.get("blocked") is True:
            return TechTreeNodeState.BLOCKED

        # Check prerequisite completion
        all_prereqs_complete = True
        for pid in prereq_ids:
            prereq_task = self._by_id.get(pid)
            if prereq_task and not prereq_task.is_completed:
                all_prereqs_complete = False
                break

        return TechTreeNodeState.AVAILABLE if all_prereqs_complete else TechTreeNodeState.LOCKED

    def _compute_layers(self) -> dict[UUID, int]:
        """Computes topological depth layer for each task using longest path in DAG."""
        depth: dict[UUID, int] = {}
        visiting: set[UUID] = set()

        def walk(uid: UUID) -> int:
            if uid in depth:
                return depth[uid]
            if uid in visiting:
                return 0  # Cycle safeguard
            visiting.add(uid)
            prereqs = self._prereqs_map.get(uid, set())
            d = 0 if not prereqs else 1 + max(walk(p) for p in prereqs)
            visiting.discard(uid)
            depth[uid] = d
            return d

        for t in self._tasks:
            walk(t.id)
        return depth

    def _resolve_task_id(self, key: UUID | str) -> UUID:
        """Resolves a UUID or short ID string into a task UUID."""
        if isinstance(key, UUID):
            if key in self._by_id:
                return key
            raise TaskNotFoundError(f"Task with ID '{key}' not found in Tech Tree.")

        clean_key = str(key).strip().lstrip("#").upper()
        if clean_key in self._by_short_id:
            return self._by_short_id[clean_key].id

        try:
            parsed_uuid = UUID(clean_key)
            if parsed_uuid in self._by_id:
                return parsed_uuid
        except (ValueError, AttributeError):
            pass

        raise TaskNotFoundError(f"Task '{key}' not found in Tech Tree.")

    # -----------------------------------------------------------------------
    # Query Surface
    # -----------------------------------------------------------------------

    def node(self, key: UUID | str) -> TechTreeNode:
        """Retrieves an analyzed TechTreeNode by UUID or short ID."""
        uid = self._resolve_task_id(key)
        return self._nodes[uid]

    def __getitem__(self, key: UUID | str) -> TechTreeNode:
        return self.node(key)

    @property
    def nodes(self) -> Mapping[UUID, TechTreeNode]:
        """All analyzed nodes indexed by task UUID."""
        return self._nodes

    def state_of(self, key: UUID | str) -> TechTreeNodeState:
        """Returns the readiness state of a task."""
        return self.node(key).state

    def prerequisites(self, key: UUID | str, *, recursive: bool = False) -> list[TechTreeNode]:
        """Returns prerequisite tasks for the given task."""
        uid = self._resolve_task_id(key)
        if not recursive:
            return [self._nodes[pid] for pid in self._prereqs_map.get(uid, set()) if pid in self._nodes]

        visited: set[UUID] = set()
        queue: deque[UUID] = deque(self._prereqs_map.get(uid, set()))
        while queue:
            curr = queue.popleft()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self._prereqs_map.get(curr, set()))
        return [self._nodes[pid] for pid in visited if pid in self._nodes]

    def dependents(self, key: UUID | str, *, recursive: bool = False) -> list[TechTreeNode]:
        """Returns dependent tasks for the given task."""
        uid = self._resolve_task_id(key)
        if not recursive:
            return [self._nodes[did] for did in self._dependents_map.get(uid, set()) if did in self._nodes]

        visited: set[UUID] = set()
        queue: deque[UUID] = deque(self._dependents_map.get(uid, set()))
        while queue:
            curr = queue.popleft()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self._dependents_map.get(curr, set()))
        return [self._nodes[did] for did in visited if did in self._nodes]

    def ready_tasks(self) -> list[TechTreeNode]:
        """Returns all tasks ready to start (AVAILABLE)."""
        return [n for n in self._nodes.values() if n.state == TechTreeNodeState.AVAILABLE]

    def locked_tasks(self) -> list[TechTreeNode]:
        """Returns all locked tasks (LOCKED)."""
        return [n for n in self._nodes.values() if n.state == TechTreeNodeState.LOCKED]

    def bottlenecks(self, limit: int = 5) -> list[BottleneckInfo]:
        """Identifies uncompleted tasks blocking the largest number of downstream dependents."""
        uncompleted = [n for n in self._nodes.values() if n.state != TechTreeNodeState.COMPLETE]
        results: list[BottleneckInfo] = []

        for node in uncompleted:
            transitive_deps = self.dependents(node.id, recursive=True)
            blocked_downstream = sum(1 for d in transitive_deps if d.state != TechTreeNodeState.COMPLETE)
            direct_deps = len(node.dependent_ids)
            if blocked_downstream > 0:
                results.append(
                    BottleneckInfo(
                        node=node,
                        blocked_dependents_count=blocked_downstream,
                        direct_dependents_count=direct_deps,
                    )
                )

        results.sort(key=lambda b: (b.blocked_dependents_count, b.direct_dependents_count), reverse=True)
        return results[:limit]

    def critical_path(self) -> list[TechTreeNode]:
        """Computes the longest uncompleted dependency chain to project completion."""
        uncompleted_nodes = {uid: n for uid, n in self._nodes.items() if n.state != TechTreeNodeState.COMPLETE}
        if not uncompleted_nodes:
            return []

        memo: dict[UUID, list[UUID]] = {}

        def get_longest(uid: UUID) -> list[UUID]:
            if uid in memo:
                return memo[uid]
            longest_child_path: list[UUID] = []
            for dep_id in self._dependents_map.get(uid, set()):
                if dep_id in uncompleted_nodes:
                    child_path = get_longest(dep_id)
                    if len(child_path) > len(longest_child_path):
                        longest_child_path = child_path
            res = [uid, *longest_child_path]
            memo[uid] = res
            return res

        longest_overall: list[UUID] = []
        for uid in uncompleted_nodes:
            path = get_longest(uid)
            if len(path) > len(longest_overall):
                longest_overall = path

        return [self._nodes[uid] for uid in longest_overall]

    def metrics(self) -> TechTreeMetrics:
        """Returns aggregate project health and Tech Tree complexity metrics."""
        total = len(self._nodes)
        completed = sum(1 for n in self._nodes.values() if n.state == TechTreeNodeState.COMPLETE)
        active = sum(1 for n in self._nodes.values() if n.state == TechTreeNodeState.ACTIVE)
        avail = sum(1 for n in self._nodes.values() if n.state == TechTreeNodeState.AVAILABLE)
        locked = sum(1 for n in self._nodes.values() if n.state == TechTreeNodeState.LOCKED)
        blocked = sum(1 for n in self._nodes.values() if n.state == TechTreeNodeState.BLOCKED)
        pct = int((completed / total) * 100) if total > 0 else 0
        max_d = max(self._layers.values(), default=0)

        return TechTreeMetrics(
            total_tasks=total,
            completed_tasks=completed,
            active_tasks=active,
            available_tasks=avail,
            locked_tasks=locked,
            blocked_tasks=blocked,
            percent_complete=pct,
            max_depth=max_d,
            total_dependencies=len(self._raw_dependencies),
        )

    # -----------------------------------------------------------------------
    # Cycle Validation & Mutation Seam
    # -----------------------------------------------------------------------

    def can_add_dependency(self, dependent_key: UUID | str, prereq_key: UUID | str) -> bool:
        """Returns True if adding the dependency edge preserves DAG invariants."""
        try:
            self.validate_edge(dependent_key, prereq_key)
            return True
        except (TechTreeError, ValueError):
            return False

    def validate_edge(self, dependent_key: UUID | str, prereq_key: UUID | str) -> None:
        """Validates that adding a dependency (dependent_key depends on prereq_key) preserves DAG acyclicity.

        Raises:
            SelfDependencyError: If dependent_key == prereq_key.
            TaskNotFoundError: If either task key cannot be resolved.
            CircularDependencyError: If adding the edge creates a cycle in the DAG.
        """
        dependent_id = self._resolve_task_id(dependent_key)
        prereq_id = self._resolve_task_id(prereq_key)

        if dependent_id == prereq_id:
            dep_name = self._by_id[dependent_id].short_id or str(dependent_id)
            raise SelfDependencyError(f"Task '{dep_name}' cannot depend on itself.")

        # Check if prereq already depends on dependent (directly or transitively)
        # Search for a path from dependent_id to prereq_id in the existing graph
        # In our storage, (dep, pre) means dep depends on pre (dep -> pre).
        # So if dependent_id depends on prereq_id, the new edge is dependent -> prereq.
        # A cycle would occur if there already is a chain: prereq -> ... -> dependent!
        adj: dict[UUID, list[UUID]] = {}
        for dep, pre in self._raw_dependencies:
            adj.setdefault(dep, []).append(pre)

        # BFS / DFS to find if prereq can reach dependent through existing dependencies
        visited: set[UUID] = set()
        parent_map: dict[UUID, UUID] = {}
        queue: deque[UUID] = deque([prereq_id])

        found_cycle = False
        while queue:
            curr = queue.popleft()
            if curr == dependent_id:
                found_cycle = True
                break
            if curr not in visited:
                visited.add(curr)
                for neighbor in adj.get(curr, []):
                    if neighbor not in visited and neighbor not in parent_map:
                        parent_map[neighbor] = curr
                        queue.append(neighbor)

        if found_cycle:
            # Reconstruct cycle path
            path: list[str] = []
            curr: UUID | None = dependent_id
            while curr is not None:
                short_name = self._by_id[curr].short_id if curr in self._by_id else str(curr)
                path.append(short_name)
                curr = parent_map.get(curr)

            # Cycle path is: dependent -> prereq -> ... -> dependent
            dep_short = self._by_id[dependent_id].short_id
            prereq_short = self._by_id[prereq_id].short_id
            cycle_chain = list(reversed(path))
            if not cycle_chain or cycle_chain[0] != prereq_short:
                middle = [p for p in cycle_chain if p not in (dep_short, prereq_short)]
                cycle_chain = [dep_short, prereq_short, *middle, dep_short]

            raise CircularDependencyError(
                f"Cannot add dependency: '{prereq_short}' already directly or indirectly "
                f"depends on '{dep_short}', which would create a circular loop.",
                cycle_path=cycle_chain,
            )

    # -----------------------------------------------------------------------
    # Compatible Export & Rendering
    # -----------------------------------------------------------------------

    def to_nodes_data(self) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """Exports DAG into the format expected by layout engines and legacy views.

        Returns:
            (nodes, edges) where:
              nodes is a list of dicts with keys:
                - key, short_id, name, description, state, assignee, priority
              edges is a list of (prereq_short_id, dependent_short_id) tuples.
        """
        nodes: list[dict[str, Any]] = []
        for t in self._tasks:
            node = self._nodes[t.id]
            nodes.append(
                {
                    "key": t.short_id,
                    "short_id": f"[{t.short_id}]",
                    "name": t.title,
                    "description": t.body or "",
                    "state": node.state.value,
                    "assignee": node.assignee_name,
                    "priority": t.priority.value,
                }
            )

        edges: list[tuple[str, str]] = []
        for dependent_id, prereq_id in self._raw_dependencies:
            if dependent_id in self._by_id and prereq_id in self._by_id:
                prereq_short = self._by_id[prereq_id].short_id
                dependent_short = self._by_id[dependent_id].short_id
                edges.append((prereq_short, dependent_short))

        return nodes, edges

    def render(self, renderer: TechTreeRenderer[T_RenderOutput] | Any, **kwargs: Any) -> T_RenderOutput:
        """Renders the Tech Tree using an injected rendering adapter."""
        return renderer.render(self, **kwargs)

    def to_png(
        self,
        renderer: Any | None = None,
        title: str = "Project Tech Tree",
        subtitle: str | None = None,
        orientation: str = "lr",
    ) -> io.BytesIO:
        """Renders the Tech Tree into a PNG byte buffer using an injected renderer."""
        if renderer is None:
            raise ValueError("No PNG renderer provided. Pass renderer=... to to_png or use tree.render(renderer).")
        mode = orientation.value if hasattr(orientation, "value") else str(orientation)
        return renderer.render(self, title=title, subtitle=subtitle, mode=mode)

    def to_mermaid(
        self,
        renderer: Any | None = None,
        orientation: str = "lr",
    ) -> str:
        """Renders the Tech Tree into a Mermaid markdown graph for Discord text messages."""
        if renderer is None:
            raise ValueError(
                "No Mermaid renderer provided. Pass renderer=... to to_mermaid or use tree.render(renderer)."
            )
        return renderer.render(self, orientation=orientation)
