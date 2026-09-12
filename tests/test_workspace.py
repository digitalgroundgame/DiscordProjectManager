from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.views.forum_helpers import (
    OVERDUE_KEYWORDS,
    STANDARD_PM_TAG_DEFINITIONS,
    resolve_forum_tags,
    setup_forum_tags,
)
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


def _create_mock_tag(tag_id: int, name: str, emoji: str | None = None) -> MagicMock:
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def test_standard_pm_tags_include_overdue_definition():
    """Verify STANDARD_PM_TAG_DEFINITIONS and OVERDUE_KEYWORDS include the Overdue tag."""
    overdue_def = next((t for t in STANDARD_PM_TAG_DEFINITIONS if t["name"] == "Overdue"), None)
    assert overdue_def is not None
    assert overdue_def["emoji"] == "⏰"
    assert "overdue" in overdue_def["keywords"]
    assert "overdue" in OVERDUE_KEYWORDS


@pytest.mark.asyncio
async def test_setup_forum_tags_adds_overdue_tag():
    """Verify setup_forum_tags adds the Overdue tag if it is missing."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()

    added, _total, err = await setup_forum_tags(mock_forum)
    assert err is None
    assert added > 0
    assert _total > 0
    saved_tags = mock_forum.edit.call_args.kwargs.get("available_tags")
    assert any(t.name == "Overdue" and str(t.emoji) == "⏰" for t in saved_tags)


def test_resolve_forum_tags_applies_overdue_tag_when_task_is_overdue():
    """Verify resolve_forum_tags adds ⏰ Overdue tag when task.is_overdue is True."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_todo = _create_mock_tag(1, "Not Started")
    tag_high = _create_mock_tag(2, "🔴 High Priority")
    tag_overdue = _create_mock_tag(3, "⏰ Overdue")
    mock_forum.available_tags = [tag_todo, tag_high, tag_overdue]

    past_due = datetime.now(UTC) - timedelta(hours=2)
    task = Task(
        id=uuid4(),
        short_id="OPS-1",
        guild_id=12345,
        title="Fix production latency",
        creator_discord_id=1001,
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.HIGH,
        due_at=past_due,
    )
    assert task.is_overdue is True

    applied = resolve_forum_tags(mock_forum, task)
    assert tag_todo in applied
    assert tag_high in applied
    assert tag_overdue in applied


def test_resolve_forum_tags_removes_overdue_tag_when_completed():
    """Verify resolve_forum_tags removes ⏰ Overdue tag when task is completed, even if in existing_tags."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_done = _create_mock_tag(1, "Completed")
    tag_normal = _create_mock_tag(2, "Normal Priority")
    tag_overdue = _create_mock_tag(3, "⏰ Overdue")
    mock_forum.available_tags = [tag_done, tag_normal, tag_overdue]

    past_due = datetime.now(UTC) - timedelta(hours=5)
    task = Task(
        id=uuid4(),
        short_id="OPS-2",
        guild_id=12345,
        title="Fix completed task",
        creator_discord_id=1001,
        status=TaskStatus.COMPLETED,
        priority=PriorityLevel.NORMAL,
        due_at=past_due,
    )
    assert task.is_overdue is False

    # Thread previously had overdue tag applied
    applied = resolve_forum_tags(mock_forum, task, existing_tags=[tag_overdue])
    assert tag_done in applied
    assert tag_normal in applied
    assert tag_overdue not in applied


def test_resolve_forum_tags_removes_overdue_tag_when_archived():
    """Verify resolve_forum_tags removes ⏰ Overdue tag when task is archived."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_wip = _create_mock_tag(1, "In Progress")
    tag_overdue = _create_mock_tag(2, "⏰ Overdue")
    mock_forum.available_tags = [tag_wip, tag_overdue]

    past_due = datetime.now(UTC) - timedelta(hours=5)
    task = Task(
        id=uuid4(),
        short_id="OPS-3",
        guild_id=12345,
        title="Archived task",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        due_at=past_due,
        archived_at=datetime.now(UTC),
    )
    assert task.is_overdue is False

    applied = resolve_forum_tags(mock_forum, task, existing_tags=[tag_overdue])
    assert tag_overdue not in applied


def test_resolve_forum_tags_removes_overdue_tag_when_rescheduled_to_future():
    """Verify resolve_forum_tags removes ⏰ Overdue tag when deadline is moved to the future."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_wip = _create_mock_tag(1, "In Progress")
    tag_overdue = _create_mock_tag(2, "⏰ Overdue")
    mock_forum.available_tags = [tag_wip, tag_overdue]

    future_due = datetime.now(UTC) + timedelta(days=2)
    task = Task(
        id=uuid4(),
        short_id="OPS-4",
        guild_id=12345,
        title="Rescheduled task",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        due_at=future_due,
    )
    assert task.is_overdue is False

    applied = resolve_forum_tags(mock_forum, task, existing_tags=[tag_overdue])
    assert tag_overdue not in applied


def test_resolve_forum_tags_restores_overdue_tag_upon_reopening():
    """Verify reopening a late completed task re-applies the ⏰ Overdue tag."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_todo = _create_mock_tag(1, "Not Started")
    tag_done = _create_mock_tag(2, "Completed")
    tag_overdue = _create_mock_tag(3, "⏰ Overdue")
    mock_forum.available_tags = [tag_todo, tag_done, tag_overdue]

    past_due = datetime.now(UTC) - timedelta(hours=3)
    # Task was completed, now reopened to NOT_STARTED
    task = Task(
        id=uuid4(),
        short_id="OPS-5",
        guild_id=12345,
        title="Reopened task",
        creator_discord_id=1001,
        status=TaskStatus.NOT_STARTED,
        due_at=past_due,
    )
    assert task.is_overdue is True

    # Existing thread had Completed tag
    applied = resolve_forum_tags(mock_forum, task, existing_tags=[tag_done])
    assert tag_todo in applied
    assert tag_overdue in applied
    assert tag_done not in applied


def test_resolve_forum_tags_5_tag_cap_preserves_priority_status_squad_over_unassigned():
    """Verify 5-tag cap retains status, priority, overdue, project, and squad tags, dropping unassigned."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_status = _create_mock_tag(1, "In Progress")
    tag_prio = _create_mock_tag(2, "High Priority")
    tag_overdue = _create_mock_tag(3, "⏰ Overdue")
    tag_proj = _create_mock_tag(4, "Core Engine")
    tag_squad = _create_mock_tag(5, "Sec Squad")
    tag_unassigned = _create_mock_tag(6, "👤 Unassigned")

    mock_forum.available_tags = [tag_status, tag_prio, tag_overdue, tag_proj, tag_squad, tag_unassigned]

    past_due = datetime.now(UTC) - timedelta(hours=1)
    task = Task(
        id=uuid4(),
        short_id="COR-10",
        guild_id=12345,
        title="Critical Security Patch",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        assignee_discord_id=None,  # Unassigned
        due_at=past_due,
    )

    applied = resolve_forum_tags(
        mock_forum,
        task,
        project_name="Core Engine",
        existing_tags=[tag_squad],
    )

    assert len(applied) == 5
    assert tag_status in applied
    assert tag_prio in applied
    assert tag_overdue in applied
    assert tag_proj in applied
    assert tag_squad in applied
    assert tag_unassigned not in applied  # Dropped due to 5-tag limit


@pytest.mark.asyncio
async def test_sync_thread_forum_tags_helper():
    """Verify sync_thread_forum_tags helper updates thread tags."""
    from src.adapters.discord_bot.views.forum_helpers import sync_thread_forum_tags

    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_wip = _create_mock_tag(1, "In Progress")
    tag_overdue = _create_mock_tag(2, "⏰ Overdue")
    mock_forum.available_tags = [tag_wip, tag_overdue]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.parent = mock_forum
    mock_thread.applied_tags = [tag_wip]
    mock_thread.edit = AsyncMock()

    past_due = datetime.now(UTC) - timedelta(hours=2)
    task = Task(
        id=uuid4(),
        short_id="COR-11",
        guild_id=12345,
        title="Overdue thread sync",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        due_at=past_due,
    )

    result_tags = await sync_thread_forum_tags(mock_thread, task)
    assert tag_wip in result_tags
    assert tag_overdue in result_tags
    mock_thread.edit.assert_awaited_once_with(applied_tags=result_tags)


@pytest.mark.asyncio
async def test_workspace_sync_lifecycle_transitions(services):
    """Verify DiscordTaskWorkspaceAdapter.sync_workspace manages overdue tags through task lifecycle."""
    from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 11111
    tag_todo = _create_mock_tag(1, "Not Started")
    tag_wip = _create_mock_tag(2, "In Progress")
    tag_done = _create_mock_tag(3, "Completed")
    tag_overdue = _create_mock_tag(4, "⏰ Overdue")
    mock_forum.available_tags = [tag_todo, tag_wip, tag_done, tag_overdue]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888999
    mock_thread.name = "[OPS-10] Server Memory Leak"
    mock_thread.parent = mock_forum
    mock_thread.archived = False
    mock_thread.applied_tags = [tag_wip]
    mock_thread.edit = AsyncMock()

    mock_starter_msg = MagicMock(spec=discord.Message)
    mock_starter_msg.edit = AsyncMock()
    mock_thread.starter_message = mock_starter_msg

    bot.get_channel = MagicMock(return_value=mock_thread)

    past_due = datetime.now(UTC) - timedelta(hours=4)
    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="OPS-10",
        title="Server Memory Leak",
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        creator_discord_id=1001,
        discord_thread_id=888999,
        discord_message_id=777111,
        due_at=past_due,
    )

    # 1. Sync while task is overdue -> adds Overdue tag
    assert task.is_overdue is True
    await adapter.sync_workspace(task)
    mock_thread.edit.assert_awaited_once()
    applied = mock_thread.edit.call_args.kwargs.get("applied_tags")
    assert tag_overdue in applied
    assert tag_wip in applied

    # Update thread's applied_tags to simulate Discord state
    mock_thread.applied_tags = applied
    mock_thread.edit.reset_mock()

    # 2. Complete the task -> removes Overdue tag, applies Completed, archives thread
    task.status = TaskStatus.COMPLETED
    assert task.is_overdue is False
    await adapter.sync_workspace(task)
    mock_thread.edit.assert_awaited_once()
    completed_kwargs = mock_thread.edit.call_args.kwargs
    assert completed_kwargs.get("archived") is True
    assert tag_done in completed_kwargs.get("applied_tags")
    assert tag_overdue not in completed_kwargs.get("applied_tags")

    # Update thread state
    mock_thread.applied_tags = completed_kwargs.get("applied_tags")
    mock_thread.archived = True
    mock_thread.edit.reset_mock()

    # 3. Reopen the late task -> unarchives, restores Overdue tag
    task.status = TaskStatus.IN_PROGRESS
    assert task.is_overdue is True
    await adapter.sync_workspace(task)
    # Thread was unarchived and updated with applied_tags
    tags_call = [c for c in mock_thread.edit.await_args_list if "applied_tags" in c.kwargs][-1]
    assert tag_wip in tags_call.kwargs["applied_tags"]
    assert tag_overdue in tags_call.kwargs["applied_tags"]
    assert tag_done not in tags_call.kwargs["applied_tags"]

    # Update thread state
    mock_thread.applied_tags = tags_call.kwargs["applied_tags"]
    mock_thread.archived = False
    mock_thread.edit.reset_mock()

    # 4. Reschedule deadline to the future -> removes Overdue tag
    task.due_at = datetime.now(UTC) + timedelta(days=3)
    assert task.is_overdue is False
    await adapter.sync_workspace(task)
    mock_thread.edit.assert_awaited_once()
    rescheduled_kwargs = mock_thread.edit.call_args.kwargs
    assert tag_overdue not in rescheduled_kwargs.get("applied_tags")


@pytest.mark.asyncio
async def test_discord_notifier_due_reminder_triggers_tag_sync_with_assignee():
    """Verify DiscordNotifier._handle_due_reminder syncs workspace tags at T=0."""
    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    bot = MagicMock(spec=discord.Client)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(return_value=None)

    mock_workspace = MagicMock(spec=ITaskDiscordWorkspace)
    mock_workspace.sync_workspace = AsyncMock(return_value=True)

    notifier = DiscordNotifier(bot=bot, workspace=mock_workspace)

    past_due = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    event = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="due_test_1",
        payload={
            "task_id": str(uuid4()),
            "short_id": "OPS-20",
            "title": "Database failover drill",
            "guild_id": 12345,
            "assignee_discord_id": 2001,
            "discord_thread_id": 888123,
            "discord_message_id": 777123,
            "due_at": past_due,
            "reminder_type": "due",
        },
    )

    await notifier.dispatch_event(event)

    # Must invoke workspace.sync_workspace
    mock_workspace.sync_workspace.assert_awaited_once()
    synced_task = mock_workspace.sync_workspace.call_args.args[0]
    assert synced_task.is_overdue is True


@pytest.mark.asyncio
async def test_discord_notifier_due_reminder_triggers_tag_sync_unassigned():
    """Verify DiscordNotifier._handle_due_reminder syncs workspace tags at T=0 even when unassigned."""
    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    bot = MagicMock(spec=discord.Client)
    mock_workspace = MagicMock(spec=ITaskDiscordWorkspace)
    mock_workspace.sync_workspace = AsyncMock(return_value=True)

    notifier = DiscordNotifier(bot=bot, workspace=mock_workspace)

    past_due = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    event = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="due_test_2",
        payload={
            "task_id": str(uuid4()),
            "short_id": "OPS-21",
            "title": "Unassigned drill",
            "guild_id": 12345,
            "assignee_discord_id": None,
            "discord_thread_id": 888124,
            "discord_message_id": 777124,
            "due_at": past_due,
            "reminder_type": "due",
        },
    )

    await notifier.dispatch_event(event)

    mock_workspace.sync_workspace.assert_awaited_once()
    synced_task = mock_workspace.sync_workspace.call_args.args[0]
    assert synced_task.is_overdue is True
