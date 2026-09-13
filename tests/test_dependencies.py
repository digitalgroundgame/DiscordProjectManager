import pytest

from src.domain.enums import TaskStatus
from src.services.task_service import ValidationError


@pytest.mark.asyncio
async def test_add_and_remove_dependency(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877661

    project = await proj_srv.create_project(guild_id=guild_id, name="Cloud Infra", prefix="CLD")

    task_a = await task_srv.create_task(
        guild_id=guild_id, title="Setup VPC", creator_discord_id=1001, project_id=project.id
    )
    task_b = await task_srv.create_task(
        guild_id=guild_id, title="Deploy EKS Cluster", creator_discord_id=1001, project_id=project.id
    )

    # Link B depends on A
    await task_srv.add_dependency(
        guild_id=guild_id,
        task_short_id=task_b.short_id,
        depends_on_short_id=task_a.short_id,
        actor_discord_id=1001,
    )

    # Query dependencies
    prereqs, dependents = await task_srv.get_task_dependencies(task_b.id)
    assert len(prereqs) == 1
    assert prereqs[0].id == task_a.id
    assert len(dependents) == 0

    prereqs_a, dependents_a = await task_srv.get_task_dependencies(task_a.id)
    assert len(prereqs_a) == 0
    assert len(dependents_a) == 1
    assert dependents_a[0].id == task_b.id

    # Remove dependency
    removed = await task_srv.remove_dependency(
        guild_id=guild_id,
        task_short_id=task_b.short_id,
        depends_on_short_id=task_a.short_id,
        actor_discord_id=1001,
    )
    assert removed is True

    prereqs_after, _ = await task_srv.get_task_dependencies(task_b.id)
    assert len(prereqs_after) == 0


@pytest.mark.asyncio
async def test_self_dependency_rejection(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877662

    project = await proj_srv.create_project(guild_id=guild_id, name="Core Engine", prefix="COR")
    task_a = await task_srv.create_task(
        guild_id=guild_id, title="Core Task", creator_discord_id=1001, project_id=project.id
    )

    with pytest.raises(ValidationError, match="cannot depend on itself"):
        await task_srv.add_dependency(
            guild_id=guild_id,
            task_short_id=task_a.short_id,
            depends_on_short_id=task_a.short_id,
            actor_discord_id=1001,
        )


@pytest.mark.asyncio
async def test_cycle_detection_rejection(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877663

    project = await proj_srv.create_project(guild_id=guild_id, name="Pipeline", prefix="PIP")
    task_a = await task_srv.create_task(
        guild_id=guild_id, title="Step A", creator_discord_id=1001, project_id=project.id
    )
    task_b = await task_srv.create_task(
        guild_id=guild_id, title="Step B", creator_discord_id=1001, project_id=project.id
    )
    task_c = await task_srv.create_task(
        guild_id=guild_id, title="Step C", creator_discord_id=1001, project_id=project.id
    )

    # A -> B -> C
    await task_srv.add_dependency(guild_id=guild_id, task_short_id=task_b.short_id, depends_on_short_id=task_a.short_id)
    await task_srv.add_dependency(guild_id=guild_id, task_short_id=task_c.short_id, depends_on_short_id=task_b.short_id)

    # Attempting to make A depend on C should be rejected because C depends on B which depends on A!
    with pytest.raises(ValidationError, match="circular loop"):
        await task_srv.add_dependency(
            guild_id=guild_id,
            task_short_id=task_a.short_id,
            depends_on_short_id=task_c.short_id,
        )

    # Attempting to make A depend on B should also be rejected
    with pytest.raises(ValidationError, match="circular loop"):
        await task_srv.add_dependency(
            guild_id=guild_id,
            task_short_id=task_a.short_id,
            depends_on_short_id=task_b.short_id,
        )


@pytest.mark.asyncio
async def test_create_task_with_prerequisites(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877664

    project = await proj_srv.create_project(guild_id=guild_id, name="Auth Flow", prefix="AUT")
    task_1 = await task_srv.create_task(
        guild_id=guild_id, title="Schema Design", creator_discord_id=1001, project_id=project.id
    )
    task_2 = await task_srv.create_task(
        guild_id=guild_id, title="Token Issuer", creator_discord_id=1001, project_id=project.id
    )

    task_3 = await task_srv.create_task(
        guild_id=guild_id,
        title="OAuth Endpoints",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[task_1.short_id, task_2.short_id],
    )

    prereqs, _ = await task_srv.get_task_dependencies(task_3.id)
    assert len(prereqs) == 2
    prereq_ids = {p.id for p in prereqs}
    assert task_1.id in prereq_ids
    assert task_2.id in prereq_ids


@pytest.mark.asyncio
async def test_inline_description_dependency_parsing(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877665

    project = await proj_srv.create_project(guild_id=guild_id, name="Frontend", prefix="FE")
    task_1 = await task_srv.create_task(
        guild_id=guild_id, title="UI Kit", creator_discord_id=1001, project_id=project.id
    )
    task_2 = await task_srv.create_task(
        guild_id=guild_id, title="State Store", creator_discord_id=1001, project_id=project.id
    )

    # Create task with inline description mentioning prerequisites
    task_3 = await task_srv.create_task(
        guild_id=guild_id,
        title="Dashboard View",
        creator_discord_id=1001,
        project_id=project.id,
        body=f"Build modern dashboard view.\nRequires: #{task_1.short_id}\nBlocked by: {task_2.short_id}",
    )

    prereqs, _ = await task_srv.get_task_dependencies(task_3.id)
    assert len(prereqs) == 2
    prereq_ids = {p.id for p in prereqs}
    assert task_1.id in prereq_ids
    assert task_2.id in prereq_ids


@pytest.mark.asyncio
async def test_project_tree_data_derivation(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877666

    project = await proj_srv.create_project(guild_id=guild_id, name="Game Engine", prefix="GAM")
    task_a = await task_srv.create_task(
        guild_id=guild_id, title="Math Lib", creator_discord_id=1001, project_id=project.id
    )
    task_b = await task_srv.create_task(
        guild_id=guild_id,
        title="Physics Solver",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[task_a.short_id],
    )
    task_c = await task_srv.create_task(
        guild_id=guild_id,
        title="Character Controller",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[task_b.short_id],
    )

    # Initially:
    # A has no prereqs -> status notStarted -> state 'available'
    # B has prereq A (not complete) -> state 'locked'
    # C has prereq B (not complete) -> state 'locked'
    nodes, edges = await task_srv.get_project_tree_data(guild_id, project.id)
    assert len(nodes) == 3
    assert len(edges) == 2

    node_map = {n["key"]: n for n in nodes}
    assert node_map[task_a.short_id]["state"] == "available"
    assert node_map[task_b.short_id]["state"] == "locked"
    assert node_map[task_c.short_id]["state"] == "locked"

    # Complete task A
    await task_srv.update_status(
        task_a.id, TaskStatus.COMPLETED, expected_version=task_a.version, actor_discord_id=1001
    )

    nodes_after, _ = await task_srv.get_project_tree_data(guild_id, project.id)
    node_map_after = {n["key"]: n for n in nodes_after}
    assert node_map_after[task_a.short_id]["state"] == "complete"
    # Task B should now be unlocked and available / ready to start!
    assert node_map_after[task_b.short_id]["state"] == "available"
    assert node_map_after[task_c.short_id]["state"] == "locked"


@pytest.mark.asyncio
async def test_get_unresolved_prerequisites_lifecycle(services):
    """TaskService.get_unresolved_prerequisites returns only prerequisites that are not yet COMPLETED."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877665

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra DAG", prefix="DAG")

    task_1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Database Setup",
        creator_discord_id=1001,
        project_id=project.id,
    )
    task_2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Redis Cache",
        creator_discord_id=1001,
        project_id=project.id,
    )
    task_3 = await task_srv.create_task(
        guild_id=guild_id,
        title="API Gateway",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[task_1.short_id, task_2.short_id],
    )

    # Initially: both task_1 and task_2 are unresolved blockers
    unresolved_initial = await task_srv.get_unresolved_prerequisites(task_3.id)
    unresolved_ids = {t.id for t in unresolved_initial}
    assert unresolved_ids == {task_1.id, task_2.id}

    # Transition task_1 to IN_PROGRESS: still unresolved!
    task_1 = await task_srv.update_status(
        task_1.id,
        TaskStatus.IN_PROGRESS,
        expected_version=task_1.version,
        actor_discord_id=1001,
    )
    unresolved_in_prog = await task_srv.get_unresolved_prerequisites(task_3.id)
    assert {t.id for t in unresolved_in_prog} == {task_1.id, task_2.id}

    # Complete task_1: only task_2 remains unresolved
    task_1 = await task_srv.update_status(
        task_1.id,
        TaskStatus.COMPLETED,
        expected_version=task_1.version,
        actor_discord_id=1001,
    )
    unresolved_partial = await task_srv.get_unresolved_prerequisites(task_3.id)
    assert len(unresolved_partial) == 1
    assert unresolved_partial[0].id == task_2.id

    # Complete task_2: all blockers resolved
    await task_srv.update_status(
        task_2.id,
        TaskStatus.COMPLETED,
        expected_version=task_2.version,
        actor_discord_id=1001,
    )
    unresolved_done = await task_srv.get_unresolved_prerequisites(task_3.id)
    assert unresolved_done == []

    # Task without dependencies returns empty list
    assert await task_srv.get_unresolved_prerequisites(task_1.id) == []
