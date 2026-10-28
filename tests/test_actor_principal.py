"""Unit tests for ActorPrincipal, actor mappers, and pure AuthService actor evaluation."""

from unittest.mock import MagicMock

import discord
import pytest

from src.adapters.discord_bot.actor_mapper import (
    DiscordMemberDirectory,
    actor_from_interaction,
    actor_from_member,
)
from src.domain.actor import ActorPrincipal
from src.domain.exceptions import PermissionDeniedError
from src.services.auth_service import AuthService


def test_actor_principal_creation_and_duck_typing():
    actor = ActorPrincipal(
        user_id=12345,
        guild_id=67890,
        role_ids=frozenset([111, 222]),
        is_guild_admin=True,
    )

    assert actor.user_id == 12345
    assert actor.id == 12345
    assert actor.guild_id == 67890
    assert actor.role_ids == frozenset([111, 222])
    assert actor.is_guild_admin is True

    # Check duck-typing properties
    assert {r.id for r in actor.roles} == {111, 222}
    assert actor.guild_permissions.administrator is True
    assert actor.guild_permissions.manage_guild is True


def test_actor_from_member():
    mock_role1 = MagicMock()
    mock_role1.id = 555
    mock_role2 = MagicMock()
    mock_role2.id = 777

    mock_member = MagicMock(spec=discord.Member)
    mock_member.id = 999
    mock_member.guild = MagicMock(id=888)
    mock_member.roles = [mock_role1, mock_role2]
    mock_member.guild_permissions.administrator = False
    mock_member.guild_permissions.manage_guild = True

    actor = actor_from_member(mock_member)
    assert actor.user_id == 999
    assert actor.guild_id == 888
    assert actor.role_ids == frozenset([555, 777])
    assert actor.is_guild_admin is True


def test_actor_from_interaction():
    mock_inter = MagicMock(spec=discord.Interaction)
    mock_inter.guild_id = 444
    mock_user = MagicMock()
    mock_user.id = 333
    mock_user.guild_permissions.administrator = True
    mock_inter.user = mock_user

    actor = actor_from_interaction(mock_inter)
    assert actor.user_id == 333
    assert actor.guild_id == 444
    assert actor.is_guild_admin is True


@pytest.mark.asyncio
async def test_discord_member_directory():
    bot = MagicMock(spec=discord.Client)
    guild = MagicMock()
    guild.id = 100
    member = MagicMock(spec=discord.Member)
    member.id = 200
    member.guild = guild
    member.roles = []
    member.guild_permissions.administrator = False
    member.guild_permissions.manage_guild = False
    member.display_name = "Alice"

    guild.get_member.return_value = member
    bot.get_guild.return_value = guild

    directory = DiscordMemberDirectory(bot)
    principal = await directory.get_member_principal(100, 200)
    assert principal is not None
    assert principal.user_id == 200
    assert principal.guild_id == 100
    assert principal.is_guild_admin is False

    display_name = await directory.get_member_display_name(100, 200)
    assert display_name == "Alice"


@pytest.mark.asyncio
async def test_auth_service_with_actor_principal(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    auth_srv = AuthService(proj_srv)

    guild_id = 123000
    project = await proj_srv.create_project(guild_id, "Test Actor Proj", "TAP", lead_discord_id=1001)
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Actor Task",
        project_id=project.id,
        creator_discord_id=2001,
        assignee_discord_id=3001,
    )

    lead_actor = ActorPrincipal(user_id=1001, guild_id=guild_id)
    assignee_actor = ActorPrincipal(user_id=3001, guild_id=guild_id)
    stranger_actor = ActorPrincipal(user_id=9999, guild_id=guild_id)

    assert await auth_srv.can_mutate_task(lead_actor, task) is True
    assert await auth_srv.can_mutate_task(assignee_actor, task) is True
    assert await auth_srv.can_mutate_task(stranger_actor, task) is False

    with pytest.raises(PermissionDeniedError) as exc_info:
        await auth_srv.require_task_mutation(stranger_actor, task)
    assert exc_info.value.user_id == 9999
    assert exc_info.value.project_id == project.id
