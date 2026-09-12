from datetime import UTC, datetime, timedelta
from uuid import uuid4

import discord

from src.adapters.discord_bot.views.task_embed import build_task_embed, build_thread_workspace_content
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


def _make_task(**kwargs) -> Task:
    defaults = {
        "id": uuid4(),
        "guild_id": 123456789,
        "short_id": "TEST-1",
        "title": "Test Embed Task",
        "creator_discord_id": 111111,
        "status": TaskStatus.IN_PROGRESS,
        "priority": PriorityLevel.NORMAL,
    }
    defaults.update(kwargs)
    return Task(**defaults)


def test_build_task_embed_not_overdue():
    future = datetime.now(UTC) + timedelta(days=2)
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=future)

    embed = build_task_embed(task)
    assert embed.color == discord.Color.gold()

    status_field = next(f for f in embed.fields if f.name == "Status & Priority")
    assert "🟡 In Progress" in status_field.value
    assert "Overdue" not in status_field.value

    timeline_field = next(f for f in embed.fields if f.name == "Timeline & Details")
    assert "Target Due Date" in timeline_field.value
    assert "*(Overdue)*" not in timeline_field.value


def test_build_task_embed_overdue():
    past = datetime.now(UTC) - timedelta(hours=3)
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=past)

    embed = build_task_embed(task)
    # Color must be brand red when overdue
    assert embed.color == discord.Color.brand_red()

    # Status must include prominent overdue warning badge
    status_field = next(f for f in embed.fields if f.name == "Status & Priority")
    assert "🟡 In Progress • ⏰ **Overdue**" in status_field.value

    # Timeline must emphasize overdue
    timeline_field = next(f for f in embed.fields if f.name == "Timeline & Details")
    assert "⚠️ *(Overdue)*" in timeline_field.value


def test_build_task_embed_overdue_not_started():
    past = datetime.now(UTC) - timedelta(hours=1)
    task = _make_task(status=TaskStatus.NOT_STARTED, due_at=past)

    embed = build_task_embed(task)
    assert embed.color == discord.Color.brand_red()

    status_field = next(f for f in embed.fields if f.name == "Status & Priority")
    assert "⚪ Not Started • ⏰ **Overdue**" in status_field.value


def test_build_task_embed_completed_with_past_due_date():
    past = datetime.now(UTC) - timedelta(days=1)
    task = _make_task(
        status=TaskStatus.COMPLETED,
        due_at=past,
        completed_at=past,
    )

    embed = build_task_embed(task)
    assert embed.color == discord.Color.brand_green()

    status_field = next(f for f in embed.fields if f.name == "Status & Priority")
    assert "🟢 Completed" in status_field.value
    assert "Overdue" not in status_field.value


def test_build_task_embed_archived_with_past_due_date():
    past = datetime.now(UTC) - timedelta(days=2)
    task = _make_task(
        status=TaskStatus.IN_PROGRESS,
        due_at=past,
        archived_at=datetime.now(UTC),
    )

    embed = build_task_embed(task)
    assert embed.color == discord.Color.dark_grey()

    status_field = next(f for f in embed.fields if f.name == "Status & Priority")
    assert "Overdue" not in status_field.value


def test_build_thread_workspace_content_overdue():
    past = datetime.now(UTC) - timedelta(hours=2)
    task_overdue = _make_task(status=TaskStatus.IN_PROGRESS, due_at=past)
    content_overdue = build_thread_workspace_content(task_overdue)
    assert "• ⏰ Overdue" in content_overdue

    future = datetime.now(UTC) + timedelta(days=1)
    task_normal = _make_task(status=TaskStatus.IN_PROGRESS, due_at=future)
    content_normal = build_thread_workspace_content(task_normal)
    assert "• ⏰ Overdue" not in content_normal


def test_reschedule_task_clears_overdue_embed_styling():
    past = datetime.now(UTC) - timedelta(hours=5)
    task = _make_task(status=TaskStatus.IN_PROGRESS, due_at=past)

    # Initially overdue
    assert task.is_overdue is True
    embed_overdue = build_task_embed(task)
    assert embed_overdue.color == discord.Color.brand_red()
    assert "⏰ **Overdue**" in embed_overdue.fields[0].value

    # Reschedule to future
    future = datetime.now(UTC) + timedelta(days=3)
    task.due_at = future

    # Overdue state clears immediately
    assert task.is_overdue is False
    embed_rescheduled = build_task_embed(task)
    assert embed_rescheduled.color == discord.Color.gold()
    assert "⏰ **Overdue**" not in embed_rescheduled.fields[0].value
    assert "⚠️ *(Overdue)*" not in str(embed_rescheduled.fields[2].value)
