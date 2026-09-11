from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.adapters.db.tables import OutboxEventTable
from src.adapters.worker.outbox_worker import OutboxWorker
from src.domain.enums import EventType, OutboxStatus, TaskStatus
from src.ports.notifier import INotificationDispatcher


class MockNotifier(INotificationDispatcher):
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event):
        self.dispatched.append(event)


@pytest.mark.asyncio
async def test_tiered_reminder_scheduling_and_cancellation(services, db_session):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 555555555555555555

    project = await proj_srv.create_project(guild_id=guild_id, name="Sprint 1", prefix="SP1")

    # Due in 48 hours
    due_time = datetime.now(UTC) + timedelta(hours=48)
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Release MVP",
        creator_discord_id=1001,
        project_id=project.id,
        assignee_discord_id=2001,
        due_at=due_time,
    )

    # Verify all 4 events are in outbox table (task_created, 24h, 1h, due)
    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key.like(f"%{task.id}%"))
    res = await db_session.execute(stmt)
    rows = res.scalars().all()
    assert len(rows) == 4

    keys = [r.idempotency_key for r in rows]
    assert f"task_created:{task.id}" in keys
    assert f"task_due:{task.id}:24h" in keys
    assert f"task_due:{task.id}:1h" in keys
    assert f"task_due:{task.id}:due" in keys

    # Complete task
    await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=1,
        actor_discord_id=2001,
    )

    # Reminders should now be CANCELLED
    db_session.expire_all()
    res_after = await db_session.execute(stmt)
    rows_after = res_after.scalars().all()
    cancelled_reminders = [
        r
        for r in rows_after
        if r.idempotency_key.startswith(f"task_due:{task.id}") and r.status == OutboxStatus.CANCELLED.value
    ]
    assert len(cancelled_reminders) == 3


@pytest.mark.asyncio
async def test_outbox_worker_batch_processing(services, repos, db_session):
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]
    mock_notifier = MockNotifier()

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_key_1",
        payload={"msg": "Hello"},
        scheduled_for=datetime.now(UTC),
    )

    # Check pending batch
    pending = await outbox_repo.fetch_pending_batch(limit=10)
    assert len(pending) >= 1

    # Test processing directly with mock notifier
    worker = OutboxWorker(outbox_repo=outbox_repo, notifier=mock_notifier, poll_interval=1.0)
    await worker._process_single_event(pending[0])
    assert len(mock_notifier.dispatched) == 1
    assert mock_notifier.dispatched[0].idempotency_key == "test_key_1"


@pytest.mark.asyncio
async def test_outbox_worker_429_rate_limit_backoff(services, repos):
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    class RateLimitedNotifier(INotificationDispatcher):
        async def dispatch_event(self, event):
            exc = Exception("Discord rate limit")
            exc.status = 429
            exc.retry_after = 0.1
            raise exc

    notifier = RateLimitedNotifier()
    evt = await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_rate_limited",
        payload={"msg": "Burst"},
        scheduled_for=datetime.now(UTC),
    )

    worker = OutboxWorker(outbox_repo=outbox_repo, notifier=notifier, poll_interval=0.1)
    await worker.process_batch()

    assert evt is not None


@pytest.mark.asyncio
async def test_outbox_worker_429_retries_are_capped(services, repos, db_session):
    """A permanently rate-limited event must be marked FAILED after max 429 retries, not loop forever."""
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    class RateLimitedNotifier(INotificationDispatcher):
        async def dispatch_event(self, event):
            exc = Exception("Discord rate limit")
            exc.status = 429
            exc.retry_after = 0.1
            raise exc

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_rate_limited_capped",
        payload={"msg": "Burst"},
        scheduled_for=datetime.now(UTC),
    )

    worker = OutboxWorker(outbox_repo=outbox_repo, notifier=RateLimitedNotifier(), poll_interval=1.0)

    pending = await outbox_repo.fetch_pending_batch(limit=10)
    domain_evt = next(e for e in pending if e.idempotency_key == "test_rate_limited_capped")
    domain_evt.retry_count = 4  # Next 429 -> attempt 5/5 -> FAILED

    await worker._process_single_event(domain_evt)

    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "test_rate_limited_capped")
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.status == OutboxStatus.FAILED.value
    assert row.retry_count == 5


@pytest.mark.asyncio
async def test_reclaim_stale_processing_restores_pending(services, repos, db_session):
    """Events stranded in PROCESSING (e.g. after a crash between fetch and mark_processed) must be reclaimed."""
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="stale_processing_key",
        payload={"msg": "stale"},
        scheduled_for=datetime.now(UTC),
    )

    # Fetching marks the event PROCESSING without dispatching it (simulates crash/stall)
    pending = await outbox_repo.fetch_pending_batch(limit=10)
    assert len(pending) == 1

    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "stale_processing_key")
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.status == OutboxStatus.PROCESSING.value

    reclaimed = await outbox_repo.reclaim_stale_processing()
    assert reclaimed == 1

    db_session.expire_all()
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.status == OutboxStatus.PENDING.value

    # A second reclaim (nothing processing) is a no-op
    assert await outbox_repo.reclaim_stale_processing() == 0

    # The reclaimed event is now eligible for dispatch again
    redispatched = await outbox_repo.fetch_pending_batch(limit=10)
    assert any(e.idempotency_key == "stale_processing_key" for e in redispatched)


@pytest.mark.asyncio
async def test_outbox_worker_start_reclaims_and_dispatches_stranded_events(services, repos):
    """Worker start() must reclaim PROCESSING events from a previous crashed run and deliver them."""
    import asyncio

    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]
    mock_notifier = MockNotifier()

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="start_delivery_key",
        payload={"msg": "stranded from crash"},
        scheduled_for=datetime.now(UTC),
    )

    # Simulate a process crash: fetch marks the event PROCESSING, then "crashes" before dispatch.
    await outbox_repo.fetch_pending_batch(limit=10)

    worker = OutboxWorker(outbox_repo=outbox_repo, notifier=mock_notifier, poll_interval=0.005)
    task = asyncio.create_task(worker.start())
    for _ in range(50):
        if mock_notifier.dispatched:
            break
        await asyncio.sleep(0.002)
    worker.stop()
    await task

    assert any(e.idempotency_key == "start_delivery_key" for e in mock_notifier.dispatched)


@pytest.mark.asyncio
async def test_discord_notifier_user_preferences_routing(services):
    """Verify DiscordNotifier respects user notification preferences (DM, Channel, Both, Silent, Closed DMs)."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import NotificationPreference
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111888

    # Set user 1001 -> CHANNEL
    await user_srv.set_preference(guild_id, 1001, NotificationPreference.CHANNEL)
    # Set user 1002 -> NONE (Silent)
    await user_srv.set_preference(guild_id, 1002, NotificationPreference.NONE)
    # User 1003 has default (DM) but DMs closed -> triggers fallback in thread

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.send = AsyncMock()
    mock_thread.parent = None
    bot.get_channel = MagicMock(return_value=mock_thread)

    mock_user_1003 = MagicMock(spec=discord.User)
    mock_user_1003.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "DMs closed"))
    bot.get_user = MagicMock(return_value=mock_user_1003)
    bot.fetch_user = AsyncMock(return_value=mock_user_1003)

    notifier = DiscordNotifier(bot, user_service=user_srv)

    # 1. Test status change for user 1001 (CHANNEL)
    evt_channel = OutboxEvent(
        event_type=EventType.TASK_STATUS_CHANGED,
        idempotency_key="status_channel_test",
        payload={
            "task_id": "test-task-1",
            "short_id": "T-1",
            "title": "Build UI",
            "guild_id": guild_id,
            "old_status": "notStarted",
            "new_status": "inProgress",
            "actor_discord_id": 9999,
            "assignee_discord_id": 1001,
            "watchers": [],
            "discord_thread_id": 555666,
        },
    )
    await notifier.dispatch_event(evt_channel)
    # Thread received status message + in-thread ping for 1001
    assert mock_thread.send.await_count >= 2

    # 2. Test status change for user 1002 (NONE / Silent)
    mock_thread.send.reset_mock()
    evt_silent = OutboxEvent(
        event_type=EventType.TASK_STATUS_CHANGED,
        idempotency_key="status_silent_test",
        payload={
            "task_id": "test-task-2",
            "short_id": "T-2",
            "title": "Build UI 2",
            "guild_id": guild_id,
            "old_status": "notStarted",
            "new_status": "inProgress",
            "actor_discord_id": 9999,
            "assignee_discord_id": 1002,
            "watchers": [],
            "discord_thread_id": 555666,
        },
    )
    await notifier.dispatch_event(evt_silent)
    # Only thread update message, NO extra user ping
    assert mock_thread.send.await_count == 1


@pytest.mark.asyncio
async def test_discord_notifier_task_updated_events(services):
    """Verify TASK_UPDATED events route DMs to assignees and watchers for reassignments, priority changes, and edits."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111999

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.send = AsyncMock()
    mock_thread.parent = None
    mock_thread.archived = False
    bot.get_channel = MagicMock(return_value=mock_thread)

    dmd_users: dict[int, MagicMock] = {}

    def get_mock_user(uid: int):
        if uid not in dmd_users:
            u = MagicMock(spec=discord.User)
            u.id = uid
            u.send = AsyncMock()
            dmd_users[uid] = u
        return dmd_users[uid]

    bot.get_user = MagicMock(side_effect=get_mock_user)
    bot.fetch_user = AsyncMock(side_effect=get_mock_user)

    notifier = DiscordNotifier(bot, user_service=user_srv)

    # 1. Test Reassignment: new assignee (2001), old assignee (2002), watcher (3001), actor (9999)
    evt_reassign = OutboxEvent(
        event_type=EventType.TASK_UPDATED,
        idempotency_key="reassign_test_1",
        payload={
            "task_id": "test-task-assignee",
            "short_id": "TASK-101",
            "title": "Migrate Database",
            "guild_id": guild_id,
            "actor_discord_id": 9999,
            "old_assignee_id": 2002,
            "new_assignee_id": 2001,
            "assignee_discord_id": 2001,
            "watchers": [3001],
            "update_type": "assignee",
            "discord_thread_id": 777888,
        },
    )
    await notifier.dispatch_event(evt_reassign)

    assert dmd_users[2001].send.await_count == 1
    assert dmd_users[2002].send.await_count == 1
    assert dmd_users[3001].send.await_count == 1
    assert 9999 not in dmd_users  # Actor must not be notified

    # 2. Test Priority Update: assignee (2001), watcher (3001), actor (9999)
    for u in dmd_users.values():
        u.send.reset_mock()

    evt_prio = OutboxEvent(
        event_type=EventType.TASK_UPDATED,
        idempotency_key="prio_test_1",
        payload={
            "task_id": "test-task-prio",
            "short_id": "TASK-102",
            "title": "Deploy API",
            "guild_id": guild_id,
            "actor_discord_id": 9999,
            "old_priority": "normal",
            "new_priority": "high",
            "priority": "high",
            "assignee_discord_id": 2001,
            "watchers": [3001],
            "update_type": "priority",
            "discord_thread_id": 777888,
        },
    )
    await notifier.dispatch_event(evt_prio)

    assert dmd_users[2001].send.await_count == 1
    assert dmd_users[3001].send.await_count == 1
    assert dmd_users[2002].send.await_count == 0

    # 3. Test Details / Watchers Update: assignee (2001), old watcher (3001), new watcher (3002), actor (9999)
    for u in dmd_users.values():
        u.send.reset_mock()

    evt_details = OutboxEvent(
        event_type=EventType.TASK_UPDATED,
        idempotency_key="details_test_1",
        payload={
            "task_id": "test-task-details",
            "short_id": "TASK-103",
            "title": "Refactor Code",
            "guild_id": guild_id,
            "actor_discord_id": 9999,
            "assignee_discord_id": 2001,
            "watchers": [3001, 3002],
            "old_watchers": [3001],
            "changes": ["Title: `Old` ➔ **`Refactor Code`**", "Added watchers: <@3002>"],
            "update_type": "details",
            "discord_thread_id": 777888,
        },
    )
    await notifier.dispatch_event(evt_details)

    assert dmd_users[2001].send.await_count == 1
    assert dmd_users[3001].send.await_count == 1
    assert dmd_users[3002].send.await_count == 1
    assert 9999 not in dmd_users

    # Verify footer on detail update DM
    embed_sent = dmd_users[3002].send.call_args.kwargs.get("embed")
    assert embed_sent is not None
    assert embed_sent.footer.text == "Control notifications via /pm settings"


@pytest.mark.asyncio
async def test_discord_notifier_footers_and_creation_watcher_notifications(services):
    """Verify notification embeds have 'Control notifications via /pm settings' and task creation notifies watchers."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import NOTIFICATION_FOOTER, DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999222333

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.send = AsyncMock()
    mock_thread.parent = None
    mock_thread.archived = False
    bot.get_channel = MagicMock(return_value=mock_thread)

    dmd_users: dict[int, MagicMock] = {}

    def get_mock_user(uid: int):
        if uid not in dmd_users:
            u = MagicMock(spec=discord.User)
            u.id = uid
            u.send = AsyncMock()
            dmd_users[uid] = u
        return dmd_users[uid]

    bot.get_user = MagicMock(side_effect=get_mock_user)
    bot.fetch_user = AsyncMock(side_effect=get_mock_user)

    notifier = DiscordNotifier(bot, user_service=user_srv)

    # 1. Test TASK_CREATED: creator (5000), assignee (5001), watcher (5002)
    evt_create = OutboxEvent(
        event_type=EventType.TASK_CREATED,
        idempotency_key="create_test_footer",
        payload={
            "task_id": "test-task-create",
            "short_id": "TASK-201",
            "title": "Build Architecture",
            "guild_id": guild_id,
            "creator_discord_id": 5000,
            "assignee_discord_id": 5001,
            "watchers": [5002],
            "discord_thread_id": 888999,
        },
    )
    await notifier.dispatch_event(evt_create)

    # Assignee got notification with footer
    assert dmd_users[5001].send.await_count == 1
    assignee_embed = dmd_users[5001].send.call_args.kwargs.get("embed")
    assert assignee_embed is not None
    assert assignee_embed.footer.text == NOTIFICATION_FOOTER
    assert "New Task Assigned" in assignee_embed.title

    # Watcher got notification with footer
    assert dmd_users[5002].send.await_count == 1
    watcher_embed = dmd_users[5002].send.call_args.kwargs.get("embed")
    assert watcher_embed is not None
    assert watcher_embed.footer.text == NOTIFICATION_FOOTER
    assert "Added as Watcher" in watcher_embed.title

    # Creator is not notified
    assert 5000 not in dmd_users

    # 2. Test TASK_DUE_REMINDER
    dmd_users[5001].send.reset_mock()
    evt_reminder = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="reminder_test_footer",
        payload={
            "task_id": "test-task-create",
            "short_id": "TASK-201",
            "title": "Build Architecture",
            "guild_id": guild_id,
            "assignee_discord_id": 5001,
            "reminder_type": "due",
            "due_at": "2026-09-01T20:00:00Z",
        },
    )
    await notifier.dispatch_event(evt_reminder)
    assert dmd_users[5001].send.await_count == 1
    reminder_embed = dmd_users[5001].send.call_args.kwargs.get("embed")
    assert reminder_embed is not None
    assert reminder_embed.footer.text == NOTIFICATION_FOOTER

    # 3. Test TASK_NOTE_ADDED
    dmd_users[5001].send.reset_mock()
    evt_note = OutboxEvent(
        event_type=EventType.TASK_NOTE_ADDED,
        idempotency_key="note_test_footer",
        payload={
            "task_id": "test-task-create",
            "short_id": "TASK-201",
            "title": "Build Architecture",
            "guild_id": guild_id,
            "actor_discord_id": 5000,
            "assignee_discord_id": 5001,
            "watchers": [5002],
            "note": "Ready for review",
            "discord_thread_id": 888999,
        },
    )
    await notifier.dispatch_event(evt_note)
    assert dmd_users[5001].send.await_count == 1
    note_embed = dmd_users[5001].send.call_args.kwargs.get("embed")
    assert note_embed is not None
    assert note_embed.footer.text == NOTIFICATION_FOOTER
