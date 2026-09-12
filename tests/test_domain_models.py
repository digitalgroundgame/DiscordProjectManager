from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


def _make_task(**kwargs) -> Task:
    defaults = {
        "id": uuid4(),
        "guild_id": 123456789,
        "short_id": "TEST-1",
        "title": "Test Task",
        "creator_discord_id": 111111,
        "status": TaskStatus.NOT_STARTED,
        "priority": PriorityLevel.NORMAL,
    }
    defaults.update(kwargs)
    return Task(**defaults)


def test_task_is_overdue_when_past_due():
    past = datetime.now(UTC) - timedelta(hours=2)

    # Not started task past due
    task_not_started = _make_task(status=TaskStatus.NOT_STARTED, due_at=past)
    assert task_not_started.is_overdue is True

    # In progress task past due
    task_in_prog = _make_task(status=TaskStatus.IN_PROGRESS, due_at=past)
    assert task_in_prog.is_overdue is True


def test_task_not_overdue_when_future_due():
    future = datetime.now(UTC) + timedelta(days=2)
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=future)
    assert task.is_overdue is False


def test_task_not_overdue_when_no_due_date():
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=None)
    assert task.is_overdue is False


def test_task_not_overdue_when_completed():
    past = datetime.now(UTC) - timedelta(days=1)
    task = _make_task(
        status=TaskStatus.COMPLETED,
        due_at=past,
        completed_at=past,
    )
    assert task.is_completed is True
    assert task.is_overdue is False


def test_task_not_overdue_when_archived():
    past = datetime.now(UTC) - timedelta(days=1)
    task = _make_task(
        status=TaskStatus.IN_PROGRESS,
        due_at=past,
        archived_at=datetime.now(UTC),
    )
    assert task.is_archived is True
    assert task.is_overdue is False


def test_task_is_overdue_handles_naive_and_utc_datetimes():
    now_utc = datetime.now(UTC)
    naive_past = (now_utc - timedelta(hours=3)).replace(tzinfo=None)
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=naive_past)
    assert task.is_overdue is True

    naive_future = (now_utc + timedelta(hours=3)).replace(tzinfo=None)
    task_future = _make_task(status=TaskStatus.IN_PROGRESS, due_at=naive_future)
    assert task_future.is_overdue is False
