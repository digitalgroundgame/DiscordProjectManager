"""SQL Query Efficiency & N+1 Regression Test Suite.

Asserts bounded, O(1) constant query counts across repository and service layers
as projects, tasks, squads, and dependency graphs scale.
See GitHub Issue #101.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.domain.models import Task


@pytest.mark.asyncio
async def test_query_counter_fixture_captures_statements_and_assertions(
    async_engine: AsyncEngine,
    query_counter,
) -> None:
    """AC 1: Query count fixture captures and asserts total executed SQL statements during a test block."""
    # 1. As context manager with sync syntax
    with query_counter as qc:
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.execute(text("SELECT 2"))

    assert qc.count == 2
    assert len(qc.statements) == 2
    assert "SELECT 1" in qc.statements[0]
    qc.assert_max_queries(2)
    qc.assert_query_count(2)

    with pytest.raises(AssertionError, match="Expected at most 1 SQL queries, but executed 2"):
        qc.assert_max_queries(1)

    with pytest.raises(AssertionError, match="Expected exactly 3 SQL queries, but executed 2"):
        qc.assert_query_count(3)

    # 2. As async context manager
    async with query_counter as qc_async:
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 3"))

    assert qc_async.count == 1
    qc_async.assert_query_count(1)


@pytest.mark.asyncio
async def test_get_project_tree_query_efficiency_at_scale(services, query_counter) -> None:
    """Benchmark: TaskService.get_project_tree() with 100 tasks and 200 dependencies

    must execute in <= 3 SQL queries.
    """
    proj_srv = services["project"]
    task_srv = services["task"]
    task_repo = services["task"].task_repo
    guild_id = 998877

    # 1. Seed 1 project
    project = await proj_srv.create_project(guild_id=guild_id, name="Massive Tech Tree Scale")

    # 2. Seed 100 tasks
    tasks: list[Task] = []
    for i in range(100):
        t = await task_repo.create(
            Task(
                guild_id=guild_id,
                project_id=project.id,
                task_number=i + 1,
                short_id=f"TREE-{i + 1}",
                title=f"Node {i + 1}",
                creator_discord_id=1000 + i,
            )
        )
        tasks.append(t)

    # 3. Seed 200 dependencies
    # 99 linear chain dependencies (Node 2 -> Node 1, Node 3 -> Node 2, ...)
    for i in range(99):
        await task_repo.add_dependency(tasks[i + 1].id, tasks[i].id)
    # 98 skip-1 dependencies (Node 3 -> Node 1, Node 4 -> Node 2, ...)
    for i in range(98):
        await task_repo.add_dependency(tasks[i + 2].id, tasks[i].id)
    # 3 skip-2 dependencies (Node 4 -> Node 1, Node 5 -> Node 2, Node 6 -> Node 3)
    for i in range(3):
        await task_repo.add_dependency(tasks[i + 3].id, tasks[i].id)

    # Total dependencies = 99 + 98 + 3 = 200 dependencies

    # 4. Measure queries executed by TaskService.get_project_tree()
    with query_counter as qc:
        tree = await task_srv.get_project_tree(guild_id=guild_id, project_id=project.id)

    # Assert correctness
    assert len(tree.nodes) == 100
    _nodes, edges = tree.to_nodes_data()
    assert len(edges) == 200

    # Assert query count ceiling: <= 3 queries (Tasks query + Watchers selectin + Dependencies query)
    qc.assert_max_queries(3)


@pytest.mark.asyncio
async def test_get_project_tree_scales_o1_constant(services, query_counter) -> None:
    """AC 3: Scaling entity count (5 vs 50 tasks) verifies that get_project_tree query count remains O(1) constant."""
    proj_srv = services["project"]
    task_srv = services["task"]
    task_repo = services["task"].task_repo
    guild_id = 112233

    # Small Project: 5 tasks, 4 dependencies
    p_small = await proj_srv.create_project(guild_id=guild_id, name="Small Tree")
    small_tasks = []
    for i in range(5):
        t = await task_repo.create(
            Task(
                guild_id=guild_id,
                project_id=p_small.id,
                task_number=i + 1,
                short_id=f"SML-{i + 1}",
                title=f"Small {i + 1}",
                creator_discord_id=2000 + i,
            )
        )
        small_tasks.append(t)
    for i in range(4):
        await task_repo.add_dependency(small_tasks[i + 1].id, small_tasks[i].id)

    # Medium Project: 50 tasks, 75 dependencies
    p_med = await proj_srv.create_project(guild_id=guild_id, name="Medium Tree")
    med_tasks = []
    for i in range(50):
        t = await task_repo.create(
            Task(
                guild_id=guild_id,
                project_id=p_med.id,
                task_number=i + 1,
                short_id=f"MED-{i + 1}",
                title=f"Medium {i + 1}",
                creator_discord_id=3000 + i,
            )
        )
        med_tasks.append(t)
    for i in range(49):
        await task_repo.add_dependency(med_tasks[i + 1].id, med_tasks[i].id)
    for i in range(26):
        await task_repo.add_dependency(med_tasks[i + 2].id, med_tasks[i].id)

    # Measure Small Project
    with query_counter as qc_small:
        tree_small = await task_srv.get_project_tree(guild_id=guild_id, project_id=p_small.id)
    assert len(tree_small.nodes) == 5

    # Measure Medium Project
    with query_counter as qc_med:
        tree_med = await task_srv.get_project_tree(guild_id=guild_id, project_id=p_med.id)
    assert len(tree_med.nodes) == 50

    # Both must execute in <= 3 queries and have IDENTICAL query counts (O(1) complexity)
    qc_small.assert_max_queries(3)
    qc_med.assert_max_queries(3)
    assert qc_small.count == qc_med.count, (
        f"Query count scaled with task count: small had {qc_small.count}, medium had {qc_med.count}"
    )


@pytest.mark.asyncio
async def test_list_projects_query_efficiency_and_scale(services, query_counter) -> None:
    """Benchmark: ProjectService.list_projects() uses selectinload / joinedload appropriately

    to avoid per-project role queries, remaining O(1) constant as projects scale.
    """
    proj_srv = services["project"]
    guild_id = 445566

    # 1. Create 2 projects with multiple squad roles
    for i in range(2):
        await proj_srv.create_project(
            guild_id=guild_id,
            name=f"Base Project {i}",
            prefix=f"BP{i}",
            discord_role_ids=[5000 + i, 6000 + i, 7000 + i],
        )

    # Benchmark 2 projects
    with query_counter as qc_baseline:
        projects_baseline = await proj_srv.list_projects(guild_id=guild_id)

    assert len(projects_baseline) == 2
    # Verify roles are loaded eagerly without lazy load queries
    for p in projects_baseline:
        assert len(p.discord_role_ids) == 3
    qc_baseline.assert_max_queries(3)

    # 2. Scale by adding 15 more projects (total 17 projects with 3 squads each)
    for i in range(2, 17):
        await proj_srv.create_project(
            guild_id=guild_id,
            name=f"Scaled Project {i}",
            prefix=f"SP{i}",
            discord_role_ids=[5000 + i, 6000 + i, 7000 + i],
        )

    # Benchmark 17 projects
    with query_counter as qc_scaled:
        projects_scaled = await proj_srv.list_projects(guild_id=guild_id)

    assert len(projects_scaled) == 17
    for p in projects_scaled:
        assert len(p.discord_role_ids) == 3

    # Scaled project query count must equal baseline query count (O(1) query complexity)
    qc_scaled.assert_max_queries(3)
    assert qc_scaled.count == qc_baseline.count, (
        f"Query count scaled with project count: baseline had {qc_baseline.count}, scaled had {qc_scaled.count}"
    )


@pytest.mark.asyncio
async def test_workspace_service_get_project_workspace_fixed_queries(services, query_counter) -> None:
    """Benchmark: WorkspaceService.get_project_workspace() with multiple squads and tasks

    executes in a fixed number of queries regardless of entity count.
    """
    proj_srv = services["project"]
    task_repo = services["task"].task_repo
    ws_srv = services["workspace"]
    guild_id = 778899

    # Case A: Small Workspace (2 squads, 5 tasks)
    p_small = await proj_srv.create_project(
        guild_id=guild_id,
        name="Small WS",
        discord_role_ids=[101, 102],
    )
    for i in range(5):
        await task_repo.create(
            Task(
                guild_id=guild_id,
                project_id=p_small.id,
                task_number=i + 1,
                short_id=f"SWS-{i + 1}",
                title=f"Small Task {i}",
                creator_discord_id=900,
            )
        )

    # Case B: Large Workspace (10 squads, 50 tasks)
    large_role_ids = [200 + i for i in range(10)]
    p_large = await proj_srv.create_project(
        guild_id=guild_id,
        name="Large WS",
        discord_role_ids=large_role_ids,
    )
    for i in range(50):
        await task_repo.create(
            Task(
                guild_id=guild_id,
                project_id=p_large.id,
                task_number=i + 1,
                short_id=f"LWS-{i + 1}",
                title=f"Large Task {i}",
                creator_discord_id=900,
            )
        )

    # Measure Case A
    with query_counter as qc_small:
        view_small = await ws_srv.get_project_workspace(p_small.id)

    assert view_small.project.id == p_small.id
    assert len(view_small.squads) == 2
    assert len(view_small.tasks) == 5

    # Measure Case B
    with query_counter as qc_large:
        view_large = await ws_srv.get_project_workspace(p_large.id)

    assert view_large.project.id == p_large.id
    assert len(view_large.squads) == 10
    assert len(view_large.tasks) == 50

    # Query counts must be bounded and identical (O(1) constant)
    qc_small.assert_max_queries(6)
    qc_large.assert_max_queries(6)
    assert qc_small.count == qc_large.count, (
        f"Workspace query count scaled: small had {qc_small.count}, large had {qc_large.count}"
    )


@pytest.mark.asyncio
async def test_fails_immediately_if_unbatched_lazy_loading(services, query_counter) -> None:
    """AC 4: Fails immediately if relationship traversal triggers unbatched lazy loading."""
    proj_srv = services["project"]
    guild_id = 332211

    # Create 5 projects each with 2 squad roles
    for i in range(5):
        await proj_srv.create_project(
            guild_id=guild_id,
            name=f"Lazy Test Project {i}",
            discord_role_ids=[8000 + i, 9000 + i],
        )

    # Execute list_projects and iterate through all project role IDs
    with query_counter as qc:
        projects = await proj_srv.list_projects(guild_id)
        # Accessing discord_role_ids on each project domain model must NOT execute additional SQL queries
        extracted_roles = [p.discord_role_ids for p in projects]

    assert len(extracted_roles) == 5
    for roles in extracted_roles:
        assert len(roles) == 2

    # If unbatched lazy loading occurred, query count would be at least 1 + 5 = 6 (or 1 + 10 = 11).
    # Since it is batched via selectinload/joinedload, count must remain <= 3.
    qc.assert_max_queries(3)
