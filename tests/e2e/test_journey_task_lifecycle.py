"""User Journey 2: Full Task Lifecycle via Slash Commands & Thread Action Buttons.

Validates creating a task thread in a Forum Channel, claiming the task via dynamic
starter card button, moving to In Progress, adding progress notes via Modal,
updating status, completing the task, archiving the thread, and inspecting the audit trail.
"""

import pytest

from src.domain.enums import TaskStatus
from tests.e2e.harness.environment import E2ETestEnvironment
from tests.e2e.harness.interaction_driver import UserJourneyActor


@pytest.mark.asyncio
async def test_journey_task_lifecycle(
    e2e_env: E2ETestEnvironment,
    admin_actor: UserJourneyActor,
    member_actor: UserJourneyActor,
) -> None:
    # 1. Setup Forum and Project bound to Platform Squad
    forum = e2e_env.guild.create_forum_channel("platform-board")
    squad_role = e2e_env.guild.create_role("Platform Squad")
    member_actor.user.add_role(squad_role)

    await admin_actor.slash(
        "/pm project create",
        name="Platform Core",
        prefix="PLT",
        role=squad_role,
        channel=forum,
    )

    # 2. Member creates a new task via /pm task create
    create_resp = await member_actor.slash(
        "/pm task create",
        project_name="Platform Core",
        title="Migrate Auth Service",
        priority="high",
        description="Migrate JWT tokens to asymmetric Ed25519 signatures",
    )
    assert create_resp.deferred is True
    assert "Created task" in create_resp.all_text() or "PLT-1" in create_resp.all_text()

    # Drain background outbox worker
    await e2e_env.drain_outbox()

    # 3. Locate the created Forum Thread
    thread = forum.get_thread_by_name("PLT-1")
    assert thread is not None, "Task forum thread '[PLT-1] Migrate Auth Service' was not created"
    assert thread.starter_message is not None
    assert thread.starter_message.view is not None

    tag_names = {t.name for t in thread.applied_tags}
    assert "Not Started" in tag_names
    assert "High Priority" in tag_names
    assert thread.archived is False

    # 4. Member clicks "Claim Task" on the starter message
    claim_resp = await member_actor.click_button(thread.starter_message, label="Claim Task")
    assert claim_resp is not None
    await e2e_env.drain_outbox()

    # Verify task updated in database
    task = await e2e_env.task_service.get_by_short_id(e2e_env.guild.id, "PLT-1")
    assert task is not None
    assert task.assignee_discord_id == member_actor.user.id

    # Refresh starter message and verify button toggled to "Unassign Me"
    starter_buttons = [b.label for b in thread.starter_message.view.children if hasattr(b, "label")]
    assert "Unassign Me" in starter_buttons

    # 5. Member clicks "In Progress" on the card
    await member_actor.click_button(thread.starter_message, label="In Progress")
    await e2e_env.drain_outbox()

    task = await e2e_env.task_service.get_by_short_id(e2e_env.guild.id, "PLT-1")
    assert task.status == TaskStatus.IN_PROGRESS

    thread_tags = {t.name for t in thread.applied_tags}
    assert "In Progress" in thread_tags

    # 6. Member clicks "Add Note" on the card and receives TaskNoteModal
    note_resp = await member_actor.click_button(thread.starter_message, label="Add Note")
    assert note_resp.modal is not None, "Expected TaskNoteModal to be presented on clicking Add Note"

    # Submit progress update note via Modal
    await member_actor.submit_modal(
        note_resp.modal,
        note_input="Drafted Ed25519 key rotation schema and tested token validation locally.",
    )

    # Verify note in DB
    history = await e2e_env.task_service.get_history(task.id)
    recorded_notes = [h for h in history if h.action == "NOTE_ADDED"]
    assert len(recorded_notes) >= 1
    assert "Ed25519" in (recorded_notes[0].notes or "")

    # 7. Member clicks "Complete" on the action card
    await member_actor.click_button(thread.starter_message, label="Complete")
    await e2e_env.drain_outbox()

    # Verify task status in database is COMPLETED
    task = await e2e_env.task_service.get_by_short_id(e2e_env.guild.id, "PLT-1")
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None

    # Verify Forum Thread state (tags updated to Completed, thread is archived)
    final_tags = {t.name for t in thread.applied_tags}
    assert "Completed" in final_tags
    assert thread.archived is True

    # 8. Check audit trail via /pm task history
    history_resp = await member_actor.slash("/pm task history", task="PLT-1")
    history_text = history_resp.all_text()
    assert "PLT-1" in history_text
    assert "Audit Trail" in history_text or "History" in history_text
