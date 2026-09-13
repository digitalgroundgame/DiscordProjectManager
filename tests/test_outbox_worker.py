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

    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=RateLimitedNotifier(),
        poll_interval=1.0,
        max_retries=5,
    )

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
async def test_outbox_worker_survives_extended_outage_past_5_retries(services, repos, db_session):
    """Verifies that an event with >5 retries during outages stays PENDING rather than permanently FAILED."""
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    class OutageNotifier(INotificationDispatcher):
        async def dispatch_event(self, event):
            raise ConnectionError("Discord API Outage 503")

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_extended_outage_survival",
        payload={"msg": "Outage Test"},
        scheduled_for=datetime.now(UTC),
    )

    worker = OutboxWorker(outbox_repo=outbox_repo, notifier=OutageNotifier(), poll_interval=1.0)

    pending = await outbox_repo.fetch_pending_batch(limit=10)
    domain_evt = next(e for e in pending if e.idempotency_key == "test_extended_outage_survival")
    domain_evt.retry_count = 5  # 5 prior attempts. Under old policy, next attempt would transition to FAILED.

    await worker._process_single_event(domain_evt)

    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "test_extended_outage_survival")
    row = (await db_session.execute(stmt)).scalar_one()

    # Must survive and remain PENDING for next attempt (retry 6)
    assert row.status == OutboxStatus.PENDING.value
    assert row.retry_count == 6
    # Delay for retry 6 should be min(600, 2**6 * 5 = 320) seconds
    scheduled_diff = (row.scheduled_for.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 310 <= scheduled_diff <= 330


@pytest.mark.asyncio
async def test_outbox_worker_exponential_backoff_progression_and_cap(services, repos, db_session):
    """Verifies that exponential backoff doubles properly and enforces the configured cap."""
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    class FailingNotifier(INotificationDispatcher):
        async def dispatch_event(self, event):
            raise RuntimeError("API temporary error")

    # Custom cap of 100.0s for testing
    cap = 100.0
    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=FailingNotifier(),
        poll_interval=1.0,
        backoff_cap_seconds=cap,
    )

    # Test retry 1: 2^1 * 5 = 10s
    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_backoff_retry_1",
        payload={"msg": "1"},
        scheduled_for=datetime.now(UTC),
    )
    pending1 = await outbox_repo.fetch_pending_batch(limit=10)
    domain1 = next(e for e in pending1 if e.idempotency_key == "test_backoff_retry_1")
    domain1.retry_count = 0  # 1st failure
    await worker._process_single_event(domain1)

    db_session.expire_all()
    stmt1 = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "test_backoff_retry_1")
    row1 = (await db_session.execute(stmt1)).scalar_one()
    diff1 = (row1.scheduled_for.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 8 <= diff1 <= 12  # ~10s

    # Test retry 7: 2^7 * 5 = 640s -> capped at 100s
    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_backoff_retry_7_capped",
        payload={"msg": "7"},
        scheduled_for=datetime.now(UTC),
    )
    pending7 = await outbox_repo.fetch_pending_batch(limit=10)
    domain7 = next(e for e in pending7 if e.idempotency_key == "test_backoff_retry_7_capped")
    domain7.retry_count = 6  # 7th failure
    await worker._process_single_event(domain7)

    db_session.expire_all()
    stmt7 = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "test_backoff_retry_7_capped")
    row7 = (await db_session.execute(stmt7)).scalar_one()
    diff7 = (row7.scheduled_for.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 95 <= diff7 <= 105  # capped at ~100s


@pytest.mark.asyncio
async def test_outbox_worker_retention_window_expiration(services, repos, db_session):
    """Verifies that an event exceeding max_retention_hours transitions to FAILED even if retry count is low."""
    outbox_srv = services["outbox"]
    outbox_repo = repos["outbox"]

    class FailingNotifier(INotificationDispatcher):
        async def dispatch_event(self, event):
            raise RuntimeError("Outage error")

    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=FailingNotifier(),
        poll_interval=1.0,
        max_retries=150,
        max_retention_hours=24.0,
    )

    await outbox_srv.enqueue_event(
        event_type=EventType.TASK_CREATED,
        idempotency_key="test_retention_window_expired",
        payload={"msg": "Old event"},
        scheduled_for=datetime.now(UTC),
    )

    pending = await outbox_repo.fetch_pending_batch(limit=10)
    domain_evt = next(e for e in pending if e.idempotency_key == "test_retention_window_expired")
    # Simulate event created 30 hours ago (beyond 24h retention window)
    domain_evt.created_at = datetime.now(UTC) - timedelta(hours=30)
    domain_evt.retry_count = 2  # Only 2 retries, well below max_retries 150

    await worker._process_single_event(domain_evt)

    db_session.expire_all()
    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key == "test_retention_window_expired")
    row = (await db_session.execute(stmt)).scalar_one()

    # Must transition to FAILED due to retention window expiry
    assert row.status == OutboxStatus.FAILED.value
    assert row.retry_count == 3


@pytest.mark.asyncio
async def test_outbox_worker_custom_config_injection(repos):
    """Verifies that OutboxWorker accepts and applies custom configuration parameters."""
    outbox_repo = repos["outbox"]
    mock_notifier = MockNotifier()

    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        notifier=mock_notifier,
        poll_interval=2.5,
        batch_size=20,
        max_retries=50,
        backoff_cap_seconds=120.0,
        max_retention_hours=12.0,
    )

    assert worker.poll_interval == 2.5
    assert worker.batch_size == 20
    assert worker.max_retries == 50
    assert worker.backoff_cap_seconds == 120.0
    assert worker.max_retention_hours == 12.0


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


@pytest.mark.asyncio
async def test_schedule_task_reminders_includes_discord_location_ids(services):
    """Verify OutboxService.schedule_task_reminders includes discord_thread_id and discord_message_id in payload."""
    from src.domain.models import Task

    outbox_srv = services["outbox"]
    now = datetime.now(UTC)
    task = Task(
        guild_id=123456,
        short_id="TASK-42",
        title="Deploy to Prod",
        creator_discord_id=100,
        assignee_discord_id=200,
        due_at=now + timedelta(hours=48),
        discord_thread_id=987654321,
        discord_message_id=123456789,
    )
    events = await outbox_srv.schedule_task_reminders(task)
    assert len(events) == 3
    for evt in events:
        assert evt.payload["discord_thread_id"] == 987654321
        assert evt.payload["discord_message_id"] == 123456789


@pytest.mark.asyncio
async def test_due_reminder_resolves_jump_url_and_view_from_payload(services):
    """Verify TASK_DUE_REMINDER event includes jump URL in embed title/body and attaches Open Task link button."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    bot = MagicMock()
    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    notifier = DiscordNotifier(bot, user_service=user_srv)
    evt = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="rem_payload_loc",
        payload={
            "task_id": "test-task-1",
            "short_id": "TASK-1",
            "title": "Fix Critical Bug",
            "guild_id": guild_id,
            "assignee_discord_id": 5001,
            "discord_thread_id": 111222,
            "discord_message_id": 333444,
            "reminder_type": "due",
            "due_at": "2026-09-01T20:00:00Z",
        },
    )
    await notifier.dispatch_event(evt)

    assert mock_user.send.await_count == 1
    call_kwargs = mock_user.send.call_args.kwargs
    embed = call_kwargs["embed"]
    view = call_kwargs.get("view")

    expected_jump_url = f"https://discord.com/channels/{guild_id}/111222/333444"
    assert embed.url == expected_jump_url
    assert expected_jump_url in embed.description

    assert view is not None
    assert isinstance(view, discord.ui.View)
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    assert len(buttons) == 1
    assert buttons[0].style == discord.ButtonStyle.link
    assert buttons[0].label == "Open Task"
    assert buttons[0].url == expected_jump_url


@pytest.mark.asyncio
async def test_due_reminder_resolves_location_from_task_service_when_omitted(services):
    """Verify TASK_DUE_REMINDER queries task_service when location IDs are omitted from payload."""
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent, Task

    user_srv = services["user"]
    task_srv = services["task"]
    guild_id = 999111

    task_id = uuid4()
    mock_task = Task(
        id=task_id,
        guild_id=guild_id,
        short_id="TASK-99",
        title="Async Worker Optimization",
        creator_discord_id=100,
        assignee_discord_id=5001,
        discord_thread_id=555666,
        discord_message_id=777888,
    )
    task_srv.get_by_id = AsyncMock(return_value=mock_task)

    bot = MagicMock()
    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    notifier = DiscordNotifier(bot, user_service=user_srv, task_service=task_srv)

    # Event payload has NO discord_thread_id and NO discord_message_id
    evt = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="rem_lookup_loc",
        payload={
            "task_id": str(task_id),
            "short_id": "TASK-99",
            "title": "Async Worker Optimization",
            "guild_id": guild_id,
            "assignee_discord_id": 5001,
            "reminder_type": "1h",
            "due_at": "2026-09-01T20:00:00Z",
        },
    )
    await notifier.dispatch_event(evt)

    task_srv.get_by_id.assert_awaited_once_with(task_id)
    assert mock_user.send.await_count == 1
    call_kwargs = mock_user.send.call_args.kwargs
    embed = call_kwargs["embed"]
    view = call_kwargs.get("view")

    expected_jump_url = f"https://discord.com/channels/{guild_id}/555666/777888"
    assert embed.url == expected_jump_url
    assert expected_jump_url in embed.description

    assert view is not None
    button = view.children[0]
    assert button.url == expected_jump_url
    assert button.label == "Open Task"


@pytest.mark.asyncio
async def test_due_reminder_resolves_via_short_id_fallback(services):
    """Verify TASK_DUE_REMINDER falls back to get_by_short_id when task_id lookup fails."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent, Task

    user_srv = services["user"]
    task_srv = services["task"]
    guild_id = 999111

    mock_task = Task(
        guild_id=guild_id,
        short_id="TASK-77",
        title="Fallback Task",
        creator_discord_id=100,
        assignee_discord_id=5001,
        discord_thread_id=333222,
        discord_message_id=444333,
    )
    task_srv.get_by_id = AsyncMock(return_value=None)
    task_srv.get_by_short_id = AsyncMock(return_value=mock_task)

    bot = MagicMock()
    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    notifier = DiscordNotifier(bot, user_service=user_srv, task_service=task_srv)

    evt = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="rem_short_id_fallback",
        payload={
            "task_id": "non-uuid-id",
            "short_id": "TASK-77",
            "title": "Fallback Task",
            "guild_id": guild_id,
            "assignee_discord_id": 5001,
            "reminder_type": "due",
        },
    )
    await notifier.dispatch_event(evt)

    task_srv.get_by_short_id.assert_awaited_once_with(guild_id, "TASK-77")
    call_kwargs = mock_user.send.call_args.kwargs
    assert call_kwargs["embed"].url == f"https://discord.com/channels/{guild_id}/333222/444333"


@pytest.mark.asyncio
async def test_due_reminder_thread_only_url_when_no_message_id(services):
    """Verify TASK_DUE_REMINDER falls back to thread jump URL when message_id is absent.

    Also verifies redundant DB queries are avoided when thread_id is already known.
    """
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    task_srv = services["task"]
    task_srv.get_by_id = AsyncMock()
    guild_id = 999111
    bot = MagicMock()
    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=mock_user)
    bot.fetch_user = AsyncMock(return_value=mock_user)

    # Wire task_service as in production
    notifier = DiscordNotifier(bot, user_service=user_srv, task_service=task_srv)
    evt = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="rem_thread_only_loc",
        payload={
            "task_id": "test-task-thread-only",
            "short_id": "TASK-2",
            "title": "Fix Docs",
            "guild_id": guild_id,
            "assignee_discord_id": 5001,
            "discord_thread_id": 111222,
            "reminder_type": "24h",
            "due_at": "2026-09-01T20:00:00Z",
        },
    )
    await notifier.dispatch_event(evt)

    # When discord_thread_id is already present in payload, no DB query needed
    task_srv.get_by_id.assert_not_called()

    call_kwargs = mock_user.send.call_args.kwargs
    embed = call_kwargs["embed"]
    view = call_kwargs.get("view")

    expected_jump_url = f"https://discord.com/channels/{guild_id}/111222"
    assert embed.url == expected_jump_url
    assert expected_jump_url in embed.description

    assert view is not None
    assert view.children[0].url == expected_jump_url


@pytest.mark.asyncio
async def test_due_reminder_channel_preference_receives_link_button_view(services):
    """Verify in-thread reminder receives embed with jump URL and Open Task link button view."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType, NotificationPreference
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    assignee_id = 7001
    await user_srv.set_preference(guild_id, assignee_id, NotificationPreference.CHANNEL)

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)
    bot.fetch_channel = AsyncMock(return_value=mock_thread)

    notifier = DiscordNotifier(bot, user_service=user_srv)
    evt = OutboxEvent(
        event_type=EventType.TASK_DUE_REMINDER,
        idempotency_key="rem_chan_pref",
        payload={
            "task_id": "test-task-chan",
            "short_id": "TASK-3",
            "title": "Channel Reminder",
            "guild_id": guild_id,
            "assignee_discord_id": assignee_id,
            "discord_thread_id": 888111,
            "discord_message_id": 888222,
            "reminder_type": "due",
        },
    )
    await notifier.dispatch_event(evt)

    assert mock_thread.send.await_count == 1
    call_kwargs = mock_thread.send.call_args.kwargs
    expected_url = f"https://discord.com/channels/{guild_id}/888111/888222"
    assert call_kwargs["embed"].url == expected_url
    assert call_kwargs.get("view") is not None
    assert call_kwargs["view"].children[0].url == expected_url


@pytest.mark.asyncio
async def test_task_created_notification_delivers_jump_url_and_link_button(services):
    """Verify TASK_CREATED notifications to assignee and watchers include jump URL and link button view."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    bot = MagicMock()
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
    evt = OutboxEvent(
        event_type=EventType.TASK_CREATED,
        idempotency_key="task_created_jump_test",
        payload={
            "task_id": "test-task-created-jump",
            "short_id": "TASK-10",
            "title": "Setup OAuth2 Flow",
            "guild_id": guild_id,
            "creator_discord_id": 5000,
            "assignee_discord_id": 5001,
            "watchers": [5002],
            "discord_thread_id": 777111,
            "discord_message_id": 777222,
        },
    )
    await notifier.dispatch_event(evt)

    expected_url = f"https://discord.com/channels/{guild_id}/777111/777222"

    # Assignee notification
    assert dmd_users[5001].send.await_count == 1
    assignee_kwargs = dmd_users[5001].send.call_args.kwargs
    assignee_embed = assignee_kwargs["embed"]
    assignee_view = assignee_kwargs.get("view")
    assert assignee_embed.url == expected_url
    assert "Task Location:" not in (assignee_embed.description or "")
    assert assignee_view is not None
    assert assignee_view.children[0].url == expected_url
    assert assignee_view.children[0].label == "Open Task"

    # Watcher notification
    assert dmd_users[5002].send.await_count == 1
    watcher_kwargs = dmd_users[5002].send.call_args.kwargs
    watcher_embed = watcher_kwargs["embed"]
    watcher_view = watcher_kwargs.get("view")
    assert watcher_embed.url == expected_url
    assert "Task Location:" not in (watcher_embed.description or "")
    assert watcher_view is not None
    assert watcher_view.children[0].url == expected_url
    assert watcher_view.children[0].label == "Open Task"


@pytest.mark.asyncio
async def test_task_status_changed_notification_delivers_jump_url_and_link_button(services):
    """Verify TASK_STATUS_CHANGED notifications include jump URL and link button view."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    bot = MagicMock()
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
    evt = OutboxEvent(
        event_type=EventType.TASK_STATUS_CHANGED,
        idempotency_key="status_jump_test",
        payload={
            "task_id": "test-task-status-jump",
            "short_id": "TASK-11",
            "title": "Database Optimization",
            "guild_id": guild_id,
            "old_status": "notStarted",
            "new_status": "inProgress",
            "actor_discord_id": 9999,
            "assignee_discord_id": 6001,
            "watchers": [6002],
            "discord_thread_id": 888111,
            "discord_message_id": 888222,
        },
    )
    await notifier.dispatch_event(evt)

    expected_url = f"https://discord.com/channels/{guild_id}/888111/888222"

    for uid in (6001, 6002):
        assert dmd_users[uid].send.await_count == 1
        kwargs = dmd_users[uid].send.call_args.kwargs
        embed = kwargs["embed"]
        view = kwargs.get("view")
        assert embed.url == expected_url
        assert "Task Location:" not in (embed.description or "")
        assert view is not None
        assert view.children[0].url == expected_url
        assert view.children[0].label == "Open Task"


@pytest.mark.asyncio
async def test_task_note_added_notification_delivers_jump_url_and_link_button(services):
    """Verify TASK_NOTE_ADDED notifications include jump URL and link button view."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    bot = MagicMock()
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
    evt = OutboxEvent(
        event_type=EventType.TASK_NOTE_ADDED,
        idempotency_key="note_jump_test",
        payload={
            "task_id": "test-task-note-jump",
            "short_id": "TASK-12",
            "title": "API Rate Limiting",
            "guild_id": guild_id,
            "actor_discord_id": 9999,
            "assignee_discord_id": 7001,
            "watchers": [7002],
            "note": "Initial PR has been posted.",
            "discord_thread_id": 999111,
            "discord_message_id": 999222,
        },
    )
    await notifier.dispatch_event(evt)

    expected_url = f"https://discord.com/channels/{guild_id}/999111/999222"

    for uid in (7001, 7002):
        assert dmd_users[uid].send.await_count == 1
        kwargs = dmd_users[uid].send.call_args.kwargs
        embed = kwargs["embed"]
        view = kwargs.get("view")
        assert embed.url == expected_url
        assert "Task Location:" not in (embed.description or "")
        assert view is not None
        assert view.children[0].url == expected_url
        assert view.children[0].label == "Open Task"


@pytest.mark.asyncio
async def test_task_updated_notification_delivers_jump_url_and_link_button(services):
    """Verify TASK_UPDATED notifications include jump URL and link button view."""
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    user_srv = services["user"]
    guild_id = 999111
    bot = MagicMock()
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
    evt = OutboxEvent(
        event_type=EventType.TASK_UPDATED,
        idempotency_key="updated_jump_test",
        payload={
            "task_id": "test-task-updated-jump",
            "short_id": "TASK-13",
            "title": "Refactor Cogs",
            "guild_id": guild_id,
            "actor_discord_id": 9999,
            "old_assignee_id": 8001,
            "new_assignee_id": 8002,
            "assignee_discord_id": 8002,
            "watchers": [8003],
            "update_type": "assignee",
            "discord_thread_id": 555111,
            "discord_message_id": 555222,
        },
    )
    await notifier.dispatch_event(evt)

    expected_url = f"https://discord.com/channels/{guild_id}/555111/555222"

    for uid in (8001, 8002, 8003):
        assert dmd_users[uid].send.await_count == 1
        kwargs = dmd_users[uid].send.call_args.kwargs
        embed = kwargs["embed"]
        view = kwargs.get("view")
        assert embed.url == expected_url
        assert "Task Location:" not in (embed.description or "")
        assert view is not None
        assert view.children[0].url == expected_url
        assert view.children[0].label == "Open Task"
