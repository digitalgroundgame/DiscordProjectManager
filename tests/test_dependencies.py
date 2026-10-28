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


def test_parse_prerequisite_short_ids():
    """Verify comma/space-separated prerequisite short ID normalization and deduplication."""
    from src.services.task_service import parse_prerequisite_short_ids

    assert parse_prerequisite_short_ids(None) == []
    assert parse_prerequisite_short_ids("") == []
    assert parse_prerequisite_short_ids("   ,  ") == []
    assert parse_prerequisite_short_ids("be-1, #FE-2  [OPS-10], be-1") == [
        "BE-1",
        "FE-2",
        "OPS-10",
    ]
    assert parse_prerequisite_short_ids(["#be-1, fe-2", "FE-2", "[QA-3]"]) == [
        "BE-1",
        "FE-2",
        "QA-3",
    ]


@pytest.mark.asyncio
async def test_create_task_fails_upfront_on_missing_prerequisite(services):
    """If a prerequisite_short_id does not exist, create_task raises TaskNotFoundError without creating the task."""
    from src.services.task_service import TaskNotFoundError

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877670

    project = await proj_srv.create_project(guild_id=guild_id, name="Upfront Check", prefix="UFC")
    task_1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Valid Prerequisite",
        creator_discord_id=1001,
        project_id=project.id,
    )

    with pytest.raises(TaskNotFoundError, match="UFC-999"):
        await task_srv.create_task(
            guild_id=guild_id,
            title="Should Not Be Created",
            creator_discord_id=1001,
            project_id=project.id,
            prerequisite_short_ids=[task_1.short_id, "UFC-999"],
        )

    # Verify only task_1 exists and the next task number was not consumed
    tasks, total = await task_srv.list_tasks(guild_id=guild_id, project_id=project.id)
    assert total == 1
    assert tasks[0].id == task_1.id

    task_2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Next Valid Task",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[f"#{task_1.short_id.lower()}"],
    )
    assert task_2.short_id == "UFC-2"
    prereqs, _ = await task_srv.get_task_dependencies(task_2.id)
    assert [p.id for p in prereqs] == [task_1.id]


@pytest.mark.asyncio
async def test_pm_cog_task_autocomplete_multi_id_and_project_scope(services):
    """Verify PmCog.task_autocomplete scopes by project_name when present and supports comma-separated multi-IDs."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.adapters.discord_bot.cogs.pm_cog import PmCog

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877671

    proj_a = await proj_srv.create_project(guild_id=guild_id, name="Alpha", prefix="ALP")
    proj_b = await proj_srv.create_project(guild_id=guild_id, name="Beta", prefix="BET")

    t_a1 = await task_srv.create_task(
        guild_id=guild_id, title="Alpha One", creator_discord_id=1001, project_id=proj_a.id
    )
    t_a2 = await task_srv.create_task(
        guild_id=guild_id, title="Alpha Two", creator_discord_id=1001, project_id=proj_a.id
    )
    await task_srv.create_task(guild_id=guild_id, title="Beta One", creator_discord_id=1001, project_id=proj_b.id)

    bot = MagicMock()
    cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        task_service=task_srv,
        squad_service=services["squad"],
        user_service=services["user"],
    )

    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=guild_id)
    interaction.guild_id = guild_id
    interaction.namespace = SimpleNamespace(project_name="Alpha")

    # 1. Scoped to Alpha project
    choices = await cog.task_autocomplete(interaction, "")
    values = [c.value for c in choices]
    assert t_a1.short_id in values
    assert t_a2.short_id in values
    assert "BET-1" not in values

    # 2. Multi-ID comma-separated autocomplete preserves prefix and excludes already-entered IDs
    multi_choices = await cog.task_autocomplete(interaction, f"{t_a1.short_id}, ")
    multi_values = [c.value for c in multi_choices]
    assert f"{t_a1.short_id}, {t_a2.short_id}" in multi_values
    assert f"{t_a1.short_id}, {t_a1.short_id}" not in multi_values


@pytest.mark.asyncio
async def test_task_details_modal_and_draft_view_prerequisites(services):
    """Verify TaskDetailsModal has no prereqs input and TaskCreateDraftView preserves prerequisites across updates."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from src.adapters.discord_bot.views.task_builder import TaskCreateDraftView, TaskDetailsModal

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877672

    project = await proj_srv.create_project(guild_id=guild_id, name="Modal Project", prefix="MOD")
    prereq_task = await task_srv.create_task(
        guild_id=guild_id,
        title="Base Setup",
        creator_discord_id=1001,
        project_id=project.id,
    )

    # 1. TaskDetailsModal only contains Task Title and Description/Body inputs
    modal = TaskDetailsModal(
        task_service=task_srv,
        project=project,
    )
    assert not hasattr(modal, "prereqs_input")
    modal.title_input._value = "Dependent Feature"
    modal.desc_input._value = "Feature details"

    interaction_ok = MagicMock()
    interaction_ok.guild = SimpleNamespace(id=guild_id)
    interaction_ok.guild_id = guild_id
    interaction_ok.response = AsyncMock()
    await modal.on_submit(interaction_ok)
    interaction_ok.response.send_message.assert_awaited_once()
    sent_kwargs = interaction_ok.response.send_message.call_args.kwargs
    draft_view: TaskCreateDraftView = sent_kwargs["view"]

    # 2. Selecting prerequisites in TaskCreateDraftView and updating another field preserves prerequisites
    draft_view.prerequisite_short_ids = [prereq_task.short_id]
    draft_view.priority_select._values = ["high"]
    interaction_prio = MagicMock()
    interaction_prio.response = AsyncMock()
    await draft_view._on_priority_selected(interaction_prio)
    edited_embed = interaction_prio.response.edit_message.call_args.kwargs["embed"]
    assert f"• **Prerequisites**: `[{prereq_task.short_id}]`" in edited_embed.description


@pytest.mark.asyncio
async def test_completing_prerequisite_enqueues_task_unblocked_only_when_all_satisfied(services, db_session):
    """Verify TASK_UNBLOCKED is enqueued only when 100% of prerequisites for a NOT_STARTED task are completed."""
    from sqlalchemy import select

    from src.adapters.db.tables import OutboxEventTable
    from src.domain.enums import EventType

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877680

    project = await proj_srv.create_project(guild_id=guild_id, name="Unblock Engine", prefix="UNB")
    prereq_1 = await task_srv.create_task(
        guild_id=guild_id, title="Backend API", creator_discord_id=1001, project_id=project.id
    )
    prereq_2 = await task_srv.create_task(
        guild_id=guild_id, title="Auth Service", creator_discord_id=1001, project_id=project.id
    )
    dependent_task = await task_srv.create_task(
        guild_id=guild_id,
        title="Frontend UI",
        creator_discord_id=1001,
        assignee_discord_id=2002,
        watchers=[3003],
        project_id=project.id,
        prerequisite_short_ids=[prereq_1.short_id, prereq_2.short_id],
    )
    await task_srv.update_discord_message_ids(dependent_task.id, discord_message_id=888999, discord_thread_id=777888)

    # 1. Completing prereq_1 (50% satisfied) must NOT enqueue TASK_UNBLOCKED
    prereq_1 = await task_srv.update_status(
        prereq_1.id, TaskStatus.COMPLETED, expected_version=prereq_1.version, actor_discord_id=1001
    )
    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.event_type == EventType.TASK_UNBLOCKED.value)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 0

    # 2. Moving prereq_2 to IN_PROGRESS must NOT enqueue TASK_UNBLOCKED
    prereq_2 = await task_srv.update_status(
        prereq_2.id, TaskStatus.IN_PROGRESS, expected_version=prereq_2.version, actor_discord_id=1001
    )
    db_session.expire_all()
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 0

    # 3. Completing prereq_2 (100% satisfied) MUST enqueue TASK_UNBLOCKED for dependent_task
    prereq_2 = await task_srv.update_status(
        prereq_2.id, TaskStatus.COMPLETED, expected_version=prereq_2.version, actor_discord_id=1001
    )
    db_session.expire_all()
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1

    evt = rows[0]
    assert evt.payload["task_id"] == str(dependent_task.id)
    assert evt.payload["short_id"] == dependent_task.short_id
    assert evt.payload["title"] == "Frontend UI"
    assert evt.payload["assignee_discord_id"] == 2002
    assert evt.payload["watchers"] == [3003]
    assert evt.payload["discord_thread_id"] == 777888
    assert evt.payload["discord_message_id"] == 888999
    assert evt.payload["unblocked_by_short_id"] == prereq_2.short_id
    prereq_short_ids = {p["short_id"] for p in evt.payload["prerequisites"]}
    assert prereq_short_ids == {prereq_1.short_id, prereq_2.short_id}


@pytest.mark.asyncio
async def test_reopening_task_or_non_not_started_dependent_does_not_trigger_false_unblock(services, db_session):
    """Verify reopening a prerequisite or completing a blocker for an already started/completed/archived task
    does not emit false positives."""
    from sqlalchemy import select

    from src.adapters.db.tables import OutboxEventTable
    from src.domain.enums import EventType

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877681

    project = await proj_srv.create_project(guild_id=guild_id, name="Reopen Guard", prefix="RPN")
    prereq = await task_srv.create_task(
        guild_id=guild_id, title="Core Schema", creator_discord_id=1001, project_id=project.id
    )
    dep_in_prog = await task_srv.create_task(
        guild_id=guild_id,
        title="Already Started Task",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[prereq.short_id],
    )
    dep_archived = await task_srv.create_task(
        guild_id=guild_id,
        title="Archived Dependent",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[prereq.short_id],
    )
    dep_ready = await task_srv.create_task(
        guild_id=guild_id,
        title="Waiting Dependent",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[prereq.short_id],
    )

    # Set dep_in_prog to IN_PROGRESS and archive dep_archived before prereq completes
    await task_srv.update_status(
        dep_in_prog.id, TaskStatus.IN_PROGRESS, expected_version=dep_in_prog.version, actor_discord_id=1001
    )
    await task_srv.archive_task(dep_archived.id, actor_discord_id=1001)

    # Complete prereq -> only dep_ready (NOT_STARTED & unarchived) should emit TASK_UNBLOCKED
    prereq = await task_srv.update_status(
        prereq.id, TaskStatus.COMPLETED, expected_version=prereq.version, actor_discord_id=1001
    )
    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.event_type == EventType.TASK_UNBLOCKED.value)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["task_id"] == str(dep_ready.id)

    # Reopen prereq (COMPLETED -> IN_PROGRESS) and (IN_PROGRESS -> NOT_STARTED) -> must NOT emit any new TASK_UNBLOCKED
    prereq = await task_srv.update_status(
        prereq.id, TaskStatus.IN_PROGRESS, expected_version=prereq.version, actor_discord_id=1001
    )
    prereq = await task_srv.update_status(
        prereq.id, TaskStatus.NOT_STARTED, expected_version=prereq.version, actor_discord_id=1001
    )
    db_session.expire_all()
    rows_after_reopen = (await db_session.execute(stmt)).scalars().all()
    assert len(rows_after_reopen) == 1

    # Reopening dep_ready itself (if it had been completed and reopened) does not emit TASK_UNBLOCKED
    dep_ready = await task_srv.update_status(
        dep_ready.id, TaskStatus.COMPLETED, expected_version=dep_ready.version, actor_discord_id=1001
    )
    dep_ready = await task_srv.update_status(
        dep_ready.id, TaskStatus.NOT_STARTED, expected_version=dep_ready.version, actor_discord_id=1001
    )
    db_session.expire_all()
    rows_after_dep_reopen = (await db_session.execute(stmt)).scalars().all()
    assert len(rows_after_dep_reopen) == 1
