from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.adapters.discord_bot.views.task_builder import (
    TaskCreateDraftView,
    TaskCustomDueModal,
    TaskDetailsModal,
)
from src.domain.enums import PriorityLevel


@pytest.mark.asyncio
async def test_task_details_modal_submission(services):
    task_srv = services["task"]
    proj_srv = services["project"]
    guild_id = 1234567890

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra Setup", prefix="INF")

    modal = TaskDetailsModal(task_service=task_srv, project=project)
    assert "New Task: [INF] Infra Setup" in modal.title

    # 1. Empty title validation
    modal.title_input._value = "   "
    empty_interaction = MagicMock(spec=discord.Interaction)
    empty_interaction.response = MagicMock()
    empty_interaction.response.send_message = AsyncMock()
    await modal.on_submit(empty_interaction)
    empty_interaction.response.send_message.assert_awaited_once()
    assert "cannot be empty" in empty_interaction.response.send_message.call_args[0][0]

    # 2. Valid title and description submission launches TaskCreateDraftView
    modal.title_input._value = "Setup Redis Cluster"
    modal.desc_input._value = "Cluster of 3 nodes with replication"
    valid_interaction = MagicMock(spec=discord.Interaction)
    valid_interaction.channel = MagicMock()
    valid_interaction.response = MagicMock()
    valid_interaction.response.send_message = AsyncMock()

    await modal.on_submit(valid_interaction)
    valid_interaction.response.send_message.assert_awaited_once()
    kwargs = valid_interaction.response.send_message.call_args.kwargs
    assert kwargs.get("ephemeral") is True
    draft_view = kwargs.get("view")
    assert isinstance(draft_view, TaskCreateDraftView)
    assert draft_view.title == "Setup Redis Cluster"
    assert draft_view.description == "Cluster of 3 nodes with replication"
    assert draft_view.project.id == project.id


@pytest.mark.asyncio
async def test_task_create_draft_view_interactive_pickers(services):
    task_srv = services["task"]
    proj_srv = services["project"]
    guild_id = 9988776655

    project = await proj_srv.create_project(guild_id=guild_id, name="Frontend UI", prefix="FE")

    draft_view = TaskCreateDraftView(
        task_service=task_srv,
        project=project,
        title="Implement Dark Mode",
        description="Support dark and light theme tokens",
    )

    # 1. Test Assignee selection
    mock_member = MagicMock(spec=discord.Member)
    mock_member.id = 4001
    draft_view.assignee_select._values = [mock_member]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await draft_view._on_assignee_selected(interaction)
    interaction.response.edit_message.assert_awaited_once()
    assert draft_view.assignee_id == 4001
    assert [v.id for v in draft_view.assignee_select.default_values] == [4001]
    embed = interaction.response.edit_message.call_args.kwargs["embed"]
    assert "<@4001>" in embed.description

    # 2. Test Unassign button
    unassign_interaction = MagicMock(spec=discord.Interaction)
    unassign_interaction.response = MagicMock()
    unassign_interaction.response.edit_message = AsyncMock()

    await draft_view._on_unassign_clicked(unassign_interaction)
    unassign_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.assignee_id is None
    assert draft_view.assignee_select.default_values == []
    embed2 = unassign_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "*Unassigned" in embed2.description

    # 3. Test Due Date preset selection ('tomorrow')
    due_interaction = MagicMock(spec=discord.Interaction)
    due_interaction.response = MagicMock()
    due_interaction.response.edit_message = AsyncMock()
    draft_view.due_select._values = ["tomorrow"]

    await draft_view._on_due_selected(due_interaction)
    due_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.due_at is not None

    # Clear due date preset
    clear_due_interaction = MagicMock(spec=discord.Interaction)
    clear_due_interaction.response = MagicMock()
    clear_due_interaction.response.edit_message = AsyncMock()
    draft_view.due_select._values = ["clear"]

    await draft_view._on_due_selected(clear_due_interaction)
    clear_due_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.due_at is None

    # 4. Test Priority selection
    prio_interaction = MagicMock(spec=discord.Interaction)
    prio_interaction.response = MagicMock()
    prio_interaction.response.edit_message = AsyncMock()
    draft_view.priority_select._values = ["high"]

    await draft_view._on_priority_selected(prio_interaction)
    prio_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.priority == PriorityLevel.HIGH

    # 5. Test Watchers selection
    mock_watcher1 = MagicMock(spec=discord.Member)
    mock_watcher1.id = 5001
    mock_watcher2 = MagicMock(spec=discord.Member)
    mock_watcher2.id = 5002
    draft_view.watchers_select._values = [mock_watcher1, mock_watcher2]

    watchers_interaction = MagicMock(spec=discord.Interaction)
    watchers_interaction.response = MagicMock()
    watchers_interaction.response.edit_message = AsyncMock()

    await draft_view._on_watchers_selected(watchers_interaction)
    watchers_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.watchers == [5001, 5002]
    assert [v.id for v in draft_view.watchers_select.default_values] == [5001, 5002]

    # 6. Test Watchers deselect / clear
    draft_view.watchers_select._values = []
    clear_w_interaction = MagicMock(spec=discord.Interaction)
    clear_w_interaction.response = MagicMock()
    clear_w_interaction.response.edit_message = AsyncMock()

    await draft_view._on_watchers_selected(clear_w_interaction)
    clear_w_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.watchers == []
    assert draft_view.watchers_select.default_values == []

    # 7. Test Assignee deselect / clear directly from dropdown
    draft_view.assignee_id = 4001
    draft_view.assignee_select._values = []
    clear_a_interaction = MagicMock(spec=discord.Interaction)
    clear_a_interaction.response = MagicMock()
    clear_a_interaction.response.edit_message = AsyncMock()

    await draft_view._on_assignee_selected(clear_a_interaction)
    clear_a_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.assignee_id is None
    assert draft_view.assignee_select.default_values == []


@pytest.mark.asyncio
async def test_custom_due_date_modal(services):
    task_srv = services["task"]
    draft_view = TaskCreateDraftView(
        task_service=task_srv,
        title="Custom Deadline Task",
    )

    # 1. Due select 'custom' opens modal
    custom_interaction = MagicMock(spec=discord.Interaction)
    custom_interaction.response = MagicMock()
    custom_interaction.response.send_modal = AsyncMock()
    draft_view.due_select._values = ["custom"]

    await draft_view._on_due_selected(custom_interaction)
    custom_interaction.response.send_modal.assert_awaited_once()
    modal = custom_interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, TaskCustomDueModal)

    # 2. Invalid date expression in modal
    modal.due_input._value = "not a real date xyz"
    invalid_interaction = MagicMock(spec=discord.Interaction)
    invalid_interaction.response = MagicMock()
    invalid_interaction.response.edit_message = AsyncMock()
    invalid_interaction.followup = MagicMock()
    mock_toast = MagicMock()
    invalid_interaction.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await modal.on_submit(invalid_interaction)
        invalid_interaction.response.edit_message.assert_awaited_once()
        invalid_interaction.followup.send.assert_awaited_once()
        assert "Could not parse" in invalid_interaction.followup.send.call_args[0][0]
        assert "*⏱️ Auto-dismisses <t:" in invalid_interaction.followup.send.call_args[0][0]
        assert invalid_interaction.followup.send.call_args.kwargs.get("ephemeral") is True
        assert invalid_interaction.followup.send.call_args.kwargs.get("wait") is True
        mock_schedule.assert_called_once_with(mock_toast, delay=10.0)

    # Verify due_select reset to placeholder and existing due_at untouched
    assert draft_view.due_select.placeholder == "Pick due date preset..."
    assert draft_view.due_at is None

    # Verify that an existing valid due_at is also preserved if another invalid input is provided
    existing_due = datetime.now(UTC) + timedelta(days=5)
    draft_view.due_at = existing_due
    draft_view._rebuild_items()

    modal_retry = TaskCustomDueModal(draft_view)
    modal_retry.due_input._value = "invalid date second try"
    retry_interaction = MagicMock(spec=discord.Interaction)
    retry_interaction.response = MagicMock()
    retry_interaction.response.edit_message = AsyncMock()
    retry_interaction.followup = MagicMock()
    retry_interaction.followup.send = AsyncMock(return_value=mock_toast)

    await modal_retry.on_submit(retry_interaction)
    retry_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.due_select.placeholder == "Pick due date preset..."
    assert draft_view.due_at == existing_due

    # 3. Valid date expression in modal
    modal_valid = TaskCustomDueModal(draft_view)
    modal_valid.due_input._value = "friday 5pm"
    valid_interaction = MagicMock(spec=discord.Interaction)
    valid_interaction.response = MagicMock()
    valid_interaction.response.edit_message = AsyncMock()

    await modal_valid.on_submit(valid_interaction)
    valid_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.due_at is not None
    assert draft_view.due_at != existing_due


@pytest.mark.asyncio
async def test_edit_details_and_cancel_in_draft_view(services):
    task_srv = services["task"]
    draft_view = TaskCreateDraftView(
        task_service=task_srv,
        title="Original Title",
        description="Original Description",
    )

    # 1. Test clicking Edit Details opens modal
    edit_btn_interaction = MagicMock(spec=discord.Interaction)
    edit_btn_interaction.response = MagicMock()
    edit_btn_interaction.response.send_modal = AsyncMock()

    await draft_view._on_edit_details_clicked(edit_btn_interaction)
    edit_btn_interaction.response.send_modal.assert_awaited_once()
    edit_modal = edit_btn_interaction.response.send_modal.call_args.args[0]
    assert isinstance(edit_modal, TaskDetailsModal)
    assert edit_modal.title_input.default == "Original Title"

    # Submitting edit modal updates the draft view
    edit_modal.title_input._value = "Updated Task Title"
    edit_modal.desc_input._value = "Updated Task Description"

    modal_sub_interaction = MagicMock(spec=discord.Interaction)
    modal_sub_interaction.response = MagicMock()
    modal_sub_interaction.response.edit_message = AsyncMock()

    await edit_modal.on_submit(modal_sub_interaction)
    modal_sub_interaction.response.edit_message.assert_awaited_once()
    assert draft_view.title == "Updated Task Title"
    assert draft_view.description == "Updated Task Description"

    # 2. Test Cancel button
    cancel_interaction = MagicMock(spec=discord.Interaction)
    cancel_interaction.response = MagicMock()
    cancel_interaction.response.edit_message = AsyncMock()

    await draft_view._on_cancel_clicked(cancel_interaction)
    cancel_interaction.response.edit_message.assert_awaited_once()
    cancel_embed = cancel_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Task Creation Cancelled" in cancel_embed.title


@pytest.mark.asyncio
async def test_confirm_task_creation_in_forum_channel(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 99881122

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 88776655
    mock_forum.name = "backend-dev"
    mock_forum.available_tags = []

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 11223344
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 11223344
    res_mock = MagicMock()
    res_mock.thread = mock_thread
    res_mock.message = mock_msg
    mock_forum.create_thread = AsyncMock(return_value=res_mock)

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend API",
        prefix="API",
        discord_channel_id=mock_forum.id,
    )

    draft_view = TaskCreateDraftView(
        task_service=task_srv,
        project=project,
        title="Implement Rate Limiter",
        description="Token bucket rate limiter per API key",
        target_channel=mock_forum,
        assignee_id=3001,
        priority=PriorityLevel.HIGH,
        due_at=datetime.now(UTC) + timedelta(days=2),
        watchers=[4001, 4002],
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await draft_view._on_confirm_clicked(interaction)

    # 1. Thread created in ForumChannel
    mock_forum.create_thread.assert_awaited_once()
    create_args = mock_forum.create_thread.call_args.kwargs
    assert "[API-1] Implement Rate Limiter" in create_args["name"]

    # 2. Ephemeral success confirmation returned
    interaction.response.edit_message.assert_awaited_once()
    success_embed = interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Task Created: [API-1] Implement Rate Limiter" in success_embed.title
    assert success_embed.footer is not None
    assert "Task UUID:" in success_embed.footer.text
    assert "⏱️ Auto-dismisses <t:" in success_embed.description

    # 3. Database task created with all parameters
    tasks, total = await task_srv.list_tasks(guild_id=guild_id, project_id=project.id)
    assert total == 1
    t = tasks[0]
    assert t.title == "Implement Rate Limiter"
    assert t.assignee_discord_id == 3001
    assert t.priority == PriorityLevel.HIGH
    assert t.watchers == [4001, 4002]
    assert t.discord_thread_id == 11223344
    assert t.discord_message_id == 11223344

    # 4. Cancel clicked verifies dismissal countdown description
    cancel_interaction = MagicMock(spec=discord.Interaction)
    cancel_interaction.response = MagicMock()
    cancel_interaction.response.edit_message = AsyncMock()
    await draft_view._on_cancel_clicked(cancel_interaction)
    cancel_interaction.response.edit_message.assert_awaited_once()
    cancel_embed = cancel_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "⏱️ Auto-dismisses <t:" in cancel_embed.description
