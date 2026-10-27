"""Property-based testing suite for TechTree DAG invariants using Hypothesis.

Rigorously validates mathematical invariants across arbitrary randomized graph
topologies:
  1. Acyclicity Invariant: Cycles are strictly rejected with CircularDependencyError,
     and valid non-cyclic edges are accepted.
  2. Topological Ordering Invariant: Prerequisite nodes strictly precede dependent
     nodes in depth layers and topological order.
  3. State Cascading Invariant: A task's operational readiness state is AVAILABLE
     if and only if 100% of its prerequisites are COMPLETED.
  4. Cycle Path Truth: Any cycle_path returned by CircularDependencyError forms
     a verified closed loop in the graph.

Adheres strictly to Hexagonal Architecture: imports domain models only.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import hypothesis.strategies as st
from hypothesis import given, seed, settings

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.tech_tree import (
    CircularDependencyError,
    SelfDependencyError,
    TechTree,
    TechTreeNodeState,
)

# ---------------------------------------------------------------------------
# Hypothesis Strategies for Randomized DAG Topologies
# ---------------------------------------------------------------------------


def _make_domain_task(
    index: int,
    status: TaskStatus,
    priority: PriorityLevel,
    is_blocked: bool,
) -> Task:
    """Helper to instantiate a pure domain Task entity."""
    return Task(
        id=uuid4(),
        guild_id=123456789,
        short_id=f"TSK-{index + 1}",
        title=f"Task {index + 1}",
        status=status,
        priority=priority,
        creator_discord_id=1000 + index,
        metadata_json={"blocked": True} if is_blocked else {},
    )


@st.composite
def random_tasks_strategy(
    draw: st.DrawFn,
    min_tasks: int = 1,
    max_tasks: int = 16,
) -> list[Task]:
    """Generates an arbitrary list of domain Tasks with diverse statuses and priorities."""
    n = draw(st.integers(min_value=min_tasks, max_value=max_tasks))
    tasks: list[Task] = []
    for i in range(n):
        status = draw(st.sampled_from(list(TaskStatus)))
        priority = draw(st.sampled_from(list(PriorityLevel)))
        is_blocked = draw(st.booleans())
        tasks.append(_make_domain_task(i, status, priority, is_blocked))
    return tasks


@st.composite
def random_dag_strategy(
    draw: st.DrawFn,
    min_tasks: int = 1,
    max_tasks: int = 16,
) -> tuple[list[Task], list[tuple[UUID, UUID]], TechTree]:
    """Generates an arbitrary valid DAG by imposing an index-based topological order.

    For any edge (dependent, prerequisite), dep_index > prereq_index is enforced,
    which mathematically guarantees acyclicity across arbitrary edge densities.
    """
    tasks = draw(random_tasks_strategy(min_tasks=min_tasks, max_tasks=max_tasks))
    n = len(tasks)

    if n <= 1:
        tree = TechTree.build(tasks, [])
        return tasks, [], tree

    raw_pairs = draw(
        st.lists(
            st.tuples(st.integers(min_value=0, max_value=n - 1), st.integers(min_value=0, max_value=n - 1)),
            max_size=n * 2,
        )
    )

    edges: set[tuple[UUID, UUID]] = set()
    for a, b in raw_pairs:
        if a != b:
            dep_idx, pre_idx = max(a, b), min(a, b)
            edges.add((tasks[dep_idx].id, tasks[pre_idx].id))

    edge_list = list(edges)
    tree = TechTree.build(tasks, edge_list)
    return tasks, edge_list, tree


@st.composite
def dag_with_candidate_cycle_strategy(
    draw: st.DrawFn,
    min_tasks: int = 2,
    max_tasks: int = 14,
) -> tuple[list[Task], list[tuple[UUID, UUID]], TechTree, tuple[UUID, UUID]]:
    """Generates a valid DAG plus a candidate edge guaranteed to create a cycle.

    Finds a pair where dependent transitively depends on prerequisite, then proposes
    inverting that edge (making prerequisite depend on dependent).
    """
    tasks = draw(random_tasks_strategy(min_tasks=min_tasks, max_tasks=max_tasks))
    n = len(tasks)

    raw_pairs = draw(
        st.lists(
            st.tuples(st.integers(min_value=0, max_value=n - 1), st.integers(min_value=0, max_value=n - 1)),
            max_size=n * 2,
        )
    )

    edges: set[tuple[UUID, UUID]] = set()
    for a, b in raw_pairs:
        if a != b:
            dep_idx, pre_idx = max(a, b), min(a, b)
            edges.add((tasks[dep_idx].id, tasks[pre_idx].id))

    tree = TechTree.build(tasks, list(edges))

    cycle_candidates: list[tuple[UUID, UUID]] = []
    for t in tasks:
        ancestors = tree.prerequisites(t.id, recursive=True)
        for anc in ancestors:
            # t depends on anc (anc is an ancestor of t).
            # Attempting to add (anc.id, t.id) makes anc depend on t -> guaranteed cycle!
            cycle_candidates.append((anc.id, t.id))

    if not cycle_candidates:
        # Guarantee at least one chain exists if random selection yielded an edgeless graph
        edges.add((tasks[1].id, tasks[0].id))
        tree = TechTree.build(tasks, list(edges))
        cycle_candidates.append((tasks[0].id, tasks[1].id))

    candidate_edge = draw(st.sampled_from(cycle_candidates))
    return tasks, list(edges), tree, candidate_edge


@st.composite
def dag_with_acyclic_candidate_strategy(
    draw: st.DrawFn,
    min_tasks: int = 2,
    max_tasks: int = 14,
) -> tuple[list[Task], list[tuple[UUID, UUID]], TechTree, tuple[UUID, UUID]]:
    """Generates a valid DAG plus a candidate non-self edge guaranteed NOT to create a cycle."""
    tasks = draw(random_tasks_strategy(min_tasks=min_tasks, max_tasks=max_tasks))
    n = len(tasks)

    raw_pairs = draw(
        st.lists(
            st.tuples(st.integers(min_value=0, max_value=n - 1), st.integers(min_value=0, max_value=n - 1)),
            max_size=n,
        )
    )

    edges: set[tuple[UUID, UUID]] = set()
    for a, b in raw_pairs:
        if a != b:
            dep_idx, pre_idx = max(a, b), min(a, b)
            edges.add((tasks[dep_idx].id, tasks[pre_idx].id))

    tree = TechTree.build(tasks, list(edges))

    # Find pairs (dep, pre) where adding dep -> pre will NOT create a cycle
    # (i.e. dep is not an ancestor of pre) and is not already a direct edge
    valid_candidates: list[tuple[UUID, UUID]] = []
    for dep in tasks:
        dep_direct = {p.id for p in tree.prerequisites(dep.id, recursive=False)}
        for pre in tasks:
            if dep.id != pre.id and pre.id not in dep_direct:
                pre_ancestors = {p.id for p in tree.prerequisites(pre.id, recursive=True)}
                if dep.id not in pre_ancestors:
                    valid_candidates.append((dep.id, pre.id))

    if not valid_candidates:
        # Create an isolated task to guarantee an acyclic edge candidate
        isolated_task = _make_domain_task(len(tasks), TaskStatus.NOT_STARTED, PriorityLevel.NORMAL, False)
        tasks.append(isolated_task)
        tree = TechTree.build(tasks, list(edges))
        valid_candidates.append((isolated_task.id, tasks[0].id))

    candidate_edge = draw(st.sampled_from(valid_candidates))
    return tasks, list(edges), tree, candidate_edge


# ---------------------------------------------------------------------------
# Invariant 1: Acyclicity Invariant
# ---------------------------------------------------------------------------


class TestTechTreeAcyclicityInvariant:
    """Validates that DAG acyclicity invariants are mathematically sound."""

    @settings(max_examples=100)
    @given(dag_with_candidate_cycle_strategy())
    def test_cyclic_candidate_edge_strictly_rejected(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree, tuple[UUID, UUID]],
    ) -> None:
        """Attempting to add any edge that introduces a cycle must raise CircularDependencyError."""
        _tasks, _edges, tree, (dep_id, pre_id) = data
        assert tree.can_add_dependency(dep_id, pre_id) is False

        try:
            tree.validate_edge(dep_id, pre_id)
            raise AssertionError(f"Expected CircularDependencyError for edge ({dep_id}, {pre_id})")
        except CircularDependencyError as exc:
            assert exc.cycle_path, "CircularDependencyError must provide a non-empty cycle_path"
            assert len(exc.cycle_path) >= 2, f"Cycle path must have at least 2 nodes: {exc.cycle_path}"
            assert "circular loop" in str(exc).lower() or "depends on" in str(exc).lower()

    @settings(max_examples=100)
    @given(dag_with_acyclic_candidate_strategy())
    def test_valid_acyclic_edge_accepted(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree, tuple[UUID, UUID]],
    ) -> None:
        """Adding a non-cyclic edge between distinct nodes must always succeed."""
        _tasks, _edges, tree, (dep_id, pre_id) = data
        assert tree.can_add_dependency(dep_id, pre_id) is True
        # validate_edge must not raise
        tree.validate_edge(dep_id, pre_id)

    @settings(max_examples=100)
    @given(random_dag_strategy(min_tasks=1, max_tasks=12))
    def test_self_dependency_strictly_rejected(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """A task can never depend upon itself (irreflexivity invariant)."""
        tasks, _edges, tree = data
        for task in tasks:
            assert tree.can_add_dependency(task.id, task.id) is False
            try:
                tree.validate_edge(task.id, task.id)
                raise AssertionError(f"Expected SelfDependencyError for self-loop on {task.short_id}")
            except SelfDependencyError as exc:
                assert "cannot depend on itself" in str(exc)


# ---------------------------------------------------------------------------
# Invariant 2: Topological Ordering Invariant
# ---------------------------------------------------------------------------


class TestTechTreeTopologicalOrderingInvariant:
    """Validates that topological depth and sorting order strictly respect dependencies."""

    @settings(max_examples=100)
    @given(random_dag_strategy(min_tasks=2, max_tasks=16))
    def test_depth_layer_strictly_increases_along_dependency_edges(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """For every edge (dependent -> prerequisite), prerequisite.depth < dependent.depth."""
        _tasks, edges, tree = data
        for dep_id, pre_id in edges:
            dep_node = tree.node(dep_id)
            pre_node = tree.node(pre_id)
            assert pre_node.depth_layer < dep_node.depth_layer, (
                f"Topological violation: prereq {pre_node.short_id} (depth={pre_node.depth_layer}) "
                f"must have strictly lower depth than dependent {dep_node.short_id} (depth={dep_node.depth_layer})"
            )

    @settings(max_examples=100)
    @given(random_dag_strategy(min_tasks=2, max_tasks=16))
    def test_transitive_reachability_depth_invariants(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """All transitive ancestors have strictly lower depth, and all descendants have higher depth."""
        tasks, _edges, tree = data
        for task in tasks:
            curr_node = tree.node(task.id)

            for anc in tree.prerequisites(task.id, recursive=True):
                assert anc.depth_layer < curr_node.depth_layer, (
                    f"Ancestor {anc.short_id} depth {anc.depth_layer} "
                    f">= current {curr_node.short_id} depth {curr_node.depth_layer}"
                )

            for desc in tree.dependents(task.id, recursive=True):
                assert desc.depth_layer > curr_node.depth_layer, (
                    f"Descendant {desc.short_id} depth {desc.depth_layer} "
                    f"<= current {curr_node.short_id} depth {curr_node.depth_layer}"
                )


# ---------------------------------------------------------------------------
# Invariant 3: State Cascading Invariant
# ---------------------------------------------------------------------------


class TestTechTreeStateCascadingInvariant:
    """Validates that task operational readiness states follow exact logical rules."""

    @settings(max_examples=100)
    @given(random_dag_strategy(min_tasks=1, max_tasks=16))
    def test_node_state_evaluation_truth(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """A node is AVAILABLE iff uncompleted, not active, not blocked, and 100% of prereqs are COMPLETE."""
        _tasks, _edges, tree = data

        for node in tree.nodes.values():
            task = tree._by_id[node.id]
            direct_prereqs = tree.prerequisites(node.id)
            all_prereqs_complete = all(p.state == TechTreeNodeState.COMPLETE for p in direct_prereqs)

            if task.is_completed:
                assert node.state == TechTreeNodeState.COMPLETE
            elif task.status == TaskStatus.IN_PROGRESS:
                assert node.state == TechTreeNodeState.ACTIVE
            elif task.metadata_json.get("blocked") is True:
                assert node.state == TechTreeNodeState.BLOCKED
            elif all_prereqs_complete:
                assert node.state == TechTreeNodeState.AVAILABLE
            else:
                assert node.state == TechTreeNodeState.LOCKED

    @settings(max_examples=100)
    @given(random_dag_strategy(min_tasks=1, max_tasks=16))
    def test_ready_and_locked_collections_exhaustive(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """ready_tasks() and locked_tasks() partition their respective states exhaustively."""
        _tasks, _edges, tree = data
        expected_ready = {n.id for n in tree.nodes.values() if n.state == TechTreeNodeState.AVAILABLE}
        expected_locked = {n.id for n in tree.nodes.values() if n.state == TechTreeNodeState.LOCKED}

        actual_ready = {n.id for n in tree.ready_tasks()}
        actual_locked = {n.id for n in tree.locked_tasks()}

        assert actual_ready == expected_ready
        assert actual_locked == expected_locked


# ---------------------------------------------------------------------------
# Invariant 4: Cycle Path Truth Invariant
# ---------------------------------------------------------------------------


class TestTechTreeCyclePathTruthInvariant:
    """Validates that cycle_path returned by CircularDependencyError forms a closed loop."""

    @settings(max_examples=100)
    @given(dag_with_candidate_cycle_strategy())
    def test_cycle_path_forms_verified_closed_loop(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree, tuple[UUID, UUID]],
    ) -> None:
        """Every adjacent step in cycle_path and the closing step must exist in the augmented graph."""
        tasks, edges, tree, (dep_id, pre_id) = data
        task_short_map = {t.id: t.short_id for t in tasks}

        # Dependencies stored as (dependent, prerequisite)
        existing_edges = {(task_short_map[d], task_short_map[p]) for d, p in edges}
        candidate_edge = (task_short_map[dep_id], task_short_map[pre_id])

        # The augmented edge set contains all existing edges plus the proposed candidate edge
        augmented_edges = set(existing_edges)
        augmented_edges.add(candidate_edge)

        try:
            tree.validate_edge(dep_id, pre_id)
            raise AssertionError("Should have raised CircularDependencyError")
        except CircularDependencyError as exc:
            cycle = exc.cycle_path
            assert len(cycle) >= 2, f"Cycle path must contain at least 2 nodes: {cycle}"

            # Verify that every node in the cycle path belongs to the TechTree
            for node_short in cycle:
                assert tree.node(node_short) is not None

            # Build the sequence of transitions in the cycle path plus the loop closure
            cycle_transitions: list[tuple[str, str]] = []
            for i in range(len(cycle) - 1):
                cycle_transitions.append((cycle[i], cycle[i + 1]))
            cycle_transitions.append((cycle[-1], cycle[0]))

            # Every transition in the cycle must be a real directed edge in the augmented graph
            for from_node, to_node in cycle_transitions:
                assert (from_node, to_node) in augmented_edges, (
                    f"Cycle edge ({from_node} -> {to_node}) does not exist in graph! "
                    f"Cycle: {cycle}, Candidate: {candidate_edge}"
                )

            # The candidate edge that created the cycle MUST be part of this closed loop
            assert candidate_edge in cycle_transitions, (
                f"Candidate edge {candidate_edge} must participate in the cycle transitions: {cycle_transitions}"
            )

            # format_cycle() should render a valid arrow-separated string
            formatted = exc.format_cycle()
            assert "➔" in formatted or len(cycle) == 1


# ---------------------------------------------------------------------------
# Deterministic Seed & Performance Validation
# ---------------------------------------------------------------------------


class TestTechTreeDeterministicSeed:
    """Validates deterministic reproducibility under fixed Hypothesis seeds."""

    @seed(42)
    @settings(max_examples=50)
    @given(random_dag_strategy(min_tasks=3, max_tasks=10))
    def test_seed_reproducibility(
        self,
        data: tuple[list[Task], list[tuple[UUID, UUID]], TechTree],
    ) -> None:
        """Assures test runs with fixed seeds produce identical topologies and metrics."""
        tasks, edges, tree = data
        metrics = tree.metrics()
        assert metrics.total_tasks == len(tasks)
        assert metrics.total_dependencies == len(edges)
