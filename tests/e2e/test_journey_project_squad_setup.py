"""User Journey 1: Project & Squad Setup.

Validates end-to-end provisioning of a project container bound to a Discord Forum
Channel, automatic setup of standard forum tags, creation of the pinned Control Hub,
mapping of the squad role, and designation of a squad lead.
"""

import pytest

from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest.mark.asyncio
async def test_journey_project_and_squad_setup(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    lead_actor: UserJourneyActor,
) -> None:
    # 1. Setup Discord server structure: Forum Channel and Squad Role
    forum = e2e_env.guild.create_forum_channel("platform-board")
    squad_role = e2e_env.guild.create_role("Platform Squad")

    # 2. Server Manager runs /pm project create
    resp = await admin_actor.slash(
        "/pm project create",
        name="Platform Core",
        prefix="PLT",
        role=squad_role,
        channel=forum,
        description="Core platform infrastructure and tooling",
    )
    assert resp.deferred is True
    assert "Created project" in resp.all_text() or "Platform Core" in resp.all_text()

    # 3. Verify Standard PM Tags were provisioned on the Forum Channel
    tag_names = {t.name for t in forum.available_tags}
    expected_tags = {"Not Started", "In Progress", "Completed", "Overdue", "High Priority"}
    assert expected_tags.issubset(tag_names), f"Missing PM tags in forum: {expected_tags - tag_names}"

    # Verify per-project tag exists (📁 Platform Core)
    assert any("Platform Core" in t.name for t in forum.available_tags)

    # 4. Verify the pinned Control Hub thread was created in the Forum Channel
    hub_thread = forum.get_thread_by_name("Control Hub")
    assert hub_thread is not None, "Pinned Control Hub thread was not created in the Forum Channel"
    assert hub_thread.starter_message is not None
    assert hub_thread.starter_message.view is not None

    # 5. Member holds the squad role, and Admin designates them as Squad Lead via /pm project lead
    lead_actor.user.add_role(squad_role)
    lead_resp = await admin_actor.slash(
        "/pm project lead",
        project_name="Platform Core",
        user=lead_actor.user,
        action="add",
    )
    assert "Added" in lead_resp.all_text() or "lead" in lead_resp.all_text().lower()

    # 6. Verify Project Service and DB state
    project = await e2e_env.project_service.get_by_name(e2e_env.guild.id, "Platform Core")
    assert project is not None
    assert project.prefix == "PLT"
    assert project.discord_channel_id == forum.id

    squads = await e2e_env.project_service.list_squads_for_project(project.id)
    assert len(squads) == 1
    assert squads[0].discord_role_id == squad_role.id

    is_lead = await e2e_env.squad_service.is_squad_lead(squads[0].id, lead_actor.user.id)
    assert is_lead is True

    # 7. Member runs /pm project list and verifies the output
    list_resp = await admin_actor.slash("/pm project list")
    assert "Platform Core" in list_resp.all_text()
    assert "PLT" in list_resp.all_text()
