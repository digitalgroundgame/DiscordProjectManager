from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.services.seed.discord_provisioner import (
    clean_managed_category_channels,
    resolve_and_provision_guild_roles,
)
from src.services.seed.manifest_loader import SquadSeedSpec


@pytest.mark.asyncio
async def test_resolve_and_provision_guild_roles():
    mock_guild = MagicMock(spec=discord.Guild)
    existing_role = MagicMock(spec=discord.Role)
    existing_role.name = "Existing Role"
    existing_role.id = 5555

    mock_guild.roles = [existing_role]
    mock_guild.fetch_roles = AsyncMock(return_value=[existing_role])

    new_role = MagicMock(spec=discord.Role)
    new_role.name = "New Squad Role"
    new_role.id = 7777
    mock_guild.create_role = AsyncMock(return_value=new_role)

    squads = [
        SquadSeedSpec(name="Existing Squad", role_name="Existing Role"),
        SquadSeedSpec(name="New Squad", role_name="New Squad Role"),
    ]

    mapping = await resolve_and_provision_guild_roles(mock_guild, squads)

    assert mapping["Existing Role"] == 5555
    assert mapping["New Squad Role"] == 7777
    mock_guild.create_role.assert_awaited_once_with(name="New Squad Role")


@pytest.mark.asyncio
async def test_clean_category_isolated_channels():
    mock_guild = MagicMock(spec=discord.Guild)

    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 100
    category.name = "📁 DGG-PM Projects"
    category.delete = AsyncMock()

    chan_in_cat = MagicMock(spec=discord.TextChannel)
    chan_in_cat.id = 101
    chan_in_cat.name = "🧪-scale-testing"
    chan_in_cat.category_id = 100
    chan_in_cat.delete = AsyncMock()

    chan_outside = MagicMock(spec=discord.TextChannel)
    chan_outside.id = 201
    chan_outside.name = "general"
    chan_outside.category_id = 999
    chan_outside.delete = AsyncMock()

    mock_guild.fetch_channels = AsyncMock(return_value=[category, chan_in_cat, chan_outside])

    deleted_count = await clean_managed_category_channels(mock_guild, category_name="📁 DGG-PM Projects")

    assert deleted_count == 1
    chan_in_cat.delete.assert_awaited_once()
    chan_outside.delete.assert_not_awaited()
    category.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_discord_workspaces_hub_buttons():
    from src.adapters.discord_bot.views.hub_menu import PmHubView
    from src.domain.models import Project
    from src.services.project_service import ProjectService
    from src.services.seed.discord_provisioner import provision_discord_workspaces
    from src.services.seed.manifest_loader import ChannelSeedSpec, ProjectSeedSpec, SeedManifest
    from src.services.seed.seed_service import SeedDbResult
    from src.services.task_service import TaskService
    from src.services.team_service import TeamService

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 12345

    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 100
    category.name = "📁 DGG-PM Projects"

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 200
    mock_forum.name = "🎯-single-project"
    mock_forum.available_tags = []
    mock_forum.threads = []
    mock_forum.edit = AsyncMock()

    thread_result = MagicMock()
    thread_result.thread = MagicMock()
    thread_result.thread.edit = AsyncMock()
    thread_result.message = MagicMock()
    mock_forum.create_thread = AsyncMock(return_value=thread_result)

    mock_guild.fetch_channels = AsyncMock(return_value=[category, mock_forum])

    manifest = SeedManifest(
        projects=[ProjectSeedSpec(name="Platform Core", prefix="CORE")],
        channels=[ChannelSeedSpec(name="🎯-single-project", type="forum", projects=["CORE"])],
        tasks=[],
    )

    project = Project(guild_id=12345, name="Platform Core", prefix="CORE", discord_channel_id=200)
    db_result = SeedDbResult(projects_by_prefix={"CORE": project})

    mock_task_service = MagicMock(spec=TaskService)
    mock_task_service.project_service = MagicMock(spec=ProjectService)
    mock_task_service.project_service.list_projects = AsyncMock(return_value=[project])
    mock_task_service.project_service.update_project_channel = AsyncMock()

    mock_team_service = MagicMock(spec=TeamService)

    await provision_discord_workspaces(
        guild=mock_guild,
        manifest=manifest,
        db_result=db_result,
        task_service=mock_task_service,
        team_service=mock_team_service,
    )

    hub_call = mock_forum.create_thread.call_args
    assert hub_call is not None
    view_passed = hub_call.kwargs.get("view")
    assert view_passed is not None
    assert isinstance(view_passed, PmHubView)
    assert len(view_passed.children) > 0
