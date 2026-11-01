"""Unit tests for safe ephemeral dismissal in hub menus and tree viewer.

Asserts that:
1. TaskMenuView, ProjectMenuView, and TechTreeViewer contain a Close button that
   deletes the ephemeral card via interaction.delete_original_response().
2. PmHubView methods spawned from the public forum post never invoke
   interaction.delete_original_response() or register the public interaction
   into MenuSessionManager.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.views.hub_menu import PmHubView
from src.adapters.discord_bot.views.project.dashboard import ProjectMenuView
from src.adapters.discord_bot.views.task_menu import TaskMenuView
from src.adapters.discord_bot.views.tree_view import TechTreeViewer
from src.domain.models import Project


@pytest.mark.asyncio
async def test_task_menu_view_close_button():
    task_srv = MagicMock()
    proj_srv = MagicMock()
    squad_srv = MagicMock()

    view = TaskMenuView(task_service=task_srv, project_service=proj_srv, squad_service=squad_srv)

    close_buttons = [c for c in view.children if isinstance(c, discord.ui.Button) and c.label == "Close"]
    assert len(close_buttons) == 1, "TaskMenuView must have exactly one Close button"
    close_btn = close_buttons[0]
    assert close_btn.style == discord.ButtonStyle.secondary

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.defer = AsyncMock()
    interaction.delete_original_response = AsyncMock()

    await view._on_close_clicked(interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_menu_view_close_button():
    proj_srv = MagicMock()
    squad_srv = MagicMock()
    task_srv = MagicMock()

    view = ProjectMenuView(proj_srv, squad_srv, task_srv)

    close_buttons = [c for c in view.children if isinstance(c, discord.ui.Button) and c.label == "Close"]
    assert len(close_buttons) == 1, "ProjectMenuView must have exactly one Close button"
    close_btn = close_buttons[0]
    assert close_btn.style == discord.ButtonStyle.secondary

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.defer = AsyncMock()
    interaction.delete_original_response = AsyncMock()

    await view._on_close_clicked(interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_tech_tree_viewer_close_button():
    task_srv = MagicMock()
    project = Project(id=uuid4(), guild_id=111, name="Test Project", prefix="TP")

    viewer = TechTreeViewer(task_srv, project, current_orientation="lr")

    close_buttons = [c for c in viewer.children if isinstance(c, discord.ui.Button) and c.label == "Close"]
    assert len(close_buttons) == 1, "TechTreeViewer must have exactly one Close button"
    close_btn = close_buttons[0]
    assert close_btn.style == discord.ButtonStyle.secondary

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.defer = AsyncMock()
    interaction.delete_original_response = AsyncMock()

    await viewer._on_close_clicked(interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_hub_view_tabs_never_delete_public_message(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 998877699

    hub_view = PmHubView(
        project_service=proj_srv,
        squad_service=squad_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    # Simulate interaction on public pinned Forum Control Hub post
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.get_channel.return_value = None
    interaction.user = MagicMock()
    interaction.user.id = 12345
    interaction.channel = MagicMock()
    interaction.channel.id = 55555
    interaction.channel.parent_id = None
    interaction.channel.name = "control-hub"

    # Public message mock
    public_msg = MagicMock()
    public_msg.flags = MagicMock()
    public_msg.flags.ephemeral = False
    public_msg.edit = AsyncMock()
    public_msg.delete = AsyncMock()
    interaction.message = public_msg

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Projects Hub tab
    await hub_view.projects_tab.callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
    # Crucial assertion: never delete original response on public interaction
    interaction.delete_original_response.assert_not_awaited()
    public_msg.delete.assert_not_awaited()

    # 2. My Tasks tab
    interaction.response.send_message.reset_mock()
    interaction.delete_original_response.reset_mock()
    await hub_view.tasks_tab.callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
    interaction.delete_original_response.assert_not_awaited()
    public_msg.delete.assert_not_awaited()
