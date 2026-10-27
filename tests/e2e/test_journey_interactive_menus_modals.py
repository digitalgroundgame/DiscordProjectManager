"""User Journey 3: Interactive Ephemeral Menus, Task Builder & Notifications.

Validates opening the Task Builder from the pinned Control Hub, submitting initial
details via TaskDetailsModal, configuring priority, due date, assignee, and watchers
in the ephemeral TaskCreateDraftView, creating the task, draining OutboxWorker,
and verifying notification delivery based on personal /pm settings preferences.
"""

import pytest

from src.domain.enums import NotificationPreference, PriorityLevel
from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest.mark.asyncio
async def test_journey_interactive_menus_modals_and_notifications(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    lead_actor: UserJourneyActor,
    member_actor: UserJourneyActor,
) -> None:
    # 1. Setup Forum and Project bound to Platform Squad
    forum = e2e_env.guild.create_forum_channel("platform-board")
    squad_role = e2e_env.guild.create_role("Platform Squad")
    lead_actor.user.add_role(squad_role)
    member_actor.user.add_role(squad_role)

    watcher_actor = e2e_env.create_actor("WatcherUser")
    watcher_actor.user.add_role(squad_role)

    await admin_actor.slash(
        "/pm project create",
        name="Platform Core",
        prefix="PLT",
        role=squad_role,
        channel=forum,
    )

    # 2. Assignee configures notification preference to DM Only via /pm settings
    pref_resp = await lead_actor.slash("/pm settings")
    assert pref_resp.view is not None

    # Click "DM Only" notification preference button
    await lead_actor.click_button(
        pref_resp,
        label="DM Only",
    )
    pref = await e2e_env.user_service.get_preference(e2e_env.guild.id, lead_actor.user.id)
    assert pref == NotificationPreference.DM

    # 3. Member navigates to the pinned Control Hub in the Forum Channel
    hub_thread = forum.get_thread_by_name("Control Hub")
    assert hub_thread is not None
    assert hub_thread.starter_message is not None

    # Member clicks "New Task" (pm_hub:new_task) on the Control Hub starter card
    new_task_resp = await member_actor.click_button(
        hub_thread.starter_message,
        custom_id="pm_hub:new_task",
    )
    assert new_task_resp.modal is not None, "Expected TaskDetailsModal to be returned on clicking New Task"

    # 4. Member fills out the modal with title and description
    draft_resp = await member_actor.submit_modal(
        new_task_resp.modal,
        title_input="Build Metrics Endpoint",
        desc_input="Expose Prometheus metrics at /metrics with auth token",
    )
    assert draft_resp.view is not None, "Expected TaskCreateDraftView after modal submission"
    assert "Draft" in draft_resp.all_text() or "Build Metrics Endpoint" in draft_resp.all_text()

    # 5. Member configures fields in TaskCreateDraftView
    # Select High Priority (row 3)
    await member_actor.select_values(
        draft_resp,
        row=3,
        values=["high"],
    )

    # Select Tomorrow due date preset (row 2)
    await member_actor.select_values(
        draft_resp,
        row=2,
        values=["tomorrow"],
    )

    # Pick Assignee: lead_actor (UserSelect at row 1)
    await member_actor.select_values(
        draft_resp,
        row=1,
        values=[lead_actor.user],
    )

    # Pick Watcher: watcher_actor (UserSelect at row 4)
    await member_actor.select_values(
        draft_resp,
        row=4,
        values=[watcher_actor.user],
    )

    # 6. Member clicks "Create Task" on the draft builder
    final_resp = await member_actor.click_button(draft_resp, label="Create Task")
    assert "Created" in final_resp.all_text() or "PLT-1" in final_resp.all_text()

    # Drain background outbox worker
    await e2e_env.drain_outbox()

    # 7. Verify Task and Forum Thread creation
    task = await e2e_env.task_service.get_by_short_id(e2e_env.guild.id, "PLT-1")
    assert task is not None
    assert task.title == "Build Metrics Endpoint"
    assert task.priority == PriorityLevel.HIGH
    assert task.assignee_discord_id == lead_actor.user.id
    assert watcher_actor.user.id in task.watchers
    assert task.due_at is not None

    thread = forum.get_thread_by_name("PLT-1")
    assert thread is not None
    thread_tags = {t.name for t in thread.applied_tags}
    assert "High Priority" in thread_tags
    assert "Not Started" in thread_tags

    # 8. Verify DM notification was delivered to lead_actor (due to DM Only preference)
    assert len(lead_actor.user.dm_messages) >= 1
    dm_parts = [m.content + " " + " ".join(e.title or "" for e in m.embeds) for m in lead_actor.user.dm_messages]
    dm_text = "\n".join(dm_parts)
    assert "PLT-1" in dm_text or "Build Metrics Endpoint" in dm_text
