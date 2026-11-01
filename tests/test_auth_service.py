from __future__ import annotations

import pytest

from src.domain.actor import ActorPrincipal
from src.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_can_view_project_allows_guild_lead_user_and_role(services, repos):
    """Guild Lead users and members holding Guild Lead roles can view restricted projects."""
    proj_srv = services["project"]
    squad_srv = services["squad"]
    lead_role_repo = repos["guild_lead_role"]
    lead_user_repo = repos["guild_lead_user"]
    uow = services["uow"]

    auth_srv = AuthService(
        project_service=proj_srv,
        squad_service=squad_srv,
        guild_lead_role_repo=lead_role_repo,
        guild_lead_user_repo=lead_user_repo,
        uow=uow,
    )

    guild_id = 777001
    restricted_role_id = 555001
    guild_lead_role_id = 555999
    guild_lead_user_id = 888001
    outsider_user_id = 888002

    project = await proj_srv.create_project(
        guild_id=guild_id,
        name="Private Initiative",
        prefix="PRV",
        discord_role_id=restricted_role_id,
    )

    await auth_srv.add_guild_lead_role(guild_id, guild_lead_role_id)
    await auth_srv.add_guild_lead_user(guild_id, guild_lead_user_id)

    # 1. Outsider without squad role or guild lead status cannot view
    outsider = ActorPrincipal(user_id=outsider_user_id, guild_id=guild_id, role_ids=frozenset({111}))
    assert await auth_srv.can_view_project(outsider, project) is False
    assert await auth_srv.filter_visible_projects(outsider, [project]) == []

    # 2. Designated Guild Lead user can view restricted project
    lead_user = ActorPrincipal(user_id=guild_lead_user_id, guild_id=guild_id, role_ids=frozenset())
    assert await auth_srv.can_view_project(lead_user, project) is True
    assert await auth_srv.filter_visible_projects(lead_user, [project]) == [project]

    # 3. Member holding Guild Lead role can view restricted project
    lead_role_holder = ActorPrincipal(
        user_id=outsider_user_id,
        guild_id=guild_id,
        role_ids=frozenset({guild_lead_role_id}),
    )
    assert await auth_srv.can_view_project(lead_role_holder, project) is True
    assert await auth_srv.filter_visible_projects(lead_role_holder, [project]) == [project]
