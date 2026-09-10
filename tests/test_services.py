import pytest

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.exceptions import ValidationError
from src.services.task_service import StaleVersionError


@pytest.mark.asyncio
async def test_project_lifecycle_and_prefix(services):
    proj_srv = services["project"]
    guild_id = 111111111111111111

    # Create project with auto prefix
    p1 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Infrastructure & DevOps",
        description="Core platform infra",
    )
    assert p1.name == "Infrastructure & DevOps"
    assert p1.prefix == "ID"  # Derived prefix
    assert p1.next_task_number == 1

    # Duplicate name should fail
    with pytest.raises(ValueError, match="already exists"):
        await proj_srv.create_project(guild_id=guild_id, name="Infrastructure & DevOps")

    # Custom prefix
    p2 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Frontend App",
        prefix="FE",
    )
    assert p2.prefix == "FE"


@pytest.mark.asyncio
async def test_task_creation_and_short_id(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 222222222222222222

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend Core",
        prefix="BE",
    )

    # Create task 1
    t1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Setup PostgreSQL",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.HIGH,
    )
    assert t1.short_id == "BE-1"
    assert t1.task_number == 1
    assert t1.version == 1
    assert t1.status == TaskStatus.NOT_STARTED

    # Create task 2
    t2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Implement Outbox Worker",
        creator_discord_id=1001,
        project_id=project.id,
    )
    assert t2.short_id == "BE-2"
    assert t2.task_number == 2

    # Standalone task without project container is prohibited (ADR 0002)
    with pytest.raises(ValidationError, match="project container must be specified"):
        await task_srv.create_task(
            guild_id=guild_id,
            title="Quick standalone bugfix",
            creator_discord_id=1002,
            project_id=None,
        )


@pytest.mark.asyncio
async def test_standalone_tasks_prohibited_without_project(services):
    task_srv = services["task"]
    guild_id = 666666666666666666

    # Attempting to create tasks without project_id raises ValidationError
    with pytest.raises(ValidationError, match="project container must be specified"):
        await task_srv.create_task(
            guild_id=guild_id,
            title="Standalone capture",
            creator_discord_id=1001,
            project_id=None,
        )


@pytest.mark.asyncio
async def test_task_status_lifecycle_and_cas_concurrency(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 333333333333333333

    project = await proj_srv.create_project(guild_id=guild_id, name="Security Audit", prefix="SEC")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Rotate API Secrets",
        creator_discord_id=1001,
        project_id=project.id,
    )

    # 1. Transition to IN_PROGRESS
    updated = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.IN_PROGRESS,
        expected_version=1,
        actor_discord_id=1002,
        notes="Started key rotation",
    )
    assert updated.status == TaskStatus.IN_PROGRESS
    assert updated.version == 2
    assert updated.completed_at is None

    # 2. CAS Conflict: Attempting update with stale version 1 should fail
    with pytest.raises(StaleVersionError, match="already modified"):
        await task_srv.update_status(
            task_id=task.id,
            new_status=TaskStatus.COMPLETED,
            expected_version=1,  # Stale version!
            actor_discord_id=1003,
        )

    # 3. Transition to COMPLETED with correct version 2
    completed = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=2,
        actor_discord_id=1002,
        notes="Rotation finished",
    )
    assert completed.status == TaskStatus.COMPLETED
    assert completed.version == 3
    assert completed.completed_at is not None

    # 4. Verify audit history
    history = await task_srv.get_history(task.id)
    assert len(history) == 3  # CREATED, STATUS_CHANGE (inProgress), STATUS_CHANGE (completed)
    assert history[0].action.value == "CREATED"
    assert history[1].new_status == TaskStatus.IN_PROGRESS
    assert history[2].new_status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_archival_and_restoration(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 444444444444444444

    project = await proj_srv.create_project(guild_id=guild_id, name="Legacy System", prefix="LEG")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Decommission old server",
        creator_discord_id=1001,
        project_id=project.id,
    )

    # Archive project
    archived_proj = await proj_srv.archive_project(project.id)
    assert archived_proj.is_archived

    # Task should also be archived via cascade
    t_refreshed = await task_srv.get_by_id(task.id)
    assert t_refreshed.is_archived

    # Unarchive project
    restored_proj = await proj_srv.unarchive_project(project.id)
    assert not restored_proj.is_archived

    # Task unarchive
    restored_task = await task_srv.unarchive_task(task.id, actor_discord_id=1001)
    assert not restored_task.is_archived


@pytest.mark.asyncio
async def test_task_update_assignee_priority_and_details(services):
    from datetime import UTC, datetime, timedelta

    from src.domain.enums import TaskHistoryAction

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 555555555555555555

    project = await proj_srv.create_project(guild_id=guild_id, name="Ops Pipeline", prefix="OPS")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Deploy Cluster",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.NORMAL,
    )
    assert task.assignee_discord_id is None
    assert task.priority == PriorityLevel.NORMAL

    # 1. Update priority
    p_updated = await task_srv.update_priority(task.id, PriorityLevel.HIGH, actor_discord_id=1002)
    assert p_updated.priority == PriorityLevel.HIGH
    assert p_updated.version == 2

    # 2. Update assignee
    a_updated = await task_srv.update_assignee(task.id, new_assignee_id=2002, actor_discord_id=1001)
    assert a_updated.assignee_discord_id == 2002
    assert a_updated.version == 3

    # 3. Update details (due date and watchers)
    due_target = datetime.now(UTC) + timedelta(days=2)
    d_updated = await task_srv.update_details(
        task_id=task.id,
        actor_discord_id=1001,
        title="Deploy Production Cluster",
        body="Upgraded specs and memory limits",
        due_at=due_target,
        watchers=[3001, 3002],
    )
    assert d_updated.title == "Deploy Production Cluster"
    assert d_updated.body == "Upgraded specs and memory limits"
    assert d_updated.due_at is not None
    assert set(d_updated.watchers) == {3001, 3002}
    assert d_updated.version == 4

    # 4. Verify history trail
    history = await task_srv.get_history(task.id)
    actions = [h.action for h in history]
    assert TaskHistoryAction.CREATED in actions
    assert TaskHistoryAction.PRIORITY_CHANGED in actions
    assert TaskHistoryAction.ASSIGNED in actions
    assert TaskHistoryAction.UPDATED in actions


@pytest.mark.asyncio
async def test_user_service_preferences(services):
    user_srv = services["user"]
    guild_id = 999111222
    user_id = 123456789

    # Default preference is DM
    pref = await user_srv.get_preference(guild_id, user_id)
    from src.domain.enums import NotificationPreference

    assert pref == NotificationPreference.DM

    # Update to CHANNEL
    updated = await user_srv.set_preference(guild_id, user_id, NotificationPreference.CHANNEL)
    assert updated.notify_preference == NotificationPreference.CHANNEL

    pref2 = await user_srv.get_preference(guild_id, user_id)
    assert pref2 == NotificationPreference.CHANNEL

    # Bulk fetch
    bulk = await user_srv.get_preferences_bulk(guild_id, [user_id, 999999])
    assert bulk[user_id] == NotificationPreference.CHANNEL
    assert bulk[999999] == NotificationPreference.DM


@pytest.mark.asyncio
async def test_task_creation_atomic_rollback(services, repos):
    """Verify that if outbox enqueueing fails, the task and history are completely rolled back."""
    task_srv = services["task"]
    proj_srv = services["project"]
    outbox_srv = services["outbox"]
    task_repo = repos["task"]
    guild_id = 999888777666

    project = await proj_srv.create_project(guild_id=guild_id, name="Rollback Test", prefix="RBK")

    # Mock outbox_service.enqueue_event to raise an exception
    original_enqueue = outbox_srv.enqueue_event

    async def failing_enqueue(*args, **kwargs):
        raise RuntimeError("Simulated transient database/outbox error during enqueue")

    outbox_srv.enqueue_event = failing_enqueue

    try:
        with pytest.raises(RuntimeError, match="Simulated transient database/outbox error"):
            await task_srv.create_task(
                guild_id=guild_id,
                title="This Task Must Roll Back",
                creator_discord_id=12345,
                project_id=project.id,
            )

        # Confirm the task was NEVER persisted to the database
        tasks, total = await task_repo.list_tasks(guild_id=guild_id)
        assert total == 0
        assert len(tasks) == 0
    finally:
        outbox_srv.enqueue_event = original_enqueue


@pytest.mark.asyncio
async def test_team_service_leads_lifecycle(services):
    team_srv = services["team"]
    guild_id = 999888111

    team = await team_srv.create_team(guild_id=guild_id, name="Infra Squad", discord_role_id=123456)
    assert team.id is not None

    # Initial state: no leads
    leads = await team_srv.list_team_leads(team.id)
    assert len(leads) == 0
    assert await team_srv.is_team_lead(team.id, 9001) is False

    # Add lead
    await team_srv.add_team_lead(team.id, 9001)
    await team_srv.add_team_lead(team.id, 9002)

    leads = await team_srv.list_team_leads(team.id)
    assert set(leads) == {9001, 9002}
    assert await team_srv.is_team_lead(team.id, 9001) is True
    assert await team_srv.is_team_lead(team.id, 9002) is True
    assert await team_srv.is_team_lead(team.id, 9003) is False

    # Remove lead
    await team_srv.remove_team_lead(team.id, 9001)
    leads = await team_srv.list_team_leads(team.id)
    assert leads == [9002]
    assert await team_srv.is_team_lead(team.id, 9001) is False
    assert await team_srv.is_team_lead(team.id, 9002) is True


@pytest.mark.asyncio
async def test_task_service_update_outbox_events(services, db_session):
    """Verify update_priority, update_assignee, and update_details enqueue TASK_UPDATED outbox events."""
    from sqlalchemy import select

    from src.adapters.db.tables import OutboxEventTable
    from src.domain.enums import EventType

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 888777666555

    project = await proj_srv.create_project(guild_id=guild_id, name="Outbox Test Proj", prefix="OTP")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Original Title",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.LOW,
        watchers=[5001],
    )

    # 1. Update priority
    await task_srv.update_priority(task.id, PriorityLevel.HIGH, actor_discord_id=1001)

    # 2. Update assignee
    await task_srv.update_assignee(task.id, new_assignee_id=2001, actor_discord_id=1001)

    # 3. Update details
    await task_srv.update_details(
        task_id=task.id,
        actor_discord_id=1001,
        title="Updated Title",
        watchers=[5001, 5002],
    )

    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key.like(f"%{task.id}%"))
    res = await db_session.execute(stmt)
    events = res.scalars().all()

    types = [e.event_type for e in events]
    assert EventType.TASK_CREATED.value in types
    assert types.count(EventType.TASK_UPDATED.value) == 3

    # Check payload structure for details update
    details_evt = next(e for e in events if e.idempotency_key.startswith(f"task_details:{task.id}"))
    assert details_evt.payload["update_type"] == "details"
    assert "Title: `Original Title` ➔ **`Updated Title`**" in details_evt.payload["changes"]
    assert "Added watchers: <@5002>" in details_evt.payload["changes"]
    assert set(details_evt.payload["watchers"]) == {5001, 5002}
