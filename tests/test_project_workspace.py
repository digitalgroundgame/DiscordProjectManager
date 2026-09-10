from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.workspace_protocol import (
    ProjectProvisionSpec,
    ProjectWorkspaceRef,
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
