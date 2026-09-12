from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.adapters.discord_bot.views.hub_menu import (
    HubBoardProjectSelectView,
    HubTaskProjectSelectView,
    PmHubView,
    build_hub_welcome_embed,
)
from src.adapters.discord_bot.views.project_menu import (
    ProjectActiveListView,
    ProjectArchiveConfirmView,
    ProjectArchiveSelectView,
    ProjectChannelSelectView,
    ProjectCreateDraftView,
    ProjectCreateModal,
    ProjectMenuView,
    ProjectRestoreConfirmView,
    ProjectRestoreSelectView,
    ProjectSearchModal,
    build_active_projects_embed,
    build_project_draft_embed,
)
from src.adapters.discord_bot.views.settings_menu import (
    UserSettingsView,
)
from src.adapters.discord_bot.views.squad_menu import (
    SquadCreateModalWithName,
    SquadCreateRoleSelectView,
    SquadMemberAssignView,
    SquadMenuView,
    SquadRosterDetailView,
    build_squad_menu_embed,
)
from src.adapters.discord_bot.views.task_menu import (
    TaskCreateModal,
    TaskMenuView,
    TaskProjectSearchModal,
    TaskSelectProjectView,
    build_task_board_embed,
    build_task_menu_embed,
)
from src.domain.enums import PriorityLevel, TaskStatus


@pytest.mark.asyncio
async def test_project_menu_and_modal(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    guild_id = 9999999999

    # 1. Test ProjectMenuView and embed
    view = ProjectMenuView(proj_srv, team_srv, task_service=services["task"])
    # 9 buttons: New Project, Active Projects, Set Squad Role, Set Lead,
    # Archive, Restore, Rebuild Workspace, Tech Tree, Hub
    assert len(view.children) == 9

    # Test clicking New Project opens ProjectChannelSelectView
    new_proj_interaction = MagicMock(spec=discord.Interaction)
    new_proj_interaction.guild = MagicMock()
    new_proj_interaction.response = MagicMock()
    new_proj_interaction.response.edit_message = AsyncMock()
    await view.new_project_btn.callback(new_proj_interaction)
    new_proj_interaction.response.edit_message.assert_awaited_once()
    called_view = new_proj_interaction.response.edit_message.call_args.kwargs.get("view")
    assert isinstance(called_view, ProjectChannelSelectView)

    # 2. Test ProjectChannelSelectView callbacks
    chan_select_view = ProjectChannelSelectView(proj_srv, team_srv, services["task"])
    mock_forum_channel = MagicMock(spec=discord.ForumChannel)
    mock_forum_channel.id = 555666777

    # Select channel from dropdown
    chan_select_view.channel_select._values = [mock_forum_channel]
    select_interaction = MagicMock(spec=discord.Interaction)
    select_interaction.guild = MagicMock()
    select_interaction.guild.get_channel = MagicMock(return_value=mock_forum_channel)
    select_interaction.response = MagicMock()
    select_interaction.response.send_modal = AsyncMock()
    await chan_select_view._on_channel_selected(select_interaction)
    select_interaction.response.send_modal.assert_awaited_once()
    sent_modal = select_interaction.response.send_modal.call_args.args[0]
    assert isinstance(sent_modal, ProjectCreateModal)
    assert sent_modal.channel == mock_forum_channel

    # 3. Test ProjectCreateModal submission -> launches ProjectCreateDraftView
    mock_channel = MagicMock(spec=discord.ForumChannel)
    mock_channel.id = 123456789

    modal = ProjectCreateModal(project_service=proj_srv, channel=mock_channel)
    modal.name_input._value = "Cloud Infrastructure"
    modal.prefix_input._value = "CLD"
    modal.desc_input._value = "AWS & Kubernetes migration"
    modal.cat_input._value = "DevOps"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    draft_view = interaction.response.send_message.call_args.kwargs.get("view")
    assert isinstance(draft_view, ProjectCreateDraftView)
    assert draft_view.name == "Cloud Infrastructure"
    assert draft_view.prefix == "CLD"
    assert draft_view.role is None
    assert draft_view.lead is None

    # 3a. Select Role and Lead, test Clear Squad Role and Clear Lead buttons
    mock_squad_role = MagicMock(spec=discord.Role)
    mock_squad_role.id = 998877
    draft_view.role_select._values = [mock_squad_role]

    role_select_inter = MagicMock(spec=discord.Interaction)
    role_select_inter.response = MagicMock()
    role_select_inter.response.edit_message = AsyncMock()
    await draft_view._on_role_selected(role_select_inter)
    assert draft_view.role == mock_squad_role

    # Test Clear Squad Role button
    clear_role_inter = MagicMock(spec=discord.Interaction)
    clear_role_inter.response = MagicMock()
    clear_role_inter.response.edit_message = AsyncMock()
    await draft_view._on_clear_role_clicked(clear_role_inter)
    assert draft_view.role is None

    # Re-assign squad role
    draft_view.role = mock_squad_role

    mock_lead_member = MagicMock(spec=discord.Member)
    mock_lead_member.id = 112233
    draft_view.lead_select._values = [mock_lead_member]

    lead_select_inter = MagicMock(spec=discord.Interaction)
    lead_select_inter.response = MagicMock()
    lead_select_inter.response.edit_message = AsyncMock()
    await draft_view._on_lead_selected(lead_select_inter)
    assert draft_view.lead == mock_lead_member

    # Test Clear Lead button
    clear_lead_inter = MagicMock(spec=discord.Interaction)
    clear_lead_inter.response = MagicMock()
    clear_lead_inter.response.edit_message = AsyncMock()
    await draft_view._on_clear_lead_clicked(clear_lead_inter)
    assert draft_view.lead is None

    # Re-assign lead and confirm project creation
    draft_view.lead = mock_lead_member
    confirm_interaction = MagicMock(spec=discord.Interaction)
    confirm_interaction.guild = MagicMock()
    confirm_interaction.guild.id = guild_id
    confirm_interaction.response = MagicMock()
    confirm_interaction.response.edit_message = AsyncMock()

    await draft_view._on_confirm_clicked(confirm_interaction)
    confirm_interaction.response.edit_message.assert_awaited_once()

    created_proj = await proj_srv.get_by_name(guild_id, "Cloud Infrastructure")
    assert created_proj is not None
    assert created_proj.prefix == "CLD"
    assert created_proj.discord_channel_id == 123456789
    assert created_proj.discord_role_id == 998877
    assert created_proj.lead_discord_id == 112233

    # 3c. ProjectCreateDraftView creates the Control Hub + project tag on a ForumChannel
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.id = 37373737
    mock_forum.name = "mobile-dev"
    mock_forum.available_tags = []
    mock_forum.edit = AsyncMock()
    mock_thread = MagicMock()
    mock_thread.id = 42424242
    mock_thread.edit = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    modal2 = ProjectCreateModal(
        project_service=proj_srv,
        channel=mock_forum,
        team_service=team_srv,
        task_service=services["task"],
    )
    modal2.name_input._value = "Mobile Redesign"
    modal2.prefix_input._value = "MOB"
    modal2.desc_input._value = ""
    modal2.cat_input._value = ""

    interaction2 = MagicMock(spec=discord.Interaction)
    interaction2.guild = MagicMock()
    interaction2.guild.id = guild_id
    interaction2.response = MagicMock()
    interaction2.response.send_message = AsyncMock()

    await modal2.on_submit(interaction2)
    draft_view2 = interaction2.response.send_message.call_args.kwargs.get("view")
    assert isinstance(draft_view2, ProjectCreateDraftView)

    mock_forum_role = MagicMock(spec=discord.Role)
    mock_forum_role.id = 887766
    draft_view2.role = mock_forum_role

    confirm_inter2 = MagicMock(spec=discord.Interaction)
    confirm_inter2.guild = MagicMock()
    confirm_inter2.guild.id = guild_id
    confirm_inter2.response = MagicMock()
    confirm_inter2.response.edit_message = AsyncMock()

    await draft_view2._on_confirm_clicked(confirm_inter2)

    # Hub thread created in the forum
    mock_forum.create_thread.assert_awaited_once()
    assert "Control Hub" in mock_forum.create_thread.call_args.kwargs["name"]
    mock_thread.edit.assert_awaited_once_with(pinned=True)

    # Per-project tag added to the forum
    saved = [
        c.kwargs.get("available_tags", []) for c in mock_forum.edit.await_args_list if "available_tags" in c.kwargs
    ]
    assert any(t.name == "Mobile Redesign" for tags in saved for t in tags)

    # Confirm note surfaced in the embed
    embed2 = confirm_inter2.response.edit_message.call_args.kwargs.get("embed")
    bound_field = next(f for f in embed2.fields if f.name == "Bound Channel")
    assert "Pinned Control Hub" in bound_field.value

    # 3d. ProjectCreateDraftView creates a public project when role is None (or cleared)
    mock_channel3 = MagicMock(spec=discord.TextChannel)
    mock_channel3.id = 55443322
    draft_view3 = ProjectCreateDraftView(
        project_service=proj_srv,
        channel=mock_channel3,
        name="Public Community Tools",
        prefix="PUB",
    )
    assert draft_view3.role is None
    confirm_inter3 = MagicMock(spec=discord.Interaction)
    confirm_inter3.guild = MagicMock()
    confirm_inter3.guild.id = guild_id
    confirm_inter3.response = MagicMock()
    confirm_inter3.response.edit_message = AsyncMock()

    await draft_view3._on_confirm_clicked(confirm_inter3)
    pub_proj = await proj_srv.get_by_name(guild_id, "Public Community Tools")
    assert pub_proj is not None
    assert pub_proj.discord_role_id is None

    # 4. Test ProjectRoleSelectView and ProjectLeadSelectView
    from src.adapters.discord_bot.views.project_menu import ProjectLeadSelectView, ProjectRoleSelectView

    role_view = ProjectRoleSelectView([created_proj], proj_srv, team_srv, services["task"])
    assert len(role_view.proj_select.options) == 1
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 987654
    role_view.role_select._values = [mock_role]

    role_interaction = MagicMock(spec=discord.Interaction)
    role_interaction.response = MagicMock()
    role_interaction.response.edit_message = AsyncMock()
    role_interaction.response.defer = AsyncMock()

    await role_view._on_role_selected(role_interaction)
    await role_view._on_assign_clicked(role_interaction)
    role_interaction.response.edit_message.assert_awaited_once()

    updated_proj = await proj_srv.get_by_id(created_proj.id)
    assert updated_proj.discord_role_id == 987654

    # Test ProjectLeadSelectView
    lead_view = ProjectLeadSelectView([updated_proj], proj_srv, team_srv, services["task"])
    mock_member = MagicMock(spec=discord.Member)
    mock_member.id = 444555
    lead_view.user_select._values = [mock_member]

    lead_interaction = MagicMock(spec=discord.Interaction)
    lead_interaction.response = MagicMock()
    lead_interaction.response.edit_message = AsyncMock()
    lead_interaction.response.defer = AsyncMock()

    await lead_view._on_user_selected(lead_interaction)
    await lead_view._on_assign_clicked(lead_interaction)
    lead_interaction.response.edit_message.assert_awaited_once()

    updated_lead_proj = await proj_srv.get_by_id(created_proj.id)
    assert updated_lead_proj.lead_discord_id == 444555


@pytest.mark.asyncio
async def test_squad_menu_and_modal(services):
    team_srv = services["team"]
    guild_id = 8888888888

    # 1. Test SquadMenuView and embed
    view = SquadMenuView(team_srv, project_service=services["project"], task_service=services["task"])
    embed = build_squad_menu_embed()
    assert "Squad Management Hub" in embed.title
    assert len(view.children) == 4  # Create Squad, Assign Member, Squad Roster, PM Main Menu

    # Test clicking PM Main Menu
    main_menu_interaction = MagicMock(spec=discord.Interaction)
    main_menu_interaction.guild = MagicMock(spec=discord.Guild)
    main_menu_interaction.guild.id = guild_id
    main_menu_interaction.user = MagicMock(spec=discord.Member)
    main_menu_interaction.user.id = 1001
    main_menu_interaction.response = MagicMock()
    main_menu_interaction.response.edit_message = AsyncMock()
    await view._on_hub_clicked(main_menu_interaction)
    main_menu_interaction.response.edit_message.assert_awaited_once()

    # 2. Test SquadCreateRoleSelectView
    role_view = SquadCreateRoleSelectView(team_srv)
    assert len(role_view.children) == 2  # Role Select + Back Button

    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 555666777
    mock_role.name = "Site Reliability"
    role_view.role_select._values = [mock_role]

    select_interaction = MagicMock(spec=discord.Interaction)
    select_interaction.response = MagicMock()
    select_interaction.response.send_modal = AsyncMock()
    await role_view._on_role_selected(select_interaction)
    select_interaction.response.send_modal.assert_awaited_once()

    # 3. Test SquadCreateModalWithName submission
    modal = SquadCreateModalWithName(squad_service=team_srv, selected_role=mock_role)
    assert modal.name_input.default == "Site Reliability"
    modal.name_input._value = "Site Reliability Engineering"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()

    created_team = await team_srv.get_by_name(guild_id, "Site Reliability Engineering")
    assert created_team is not None
    assert created_team.discord_role_id == 555666777

    # 4. Test SquadMemberAssignView (Case A: User missing squad role)
    assign_view = SquadMemberAssignView(teams=[created_team], team_service=team_srv)
    assert len(assign_view.children) == 5  # Squad, User, Role, Confirm, Cancel

    mock_member_no_role = MagicMock(spec=discord.Member)
    mock_member_no_role.id = 3001
    mock_member_no_role.roles = []

    assign_view.user_select._values = [mock_member_no_role]
    assign_interaction = MagicMock(spec=discord.Interaction)
    assign_interaction.guild = MagicMock()
    assign_interaction.guild.id = guild_id
    assign_interaction.guild.get_member.return_value = mock_member_no_role
    assign_interaction.response = MagicMock()
    assign_interaction.response.defer = AsyncMock()
    assign_interaction.response.send_message = AsyncMock()
    assign_interaction.response.edit_message = AsyncMock()

    await assign_view._on_user_selected(assign_interaction)
    await assign_view.confirm_btn.callback(assign_interaction)
    assign_interaction.response.send_message.assert_awaited_once()
    msg = assign_interaction.response.send_message.call_args[0][0]
    assert "is not part of squad" in msg

    # Case B: User has squad role, select lead role and confirm
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 555666777
    mock_member_with_role = MagicMock(spec=discord.Member)
    mock_member_with_role.id = 3002
    mock_member_with_role.roles = [mock_role]

    assign_view.user_select._values = [mock_member_with_role]
    assign_view.role_select._values = ["lead"]
    assign_interaction.guild.get_member.return_value = mock_member_with_role

    await assign_view._on_user_selected(assign_interaction)
    await assign_view._on_role_selected(assign_interaction)
    await assign_view.confirm_btn.callback(assign_interaction)
    assign_interaction.response.edit_message.assert_awaited_once()
    success_embed = assign_interaction.response.edit_message.call_args[1]["embed"]
    assert "Designated" in success_embed.description and "Squad Lead" in success_embed.description

    # 5. Test SquadRosterDetailView
    roster_view = SquadRosterDetailView(teams=[created_team], team_service=team_srv)
    assert len(roster_view.children) == 2  # Squad Select + Back Button
    roster_view.select._values = [str(created_team.id)]

    roster_interaction = MagicMock(spec=discord.Interaction)
    roster_interaction.response = MagicMock()
    roster_interaction.response.edit_message = AsyncMock()

    await roster_view._on_select_team(roster_interaction)
    roster_interaction.response.edit_message.assert_awaited_once()
    roster_embed = roster_interaction.response.edit_message.call_args[1]["embed"]
    assert "Site Reliability Engineering" in roster_embed.title


@pytest.mark.asyncio
async def test_task_menu_and_modal(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 7777777777

    project = await proj_srv.create_project(guild_id=guild_id, name="Security Ops", prefix="SEC")

    # 1. Test TaskMenuView and embeds
    view = TaskMenuView(task_srv, proj_srv, team_service=services["team"], projects=[project])
    embed = build_task_menu_embed()
    assert "Task Operations Control Center" in embed.title
    assert len(view.children) == 9

    board_embed = build_task_board_embed([], 0, "Global Scope", "Active", "All")
    assert "Task Board" in board_embed.title
    assert "No Tasks Found" in board_embed.fields[0].name

    # Test project filter callback
    project_interaction = MagicMock(spec=discord.Interaction)
    project_interaction.guild = MagicMock()
    project_interaction.guild.id = guild_id
    project_interaction.response = MagicMock()
    project_interaction.response.edit_message = AsyncMock()
    view.project_select._values = [str(project.id)]
    await view._on_project_filter_changed(project_interaction)
    project_interaction.response.edit_message.assert_awaited_once()
    assert view.selected_project_id == project.id

    # Test status filter callback
    status_interaction = MagicMock(spec=discord.Interaction)
    status_interaction.guild = MagicMock()
    status_interaction.guild.id = guild_id
    status_interaction.response = MagicMock()
    status_interaction.response.edit_message = AsyncMock()
    view.status_select._values = ["inProgress"]
    await view._on_status_filter_changed(status_interaction)
    status_interaction.response.edit_message.assert_awaited_once()
    assert view.selected_status == TaskStatus.IN_PROGRESS

    # Test assignee filter callback
    mock_filter_user = MagicMock(spec=discord.Member)
    mock_filter_user.id = 1002
    assignee_interaction = MagicMock(spec=discord.Interaction)
    assignee_interaction.guild = MagicMock()
    assignee_interaction.guild.id = guild_id
    assignee_interaction.response = MagicMock()
    assignee_interaction.response.edit_message = AsyncMock()
    view.assignee_select._values = [mock_filter_user]
    await view._on_assignee_filter_changed(assignee_interaction)
    assignee_interaction.response.edit_message.assert_awaited_once()
    assert view.selected_assignee_id == 1002

    # Test clicking PM Main Menu
    main_menu_interaction = MagicMock(spec=discord.Interaction)
    main_menu_interaction.response = MagicMock()
    main_menu_interaction.response.edit_message = AsyncMock()
    await view._on_hub_clicked(main_menu_interaction)
    main_menu_interaction.response.edit_message.assert_awaited_once()

    # 2. Test TaskCreateModal submission
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 987654321
    mock_channel.send = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.id = 111222333
    mock_msg.create_thread = AsyncMock()
    mock_thread = MagicMock()
    mock_thread.id = 444555666
    mock_msg.create_thread.return_value = mock_thread
    mock_channel.send.return_value = mock_msg

    modal = TaskCreateModal(task_service=task_srv, project=project, target_channel=mock_channel)
    modal.title_input._value = "Zero-Trust Network Setup"
    modal.desc_input._value = "Configure Tailscale mesh and ACLs"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    draft_view = interaction.response.send_message.call_args.kwargs.get("view")
    assert draft_view is not None

    # Test interactive assignee selection
    mock_assignee = MagicMock(spec=discord.Member)
    mock_assignee.id = 1002
    draft_view.assignee_select._values = [mock_assignee]
    assignee_int = MagicMock(spec=discord.Interaction)
    assignee_int.response = MagicMock()
    assignee_int.response.edit_message = AsyncMock()
    await draft_view._on_assignee_selected(assignee_int)
    assert draft_view.assignee_id == 1002

    # Test interactive due date preset
    draft_view.due_select._values = ["tomorrow"]
    due_int = MagicMock(spec=discord.Interaction)
    due_int.response = MagicMock()
    due_int.response.edit_message = AsyncMock()
    await draft_view._on_due_selected(due_int)
    assert draft_view.due_at is not None

    # Test interactive priority selection
    draft_view.priority_select._values = ["high"]
    prio_int = MagicMock(spec=discord.Interaction)
    prio_int.response = MagicMock()
    prio_int.response.edit_message = AsyncMock()
    await draft_view._on_priority_selected(prio_int)
    assert draft_view.priority == PriorityLevel.HIGH

    # Confirm and create
    confirm_int = MagicMock(spec=discord.Interaction)
    confirm_int.guild = interaction.guild
    confirm_int.user = interaction.user
    confirm_int.response = MagicMock()
    confirm_int.response.edit_message = AsyncMock()
    await draft_view._on_confirm_clicked(confirm_int)
    confirm_int.response.edit_message.assert_awaited_once()

    tasks, total = await task_srv.list_tasks(guild_id=guild_id, project_id=project.id)
    assert total == 1
    t = tasks[0]
    assert t.title == "Zero-Trust Network Setup"
    assert t.priority == PriorityLevel.HIGH
    assert t.assignee_discord_id == 1002
    assert t.due_at is not None


@pytest.mark.asyncio
async def test_pm_hub_navigation(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]

    guild_id = 9999999999
    # Create project so New Task has an allowed project container
    await proj_srv.create_project(guild_id=guild_id, name="Infrastructure", prefix="INF")

    hub_view = PmHubView(proj_srv, team_srv, task_srv, user_service=user_srv)
    welcome_embed = build_hub_welcome_embed()
    assert "Control Hub" in welcome_embed.title
    assert len(hub_view.children) == 4  # New Task, Task Board, Projects, Tech Tree

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock()
    mock_interaction.guild.id = guild_id
    mock_interaction.user = MagicMock()
    mock_interaction.user.id = 123456789
    mock_interaction.user.display_name = "testuser"
    mock_interaction.channel = MagicMock()
    mock_interaction.message = MagicMock()
    mock_interaction.message.edit = AsyncMock()
    mock_interaction.response = MagicMock()
    mock_interaction.response.send_message = AsyncMock()
    mock_interaction.response.send_modal = AsyncMock()

    # Click New Task (opens modal and refreshes message)
    await hub_view.new_task_btn.callback(mock_interaction)
    mock_interaction.response.send_modal.assert_awaited_once()
    mock_interaction.message.edit.assert_awaited_once()

    # Switch to projects tab (ephemeral)
    await hub_view.projects_tab.callback(mock_interaction)
    assert mock_interaction.response.send_message.await_count == 1
    assert mock_interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    # Switch to tasks tab (ephemeral)
    await hub_view.tasks_tab.callback(mock_interaction)
    assert mock_interaction.response.send_message.await_count == 2
    assert mock_interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_pm_hub_multi_project_flows(services):
    """Verify PmHubView in multi-project forums shows project selection for tasks and board."""
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 9988776655
    channel_id = 33445566

    p1 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Alpha",
        prefix="ALPH",
        discord_channel_id=channel_id,
    )
    p2 = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Beta",
        prefix="BETA",
        discord_channel_id=channel_id,
    )

    hub_view = PmHubView(proj_srv, team_srv, task_srv, user_service=user_srv)

    mock_channel = MagicMock(spec=discord.ForumChannel)
    mock_channel.id = channel_id
    mock_channel.name = "multi-forum"

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock()
    mock_interaction.guild.id = guild_id
    mock_interaction.user = MagicMock()
    mock_interaction.user.id = 123456789
    mock_interaction.user.display_name = "testuser"
    mock_interaction.channel = mock_channel
    mock_interaction.response = MagicMock()
    mock_interaction.response.send_message = AsyncMock()
    mock_interaction.response.send_modal = AsyncMock()
    mock_interaction.response.edit_message = AsyncMock()

    # 1. Clicking New Task in multi-project forum should send ephemeral HubTaskProjectSelectView
    await hub_view.new_task_btn.callback(mock_interaction)
    mock_interaction.response.send_message.assert_awaited_once()
    send_args = mock_interaction.response.send_message.call_args
    assert send_args.kwargs.get("ephemeral") is True
    picker_view = send_args.kwargs.get("view")
    assert isinstance(picker_view, HubTaskProjectSelectView)
    assert len(picker_view.select.options) == 3  # Alpha, Beta, Standalone

    # 2. Select Project Alpha -> opens TaskCreateModal for Alpha
    picker_view.select._values = [str(p1.id)]
    select_interaction = MagicMock(spec=discord.Interaction)
    select_interaction.response = MagicMock()
    select_interaction.response.send_modal = AsyncMock()
    await picker_view._on_select(select_interaction)
    select_interaction.response.send_modal.assert_awaited_once()
    modal = select_interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, TaskCreateModal)
    assert modal.project.id == p1.id

    # 3. Select Standalone -> opens TaskCreateModal with project=None
    picker_view.select._values = ["standalone"]
    select_interaction2 = MagicMock(spec=discord.Interaction)
    select_interaction2.response = MagicMock()
    select_interaction2.response.send_modal = AsyncMock()
    await picker_view._on_select(select_interaction2)
    select_interaction2.response.send_modal.assert_awaited_once()
    modal2 = select_interaction2.response.send_modal.call_args.args[0]
    assert isinstance(modal2, TaskCreateModal)
    assert modal2.project is None

    # Test clicking Select & Open Modal button directly
    btn_interaction = MagicMock(spec=discord.Interaction)
    btn_interaction.response = MagicMock()
    btn_interaction.response.send_modal = AsyncMock()
    await picker_view._on_select_button_clicked(btn_interaction)
    btn_interaction.response.send_modal.assert_awaited_once()

    # Test Cancel button
    cancel_inter = MagicMock(spec=discord.Interaction)
    cancel_inter.response = MagicMock()
    cancel_inter.response.edit_message = AsyncMock()
    await picker_view._on_cancel_clicked(cancel_inter)
    cancel_inter.response.edit_message.assert_awaited_once()

    # 4. Clicking My Tasks in multi-project forum should send ephemeral HubBoardProjectSelectView
    board_interaction = MagicMock(spec=discord.Interaction)
    board_interaction.guild = MagicMock()
    board_interaction.guild.id = guild_id
    board_interaction.user = MagicMock()
    board_interaction.user.id = 9999
    board_interaction.channel = mock_channel
    board_interaction.response = MagicMock()
    board_interaction.response.send_message = AsyncMock()

    await hub_view.tasks_tab.callback(board_interaction)
    board_interaction.response.send_message.assert_awaited_once()
    board_send_args = board_interaction.response.send_message.call_args
    assert board_send_args.kwargs.get("ephemeral") is True
    board_picker_view = board_send_args.kwargs.get("view")
    assert isinstance(board_picker_view, HubBoardProjectSelectView)
    assert len(board_picker_view.select.options) == 3  # All, Alpha, Beta

    # 5. Select Project Beta in board picker -> edits message to TaskMenuView scoped to Beta
    board_picker_view.select._values = [str(p2.id)]
    board_select_interaction = MagicMock(spec=discord.Interaction)
    board_select_interaction.guild = MagicMock()
    board_select_interaction.guild.id = guild_id
    board_select_interaction.user = MagicMock()
    board_select_interaction.user.id = 9999
    board_select_interaction.response = MagicMock()
    board_select_interaction.response.edit_message = AsyncMock()
    await board_picker_view._on_select(board_select_interaction)
    board_select_interaction.response.edit_message.assert_awaited_once()
    task_view = board_select_interaction.response.edit_message.call_args.kwargs.get("view")
    assert isinstance(task_view, TaskMenuView)
    assert task_view.selected_project_id == p2.id


@pytest.mark.asyncio
async def test_user_settings_menu_and_toggle(services):
    user_srv = services["user"]
    guild_id = 9999999999
    from src.domain.enums import NotificationPreference

    view = UserSettingsView(
        user_service=user_srv,
        current_pref=NotificationPreference.DM,
        project_service=services["project"],
        team_service=services["team"],
        task_service=services["task"],
    )
    assert len(view.children) == 6  # 4 prefs + 1 test + 1 back

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock()
    mock_interaction.guild.id = guild_id
    mock_interaction.guild.name = "Test Guild"
    mock_interaction.user = MagicMock()
    mock_interaction.user.id = 777888
    mock_interaction.user.display_name = "Alice"
    mock_interaction.user.send = AsyncMock()
    mock_interaction.response = MagicMock()
    mock_interaction.response.edit_message = AsyncMock()
    mock_interaction.response.send_message = AsyncMock()

    # 1. Click Channel Ping preference
    await view._on_pref_clicked(mock_interaction, NotificationPreference.CHANNEL)
    mock_interaction.response.edit_message.assert_awaited_once()

    pref = await user_srv.get_preference(guild_id, 777888)
    assert pref == NotificationPreference.CHANNEL

    # 2. Click Test Notification button
    await view._on_test_clicked(mock_interaction)
    mock_interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_menu_and_list_excludes_completed_by_default(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 9991234567

    p = await proj_srv.create_project(guild_id=guild_id, name="Core Board", prefix="CRB")
    await task_srv.create_task(guild_id=guild_id, title="Active 1", creator_discord_id=1, project_id=p.id)
    await task_srv.create_task(guild_id=guild_id, title="Active 2", creator_discord_id=1, project_id=p.id)
    t3 = await task_srv.create_task(guild_id=guild_id, title="Done 1", creator_discord_id=1, project_id=p.id)
    await task_srv.update_status(
        task_id=t3.id, new_status=TaskStatus.COMPLETED, expected_version=t3.version, actor_discord_id=1
    )

    # 1. Default list_tasks with exclude_completed=True
    active_tasks, total = await task_srv.list_tasks(guild_id=guild_id, exclude_completed=True)
    assert total == 2
    titles = [t.title for t in active_tasks]
    assert "Active 1" in titles
    assert "Active 2" in titles
    assert "Done 1" not in titles

    # 2. list_tasks with exclude_completed=False
    _, total_all = await task_srv.list_tasks(guild_id=guild_id, exclude_completed=False)
    assert total_all == 3

    # 3. list_tasks with status=TaskStatus.COMPLETED
    completed_tasks, total_completed = await task_srv.list_tasks(guild_id=guild_id, status=TaskStatus.COMPLETED)
    assert total_completed == 1
    assert completed_tasks[0].title == "Done 1"


@pytest.mark.asyncio
async def test_project_archive_and_restore_confirmation_flows(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 9995554443

    # 1. Setup project and task
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Security Audit",
        prefix="SEC",
        description="Quarterly security audit",
        discord_channel_id=123456,
    )
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Penetration Testing",
        creator_discord_id=1,
        project_id=project.id,
    )
    await task_srv.update_discord_message_ids(task_id=task.id, discord_message_id=111222, discord_thread_id=987654321)

    # 2. Test ProjectMenuView -> Archive Project button opens ProjectArchiveSelectView
    menu_view = ProjectMenuView(proj_srv, team_srv, task_srv)
    archive_btn_interaction = MagicMock(spec=discord.Interaction)
    archive_btn_interaction.guild = MagicMock()
    archive_btn_interaction.guild.id = guild_id
    archive_btn_interaction.response = MagicMock()
    archive_btn_interaction.response.edit_message = AsyncMock()

    await menu_view.archive_btn.callback(archive_btn_interaction)
    archive_btn_interaction.response.edit_message.assert_awaited_once()
    select_view = archive_btn_interaction.response.edit_message.call_args.kwargs["view"]
    assert isinstance(select_view, ProjectArchiveSelectView)

    # 3. Test Selecting project from ProjectArchiveSelectView opens ProjectArchiveConfirmView
    select_view.select._values = [str(project.id)]
    select_interaction = MagicMock(spec=discord.Interaction)
    select_interaction.guild = MagicMock()
    select_interaction.guild.id = guild_id
    select_interaction.response = MagicMock()
    select_interaction.response.edit_message = AsyncMock()

    await select_view._on_select(select_interaction)
    select_interaction.response.edit_message.assert_awaited_once()
    confirm_view = select_interaction.response.edit_message.call_args.kwargs["view"]
    confirm_embed = select_interaction.response.edit_message.call_args.kwargs["embed"]
    assert isinstance(confirm_view, ProjectArchiveConfirmView)
    assert "Confirm Project Archival" in confirm_embed.title
    assert "Security Audit" in confirm_embed.description

    # 4. Test clicking Cancel on ProjectArchiveConfirmView aborts and returns to ProjectMenuView
    cancel_interaction = MagicMock(spec=discord.Interaction)
    cancel_interaction.response = MagicMock()
    cancel_interaction.response.edit_message = AsyncMock()

    await confirm_view.cancel_btn.callback(cancel_interaction)
    cancel_interaction.response.edit_message.assert_awaited_once()
    returned_view = cancel_interaction.response.edit_message.call_args.kwargs["view"]
    assert isinstance(returned_view, ProjectMenuView)

    # Project is still active
    unchanged_proj = await proj_srv.get_by_id(project.id)
    assert unchanged_proj is not None
    assert not unchanged_proj.is_archived

    # 5. Test clicking Confirm Archive on ProjectArchiveConfirmView archives project and syncs threads
    client_mock = MagicMock()
    client_mock.sync_task_thread = AsyncMock()
    confirm_archive_interaction = MagicMock(spec=discord.Interaction)
    confirm_archive_interaction.guild = MagicMock()
    confirm_archive_interaction.guild.id = guild_id
    confirm_archive_interaction.client = client_mock
    confirm_archive_interaction.response = MagicMock()
    confirm_archive_interaction.response.edit_message = AsyncMock()

    await confirm_view.confirm_btn.callback(confirm_archive_interaction)
    confirm_archive_interaction.response.edit_message.assert_awaited_once()
    archived_embed = confirm_archive_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Project Archived!" in archived_embed.description

    # Project is now archived
    archived_proj = await proj_srv.get_by_id(project.id)
    assert archived_proj is not None
    assert archived_proj.is_archived
    client_mock.sync_task_thread.assert_awaited_once()

    # 6. Test ProjectMenuView -> Restore Project button opens ProjectRestoreSelectView
    restore_btn_interaction = MagicMock(spec=discord.Interaction)
    restore_btn_interaction.guild = MagicMock()
    restore_btn_interaction.guild.id = guild_id
    restore_btn_interaction.response = MagicMock()
    restore_btn_interaction.response.edit_message = AsyncMock()

    await menu_view.restore_btn.callback(restore_btn_interaction)
    restore_btn_interaction.response.edit_message.assert_awaited_once()
    restore_select_view = restore_btn_interaction.response.edit_message.call_args.kwargs["view"]
    assert isinstance(restore_select_view, ProjectRestoreSelectView)

    # 7. Test Selecting project from ProjectRestoreSelectView opens ProjectRestoreConfirmView
    restore_select_view.select._values = [str(project.id)]
    restore_select_interaction = MagicMock(spec=discord.Interaction)
    restore_select_interaction.guild = MagicMock()
    restore_select_interaction.guild.id = guild_id
    restore_select_interaction.response = MagicMock()
    restore_select_interaction.response.edit_message = AsyncMock()

    await restore_select_view._on_select(restore_select_interaction)
    restore_select_interaction.response.edit_message.assert_awaited_once()
    restore_confirm_view = restore_select_interaction.response.edit_message.call_args.kwargs["view"]
    restore_confirm_embed = restore_select_interaction.response.edit_message.call_args.kwargs["embed"]
    assert isinstance(restore_confirm_view, ProjectRestoreConfirmView)
    assert "Confirm Project Restoration" in restore_confirm_embed.title
    assert "Security Audit" in restore_confirm_embed.description

    # 8. Test clicking Cancel on ProjectRestoreConfirmView aborts and returns to ProjectMenuView
    restore_cancel_interaction = MagicMock(spec=discord.Interaction)
    restore_cancel_interaction.response = MagicMock()
    restore_cancel_interaction.response.edit_message = AsyncMock()

    await restore_confirm_view.cancel_btn.callback(restore_cancel_interaction)
    restore_cancel_interaction.response.edit_message.assert_awaited_once()
    assert isinstance(restore_cancel_interaction.response.edit_message.call_args.kwargs["view"], ProjectMenuView)

    # Project is still archived
    still_archived_proj = await proj_srv.get_by_id(project.id)
    assert still_archived_proj.is_archived

    # 9. Test clicking Confirm Restore on ProjectRestoreConfirmView reactivates project
    client_mock.reset_mock()
    confirm_restore_interaction = MagicMock(spec=discord.Interaction)
    confirm_restore_interaction.guild = MagicMock()
    confirm_restore_interaction.guild.id = guild_id
    confirm_restore_interaction.client = client_mock
    confirm_restore_interaction.response = MagicMock()
    confirm_restore_interaction.response.edit_message = AsyncMock()

    await restore_confirm_view.confirm_btn.callback(confirm_restore_interaction)
    confirm_restore_interaction.response.edit_message.assert_awaited_once()
    restored_embed = confirm_restore_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Project Restored!" in restored_embed.description

    # Project is reactivated
    restored_proj = await proj_srv.get_by_id(project.id)
    assert not restored_proj.is_archived
    client_mock.sync_task_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_search_and_pagination_flows(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 9991112233

    # 1. Create 30 projects for testing pagination (> 25 items)
    created_projects = []
    for i in range(1, 31):
        p = await proj_srv.create_project(
            guild_id=guild_id,
            name=f"Project Alpha {i:02d}",
            prefix=f"P{i:02d}",
            description=f"Description for project {i}",
        )
        created_projects.append(p)

    # 2. Test ProjectActiveListView pagination
    active_view = ProjectActiveListView(
        projects=created_projects,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        page_size=8,
    )
    # 30 items / 8 per page = 4 pages
    assert active_view.current_page == 0
    embed = build_active_projects_embed(created_projects, 0, len(created_projects), page_size=8)
    assert "Active Projects (30)" in embed.title
    assert len(embed.fields) == 8
    assert "Project Alpha 01" in embed.fields[0].name

    # Next page callback
    next_interaction = MagicMock(spec=discord.Interaction)
    next_interaction.response = MagicMock()
    next_interaction.response.edit_message = AsyncMock()
    await active_view._on_next_clicked(next_interaction)
    next_interaction.response.edit_message.assert_awaited_once()
    assert active_view.current_page == 1

    # Prev page callback
    prev_interaction = MagicMock(spec=discord.Interaction)
    prev_interaction.response = MagicMock()
    prev_interaction.response.edit_message = AsyncMock()
    await active_view._on_prev_clicked(prev_interaction)
    prev_interaction.response.edit_message.assert_awaited_once()
    assert active_view.current_page == 0

    # 3. Test Search in ProjectActiveListView and ProjectSearchModal
    search_cb_mock = AsyncMock()
    modal = ProjectSearchModal(on_search_callback=search_cb_mock, current_query="Alpha")
    modal.query_input._value = "Alpha 12"
    modal_interaction = MagicMock(spec=discord.Interaction)
    await modal.on_submit(modal_interaction)
    search_cb_mock.assert_awaited_once_with(modal_interaction, "Alpha 12")

    search_interaction = MagicMock(spec=discord.Interaction)
    search_interaction.response = MagicMock()
    search_interaction.response.edit_message = AsyncMock()
    await active_view._apply_search(search_interaction, query="Alpha 12")
    search_interaction.response.edit_message.assert_awaited_once()
    search_embed = search_interaction.response.edit_message.call_args.kwargs["embed"]
    assert "Filter: `Alpha 12`" in search_embed.title
    assert len(search_embed.fields) == 1
    assert "Project Alpha 12" in search_embed.fields[0].name

    # Clear search filter
    clear_interaction = MagicMock(spec=discord.Interaction)
    clear_interaction.response = MagicMock()
    clear_interaction.response.edit_message = AsyncMock()
    await active_view._on_clear_filter_clicked(clear_interaction)
    clear_interaction.response.edit_message.assert_awaited_once()
    cleared_embed = clear_interaction.response.edit_message.call_args.kwargs["embed"]
    assert len(cleared_embed.fields) == 8

    # 4. Test ProjectArchiveSelectView with 30 items
    archive_select_view = ProjectArchiveSelectView(
        projects=created_projects,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
    )
    # Page 0 has 25 items (max select options)
    assert len(archive_select_view.select.options) == 25
    assert archive_select_view.current_page == 0

    # Next page switch in archive select view
    archive_next_interaction = MagicMock(spec=discord.Interaction)
    archive_next_interaction.response = MagicMock()
    archive_next_interaction.response.edit_message = AsyncMock()
    await archive_select_view._on_next_clicked(archive_next_interaction)
    archive_next_interaction.response.edit_message.assert_awaited_once()
    assert archive_select_view.current_page == 1
    assert len(archive_select_view.select.options) == 5  # remaining 5 items

    # Search in archive select view
    archive_search_interaction = MagicMock(spec=discord.Interaction)
    archive_search_interaction.response = MagicMock()
    archive_search_interaction.response.edit_message = AsyncMock()
    await archive_select_view._apply_search(archive_search_interaction, query="P29")
    archive_search_interaction.response.edit_message.assert_awaited_once()
    assert len(archive_select_view.select.options) == 1
    assert "P29" in archive_select_view.select.options[0].label

    # 5. Test ProjectRestoreSelectView with 30 archived items
    for p in created_projects:
        await proj_srv.archive_project(p.id)

    archived_list = await proj_srv.list_projects(guild_id, include_archived=True)
    archived_projects = [p for p in archived_list if p.is_archived]
    assert len(archived_projects) == 30

    restore_select_view = ProjectRestoreSelectView(
        projects=archived_projects,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
    )
    assert len(restore_select_view.select.options) == 25
    assert restore_select_view.current_page == 0

    # Next page in restore select view
    restore_next_interaction = MagicMock(spec=discord.Interaction)
    restore_next_interaction.response = MagicMock()
    restore_next_interaction.response.edit_message = AsyncMock()
    await restore_select_view._on_next_clicked(restore_next_interaction)
    restore_next_interaction.response.edit_message.assert_awaited_once()
    assert restore_select_view.current_page == 1
    assert len(restore_select_view.select.options) == 5

    # Search in restore select view
    restore_search_interaction = MagicMock(spec=discord.Interaction)
    restore_search_interaction.response = MagicMock()
    restore_search_interaction.response.edit_message = AsyncMock()
    await restore_select_view._apply_search(restore_search_interaction, query="Alpha 07")
    restore_search_interaction.response.edit_message.assert_awaited_once()
    assert len(restore_select_view.select.options) == 1
    assert "Project Alpha 07" in restore_select_view.select.options[0].label


@pytest.mark.asyncio
async def test_task_menu_channel_scoping_and_global_search(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 999333444

    # 1. Setup two projects, one bound to channel 12345
    proj_a = await proj_srv.create_project(
        guild_id=guild_id,
        name="Mobile iOS",
        prefix="IOS",
        discord_channel_id=12345,
    )
    proj_b = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend Microservices",
        prefix="BACK",
        discord_channel_id=67890,
    )
    all_projects = [proj_a, proj_b]

    # Create tasks for proj_a
    await task_srv.create_task(
        guild_id=guild_id,
        title="SwiftUI Navigation Flow",
        creator_discord_id=1,
        project_id=proj_a.id,
    )

    # 2. Test opening TaskMenuView in channel 12345 (channel-bound to proj_a)
    view_channel = TaskMenuView(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=team_srv,
        projects=all_projects,
        current_channel_id=12345,
    )
    # Verify default scope is automatically proj_a
    assert view_channel.selected_project_id == proj_a.id
    assert view_channel.new_task_btn.label == "New Task [IOS]"

    # Verify channel project is marked with "(This Channel)"
    this_chan_option = next((opt for opt in view_channel.project_select.options if opt.value == str(proj_a.id)), None)
    assert this_chan_option is not None
    assert "(This Channel)" in this_chan_option.label
    assert this_chan_option.default is True

    # 3. Test opening TaskMenuView in an unbound channel (99999) -> defaults to Global Scope
    view_unbound = TaskMenuView(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=team_srv,
        projects=all_projects,
        current_channel_id=99999,
    )
    assert view_unbound.selected_project_id is None
    global_option = next((opt for opt in view_unbound.project_select.options if opt.value == "all"), None)
    assert global_option is not None
    assert global_option.default is True

    # 4. Test TaskProjectSearchModal submission and scope switching
    search_cb_mock = AsyncMock()
    modal = TaskProjectSearchModal(on_search_callback=search_cb_mock, current_query="")
    modal.query_input._value = "Backend"
    modal_interaction = MagicMock(spec=discord.Interaction)
    await modal.on_submit(modal_interaction)
    search_cb_mock.assert_awaited_once_with(modal_interaction, "Backend")

    # Apply search in view_unbound to switch scope to proj_b
    search_interaction = MagicMock(spec=discord.Interaction)
    search_interaction.guild = MagicMock()
    search_interaction.guild.id = guild_id
    search_interaction.response = MagicMock()
    search_interaction.response.edit_message = AsyncMock()

    await view_unbound._apply_scope_search(search_interaction, query="Backend")
    search_interaction.response.edit_message.assert_awaited_once()
    assert view_unbound.selected_project_id == proj_b.id
    assert view_unbound.new_task_btn.label == "New Task [BACK]"

    # 5. Test clicking New Project Task when scoped to a project opens TaskCreateModal directly
    new_task_interaction = MagicMock(spec=discord.Interaction)
    new_task_interaction.guild = MagicMock()
    new_task_interaction.guild.id = guild_id
    new_task_interaction.response = MagicMock()
    new_task_interaction.response.send_modal = AsyncMock()

    await view_unbound._on_new_task_clicked(new_task_interaction)
    new_task_interaction.response.send_modal.assert_awaited_once()
    created_modal = new_task_interaction.response.send_modal.call_args.args[0]
    assert isinstance(created_modal, TaskCreateModal)
    assert created_modal.project.id == proj_b.id

    # 6. Test TaskSelectProjectView search and pagination
    select_view = TaskSelectProjectView(
        projects=all_projects,
        task_service=task_srv,
        project_service=proj_srv,
        team_service=team_srv,
        current_channel_id=12345,
    )
    # Channel project is sorted first
    assert select_view.select.options[0].value == str(proj_a.id)
    assert "(This Channel)" in select_view.select.options[0].label

    # Search in TaskSelectProjectView
    filter_interaction = MagicMock(spec=discord.Interaction)
    filter_interaction.response = MagicMock()
    filter_interaction.response.edit_message = AsyncMock()
    await select_view._apply_search(filter_interaction, query="Backend")
    filter_interaction.response.edit_message.assert_awaited_once()
    assert len(select_view.select.options) == 1
    assert select_view.select.options[0].value == str(proj_b.id)


@pytest.mark.asyncio
async def test_menu_timeouts_and_hub_registration(services):
    """Verify on_timeout handlers cleanly delete the original response across all menu views,

    and verify that PmHubView tabs register with menu_manager.
    """
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 999111222

    # 1. ProjectMenuView on_timeout
    inter = MagicMock(spec=discord.Interaction)
    inter.guild_id = guild_id
    inter.user = MagicMock()
    inter.user.id = 1234
    inter.delete_original_response = AsyncMock()

    proj_view = ProjectMenuView(proj_srv, team_srv, task_srv, initial_interaction=inter)
    assert proj_view.timeout == 180
    await proj_view.on_timeout()
    inter.delete_original_response.assert_awaited_once()

    # 2. SquadMenuView on_timeout
    inter2 = MagicMock(spec=discord.Interaction)
    inter2.guild_id = guild_id
    inter2.user = MagicMock()
    inter2.user.id = 1234
    inter2.delete_original_response = AsyncMock()

    squad_view = SquadMenuView(team_srv, proj_srv, task_srv, initial_interaction=inter2)
    assert squad_view.timeout == 180
    await squad_view.on_timeout()
    inter2.delete_original_response.assert_awaited_once()

    # 3. TaskMenuView on_timeout
    inter3 = MagicMock(spec=discord.Interaction)
    inter3.guild_id = guild_id
    inter3.user = MagicMock()
    inter3.user.id = 1234
    inter3.delete_original_response = AsyncMock()

    task_view = TaskMenuView(task_srv, proj_srv, team_srv, initial_interaction=inter3)
    assert task_view.timeout == 180
    await task_view.on_timeout()
    inter3.delete_original_response.assert_awaited_once()

    # 4. UserSettingsView on_timeout
    from src.adapters.discord_bot.views.settings_menu import UserSettingsView
    from src.domain.enums import NotificationPreference

    inter4 = MagicMock(spec=discord.Interaction)
    inter4.guild_id = guild_id
    inter4.user = MagicMock()
    inter4.user.id = 1234
    inter4.delete_original_response = AsyncMock()

    settings_view = UserSettingsView(
        user_service=user_srv,
        current_pref=NotificationPreference.DM,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        initial_interaction=inter4,
    )
    assert settings_view.timeout == 180
    await settings_view.on_timeout()
    inter4.delete_original_response.assert_awaited_once()

    # 5. HubBoardProjectSelectView on_timeout
    from src.adapters.discord_bot.views.hub_menu import (
        HubBoardProjectSelectView,
        PmHubView,
    )

    inter5 = MagicMock(spec=discord.Interaction)
    inter5.delete_original_response = AsyncMock()
    board_picker = HubBoardProjectSelectView(
        task_service=task_srv,
        project_service=proj_srv,
        team_service=team_srv,
        projects=[],
        channel_projects=[],
    )
    board_picker._initial_interaction = inter5
    await board_picker.on_timeout()
    inter5.delete_original_response.assert_awaited_once()

    # 6. PmHubView tab interactions register menu sessions
    hub_view = PmHubView(
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
    )

    hub_inter = MagicMock(spec=discord.Interaction)
    hub_inter.guild = MagicMock()
    hub_inter.guild.id = guild_id
    hub_inter.guild_id = guild_id
    hub_inter.user = MagicMock()
    hub_inter.user.id = 7777
    hub_inter.response = MagicMock()
    hub_inter.response.send_message = AsyncMock()
    hub_inter.delete_original_response = AsyncMock()

    await hub_view.projects_tab.callback(hub_inter)
    hub_inter.response.send_message.assert_awaited_once()
    opened_view = hub_inter.response.send_message.call_args.kwargs["view"]
    assert isinstance(opened_view, ProjectMenuView)
    assert opened_view._initial_interaction == hub_inter


@pytest.mark.asyncio
async def test_project_create_draft_view_details_edit_and_cancel(services):
    guild_id = 123456789012345678
    proj_srv = services["project"]
    mock_chan = MagicMock(spec=discord.ForumChannel)
    mock_chan.id = 987123
    mock_chan.name = "eng-general"

    draft_view = ProjectCreateDraftView(
        project_service=proj_srv,
        channel=mock_chan,
        name="Initial Name",
        prefix="INIT",
        description="Initial Desc",
        category="Initial Cat",
    )

    # 0. Test build_project_draft_embed output
    draft_embed = build_project_draft_embed(
        name="Initial Name",
        channel=mock_chan,
        prefix="INIT",
        description="Initial Desc",
        category="Initial Cat",
    )
    assert "Initial Name" in draft_embed.title
    assert "Optional" in draft_embed.description

    # 1. Test Edit Details button opens modal with initial values
    edit_inter = MagicMock(spec=discord.Interaction)
    edit_inter.response = MagicMock()
    edit_inter.response.send_modal = AsyncMock()

    await draft_view._on_edit_details_clicked(edit_inter)
    edit_inter.response.send_modal.assert_awaited_once()
    edit_modal = edit_inter.response.send_modal.call_args.args[0]
    assert isinstance(edit_modal, ProjectCreateModal)
    assert edit_modal.draft_view == draft_view
    assert edit_modal.name_input.default == "Initial Name"
    assert edit_modal.prefix_input.default == "INIT"

    # 2. Test submitting the edit modal updates the draft view
    edit_modal.name_input._value = "Updated Project Name"
    edit_modal.prefix_input._value = "UPDT"
    edit_modal.desc_input._value = "Updated Description"
    edit_modal.cat_input._value = "Backend"

    submit_inter = MagicMock(spec=discord.Interaction)
    submit_inter.guild = MagicMock()
    submit_inter.guild.id = guild_id
    submit_inter.response = MagicMock()
    submit_inter.response.edit_message = AsyncMock()

    await edit_modal.on_submit(submit_inter)
    assert draft_view.name == "Updated Project Name"
    assert draft_view.prefix == "UPDT"
    assert draft_view.description == "Updated Description"
    assert draft_view.category == "Backend"
    submit_inter.response.edit_message.assert_awaited_once()

    # 3. Test Cancel button
    cancel_inter = MagicMock(spec=discord.Interaction)
    cancel_inter.response = MagicMock()
    cancel_inter.response.edit_message = AsyncMock()

    await draft_view._on_cancel_clicked(cancel_inter)
    cancel_inter.response.edit_message.assert_awaited_once()
    cancel_embed = cancel_inter.response.edit_message.call_args.kwargs["embed"]
    assert "Project Creation Cancelled" in cancel_embed.title

    # 4. Test on_timeout cleans up initial interaction
    initial_inter = MagicMock(spec=discord.Interaction)
    initial_inter.delete_original_response = AsyncMock()
    draft_view._initial_interaction = initial_inter
    await draft_view.on_timeout()
    initial_inter.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_dashboard_view_and_embed(services):
    from src.adapters.discord_bot.views.admin_menu import (
        PmDashboardOverviewView,
        PmDashboardView,
        build_pm_dashboard_embed,
    )
    from src.domain.enums import NotificationPreference

    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    user_srv = services["user"]
    guild_id = 9999888866

    p1 = await proj_srv.create_project(guild_id=guild_id, name="Admin Core", prefix="ADM")

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_guild.name = "Engineering Guild"

    mock_admin_user = MagicMock(spec=discord.Member)
    mock_admin_user.id = 1001
    mock_admin_user.display_name = "Admin Alice"
    mock_admin_user.guild_permissions = MagicMock(administrator=True, manage_guild=True)

    # 1. Test build_pm_dashboard_embed
    embed = build_pm_dashboard_embed(
        guild=mock_guild,
        user=mock_admin_user,
        active_projects=[p1],
        active_tasks_count=5,
        current_pref=NotificationPreference.BOTH,
        is_server_manager=True,
    )
    assert "Project Management Control Center" in embed.title
    assert "1" in embed.fields[0].value
    assert "5" in embed.fields[1].value
    assert "Both" in embed.fields[2].value

    # 2. Test PmDashboardView initialization
    dash_view = PmDashboardView(
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=user_srv,
        user=mock_admin_user,
    )
    assert dash_view.is_server_manager is True
    assert dash_view.new_proj_btn is not None
    assert dash_view.projects_btn is not None
    assert dash_view.settings_btn is not None
    assert dash_view.overview_btn is not None
    assert dash_view.guides_btn is not None

    # 3. Test New Project button opens ChannelSelectView
    inter = MagicMock(spec=discord.Interaction)
    inter.guild = mock_guild
    inter.user = mock_admin_user
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()

    await dash_view._on_new_project_clicked(inter)
    inter.response.edit_message.assert_awaited_once()
    chan_view = inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(chan_view, ProjectChannelSelectView)
    assert chan_view.return_to == "dashboard"

    # Test ProjectChannelSelectView Back button returns to PmDashboardView
    back_inter = MagicMock(spec=discord.Interaction)
    back_inter.guild = mock_guild
    back_inter.user = mock_admin_user
    back_inter.response = MagicMock()
    back_inter.response.edit_message = AsyncMock()

    await chan_view._on_back_clicked(back_inter)
    back_inter.response.edit_message.assert_awaited_once()
    back_dash_view = back_inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(back_dash_view, PmDashboardView)

    # 4. Test Projects button opens ProjectMenuView
    inter.response.edit_message.reset_mock()
    await dash_view._on_projects_clicked(inter)
    inter.response.edit_message.assert_awaited_once()
    proj_view = inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(proj_view, ProjectMenuView)
    assert proj_view.return_to == "dashboard"

    # Test ProjectMenuView Hub button returns to PmDashboardView
    back_inter.response.edit_message.reset_mock()
    await proj_view._on_hub_clicked(back_inter)
    back_inter.response.edit_message.assert_awaited_once()
    back_dash_view = back_inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(back_dash_view, PmDashboardView)

    # 5. Test Settings button opens UserSettingsView
    inter.response.edit_message.reset_mock()
    await dash_view._on_settings_clicked(inter)
    inter.response.edit_message.assert_awaited_once()
    settings_view = inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(settings_view, UserSettingsView)
    assert settings_view.return_to == "dashboard"

    # Test UserSettingsView Hub button returns to PmDashboardView
    back_inter.response.edit_message.reset_mock()
    await settings_view._on_hub_clicked(back_inter)
    back_inter.response.edit_message.assert_awaited_once()
    back_dash_view = back_inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(back_dash_view, PmDashboardView)

    # 7. Test Overview button and OverviewView Back button
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    await dash_view._on_overview_clicked(inter)
    inter.edit_original_response.assert_awaited_once()
    overview_view = inter.edit_original_response.call_args.kwargs["view"]
    assert isinstance(overview_view, PmDashboardOverviewView)

    back_inter.response.edit_message.reset_mock()
    await overview_view._on_back_clicked(back_inter)
    back_inter.response.edit_message.assert_awaited_once()
    back_dash_view = back_inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(back_dash_view, PmDashboardView)

    # 8. Test Guides button
    inter.response.edit_message.reset_mock()
    await dash_view._on_guides_clicked(inter)
    inter.response.edit_message.assert_awaited_once()
    guides_view = inter.response.edit_message.call_args.kwargs["view"]
    assert isinstance(guides_view, PmDashboardOverviewView)


def test_build_hub_welcome_embed_squad_roles():
    from src.domain.models import Project, Squad

    guild_id = 778899

    # 1. Single Project with a Squad Role
    p1 = Project(guild_id=guild_id, name="Dev & Platform", prefix="DEV", description="Core platform development.")
    s1 = Squad(guild_id=guild_id, name="Dev Team", discord_role_id=123456)
    embed1 = build_hub_welcome_embed(
        channel_name="💻-dev-platform",
        bound_projects=[p1],
        project_squads_map={p1.id: [s1]},
    )
    assert "**[DEV] Dev & Platform**" in embed1.description
    assert "> **Squad Role**: <@&123456>" in embed1.description

    # 2. Single Project without any Squad Roles (Open)
    p2 = Project(guild_id=guild_id, name="Open Project", prefix="OPEN", description="Open for all.")
    embed2 = build_hub_welcome_embed(
        channel_name="🌐-open-hub",
        bound_projects=[p2],
        project_squads_map={p2.id: []},
    )
    assert "> **Squad Role**: *Open (No role required)*" in embed2.description

    # 3. Multi-Project Channel with Multi-Roles and Fallbacks
    p_ne = Project(guild_id=guild_id, name="Northeast Chapter", prefix="NE", description="NE organizing.")
    p_west = Project(guild_id=guild_id, name="West Coast Chapter", prefix="WEST", description="West organizing.")
    p_open = Project(guild_id=guild_id, name="Global Chapter", prefix="GLB", description="Global organizing.")

    s_ne1 = Squad(guild_id=guild_id, name="Northeast", discord_role_id=111111)
    s_ne2 = Squad(guild_id=guild_id, name="Northeast Leads", discord_role_id=222222)
    s_west = Squad(guild_id=guild_id, name="West Team", discord_role_id=0)  # No valid discord role ID

    squads_map = {
        p_ne.id: [s_ne1, s_ne2],
        p_west.id: [s_west],
        p_open.id: [],
    }

    embed_multi = build_hub_welcome_embed(
        channel_name="🌐-regional-organizing",
        bound_projects=[p_ne, p_west, p_open],
        project_squads_map=squads_map,
    )

    desc = embed_multi.description
    assert "### Bound Projects in this Channel" in desc
    # Multi-roles comma separated
    assert "**[NE] Northeast Chapter**" in desc
    assert "> **Squad Roles**: <@&111111>, <@&222222>" in desc
    # Fallback when role ID is 0
    assert "**[WEST] West Coast Chapter**" in desc
    assert "> **Squad Role**: **West Team**" in desc
    # Open project
    assert "**[GLB] Global Chapter**" in desc
    assert "> **Squad Role**: *Open (No role required)*" in desc


@pytest.mark.asyncio
async def test_project_channel_select_incompatible_channel_resets_dropdown(services):
    """Selecting an incompatible channel (e.g. non-forum channel) in ProjectChannelSelectView

    must reset the channel select menu back to placeholder rather than leaving the invalid channel selected,
    and deliver an auto-dismissing ephemeral toast.
    """
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    view = ProjectChannelSelectView(proj_srv, team_srv, task_srv)

    mock_text_channel = MagicMock(spec=discord.TextChannel)
    mock_text_channel.id = 987654321

    # Simulate selecting a text channel in the ChannelSelect
    view.channel_select._values = [mock_text_channel]

    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    inter.guild.get_channel = MagicMock(return_value=mock_text_channel)
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    mock_toast = MagicMock()
    inter.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await view._on_channel_selected(inter)

        # Must edit the message to reset the view
        inter.response.edit_message.assert_awaited_once_with(view=view)

        # Must send auto-dismissing toast via followup
        inter.followup.send.assert_awaited_once()
        toast_text = inter.followup.send.call_args[0][0]
        assert "Selected channel must be a Forum Channel" in toast_text
        assert "*⏱️ Auto-dismisses <t:" in toast_text
        assert inter.followup.send.call_args.kwargs.get("ephemeral") is True
        assert inter.followup.send.call_args.kwargs.get("wait") is True
        mock_schedule.assert_called_once_with(mock_toast, delay=8.0)

    # Channel select must be reset back to empty selection and placeholder
    assert view.channel_select.placeholder == "Select target Forum channel..."
    assert view.channel_select.values == []


@pytest.mark.asyncio
async def test_project_create_modal_empty_name_from_channel_select_resets_dropdown(services):
    """Submitting an empty project name in ProjectCreateModal when opened from ProjectChannelSelectView

    must reset the parent channel select menu to placeholder, edit the parent message,
    and deliver an auto-dismissing ephemeral toast.
    """
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    view = ProjectChannelSelectView(proj_srv, team_srv, task_srv)
    mock_forum_channel = MagicMock(spec=discord.ForumChannel)
    mock_forum_channel.id = 555666777
    view.channel_select._values = [mock_forum_channel]

    modal = ProjectCreateModal(proj_srv, channel=mock_forum_channel, parent_view=view)
    modal.name_input._value = "   "

    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    mock_toast = MagicMock()
    inter.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await modal.on_submit(inter)

        inter.response.edit_message.assert_awaited_once_with(view=view)
        inter.followup.send.assert_awaited_once()
        toast_text = inter.followup.send.call_args[0][0]
        assert "Project name cannot be empty" in toast_text
        assert "*⏱️ Auto-dismisses <t:" in toast_text
        assert inter.followup.send.call_args.kwargs.get("ephemeral") is True
        assert inter.followup.send.call_args.kwargs.get("wait") is True
        mock_schedule.assert_called_once_with(mock_toast, delay=8.0)

    assert view.channel_select.placeholder == "Select target Forum channel..."
    assert view.channel_select.values == []


@pytest.mark.asyncio
async def test_project_create_modal_empty_name_from_draft_view_refreshes_view(services):
    """Submitting an empty project name in ProjectCreateModal when opened from ProjectCreateDraftView

    must refresh the draft view, edit the parent message with the draft embed,
    and deliver an auto-dismissing ephemeral toast.
    """
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    mock_forum_channel = MagicMock(spec=discord.ForumChannel)
    mock_forum_channel.id = 555666777
    mock_forum_channel.name = "proj-forum"

    draft_view = ProjectCreateDraftView(
        project_service=proj_srv,
        channel=mock_forum_channel,
        name="Existing Project",
        prefix="EX",
        team_service=team_srv,
        task_service=task_srv,
    )

    modal = ProjectCreateModal(proj_srv, channel=mock_forum_channel, draft_view=draft_view)
    modal.name_input._value = ""

    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    mock_toast = MagicMock()
    inter.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await modal.on_submit(inter)

        inter.response.edit_message.assert_awaited_once()
        assert inter.response.edit_message.call_args.kwargs.get("view") is draft_view
        assert inter.response.edit_message.call_args.kwargs.get("embed") is not None

        inter.followup.send.assert_awaited_once()
        toast_text = inter.followup.send.call_args[0][0]
        assert "Project name cannot be empty" in toast_text
        assert "*⏱️ Auto-dismisses <t:" in toast_text
        mock_schedule.assert_called_once_with(mock_toast, delay=8.0)


@pytest.mark.asyncio
async def test_task_details_modal_empty_title_from_task_select_project_view_resets_dropdown(services):
    """Submitting an empty title in TaskDetailsModal when opened from TaskSelectProjectView

    must reset the project dropdown to placeholder, edit the parent message,
    and deliver an auto-dismissing ephemeral toast.
    """
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    guild_id = 999888777

    proj_a = await proj_srv.create_project(guild_id, "Project Alpha", "ALP")
    view = TaskSelectProjectView(
        projects=[proj_a],
        task_service=task_srv,
        project_service=proj_srv,
        team_service=team_srv,
    )
    view.select._values = [str(proj_a.id)]

    modal = TaskCreateModal(
        task_service=task_srv,
        project=proj_a,
        parent_view=view,
    )
    modal.title_input._value = "   "

    inter = MagicMock(spec=discord.Interaction)
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    mock_toast = MagicMock()
    inter.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await modal.on_submit(inter)

        inter.response.edit_message.assert_awaited_once()
        assert inter.response.edit_message.call_args.kwargs.get("view") is view
        assert inter.response.edit_message.call_args.kwargs.get("embed") is not None

        inter.followup.send.assert_awaited_once()
        toast_text = inter.followup.send.call_args[0][0]
        assert "Task title cannot be empty" in toast_text
        assert "*⏱️ Auto-dismisses <t:" in toast_text
        assert inter.followup.send.call_args.kwargs.get("ephemeral") is True
        assert inter.followup.send.call_args.kwargs.get("wait") is True
        mock_schedule.assert_called_once_with(mock_toast, delay=10.0)

    assert "Select Project" in view.select.placeholder
    assert view.select.values == []


@pytest.mark.asyncio
async def test_task_details_modal_empty_title_from_hub_task_project_select_view_resets_dropdown(services):
    """Submitting an empty title in TaskDetailsModal when opened from HubTaskProjectSelectView

    must reset the project select dropdown to placeholder, edit the parent message,
    and deliver an auto-dismissing ephemeral toast.
    """
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 999888777

    proj_a = await proj_srv.create_project(guild_id, "Channel Project", "CP")
    view = HubTaskProjectSelectView(
        task_service=task_srv,
        channel_projects=[proj_a],
        target_channel=None,
    )
    view.select._values = [str(proj_a.id)]
    view.selected_project_val = str(proj_a.id)

    modal = TaskCreateModal(
        task_service=task_srv,
        project=proj_a,
        parent_view=view,
    )
    modal.title_input._value = ""

    inter = MagicMock(spec=discord.Interaction)
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    mock_toast = MagicMock()
    inter.followup.send = AsyncMock(return_value=mock_toast)

    with patch("src.adapters.discord_bot.menu_manager.menu_manager.schedule_toast_dismissal") as mock_schedule:
        await modal.on_submit(inter)

        inter.response.edit_message.assert_awaited_once()
        assert inter.response.edit_message.call_args.kwargs.get("view") is view
        assert inter.response.edit_message.call_args.kwargs.get("embed") is not None

        inter.followup.send.assert_awaited_once()
        toast_text = inter.followup.send.call_args[0][0]
        assert "Task title cannot be empty" in toast_text
        assert "*⏱️ Auto-dismisses <t:" in toast_text
        mock_schedule.assert_called_once_with(mock_toast, delay=10.0)

    assert view.select.placeholder == "Select Project for New Task..."
    assert view.select.values == []
