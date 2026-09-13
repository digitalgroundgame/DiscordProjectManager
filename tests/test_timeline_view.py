"""Unit and integration tests for TechTreeViewer timeline mode and slash commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.views.tree_view import TechTreeViewer


@pytest.mark.asyncio
async def test_tech_tree_viewer_timeline_toggle(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877680

    project = await proj_srv.create_project(guild_id=guild_id, name="Viewer Test", prefix="VIEW")
    await task_srv.create_task(guild_id=guild_id, title="Item 1", creator_discord_id=1001, project_id=project.id)

    viewer = TechTreeViewer(task_service=task_srv, project=project, current_mode="tree")
    assert viewer.current_mode == "tree"
    assert hasattr(viewer, "timeline_btn")
    assert hasattr(viewer, "mermaid_btn")

    # Mock interaction for clicking timeline button
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await viewer._on_timeline_clicked(interaction)
    assert viewer.current_mode == "timeline"
    interaction.edit_original_response.assert_awaited_once()
    call_kwargs = interaction.edit_original_response.call_args.kwargs
    embed = call_kwargs.get("embed")
    assert "Timeline:" in embed.title


@pytest.mark.asyncio
async def test_project_timeline_slash_command(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    guild_id = 998877681

    project = await proj_srv.create_project(guild_id=guild_id, name="Sprint Alpha", prefix="ALP")
    await task_srv.create_task(guild_id=guild_id, title="Core Logic", creator_discord_id=1001, project_id=project.id)

    bot = MagicMock()
    pm_cog = PmCog(bot=bot, project_service=proj_srv, squad_service=squad_srv, task_service=task_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await pm_cog.project_timeline.callback(pm_cog, interaction=interaction, project_name="Sprint Alpha")
    interaction.followup.send.assert_awaited_once()
    call_kwargs = interaction.followup.send.call_args.kwargs
    embed = call_kwargs.get("embed")
    assert "Timeline:" in embed.title
    assert "attachment://project_timeline.png" in embed.image.url
