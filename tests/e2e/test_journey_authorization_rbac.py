"""E2E Journey 5: Authorization, Permissions & Role-Based Access Control (RBAC).

Validates:
1. Outsider cannot run administrative commands (fails Discord check / MissingPermissions).
2. Outsider cannot create projects without server manager, squad lead, or lead role.
3. Outsider cannot create tasks in projects where they lack squad membership.
4. Outsider cannot claim tasks belonging to a squad they do not belong to.
5. Designated squad leads can create and assign tasks to members.
6. Server managers can grant authorized Team Lead roles via /pm admin lead-role,
   allowing newly authorized members to manage and create projects.
"""

from __future__ import annotations

import pytest

from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest.mark.asyncio
async def test_journey_outsider_unauthorized_boundaries(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    member_actor: UserJourneyActor,
    outsider_actor: UserJourneyActor,
) -> None:
    guild = e2e_env.guild
    forum_channel = guild.create_forum_channel(name="security-board")
    squad_role = guild.create_role(name="Security Squad")
    member_actor.user.add_role(squad_role)

    # 1. Outsider attempts to run /pm admin lead-role (requires manage_guild permission)
    admin_probe_resp = await outsider_actor.slash(
        "/pm admin lead-role",
        action="list",
    )
    assert any(
        err in admin_probe_resp.all_text()
        for err in ("missing required Discord permission", "permission", "Check failure", "❌")
    )

    # 2. Outsider attempts to run /pm project create without permissions
    proj_create_resp = await outsider_actor.slash(
        "/pm project create",
        name="Rogue Project",
        prefix="ROG",
        channel=forum_channel,
        role=squad_role,
    )
    assert "You do not have permission to manage projects" in proj_create_resp.all_text()

    # Verify project was not created
    rogue_proj = await e2e_env.project_service.get_by_name(guild.id, "Rogue Project")
    assert rogue_proj is None

    # 3. Legitimate Server Manager creates "Security Core" project
    setup_resp = await admin_actor.slash(
        "/pm project create",
        name="Security Core",
        prefix="SEC",
        channel=forum_channel,
        role=squad_role,
    )
    assert "Security Core" in setup_resp.all_text()

    # 4. Outsider attempts to create a task in "Security Core"
    task_create_resp = await outsider_actor.slash(
        "/pm task create",
        project_name="Security Core",
        title="Unauthorized Task Injection",
    )
    assert "You do not have permission to create tasks in this project" in task_create_resp.all_text()

    # 5. Member actor creates legitimate unassigned task SEC-1
    sec1_resp = await member_actor.slash(
        "/pm task create",
        project_name="Security Core",
        title="Audit Security Logs",
        description="Verify SIEM log ingestion",
    )
    assert "[SEC-1]" in sec1_resp.content
    await e2e_env.drain_outbox()

    sec1_task = await e2e_env.task_service.get_by_short_id(guild.id, "SEC-1")
    assert sec1_task is not None
    assert sec1_task.assignee_discord_id is None

    # 6. Outsider attempts to click "Claim Task" on SEC-1 action card
    sec1_thread = forum_channel.get_thread_by_name("[SEC-1] Audit Security Logs")
    assert sec1_thread is not None
    sec1_card = sec1_thread.messages[0]

    claim_resp = await outsider_actor.click_button(sec1_card, label="Claim Task")
    assert any(err in claim_resp.all_text() for err in ("eligible squad role", "permission", "❌"))

    # Verify task remains unassigned
    sec1_fresh = await e2e_env.task_service.get_by_id(sec1_task.id)
    assert sec1_fresh.assignee_discord_id is None


@pytest.mark.asyncio
async def test_journey_lead_delegation_and_role_escalation(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    lead_actor: UserJourneyActor,
    member_actor: UserJourneyActor,
    outsider_actor: UserJourneyActor,
) -> None:
    guild = e2e_env.guild
    forum_channel = guild.create_forum_channel(name="infra-board")
    squad_role = guild.create_role(name="Infra Squad")
    member_actor.user.add_role(squad_role)
    lead_actor.user.add_role(squad_role)

    # 1. Admin creates "Infra Core" project
    await admin_actor.slash(
        "/pm project create",
        name="Infra Core",
        prefix="INF",
        channel=forum_channel,
        role=squad_role,
    )

    # 2. Admin designates lead_actor as Squad Lead
    lead_add_resp = await admin_actor.slash(
        "/pm project lead",
        project_name="Infra Core",
        user=lead_actor.user,
        action="add",
    )
    assert "Lead" in lead_add_resp.all_text() or "✅" in lead_add_resp.all_text()

    # 3. Squad Lead creates and assigns a task to member_actor
    task_resp = await lead_actor.slash(
        "/pm task create",
        project_name="Infra Core",
        title="Deploy Kubernetes Ingress",
        assignee=member_actor.user,
    )
    assert "[INF-1]" in task_resp.content
    await e2e_env.drain_outbox()

    inf1_task = await e2e_env.task_service.get_by_short_id(guild.id, "INF-1")
    assert inf1_task is not None
    assert inf1_task.assignee_discord_id == member_actor.user.id

    # 4. Admin configures a custom Team Lead role
    custom_lead_role = guild.create_role(name="Director of Engineering")
    role_grant_resp = await admin_actor.slash(
        "/pm admin lead-role",
        action="add",
        role=custom_lead_role,
    )
    assert "Added Team Lead Role" in role_grant_resp.all_text()

    # 5. Admin assigns this custom role to outsider_actor
    outsider_actor.user.add_role(custom_lead_role)

    # 6. Formerly unauthorized outsider now creates their own project successfully
    special_ops_forum = guild.create_forum_channel(name="special-ops-board")
    special_ops_role = guild.create_role(name="Special Ops Squad")
    outsider_actor.user.add_role(special_ops_role)

    promoted_create_resp = await outsider_actor.slash(
        "/pm project create",
        name="Special Ops",
        prefix="SPO",
        channel=special_ops_forum,
        role=special_ops_role,
    )
    assert "Special Ops" in promoted_create_resp.all_text()

    # Verify project exists in database
    spo_proj = await e2e_env.project_service.get_by_name(guild.id, "Special Ops")
    assert spo_proj is not None
    assert spo_proj.prefix == "SPO"
