from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.domain.enums import TaskStatus


@pytest.mark.asyncio
async def test_task_create_sets_7day_auto_archive(services):
    """Verify task-create creates a thread with auto_archive_duration=10080 (7 days)."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 1234567890

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 999111
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 888222
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 777333
    mock_thread.send = AsyncMock()
    mock_msg.create_thread = AsyncMock(return_value=mock_thread)
    mock_channel.send = AsyncMock(return_value=mock_msg)

    await proj_srv.create_project(
        guild_id=guild_id, name="Infra Core", prefix="INF", discord_channel_id=mock_channel.id
    )

    bot = MagicMock()
    cog = PmCog(bot=bot, task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.get_channel = MagicMock(return_value=mock_channel)
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = mock_channel
    interaction.channel_id = mock_channel.id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.task_create.callback(
        cog,
        interaction=interaction,
        project_name="Infra Core",
        title="Deploy Kubernetes cluster",
        assignee=None,
        due=None,
        priority="high",
    )

    mock_msg.create_thread.assert_awaited_once()
    kwargs = mock_msg.create_thread.call_args.kwargs
    assert kwargs.get("auto_archive_duration") == 10080
    assert "[INF-1] Deploy Kubernetes cluster" in kwargs.get("name")


@pytest.mark.asyncio
async def test_task_standalone_creates_thread(services):
    """Verify task creation creates a starter message and discussion thread in TextChannel."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 1234567890

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 999111
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 888222
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 777333
    mock_thread.send = AsyncMock()
    mock_msg.create_thread = AsyncMock(return_value=mock_thread)
    mock_channel.send = AsyncMock(return_value=mock_msg)

    await proj_srv.create_project(
        guild_id=guild_id, name="Hotfix Project", prefix="HOT", discord_channel_id=mock_channel.id
    )

    bot = MagicMock()
    cog = PmCog(bot=bot, task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.get_channel = MagicMock(return_value=mock_channel)
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = mock_channel
    interaction.channel_id = mock_channel.id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.task_create.callback(
        cog,
        interaction=interaction,
        project_name="Hotfix Project",
        title="Quick hotfix on production",
        assignee=None,
        due=None,
        priority="normal",
    )

    mock_msg.create_thread.assert_awaited_once()
    kwargs = mock_msg.create_thread.call_args.kwargs
    assert kwargs.get("auto_archive_duration") == 10080
    assert "Quick hotfix on production" in kwargs.get("name")

    # DB should have thread ID persisted
    tasks, total = await task_srv.list_tasks(guild_id=guild_id)
    assert total >= 1
    hotfix_task = next(t for t in tasks if t.title == "Quick hotfix on production")
    assert hotfix_task.discord_thread_id == 777333


@pytest.mark.asyncio
async def test_bot_sync_task_thread_archive_and_unarchive(services):
    """Verify DggPmBot.sync_task_thread archives when completed/archived and unarchives when reopened."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 987654321

    project = await proj_srv.create_project(guild_id=guild_id, name="DB Migration Project", prefix="DBM")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Database migration",
        creator_discord_id=1001,
        project_id=project.id,
    )
    await task_srv.update_discord_message_ids(task.id, 55555, 66666)
    task = await task_srv.get_by_id(task.id)

    bot = DggPmBot(task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 66666
    mock_thread.name = f"[{task.short_id}] Database migration"
    mock_thread.archived = False
    mock_thread.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)

    # 1. When status is COMPLETED, should archive thread
    task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=task.version,
        actor_discord_id=1001,
    )
    await bot.sync_task_thread(task)
    mock_thread.edit.assert_awaited_once_with(archived=True)

    # 2. When status is reopened (IN_PROGRESS), should unarchive thread
    mock_thread.archived = True
    mock_thread.edit.reset_mock()
    task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.IN_PROGRESS,
        expected_version=task.version,
        actor_discord_id=1001,
    )
    await bot.sync_task_thread(task)
    mock_thread.edit.assert_awaited_once_with(archived=False)


@pytest.mark.asyncio
async def test_bot_sync_task_thread_title_sync(services):
    """Verify DggPmBot.sync_task_thread updates thread title when sync_title=True."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 987654321

    project = await proj_srv.create_project(guild_id=guild_id, name="Title Sync Project", prefix="TSP")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Initial Title",
        creator_discord_id=1001,
        project_id=project.id,
    )
    await task_srv.update_discord_message_ids(task.id, 55555, 66666)

    updated_task = await task_srv.update_details(
        task_id=task.id,
        actor_discord_id=1001,
        title="Updated Awesome Title",
    )

    bot = DggPmBot(task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 66666
    mock_thread.name = f"[{task.short_id}] Initial Title"
    mock_thread.archived = False
    mock_thread.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)

    await bot.sync_task_thread(updated_task, sync_title=True)
    mock_thread.edit.assert_awaited_once_with(name=f"[{task.short_id}] Updated Awesome Title")


@pytest.mark.asyncio
async def test_project_archive_cascade_threads(services):
    """Verify archiving a project batch-archives its task threads, and unarchiving restores them."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 3333333333

    project = await proj_srv.create_project(guild_id=guild_id, name="Cascade Project", prefix="CAS")
    t1 = await task_srv.create_task(guild_id=guild_id, title="Task 1", creator_discord_id=1001, project_id=project.id)
    t2 = await task_srv.create_task(guild_id=guild_id, title="Task 2", creator_discord_id=1001, project_id=project.id)
    await task_srv.update_discord_message_ids(t1.id, 111, 222)
    await task_srv.update_discord_message_ids(t2.id, 333, 444)

    bot = MagicMock()
    bot.sync_task_thread = AsyncMock()

    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv, task_service=task_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Archive project
    await cog.project_archive.callback(cog, interaction=interaction, project_name="Cascade Project")
    assert bot.sync_task_thread.await_count == 2
    # Verify sync_task_thread called with action="archive" for each task
    called_task_ids = [call.args[0].id for call in bot.sync_task_thread.call_args_list]
    assert t1.id in called_task_ids
    assert t2.id in called_task_ids

    # 2. Unarchive project
    bot.sync_task_thread.reset_mock()
    await cog.project_unarchive.callback(cog, interaction=interaction, project_name="Cascade Project")
    assert bot.sync_task_thread.await_count == 2
    unarchive_called_ids = [call.args[0].id for call in bot.sync_task_thread.call_args_list]
    assert t1.id in unarchive_called_ids
    assert t2.id in unarchive_called_ids


@pytest.mark.asyncio
async def test_discord_notifier_rearchives_thread_on_completion():
    """Verify DiscordNotifier re-archives the thread after posting a completion message."""
    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 999888
    mock_thread.parent = None
    mock_thread.send = AsyncMock()
    mock_thread.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)

    notifier = DiscordNotifier(bot=bot)

    event = OutboxEvent(
        event_type=EventType.TASK_STATUS_CHANGED,
        idempotency_key="status_test_1",
        payload={
            "short_id": "T-1",
            "title": "Fix bug",
            "old_status": "inProgress",
            "new_status": "completed",
            "actor_discord_id": 1001,
            "discord_thread_id": 999888,
        },
    )

    await notifier.dispatch_event(event)

    # Verify message sent to thread
    mock_thread.send.assert_awaited_once()
    # Verify thread was re-archived after sending the message
    mock_thread.edit.assert_awaited_once_with(archived=True)


@pytest.mark.asyncio
async def test_discord_notifier_rearchives_thread_on_task_updated_and_notes():
    """Verify DiscordNotifier re-archives the thread on TASK_UPDATED and TASK_NOTE_ADDED if task is completed."""
    from src.adapters.discord_bot.discord_notifier import DiscordNotifier
    from src.domain.enums import EventType
    from src.domain.models import OutboxEvent

    bot = MagicMock()
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 999888
    mock_thread.parent = None
    mock_thread.archived = False  # E.g. unarchived by message send
    mock_thread.send = AsyncMock()
    mock_thread.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)

    notifier = DiscordNotifier(bot=bot)

    # 1. TASK_UPDATED on completed task
    event_update = OutboxEvent(
        event_type=EventType.TASK_UPDATED,
        idempotency_key="update_test_1",
        payload={
            "short_id": "T-1",
            "title": "Fix bug",
            "actor_discord_id": 1001,
            "discord_thread_id": 999888,
            "status": "completed",
            "is_completed": True,
            "update_type": "assignee",
            "new_assignee_id": None,
        },
    )
    await notifier.dispatch_event(event_update)
    mock_thread.send.assert_awaited_once()
    mock_thread.edit.assert_awaited_with(archived=True)

    # 2. TASK_NOTE_ADDED on completed task
    mock_thread.send.reset_mock()
    mock_thread.edit.reset_mock()
    event_note = OutboxEvent(
        event_type=EventType.TASK_NOTE_ADDED,
        idempotency_key="note_test_1",
        payload={
            "short_id": "T-1",
            "title": "Fix bug",
            "actor_discord_id": 1001,
            "discord_thread_id": 999888,
            "note": "Post release note",
            "status": "completed",
            "is_completed": True,
        },
    )
    await notifier.dispatch_event(event_note)
    mock_thread.send.assert_awaited_once()
    mock_thread.edit.assert_awaited_with(archived=True)


@pytest.mark.asyncio
async def test_task_quick_controls_completed_task_unarchives_and_rearchives(services):
    """Verify modifying a completed/archived task via Quick Controls unarchives then re-archives thread."""
    from src.adapters.discord_bot.views.task_buttons import TaskQuickControlsView

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 99887766

    project = await proj_srv.create_project(guild_id=guild_id, name="Archive Test", prefix="ARC")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Completed Task",
        creator_discord_id=1001,
        project_id=project.id,
        assignee_discord_id=1002,
    )
    # Move to completed
    task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=task.version,
        actor_discord_id=1001,
    )

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 555666
    mock_thread.archived = True
    mock_thread.edit = AsyncMock()

    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=mock_thread)
    bot.sync_root_task_message = AsyncMock()
    bot.sync_task_thread = AsyncMock()

    controls_view = TaskQuickControlsView(
        task=task,
        task_service=task_srv,
        bot=bot,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = mock_thread
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    # Click Unassign, then click Save Changes
    await controls_view._on_unassign_clicked(interaction)
    await controls_view._on_save_clicked(interaction)

    # Verify unarchive (archived=False) and re-archive (archived=True) were both called
    edit_calls = mock_thread.edit.call_args_list
    assert any(c.kwargs.get("archived") is False for c in edit_calls)
    assert any(c.kwargs.get("archived") is True for c in edit_calls)

    # Verify task was actually unassigned in DB
    refreshed = await task_srv.get_by_id(task.id)
    assert refreshed.assignee_discord_id is None
    assert refreshed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_task_note_modal_completed_archived_thread(services):
    """Verify adding a note to a completed/archived task unarchives then re-archives thread."""
    from src.adapters.discord_bot.views.task_modals import TaskNoteModal

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 11224455

    project = await proj_srv.create_project(guild_id=guild_id, name="Notes Test", prefix="NOT")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Completed Task with Note",
        creator_discord_id=1001,
        project_id=project.id,
    )
    task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=task.version,
        actor_discord_id=1001,
    )

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 777888
    mock_thread.archived = True
    mock_thread.edit = AsyncMock()

    modal = TaskNoteModal(
        task_id=task.id,
        short_id=task.short_id,
        task_service=task_srv,
    )
    modal.note_input._value = "Final post-mortem update."

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = mock_thread
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)

    edit_calls = mock_thread.edit.call_args_list
    assert any(c.kwargs.get("archived") is False for c in edit_calls)
    assert any(c.kwargs.get("archived") is True for c in edit_calls)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_edit_modal_completed_archived_thread(services):
    """Verify editing details of a completed/archived task unarchives then re-archives thread."""
    from src.adapters.discord_bot.views.task_modals import TaskEditModal

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 99112233

    project = await proj_srv.create_project(guild_id=guild_id, name="Edit Test", prefix="EDT")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Completed Task Edit",
        creator_discord_id=1001,
        project_id=project.id,
    )
    task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=task.version,
        actor_discord_id=1001,
    )

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888999
    mock_thread.archived = True
    mock_thread.edit = AsyncMock()

    modal = TaskEditModal(
        task=task,
        task_service=task_srv,
    )
    modal.title_input._value = "Updated Completed Task Title"
    modal.body_input._value = "Updated description"
    modal.due_input._value = "clear"
    modal.cc_input._value = ""

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = mock_thread
    interaction.message = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.client = MagicMock()
    interaction.client.sync_root_task_message = AsyncMock()
    interaction.client.sync_task_thread = AsyncMock()

    await modal.on_submit(interaction)

    edit_calls = mock_thread.edit.call_args_list
    assert any(c.kwargs.get("archived") is False for c in edit_calls)
    assert any(c.kwargs.get("archived") is True for c in edit_calls)
    interaction.response.edit_message.assert_awaited_once()


def test_get_task_jump_url():
    """Verify get_task_jump_url generates accurate universal Discord links."""
    from src.adapters.discord_bot.views.task_embed import get_task_jump_url
    from src.domain.models import Task

    t1 = Task(
        guild_id=12345,
        title="Test Task",
        creator_discord_id=100,
        short_id="TST-1",
        discord_thread_id=67890,
        discord_message_id=112233,
    )
    assert get_task_jump_url(t1) == "https://discord.com/channels/12345/67890/112233"

    t2 = Task(
        guild_id=12345,
        title="Thread only",
        creator_discord_id=100,
        short_id="TST-2",
        discord_thread_id=67890,
        discord_message_id=None,
    )
    assert get_task_jump_url(t2) == "https://discord.com/channels/12345/67890"

    t3 = Task(
        guild_id=0,
        title="No guild",
        creator_discord_id=100,
        short_id="TST-3",
    )
    assert get_task_jump_url(t3) is None


def test_task_list_embed_formats_hyperlinks():
    """Verify build_page_embed in task_list_view includes clickable markdown hyperlinks."""
    from src.adapters.discord_bot.views.task_list_view import build_page_embed
    from src.domain.models import Task

    task = Task(
        guild_id=999,
        title="Build Frontend",
        creator_discord_id=101,
        short_id="UI-1",
        discord_thread_id=888,
        discord_message_id=777,
    )
    embed = build_page_embed([task], page=0, total_count=1)
    assert "https://discord.com/channels/999/888/777" in embed.description
    assert "**[[UI-1] Build Frontend](https://discord.com/channels/999/888/777)**" in embed.description


def test_task_board_embed_formats_jump_links():
    """Verify build_task_board_embed in task_menu includes clickable jump links."""
    from src.adapters.discord_bot.views.task_menu import build_task_board_embed
    from src.domain.models import Task

    task = Task(
        guild_id=999,
        title="API Integration",
        creator_discord_id=101,
        short_id="API-1",
        discord_thread_id=888,
        discord_message_id=777,
    )
    embed = build_task_board_embed(
        tasks=[task],
        total_count=1,
        project_label="Global",
        status_label="Active",
        assignee_label="All",
    )
    field = embed.fields[0]
    assert "https://discord.com/channels/999/888/777" in field.value
    assert "[**Open Task Workspace**]" in field.value
