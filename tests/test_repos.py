from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


@pytest.mark.asyncio
async def test_repo_list_tasks_overdue_only_filters(repos, services):
    """Verify ITaskRepo.list_tasks with overdue_only=True filters for incomplete, unarchived, past-due tasks."""
    task_repo = repos["task"]
    proj_srv = services["project"]
    guild_id = 888777666

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra Ops", prefix="INF")

    now = datetime.now(UTC)
    past_time = now - timedelta(hours=3)
    future_time = now + timedelta(hours=5)

    # 1. Overdue task (past due, open, unarchived)
    t1 = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="INF-1",
        title="Overdue server migration",
        status=TaskStatus.IN_PROGRESS,
        due_at=past_time,
        creator_discord_id=1001,
    )
    await task_repo.create(t1)

    # 2. Future due task (not overdue)
    t2 = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="INF-2",
        title="Future backup",
        status=TaskStatus.NOT_STARTED,
        due_at=future_time,
        creator_discord_id=1001,
    )
    await task_repo.create(t2)

    # 3. No deadline task (not overdue)
    t3 = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="INF-3",
        title="Undated cleanup",
        status=TaskStatus.IN_PROGRESS,
        due_at=None,
        creator_discord_id=1001,
    )
    await task_repo.create(t3)

    # 4. Completed task with past deadline (not overdue)
    t4 = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="INF-4",
        title="Completed audit",
        status=TaskStatus.COMPLETED,
        due_at=past_time,
        creator_discord_id=1001,
    )
    await task_repo.create(t4)

    # 5. Archived task with past deadline (not overdue)
    t5 = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="INF-5",
        title="Archived old test",
        status=TaskStatus.IN_PROGRESS,
        due_at=past_time,
        archived_at=now,
        creator_discord_id=1001,
    )
    await task_repo.create(t5)

    # Execute query with overdue_only=True
    tasks, total = await task_repo.list_tasks(guild_id=guild_id, overdue_only=True)

    assert total == 1
    assert len(tasks) == 1
    assert tasks[0].id == t1.id
    assert tasks[0].short_id == "INF-1"


@pytest.mark.asyncio
async def test_task_service_list_tasks_overdue_only(services):
    """Verify TaskService.list_tasks exposes overdue_only parameter and returns overdue tasks."""
    task_srv = services["task"]
    proj_srv = services["project"]
    guild_id = 777666555

    project = await proj_srv.create_project(guild_id=guild_id, name="Security", prefix="SEC")

    past_time = datetime.now(UTC) - timedelta(hours=2)

    # Create task with past due_at
    task = await task_srv.create_task(
        guild_id=guild_id,
        project_id=project.id,
        title="Patch CVE-2026",
        creator_discord_id=1001,
        priority=PriorityLevel.HIGH,
        due_at=past_time,
    )

    tasks, count = await task_srv.list_tasks(guild_id=guild_id, overdue_only=True)
    assert count == 1
    assert len(tasks) == 1
    assert tasks[0].id == task.id
    assert tasks[0].is_overdue is True


@pytest.mark.asyncio
async def test_repo_list_tasks_overdue_pagination(repos, services):
    """Verify pagination works when filtering by overdue_only."""
    task_repo = repos["task"]
    proj_srv = services["project"]
    guild_id = 999888111

    project = await proj_srv.create_project(guild_id=guild_id, name="Core", prefix="COR")
    past_time = datetime.now(UTC) - timedelta(hours=1)

    for i in range(5):
        t = Task(
            id=uuid4(),
            guild_id=guild_id,
            project_id=project.id,
            short_id=f"COR-{i + 1}",
            title=f"Overdue item {i + 1}",
            status=TaskStatus.NOT_STARTED,
            due_at=past_time - timedelta(minutes=i),
            creator_discord_id=1001,
        )
        await task_repo.create(t)

    # Page 1 (limit 2, offset 0)
    p1_tasks, total = await task_repo.list_tasks(guild_id=guild_id, overdue_only=True, limit=2, offset=0)
    assert total == 5
    assert len(p1_tasks) == 2

    # Page 2 (limit 2, offset 2)
    p2_tasks, total2 = await task_repo.list_tasks(guild_id=guild_id, overdue_only=True, limit=2, offset=2)
    assert total2 == 5
    assert len(p2_tasks) == 2
    assert p1_tasks[0].id != p2_tasks[0].id
