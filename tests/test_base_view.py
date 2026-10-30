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
async def test_base_view_on_error_domain_error():
    """Verify that DggPmError/DomainError is translated to a user-facing error message."""
    from src.domain.exceptions import PermissionDeniedError

    view = BaseView()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    item = MagicMock(spec=discord.ui.Item)
    item.custom_id = "test_btn_perm"

    error = PermissionDeniedError("<@123> does not hold the required squad role.")

    await view.on_error(interaction, error, item)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "❌ <@123> does not hold the required squad role." in args[0]
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


def test_base_view_default_timeout():
    """Verify that BaseView defaults to EPHEMERAL_MENU_TIMEOUT_SECONDS (810s / 13.5m)."""
    from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS

    view = BaseView()
    assert view.timeout == EPHEMERAL_MENU_TIMEOUT_SECONDS
    assert view.timeout == 810.0


@pytest.mark.asyncio
async def test_base_view_on_timeout_unregisters_and_deletes_response():
    """Verify that BaseView.on_timeout deletes the original response and unregisters menu."""
    from src.adapters.discord_bot.menu_manager import get_menu_manager

    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = 999
    inter.user.id = 888
    inter.delete_original_response = AsyncMock()

    manager = get_menu_manager()
    await manager.register_menu(inter)
    assert (999, 888) in manager._active_menus

    view = BaseView(initial_interaction=inter)
    await view.on_timeout()

    inter.delete_original_response.assert_awaited_once()
    assert (999, 888) not in manager._active_menus


@pytest.mark.asyncio
async def test_base_view_on_timeout_graceful_when_no_interaction():
    """Verify that BaseView.on_timeout handles missing interaction without crashing."""
    view = BaseView(initial_interaction=None)
    # Should not raise any exception
    await view.on_timeout()


def test_ui_constants_values():
    """Verify standard ephemeral timing and design language constants."""
    from src.adapters.discord_bot.ui_constants import (
        BUTTON_STYLE_DANGER,
        BUTTON_STYLE_PRIMARY,
        BUTTON_STYLE_SECONDARY,
        BUTTON_STYLE_SUCCESS,
        DISCORD_INTERACTION_MAX_SECONDS,
        EPHEMERAL_MENU_TIMEOUT_SECONDS,
        TOAST_DISMISS_PREFIX,
        TOAST_DURATION_EXTENDED,
        TOAST_DURATION_STANDARD,
    )

    assert DISCORD_INTERACTION_MAX_SECONDS == 900.0
    assert EPHEMERAL_MENU_TIMEOUT_SECONDS == 810.0
    # Exactly 10% shorter than Discord interaction max token expiry
    assert EPHEMERAL_MENU_TIMEOUT_SECONDS == DISCORD_INTERACTION_MAX_SECONDS * 0.90
    assert TOAST_DURATION_STANDARD == 6.0
    assert TOAST_DURATION_EXTENDED == 10.0
    assert TOAST_DISMISS_PREFIX == "Auto-dismisses"

    assert BUTTON_STYLE_PRIMARY == discord.ButtonStyle.primary
    assert BUTTON_STYLE_SECONDARY == discord.ButtonStyle.secondary
    assert BUTTON_STYLE_SUCCESS == discord.ButtonStyle.success
    assert BUTTON_STYLE_DANGER == discord.ButtonStyle.danger


def test_views_have_standard_ephemeral_timeout():
    """Verify that all non-persistent views default to EPHEMERAL_MENU_TIMEOUT_SECONDS."""
    import inspect

    from src.adapters.discord_bot.ui_constants import EPHEMERAL_MENU_TIMEOUT_SECONDS
    from src.adapters.discord_bot.views.admin_menu import PmDashboardOverviewView, PmDashboardView
    from src.adapters.discord_bot.views.admin_team_leads_view import TeamLeadsAdminView
    from src.adapters.discord_bot.views.calendar_feed_view import CalendarFeedView
    from src.adapters.discord_bot.views.hub_project_select_views import (
        HubBoardProjectSelectView,
        HubTaskProjectSelectView,
    )
    from src.adapters.discord_bot.views.project.archive import ProjectArchiveConfirmView, ProjectArchiveSelectView
    from src.adapters.discord_bot.views.project.dashboard import ProjectMenuView
    from src.adapters.discord_bot.views.project.governance import (
        ProjectAssignSquadView,
        ProjectLeadSelectView,
    )
    from src.adapters.discord_bot.views.project.project_roles import ProjectRoleSelectView
    from src.adapters.discord_bot.views.project.rebuild import ProjectRebuildSelectView, RebuildConfirmView
    from src.adapters.discord_bot.views.project.restore import ProjectRestoreConfirmView, ProjectRestoreSelectView
    from src.adapters.discord_bot.views.project.roster import ProjectActiveListView
    from src.adapters.discord_bot.views.project.wizard import ProjectCreateDraftView
    from src.adapters.discord_bot.views.project.wizard_inputs import ProjectChannelSelectView
    from src.adapters.discord_bot.views.settings_menu import UserSettingsView
    from src.adapters.discord_bot.views.squad_member_assign_view import SquadMemberAssignView
    from src.adapters.discord_bot.views.squad_menu import (
        SquadCreateRoleSelectView,
        SquadMenuView,
        SquadOverviewListView,
    )
    from src.adapters.discord_bot.views.squad_roster_view import SquadRosterDetailView
    from src.adapters.discord_bot.views.task_blocked_view import TaskBlockedConfirmView
    from src.adapters.discord_bot.views.task_builder import TaskCreateDraftView
    from src.adapters.discord_bot.views.task_builder_inputs import DraftPrerequisiteSelectView
    from src.adapters.discord_bot.views.task_buttons import TaskQuickControlsView
    from src.adapters.discord_bot.views.task_delete_view import TaskDeleteConfirmView
    from src.adapters.discord_bot.views.task_dependency_view import TaskDependencyView
    from src.adapters.discord_bot.views.task_list_view import TaskListView
    from src.adapters.discord_bot.views.task_menu import TaskMenuView
    from src.adapters.discord_bot.views.task_select_project import TaskSelectProjectView
    from src.adapters.discord_bot.views.tree_view import TechTreeProjectSelectView, TechTreeViewer

    views = [
        PmDashboardView,
        PmDashboardOverviewView,
        TeamLeadsAdminView,
        CalendarFeedView,
        HubBoardProjectSelectView,
        HubTaskProjectSelectView,
        ProjectArchiveConfirmView,
        ProjectArchiveSelectView,
        ProjectMenuView,
        ProjectAssignSquadView,
        ProjectLeadSelectView,
        ProjectRoleSelectView,
        ProjectRebuildSelectView,
        RebuildConfirmView,
        ProjectRestoreConfirmView,
        ProjectRestoreSelectView,
        ProjectActiveListView,
        ProjectCreateDraftView,
        ProjectChannelSelectView,
        UserSettingsView,
        SquadMemberAssignView,
        SquadCreateRoleSelectView,
        SquadOverviewListView,
        SquadMenuView,
        SquadRosterDetailView,
        TaskBlockedConfirmView,
        TaskCreateDraftView,
        DraftPrerequisiteSelectView,
        TaskQuickControlsView,
        TaskDeleteConfirmView,
        TaskDependencyView,
        TaskListView,
        TaskMenuView,
        TaskSelectProjectView,
        TechTreeProjectSelectView,
        TechTreeViewer,
    ]

    for view_cls in views:
        sig = inspect.signature(view_cls.__init__)
        assert "timeout" in sig.parameters, f"{view_cls.__name__} missing timeout parameter in __init__"
        default = sig.parameters["timeout"].default
        assert default == EPHEMERAL_MENU_TIMEOUT_SECONDS, (
            f"{view_cls.__name__} timeout default is {default}, expected {EPHEMERAL_MENU_TIMEOUT_SECONDS}"
        )
