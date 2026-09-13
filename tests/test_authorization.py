from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.views.task_modals import TaskEditModal, TaskNoteModal
from src.domain.enums import PriorityLevel, SquadRoleType
from src.domain.exceptions import PermissionDeniedError
from src.services.auth_service import AuthService


def _make_mock_member(
    user_id: int,
    role_ids: list[int] | None = None,
    manage_guild: bool = False,
    administrator: bool = False,
    is_admin: bool = False,
) -> MagicMock:
    """Helper to create a discord.Member mock with permissions and roles."""
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.guild = None

    # Roles
    role_mocks = []
    for rid in role_ids or []:
        r = MagicMock(spec=discord.Role)
        r.id = rid
        role_mocks.append(r)
    member.roles = role_mocks

    # Permissions
    perms = MagicMock()
    perms.manage_guild = manage_guild
    perms.administrator = administrator or is_admin
    member.guild_permissions = perms

    return member


@pytest.mark.asyncio
async def test_auth_service_server_manager_bypass(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990001
    project = await proj_srv.create_project(guild_id=guild_id, name="Project Alpha", prefix="ALP")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Admin Test Task",
        creator_discord_id=1001,
        assignee_discord_id=1002,
        project_id=project.id,
    )

    admin_member = _make_mock_member(9999, manage_guild=True)
    assert auth_srv.is_server_manager(admin_member) is True
    assert await auth_srv.can_mutate_task(admin_member, task) is True
    assert await auth_srv.can_create_task_in_project(admin_member, project.id) is True


@pytest.mark.asyncio
async def test_auth_service_task_assignee_and_creator(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990002
    project = await proj_srv.create_project(
        guild_id=guild_id, name="Creator Assignee Project", prefix="CAP", discord_role_id=888801
    )
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Creator Assignee Task",
        creator_discord_id=1001,
        assignee_discord_id=1002,
        project_id=project.id,
    )

    creator = _make_mock_member(1001)
    assignee = _make_mock_member(1002)
    random_user = _make_mock_member(1003)

    assert await auth_srv.can_mutate_task(creator, task) is True
    assert await auth_srv.can_mutate_task(assignee, task) is True
    assert await auth_srv.can_mutate_task(random_user, task) is False


@pytest.mark.asyncio
async def test_auth_service_project_squad_role_scoping(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990003
    squad_role_id = 888801
    squad = await squad_srv.create_squad(guild_id=guild_id, name="Core Devs", discord_role_id=squad_role_id)
    project = await proj_srv.create_project(guild_id=guild_id, name="Platform V2", prefix="PLT")
    await proj_srv.assign_squad_to_project(project_id=project.id, squad_id=squad.id)

    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Implement gRPC Gateway",
        creator_discord_id=1001,
        project_id=project.id,
    )

    squad_member = _make_mock_member(2001, role_ids=[squad_role_id])
    outsider = _make_mock_member(2002, role_ids=[999999])

    # Squad member with role is authorized to create and mutate
    assert await auth_srv.can_create_task_in_project(squad_member, project.id) is True
    assert await auth_srv.can_mutate_task(squad_member, task) is True

    # Outsider without role is rejected
    assert await auth_srv.can_create_task_in_project(outsider, project.id) is False
    assert await auth_srv.can_mutate_task(outsider, task) is False


@pytest.mark.asyncio
async def test_auth_service_strict_default_for_unassigned_project(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990004
    project = await proj_srv.create_project(guild_id=guild_id, name="Secret Lab", prefix="SEC", discord_role_id=555111)
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Unassigned Project Task",
        creator_discord_id=1001,
        assignee_discord_id=1002,
        project_id=project.id,
    )

    random_member = _make_mock_member(3001)
    admin_member = _make_mock_member(3002, manage_guild=True)
    squad_member = _make_mock_member(3003, role_ids=[555111])

    # Random member cannot create or mutate
    assert await auth_srv.can_create_task_in_project(random_member, project.id) is False
    assert await auth_srv.can_mutate_task(random_member, task) is False

    # Squad member and server manager can create and mutate
    assert await auth_srv.can_create_task_in_project(squad_member, project.id) is True
    assert await auth_srv.can_mutate_task(squad_member, task) is True
    assert await auth_srv.can_create_task_in_project(admin_member, project.id) is True
    assert await auth_srv.can_mutate_task(admin_member, task) is True


@pytest.mark.asyncio
async def test_squad_lead_roster_management(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990005
    squad = await squad_srv.create_squad(guild_id=guild_id, name="Security Squad", discord_role_id=555111)

    lead_id = 4001
    regular_id = 4002
    outsider_id = 4003

    await squad_srv.assign_member(squad_id=squad.id, user_discord_id=lead_id, role_type=SquadRoleType.LEAD)
    await squad_srv.assign_member(squad_id=squad.id, user_discord_id=regular_id, role_type=SquadRoleType.MEMBER)

    lead_member = _make_mock_member(lead_id, role_ids=[555111])
    regular_member = _make_mock_member(regular_id, role_ids=[555111])
    outsider_member = _make_mock_member(outsider_id, role_ids=[])
    admin_member = _make_mock_member(9999, manage_guild=True)

    # Squad lead can manage roster
    assert await auth_srv.can_manage_squad_roster(lead_member, squad.id) is True
    # Server manager can manage roster
    assert await auth_srv.can_manage_squad_roster(admin_member, squad.id) is True
    # Regular squad member cannot manage roster
    assert await auth_srv.can_manage_squad_roster(regular_member, squad.id) is False
    # Outsider cannot manage roster
    assert await auth_srv.can_manage_squad_roster(outsider_member, squad.id) is False


@pytest.mark.asyncio
async def test_squad_lead_cog_enforcement(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990006

    bot = MagicMock()
    pm_cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        squad_service=squad_srv,
        task_service=task_srv,
        auth_service=auth_srv,
    )

    squad = await squad_srv.create_squad(guild_id=guild_id, name="DevOps", discord_role_id=777888)
    lead_id = 5001
    await squad_srv.add_squad_lead(squad.id, user_discord_id=lead_id)

    # 1. Lead designates a new squad lead with the discord role
    target_user = _make_mock_member(5002, role_ids=[777888])
    interaction_lead = MagicMock(spec=discord.Interaction)
    interaction_lead.guild = MagicMock()
    interaction_lead.guild.id = guild_id
    interaction_lead.user = _make_mock_member(lead_id, role_ids=[777888])
    interaction_lead.response = MagicMock()
    interaction_lead.response.defer = AsyncMock()
    interaction_lead.followup = MagicMock()
    interaction_lead.followup.send = AsyncMock()

    await pm_cog.squad_lead.callback(
        pm_cog,
        interaction=interaction_lead,
        action="add",
        squad_name="DevOps",
        user=target_user,
    )

    interaction_lead.followup.send.assert_awaited_once()
    assert "Designated" in interaction_lead.followup.send.await_args.args[0]

    # 2. Lead attempts to designate a user MISSING the discord role (fails)
    ineligible_user = _make_mock_member(5003, role_ids=[])
    interaction_lead.guild.get_member.return_value = ineligible_user
    interaction_ineligible = MagicMock(spec=discord.Interaction)
    interaction_ineligible.guild = interaction_lead.guild
    interaction_ineligible.user = _make_mock_member(lead_id, role_ids=[777888])
    interaction_ineligible.response = MagicMock()
    interaction_ineligible.response.defer = AsyncMock()
    interaction_ineligible.followup = MagicMock()
    interaction_ineligible.followup.send = AsyncMock()

    await pm_cog.squad_lead.callback(
        pm_cog,
        interaction=interaction_ineligible,
        action="add",
        squad_name="DevOps",
        user=ineligible_user,
    )
    interaction_ineligible.followup.send.assert_awaited_once()
    assert "is not part of" in interaction_ineligible.followup.send.await_args.args[0]

    # 3. Regular member attempts to designate lead (Permission Denied)
    unauthorized_user = _make_mock_member(5099, role_ids=[777888])
    interaction_denied = MagicMock(spec=discord.Interaction)
    interaction_denied.guild = interaction_lead.guild
    interaction_denied.user = unauthorized_user
    interaction_denied.response = MagicMock()
    interaction_denied.response.defer = AsyncMock()
    interaction_denied.followup = MagicMock()
    interaction_denied.followup.send = AsyncMock()

    await pm_cog.squad_lead.callback(
        pm_cog,
        interaction=interaction_denied,
        action="add",
        squad_name="DevOps",
        user=target_user,
    )
    interaction_denied.followup.send.assert_awaited_once()
    assert "you do not have permission" in interaction_denied.followup.send.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_task_assignment_role_eligibility_enforcement(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990008

    bot = MagicMock()
    pm_cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        squad_service=squad_srv,
        task_service=task_srv,
        auth_service=auth_srv,
    )

    project = await proj_srv.create_project(guild_id=guild_id, name="Frontend App", prefix="FE", discord_role_id=999111)
    squad = await squad_srv.create_squad(guild_id=guild_id, name="Frontend Squad", discord_role_id=999111)
    await proj_srv.assign_squad_to_project(project_id=project.id, squad_id=squad.id)

    task = await task_srv.create_task(
        guild_id=guild_id,
        title="FE Navbar",
        creator_discord_id=1001,
        project_id=project.id,
    )

    eligible_member = _make_mock_member(8001, role_ids=[999111])
    ineligible_member = _make_mock_member(8002, role_ids=[])

    # 1. /task-assign with eligible member succeeds
    interaction_ok = MagicMock(spec=discord.Interaction)
    interaction_ok.guild = MagicMock()
    interaction_ok.guild.id = guild_id
    interaction_ok.guild.get_member.return_value = eligible_member
    interaction_ok.user = eligible_member
    interaction_ok.response = MagicMock()
    interaction_ok.response.defer = AsyncMock()
    interaction_ok.followup = MagicMock()
    interaction_ok.followup.send = AsyncMock()

    await pm_cog.task_assign.callback(
        pm_cog,
        interaction=interaction_ok,
        task=task.short_id,
        assignee=eligible_member,
    )

    interaction_ok.followup.send.assert_awaited_once()
    assert "Assigned" in interaction_ok.followup.send.await_args.args[0]

    # 2. /task-assign with ineligible member fails
    interaction_fail = MagicMock(spec=discord.Interaction)
    interaction_fail.guild = MagicMock()
    interaction_fail.guild.id = guild_id
    interaction_fail.guild.get_member.return_value = ineligible_member
    interaction_fail.user = eligible_member
    interaction_fail.response = MagicMock()
    interaction_fail.response.defer = AsyncMock()
    interaction_fail.followup = MagicMock()
    interaction_fail.followup.send = AsyncMock()

    await pm_cog.task_assign.callback(
        pm_cog,
        interaction=interaction_fail,
        task=task.short_id,
        assignee=ineligible_member,
    )

    interaction_fail.followup.send.assert_awaited_once()
    fail_msg = interaction_fail.followup.send.await_args.args[0]
    assert "does not hold an eligible squad Discord role for project 'Frontend App'" in fail_msg

    # 3. Dynamic button assignee selection with ineligible member fails
    dgg_bot = DggPmBot(task_service=task_srv, project_service=proj_srv, squad_service=squad_srv)

    dynamic_fail = MagicMock(spec=discord.Interaction)
    dynamic_fail.guild_id = guild_id
    dynamic_fail.guild = MagicMock()
    dynamic_fail.guild.get_member.return_value = ineligible_member
    dynamic_fail.user = eligible_member
    dynamic_fail.data = {"values": ["8002"]}
    dynamic_fail.response = MagicMock()
    dynamic_fail.response.is_done.return_value = False
    dynamic_fail.response.send_message = AsyncMock()

    await dgg_bot._handle_dynamic_task_button(dynamic_fail, "assignee", task.id)
    dynamic_fail.response.send_message.assert_awaited_once()
    dyn_err = dynamic_fail.response.send_message.await_args.args[0]
    assert "does not hold an eligible squad Discord role for project 'Frontend App'" in dyn_err


@pytest.mark.asyncio
async def test_task_cog_watchers_self_service_vs_third_party(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990007

    bot = MagicMock()
    pm_cog = PmCog(
        bot=bot,
        project_service=proj_srv,
        squad_service=squad_srv,
        task_service=task_srv,
        auth_service=auth_srv,
    )

    project = await proj_srv.create_project(guild_id=guild_id, name="Core Lib", prefix="LIB")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Watcher Testing",
        creator_discord_id=1001,
        project_id=project.id,
    )

    # 1. Any member can add themselves as a watcher
    user_self = _make_mock_member(6001)
    interaction_self = MagicMock(spec=discord.Interaction)
    interaction_self.guild = MagicMock()
    interaction_self.guild.id = guild_id
    interaction_self.user = user_self
    interaction_self.response = MagicMock()
    interaction_self.response.defer = AsyncMock()
    interaction_self.followup = MagicMock()
    interaction_self.followup.send = AsyncMock()

    await pm_cog.task_watchers.callback(
        pm_cog,
        interaction=interaction_self,
        task=task.short_id,
        action="add",
        member=user_self,
    )

    interaction_self.followup.send.assert_awaited_once()
    assert "Updated watchers" in interaction_self.followup.send.await_args.args[0]

    # 2. Unauthorized user cannot clear all watchers
    interaction_clear = MagicMock(spec=discord.Interaction)
    interaction_clear.guild = MagicMock()
    interaction_clear.guild.id = guild_id
    interaction_clear.user = user_self
    interaction_clear.response = MagicMock()
    interaction_clear.response.defer = AsyncMock()
    interaction_clear.followup = MagicMock()
    interaction_clear.followup.send = AsyncMock()

    await pm_cog.task_watchers.callback(
        pm_cog,
        interaction=interaction_clear,
        task=task.short_id,
        action="clear",
        member=None,
    )

    interaction_clear.followup.send.assert_awaited_once()
    assert "You do not have permission" in interaction_clear.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_modal_authorization_rejection(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990008

    project = await proj_srv.create_project(guild_id=guild_id, name="Secure Vault", prefix="VAU")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Restricted Task",
        creator_discord_id=1001,
        project_id=project.id,
    )

    unauth_member = _make_mock_member(7001)

    # Note modal rejection
    note_modal = TaskNoteModal(task_id=task.id, short_id=task.short_id, task_service=task_srv, auth_service=auth_srv)
    note_modal.note_input._value = "Attempted note from unauthorized user"

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = unauth_member
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await note_modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "You do not have permission" in msg

    # Edit modal rejection
    edit_modal = TaskEditModal(task=task, task_service=task_srv, auth_service=auth_srv)
    edit_modal.title_input._value = "Hacked Title"

    await edit_modal.on_submit(interaction)
    msg2 = interaction.response.send_message.await_args.args[0]
    assert "You do not have permission" in msg2


@pytest.mark.asyncio
async def test_dynamic_button_authorization_rejection(services):
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    guild_id = 9990009

    bot = DggPmBot(
        task_service=task_srv,
        project_service=proj_srv,
        squad_service=squad_srv,
    )

    project = await proj_srv.create_project(guild_id=guild_id, name="Project Gate", prefix="GAT")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Gated Operation",
        creator_discord_id=1001,
        project_id=project.id,
    )

    unauth_interaction = MagicMock(spec=discord.Interaction)
    unauth_interaction.guild_id = guild_id
    unauth_interaction.user = _make_mock_member(8001)
    unauth_interaction.response = MagicMock()
    unauth_interaction.response.is_done.return_value = False
    unauth_interaction.response.send_message = AsyncMock()

    await bot._handle_dynamic_task_button(unauth_interaction, "start", task.id)

    unauth_interaction.response.send_message.assert_awaited_once()
    err_msg = unauth_interaction.response.send_message.await_args.args[0]
    assert "You do not have permission" in err_msg


@pytest.mark.asyncio
async def test_orphaned_squad_lead_permission_denial(services):
    """If a user is in DB as a lead but loses their Discord role, they lose squad lead management powers."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990011

    squad = await squad_srv.create_squad(guild_id=guild_id, name="Security Squad", discord_role_id=888999)
    user_id = 9001
    await squad_srv.add_squad_lead(squad.id, user_id)

    # 1. User with the role has squad lead permissions
    member_with_role = _make_mock_member(user_id, role_ids=[888999])
    assert await auth_srv.can_manage_squad_leads(member_with_role, squad.id) is True

    # 2. User whose role was removed loses squad lead permissions
    member_without_role = _make_mock_member(user_id, role_ids=[111222])  # missing 888999
    assert await auth_srv.can_manage_squad_leads(member_without_role, squad.id) is False

    with pytest.raises(PermissionDeniedError, match="You do not have permission"):
        await auth_srv.require_squad_lead_management(member_without_role, squad.id)


@pytest.mark.asyncio
async def test_on_member_update_auto_prunes_squad_lead(services):
    """Removing a squad's Discord role from a member automatically prunes their DB lead record."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    guild_id = 9990012

    bot = DggPmBot(task_service=task_srv, project_service=proj_srv, squad_service=squad_srv)

    squad_role_id = 333444
    squad = await squad_srv.create_squad(guild_id=guild_id, name="Platform Squad", discord_role_id=squad_role_id)
    user_id = 9002
    await squad_srv.add_squad_lead(squad.id, user_id)
    assert await squad_srv.is_squad_lead(squad.id, user_id) is True

    mock_guild = MagicMock()
    mock_guild.id = guild_id

    before_member = _make_mock_member(user_id, role_ids=[squad_role_id])
    before_member.guild = mock_guild
    after_member = _make_mock_member(user_id, role_ids=[])
    after_member.guild = mock_guild

    await bot.on_member_update(before_member, after_member)

    # Verify DB lead record was automatically removed
    assert await squad_srv.is_squad_lead(squad.id, user_id) is False


@pytest.mark.asyncio
async def test_on_member_remove_auto_prunes_squad_lead(services):
    """When a member leaves the server, all their DB squad lead records in that guild are cleaned up."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    guild_id = 9990013

    bot = DggPmBot(task_service=task_srv, project_service=proj_srv, squad_service=squad_srv)

    squad1 = await squad_srv.create_squad(guild_id=guild_id, name="Frontend Squad", discord_role_id=111)
    squad2 = await squad_srv.create_squad(guild_id=guild_id, name="Backend Squad", discord_role_id=222)
    user_id = 9003

    await squad_srv.add_squad_lead(squad1.id, user_id)
    await squad_srv.add_squad_lead(squad2.id, user_id)

    assert await squad_srv.is_squad_lead(squad1.id, user_id) is True
    assert await squad_srv.is_squad_lead(squad2.id, user_id) is True

    mock_guild = MagicMock()
    mock_guild.id = guild_id
    leaving_member = _make_mock_member(user_id)
    leaving_member.guild = mock_guild

    await bot.on_member_remove(leaving_member)

    # Verify DB records across both squads were automatically cleaned up
    assert await squad_srv.is_squad_lead(squad1.id, user_id) is False
    assert await squad_srv.is_squad_lead(squad2.id, user_id) is False


@pytest.mark.asyncio
async def test_project_lead_authorization(services):
    """Verify Project Leads have elevated permissions to create, assign, and mutate tasks in their projects."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9990014

    lead_id = 6001
    contributor_id = 6002
    outsider_id = 6003
    role_id = 888111

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Apollo Mission",
        prefix="APO",
        discord_role_id=role_id,
        lead_discord_id=lead_id,
    )

    lead_member = _make_mock_member(lead_id, role_ids=[])  # Lead without squad role
    contributor_member = _make_mock_member(contributor_id, role_ids=[role_id])
    outsider_member = _make_mock_member(outsider_id, role_ids=[])

    # 1. Check is_project_lead
    assert await auth_srv.is_project_lead(lead_member, project.id) is True
    assert await auth_srv.is_project_lead(contributor_member, project.id) is False
    assert await auth_srv.is_project_lead(outsider_member, project.id) is False

    # 2. Check task creation in project
    assert await auth_srv.can_create_task_in_project(lead_member, project.id) is True
    assert await auth_srv.can_create_task_in_project(contributor_member, project.id) is True
    assert await auth_srv.can_create_task_in_project(outsider_member, project.id) is False

    # 3. Check assignment eligibility
    mock_guild = MagicMock()
    mock_guild.get_member.side_effect = lambda uid: {
        lead_id: lead_member,
        contributor_id: contributor_member,
        outsider_id: outsider_member,
    }.get(uid)

    assert await auth_srv.can_assign_task_to_user(mock_guild, lead_id, project.id) is True
    assert await auth_srv.can_assign_task_to_user(mock_guild, contributor_id, project.id) is True
    assert await auth_srv.can_assign_task_to_user(mock_guild, outsider_id, project.id) is False

    # 4. Check task mutation
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Lunar Landing",
        creator_discord_id=contributor_id,
        assignee_discord_id=contributor_id,
        project_id=project.id,
    )

    # Lead can mutate contributor's task
    assert await auth_srv.can_mutate_task(lead_member, task) is True
    # Contributor can mutate own task
    assert await auth_srv.can_mutate_task(contributor_member, task) is True
    # Outsider cannot mutate task
    assert await auth_srv.can_mutate_task(outsider_member, task) is False


@pytest.mark.asyncio
async def test_permission_aware_project_menu_ui(services):
    """Test that ProjectMenuView dynamic elements show/hide based on server manager permission."""
    from src.adapters.discord_bot.views.project_menu import ProjectMenuView, build_project_menu_embed

    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]

    # 1. Server Manager view
    manager_member = _make_mock_member(1001, manage_guild=True)
    manager_view = ProjectMenuView(proj_srv, squad_srv, task_srv, user=manager_member)
    assert manager_view.is_server_manager is True
    assert len(manager_view.children) == 9
    assert manager_view.new_project_btn is not None
    assert manager_view.set_role_btn is not None
    assert manager_view.set_lead_btn is not None
    assert manager_view.archive_btn is not None
    assert manager_view.restore_btn is not None
    assert manager_view.rebuild_btn is not None
    assert manager_view.list_projects_btn is not None
    assert manager_view.tech_tree_btn is not None
    assert manager_view.hub_btn is not None

    manager_embed = build_project_menu_embed(is_server_manager=True)
    assert "New Project" in manager_embed.description
    assert "Set Squad Role" in manager_embed.description
    assert "Archive Project" in manager_embed.description
    assert "Rebuild Workspace" in manager_embed.description

    # 2. Non-server Manager view
    regular_member = _make_mock_member(2002, manage_guild=False, administrator=False)
    regular_view = ProjectMenuView(proj_srv, squad_srv, task_srv, user=regular_member)
    assert regular_view.is_server_manager is False
    assert len(regular_view.children) == 3  # Active Projects, Tech Tree, and PM Main Menu
    assert regular_view.new_project_btn is None
    assert regular_view.set_role_btn is None
    assert regular_view.set_lead_btn is None
    assert regular_view.archive_btn is None
    assert regular_view.restore_btn is None
    assert regular_view.list_projects_btn is not None
    assert regular_view.tech_tree_btn is not None
    assert regular_view.hub_btn is not None

    regular_embed = build_project_menu_embed(is_server_manager=False)
    assert "Active Projects" in regular_embed.description
    assert "New Project" not in regular_embed.description
    assert "Set Squad Role" not in regular_embed.description
    assert "Archive Project" not in regular_embed.description


@pytest.mark.asyncio
async def test_permission_aware_squad_menu_ui(services):
    """Test that SquadMenuView dynamic buttons adapt based on manager/lead permissions."""
    from src.adapters.discord_bot.views.squad_menu import SquadMenuView, build_squad_menu_embed

    squad_srv = services["squad"]
    proj_srv = services["project"]
    task_srv = services["task"]

    # 1. Server Manager (can create squads, can assign members)
    manager_view = SquadMenuView(
        squad_srv,
        proj_srv,
        task_srv,
        can_create_squads=True,
        can_assign_members=True,
    )
    assert len(manager_view.children) == 4
    assert manager_view.create_squad_btn is not None
    assert manager_view.assign_member_btn is not None
    assert manager_view.list_squads_btn is not None
    assert manager_view.hub_btn is not None

    manager_embed = build_squad_menu_embed(can_create_squads=True, can_assign_members=True)
    assert "Create Squad" in manager_embed.description
    assert "Assign Member" in manager_embed.description
    assert "Squad Roster" in manager_embed.description

    # 2. Squad Lead (cannot create squads, can assign members)
    lead_view = SquadMenuView(
        squad_srv,
        proj_srv,
        task_srv,
        can_create_squads=False,
        can_assign_members=True,
    )
    assert len(lead_view.children) == 3
    assert lead_view.create_squad_btn is None
    assert lead_view.assign_member_btn is not None
    assert lead_view.list_squads_btn is not None
    assert lead_view.hub_btn is not None

    lead_embed = build_squad_menu_embed(can_create_squads=False, can_assign_members=True)
    assert "Create Squad" not in lead_embed.description
    assert "Assign Member" in lead_embed.description
    assert "Squad Roster" in lead_embed.description

    # 3. Regular Member (cannot create squads, cannot assign members)
    member_view = SquadMenuView(
        squad_srv,
        proj_srv,
        task_srv,
        can_create_squads=False,
        can_assign_members=False,
    )
    assert len(member_view.children) == 2
    assert member_view.create_squad_btn is None
    assert member_view.assign_member_btn is None
    assert member_view.list_squads_btn is not None
    assert member_view.hub_btn is not None

    member_embed = build_squad_menu_embed(can_create_squads=False, can_assign_members=False)
    assert "Create Squad" not in member_embed.description
    assert "Assign Member" not in member_embed.description
    assert "Squad Roster" in member_embed.description


@pytest.mark.asyncio
async def test_permission_aware_task_creation_project_filtering(services):
    """Test that task creation views only offer projects the user has permissions for."""
    from src.adapters.discord_bot.views.hub_menu import PmHubView
    from src.adapters.discord_bot.views.task_menu import TaskMenuView, TaskSelectProjectView

    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990099
    role_squad = 888999

    # Create restricted project and public project
    await proj_srv.create_project(
        guild_id=guild_id,
        name="Secret Squad Ops",
        prefix="SSO",
        discord_role_id=role_squad,
    )
    proj_public = await proj_srv.create_project(
        guild_id=guild_id,
        name="Public Community Tasks",
        prefix="PUB",
    )

    contributor_user = _make_mock_member(3001, role_ids=[role_squad])
    outsider_user = _make_mock_member(3002, role_ids=[])

    # Outsider in PmHubView new_task_btn
    hub_view = PmHubView(proj_srv, squad_srv, task_srv)

    outsider_interaction = MagicMock(spec=discord.Interaction)
    outsider_interaction.guild = MagicMock()
    outsider_interaction.guild.id = guild_id
    outsider_interaction.channel = MagicMock(spec=discord.TextChannel)
    outsider_interaction.channel.id = 11111
    outsider_interaction.channel.parent_id = None
    outsider_interaction.user = outsider_user
    outsider_interaction.response = MagicMock()
    outsider_interaction.response.send_modal = AsyncMock()

    await hub_view.new_task_btn.callback(outsider_interaction)
    # Since outsider only has access to 1 project (proj_public), directly open TaskCreateModal
    outsider_interaction.response.send_modal.assert_awaited_once()
    modal = outsider_interaction.response.send_modal.call_args[0][0]
    assert modal.project.id == proj_public.id

    # Contributor in PmHubView new_task_btn
    contributor_interaction = MagicMock(spec=discord.Interaction)
    contributor_interaction.guild = MagicMock()
    contributor_interaction.guild.id = guild_id
    contributor_interaction.channel = MagicMock(spec=discord.TextChannel)
    contributor_interaction.channel.id = 11111
    contributor_interaction.channel.parent_id = None
    contributor_interaction.user = contributor_user
    contributor_interaction.response = MagicMock()
    contributor_interaction.response.send_message = AsyncMock()

    await hub_view.new_task_btn.callback(contributor_interaction)
    # Contributor has access to 2 projects -> presents project selector view
    contributor_interaction.response.send_message.assert_awaited_once()
    picker_view = contributor_interaction.response.send_message.call_args.kwargs.get("view")
    assert isinstance(picker_view, TaskSelectProjectView)
    assert len(picker_view.all_projects) == 2

    # Outsider in TaskMenuView _on_new_task_clicked
    task_menu_view = TaskMenuView(
        task_srv,
        proj_srv,
        squad_srv,
        auth_service=auth_srv,
    )
    task_menu_interaction = MagicMock(spec=discord.Interaction)
    task_menu_interaction.guild = MagicMock()
    task_menu_interaction.guild.id = guild_id
    task_menu_interaction.channel = MagicMock(spec=discord.TextChannel)
    task_menu_interaction.user = outsider_user
    task_menu_interaction.response = MagicMock()
    task_menu_interaction.response.edit_message = AsyncMock()

    await task_menu_view._on_new_task_clicked(task_menu_interaction)
    task_menu_interaction.response.edit_message.assert_awaited_once()
    called_picker = task_menu_interaction.response.edit_message.call_args.kwargs.get("view")
    assert isinstance(called_picker, TaskSelectProjectView)
    # Filtered down to only 1 allowed project
    assert len(called_picker.all_projects) == 1
    assert called_picker.all_projects[0].id == proj_public.id


@pytest.mark.asyncio
async def test_auth_service_standalone_task_permissions_option_b(services):
    """Verify that under Option B, standalone tasks are restricted to Server Managers & Squad Leads."""
    from src.adapters.discord_bot.views.task_menu import TaskMenuView

    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 8880001
    # Create a squad with a squad lead
    squad = await squad_srv.create_squad(guild_id=guild_id, name="Security Squad", discord_role_id=888801)
    squad_lead_user_id = 4001
    await squad_srv.add_squad_lead(squad.id, squad_lead_user_id)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id

    squad_lead_member = _make_mock_member(squad_lead_user_id)
    squad_lead_member.guild = mock_guild

    admin_member = _make_mock_member(4002, is_admin=True)
    admin_member.guild = mock_guild

    regular_member = _make_mock_member(4003)
    regular_member.guild = mock_guild

    # 1. Admin / Server Manager is authorized for standalone tasks
    assert await auth_srv.can_create_task_in_project(admin_member, None) is True

    # 2. Squad Lead is authorized for standalone tasks
    assert await auth_srv.can_create_task_in_project(squad_lead_member, None) is True

    # 3. Regular member is rejected for standalone tasks
    assert await auth_srv.can_create_task_in_project(regular_member, None) is False

    with pytest.raises(PermissionDeniedError) as exc_info:
        await auth_srv.require_task_creation(regular_member, None)
    assert "You do not have permission to create standalone tasks" in str(exc_info.value)

    # 4. TaskMenuView UI gating: regular member does not see standalone_btn
    regular_view = TaskMenuView(
        task_srv,
        proj_srv,
        squad_srv,
        auth_service=auth_srv,
        can_create_standalone=False,
    )
    assert not hasattr(regular_view, "standalone_btn")

    # 5. TaskMenuView UI gating: admin/lead sees standalone_btn
    lead_view = TaskMenuView(
        task_srv,
        proj_srv,
        squad_srv,
        auth_service=auth_srv,
        can_create_standalone=True,
    )
    assert hasattr(lead_view, "standalone_btn")
    assert lead_view.standalone_btn in lead_view.children

    # 6. If regular member somehow clicks standalone_btn callback, it rejects with ephemeral message
    bad_interaction = MagicMock(spec=discord.Interaction)
    bad_interaction.guild = mock_guild
    bad_interaction.user = regular_member
    bad_interaction.response = MagicMock()
    bad_interaction.response.send_message = AsyncMock()

    await lead_view.standalone_btn.callback(bad_interaction)
    bad_interaction.response.send_message.assert_awaited_once()
    assert "Only Server Managers and Squad Leads" in bad_interaction.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_tech_tree_visual_graph_permission_gating(services):
    """Verify that Visual Graph / Tech Tree viewing is gated by project role, lead, or server manager."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)
    guild_id = 9999111122

    # Create project with squad role 5050 and project lead 7070
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 5050
    mock_role.name = "Infra Squad"
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Infra Engine",
        prefix="INF",
        discord_role_id=5050,
        lead_discord_id=7070,
    )
    # Seed a task
    await task_srv.create_task(
        guild_id=guild_id,
        title="Setup K8s",
        creator_discord_id=7070,
        project_id=project.id,
    )

    # 1. Server Manager -> Authorized
    admin = _make_mock_member(1001, is_admin=True)
    assert await auth_srv.can_view_project(admin, project.id) is True

    # 2. Project Lead -> Authorized
    lead = _make_mock_member(7070)
    assert await auth_srv.can_view_project(lead, project.id) is True

    # 3. Squad Role Member -> Authorized
    squad_member = _make_mock_member(3030, role_ids=[5050])
    assert await auth_srv.can_view_project(squad_member, project.id) is True

    # 4. Outsider / Unauthorized Member -> Denied
    outsider = _make_mock_member(4040, role_ids=[9999])
    assert await auth_srv.can_view_project(outsider, project.id) is False
    with pytest.raises(PermissionDeniedError) as exc_info:
        await auth_srv.require_project_view(outsider, project.id)
    assert "You do not have permission to view this project's visual graph" in str(exc_info.value)

    # 5. Verify PmCog /pm tree command permission enforcement
    bot = MagicMock()
    pm_cog = PmCog(
        bot=bot,
        task_service=task_srv,
        project_service=proj_srv,
        squad_service=squad_srv,
        auth_service=auth_srv,
    )

    # Denied interaction for outsider
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_guild.get_member = MagicMock(return_value=None)

    interaction_denied = MagicMock(spec=discord.Interaction)
    interaction_denied.guild = mock_guild
    interaction_denied.user = outsider
    interaction_denied.response = MagicMock()
    interaction_denied.response.defer = AsyncMock()
    interaction_denied.followup = MagicMock()
    interaction_denied.followup.send = AsyncMock()

    await pm_cog.pm_tree.callback(pm_cog, interaction=interaction_denied, project_name="Infra Engine")
    interaction_denied.followup.send.assert_awaited_once()
    assert "You do not have permission" in interaction_denied.followup.send.await_args.args[0]

    # Allowed interaction for squad member
    interaction_allowed = MagicMock(spec=discord.Interaction)
    interaction_allowed.guild = mock_guild
    interaction_allowed.user = squad_member
    interaction_allowed.response = MagicMock()
    interaction_allowed.response.defer = AsyncMock()
    interaction_allowed.followup = MagicMock()
    interaction_allowed.followup.send = AsyncMock()

    await pm_cog.pm_tree.callback(pm_cog, interaction=interaction_allowed, project_name="Infra Engine")
    interaction_allowed.followup.send.assert_awaited_once()
    send_kwargs = interaction_allowed.followup.send.await_args.kwargs
    assert send_kwargs.get("embed") is not None
    assert send_kwargs.get("file") is not None


@pytest.mark.asyncio
async def test_can_assign_task_to_user_uncached_member_fallback_fetch(services):
    """Verifies that can_assign_task_to_user fetches uncached members via guild.fetch_member."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990020
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Uncached",
        prefix="UNC",
        discord_role_id=5555,
    )

    uncached_user_id = 2001
    uncached_member = _make_mock_member(uncached_user_id, role_ids=[5555])

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.get_member = MagicMock(return_value=None)
    mock_guild.fetch_member = AsyncMock(return_value=uncached_member)

    # Calling with uncached user ID should fall back to fetch_member and return True
    eligible = await auth_srv.can_assign_task_to_user(mock_guild, uncached_user_id, project.id)

    mock_guild.get_member.assert_called_once_with(uncached_user_id)
    mock_guild.fetch_member.assert_awaited_once_with(uncached_user_id)
    assert eligible is True


@pytest.mark.asyncio
async def test_can_assign_task_to_user_fetch_member_error_handling(services):
    """Verifies that can_assign_task_to_user gracefully handles Discord API errors on fetch_member."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990021
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Error Handling",
        prefix="ERR",
        discord_role_id=5555,
    )

    user_id = 2002
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.get_member = MagicMock(return_value=None)

    # 1. NotFound error
    mock_guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(), "User not found"))
    assert await auth_srv.can_assign_task_to_user(mock_guild, user_id, project.id) is False

    # 2. HTTPException error
    mock_guild.fetch_member = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "Discord API error"))
    assert await auth_srv.can_assign_task_to_user(mock_guild, user_id, project.id) is False


@pytest.mark.asyncio
async def test_require_task_assignee_eligibility_error_message_details(services):
    """Verifies that require_task_assignee_eligibility formats a descriptive error naming the project and roles."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990022
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Apollo",
        prefix="APO",
        discord_role_ids=[7001, 7002],
    )

    ineligible_user_id = 2003
    ineligible_member = _make_mock_member(ineligible_user_id, role_ids=[9999])

    role_7001 = MagicMock(spec=discord.Role)
    role_7001.id = 7001
    role_7001.name = "Engineering"

    role_7002 = MagicMock(spec=discord.Role)
    role_7002.id = 7002
    role_7002.name = "Design"

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_guild.get_member = MagicMock(return_value=ineligible_member)
    mock_guild.get_role.side_effect = lambda rid: {
        7001: role_7001,
        7002: role_7002,
    }.get(rid)

    with pytest.raises(PermissionDeniedError) as exc_info:
        await auth_srv.require_task_assignee_eligibility(mock_guild, ineligible_member, project.id)

    err_msg = str(exc_info.value)
    assert f"<@{ineligible_user_id}>" in err_msg
    assert "Project Apollo" in err_msg
    assert "@Engineering" in err_msg
    assert "@Design" in err_msg
    assert "Project Lead" in err_msg


@pytest.mark.asyncio
async def test_can_assign_task_to_user_project_lead_exempt(services):
    """Verifies that Project Leads are always eligible for assignment without squad roles or member fetching."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990023
    lead_id = 1111
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Lead Exemption",
        prefix="PLE",
        discord_role_ids=[8888],
        lead_discord_id=lead_id,
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.get_member = MagicMock(return_value=None)
    mock_guild.fetch_member = AsyncMock()

    # 1. Check can_assign_task_to_user returns True without fetching member
    eligible = await auth_srv.can_assign_task_to_user(mock_guild, lead_id, project.id)
    assert eligible is True
    mock_guild.fetch_member.assert_not_awaited()

    # 2. Check require_task_assignee_eligibility does not raise
    await auth_srv.require_task_assignee_eligibility(mock_guild, lead_id, project.id)


@pytest.mark.asyncio
async def test_task_action_controls_ineligible_assignee_error_feedback(services):
    """Verifies that selecting an ineligible member in TaskActionControlsView catches PermissionDeniedError,
    responds immediately with an ephemeral error message, and preserves the prior assignee."""
    from src.adapters.discord_bot.views.task_buttons import TaskActionControlsView

    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990024
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Phoenix",
        prefix="PHX",
        discord_role_ids=[3333],
    )

    prior_assignee_id = 1001
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Phoenix Core",
        creator_discord_id=prior_assignee_id,
        assignee_discord_id=prior_assignee_id,
        project_id=project.id,
    )

    view = TaskActionControlsView(task, task_srv, auth_srv)

    ineligible_id = 9999
    ineligible_member = _make_mock_member(ineligible_id, role_ids=[1234])

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id
    mock_guild.get_member.return_value = ineligible_member

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = mock_guild
    interaction.user = _make_mock_member(prior_assignee_id)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.edit_message = AsyncMock()

    # User selected an ineligible member in the select component
    view.assignee_select._values = [ineligible_member]

    await view._on_assignee_selected(interaction)

    # 1. Ephemeral card edited in-place with error banner and red color
    interaction.response.edit_message.assert_awaited_once()
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "does not hold an eligible squad Discord role for project 'Project Phoenix'" in embed.description
    assert embed.color == discord.Color.red()

    # 2. Staged assignee reset to prior assignee; task in db unchanged
    assert view.staged_assignee_id == prior_assignee_id
    assert view.task.assignee_discord_id == prior_assignee_id
    db_task = await task_srv.get_by_id(task.id)
    assert db_task.assignee_discord_id == prior_assignee_id

    # 3. Dropdown reconstructed with canonical default (resetting invalid selection)
    assert len(view.assignee_select.default_values) == 1
    assert view.assignee_select.default_values[0].id == prior_assignee_id


@pytest.mark.asyncio
async def test_task_action_controls_save_and_cancel_workflow(services):
    """Verifies that Quick Controls stages changes across dropdowns and commits them on Save,
    while Cancel dismisses without persisting."""
    from src.adapters.discord_bot.views.task_buttons import TaskActionControlsView

    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 9990025
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Project Confirm",
        prefix="CFM",
        discord_role_ids=[3333],
    )

    prior_assignee_id = 1001
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Confirm Workflow Test",
        creator_discord_id=prior_assignee_id,
        assignee_discord_id=prior_assignee_id,
        project_id=project.id,
    )

    view = TaskActionControlsView(task, task_srv, auth_srv)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = guild_id

    interaction_clear = MagicMock(spec=discord.Interaction)
    interaction_clear.guild = mock_guild
    interaction_clear.user = _make_mock_member(prior_assignee_id)
    interaction_clear.response = MagicMock()
    interaction_clear.response.edit_message = AsyncMock()

    # 1. User clears assignee (staged, not yet saved to DB)
    view.assignee_select._values = []
    await view._on_assignee_selected(interaction_clear)
    interaction_clear.response.edit_message.assert_awaited_once()
    assert view.staged_assignee_id is None
    db_task = await task_srv.get_by_id(task.id)
    assert db_task.assignee_discord_id == prior_assignee_id  # Not yet committed!

    # 2. User changes priority to High (staged)
    interaction_prio = MagicMock(spec=discord.Interaction)
    interaction_prio.guild = mock_guild
    interaction_prio.user = _make_mock_member(prior_assignee_id)
    interaction_prio.response = MagicMock()
    interaction_prio.response.edit_message = AsyncMock()

    view.priority_select._values = ["high"]
    await view._on_priority_selected(interaction_prio)
    assert view.staged_priority == PriorityLevel.HIGH

    # 3. User clicks Save Changes -> commits all staged changes atomically
    interaction_save = MagicMock(spec=discord.Interaction)
    interaction_save.guild = mock_guild
    interaction_save.user = _make_mock_member(prior_assignee_id)
    interaction_save.channel = MagicMock(spec=discord.Thread)
    interaction_save.response = MagicMock()
    interaction_save.response.edit_message = AsyncMock()

    await view._on_save_clicked(interaction_save)
    interaction_save.response.edit_message.assert_awaited_once()
    saved_embed = interaction_save.response.edit_message.await_args.kwargs["embed"]
    assert "Updated" in saved_embed.title
    assert saved_embed.color == discord.Color.green()

    db_task = await task_srv.get_by_id(task.id)
    assert db_task.assignee_discord_id is None
    assert db_task.priority == PriorityLevel.HIGH

    # 4. Cancel button calls delete_original_response
    interaction_cancel = MagicMock(spec=discord.Interaction)
    interaction_cancel.guild_id = guild_id
    interaction_cancel.user = _make_mock_member(prior_assignee_id)
    interaction_cancel.response = MagicMock()
    interaction_cancel.response.defer = AsyncMock()
    interaction_cancel.delete_original_response = AsyncMock()

    cancel_view = TaskActionControlsView(task, task_srv, auth_srv)
    await cancel_view._on_cancel_clicked(interaction_cancel)
    interaction_cancel.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_service_dependency_bypass_permissions(services):
    """AuthService evaluates dependency bypass authority for managers, leads, mutators, and rejects others."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, squad_srv)

    guild_id = 998877881
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Platform Core",
        prefix="COR",
        lead_discord_id=5001,
    )

    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Protected Service",
        creator_discord_id=1001,
        project_id=project.id,
        assignee_discord_id=5002,
    )

    admin_member = _make_mock_member(5000, is_admin=True)
    lead_member = _make_mock_member(5001)
    assignee_member = _make_mock_member(5002)
    unauthorized_member = _make_mock_member(9999)

    # 1. Server Manager bypass
    assert await auth_srv.can_bypass_dependencies(admin_member, task) is True
    await auth_srv.require_dependency_bypass(admin_member, task)

    # 2. Project Lead bypass
    assert await auth_srv.can_bypass_dependencies(lead_member, task) is True
    await auth_srv.require_dependency_bypass(lead_member, task)

    # 3. Assignee bypass
    assert await auth_srv.can_bypass_dependencies(assignee_member, task) is True
    await auth_srv.require_dependency_bypass(assignee_member, task)

    # 4. Unauthorized user rejected
    assert await auth_srv.can_bypass_dependencies(unauthorized_member, task) is False
    with pytest.raises(PermissionDeniedError, match="do not have permission"):
        await auth_srv.require_dependency_bypass(unauthorized_member, task)
