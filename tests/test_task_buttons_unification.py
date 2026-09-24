from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.views.task_buttons import TaskActionView, TaskQuickControlsView
from src.adapters.discord_bot.views.task_modals import TaskQuickEditTitleModal
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


def test_task_action_view_has_unified_edit_task_button():
    """Verify TaskActionView consolidates Edit Details and Quick Controls into a single Edit Task button."""
    task_id = uuid4()
    view = TaskActionView(
        task_id=task_id,
        current_status=TaskStatus.NOT_STARTED,
        current_priority=PriorityLevel.NORMAL,
    )

    row1_buttons = [item for item in view.children if isinstance(item, discord.ui.Button) and item.row == 1]
    button_labels = [b.label for b in row1_buttons]
    custom_ids = [getattr(b, "custom_id", "") for b in row1_buttons]

    # Consolidated button should be "Edit Task"
    assert "Edit Task" in button_labels
    assert "Edit Details" not in button_labels
    assert "Quick Controls" not in button_labels

    # Verify custom IDs: edit button and dependencies button present
    assert f"task:edit:{task_id}" in custom_ids
    assert f"task:deps:{task_id}" in custom_ids
    # Separate controls button is no longer in the row 1 layout
    assert f"task:controls:{task_id}" not in custom_ids


@pytest.mark.asyncio
async def test_workspace_handle_action_edit_and_controls_open_unified_panel():
    """Verify that both 'edit' and 'controls' actions route to the unified quick_controls panel."""
    task_srv = MagicMock()
    bot = MagicMock()
    workspace = DiscordTaskWorkspaceAdapter(bot=bot, task_service=task_srv)

    task_id = uuid4()
    mock_task = MagicMock(spec=Task)
    mock_task.id = task_id
    mock_task.guild_id = 12345
    task_srv.get_by_id = AsyncMock(return_value=mock_task)

    workspace.render_task_controls = AsyncMock()

    # Test action="edit"
    interaction_edit = MagicMock(spec=discord.Interaction)
    interaction_edit.guild_id = 12345
    interaction_edit.response.is_done.return_value = False
    await workspace.handle_action(interaction_edit, action="edit", task_id=task_id)

    workspace.render_task_controls.assert_awaited_once_with(
        interaction=interaction_edit,
        task=mock_task,
        panel="quick_controls",
    )

    workspace.render_task_controls.reset_mock()

    # Test action="controls" (backward compatibility)
    interaction_ctrl = MagicMock(spec=discord.Interaction)
    interaction_ctrl.guild_id = 12345
    interaction_ctrl.response.is_done.return_value = False
    await workspace.handle_action(interaction_ctrl, action="controls", task_id=task_id)

    workspace.render_task_controls.assert_awaited_once_with(
        interaction=interaction_ctrl,
        task=mock_task,
        panel="quick_controls",
    )


@pytest.mark.asyncio
async def test_unified_controls_view_stages_title_and_body_from_modal():
    """Verify TaskQuickControlsView allows staging title & body updates via modal."""
    task_srv = MagicMock()
    mock_task = MagicMock(spec=Task)
    mock_task.id = uuid4()
    mock_task.short_id = "PRJ-101"
    mock_task.title = "Original Title"
    mock_task.body = "Original Body"
    mock_task.priority = PriorityLevel.NORMAL
    mock_task.assignee_discord_id = None
    mock_task.due_at = None
    mock_task.watchers = []

    view = TaskQuickControlsView(task=mock_task, task_service=task_srv)

    # 1. Check Row 0 contains "Edit Title / Body" button
    row0_buttons = [item for item in view.children if isinstance(item, discord.ui.Button) and item.row == 0]
    button_labels = [b.label for b in row0_buttons]
    assert "Edit Title / Body" in button_labels

    # 2. Check initial staged values
    assert view.staged_title == "Original Title"
    assert view.staged_body == "Original Body"

    # 3. Simulate modal submit
    modal = TaskQuickEditTitleModal(target_view=view)
    modal.title_input._value = "Updated Staged Title"
    modal.desc_input._value = "Updated Staged Body"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    await modal.on_submit(interaction)

    # Staged values should be updated on view
    assert view.staged_title == "Updated Staged Title"
    assert view.staged_body == "Updated Staged Body"

    # Check edit_message was called with updated embed
    interaction.response.edit_message.assert_awaited_once()
    _, kwargs = interaction.response.edit_message.call_args
    updated_embed = kwargs.get("embed")
    assert "Updated Staged Title" in updated_embed.title
    assert "Updated Staged Body" in updated_embed.description


@pytest.mark.asyncio
async def test_unified_controls_view_saves_all_staged_fields_atomically():
    """Verify that clicking Save Changes passes staged title, body, priority, assignee, due date, watchers."""
    mock_workspace = MagicMock()
    mock_workspace.save_task_controls = AsyncMock()

    task_srv = MagicMock()
    mock_task = MagicMock(spec=Task)
    mock_task.id = uuid4()
    mock_task.short_id = "PRJ-202"
    mock_task.title = "Old Title"
    mock_task.body = "Old Body"
    mock_task.priority = PriorityLevel.LOW
    mock_task.assignee_discord_id = None
    mock_task.due_at = None
    mock_task.watchers = []
    mock_task.version = 1

    view = TaskQuickControlsView(task=mock_task, task_service=task_srv, workspace=mock_workspace)

    # Change staged fields
    view.staged_title = "New Atomically Saved Title"
    view.staged_body = "New Atomically Saved Body"
    view.staged_priority = PriorityLevel.HIGH
    view.staged_assignee_id = 998877
    view.staged_watchers = [1122, 3344]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=1001)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    updated_mock_task = MagicMock(spec=Task)
    updated_mock_task.short_id = "PRJ-202"
    mock_workspace.save_task_controls.return_value = updated_mock_task

    await view._on_save_clicked(interaction)

    mock_workspace.save_task_controls.assert_awaited_once_with(
        interaction,
        task=mock_task,
        priority=PriorityLevel.HIGH,
        assignee_id=998877,
        due_at=None,
        clear_due_at=False,
        watchers=[1122, 3344],
        title="New Atomically Saved Title",
        body="New Atomically Saved Body",
        clear_body=False,
    )


@pytest.mark.asyncio
async def test_workspace_adapter_save_task_controls_persists_title_and_body(services):
    """End-to-end verification that save_task_controls persists title and body to database."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 999111222

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra Ops", prefix="INF")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Initial Task Title",
        body="Initial Body",
        creator_discord_id=1001,
        project_id=project.id,
    )

    bot = MagicMock()
    workspace = DiscordTaskWorkspaceAdapter(bot=bot, task_service=task_srv, project_service=proj_srv)
    workspace.sync_workspace = AsyncMock()

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=1001)
    interaction.channel = None

    updated_task = await workspace.save_task_controls(
        interaction=interaction,
        task=task,
        title="Persisted New Title",
        body="Persisted New Body",
        priority=PriorityLevel.HIGH,
    )

    assert updated_task is not None
    assert updated_task.title == "Persisted New Title"
    assert updated_task.body == "Persisted New Body"
    assert updated_task.priority == PriorityLevel.HIGH

    # Verify directly from database
    db_task = await task_srv.get_by_id(task.id)
    assert db_task.title == "Persisted New Title"
    assert db_task.body == "Persisted New Body"
    assert db_task.priority == PriorityLevel.HIGH

    # Verify sync_workspace was invoked with sync_title=True
    workspace.sync_workspace.assert_awaited_once_with(
        updated_task,
        sync_title=True,
        sync_tags=True,
        sync_archive=False,
        sync_starter_card=True,
    )


@pytest.mark.asyncio
async def test_workspace_adapter_save_task_controls_clears_body(services):
    """Verify that save_task_controls with clear_body=True removes the description from the database."""
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 999111222

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra Ops", prefix="INF")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Task With Description",
        body="Initial description to be cleared",
        creator_discord_id=1001,
        project_id=project.id,
    )

    bot = MagicMock()
    workspace = DiscordTaskWorkspaceAdapter(bot=bot, task_service=task_srv, project_service=proj_srv)
    workspace.sync_workspace = AsyncMock()

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=1001)
    interaction.channel = None

    updated_task = await workspace.save_task_controls(
        interaction=interaction,
        task=task,
        clear_body=True,
    )

    assert updated_task is not None
    assert updated_task.body is None

    # Verify in DB
    db_task = await task_srv.get_by_id(task.id)
    assert db_task.body is None


@pytest.mark.asyncio
async def test_unified_controls_view_clearing_body_passes_clear_body_flag():
    """Verify that when a user clears an existing body, save_task_controls receives clear_body=True."""
    mock_workspace = MagicMock()
    mock_workspace.save_task_controls = AsyncMock()

    task_srv = MagicMock()
    mock_task = MagicMock(spec=Task)
    mock_task.id = uuid4()
    mock_task.short_id = "PRJ-205"
    mock_task.title = "Task Title"
    mock_task.body = "Existing description"
    mock_task.priority = PriorityLevel.NORMAL
    mock_task.assignee_discord_id = None
    mock_task.due_at = None
    mock_task.watchers = []
    mock_task.version = 1

    view = TaskQuickControlsView(task=mock_task, task_service=task_srv, workspace=mock_workspace)

    # Simulate clearing body
    view.staged_body = None

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=1001)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    updated_mock_task = MagicMock(spec=Task)
    updated_mock_task.short_id = "PRJ-205"
    mock_workspace.save_task_controls.return_value = updated_mock_task

    await view._on_save_clicked(interaction)

    mock_workspace.save_task_controls.assert_awaited_once_with(
        interaction,
        task=mock_task,
        priority=PriorityLevel.NORMAL,
        assignee_id=None,
        due_at=None,
        clear_due_at=False,
        watchers=[],
        title="Task Title",
        body=None,
        clear_body=True,
    )


def test_unified_controls_view_displays_clear_draft_indicators():
    """Verify TaskQuickControlsView clearly signals that it is an unsaved draft view and orders buttons properly."""
    task_srv = MagicMock()
    mock_task = MagicMock(spec=Task)
    mock_task.id = uuid4()
    mock_task.short_id = "PRJ-303"
    # Long 90-character title to test truncation threshold
    mock_task.title = "Draft UX Task with a very long title that should not be truncated prematurely at 70 chars"
    mock_task.body = "Testing draft indications"
    mock_task.priority = PriorityLevel.NORMAL
    mock_task.assignee_discord_id = None
    mock_task.due_at = None
    mock_task.watchers = []

    view = TaskQuickControlsView(task=mock_task, task_service=task_srv)
    embed = view._build_embed()

    # 1. Embed title signals draft cleanly without alert emoji and preserves titles up to 100 chars
    assert embed.title.startswith("Edit Draft:")
    assert f"[PRJ-303] {mock_task.title}" in embed.title
    assert "⚠️" not in embed.title

    # 2. Embed color is amber/gold
    assert embed.color == discord.Color.gold()

    # 3. Description does not clutter with duplicate header warning
    assert "⚠️ **Unsaved Draft Changes**" not in embed.description

    # 4. Footer displays the single clear unsaved draft warning with alert emoji
    assert "⚠️ Unsaved Draft" in embed.footer.text

    # 5. Row 0 has "Discard Changes" with danger style immediately adjacent to "Save Changes"
    row0_buttons = [item for item in view.children if isinstance(item, discord.ui.Button) and item.row == 0]
    button_labels = [b.label for b in row0_buttons]
    assert button_labels[:3] == ["Save Changes", "Discard Changes", "Edit Title / Body"]
    discard_btn = next((b for b in row0_buttons if b.label == "Discard Changes"), None)
    assert discard_btn is not None
    assert discard_btn.style == discord.ButtonStyle.danger
