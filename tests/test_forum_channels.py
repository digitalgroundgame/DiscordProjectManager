from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.discord_notifier import DiscordNotifier
from src.adapters.discord_bot.views.forum_helpers import (
    ensure_pinned_hub_post,
    ensure_project_tag,
    resolve_forum_tags,
    setup_forum_tags,
)
from src.adapters.discord_bot.views.task_menu import TaskCreateModal
from src.domain.enums import EventType, PriorityLevel, TaskStatus
from src.domain.models import OutboxEvent, Task


def _create_mock_tag(tag_id: int, name: str, emoji: str | None = None) -> MagicMock:
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def test_resolve_forum_tags_matching():
    """Verify resolve_forum_tags correctly matches status, priority, unassigned, and project tags."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_todo = _create_mock_tag(1, "To Do")
    tag_wip = _create_mock_tag(2, "In Progress")
    tag_done = _create_mock_tag(3, "Completed")
    tag_high = _create_mock_tag(4, "🔴 High Priority")
    tag_norm = _create_mock_tag(5, "🟡 Normal")
    tag_unassigned = _create_mock_tag(6, "👤 Unassigned")
    tag_custom = _create_mock_tag(7, "Backend")
    tag_project = _create_mock_tag(8, "📁 Auth")

    mock_forum.available_tags = [
        tag_todo,
        tag_wip,
        tag_done,
        tag_high,
        tag_norm,
        tag_unassigned,
        tag_custom,
        tag_project,
    ]

    # 1. Unassigned task (assignee_discord_id is None)
    task_unassigned = Task(
        id=uuid4(),
        short_id="AUTH-1",
        guild_id=12345,
        title="Implement Auth",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        assignee_discord_id=None,
    )

    tags = resolve_forum_tags(mock_forum, task_unassigned, project_name="Auth", existing_tags=[tag_custom, tag_todo])
    assert tag_wip in tags
    assert tag_high in tags
    assert tag_unassigned in tags
    assert tag_custom in tags
    assert tag_project in tags
    assert tag_todo not in tags  # Old status tag replaced

    # 2. Assigned task (assignee_discord_id is set) -> Unassigned tag is removed
    task_assigned = Task(
        id=uuid4(),
        short_id="AUTH-1",
        guild_id=12345,
        title="Implement Auth",
        creator_discord_id=1001,
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        assignee_discord_id=9999,
    )
    tags_assigned = resolve_forum_tags(
        mock_forum, task_assigned, project_name="Auth", existing_tags=[tag_unassigned, tag_custom]
    )
    assert tag_unassigned not in tags_assigned
    assert tag_wip in tags_assigned
    assert tag_high in tags_assigned
    assert tag_custom in tags_assigned
    assert tag_project in tags_assigned


@pytest.mark.asyncio
async def test_project_create_with_forum_channel(services):
    """Verify /project-create accepts a ForumChannel and formats the embed correctly."""
    proj_srv = services["project"]
    team_srv = services["team"]
    bot = MagicMock()
    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = 7777777777
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 555444333
    mock_forum.guild = interaction.guild
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 777111
    mock_role.name = "Sec Squad"

    await cog.project_create.callback(
        cog,
        interaction=interaction,
        name="Security Audit",
        prefix="SEC",
        role=mock_role,
        channel=mock_forum,
        description="Security pentest deliverables",
        category="Security",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()

    send_call_args = interaction.followup.send.call_args
    embed = send_call_args.kwargs.get("embed")
    assert embed is not None
    assert "<#555444333>" in next(f.value for f in embed.fields if f.name == "Bound Channel")

    project = await proj_srv.get_by_name(7777777777, "Security Audit")
    assert project is not None
    assert project.discord_channel_id == 555444333

    project_tag_saved = [
        c.kwargs.get("available_tags", []) for c in mock_forum.edit.await_args_list if "available_tags" in c.kwargs
    ]
    assert any("Security Audit" == t.name for tags in project_tag_saved for t in tags)


@pytest.mark.asyncio
async def test_task_create_in_forum_channel(services):
    """Verify /task-create creates a Forum Post (thread + starter message) with tags in a ForumChannel."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 1122334455

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Core Engine",
        prefix="COR",
        discord_channel_id=987654321,
    )

    bot = MagicMock()
    cog = PmCog(bot=bot, task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 987654321
    tag_todo = _create_mock_tag(10, "Not Started")
    tag_urgent = _create_mock_tag(20, "High")
    mock_forum.available_tags = [tag_todo, tag_urgent]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888999
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 888999

    res_mock = MagicMock()
    res_mock.thread = mock_thread
    res_mock.message = mock_msg
    mock_forum.create_thread = AsyncMock(return_value=res_mock)
    bot.get_channel = MagicMock(return_value=mock_forum)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel_id = 111111
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.task_create.callback(
        cog,
        interaction=interaction,
        project_name="Core Engine",
        title="Refactor Query Pipeline",
        assignee=None,
        due=None,
        priority="high",
    )

    mock_forum.create_thread.assert_awaited_once()
    kwargs = mock_forum.create_thread.call_args.kwargs
    assert kwargs.get("auto_archive_duration") == 10080
    assert "[COR-1] Refactor Query Pipeline" in kwargs.get("name")
    assert tag_urgent in kwargs.get("applied_tags")

    # Verify task persisted with message and thread IDs
    tasks, _ = await task_srv.list_tasks(guild_id=guild_id, project_id=project.id)
    assert len(tasks) == 1
    assert tasks[0].discord_thread_id == 888999
    assert tasks[0].discord_message_id == 888999


@pytest.mark.asyncio
async def test_task_create_modal_in_forum_channel(services):
    """Verify TaskCreateModal properly creates a thread in a ForumChannel."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 9988776655

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Design System",
        prefix="DS",
        discord_channel_id=444555666,
    )

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 444555666
    mock_forum.available_tags = []

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 777666
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 777666
    res_mock = MagicMock()
    res_mock.thread = mock_thread
    res_mock.message = mock_msg
    mock_forum.create_thread = AsyncMock(return_value=res_mock)

    modal = TaskCreateModal(task_service=task_srv, project=project, target_channel=mock_forum)
    modal.title_input._value = "Add Dark Mode Colors"
    modal.desc_input._value = "Tokens for dark mode"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    draft_view = interaction.response.send_message.call_args.kwargs.get("view")
    assert draft_view is not None

    confirm_interaction = MagicMock(spec=discord.Interaction)
    confirm_interaction.guild = interaction.guild
    confirm_interaction.user = interaction.user
    confirm_interaction.response = MagicMock()
    confirm_interaction.response.edit_message = AsyncMock()
    await draft_view._on_confirm_clicked(confirm_interaction)

    mock_forum.create_thread.assert_awaited_once()
    kwargs = mock_forum.create_thread.call_args.kwargs
    assert "[DS-1] Add Dark Mode Colors" in kwargs.get("name")


@pytest.mark.asyncio
async def test_bot_sync_forum_post_starter_message_and_tags(services):
    """Verify DggPmBot.sync_root_task_message and sync_task_thread work inside ForumChannels."""
    proj_srv = services["project"]
    task_srv = services["task"]
    team_srv = services["team"]
    guild_id = 123123123

    project = await proj_srv.create_project(guild_id=guild_id, name="Cache Project", prefix="CP")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Migrate Cache Cluster",
        creator_discord_id=1001,
        priority=PriorityLevel.HIGH,
        project_id=project.id,
    )
    await task_srv.update_discord_message_ids(task.id, 888111, 888111)
    task = await task_srv.get_by_id(task.id)

    bot = DggPmBot(task_service=task_srv, project_service=proj_srv, team_service=team_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_high = _create_mock_tag(1, "High")
    tag_done = _create_mock_tag(2, "Completed")
    mock_forum.available_tags = [tag_high, tag_done]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 888111
    mock_thread.parent = mock_forum
    mock_thread.applied_tags = [tag_high]
    mock_thread.archived = False
    mock_thread.edit = AsyncMock()

    mock_starter_msg = MagicMock(spec=discord.Message)
    mock_starter_msg.edit = AsyncMock()
    mock_thread.starter_message = mock_starter_msg
    mock_thread.fetch_message = AsyncMock(return_value=mock_starter_msg)

    bot.get_channel = MagicMock(return_value=mock_thread)

    # 1. Test sync_root_task_message edits starter message in forum post
    await bot.sync_root_task_message(task)
    mock_starter_msg.edit.assert_awaited_once()

    # 2. Test sync_task_thread updates applied tags and archives upon completion
    completed_task = await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.COMPLETED,
        expected_version=task.version,
        actor_discord_id=1001,
    )
    await bot.sync_task_thread(completed_task)
    mock_thread.edit.assert_awaited_once()
    edit_kwargs = mock_thread.edit.call_args.kwargs
    assert edit_kwargs.get("archived") is True
    assert tag_done in edit_kwargs.get("applied_tags")


@pytest.mark.asyncio
async def test_discord_notifier_handles_forum_post_status_changed():
    """Verify DiscordNotifier updates starter message and applies forum tags on status changed."""
    bot = MagicMock()
    mock_forum = MagicMock(spec=discord.ForumChannel)
    tag_done = _create_mock_tag(99, "Completed")
    mock_forum.available_tags = [tag_done]

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 555666
    mock_thread.parent = mock_forum
    mock_thread.send = AsyncMock()
    mock_thread.edit = AsyncMock()
    mock_thread.archived = False

    mock_embed = MagicMock(spec=discord.Embed)
    mock_field = MagicMock()
    mock_field.name = "Status"
    mock_field.inline = True
    mock_embed.fields = [mock_field]
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.embeds = [mock_embed]
    mock_msg.edit = AsyncMock()

    mock_thread.starter_message = mock_msg
    mock_thread.fetch_message = AsyncMock(return_value=mock_msg)
    bot.get_channel = MagicMock(return_value=mock_thread)

    notifier = DiscordNotifier(bot=bot)
    event = OutboxEvent(
        event_type=EventType.TASK_STATUS_CHANGED,
        idempotency_key="status_forum_test_1",
        payload={
            "short_id": "T-10",
            "title": "Fix memory leak",
            "old_status": "inProgress",
            "new_status": "completed",
            "actor_discord_id": 1001,
            "discord_thread_id": 555666,
            "discord_message_id": 555666,
        },
    )

    await notifier.dispatch_event(event)

    # Starter message in forum post should have been edited
    mock_msg.edit.assert_awaited_once()
    # Thread should have applied tags and been archived
    mock_thread.edit.assert_awaited_once()
    kwargs = mock_thread.edit.call_args.kwargs
    assert kwargs.get("archived") is True
    assert tag_done in kwargs.get("applied_tags")


@pytest.mark.asyncio
async def test_setup_forum_tags_merging():
    """Verify setup_forum_tags appends missing standard PM tags and preserves custom tags."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    custom_tag = _create_mock_tag(1, "Customer Request")
    existing_wip = _create_mock_tag(2, "In Progress")
    mock_forum.available_tags = [custom_tag, existing_wip]
    mock_forum.edit = AsyncMock()

    added, total, err = await setup_forum_tags(mock_forum)
    assert err is None
    assert added > 0
    assert total > 2
    mock_forum.edit.assert_awaited_once()
    saved_tags = mock_forum.edit.call_args.kwargs.get("available_tags")
    assert custom_tag in saved_tags
    assert existing_wip in saved_tags
    tag_names = [t.name for t in saved_tags]
    assert "High Priority" in tag_names
    assert "Completed" in tag_names


@pytest.mark.asyncio
async def test_setup_forum_tags_forbidden():
    """Verify setup_forum_tags handles missing permissions gracefully."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.available_tags = []
    mock_forum.name = "restricted-forum"
    mock_forum.id = 999111
    mock_forum.edit = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Missing Permissions"))

    added, _total, err = await setup_forum_tags(mock_forum)
    assert added == 0
    assert err is not None
    assert "Manage Channels" in err


@pytest.mark.asyncio
async def test_ensure_project_tag():
    """Verify ensure_project_tag adds a clean per-project tag, is idempotent, and migrates legacy tags."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 1230001
    mock_forum.name = "eng-backlog"
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()

    # First call adds the clean project tag
    err = await ensure_project_tag(mock_forum, "Mobile App")
    assert err is None
    saved = mock_forum.edit.call_args.kwargs.get("available_tags")
    assert any(t.name == "Mobile App" for t in saved)

    # Clean tag present -> idempotent, no further edit
    tag_project = _create_mock_tag(50, "Mobile App")
    mock_forum.available_tags = [tag_project]
    mock_forum.edit.reset_mock()
    err = await ensure_project_tag(mock_forum, "Mobile App")
    assert err is None
    mock_forum.edit.assert_not_awaited()

    # Legacy tag present ('📁 Mobile App') -> migrated to 'Mobile App'
    tag_legacy = _create_mock_tag(50, "📁 Mobile App")
    mock_forum.available_tags = [tag_legacy]
    mock_forum.edit.reset_mock()
    err = await ensure_project_tag(mock_forum, "Mobile App")
    assert err is None
    mock_forum.edit.assert_awaited_once()
    migrated_tags = mock_forum.edit.call_args.kwargs.get("available_tags")
    assert any(t.name == "Mobile App" for t in migrated_tags)


@pytest.mark.asyncio
async def test_ensure_project_tag_forbidden():
    """Verify ensure_project_tag handles missing permissions gracefully."""
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "restricted-forum"
    mock_forum.id = 999111
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Missing Permissions"))

    err = await ensure_project_tag(mock_forum, "Mobile App")
    assert err is not None
    assert "Manage Channels" in err


@pytest.mark.asyncio
async def test_project_setup_forum_command(services):
    """Verify /project-setup-forum executes setup_forum_tags and replies with embed."""
    proj_srv = services["project"]
    team_srv = services["team"]
    bot = MagicMock()
    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 333222111
    mock_forum.name = "eng-backlog"
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.project_setup_forum.callback(cog, interaction=interaction, forum=mock_forum)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Forum Tags Configured" in embed.title


@pytest.mark.asyncio
async def test_ensure_pinned_hub_post_in_forum(services):
    """Verify ensure_pinned_hub_post creates tags, creates thread post, and pins it in forum."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "product-dev"
    mock_forum.id = 7770001
    mock_forum.available_tags = []
    mock_forum.threads = []
    mock_forum.edit = AsyncMock()

    mock_thread = MagicMock()
    mock_thread.id = 9998881
    mock_thread.edit = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    ok, msg = await ensure_pinned_hub_post(
        channel=mock_forum,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        project_name="Core Engine",
    )

    assert ok is True
    assert "Created and pinned" in msg
    mock_forum.create_thread.assert_awaited_once()
    thread_call_kwargs = mock_forum.create_thread.call_args.kwargs
    assert "Control Hub" in thread_call_kwargs["name"]
    mock_thread.edit.assert_awaited_once_with(pinned=True)


@pytest.mark.asyncio
async def test_ensure_pinned_hub_post_in_text_channel(services):
    """Verify ensure_pinned_hub_post posts embed and pins message in text channels."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.name = "general-pm"
    mock_channel.id = 7770002

    mock_msg = MagicMock()
    mock_msg.pin = AsyncMock()
    mock_channel.send = AsyncMock(return_value=mock_msg)

    ok, msg = await ensure_pinned_hub_post(
        channel=mock_channel,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        project_name="Mobile",
    )

    assert ok is True
    assert "Posted and pinned" in msg
    mock_channel.send.assert_awaited_once()
    mock_msg.pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_cog_post_hub_and_subgroups(services):
    """Verify unified /pm slash command group operations including /pm post-hub and /pm project create."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 999888999

    bot = MagicMock()
    pm_cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    # 1. /pm post-hub
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "pm-board"
    mock_forum.id = 8887771
    mock_forum.available_tags = []
    mock_forum.threads = []
    mock_forum.edit = AsyncMock()

    mock_thread = MagicMock()
    mock_thread.id = 8887772
    mock_thread.edit = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    interaction_hub = MagicMock(spec=discord.Interaction)
    interaction_hub.guild = MagicMock()
    interaction_hub.guild.id = guild_id
    interaction_hub.response = MagicMock()
    interaction_hub.response.defer = AsyncMock()
    interaction_hub.followup = MagicMock()
    interaction_hub.followup.send = AsyncMock()

    await pm_cog.post_hub.callback(pm_cog, interaction=interaction_hub, channel=mock_forum)

    interaction_hub.followup.send.assert_awaited_once()
    hub_res = interaction_hub.followup.send.await_args.args[0]
    assert "Created and pinned" in hub_res

    # 2. /pm menu
    interaction_menu = MagicMock(spec=discord.Interaction)
    interaction_menu.guild = MagicMock()
    interaction_menu.guild.id = guild_id
    interaction_menu.response = MagicMock()
    interaction_menu.response.send_message = AsyncMock()

    await pm_cog.menu.callback(pm_cog, interaction=interaction_menu)
    interaction_menu.response.send_message.assert_awaited_once()

    # 3. /pm help
    interaction_help = MagicMock(spec=discord.Interaction)
    interaction_help.response = MagicMock()
    interaction_help.response.send_message = AsyncMock()

    await pm_cog.help_command.callback(pm_cog, interaction=interaction_help)
    interaction_help.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_hub_view_ephemeral_interactions(services):
    """Verify PmHubView button interactions respond ephemerally and open modals without mutating public post."""
    from src.adapters.discord_bot.views.hub_menu import PmHubView

    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 999111222

    # Seed a project
    await proj_srv.create_project(guild_id=guild_id, name="Infra", prefix="INF", discord_channel_id=12345)

    hub_view = PmHubView(
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.channel = MagicMock()
    interaction.channel.id = 12345
    interaction.channel.parent_id = None
    interaction.user = MagicMock()
    interaction.user.id = 777888
    interaction.response = MagicMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()

    # 1. Click New Task -> opens modal
    await hub_view.new_task_btn.callback(interaction)
    interaction.response.send_modal.assert_awaited_once()

    # 2. Click Task Board -> sends ephemeral response
    await hub_view.tasks_tab.callback(interaction)
    assert interaction.response.send_message.await_count == 1
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    # 3. Click Projects Hub -> sends ephemeral response
    await hub_view.projects_tab.callback(interaction)
    assert interaction.response.send_message.await_count == 2
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_task_create_modal_inside_forum_thread_creates_new_forum_post(services):
    """Verify creating a task while inside a pinned forum thread creates a new forum thread, not a reply in the hub."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 9988112233

    # Mock Forum Channel & Pinned Hub Thread
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 11223344
    mock_forum.name = "mobile-dev"
    mock_forum.available_tags = []
    mock_forum.create_thread = AsyncMock()

    mock_hub_thread = MagicMock(spec=discord.Thread)
    mock_hub_thread.id = 55667788
    mock_hub_thread.name = "📌 📊 Mobile App • Control Hub"
    mock_hub_thread.parent = mock_forum
    mock_hub_thread.parent_id = 11223344
    mock_hub_thread.send = AsyncMock()

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Mobile App",
        prefix="MOB",
        discord_channel_id=mock_forum.id,
    )

    modal = TaskCreateModal(
        task_service=task_srv,
        project=project,
        target_channel=mock_hub_thread,  # Launched from within the pinned thread
    )
    modal.title_input._value = "Fix crash on splash screen"
    modal.desc_input._value = "Stacktrace in sentry"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.get_channel = MagicMock(return_value=mock_forum)
    interaction.channel = mock_hub_thread
    interaction.user = MagicMock()
    interaction.user.id = 12345
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    draft_view = interaction.response.send_message.call_args.kwargs.get("view")
    assert draft_view is not None

    confirm_interaction = MagicMock(spec=discord.Interaction)
    confirm_interaction.guild = interaction.guild
    confirm_interaction.user = interaction.user
    confirm_interaction.response = MagicMock()
    confirm_interaction.response.edit_message = AsyncMock()
    await draft_view._on_confirm_clicked(confirm_interaction)

    # Must create thread in ForumChannel, NOT send message inside mock_hub_thread
    mock_forum.create_thread.assert_awaited_once()
    create_args = mock_forum.create_thread.call_args.kwargs
    assert "[MOB-1] Fix crash on splash screen" in create_args["name"]
    mock_hub_thread.send.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_pinned_hub_post_multi_project_update(services):
    """Verify ensure_pinned_hub_post updates existing thread post when second project is bound."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 88776655

    mock_guild = MagicMock()
    mock_guild.id = guild_id

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 44556677
    mock_forum.name = "shared-projects"
    mock_forum.guild = mock_guild
    mock_forum.available_tags = []
    mock_forum.threads = []
    mock_forum.edit = AsyncMock()

    mock_starter_msg = MagicMock()
    mock_starter_msg.edit = AsyncMock()

    mock_thread = MagicMock()
    mock_thread.id = 11229988
    mock_thread.name = "📌 📊 #shared-projects • Control Hub"
    mock_thread.edit = AsyncMock()
    mock_thread.starter_message = mock_starter_msg
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    # 1. Create Project 1 and initialize hub
    p1 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Frontend App",
        prefix="FRONT",
        discord_channel_id=mock_forum.id,
    )

    ok1, msg1 = await ensure_pinned_hub_post(
        channel=mock_forum,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        project_name=p1.name,
    )
    assert ok1 is True
    assert "Created and pinned" in msg1
    mock_forum.create_thread.assert_awaited_once()

    # Place the created thread in forum's threads
    mock_forum.threads = [mock_thread]

    # 2. Create Project 2 bound to the same forum
    p2 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend API",
        prefix="BACK",
        discord_channel_id=mock_forum.id,
    )

    ok2, _msg2 = await ensure_pinned_hub_post(
        channel=mock_forum,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        project_name=p2.name,
    )
    assert ok2 is True
    assert mock_thread.edit.await_count == 2
    mock_thread.edit.assert_awaited_with(name="📌 #shared-projects • Forum Control Hub", pinned=True)
    mock_starter_msg.edit.assert_awaited_once()
    edited_embed = mock_starter_msg.edit.call_args.kwargs["embed"]
    assert "Frontend App" in edited_embed.description
    assert "Backend API" in edited_embed.description
