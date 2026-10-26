"""Unit test suite for the TechTree deep domain module."""

from __future__ import annotations

import io
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from PIL import Image

from src.adapters.rendering import MermaidTechTreeRenderer, PillowTechTreeRenderer
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.tech_tree import (
    CircularDependencyError,
    SelfDependencyError,
    TaskNotFoundError,
    TechTree,
    TechTreeNodeState,
    TechTreeOrientation,
)


def _make_task(
    short_id: str,
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.NOT_STARTED,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    blocked: bool = False,
    assignee_id: int | None = None,
) -> Task:
    return Task(
        id=uuid4(),
        guild_id=123456789,
        short_id=short_id,
        title=title,
        status=status,
        priority=priority,
        creator_discord_id=999,
        assignee_discord_id=assignee_id,
        metadata_json={"blocked": True} if blocked else {},
    )


class TestTechTreeConstructionAndReadiness:
    def test_empty_tree(self):
        tree = TechTree.build(tasks=[], dependencies=[])
        assert len(tree.nodes) == 0
        metrics = tree.metrics()
        assert metrics.total_tasks == 0
        assert metrics.percent_complete == 0
        assert metrics.max_depth == 0

    def test_single_task_ready(self):
        t1 = _make_task("PRJ-1", status=TaskStatus.NOT_STARTED)
        tree = TechTree.build([t1])
        assert tree.node("PRJ-1").state == TechTreeNodeState.AVAILABLE
        assert len(tree.ready_tasks()) == 1
        assert len(tree.locked_tasks()) == 0

    def test_linear_dependency_chain_states(self):
        # Chain: T1 -> T2 -> T3 (T3 depends on T2, T2 depends on T1)
        t1 = _make_task("PRJ-1", status=TaskStatus.COMPLETED)
        t2 = _make_task("PRJ-2", status=TaskStatus.IN_PROGRESS)
        t3 = _make_task("PRJ-3", status=TaskStatus.NOT_STARTED)

        deps = [
            (t2.id, t1.id),  # T2 depends on T1
            (t3.id, t2.id),  # T3 depends on T2
        ]

        tree = TechTree.build([t1, t2, t3], deps)

        # T1 is completed
        assert tree["PRJ-1"].state == TechTreeNodeState.COMPLETE
        # T2 is in progress (prereq T1 is complete)
        assert tree["PRJ-2"].state == TechTreeNodeState.ACTIVE
        # T3 is locked because its prereq T2 is NOT completed
        assert tree["PRJ-3"].state == TechTreeNodeState.LOCKED

        # Metrics check
        metrics = tree.metrics()
        assert metrics.total_tasks == 3
        assert metrics.completed_tasks == 1
        assert metrics.active_tasks == 1
        assert metrics.locked_tasks == 1
        assert metrics.max_depth == 2

    def test_blocked_metadata_flag(self):
        t1 = _make_task("PRJ-1", status=TaskStatus.NOT_STARTED, blocked=True)
        tree = TechTree.build([t1])
        assert tree.node("PRJ-1").state == TechTreeNodeState.BLOCKED


class TestTechTreeCycleDetection:
    def test_self_dependency_raises(self):
        t1 = _make_task("PRJ-1")
        tree = TechTree.build([t1])
        assert tree.can_add_dependency(t1.id, t1.id) is False
        with pytest.raises(SelfDependencyError, match="cannot depend on itself"):
            tree.validate_edge(t1.id, t1.id)

    def test_direct_cycle_detection(self):
        t1 = _make_task("PRJ-1")
        t2 = _make_task("PRJ-2")
        # Existing: T2 depends on T1
        tree = TechTree.build([t1, t2], [(t2.id, t1.id)])

        # Trying to make T1 depend on T2 should fail with cycle error
        assert tree.can_add_dependency(t1.id, t2.id) is False
        with pytest.raises(CircularDependencyError) as exc_info:
            tree.validate_edge(t1.id, t2.id)

        assert "circular loop" in str(exc_info.value)
        assert "PRJ-2" in exc_info.value.cycle_path
        assert "PRJ-1" in exc_info.value.cycle_path

    def test_transitive_cycle_detection(self):
        # A -> B -> C -> D
        ta = _make_task("PRJ-A")
        tb = _make_task("PRJ-B")
        tc = _make_task("PRJ-C")
        td = _make_task("PRJ-D")

        deps = [
            (tb.id, ta.id),  # B depends on A
            (tc.id, tb.id),  # C depends on B
            (td.id, tc.id),  # D depends on C
        ]
        tree = TechTree.build([ta, tb, tc, td], deps)

        # Trying to add A depends on D:
        with pytest.raises(CircularDependencyError) as exc_info:
            tree.validate_edge("PRJ-A", "PRJ-D")

        err = exc_info.value
        assert "circular loop" in str(err)
        assert err.format_cycle()

    def test_missing_task_raises_task_not_found(self):
        t1 = _make_task("PRJ-1")
        tree = TechTree.build([t1])
        with pytest.raises(TaskNotFoundError):
            tree.validate_edge("PRJ-1", "NON-EXISTENT")


class TestTechTreeQueriesAndBottlenecks:
    def test_prerequisites_and_dependents(self):
        t1 = _make_task("T-1")
        t2 = _make_task("T-2")
        t3 = _make_task("T-3")

        deps = [(t2.id, t1.id), (t3.id, t2.id)]
        tree = TechTree.build([t1, t2, t3], deps)

        # Direct vs recursive prereqs
        assert [n.short_id for n in tree.prerequisites(t3.id, recursive=False)] == ["T-2"]
        recursive_prereqs = {n.short_id for n in tree.prerequisites(t3.id, recursive=True)}
        assert recursive_prereqs == {"T-1", "T-2"}

        # Direct vs recursive dependents
        assert [n.short_id for n in tree.dependents(t1.id, recursive=False)] == ["T-2"]
        recursive_deps = {n.short_id for n in tree.dependents(t1.id, recursive=True)}
        assert recursive_deps == {"T-2", "T-3"}

    def test_bottleneck_detection(self):
        # Root blocker task B1 blocks 3 downstream tasks
        b1 = _make_task("B-1")
        d1 = _make_task("D-1")
        d2 = _make_task("D-2")
        d3 = _make_task("D-3")

        deps = [
            (d1.id, b1.id),
            (d2.id, b1.id),
            (d3.id, d1.id),
        ]
        tree = TechTree.build([b1, d1, d2, d3], deps)
        bottlenecks = tree.bottlenecks(limit=2)

        assert len(bottlenecks) >= 1
        top = bottlenecks[0]
        assert top.node.short_id == "B-1"
        assert top.blocked_dependents_count == 3  # D1, D2, D3

    def test_critical_path(self):
        t1 = _make_task("C-1")
        t2 = _make_task("C-2")
        t3 = _make_task("C-3")
        deps = [(t2.id, t1.id), (t3.id, t2.id)]
        tree = TechTree.build([t1, t2, t3], deps)

        cp = tree.critical_path()
        assert [n.short_id for n in cp] == ["C-1", "C-2", "C-3"]


class TestMemberResolution:
    def test_dict_resolver(self):
        t1 = _make_task("M-1", assignee_id=4242)
        tree = TechTree.build([t1], member_resolver={4242: "Arthur Dent"})
        assert tree["M-1"].assignee_name == "Arthur Dent"

    def test_callable_resolver(self):
        t1 = _make_task("M-2", assignee_id=5555)
        tree = TechTree.build([t1], member_resolver=lambda uid: f"Dev #{uid}")
        assert tree["M-2"].assignee_name == "Dev #5555"

    def test_discord_guild_mock_resolver(self):
        from src.adapters.discord_bot.member_resolver import resolve_discord_member_name

        mock_member = MagicMock()
        mock_member.display_name = "Ford Prefect"
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = mock_member

        t1 = _make_task("M-3", assignee_id=7777)
        tree = TechTree.build([t1], member_resolver=lambda uid: resolve_discord_member_name(uid, mock_guild))
        assert tree["M-3"].assignee_name == "Ford Prefect"


class TestTechTreeRenderingSeam:
    def test_to_nodes_data_compatibility(self):
        t1 = _make_task("N-1", title="First")
        t2 = _make_task("N-2", title="Second")
        tree = TechTree.build([t1, t2], [(t2.id, t1.id)])

        nodes, edges = tree.to_nodes_data()
        assert len(nodes) == 2
        assert nodes[0]["key"] == "N-1"
        assert nodes[1]["key"] == "N-2"
        assert edges == [("N-1", "N-2")]

    def test_to_png_rendering(self):
        t1 = _make_task("PNG-1", status=TaskStatus.COMPLETED)
        t2 = _make_task("PNG-2", status=TaskStatus.IN_PROGRESS)
        tree = TechTree.build([t1, t2], [(t2.id, t1.id)])

        with pytest.raises(ValueError, match="No PNG renderer provided"):
            tree.to_png()

        buf = tree.to_png(
            renderer=PillowTechTreeRenderer(),
            title="Test Project Tree",
            orientation=TechTreeOrientation.HORIZONTAL,
        )
        assert isinstance(buf, io.BytesIO)
        img = Image.open(buf)
        assert img.format == "PNG"
        assert img.width > 200
        assert img.height > 100

    def test_to_mermaid_rendering(self):
        t1 = _make_task("MER-1", title="Start Engine", status=TaskStatus.COMPLETED)
        t2 = _make_task("MER-2", title="Run System", status=TaskStatus.IN_PROGRESS)
        tree = TechTree.build([t1, t2], [(t2.id, t1.id)])

        with pytest.raises(ValueError, match="No Mermaid renderer provided"):
            tree.to_mermaid()

        mermaid = tree.to_mermaid(renderer=MermaidTechTreeRenderer(), orientation="lr")
        assert "graph LR" in mermaid
        assert 'MER-1["MER-1: Start Engine"]:::complete' in mermaid
        assert 'MER-2["MER-2: Run System"]:::active' in mermaid
        assert "MER-1 --> MER-2" in mermaid

    def test_custom_renderer_protocol(self):
        class TextSummaryRenderer:
            def render(self, tree: TechTree, **kwargs) -> str:
                return f"Tree has {len(tree.nodes)} tasks"

        t1 = _make_task("X-1")
        tree = TechTree.build([t1])
        result = tree.render(TextSummaryRenderer())
        assert result == "Tree has 1 tasks"
