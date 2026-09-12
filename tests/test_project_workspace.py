from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.workspace_protocol import (
    ProjectProvisionSpec,
    ProjectWorkspaceRef,
    RebuildWorkspaceResult,
)
from src.domain.exceptions import ProjectNotFoundError


def _create_mock_tag(tag_id: int, name: str) -> MagicMock:
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    return tag


@pytest.mark.asyncio
async def test_provision_project_forum_channel(services):
    """Verifies atomic project provisioning, squad mapping, tags, and Control Hub in a Forum Channel."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(
        bot=bot,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 778899
    mock_forum.name = "infra-pm"
    mock_forum.guild = MagicMock(id=guild_id)
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()

    mock_hub_thread = MagicMock(spec=discord.Thread)
    mock_hub_thread.id = 991122
    mock_hub_thread.name = "📌 #infra-pm • Forum Control Hub"
    mock_hub_thread.jump_url = f"https://discord.com/channels/{guild_id}/991122"
    mock_hub_thread.edit = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_hub_thread)
    mock_forum.threads = []

    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 556677
    mock_role.name = "Infra Squad"

    spec = ProjectProvisionSpec(
        guild_id=guild_id,
        name="Infrastructure Automation",
        prefix="INF",
        role=mock_role,
        channel=mock_forum,
        description="Automated cluster tooling",
        category="DevOps",
    )

    ref = await adapter.provision_project(spec)

    assert isinstance(ref, ProjectWorkspaceRef)
    assert ref.project.name == "Infrastructure Automation"
    assert ref.project.prefix == "INF"
    assert ref.project.discord_channel_id == mock_forum.id
    assert ref.project.discord_role_id == mock_role.id
    assert ref.team is not None
    assert ref.team.discord_role_id == mock_role.id

    # Verify team was mapped to project in database
    teams = await proj_srv.list_teams_for_project(ref.project.id)
    assert len(teams) == 1
    assert teams[0].id == ref.team.id

    # Verify forum tags were configured and Control Hub thread was created
    mock_forum.create_thread.assert_awaited_once()
    assert ref.control_hub_thread_id == 991122
    assert "991122" in ref.jump_url


@pytest.mark.asyncio
async def test_provision_project_text_channel_fallback(services):
    """Verifies project provisioning falls back cleanly for text channels."""
    proj_srv = services["project"]
    guild_id = 22334455

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(
        bot=bot,
        project_service=proj_srv,
    )

    mock_text = MagicMock(spec=discord.TextChannel)
    mock_text.id = 667788
    mock_text.name = "general-pm"
    mock_text.guild = MagicMock(id=guild_id)
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 445566
    mock_msg.pin = AsyncMock()
    mock_text.send = AsyncMock(return_value=mock_msg)
    mock_text.pins = AsyncMock(return_value=[])

    spec = ProjectProvisionSpec(
        guild_id=guild_id,
        name="Documentation Project",
        prefix="DOC",
        channel=mock_text,
    )

    ref = await adapter.provision_project(spec)

    assert ref.project.name == "Documentation Project"
    assert ref.channel_id == mock_text.id
    mock_text.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_project_rejects_thread(services):
    """Verifies that attempting to bind a project to a standalone Thread raises ValueError."""
    proj_srv = services["project"]
    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 123123
    mock_thread.parent = None  # Not a forum child

    spec = ProjectProvisionSpec(
        guild_id=111,
        name="Invalid Thread Project",
        prefix="ITP",
        channel=mock_thread,
    )

    with pytest.raises(ValueError, match="Projects cannot be bound to a Thread Workspace"):
        await adapter.provision_project(spec)


@pytest.mark.asyncio
async def test_provision_project_auto_derives_prefix(services):
    """Verifies that omitting prefix derives a default uppercase prefix automatically."""
    proj_srv = services["project"]
    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    spec = ProjectProvisionSpec(
        guild_id=334455,
        name="Security Architecture Review",
        prefix=None,
    )

    ref = await adapter.provision_project(spec)
    assert ref.project.prefix == "SAR"


@pytest.mark.asyncio
async def test_sync_control_hub(services):
    """Verifies sync_control_hub refreshes PM tags and Control Hub post."""
    proj_srv = services["project"]
    guild_id = 990011
    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 887766
    mock_forum.name = "backend-pm"
    mock_forum.guild = MagicMock(id=guild_id)
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()

    mock_hub = MagicMock(spec=discord.Thread)
    mock_hub.id = 332211
    mock_hub.name = "📌 #backend-pm • Forum Control Hub"
    mock_hub.starter_message = MagicMock()
    mock_hub.starter_message.edit = AsyncMock()
    mock_hub.edit = AsyncMock()
    mock_forum.threads = [mock_hub]

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend Core",
        prefix="BC",
        discord_channel_id=mock_forum.id,
    )

    ref = await adapter.sync_control_hub(mock_forum, project_id=project.id)
    assert ref is not None
    assert ref.project.id == project.id


@pytest.mark.asyncio
async def test_rebind_channel(services):
    """Verifies rebind_channel updates the project DB record and mounts in the new channel."""
    proj_srv = services["project"]
    guild_id = 445566
    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Data Pipeline",
        prefix="DATA",
        discord_channel_id=111111,
    )

    new_forum = MagicMock(spec=discord.ForumChannel)
    new_forum.id = 222222
    new_forum.name = "data-pm"
    new_forum.guild = MagicMock(id=guild_id)
    new_forum.available_tags = []
    new_forum.edit = AsyncMock()
    new_forum.create_thread = AsyncMock(return_value=MagicMock(spec=discord.Thread, id=999))
    new_forum.threads = []

    ref = await adapter.rebind_channel(project.id, new_forum)

    assert ref.project.discord_channel_id == 222222
    updated_in_db = await proj_srv.get_by_id(project.id)
    assert updated_in_db.discord_channel_id == 222222


@pytest.mark.asyncio
async def test_rebind_channel_nonexistent_project(services):
    """Verifies rebind_channel raises ProjectNotFoundError if project_id is invalid."""
    proj_srv = services["project"]
    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    with pytest.raises(ProjectNotFoundError):
        await adapter.rebind_channel(uuid4(), 12345)


@pytest.mark.asyncio
async def test_rebuild_workspace_provisions_missing_forum_channel(services):
    """Verifies that rebuilding a project with a deleted forum auto-creates a new Forum Channel."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(
        bot=bot,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    # Create project with a channel ID that is missing from Discord
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Mobile App",
        prefix="MOB",
        discord_channel_id=999888,
        category="Engineering",
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_guild.get_channel = MagicMock(return_value=None)
    mock_guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Channel not found"))
    mock_guild.categories = []

    # Newly created forum channel mock
    created_forum = MagicMock(spec=discord.ForumChannel)
    created_forum.id = 555666
    created_forum.name = "📁-mobile-app"
    created_forum.guild = mock_guild
    created_forum.available_tags = []
    created_forum.edit = AsyncMock()
    created_hub_thread = MagicMock(spec=discord.Thread, id=777888)
    created_hub_thread.jump_url = f"https://discord.com/channels/{guild_id}/777888"
    created_hub_thread.edit = AsyncMock()
    created_forum.create_thread = AsyncMock(return_value=created_hub_thread)
    created_forum.threads = []

    mock_guild.create_forum = AsyncMock(return_value=created_forum)
    mock_guild.create_forum_channel = AsyncMock(return_value=created_forum)

    result = await adapter.rebuild_workspace(project.id, guild=mock_guild)

    assert isinstance(result, RebuildWorkspaceResult)
    assert result.forum_created is True
    assert result.channel_id == created_forum.id
    assert result.hub_rebuilt is True

    # Database updated with new channel ID
    updated_proj = await proj_srv.get_by_id(project.id)
    assert updated_proj.discord_channel_id == created_forum.id
    mock_guild.create_forum.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebuild_workspace_rebinds_to_target_channel(services):
    """Verifies that passing an explicit target_channel rebinds the project instead of auto-creating."""
    proj_srv = services["project"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Analytics",
        prefix="ANA",
        discord_channel_id=111222,
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id

    target_forum = MagicMock(spec=discord.ForumChannel)
    target_forum.id = 333444
    target_forum.name = "new-analytics"
    target_forum.guild = mock_guild
    target_forum.available_tags = []
    target_forum.edit = AsyncMock()
    target_forum.create_thread = AsyncMock(return_value=MagicMock(spec=discord.Thread, id=888999))
    target_forum.threads = []

    result = await adapter.rebuild_workspace(project.id, guild=mock_guild, target_channel=target_forum)

    assert result.forum_created is False
    assert result.channel_id == target_forum.id
    updated_proj = await proj_srv.get_by_id(project.id)
    assert updated_proj.discord_channel_id == target_forum.id


@pytest.mark.asyncio
async def test_rebuild_workspace_unarchives_archived_project(services):
    """Verifies that rebuilding an archived project automatically clears archived_at in the DB."""
    proj_srv = services["project"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordProjectWorkspaceAdapter(bot=bot, project_service=proj_srv)

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Legacy Core",
        prefix="LEG",
        discord_channel_id=444555,
    )
    # Archive the project
    await proj_srv.archive_project(project.id)
    archived = await proj_srv.get_by_id(project.id)
    assert archived.is_archived is True

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    existing_forum = MagicMock(spec=discord.ForumChannel)
    existing_forum.id = 444555
    existing_forum.guild = mock_guild
    existing_forum.available_tags = []
    existing_forum.edit = AsyncMock()
    existing_forum.create_thread = AsyncMock(return_value=MagicMock(spec=discord.Thread, id=101010))
    existing_forum.threads = []
    mock_guild.get_channel = MagicMock(return_value=existing_forum)

    result = await adapter.rebuild_workspace(project.id, guild=mock_guild)
    assert result.project.is_archived is False
    unarchived_db = await proj_srv.get_by_id(project.id)
    assert unarchived_db.is_archived is False


@pytest.mark.asyncio
async def test_rebuild_workspace_reconciles_active_and_historical_tasks(services):
    """Verifies that rebuilding a workspace reconciles intact threads, recreates missing active threads,

    and recreates-then-archives completed/archived threads (Archive Invariant).
    """
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    mock_task_workspace = MagicMock()
    mock_task_workspace.sync_workspace = AsyncMock(return_value=True)

    adapter = DiscordProjectWorkspaceAdapter(
        bot=bot,
        project_service=proj_srv,
        task_service=task_srv,
        task_workspace=mock_task_workspace,
    )

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Security Audit",
        prefix="SEC",
        discord_channel_id=5001,
    )

    # Task 1: Active, intact thread
    t1 = await task_srv.create_task(
        guild_id=guild_id,
        project_name=project.name,
        title="Check SSL certificates",
        creator_discord_id=123,
    )
    await task_srv.update_discord_message_ids(t1.id, discord_message_id=9001, discord_thread_id=1001)

    # Task 2: Active, missing thread
    t2 = await task_srv.create_task(
        guild_id=guild_id,
        project_name=project.name,
        title="Patch OpenSSL vulnerability",
        creator_discord_id=123,
    )
    await task_srv.update_discord_message_ids(t2.id, discord_message_id=9002, discord_thread_id=1002)

    # Task 3: Completed, missing thread
    t3 = await task_srv.create_task(
        guild_id=guild_id,
        project_name=project.name,
        title="Review firewall rules",
        creator_discord_id=123,
    )
    from src.domain.enums import TaskStatus

    await task_srv.update_status(
        task_id=t3.id,
        expected_version=t3.version,
        new_status=TaskStatus.COMPLETED,
        actor_discord_id=123,
    )
    await task_srv.update_discord_message_ids(t3.id, discord_message_id=9003, discord_thread_id=1003)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id

    # Existing forum channel
    existing_forum = MagicMock(spec=discord.ForumChannel)
    existing_forum.id = 5001
    existing_forum.guild = mock_guild
    existing_forum.available_tags = []
    existing_forum.edit = AsyncMock()
    existing_forum.create_thread = AsyncMock(return_value=MagicMock(spec=discord.Thread, id=777))
    existing_forum.threads = []

    # Intact thread 1001
    intact_thread = MagicMock(spec=discord.Thread)
    intact_thread.id = 1001
    intact_thread.edit = AsyncMock()

    # Thread 1002 and 1003 return None (deleted)
    def get_channel_side_effect(cid):
        if cid == 5001:
            return existing_forum
        if cid == 1001:
            return intact_thread
        return None

    mock_guild.get_channel = MagicMock(side_effect=get_channel_side_effect)
    mock_guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Not found"))

    # Provision mock for missing threads
    from src.adapters.discord_bot.workspace_protocol import TaskWorkspaceRef

    new_t2_thread = MagicMock(spec=discord.Thread, id=2002)
    new_t2_thread.edit = AsyncMock()
    new_t3_thread = MagicMock(spec=discord.Thread, id=2003)
    new_t3_thread.edit = AsyncMock()

    def provision_side_effect(task, **kwargs):
        if task.id == t2.id:
            return TaskWorkspaceRef(thread_id=2002, message_id=3002, channel_id=5001, jump_url="url2")
        elif task.id == t3.id:
            return TaskWorkspaceRef(thread_id=2003, message_id=3003, channel_id=5001, jump_url="url3")
        raise ValueError(f"Unexpected task: {task.id}")

    mock_task_workspace.provision_workspace = AsyncMock(side_effect=provision_side_effect)

    # When fetching the newly created threads to archive them
    def bot_get_channel_side_effect(cid):
        if cid == 2002:
            return new_t2_thread
        if cid == 2003:
            return new_t3_thread
        return get_channel_side_effect(cid)

    bot.get_channel = MagicMock(side_effect=bot_get_channel_side_effect)

    result = await adapter.rebuild_workspace(project.id, guild=mock_guild)

    assert result.tasks_reconciled == 1
    assert result.tasks_recreated == 2
    assert result.tasks_archived == 1

    # Verify intact thread was synced
    mock_task_workspace.sync_workspace.assert_awaited_once()

    # Verify missing tasks were provisioned
    assert mock_task_workspace.provision_workspace.await_count == 2

    # Verify DB was updated with new thread/message IDs for t2 and t3
    updated_t2 = await task_srv.get_by_id(t2.id)
    assert updated_t2.discord_thread_id == 2002
    assert updated_t2.discord_message_id == 3002

    updated_t3 = await task_srv.get_by_id(t3.id)
    assert updated_t3.discord_thread_id == 2003
    assert updated_t3.discord_message_id == 3003

    # Verify completed task 3 thread was archived to satisfy Archive Invariant
    new_t3_thread.edit.assert_awaited_once_with(archived=True)


@pytest.mark.asyncio
async def test_rebuild_workspace_invokes_progress_callback(services):
    """Verifies that rebuild_workspace dispatches progressive status updates to progress_callback."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 11223344

    bot = MagicMock(spec=discord.Client)
    mock_task_workspace = MagicMock()
    mock_task_workspace.sync_workspace = AsyncMock(return_value=True)

    adapter = DiscordProjectWorkspaceAdapter(
        bot=bot,
        project_service=proj_srv,
        task_service=task_srv,
        task_workspace=mock_task_workspace,
    )

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Telemetry",
        prefix="TEL",
        discord_channel_id=8888,
    )

    t1 = await task_srv.create_task(
        guild_id=guild_id,
        project_name=project.name,
        title="Metric scraping",
        creator_discord_id=123,
    )
    await task_srv.update_discord_message_ids(t1.id, discord_message_id=101, discord_thread_id=201)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 8888
    mock_forum.guild = mock_guild
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=MagicMock(spec=discord.Thread, id=999))
    mock_forum.threads = []

    intact_thread = MagicMock(spec=discord.Thread, id=201)
    intact_thread.edit = AsyncMock()

    def get_channel(cid):
        if cid == 8888:
            return mock_forum
        if cid == 201:
            return intact_thread
        return None

    mock_guild.get_channel = MagicMock(side_effect=get_channel)

    progress_reports = []

    async def on_progress(p):
        progress_reports.append(p)

    await adapter.rebuild_workspace(project.id, guild=mock_guild, progress_callback=on_progress)

    steps = [p.step for p in progress_reports]
    assert "channel" in steps
    assert "tags" in steps
    assert "hub" in steps
    assert "tasks" in steps
