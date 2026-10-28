from datetime import UTC, datetime
from uuid import uuid4

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task
from src.domain.standard_mappings import (
    to_ms_graph_todo_task,
    to_rfc5545_vcalendar,
    to_rfc5545_vevent,
    to_rfc5545_vtodo,
)


def test_rfc5545_vtodo_serialization():
    task_id = uuid4()
    due = datetime(2026, 4, 15, 18, 0, 0, tzinfo=UTC)
    task = Task(
        id=task_id,
        guild_id=123456789012345678,
        short_id="INF-1",
        title="Deploy API Gateway",
        body="Deploy FastAPI with PostgreSQL.\nInclude tests.",
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        creator_discord_id=111222333444555666,
        assignee_discord_id=999888777666555444,
        due_at=due,
        watchers=[555666777888999000],
    )

    vtodo = to_rfc5545_vtodo(task)

    assert "BEGIN:VTODO" in vtodo
    assert f"UID:{task_id}" in vtodo
    assert "SUMMARY:Deploy API Gateway" in vtodo
    assert "STATUS:IN-PROCESS" in vtodo
    assert "PRIORITY:1" in vtodo
    assert "ORGANIZER;CN=Discord:mailto:111222333444555666@discord.local" in vtodo
    assert "ATTENDEE;ROLE=REQ-PARTICIPANT;CN=Assignee:mailto:999888777666555444@discord.local" in vtodo
    assert "ATTENDEE;ROLE=NON-PARTICIPANT;CN=Watcher:mailto:555666777888999000@discord.local" in vtodo
    assert "DUE:20260415T180000Z" in vtodo
    assert "X-DISCORD-SHORT-ID:INF-1" in vtodo
    assert "END:VTODO" in vtodo


def test_rfc5545_vevent_serialization():
    task_id = uuid4()
    due = datetime(2026, 4, 15, 18, 0, 0, tzinfo=UTC)
    task = Task(
        id=task_id,
        guild_id=123456789012345678,
        short_id="INF-1",
        title="Deploy API Gateway",
        body="Deploy FastAPI with PostgreSQL.",
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        creator_discord_id=111222333444555666,
        assignee_discord_id=999888777666555444,
        due_at=due,
        watchers=[555666777888999000],
    )

    vevent = to_rfc5545_vevent(task)
    assert vevent is not None
    assert "BEGIN:VEVENT" in vevent
    assert f"UID:{task_id}" in vevent
    assert "SUMMARY:[INF-1] Deploy API Gateway" in vevent
    assert "STATUS:CONFIRMED" in vevent
    assert "PRIORITY:1" in vevent
    assert "DTSTART:20260415T170000Z" in vevent
    assert "DTEND:20260415T180000Z" in vevent
    assert "ORGANIZER;CN=Discord:mailto:111222333444555666@discord.local" in vevent
    assert "ATTENDEE;ROLE=REQ-PARTICIPANT;CN=Assignee:mailto:999888777666555444@discord.local" in vevent
    assert "X-DISCORD-SHORT-ID:INF-1" in vevent
    assert "END:VEVENT" in vevent


def test_rfc5545_vevent_without_due_date_returns_none():
    task = Task(
        guild_id=123456789012345678,
        short_id="INF-2",
        title="No Due Date Task",
        creator_discord_id=111222333444555666,
        due_at=None,
    )
    assert to_rfc5545_vevent(task) is None


def test_rfc5545_vcalendar_wrapping():
    task_id = uuid4()
    due = datetime(2026, 4, 15, 18, 0, 0, tzinfo=UTC)
    task = Task(
        id=task_id,
        guild_id=123456789012345678,
        short_id="INF-1",
        title="Deploy API Gateway",
        creator_discord_id=111222333444555666,
        due_at=due,
    )

    # Event mode (default)
    cal_event = to_rfc5545_vcalendar([task], cal_name="Test Calendar", format_type="event")
    assert cal_event.startswith("BEGIN:VCALENDAR\r\n")
    assert "VERSION:2.0\r\n" in cal_event
    assert "PRODID:-//Digital Ground Game//Discord Project Manager//EN\r\n" in cal_event
    assert "X-WR-CALNAME:Test Calendar\r\n" in cal_event
    assert "BEGIN:VEVENT" in cal_event
    assert "BEGIN:VTODO" not in cal_event
    assert cal_event.endswith("END:VCALENDAR\r\n")

    # Todo mode
    cal_todo = to_rfc5545_vcalendar([task], cal_name="Test Calendar", format_type="todo")
    assert "BEGIN:VTODO" in cal_todo
    assert "BEGIN:VEVENT" not in cal_todo

    # Both mode
    cal_both = to_rfc5545_vcalendar([task], cal_name="Test Calendar", format_type="both")
    assert "BEGIN:VEVENT" in cal_both
    assert "BEGIN:VTODO" in cal_both


def test_ms_graph_todo_task_serialization():
    task_id = uuid4()
    due = datetime(2026, 4, 15, 18, 0, 0, tzinfo=UTC)
    task = Task(
        id=task_id,
        guild_id=123456789012345678,
        short_id="INF-1",
        title="Deploy API Gateway",
        body="Deploy FastAPI with PostgreSQL.",
        status=TaskStatus.COMPLETED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=111222333444555666,
        assignee_discord_id=999888777666555444,
        due_at=due,
        completed_at=due,
        watchers=[555666777888999000],
    )

    graph = to_ms_graph_todo_task(task)

    assert graph["id"] == str(task_id)
    assert graph["title"] == "Deploy API Gateway"
    assert graph["status"] == "completed"
    assert graph["importance"] == "normal"
    assert graph["body"]["content"] == "Deploy FastAPI with PostgreSQL."
    assert graph["dueDateTime"]["dateTime"] == "2026-04-15T18:00:00"
    assert graph["completedDateTime"]["dateTime"] == "2026-04-15T18:00:00"

    ext = graph["extensions"][0]
    assert ext["shortId"] == "INF-1"
    assert ext["assigneeDiscordId"] == "999888777666555444"
    assert ext["watchers"] == ["555666777888999000"]
