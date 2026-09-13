from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.views.task_buttons import TaskActionView
from src.domain.enums import PriorityLevel, TaskStatus
from src.services.auth_service import AuthService


def test_task_action_view_buttons_labels_and_styles():
    task_id = uuid4()

    # 1. Unassigned task: shows "Claim Task" button (green, no emoji)
    unassigned_view = TaskActionView(
        task_id=task_id,
        current_status=TaskStatus.NOT_STARTED,
        current_assignee_id=None,
    )
    claim_btns = [b for b in unassigned_view.children if getattr(b, "custom_id", "") == f"task:claim:{task_id}"]
    assert len(claim_btns) == 1
    assert claim_btns[0].label == "Claim Task"
    assert claim_btns[0].style == discord.ButtonStyle.success
    assert claim_btns[0].row == 0
    # No emojis in any button labels
    for btn in unassigned_view.children:
        if isinstance(btn, discord.ui.Button) and btn.label:
            assert "✋" not in btn.label
            assert "👤" not in btn.label

    # 2. Assigned task: shows "Unassign Me" button (secondary, no emoji)
    assigned_view = TaskActionView(
        task_id=task_id,
        current_status=TaskStatus.NOT_STARTED,
        current_assignee_id=123456,
    )
    unassign_btns = [b for b in assigned_view.children if getattr(b, "custom_id", "") == f"task:unassign:{task_id}"]
    assert len(unassign_btns) == 1
    assert unassign_btns[0].label == "Unassign Me"
    assert unassign_btns[0].style == discord.ButtonStyle.secondary
    assert unassign_btns[0].row == 0
    for btn in assigned_view.children:
        if isinstance(btn, discord.ui.Button) and btn.label:
            assert "✋" not in btn.label
            assert "👤" not in btn.label


@pytest.mark.asyncio
async def test_claim_task_action_flow(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 1234567890

    project = await proj_srv.create_project(guild_id=guild_id, name="Core Infra", prefix="INF")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Deploy Redis",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.HIGH,
    )
    assert task.assignee_discord_id is None

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(
        bot=bot,
        task_service=task_srv,
        project_service=proj_srv,
        auth_service=auth_srv,
    )

    # 1. Eligible member claims unassigned task
    user_id = 2001
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.guild_permissions = discord.Permissions(administrator=True)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.get_member.return_value = member
    interaction.user = member
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    await adapter.handle_action(interaction, "claim", task.id)
    interaction.response.edit_message.assert_awaited_once()

    updated = await task_srv.get_by_id(task.id)
    assert updated.assignee_discord_id == user_id

    # 2. Already assigned member clicks Claim Task again -> informational ephemeral
    interaction_same = MagicMock(spec=discord.Interaction)
    interaction_same.guild_id = guild_id
    interaction_same.guild = interaction.guild
    interaction_same.user = member
    interaction_same.response = MagicMock()
    interaction_same.response.is_done.return_value = False
    interaction_same.response.send_message = AsyncMock()

    await adapter.handle_action(interaction_same, "claim", task.id)
    interaction_same.response.send_message.assert_awaited_once()
    assert "already assigned" in interaction_same.response.send_message.await_args.args[0]

    # 3. Different member tries to claim already assigned task -> warning ephemeral
    other_member = MagicMock(spec=discord.Member)
    other_member.id = 9999
    other_member.guild_permissions = discord.Permissions(administrator=True)

    interaction_other = MagicMock(spec=discord.Interaction)
    interaction_other.guild_id = guild_id
    interaction_other.guild = interaction.guild
    interaction_other.user = other_member
    interaction_other.response = MagicMock()
    interaction_other.response.is_done.return_value = False
    interaction_other.response.send_message = AsyncMock()

    await adapter.handle_action(interaction_other, "claim", task.id)
    interaction_other.response.send_message.assert_awaited_once()
    assert "already claimed" in interaction_other.response.send_message.await_args.args[0]

    # 4. Assigned member clicks Unassign Me -> unassigns task
    interaction_unassign = MagicMock(spec=discord.Interaction)
    interaction_unassign.guild_id = guild_id
    interaction_unassign.guild = interaction.guild
    interaction_unassign.user = member
    interaction_unassign.response = MagicMock()
    interaction_unassign.response.is_done.return_value = False
    interaction_unassign.response.edit_message = AsyncMock()

    await adapter.handle_action(interaction_unassign, "unassign", task.id)
    interaction_unassign.response.edit_message.assert_awaited_once()

    unassigned_task = await task_srv.get_by_id(task.id)
    assert unassigned_task.assignee_discord_id is None


@pytest.mark.asyncio
async def test_in_thread_context_resolution(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 5555555555
    thread_id = 9988776655

    project = await proj_srv.create_project(guild_id=guild_id, name="DevOps", prefix="OPS")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Automate CI",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.NORMAL,
    )
    # Link task to Discord thread ID
    await task_srv.update_discord_message_ids(task.id, discord_message_id=112233, discord_thread_id=thread_id)

    bot = MagicMock()
    cog = PmCog(
        bot=bot,
        task_service=task_srv,
        project_service=proj_srv,
        squad_service=squad_srv,
        auth_service=auth_srv,
    )

    admin_user = MagicMock(spec=discord.Member)
    admin_user.id = 1001
    admin_user.guild_permissions = discord.Permissions(administrator=True)

    # 1. Resolve with explicit short_id
    interaction_explicit = MagicMock(spec=discord.Interaction)
    interaction_explicit.guild = MagicMock(id=guild_id)
    interaction_explicit.channel_id = 123456  # Some unrelated channel
    resolved = await cog._resolve_task_context(interaction_explicit, task.short_id)
    assert resolved is not None
    assert resolved.id == task.id

    # 2. Resolve implicitly when inside thread workspace (task=None)
    interaction_implicit = MagicMock(spec=discord.Interaction)
    interaction_implicit.guild = MagicMock(id=guild_id)
    interaction_implicit.channel_id = thread_id
    resolved_implicit = await cog._resolve_task_context(interaction_implicit, None)
    assert resolved_implicit is not None
    assert resolved_implicit.id == task.id

    # 3. Resolve outside thread with task=None -> returns None and error message
    interaction_outside = MagicMock(spec=discord.Interaction)
    interaction_outside.guild = MagicMock(id=guild_id)
    interaction_outside.channel_id = 999999999  # Not a task thread
    interaction_outside.followup = MagicMock()
    interaction_outside.followup.send = AsyncMock()

    resolved_none = await cog._resolve_task_context(interaction_outside, None)
    assert resolved_none is None
    interaction_outside.followup.send.assert_awaited_once()
    assert "No task specified" in interaction_outside.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_slash_commands_implicit_thread_context(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 4444444444
    thread_id = 8888888888

    project = await proj_srv.create_project(guild_id=guild_id, name="Security", prefix="SEC")
    task_1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Pen Test Setup",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.NORMAL,
    )
    task_2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Firewall Configuration",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.NORMAL,
    )
    await task_srv.update_discord_message_ids(task_1.id, discord_message_id=111, discord_thread_id=thread_id)

    bot = MagicMock()
    cog = PmCog(
        bot=bot,
        task_service=task_srv,
        project_service=proj_srv,
        squad_service=squad_srv,
        auth_service=auth_srv,
    )

    admin_user = MagicMock(spec=discord.Member)
    admin_user.id = 1001
    admin_user.guild_permissions = discord.Permissions(administrator=True)

    # 1. /pm task status with task=None inside thread
    interaction_status = MagicMock(spec=discord.Interaction)
    interaction_status.guild = MagicMock(id=guild_id)
    interaction_status.channel_id = thread_id
    interaction_status.user = admin_user
    interaction_status.response = MagicMock()
    interaction_status.response.defer = AsyncMock()
    interaction_status.followup = MagicMock()
    interaction_status.followup.send = AsyncMock()

    await cog.task_status.callback(cog, interaction_status, status="in_progress", task=None)
    updated_1 = await task_srv.get_by_id(task_1.id)
    assert updated_1.status == TaskStatus.IN_PROGRESS

    # 2. /pm task assign with task=None inside thread
    assignee = MagicMock(spec=discord.Member)
    assignee.id = 7007
    assignee.guild_permissions = discord.Permissions(administrator=True)
    interaction_status.guild.get_member.return_value = assignee

    interaction_assign = MagicMock(spec=discord.Interaction)
    interaction_assign.guild = interaction_status.guild
    interaction_assign.channel_id = thread_id
    interaction_assign.user = admin_user
    interaction_assign.response = MagicMock()
    interaction_assign.response.defer = AsyncMock()
    interaction_assign.followup = MagicMock()
    interaction_assign.followup.send = AsyncMock()

    await cog.task_assign.callback(cog, interaction_assign, task=None, assignee=assignee)
    updated_1 = await task_srv.get_by_id(task_1.id)
    assert updated_1.assignee_discord_id == 7007

    # 3. /pm task depend with task=None inside thread
    interaction_depend = MagicMock(spec=discord.Interaction)
    interaction_depend.guild = interaction_status.guild
    interaction_depend.channel_id = thread_id
    interaction_depend.user = admin_user
    interaction_depend.response = MagicMock()
    interaction_depend.response.defer = AsyncMock()
    interaction_depend.followup = MagicMock()
    interaction_depend.followup.send = AsyncMock()

    await cog.task_depend.callback(cog, interaction_depend, depends_on=task_2.short_id, task=None)
    prereqs, _ = await task_srv.get_task_dependencies(task_1.id)
    assert any(p.id == task_2.id for p in prereqs)

    # 4. /pm task undepend with task=None inside thread
    interaction_undepend = MagicMock(spec=discord.Interaction)
    interaction_undepend.guild = interaction_status.guild
    interaction_undepend.channel_id = thread_id
    interaction_undepend.user = admin_user
    interaction_undepend.response = MagicMock()
    interaction_undepend.response.defer = AsyncMock()
    interaction_undepend.followup = MagicMock()
    interaction_undepend.followup.send = AsyncMock()

    await cog.task_undepend.callback(cog, interaction_undepend, depends_on=task_2.short_id, task=None)
    prereqs_after, _ = await task_srv.get_task_dependencies(task_1.id)
    assert not any(p.id == task_2.id for p in prereqs_after)

    # 5. /pm task history with task=None inside thread
    interaction_history = MagicMock(spec=discord.Interaction)
    interaction_history.guild = interaction_status.guild
    interaction_history.channel_id = thread_id
    interaction_history.user = admin_user
    interaction_history.response = MagicMock()
    interaction_history.response.defer = AsyncMock()
    interaction_history.followup = MagicMock()
    interaction_history.followup.send = AsyncMock()

    await cog.task_history.callback(cog, interaction_history, task=None)
    interaction_history.followup.send.assert_awaited_once()
    assert interaction_history.followup.send.await_args.kwargs.get("embed") is not None
