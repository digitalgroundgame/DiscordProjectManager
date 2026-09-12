from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.domain.enums import NotificationPreference, PriorityLevel


def test_project_create_command_parameters():
    """Verify that project-create command requires 'name', 'prefix', and 'role'."""
    cmd = PmCog.project_create
    params = {p.name: p for p in cmd.parameters}

    assert "name" in params
    assert params["name"].required is True

    assert "prefix" in params
    assert params["prefix"].required is True

    assert "role" in params
    assert params["role"].required is True

    # Optional parameters
    assert "channel" in params
    assert params["channel"].required is False

    assert "description" in params
    assert params["description"].required is False

    assert "category" in params
    assert params["category"].required is False


@pytest.mark.asyncio
async def test_project_create_execution(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    bot = MagicMock()

    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = 9999999999
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    mock_channel = MagicMock(spec=discord.ForumChannel)
    mock_channel.id = 123456789
    mock_channel.guild = interaction.guild
    mock_channel.available_tags = []
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 777111
    mock_role.name = "Platform Squad"
    mock_lead = MagicMock(spec=discord.Member)
    mock_lead.id = 888222

    await cog.project_create.callback(
        cog,
        interaction=interaction,
        name="Platform Core",
        prefix="PLC",
        role=mock_role,
        channel=mock_channel,
        description="Platform engineering",
        category="Engineering",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()

    # Check project persisted in db with bound channel id, role
    project = await proj_srv.get_by_name(9999999999, "Platform Core")
    assert project is not None
    assert project.prefix == "PLC"
    assert project.discord_channel_id == 123456789
    teams = await proj_srv.list_teams_for_project(project.id)
    assert any(t.discord_role_id == 777111 for t in teams)


@pytest.mark.asyncio
async def test_project_create_rejects_non_forum(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    bot = MagicMock()
    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = 9999999999
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    mock_text_channel = MagicMock(spec=discord.TextChannel)
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 777111

    await cog.project_create.callback(
        cog,
        interaction=interaction,
        name="Non Forum Project",
        prefix="NFP",
        role=mock_role,
        channel=mock_text_channel,
    )

    interaction.response.send_message.assert_awaited_once()
    assert "Forum Channel" in interaction.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_project_set_role_and_lead_commands(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    guild_id = 9999999999
    bot = MagicMock()

    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    project = await proj_srv.create_project(guild_id=guild_id, name="Mobile App", prefix="MOB")

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Map Role
    new_role = MagicMock(spec=discord.Role)
    new_role.id = 333444
    new_role.name = "Mobile Devs"
    await cog.project_role.callback(
        cog, interaction=interaction, project_name="Mobile App", role=new_role, action="add"
    )
    interaction.followup.send.assert_awaited_once()

    teams = await proj_srv.list_teams_for_project(project.id)
    assert any(t.discord_role_id == 333444 for t in teams)

    # 2. Designate Lead
    interaction.followup.send.reset_mock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.guild_permissions = discord.Permissions(administrator=True)
    new_lead = MagicMock(spec=discord.Member)
    new_lead.id = 555666
    new_lead.roles = [new_role]
    await cog.project_lead.callback(
        cog, interaction=interaction, project_name="Mobile App", user=new_lead, action="add"
    )
    interaction.followup.send.assert_awaited_once()

    team = teams[0]
    is_lead = await team_srv.is_team_lead(team.id, 555666)
    assert is_lead is True

    # 3. Remove Lead
    interaction.followup.send.reset_mock()
    await cog.project_lead.callback(
        cog, interaction=interaction, project_name="Mobile App", user=new_lead, action="remove"
    )
    interaction.followup.send.assert_awaited_once()

    is_lead_after = await team_srv.is_team_lead(team.id, 555666)
    assert is_lead_after is False


@pytest.mark.asyncio
async def test_project_and_team_autocomplete(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    guild_id = 8888888888
    bot = MagicMock()

    cog = PmCog(bot=bot, project_service=proj_srv, team_service=team_srv)

    # Seed projects
    await proj_srv.create_project(guild_id=guild_id, name="Frontend UI", prefix="FUI")
    await proj_srv.create_project(guild_id=guild_id, name="Backend API", prefix="BAPI")
    p3 = await proj_srv.create_project(guild_id=guild_id, name="Legacy System", prefix="LEG")
    await proj_srv.archive_project(p3.id)

    # Seed team
    await team_srv.create_team(guild_id=guild_id, name="Core Infra", discord_role_id=111222333)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id

    # 1. Project autocomplete for active projects
    choices_all = await cog.project_autocomplete(interaction, current="")
    assert len(choices_all) == 2  # Only active projects
    choice_names = [c.name for c in choices_all]
    assert "Frontend UI (FUI)" in choice_names
    assert "Backend API (BAPI)" in choice_names

    # 2. Filtered project autocomplete
    choices_filtered = await cog.project_autocomplete(interaction, current="front")
    assert len(choices_filtered) == 1
    assert choices_filtered[0].value == "Frontend UI"

    # 3. Team autocomplete
    team_choices = await cog.team_autocomplete(interaction, current="infra")
    assert len(team_choices) == 1
    assert team_choices[0].value == "Core Infra"


@pytest.mark.asyncio
async def test_task_action_view_and_modals(services):
    from src.adapters.discord_bot.views.task_buttons import TaskActionView
    from src.adapters.discord_bot.views.task_modals import TaskEditModal
    from src.domain.enums import PriorityLevel

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 7777777777

    project = await proj_srv.create_project(guild_id=guild_id, name="Security Operations", prefix="SEC")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Firewall Rules Audit",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.NORMAL,
    )

    # 1. Test TaskActionView component structure
    view = TaskActionView(
        task_id=task.id,
        current_status=task.status,
        current_priority=task.priority,
        task_service=task_srv,
    )

    # Check children: action buttons, note, edit, deps, controls (no inline dropdown clutter)
    custom_ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    assert f"task:start:{task.id}" in custom_ids
    assert f"task:complete:{task.id}" in custom_ids
    assert f"task:note:{task.id}" in custom_ids
    assert f"task:edit:{task.id}" in custom_ids
    assert f"task:deps:{task.id}" in custom_ids
    assert f"task:controls:{task.id}" in custom_ids
    assert f"task:priority:{task.id}" not in custom_ids
    assert f"task:assignee:{task.id}" not in custom_ids
    assert f"task:due:{task.id}" not in custom_ids
    assert f"task:watchers:{task.id}" not in custom_ids

    # Check button layout and styles
    assert view.edit_btn.row == 1
    assert view.note_btn.style == discord.ButtonStyle.primary

    # Check TaskQuickControlsView contains the on-demand dropdowns with default_values
    from src.adapters.discord_bot.views.task_buttons import TaskQuickControlsView

    populated_task = task.model_copy(update={"assignee_discord_id": 7788, "watchers": [9901, 9902]})
    controls_view = TaskQuickControlsView(
        task=populated_task,
        task_service=task_srv,
    )
    assert [v.id for v in controls_view.assignee_select.default_values] == [7788]
    assert [v.id for v in controls_view.watchers_select.default_values] == [9901, 9902]
    assert controls_view.priority_select is not None
    assert controls_view.due_select is not None

    # Completed view has Reopen button
    from src.domain.enums import TaskStatus

    completed_view = TaskActionView(
        task_id=task.id,
        current_status=TaskStatus.COMPLETED,
        current_priority=task.priority,
        task_service=task_srv,
    )
    completed_ids = [item.custom_id for item in completed_view.children if hasattr(item, "custom_id")]
    assert f"task:reopen:{task.id}" in completed_ids
    assert f"task:start:{task.id}" not in completed_ids

    # In-progress view exposes a "Convert to Not Started" button instead of "In Progress"
    in_progress_view = TaskActionView(
        task_id=task.id,
        current_status=TaskStatus.IN_PROGRESS,
        current_priority=task.priority,
        task_service=task_srv,
    )
    in_progress_ids = [item.custom_id for item in in_progress_view.children if hasattr(item, "custom_id")]
    assert f"task:notstarted:{task.id}" in in_progress_ids
    assert f"task:start:{task.id}" not in in_progress_ids
    assert f"task:complete:{task.id}" in in_progress_ids
    assert in_progress_view.notstarted_btn.label == "Convert to Not Started"
    assert in_progress_view.notstarted_btn.style == discord.ButtonStyle.danger

    # 2. Test unassign button via dynamic dispatcher
    from src.adapters.discord_bot.bot import DggPmBot

    bot = DggPmBot(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=services["team"],
    )

    unassign_interaction = MagicMock(spec=discord.Interaction)
    unassign_interaction.guild_id = guild_id
    unassign_interaction.user = MagicMock()
    unassign_interaction.user.id = 1002
    unassign_interaction.response = MagicMock()
    unassign_interaction.response.is_done.return_value = False
    unassign_interaction.response.edit_message = AsyncMock()

    await bot._handle_dynamic_task_button(unassign_interaction, "unassign", task.id)
    unassign_interaction.response.edit_message.assert_awaited_once()

    unassigned_task = await task_srv.get_by_id(task.id)
    assert unassigned_task.assignee_discord_id is None

    # 3. Test priority select via dynamic dispatcher
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild_id = guild_id
    mock_interaction.user = MagicMock()
    mock_interaction.user.id = 1002
    mock_interaction.data = {"values": ["high"]}
    mock_interaction.response = MagicMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.response.edit_message = AsyncMock()

    await bot._handle_dynamic_task_button(mock_interaction, "priority", task.id)
    mock_interaction.response.edit_message.assert_awaited_once()

    refreshed = await task_srv.get_by_id(task.id)
    assert refreshed.priority == PriorityLevel.HIGH

    # 4. Test due date preset select via dynamic dispatcher
    due_interaction = MagicMock(spec=discord.Interaction)
    due_interaction.guild_id = guild_id
    due_interaction.user = MagicMock()
    due_interaction.user.id = 1002
    due_interaction.data = {"values": ["3days"]}
    due_interaction.response = MagicMock()
    due_interaction.response.is_done.return_value = False
    due_interaction.response.edit_message = AsyncMock()

    await bot._handle_dynamic_task_button(due_interaction, "due", task.id)
    due_interaction.response.edit_message.assert_awaited_once()

    due_refreshed = await task_srv.get_by_id(task.id)
    assert due_refreshed.due_at is not None

    # 5. Test watchers multi-select via dynamic dispatcher
    watchers_interaction = MagicMock(spec=discord.Interaction)
    watchers_interaction.guild_id = guild_id
    watchers_interaction.user = MagicMock()
    watchers_interaction.user.id = 1002
    watchers_interaction.data = {"values": ["2001", "2002"]}
    watchers_interaction.response = MagicMock()
    watchers_interaction.response.is_done.return_value = False
    watchers_interaction.response.edit_message = AsyncMock()

    await bot._handle_dynamic_task_button(watchers_interaction, "watchers", task.id)
    watchers_interaction.response.edit_message.assert_awaited_once()

    watchers_refreshed = await task_srv.get_by_id(task.id)
    assert 2001 in watchers_refreshed.watchers
    assert 2002 in watchers_refreshed.watchers

    # 6. Test "Back to Not Started" status button via dynamic dispatcher
    await task_srv.update_status(
        task_id=task.id,
        new_status=TaskStatus.IN_PROGRESS,
        expected_version=watchers_refreshed.version,
        actor_discord_id=1002,
    )
    notstarted_interaction = MagicMock(spec=discord.Interaction)
    notstarted_interaction.guild_id = guild_id
    notstarted_interaction.user = MagicMock()
    notstarted_interaction.user.id = 1002
    notstarted_interaction.response = MagicMock()
    notstarted_interaction.response.is_done.return_value = False
    notstarted_interaction.response.edit_message = AsyncMock()

    await bot._handle_dynamic_task_button(notstarted_interaction, "notstarted", task.id)
    notstarted_interaction.response.edit_message.assert_awaited_once()

    reverted = await task_srv.get_by_id(task.id)
    assert reverted.status == TaskStatus.NOT_STARTED

    # 4. Test TaskEditModal with natural language date
    edit_modal = TaskEditModal(task=due_refreshed, task_service=task_srv)
    edit_modal.title_input._value = "Firewall & WAF Rules Audit"
    edit_modal.body_input._value = "Comprehensive review of all WAF rules"
    edit_modal.due_input._value = "in 2 weeks"
    edit_modal.cc_input._value = "<@1001> <@1003>"

    modal_interaction = MagicMock(spec=discord.Interaction)
    modal_interaction.user = MagicMock()
    modal_interaction.user.id = 1001
    modal_interaction.message = MagicMock()
    modal_interaction.message.edit = AsyncMock()
    modal_interaction.response = MagicMock()
    modal_interaction.response.send_message = AsyncMock()

    await edit_modal.on_submit(modal_interaction)

    updated = await task_srv.get_by_id(task.id)
    assert updated.title == "Firewall & WAF Rules Audit"
    assert updated.body == "Comprehensive review of all WAF rules"
    assert updated.due_at is not None
    assert set(updated.watchers) == {1001, 1003}


@pytest.mark.asyncio
async def test_dynamic_task_button_rejects_cross_guild(services):
    """Buttons must not act on tasks that don't belong to the interaction's guild."""
    from src.adapters.discord_bot.bot import DggPmBot

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 1000000001

    project = await proj_srv.create_project(guild_id=guild_id, name="Trusted Server", prefix="TRU")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Secret Task",
        creator_discord_id=1001,
        project_id=project.id,
    )

    bot = DggPmBot(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=services["team"],
    )

    # Interaction from a DIFFERENT guild attempting to act on the task
    foreign_interaction = MagicMock(spec=discord.Interaction)
    foreign_interaction.guild_id = 8888888888
    foreign_interaction.user = MagicMock()
    foreign_interaction.user.id = 9999
    foreign_interaction.response = MagicMock()
    foreign_interaction.response.is_done.return_value = False
    foreign_interaction.response.send_message = AsyncMock()

    await bot._handle_dynamic_task_button(foreign_interaction, "note", task.id)

    foreign_interaction.response.send_message.assert_awaited_once()
    error_msg = foreign_interaction.response.send_message.await_args.args[0]
    assert "does not belong to this server" in error_msg

    # Task unchanged
    refreshed = await task_srv.get_by_id(task.id)
    assert refreshed.title == "Secret Task"


@pytest.mark.asyncio
async def test_thread_workspace_content_leads_with_description(services):
    """The thread workspace message should lead with the task description for clarity."""
    from src.adapters.discord_bot.views.task_embed import build_thread_workspace_content
    from src.domain.enums import PriorityLevel

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 1000000003

    project = await proj_srv.create_project(guild_id=guild_id, name="Workspace Project", prefix="WP")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Workspace Clarity",
        creator_discord_id=1001,
        assignee_discord_id=2001,
        priority=PriorityLevel.LOW,
        body="Deploy the new API gateway and wire up monitoring dashboards.",
        project_id=project.id,
    )

    content = build_thread_workspace_content(task)
    lines = content.splitlines()
    # First line is the description; assignee/priority summary follows on a separate line
    assert lines[0] == "Deploy the new API gateway and wire up monitoring dashboards."
    assert "**Assignee**: <@2001>" in content
    assert "**Priority**: `Low`" in content

    # Watchers are included when present
    task.watchers = [3001, 3002]
    content_with_watchers = build_thread_workspace_content(task)
    assert "**Watchers**: <@3001> <@3002>" in content_with_watchers

    # Long descriptions are truncated safely within Discord's 2000-char limit
    task.body = "X" * 2500
    truncated = build_thread_workspace_content(task)
    assert len(truncated) <= 2000
    assert truncated.splitlines()[0].endswith("...")

    # Unassigned + no description fallbacks
    task.assignee_discord_id = None
    task.body = None
    task.watchers = []
    fallback = build_thread_workspace_content(task)
    assert fallback.startswith("*No additional description provided.*")
    assert "**Assignee**: *Unassigned*" in fallback
    assert "**Priority**: `Low`" in fallback


@pytest.mark.asyncio
async def test_dynamic_task_button_same_guild_note_modal(services):
    """A note button from the correct guild should open the note modal."""
    from src.adapters.discord_bot.bot import DggPmBot

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 1000000002

    project = await proj_srv.create_project(guild_id=guild_id, name="Local Server", prefix="LOC")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Local Task",
        creator_discord_id=1001,
        project_id=project.id,
    )

    bot = DggPmBot(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=services["team"],
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1002
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_modal = AsyncMock()

    await bot._handle_dynamic_task_button(interaction, "note", task.id)

    interaction.response.send_modal.assert_awaited_once()
    interaction.response.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_settings_cog_my_settings(services):
    user_srv = services["user"]
    bot = MagicMock()
    cog = PmCog(
        bot=bot,
        user_service=user_srv,
        project_service=services["project"],
        team_service=services["team"],
        task_service=services["task"],
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = 123456789
    interaction.user = MagicMock()
    interaction.user.id = 987654321
    interaction.user.display_name = "Charlie"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Run /pm settings without argument
    await cog.settings.callback(cog, interaction=interaction, notify_preference=None)
    interaction.followup.send.assert_awaited_once()

    # 2. Run /pm settings with notify choice
    interaction.followup.send.reset_mock()
    await cog.settings.callback(cog, interaction=interaction, notify_preference="both")
    interaction.followup.send.assert_awaited_once()

    pref = await user_srv.get_preference(123456789, 987654321)
    assert pref == NotificationPreference.BOTH


def test_seed_script_production_safety_guards(monkeypatch):
    """Verify check_production_safety_guard blocks execution in unsafe environments."""
    from scripts.seed_dev_data import check_production_safety_guard
    from src.config import settings

    # 1. Blocks if ENVIRONMENT is production
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="cannot be run in production"):
        check_production_safety_guard(1543430283250901023)

    # 2. Blocks if DISCORD_GUILD_ID is not configured
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DISCORD_GUILD_ID", None)
    with pytest.raises(RuntimeError, match="requires DISCORD_GUILD_ID"):
        check_production_safety_guard(1543430283250901023)

    # 3. Blocks if target guild does not match dev guild
    monkeypatch.setattr(settings, "DISCORD_GUILD_ID", 1543430283250901023)
    with pytest.raises(RuntimeError, match="does not match configured dev guild"):
        check_production_safety_guard(999999999999999999)

    # 4. Blocks if DATABASE_URL points to a production cloud database
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:pass@production-db.rds.amazonaws.com:5432/dgg_pm",
    )
    with pytest.raises(RuntimeError, match="appears to point to a production/cloud database"):
        check_production_safety_guard(1543430283250901023)


def test_clear_db_script_safety_guards(monkeypatch):
    """Verify clear_db script safety guards block execution in unsafe environments."""
    from scripts.clear_db import check_production_safety_guard as clear_safety_guard
    from src.config import settings

    # 1. Blocks if ENVIRONMENT is production
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="cannot be run in 'production' environment"):
        clear_safety_guard()

    # 2. Blocks if DATABASE_URL points to a cloud database
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:pass@db.supabase.co:5432/postgres",
    )
    with pytest.raises(RuntimeError, match="appears to point to a production/cloud database"):
        clear_safety_guard()


@pytest.mark.asyncio
async def test_pm_project_create_with_required_role(services):
    """Verify /pm project create requires role, creates team, and assigns squad to project."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 1122334455

    pm_cog = PmCog(
        bot=MagicMock(),
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
    )

    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 55667788
    mock_role.name = "Mobile Engineers"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await pm_cog.project_create.callback(
        pm_cog,
        interaction=interaction,
        name="Mobile Redesign",
        prefix="MOB",
        role=mock_role,
        channel=None,
    )

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Mobile Redesign" in embed.title

    # Verify project exists and team is mapped
    project = await proj_srv.get_by_name(guild_id, "Mobile Redesign")
    assert project is not None
    assert project.prefix == "MOB"

    teams = await proj_srv.list_teams_for_project(project.id)
    assert len(teams) == 1
    assert teams[0].discord_role_id == 55667788


@pytest.mark.asyncio
async def test_pm_project_role_and_lead_commands(services):
    """Verify /pm project role (add/remove) and /pm project lead (add/remove)."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 9988776655

    pm_cog = PmCog(
        bot=MagicMock(),
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
    )

    # 1. Seed project with primary role
    p = await proj_srv.create_project(guild_id=guild_id, name="Cloud Backend", prefix="CLD")
    t1 = await team_srv.create_team(guild_id=guild_id, name="Backend", discord_role_id=1001)
    await proj_srv.assign_team_to_project(p.id, t1.id)

    # 2. Add second role via /pm project role add
    mock_role_qa = MagicMock(spec=discord.Role)
    mock_role_qa.id = 1002
    mock_role_qa.name = "QA Engineers"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.guild_permissions = MagicMock(manage_guild=True, administrator=True)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await pm_cog.project_role.callback(
        pm_cog,
        interaction=interaction,
        project_name="Cloud Backend",
        role=mock_role_qa,
        action="add",
    )

    teams = await proj_srv.list_teams_for_project(p.id)
    assert len(teams) == 2
    role_ids = {t.discord_role_id for t in teams}
    assert 1001 in role_ids
    assert 1002 in role_ids

    # 3. Designate lead via /pm project lead add
    mock_user_lead = MagicMock(spec=discord.Member)
    mock_user_lead.id = 5050
    mock_user_lead.roles = [mock_role_qa]

    await pm_cog.project_lead.callback(
        pm_cog,
        interaction=interaction,
        project_name="Cloud Backend",
        user=mock_user_lead,
        action="add",
    )

    t2 = next(t for t in teams if t.discord_role_id == 1002)
    leads = await team_srv.list_team_leads(t2.id)
    assert 5050 in leads

    # 4. Remove role via /pm project role remove
    await pm_cog.project_role.callback(
        pm_cog,
        interaction=interaction,
        project_name="Cloud Backend",
        role=mock_role_qa,
        action="remove",
    )
    teams_after = await proj_srv.list_teams_for_project(p.id)
    assert len(teams_after) == 1
    assert teams_after[0].discord_role_id == 1001


@pytest.mark.asyncio
async def test_pm_cog_menu_and_notifications(services):
    from src.adapters.discord_bot.views.admin_menu import PmDashboardView

    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    bot = MagicMock()
    guild_id = 9999888877

    pm_cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        task_service=task_srv,
        team_service=team_srv,
        user_service=user_srv,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.user.display_name = "Alice"
    interaction.user.guild_permissions = MagicMock(manage_guild=True, administrator=True)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Test /pm menu
    await pm_cog.menu.callback(pm_cog, interaction=interaction)
    interaction.response.send_message.assert_awaited_once()
    menu_embed = interaction.response.send_message.call_args.kwargs["embed"]
    menu_view = interaction.response.send_message.call_args.kwargs["view"]
    assert "Project Management Control Center" in menu_embed.title
    assert isinstance(menu_view, PmDashboardView)

    # 2. Test /pm notifications with value
    await pm_cog.notifications.callback(pm_cog, interaction=interaction, notify_preference="channel")
    interaction.followup.send.assert_awaited_once()
    assert "CHANNEL" in interaction.followup.send.call_args.args[0]

    pref = await user_srv.get_preference(guild_id, 1001)
    assert pref == NotificationPreference.CHANNEL

    # 3. Test /pm notification alias without value opens settings view
    interaction.followup.send.reset_mock()
    await pm_cog.notification.callback(pm_cog, interaction=interaction, notify_preference=None)
    interaction.followup.send.assert_awaited_once()
    settings_embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Notification Preferences" in settings_embed.title


@pytest.mark.asyncio
async def test_task_quick_controls_view_callbacks(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 9988771122

    project = await proj_srv.create_project(guild_id=guild_id, name="Controls Project", prefix="CTRL")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Controls Test Task",
        creator_discord_id=1001,
        project_id=project.id,
        priority=PriorityLevel.LOW,
    )

    from src.adapters.discord_bot.views.task_buttons import TaskQuickControlsView, build_task_controls_embed

    ctrl_embed = build_task_controls_embed(task)
    assert "Quick Controls" in ctrl_embed.title
    assert "Priority**: Low" in ctrl_embed.description

    mock_bot = MagicMock()
    mock_bot.sync_root_task_message = AsyncMock()
    mock_bot.sync_task_thread = AsyncMock()

    controls_view = TaskQuickControlsView(
        task=task,
        task_service=task_srv,
        bot=mock_bot,
    )

    # Check min_values allowing clearing
    assert controls_view.assignee_select.min_values == 0
    assert controls_view.watchers_select.min_values == 0

    # 1. Priority change
    controls_view.priority_select._values = ["high"]
    prio_interaction = MagicMock(spec=discord.Interaction)
    prio_interaction.user = MagicMock(id=1001)
    prio_interaction.response = MagicMock()
    prio_interaction.response.edit_message = AsyncMock()

    await controls_view._on_priority_selected(prio_interaction)
    prio_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.priority == PriorityLevel.HIGH
    mock_bot.sync_root_task_message.assert_awaited_once()

    # 2. Assignee change
    mock_member = MagicMock(spec=discord.Member)
    mock_member.id = 4001
    controls_view.assignee_select._values = [mock_member]
    assign_interaction = MagicMock(spec=discord.Interaction)
    assign_interaction.guild = MagicMock(id=guild_id)
    assign_interaction.user = MagicMock(id=1001)
    assign_interaction.response = MagicMock()
    assign_interaction.response.edit_message = AsyncMock()

    await controls_view._on_assignee_selected(assign_interaction)
    assign_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.assignee_discord_id == 4001

    # 2b. Clear Assignee directly via dropdown deselect
    controls_view.assignee_select._values = []
    clear_assign_interaction = MagicMock(spec=discord.Interaction)
    clear_assign_interaction.guild = MagicMock(id=guild_id)
    clear_assign_interaction.user = MagicMock(id=1001)
    clear_assign_interaction.response = MagicMock()
    clear_assign_interaction.response.edit_message = AsyncMock()

    await controls_view._on_assignee_selected(clear_assign_interaction)
    clear_assign_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.assignee_discord_id is None

    # 3. Unassign button
    controls_view.task.assignee_discord_id = 4001
    unassign_interaction = MagicMock(spec=discord.Interaction)
    unassign_interaction.user = MagicMock(id=1001)
    unassign_interaction.response = MagicMock()
    unassign_interaction.response.edit_message = AsyncMock()

    await controls_view._on_unassign_clicked(unassign_interaction)
    unassign_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.assignee_discord_id is None

    # 4. Due date
    controls_view.due_select._values = ["tomorrow"]
    due_interaction = MagicMock(spec=discord.Interaction)
    due_interaction.user = MagicMock(id=1001)
    due_interaction.response = MagicMock()
    due_interaction.response.edit_message = AsyncMock()

    await controls_view._on_due_selected(due_interaction)
    due_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.due_at is not None

    # 5. Watchers
    mock_w = MagicMock(spec=discord.Member)
    mock_w.id = 5001
    controls_view.watchers_select._values = [mock_w]
    watchers_interaction = MagicMock(spec=discord.Interaction)
    watchers_interaction.user = MagicMock(id=1001)
    watchers_interaction.response = MagicMock()
    watchers_interaction.response.edit_message = AsyncMock()

    await controls_view._on_watchers_selected(watchers_interaction)
    watchers_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.watchers == [5001]

    # 5b. Remove watchers directly via dropdown deselect
    controls_view.watchers_select._values = []
    clear_watchers_interaction = MagicMock(spec=discord.Interaction)
    clear_watchers_interaction.user = MagicMock(id=1001)
    clear_watchers_interaction.response = MagicMock()
    clear_watchers_interaction.response.edit_message = AsyncMock()

    await controls_view._on_watchers_selected(clear_watchers_interaction)
    clear_watchers_interaction.response.edit_message.assert_awaited_once()
    assert controls_view.task.watchers == []

    # 6. Done button
    done_interaction = MagicMock(spec=discord.Interaction)
    done_interaction.response = MagicMock()
    done_interaction.response.edit_message = AsyncMock()

    await controls_view._on_done_clicked(done_interaction)
    done_interaction.response.edit_message.assert_awaited_once()
    done_embed = done_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Updated" in done_embed.title


@pytest.mark.asyncio
async def test_pm_menu_command_and_bot_wiring(services):
    """Verify DggPmBot setup_hook wires TaskService to PmCog and /pm menu runs without error."""
    from unittest.mock import patch

    from src.adapters.discord_bot.bot import DggPmBot
    from src.services.auth_service import AuthService
    from src.services.task_service import TaskService

    bot = DggPmBot(
        project_service=services["project"],
        team_service=services["team"],
        task_service=services["task"],
        user_service=services["user"],
        squad_service=services.get("squad", services["team"]),
    )

    with patch.object(bot.tree, "sync", new_callable=AsyncMock):
        await bot.setup_hook()

    pm_cog = bot.get_cog("PmCog")
    assert pm_cog is not None
    assert isinstance(pm_cog.task_service, TaskService)
    assert isinstance(pm_cog.auth_service, AuthService)
    assert pm_cog.task_service == services["task"]

    # Now execute /pm menu
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = 12345
    interaction.guild.name = "Test Guild"
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 99999
    interaction.user.guild_permissions = discord.Permissions(administrator=True)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await pm_cog.menu.callback(pm_cog, interaction)
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert "embed" in kwargs
    assert "view" in kwargs


def test_project_rebuild_command_parameters():
    """Verify that project rebuild command requires 'project_name' and accepts optional 'forum'."""
    cmd = PmCog.project_rebuild
    params = {p.name: p for p in cmd.parameters}

    assert "project_name" in params
    assert params["project_name"].required is True

    assert "forum" in params
    assert params["forum"].required is False


@pytest.mark.asyncio
async def test_project_rebuild_execution_shows_confirmation(services):
    """Verifies that invoking /pm project rebuild displays an ephemeral confirmation view."""
    proj_srv = services["project"]
    guild_id = 9999999999
    bot = MagicMock()

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Security App",
        prefix="SEC",
        discord_channel_id=12345,
    )

    mock_project_workspace = MagicMock()
    cog = PmCog(bot=bot, project_service=proj_srv, project_workspace=mock_project_workspace)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(id=guild_id)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.guild_permissions = discord.Permissions(manage_guild=True)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.project_rebuild.callback(
        cog,
        interaction=interaction,
        project_name=project.name,
    )

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert "view" in kwargs
    assert "embed" in kwargs

    # Now test clicking Confirm
    confirm_view = kwargs["view"]
    btn_interaction = MagicMock(spec=discord.Interaction)
    btn_interaction.guild = interaction.guild
    btn_interaction.response = MagicMock()
    btn_interaction.response.edit_message = AsyncMock()
    btn_interaction.edit_original_response = AsyncMock()

    from src.adapters.discord_bot.workspace_protocol import RebuildWorkspaceResult

    mock_project_workspace.rebuild_workspace = AsyncMock(
        return_value=RebuildWorkspaceResult(
            project=project,
            channel_id=12345,
            forum_created=False,
            tags_created=6,
            hub_rebuilt=True,
            tasks_reconciled=3,
            tasks_recreated=1,
            tasks_archived=1,
            warnings=(),
        )
    )

    await confirm_view.confirm.callback(btn_interaction)

    mock_project_workspace.rebuild_workspace.assert_awaited_once()
    btn_interaction.edit_original_response.assert_awaited()
    final_kwargs = btn_interaction.edit_original_response.call_args.kwargs
    assert "embed" in final_kwargs
    assert "Workspace Rebuilt" in final_kwargs["embed"].title


@pytest.mark.asyncio
async def test_project_rebuild_cancel_button_cancels(services):
    """Verifies that clicking Cancel aborts the rebuild."""
    proj_srv = services["project"]
    guild_id = 9999999999
    bot = MagicMock()

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Security App 2",
        prefix="SEC2",
        discord_channel_id=12345,
    )

    mock_project_workspace = MagicMock()
    cog = PmCog(bot=bot, project_service=proj_srv, project_workspace=mock_project_workspace)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(id=guild_id)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.guild_permissions = discord.Permissions(manage_guild=True)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.project_rebuild.callback(
        cog,
        interaction=interaction,
        project_name=project.name,
    )

    kwargs = interaction.response.send_message.call_args.kwargs
    confirm_view = kwargs["view"]

    btn_interaction = MagicMock(spec=discord.Interaction)
    btn_interaction.response = MagicMock()
    btn_interaction.response.edit_message = AsyncMock()

    await confirm_view.cancel.callback(btn_interaction)

    mock_project_workspace.rebuild_workspace.assert_not_called()
    btn_interaction.response.edit_message.assert_awaited_once()
    cancel_kwargs = btn_interaction.response.edit_message.call_args.kwargs
    assert "cancelled" in cancel_kwargs["content"].lower()
