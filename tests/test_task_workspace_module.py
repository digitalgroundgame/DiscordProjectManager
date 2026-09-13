from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.workspace_protocol import TaskWorkspaceRef
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Project, Task


def _create_mock_forum_tag(tag_id: int, name: str) -> MagicMock:
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    return tag


@pytest.mark.asyncio
async def test_provision_workspace_forum_channel(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    project = Project(
        id=uuid4(),
        guild_id=guild_id,
        name="Security Core",
        prefix="SEC",
        discord_channel_id=12345,
    )

    task = Task(
        id=uuid4(),
        guild_id=guild_id,
        short_id="SEC-1",
        title="Audit OAuth2 flow",
        project_id=project.id,
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.HIGH,
        creator_discord_id=1001,
        assignee_discord_id=2001,
        watchers=[3001],
    )

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 12345
    mock_forum.guild = MagicMock(id=guild_id)
    tag_todo = _create_mock_forum_tag(10, "Not Started")
    tag_high = _create_mock_forum_tag(20, "High")
    mock_forum.available_tags = [tag_todo, tag_high]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 555666
    mock_thread.jump_url = "https://discord.com/channels/998877/555666"

    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 777888
    mock_msg.jump_url = "https://discord.com/channels/998877/555666/777888"

    res_mock = MagicMock()
    res_mock.thread = mock_thread
    res_mock.message = mock_msg
    mock_forum.create_thread = AsyncMock(return_value=res_mock)
    bot.get_channel = MagicMock(return_value=mock_forum)

    ref = await adapter.provision_workspace(task, project=project)

    assert isinstance(ref, TaskWorkspaceRef)
    assert ref.thread_id == 555666
    assert ref.message_id == 777888
    assert ref.channel_id == 12345
    assert ref.jump_url == mock_msg.jump_url

    mock_forum.create_thread.assert_awaited_once()
    kwargs = mock_forum.create_thread.call_args.kwargs
    assert kwargs["name"] == "[SEC-1] Audit OAuth2 flow"
    assert kwargs["auto_archive_duration"] == 10080
    assert kwargs["applied_tags"] == [tag_todo, tag_high]
    assert kwargs["embed"] is not None
    assert kwargs["view"] is not None


@pytest.mark.asyncio
async def test_provision_workspace_text_channel(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    project = Project(
        id=uuid4(),
        guild_id=guild_id,
        name="Infra Project",
        prefix="INF",
        discord_channel_id=222333,
    )

    task = Task(
        id=uuid4(),
        guild_id=guild_id,
        short_id="INF-1",
        title="Deploy PostgreSQL Cluster",
        project_id=project.id,
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=1001,
    )

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 222333
    mock_channel.guild = MagicMock(id=guild_id)

    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 333444
    mock_msg.jump_url = "https://discord.com/channels/998877/222333/333444"

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 444555
    mock_thread.send = AsyncMock()

    mock_msg.create_thread = AsyncMock(return_value=mock_thread)
    mock_channel.send = AsyncMock(return_value=mock_msg)
    bot.get_channel = MagicMock(return_value=mock_channel)

    ref = await adapter.provision_workspace(task, project=project)

    assert isinstance(ref, TaskWorkspaceRef)
    assert ref.thread_id == 444555
    assert ref.message_id == 333444
    assert ref.channel_id == 222333

    mock_channel.send.assert_awaited_once()
    mock_msg.create_thread.assert_awaited_once_with(
        name="[INF-1] Deploy PostgreSQL Cluster", auto_archive_duration=10080
    )
    mock_thread.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_workspace_invalid_channel_error(services):
    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="ERR-1",
        title="Invalid channel test",
        creator_discord_id=1001,
    )

    with pytest.raises(ValueError, match="Could not resolve a valid Discord"):
        await adapter.provision_workspace(task)


@pytest.mark.asyncio
async def test_sync_workspace_forum_and_archive(services):
    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 11111
    tag_done = _create_mock_forum_tag(30, "Completed")
    mock_forum.available_tags = [tag_done]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888999
    mock_thread.name = "[OLD] Old Title"
    mock_thread.parent = mock_forum
    mock_thread.archived = False
    mock_thread.applied_tags = []
    mock_thread.edit = AsyncMock()

    mock_starter_msg = MagicMock(spec=discord.Message)
    mock_starter_msg.edit = AsyncMock()
    mock_thread.starter_message = mock_starter_msg

    bot.get_channel = MagicMock(return_value=mock_thread)

    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="AUD-10",
        title="Completed Audit",
        status=TaskStatus.COMPLETED,
        priority=PriorityLevel.LOW,
        creator_discord_id=1001,
        discord_thread_id=888999,
        discord_message_id=777111,
    )

    ok = await adapter.sync_workspace(task, sync_title=True, sync_tags=True, sync_archive=True, sync_starter_card=True)
    assert ok.success is True

    mock_starter_msg.edit.assert_awaited_once()
    mock_thread.edit.assert_awaited_once()
    edit_kwargs = mock_thread.edit.call_args.kwargs
    assert edit_kwargs.get("name") == "[AUD-10] Completed Audit"
    assert edit_kwargs.get("archived") is True
    assert edit_kwargs.get("applied_tags") == [tag_done]


@pytest.mark.asyncio
async def test_sync_workspace_throttles_thread_rename_and_updates_card(services):
    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.available_tags = []

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888123
    mock_thread.name = "[AUD-11] Old Name"
    mock_thread.parent = mock_forum
    mock_thread.archived = False
    mock_thread.applied_tags = []
    mock_thread.edit = AsyncMock()

    mock_starter_msg = MagicMock(spec=discord.Message)
    mock_starter_msg.edit = AsyncMock()
    mock_thread.starter_message = mock_starter_msg

    bot.get_channel = MagicMock(return_value=mock_thread)

    # Force rate limiter for this thread to be on cooldown
    adapter.rename_limiter.record_rate_limit(mock_thread.id, retry_after=300.0)

    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="AUD-11",
        title="New Name Updated",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=1001,
        discord_thread_id=888123,
        discord_message_id=777222,
    )

    res = await adapter.sync_workspace(task, sync_title=True, sync_starter_card=True)
    # Result should be truthy, with title_deferred=True
    assert bool(res) is True
    assert getattr(res, "title_deferred", False) is True
    assert getattr(res, "cooldown_remaining_seconds", 0.0) > 0.0

    # Starter embed card MUST have been updated immediately
    mock_starter_msg.edit.assert_awaited_once()

    # Thread.edit was NOT called with 'name' synchronously
    if mock_thread.edit.await_count > 0:
        for call in mock_thread.edit.await_args_list:
            assert "name" not in call.kwargs


@pytest.mark.asyncio
async def test_post_activity_in_thread(services):
    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 444111
    mock_thread.archived = True
    mock_thread.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    mock_thread.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=mock_thread)

    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="NOTE-1",
        title="Note testing",
        status=TaskStatus.COMPLETED,
        creator_discord_id=1001,
        discord_thread_id=444111,
    )

    msg = await adapter.post_activity(task, content="Audit note text", rearchive_if_completed=True)
    assert msg is not None
    mock_thread.send.assert_awaited_once_with(content="Audit note text", embed=None)


@pytest.mark.asyncio
async def test_render_task_controls_panels(services):
    proj_srv = services["project"]
    task_srv = services["task"]

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    task = Task(
        id=uuid4(),
        guild_id=999,
        short_id="CTRL-1",
        title="Control panel test",
        creator_discord_id=1001,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 999
    interaction.user = MagicMock(id=1001)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.delete_original_response = AsyncMock()

    # Quick Controls (first open)
    await adapter.render_task_controls(interaction, task, panel="quick_controls")
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    # Quick Controls (second open by same user - dismisses previous)
    interaction_second = MagicMock(spec=discord.Interaction)
    interaction_second.guild_id = 999
    interaction_second.user = MagicMock(id=1001)
    interaction_second.response = MagicMock()
    interaction_second.response.send_message = AsyncMock()
    await adapter.render_task_controls(interaction_second, task, panel="quick_controls")
    interaction.delete_original_response.assert_awaited_once()

    # Dependencies
    interaction.response.send_message.reset_mock()
    await adapter.render_task_controls(
        interaction, task, panel="dependencies", prerequisites=[], dependents=[], sibling_tasks=[]
    )
    interaction.response.send_message.assert_awaited_once()

    # History
    interaction.response.send_message.reset_mock()
    await adapter.render_task_controls(interaction, task, panel="history", history=[])
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_action_status_and_permissions(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    project = await proj_srv.create_project(guild_id=guild_id, name="Actions Proj", prefix="ACT")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Action Test Task",
        project_id=project.id,
        creator_discord_id=1001,
        assignee_discord_id=2001,
    )

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    # 1. Unauthorized user attempt
    unauth_interaction = MagicMock(spec=discord.Interaction)
    unauth_interaction.guild_id = guild_id
    unauth_interaction.user = MagicMock(id=9999)  # random user
    unauth_interaction.user.guild_permissions = discord.Permissions(0)
    unauth_interaction.response = MagicMock()
    unauth_interaction.response.is_done.return_value = False
    unauth_interaction.response.send_message = AsyncMock()

    await adapter.handle_action(unauth_interaction, "start", task.id)
    unauth_interaction.response.send_message.assert_awaited_once()
    assert "You do not have permission" in unauth_interaction.response.send_message.call_args[0][0]

    # Task should remain NOT_STARTED
    cur_task = await task_srv.get_by_id(task.id)
    assert cur_task.status == TaskStatus.NOT_STARTED

    # 2. Authorized user changes status to start (IN_PROGRESS)
    auth_interaction = MagicMock(spec=discord.Interaction)
    auth_interaction.guild_id = guild_id
    auth_interaction.user = MagicMock(id=1001)  # creator
    auth_interaction.response = MagicMock()
    auth_interaction.response.is_done.return_value = False
    auth_interaction.response.edit_message = AsyncMock()

    await adapter.handle_action(auth_interaction, "start", task.id)
    cur_task = await task_srv.get_by_id(task.id)
    assert cur_task.status == TaskStatus.IN_PROGRESS

    # 3. Authorized user completes task
    await adapter.handle_action(auth_interaction, "complete", task.id)
    cur_task = await task_srv.get_by_id(task.id)
    assert cur_task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_handle_action_blocked_state_start_and_complete_guards(services):
    """handle_action with 'start' or 'complete' on a task with incomplete blockers presents confirmation view."""
    from src.adapters.discord_bot.views.task_blocked_view import TaskBlockedConfirmView

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    project = await proj_srv.create_project(guild_id=guild_id, name="Guard Proj", prefix="GUA")
    blocker = await task_srv.create_task(
        guild_id=guild_id,
        title="Blocker Task",
        project_id=project.id,
        creator_discord_id=1001,
    )
    blocked_task = await task_srv.create_task(
        guild_id=guild_id,
        title="Blocked Task",
        project_id=project.id,
        creator_discord_id=1001,
        assignee_discord_id=2001,
        prerequisite_short_ids=[blocker.short_id],
    )

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    # 1. Attempting 'start' on blocked task
    interaction_start = MagicMock(spec=discord.Interaction)
    interaction_start.guild_id = guild_id
    interaction_start.user = MagicMock(id=2001)  # Assignee
    interaction_start.response = MagicMock()
    interaction_start.response.is_done.return_value = False
    interaction_start.response.send_message = AsyncMock()

    await adapter.handle_action(interaction_start, "start", blocked_task.id)

    # Should send ephemeral warning view with blocker info
    interaction_start.response.send_message.assert_awaited_once()
    kwargs = interaction_start.response.send_message.call_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("view"), TaskBlockedConfirmView)
    embed = kwargs.get("embed")
    assert embed is not None
    assert "Unresolved Dependencies" in embed.title
    assert blocker.short_id in embed.fields[0].value

    # Task status must NOT have changed in DB
    task_in_db = await task_srv.get_by_id(blocked_task.id)
    assert task_in_db.status == TaskStatus.NOT_STARTED

    # 2. Attempting 'complete' on blocked task
    interaction_complete = MagicMock(spec=discord.Interaction)
    interaction_complete.guild_id = guild_id
    interaction_complete.user = MagicMock(id=2001)
    interaction_complete.response = MagicMock()
    interaction_complete.response.is_done.return_value = False
    interaction_complete.response.send_message = AsyncMock()

    await adapter.handle_action(interaction_complete, "complete", blocked_task.id)

    interaction_complete.response.send_message.assert_awaited_once()
    kwargs_c = interaction_complete.response.send_message.call_args.kwargs
    assert isinstance(kwargs_c.get("view"), TaskBlockedConfirmView)
    assert kwargs_c["view"].target_status == TaskStatus.COMPLETED

    task_in_db = await task_srv.get_by_id(blocked_task.id)
    assert task_in_db.status == TaskStatus.NOT_STARTED


@pytest.mark.asyncio
async def test_handle_action_unassign_and_priority(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    project = await proj_srv.create_project(guild_id=guild_id, name="Unassign Proj", prefix="UNA")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Unassign Test",
        project_id=project.id,
        creator_discord_id=1001,
        assignee_discord_id=2001,
        priority=PriorityLevel.NORMAL,
    )

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.user = MagicMock(id=1001)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    # Priority update
    interaction.data = {"values": ["high"]}
    await adapter.handle_action(interaction, "priority", task.id)
    cur_task = await task_srv.get_by_id(task.id)
    assert cur_task.priority == PriorityLevel.HIGH

    # Unassign
    await adapter.handle_action(interaction, "unassign", task.id)
    cur_task = await task_srv.get_by_id(task.id)
    assert cur_task.assignee_discord_id is None


@pytest.mark.asyncio
async def test_save_task_controls_batched(services):
    from datetime import UTC, datetime

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    project = await proj_srv.create_project(guild_id=guild_id, name="Save Controls Proj", prefix="SAV")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Save Controls Test",
        project_id=project.id,
        creator_discord_id=1001,
        priority=PriorityLevel.LOW,
    )

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.user = MagicMock(id=1001)
    interaction.guild = MagicMock(id=guild_id)
    interaction.channel = None

    due = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    updated = await adapter.save_task_controls(
        interaction,
        task=task,
        priority=PriorityLevel.HIGH,
        assignee_id=3003,
        due_at=due,
        watchers=[4004, 5005],
    )

    assert updated is not None
    assert updated.priority == PriorityLevel.HIGH
    assert updated.assignee_discord_id == 3003
    assert updated.due_at is not None
    assert updated.due_at.replace(tzinfo=UTC) == due
    assert updated.watchers == [4004, 5005]
