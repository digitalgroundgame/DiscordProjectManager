"""E2E Journey 4: Task Dependencies, Blocker Gating & Tech Tree Rendering.

Validates:
1. Creating prerequisite dependencies between tasks (PLT-1 and PLT-2).
2. Blocker gating preventing completion of blocked task (TaskBlockedConfirmView).
3. Auto-unblocking cascade when prerequisite completes (outbox TASK_UNBLOCKED event -> thread activity).
4. Tech tree rendering with PillowTechTreeRenderer via /pm tree.
5. Interactive layout switching (Horizontal <-> Vertical) on TechTreeViewer.
"""

from __future__ import annotations

import pytest

from src.adapters.discord_bot.views.tree_view import TechTreeViewer
from src.domain.enums import TaskStatus
from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest.mark.asyncio
async def test_journey_dependencies_blocker_gating_and_tech_tree(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    member_actor: UserJourneyActor,
) -> None:
    guild = e2e_env.guild

    # 1. Setup project "Platform Core" (PLT) bound to forum channel with squad role
    forum_channel = guild.create_forum_channel(name="platform-board")
    squad_role = guild.create_role(name="Platform Squad")
    member_actor.user.add_role(squad_role)

    setup_resp = await admin_actor.slash(
        "/pm project create",
        name="Platform Core",
        prefix="PLT",
        channel=forum_channel,
        role=squad_role,
    )
    assert "Platform Core" in setup_resp.all_text()

    # 2. Member creates prerequisite task PLT-1: Database Schema
    t1_resp = await member_actor.slash(
        "/pm task create",
        project_name="Platform Core",
        title="Database Schema",
        description="Design core SQL schema and migrations",
    )
    assert "[PLT-1]" in t1_resp.content
    await e2e_env.drain_outbox()

    task1 = await e2e_env.task_service.get_by_short_id(guild.id, "PLT-1")
    assert task1 is not None
    assert task1.status == TaskStatus.NOT_STARTED

    # 3. Member creates dependent task PLT-2: API Endpoints (depends on PLT-1)
    t2_resp = await member_actor.slash(
        "/pm task create",
        project_name="Platform Core",
        title="API Endpoints",
        description="FastAPI CRUD endpoints",
        depends_on="PLT-1",
    )
    assert "[PLT-2]" in t2_resp.content
    await e2e_env.drain_outbox()

    task2 = await e2e_env.task_service.get_by_short_id(guild.id, "PLT-2")
    assert task2 is not None
    assert task2.status == TaskStatus.NOT_STARTED

    # Verify dependency relationship in service layer
    unresolved_before = await e2e_env.task_service.get_unresolved_prerequisites(task2.id)
    assert len(unresolved_before) == 1
    assert unresolved_before[0].short_id == "PLT-1"

    # Find Discord thread for PLT-2
    plt2_thread = forum_channel.get_thread_by_name("[PLT-2] API Endpoints")
    assert plt2_thread is not None
    assert len(plt2_thread.messages) > 0
    card2_msg = plt2_thread.messages[0]

    # 4. Member attempts to click "Complete" on PLT-2 while PLT-1 is still uncompleted
    # Should trigger TaskBlockedConfirmView warning modal/card
    blocked_warning_resp = await member_actor.click_button(card2_msg, label="Complete")

    # Verify blocked confirmation warning is presented
    assert blocked_warning_resp.embed is not None
    assert "Unresolved Dependencies: [PLT-2]" in blocked_warning_resp.embed.title
    assert "PLT-1" in str(blocked_warning_resp.embed.fields[0].value)

    # Verify task2 remains in NOT_STARTED state (not completed)
    task2_check = await e2e_env.task_service.get_by_id(task2.id)
    assert task2_check.status == TaskStatus.NOT_STARTED

    # 5. Member completes prerequisite task PLT-1
    plt1_thread = forum_channel.get_thread_by_name("[PLT-1] Database Schema")
    assert plt1_thread is not None
    assert len(plt1_thread.messages) > 0
    card1_msg = plt1_thread.messages[0]

    await member_actor.click_button(card1_msg, label="Complete")

    task1_completed = await e2e_env.task_service.get_by_id(task1.id)
    assert task1_completed.status == TaskStatus.COMPLETED

    # 6. Drain outbox: worker processes TASK_UNBLOCKED event
    unblocked_events_count = await e2e_env.drain_outbox()
    assert unblocked_events_count >= 1

    # Verify thread activity message posted to PLT-2 thread informing team that blockers are resolved
    activity_messages = [m.content for m in plt2_thread.messages if m.content]
    expected_msg = "All prerequisite blockers for **[PLT-2] API Endpoints** have completed!"
    assert any(expected_msg in msg for msg in activity_messages)

    # Verify unresolved prerequisites list is now empty
    unresolved_after = await e2e_env.task_service.get_unresolved_prerequisites(task2.id)
    assert len(unresolved_after) == 0

    # 7. Now member completes PLT-2 directly without blocker warning
    # Re-fetch action card message to ensure fresh state
    card2_fresh = plt2_thread.messages[0]
    await member_actor.click_button(card2_fresh, label="Complete")

    task2_completed = await e2e_env.task_service.get_by_id(task2.id)
    assert task2_completed.status == TaskStatus.COMPLETED

    # 8. Member renders project tech tree graph via /pm tree
    tree_resp = await member_actor.slash(
        "/pm tree",
        project_name="Platform Core",
    )
    assert tree_resp.embed is not None
    assert "Tech Tree: [PLT] Platform Core" in tree_resp.embed.title
    assert "Horizontal" in tree_resp.embed.description
    assert len(tree_resp.files) == 1
    assert tree_resp.files[0].filename == "tech_tree.png"
    assert len(tree_resp.files[0].fp.getvalue()) > 0

    viewer_view = tree_resp.view
    assert isinstance(viewer_view, TechTreeViewer)
    assert viewer_view.current_orientation == "lr"

    # 9. Member toggles orientation to Vertical View
    vertical_resp = await member_actor.click_button(tree_resp, label="Vertical View")
    assert viewer_view.current_orientation == "tb"
    assert "Vertical" in vertical_resp.embed.description
    assert len(vertical_resp.files) == 1
    assert vertical_resp.files[0].filename == "tech_tree.png"
    assert len(vertical_resp.files[0].fp.getvalue()) > 0
