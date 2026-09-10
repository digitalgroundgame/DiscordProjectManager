from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter
from src.adapters.discord_bot.views.project_menu import ProjectRoleSelectView
from src.adapters.discord_bot.workspace_protocol import ProjectProvisionSpec
from src.services.auth_service import AuthService


def _make_member(user_id: int, role_ids: list[int] | None = None, is_manager: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    role_mocks = []
    for rid in role_ids or []:
        r = MagicMock(spec=discord.Role)
        r.id = rid
        r.name = f"Role-{rid}"
        role_mocks.append(r)
    member.roles = role_mocks
    perms = MagicMock()
    perms.administrator = is_manager
    perms.manage_guild = is_manager
    member.guild_permissions = perms
    return member


@pytest.mark.asyncio
async def test_multi_role_project_creation_and_persistence(services):
    proj_srv = services["project"]
    guild_id = 7770001
    role_1 = 111222
    role_2 = 333444
    role_3 = 555666

    # 1. Create project with multiple roles
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Multi Squad Initiative",
        prefix="MSQ",
        discord_role_ids=[role_1, role_2, role_3],
    )

    assert set(project.discord_role_ids) == {role_1, role_2, role_3}
    assert project.discord_role_id == role_1  # Backward compatibility property

    # 2. Reload project from DB
    loaded = await proj_srv.get_by_id(project.id)
    assert loaded is not None
    assert set(loaded.discord_role_ids) == {role_1, role_2, role_3}
    assert loaded.discord_role_id in {role_1, role_2, role_3}


@pytest.mark.asyncio
async def test_multi_role_authorization(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv, team_srv)

    guild_id = 7770002
    frontend_role = 101
    backend_role = 102
    qa_role = 103
    unrelated_role = 999

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Fullstack Platform",
        prefix="FSP",
        discord_role_ids=[frontend_role, backend_role],
    )

    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Implement API Endpoint",
        creator_discord_id=2001,
        project_id=project.id,
    )

    frontend_member = _make_member(2002, role_ids=[frontend_role])
    backend_member = _make_member(2003, role_ids=[backend_role])
    qa_member = _make_member(2004, role_ids=[qa_role])
    unrelated_member = _make_member(2005, role_ids=[unrelated_role])

    # Frontend member has access
    assert await auth_srv.can_create_task_in_project(frontend_member, project.id) is True
    assert await auth_srv.can_mutate_task(frontend_member, task) is True
    assert await auth_srv.can_view_project(frontend_member, project.id) is True
    assert await auth_srv.can_assign_task_to_user(None, frontend_member, project.id) is True

    # Backend member has access
    assert await auth_srv.can_create_task_in_project(backend_member, project.id) is True
    assert await auth_srv.can_mutate_task(backend_member, task) is True
    assert await auth_srv.can_view_project(backend_member, project.id) is True
    assert await auth_srv.can_assign_task_to_user(None, backend_member, project.id) is True

    # QA member currently does not have access
    assert await auth_srv.can_create_task_in_project(qa_member, project.id) is False
    assert await auth_srv.can_mutate_task(qa_member, task) is False
    assert await auth_srv.can_view_project(qa_member, project.id) is False
    assert await auth_srv.can_assign_task_to_user(None, qa_member, project.id) is False

    # Unrelated member has no access
    assert await auth_srv.can_create_task_in_project(unrelated_member, project.id) is False

    # Dynamically assign QA squad to the project
    qa_team = await team_srv.get_or_create_team_for_role(guild_id, qa_role, "QA Squad")
    await proj_srv.assign_team_to_project(project.id, qa_team.id)

    # QA member now has access
    assert await auth_srv.can_create_task_in_project(qa_member, project.id) is True
    assert await auth_srv.can_mutate_task(qa_member, task) is True
    assert await auth_srv.can_view_project(qa_member, project.id) is True

    # Remove frontend squad
    fe_team = await team_srv.get_by_role_id(guild_id, frontend_role)
    assert fe_team is not None
    await proj_srv.remove_team_from_project(project.id, fe_team.id)

    # Frontend member lost access, but backend still has access
    assert await auth_srv.can_create_task_in_project(frontend_member, project.id) is False
    assert await auth_srv.can_create_task_in_project(backend_member, project.id) is True


@pytest.mark.asyncio
async def test_project_workspace_provision_spec_multiple_roles(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    mock_bot = MagicMock()
    workspace_adapter = DiscordProjectWorkspaceAdapter(
        bot=mock_bot,
        project_service=proj_srv,
        team_service=team_srv,
        task_service=task_srv,
        user_service=services["user"],
    )

    guild_id = 7770003
    role_a = MagicMock(spec=discord.Role)
    role_a.id = 8801
    role_a.name = "Designers"

    role_b = MagicMock(spec=discord.Role)
    role_b.id = 8802
    role_b.name = "Engineers"

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 999901
    mock_channel.guild = MagicMock()
    mock_channel.guild.id = guild_id

    spec = ProjectProvisionSpec(
        guild_id=guild_id,
        name="Multi Squad Workspace",
        prefix="MSW",
        roles=[role_a, role_b],
        channel=mock_channel,
    )

    ref = await workspace_adapter.provision_project(spec)
    project = ref.project
    assert set(project.discord_role_ids) == {8801, 8802}

    # Verify both teams were mapped
    teams = await proj_srv.list_teams_for_project(project.id)
    team_role_ids = {t.discord_role_id for t in teams}
    assert 8801 in team_role_ids
    assert 8802 in team_role_ids


@pytest.mark.asyncio
async def test_project_role_select_view_multiple_roles(services):
    proj_srv = services["project"]
    team_srv = services["team"]
    task_srv = services["task"]

    guild_id = 7770004
    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="View Multi Role Project",
        prefix="VMR",
    )

    role_view = ProjectRoleSelectView([project], proj_srv, team_srv, task_srv)

    role_x = MagicMock(spec=discord.Role)
    role_x.id = 901
    role_x.name = "Squad Alpha"

    role_y = MagicMock(spec=discord.Role)
    role_y.id = 902
    role_y.name = "Squad Beta"

    role_view.role_select._values = [role_x, role_y]

    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    inter.guild.id = guild_id
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.response.edit_message = AsyncMock()

    await role_view._on_role_selected(inter)
    await role_view._on_assign_clicked(inter)

    # Both roles should now be mapped to the project
    updated = await proj_srv.get_by_id(project.id)
    assert 901 in updated.discord_role_ids
    assert 902 in updated.discord_role_ids

    # Now remove one role
    role_view.role_select._values = [role_x]
    await role_view._on_role_selected(inter)
    await role_view._on_remove_clicked(inter)

    updated_after_remove = await proj_srv.get_by_id(project.id)
    assert 901 not in updated_after_remove.discord_role_ids
    assert 902 in updated_after_remove.discord_role_ids

    # Clear all roles
    await role_view._on_clear_clicked(inter)
    updated_after_clear = await proj_srv.get_by_id(project.id)
    assert len(updated_after_clear.discord_role_ids) == 0
