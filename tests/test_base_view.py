import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.views.base_view import BaseModal, BaseView
from src.domain.exceptions import StaleVersionError


@pytest.mark.asyncio
async def test_base_view_on_error_not_done_replies_ephemeral():
    """Verify that if interaction.response.is_done() is False, on_error sends an ephemeral error response."""
    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "test_btn_123"

    error = RuntimeError("Database connection timed out")

    await view.on_error(interaction, error, item)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    args = interaction.response.send_message.call_args[0]
    message_content = args[0] if args else kwargs.get("content", "")
    assert "⚠️ An unexpected error occurred while processing this action" in message_content
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_view_on_error_is_done_sends_followup():
    """Verify that if interaction.response.is_done() is True, on_error sends an ephemeral followup message."""
    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = True
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "test_select_456"

    error = RuntimeError("Late failure after deferral")

    await view.on_error(interaction, error, item)

    interaction.followup.send.assert_awaited_once()
    _, kwargs = interaction.followup.send.call_args
    args = interaction.followup.send.call_args[0]
    message_content = args[0] if args else kwargs.get("content", "")
    assert "⚠️ An unexpected error occurred while processing this action" in message_content
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_view_on_error_stale_version_error():
    """Verify that StaleVersionError is translated to a helpful concurrency message."""
    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "test_btn_stale"

    error = StaleVersionError("Task version mismatch")

    await view.on_error(interaction, error, item)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "already modified by another user" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_view_on_error_logs_interaction_details(caplog):
    """Verify that unhandled exceptions log interaction details (user ID, guild ID, channel ID, custom ID)."""
    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    user = MagicMock()
    user.id = 11223344
    interaction.user = user

    guild = MagicMock()
    guild.id = 55667788
    interaction.guild = guild

    channel = MagicMock()
    channel.id = 99887766
    interaction.channel = channel

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "btn_submit_action"

    error = ValueError("Something invalid occurred")

    with caplog.at_level(logging.ERROR):
        await view.on_error(interaction, error, item)

    log_text = caplog.text
    assert "11223344" in log_text
    assert "55667788" in log_text
    assert "99887766" in log_text
    assert "btn_submit_action" in log_text
    assert "Something invalid occurred" in log_text


@pytest.mark.asyncio
async def test_base_view_on_error_logs_direct_id_attributes(caplog):
    """Verify that top-level guild_id and channel_id are resolved when guild/channel objects are uncached."""
    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    interaction.user = MagicMock(id=123)
    interaction.guild = None
    interaction.guild_id = 456
    interaction.channel = None
    interaction.channel_id = 789

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "uncached_item"

    error = RuntimeError("Uncached context error")

    with caplog.at_level(logging.ERROR):
        await view.on_error(interaction, error, item)

    log_text = caplog.text
    assert "guild_id=456" in log_text
    assert "channel_id=789" in log_text
    assert "user_id=123" in log_text


@pytest.mark.asyncio
async def test_callback_exception_triggers_on_error():
    """Verify that simulated exceptions inside button/select callbacks trigger on_error and reply ephemerally."""

    class FailingView(BaseView):
        @discord.ui.button(label="Boom", custom_id="boom_btn")
        async def boom(self, interaction: discord.Interaction, button: discord.ui.Button):
            raise ValueError("Simulated callback explosion")

    view = FailingView()
    button = view.children[0]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    user = MagicMock()
    user.id = 12345
    interaction.user = user
    interaction.guild = None
    interaction.channel = None

    # In discord.py, component interactions execute via _scheduled_task
    await view._scheduled_task(button, interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "⚠️ An unexpected error occurred while processing this action" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_select_callback_exception_triggers_on_error():
    """Verify that simulated exceptions inside select callbacks trigger on_error and reply ephemerally."""

    class FailingSelectView(BaseView):
        @discord.ui.select(
            placeholder="Choose option",
            options=[discord.SelectOption(label="Opt 1", value="1")],
            custom_id="failing_select_menu",
        )
        async def on_select(self, interaction: discord.Interaction, select: discord.ui.Select):
            raise KeyError("Simulated select callback failure")

    view = FailingSelectView()
    select_item = view.children[0]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.user = MagicMock(id=999)
    interaction.guild = MagicMock(id=888)
    interaction.channel = MagicMock(id=777)

    await view._scheduled_task(select_item, interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "⚠️ An unexpected error occurred while processing this action" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_modal_on_error_not_done_replies_ephemeral():
    """Verify that if interaction.response.is_done() is False, modal on_error sends an ephemeral error response."""

    class TestModal(BaseModal):
        def __init__(self):
            super().__init__(title="Test Modal", custom_id="modal_123")

    modal = TestModal()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    error = RuntimeError("Modal processing error")

    await modal.on_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    args = interaction.response.send_message.call_args[0]
    message_content = args[0] if args else kwargs.get("content", "")
    assert "⚠️ An unexpected error occurred while processing this action" in message_content
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_modal_on_error_is_done_sends_followup():
    """Verify that if interaction.response.is_done() is True, modal on_error sends an ephemeral followup."""

    class TestModal(BaseModal):
        def __init__(self):
            super().__init__(title="Test Modal", custom_id="modal_456")

    modal = TestModal()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = True
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    error = RuntimeError("Late modal failure")

    await modal.on_error(interaction, error)

    interaction.followup.send.assert_awaited_once()
    _, kwargs = interaction.followup.send.call_args
    args = interaction.followup.send.call_args[0]
    message_content = args[0] if args else kwargs.get("content", "")
    assert "⚠️ An unexpected error occurred while processing this action" in message_content
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_base_modal_on_error_logs_interaction_details(caplog):
    """Verify that unhandled modal exceptions log interaction details (user, guild, channel, custom ID)."""

    class TestModal(BaseModal):
        def __init__(self):
            super().__init__(title="Test Modal", custom_id="modal_submit_xyz")

    modal = TestModal()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.user = MagicMock(id=554433)
    interaction.guild_id = 998811
    interaction.guild = None
    interaction.channel_id = 443322
    interaction.channel = None
    interaction.data = {"custom_id": "modal_submit_xyz"}

    error = ValueError("Invalid modal field submission")

    with caplog.at_level(logging.ERROR):
        await modal.on_error(interaction, error)

    log_text = caplog.text
    assert "554433" in log_text
    assert "998811" in log_text
    assert "443322" in log_text
    assert "modal_submit_xyz" in log_text
    assert "Invalid modal field submission" in log_text


def test_interactive_views_inherit_base_view():
    """Verify that all target interactive views inherit from BaseView."""
    from src.adapters.discord_bot.views.hub_menu import PmHubView
    from src.adapters.discord_bot.views.task_buttons import (
        TaskActionControlsView,
        TaskActionView,
        TaskQuickControlsView,
    )
    from src.adapters.discord_bot.views.task_menu import TaskMenuView
    from src.adapters.discord_bot.views.tree_view import TechTreeViewer

    target_views = [
        TaskActionView,
        TaskQuickControlsView,
        TaskActionControlsView,
        PmHubView,
        TechTreeViewer,
        TaskMenuView,
    ]
    for view_cls in target_views:
        assert issubclass(view_cls, BaseView), f"{view_cls.__name__} must inherit from BaseView"
        assert hasattr(view_cls, "on_error")


def test_all_views_in_package_subclass_base_view():
    """Verify that every discord.ui.View subclass in src.adapters.discord_bot.views inherits from BaseView."""
    import importlib
    import inspect
    import pkgutil

    import src.adapters.discord_bot.views as views_pkg

    view_classes = []
    for _, module_name, is_pkg in pkgutil.iter_modules(views_pkg.__path__):
        if is_pkg or module_name == "base_view":
            continue
        mod = importlib.import_module(f"src.adapters.discord_bot.views.{module_name}")
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, discord.ui.View) and obj.__module__ == mod.__name__:
                view_classes.append(obj)

    assert len(view_classes) > 0
    for cls in view_classes:
        assert issubclass(cls, BaseView), f"View {cls.__name__} in module {cls.__module__} does not subclass BaseView"


def test_all_modals_in_package_subclass_base_modal():
    """Verify that every discord.ui.Modal subclass in src.adapters.discord_bot.views inherits from BaseModal."""
    import importlib
    import inspect
    import pkgutil

    import src.adapters.discord_bot.views as views_pkg

    modal_classes = []
    for _, module_name, is_pkg in pkgutil.iter_modules(views_pkg.__path__):
        if is_pkg or module_name == "base_view":
            continue
        mod = importlib.import_module(f"src.adapters.discord_bot.views.{module_name}")
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, discord.ui.Modal) and obj.__module__ == mod.__name__:
                modal_classes.append(obj)

    assert len(modal_classes) > 0
    for cls in modal_classes:
        assert issubclass(cls, BaseModal), (
            f"Modal {cls.__name__} in module {cls.__module__} does not subclass BaseModal"
        )
