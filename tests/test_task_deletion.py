from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter
from src.adapters.discord_bot.views.task_delete_view import (
    TaskDeleteConfirmView,
    build_task_delete_confirm_embed,
)
from src.domain.enums import OutboxStatus, PriorityLevel, TaskStatus
from src.domain.exceptions import PermissionDeniedError
from src.domain.models import Task
from src.services.auth_service import AuthService


def _mock_member(
    user_id: int, is_admin: bool = False, is_manager: bool = False, role_ids: list[int] | None = None
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    perms = MagicMock()
    perms.administrator = is_admin
    perms.manage_guild = is_manager
    member.guild_permissions = perms

    roles = []
    for rid in role_ids or []:
        r = MagicMock(spec=discord.Role)
        r.id = rid
        roles.append(r)
    member.roles = roles
    return member


@pytest.mark.asyncio
async def test_auth_delete_permissions(services):
    proj_srv = services["project"]
    auth_srv = AuthService(project_service=proj_srv)
    guild_id = 123456

    lead_id = 100
    creator_id = 200
    assignee_id = 300
    manager_id = 400
    random_user_id = 500

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Apollo Mission",
        prefix="APL",
        lead_discord_id=lead_id,
    )

    task_not_started = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="APL-1",
        title="Fuel rocket",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.HIGH,
        creator_discord_id=creator_id,
        assignee_discord_id=assignee_id,
    )

    task_in_progress = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="APL-2",
        title="Launch sequence",
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        creator_discord_id=creator_id,
        assignee_discord_id=assignee_id,
    )

    manager_user = _mock_member(manager_id, is_manager=True)
    lead_user = _mock_member(lead_id)
    creator_user = _mock_member(creator_id)
    assignee_user = _mock_member(assignee_id)
    random_user = _mock_member(random_user_id)

    # 1. Server Manager can delete any task regardless of status
    assert await auth_srv.can_delete_task(manager_user, task_not_started) is True
    assert await auth_srv.can_delete_task(manager_user, task_in_progress) is True
    await auth_srv.require_task_deletion(manager_user, task_not_started)
    await auth_srv.require_task_deletion(manager_user, task_in_progress)

    # 2. Project Lead can delete any task in the project regardless of status
    assert await auth_srv.can_delete_task(lead_user, task_not_started) is True
    assert await auth_srv.can_delete_task(lead_user, task_in_progress) is True
    await auth_srv.require_task_deletion(lead_user, task_not_started)
    await auth_srv.require_task_deletion(lead_user, task_in_progress)

    # 3. Creator can delete ONLY when status is notStarted
    assert await auth_srv.can_delete_task(creator_user, task_not_started) is True
    await auth_srv.require_task_deletion(creator_user, task_not_started)

    assert await auth_srv.can_delete_task(creator_user, task_in_progress) is False
    with pytest.raises(PermissionDeniedError, match="permission to delete this task"):
        await auth_srv.require_task_deletion(creator_user, task_in_progress)

    # 4. Assignee (without manager/lead/creator role) cannot delete
    assert await auth_srv.can_delete_task(assignee_user, task_not_started) is False
    with pytest.raises(PermissionDeniedError):
        await auth_srv.require_task_deletion(assignee_user, task_not_started)

    # 5. Random user cannot delete
    assert await auth_srv.can_delete_task(random_user, task_not_started) is False
    with pytest.raises(PermissionDeniedError):
        await auth_srv.require_task_deletion(random_user, task_not_started)


@pytest.mark.asyncio
async def test_cascading_database_deletion(services, db_session):
    proj_srv = services["project"]
    task_srv = services["task"]
    outbox_srv = services["outbox"]
    guild_id = 998877

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Saturn Project",
        prefix="SAT",
    )

    # Create 3 tasks
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Prerequisite task", creator_discord_id=1001, project_id=project.id
    )
    t2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Target task to delete",
        creator_discord_id=1001,
        project_id=project.id,
        due_at=datetime.now(UTC) + timedelta(days=2),
        watchers=[2001, 2002],
    )
    t3 = await task_srv.create_task(
        guild_id=guild_id, title="Dependent task", creator_discord_id=1001, project_id=project.id
    )

    # Setup dependencies: t2 depends on t1, t3 depends on t2
    await task_srv.add_dependency(guild_id, t2.short_id, t1.short_id)
    await task_srv.add_dependency(guild_id, t3.short_id, t2.short_id)

    # Verify dependencies are wired
    prereqs_t2, dependents_t2 = await task_srv.get_task_dependencies(t2.id)
    assert any(p.id == t1.id for p in prereqs_t2)
    assert any(d.id == t3.id for d in dependents_t2)

    # Update status to add history
    await task_srv.update_status(
        t2.id, expected_version=t2.version, new_status=TaskStatus.IN_PROGRESS, actor_discord_id=1001
    )
    history_before = await task_srv.get_history(t2.id)
    assert len(history_before) > 0

    # Reminders scheduled for t2
    reminders = await outbox_srv.schedule_task_reminders(t2)
    assert len(reminders) > 0

    # Perform deletion
    deleted = await task_srv.delete_task(t2.id, actor_discord_id=1001)
    assert deleted is not None
    assert deleted.id == t2.id
    assert deleted.short_id == t2.short_id

    # 1. Task record is gone
    assert await task_srv.get_by_id(t2.id) is None
    assert await task_srv.get_by_short_id(guild_id, t2.short_id) is None

    # 2. Dependencies involving t2 are purged
    _, dependents_t1 = await task_srv.get_task_dependencies(t1.id)
    assert not any(d.id == t2.id for d in dependents_t1)

    prereqs_t3, _ = await task_srv.get_task_dependencies(t3.id)
    assert not any(p.id == t2.id for p in prereqs_t3)

    # 3. History is purged
    history_after = await task_srv.get_history(t2.id)
    assert len(history_after) == 0

    # 4. Outbox events are cancelled
    from sqlalchemy import select

    from src.adapters.db.tables import OutboxEventTable

    stmt = select(OutboxEventTable).where(OutboxEventTable.idempotency_key.like(f"task_due:{t2.id}:%"))
    res = await db_session.execute(stmt)
    events = res.scalars().all()
    for ev in events:
        assert ev.status == OutboxStatus.CANCELLED.value

    # Deleting an already deleted task returns None
    assert await task_srv.delete_task(t2.id) is None


@pytest.mark.asyncio
async def test_workspace_deletion_and_audit(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877

    bot = MagicMock(spec=discord.Client)
    adapter = DiscordTaskWorkspaceAdapter(bot, task_service=task_srv, project_service=proj_srv)

    project_channel = MagicMock(spec=discord.TextChannel)
    project_channel.id = 88888
    project_channel.send = AsyncMock()

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Gemini Rover",
        prefix="GEM",
        discord_channel_id=project_channel.id,
    )

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 77777
    mock_thread.delete = AsyncMock()
    mock_thread.send = AsyncMock()
    mock_thread.edit = AsyncMock()

    def get_channel_side_effect(cid: int):
        if cid == 77777:
            return mock_thread
        if cid == 88888:
            return project_channel
        return None

    bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

    task = Task(
        id=uuid4(),
        guild_id=guild_id,
        project_id=project.id,
        short_id="GEM-1",
        title="Check wheel motor telemetry",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=1001,
        discord_thread_id=mock_thread.id,
    )

    # 1. Normal thread deletion and audit log
    ok = await adapter.delete_workspace(task, actor_discord_id=1001)
    assert ok is True
    mock_thread.delete.assert_awaited_once()
    project_channel.send.assert_awaited_once()
    audit_call_kwargs = project_channel.send.call_args.kwargs
    assert "embed" in audit_call_kwargs
    embed = audit_call_kwargs["embed"]
    assert "GEM-1" in embed.description
    assert "<@1001>" in embed.fields[0].value

    # 2. Thread deletion failure fallback (posts notice, renames [DELETED], archives)
    mock_thread.reset_mock()
    project_channel.reset_mock()
    mock_thread.delete.side_effect = discord.Forbidden(MagicMock(status=403), "Missing permissions")

    ok_fallback = await adapter.delete_workspace(task, actor_discord_id=1001)
    assert ok_fallback is True
    mock_thread.delete.assert_awaited_once()
    mock_thread.send.assert_awaited_once()
    assert "permanently deleted by <@1001>" in mock_thread.send.call_args.args[0]
    mock_thread.edit.assert_awaited_once_with(name="[DELETED] GEM-1", archived=True, locked=True)
    project_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_confirm_view_flow(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    auth_srv = AuthService(project_service=proj_srv)
    guild_id = 998877

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Launchpad",
        prefix="LP",
    )

    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Test typo task", creator_discord_id=1001, project_id=project.id
    )

    # Embed verification
    embed = build_task_delete_confirm_embed(t1, prerequisites=[], dependents=[])
    assert f"[{t1.short_id}]" in embed.title
    assert "⚠️" in embed.description

    workspace_mock = MagicMock()
    workspace_mock.delete_workspace = AsyncMock()

    view = TaskDeleteConfirmView(
        task=t1,
        author_id=1001,
        task_service=task_srv,
        auth_service=auth_srv,
        workspace=workspace_mock,
    )

    # Interaction check: another user cannot interact
    other_interaction = MagicMock(spec=discord.Interaction)
    other_interaction.user = _mock_member(9999)
    other_interaction.response = MagicMock()
    other_interaction.response.send_message = AsyncMock()
    allowed = await view.interaction_check(other_interaction)
    assert allowed is False

    # Invoker confirms deletion
    invoker_interaction = MagicMock(spec=discord.Interaction)
    invoker_interaction.user = _mock_member(1001)
    invoker_interaction.response = MagicMock()
    invoker_interaction.response.edit_message = AsyncMock()

    await view.confirm_delete.callback(invoker_interaction)

    invoker_interaction.response.edit_message.assert_awaited_once()
    edit_kwargs = invoker_interaction.response.edit_message.call_args.kwargs
    assert f"Task **[{t1.short_id}]**" in edit_kwargs["content"]
    assert "was permanently deleted" in edit_kwargs["content"]
    workspace_mock.delete_workspace.assert_awaited_once()
    assert await task_srv.get_by_id(t1.id) is None


@pytest.mark.asyncio
async def test_delete_confirm_view_cancel(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    auth_srv = AuthService(project_service=proj_srv)
    guild_id = 998877

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Cancel Project",
        prefix="CP",
    )

    task = await task_srv.create_task(
        guild_id=guild_id, title="Keep this task", creator_discord_id=1001, project_id=project.id
    )

    view = TaskDeleteConfirmView(
        task=task,
        author_id=1001,
        task_service=task_srv,
        auth_service=auth_srv,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = _mock_member(1001)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.cancel_delete.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    assert "cancelled" in interaction.response.edit_message.call_args.kwargs["content"]
    assert await task_srv.get_by_id(task.id) is not None


@pytest.mark.asyncio
async def test_pm_cog_task_delete_slash_command(services):
    from src.adapters.discord_bot.cogs.pm_cog import PmCog

    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    user_srv = services["user"]
    guild_id = 998877

    bot = MagicMock(spec=discord.Client)
    bot.workspace = MagicMock()

    cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        task_service=task_srv,
        squad_service=squad_srv,
        user_service=user_srv,
    )

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Cog Project",
        prefix="COG",
        lead_discord_id=1001,
    )

    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Cog task to delete",
        creator_discord_id=1001,
        project_id=project.id,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(id=guild_id)
    interaction.user = _mock_member(1001)
    interaction.channel = MagicMock()
    interaction.channel_id = 12345
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # Invoke /pm task delete
    await cog.task_delete.callback(cog, interaction=interaction, task=task.short_id)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.call_args.kwargs
    assert "embed" in kwargs
    assert "view" in kwargs
    assert isinstance(kwargs["view"], TaskDeleteConfirmView)
    assert f"[{task.short_id}]" in kwargs["embed"].title
