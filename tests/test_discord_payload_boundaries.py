"""Boundary stress tests enforcing Discord API hard payload limits.

Validates that Discord Views, Embeds, Modals, and Message Content never breach:
1. Embed Title <= 256 characters.
2. Embed Description <= 4096 characters.
3. Embed Field Name <= 256 characters.
4. Embed Field Value <= 1024 characters.
5. Embed Field Count <= 25 fields.
6. Embed Total Character Count <= 6000 characters.
7. Message Content <= 2000 characters.
8. Select Menu Options <= 25 options.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import discord

from src.adapters.discord_bot.views.hub_embeds import build_hub_welcome_embed
from src.adapters.discord_bot.views.hub_project_select_views import HubTaskProjectSelectView
from src.adapters.discord_bot.views.task_dependency_view import (
    TaskDependencyView,
    build_dependency_embed,
)
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_task_history_embed,
    build_thread_workspace_content,
)
from src.domain.enums import PriorityLevel, TaskHistoryAction, TaskStatus
from src.domain.models import Project, Squad, Task, TaskHistory


def _calculate_total_embed_length(embed: discord.Embed) -> int:
    """Calculate the total character count of an embed according to Discord's 6,000-character ceiling."""
    total = 0
    if embed.title:
        total += len(embed.title)
    if embed.description:
        total += len(embed.description)
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    if embed.author and embed.author.name:
        total += len(embed.author.name)
    for f in embed.fields:
        total += len(f.name) + len(f.value)
    return total


def test_task_embed_handles_massive_description() -> None:
    """Tasks with massive 10,000-character descriptions must truncate cleanly within Discord limits."""
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="STRESS-1",
        title="Task with massive body" * 5,
        body="Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 300,  # ~16,500 chars
        status=TaskStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
    )
    embed = build_task_embed(task)

    assert len(embed.title) <= 256
    assert len(embed.description) <= 4096
    assert _calculate_total_embed_length(embed) <= 6000
    for field in embed.fields:
        assert len(field.name) <= 256
        assert len(field.value) <= 1024


def test_task_embed_handles_hundreds_of_dependencies() -> None:
    """Tasks with hundreds of prerequisites and unlock targets must truncate field values under 1024 chars."""
    prereqs = [
        Task(
            id=uuid4(),
            guild_id=12345,
            creator_discord_id=111,
            short_id=f"PRE-{i}",
            title=f"Prerequisite Dependency Number {i}",
            status=TaskStatus.COMPLETED,
        )
        for i in range(120)
    ]
    dependents = [
        Task(
            id=uuid4(),
            guild_id=12345,
            creator_discord_id=111,
            short_id=f"DEP-{i}",
            title=f"Dependent Task Number {i}",
            status=TaskStatus.NOT_STARTED,
        )
        for i in range(120)
    ]
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="STRESS-2",
        title="Heavily linked DAG node",
        status=TaskStatus.NOT_STARTED,
    )

    embed = build_task_embed(task, prerequisites=prereqs, dependents=dependents)
    dep_field = next(f for f in embed.fields if f.name == "Dependencies")

    assert len(dep_field.value) <= 1024
    assert "... (+" in dep_field.value
    assert _calculate_total_embed_length(embed) <= 6000


def test_task_embed_handles_extreme_watcher_count() -> None:
    """Tasks with dozens of subscribers/watchers must not overflow the Timeline field (<= 1024 chars)."""
    watchers = [100000000000000000 + i for i in range(100)]
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="STRESS-3",
        title="Crowded Task",
        watchers=watchers,
        status=TaskStatus.NOT_STARTED,
    )

    embed = build_task_embed(task)
    timeline_field = next(f for f in embed.fields if f.name == "Timeline & Details")

    assert len(timeline_field.value) <= 1024
    assert "... (+" in timeline_field.value


def test_thread_workspace_content_hard_limit() -> None:
    """Thread workspace message content must never exceed Discord's 2000-character payload ceiling."""
    watchers = [200000000000000000 + i for i in range(80)]
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="STRESS-4",
        title="Thread Root Task",
        body="Discussion details about architecture and integration testing.\n" * 150,  # ~8,400 chars
        watchers=watchers,
        status=TaskStatus.IN_PROGRESS,
    )

    content = build_thread_workspace_content(task)
    assert len(content) <= 2000
    assert "..." in content


def test_task_history_embed_handles_massive_audit_trail() -> None:
    """Tasks with large history records (>100 changes) must preserve audit history within 4096 chars."""
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="STRESS-5",
        title="Old Task with Long History",
        status=TaskStatus.COMPLETED,
    )
    now = datetime.now(UTC)
    history = [
        TaskHistory(
            id=uuid4(),
            task_id=task.id,
            actor_discord_id=300000000000000000 + i,
            action=TaskHistoryAction.STATUS_CHANGE,
            old_status=TaskStatus.NOT_STARTED,
            new_status=TaskStatus.IN_PROGRESS,
            notes=f"Iteration {i}: resolved complex dependencies and updated submodules successfully.",
            created_at=now,
        )
        for i in range(80)
    ]

    embed = build_task_history_embed(task, history)
    assert len(embed.title) <= 256
    assert len(embed.description) <= 4096
    assert "... (" in embed.description
    assert _calculate_total_embed_length(embed) <= 6000


def test_hub_welcome_embed_handles_massive_project_count() -> None:
    """Hub welcome embed must handle channels bound to 40+ projects without breaching 4096 description chars."""
    projects = [
        Project(
            id=uuid4(),
            guild_id=12345,
            name=f"Sub-Project Alpha Engineering Taskforce {i}",
            prefix=f"P{i:02d}",
            description=f"Detailed project scope and requirements specification for squad team {i}.",
        )
        for i in range(40)
    ]
    squads_map = {
        p.id: [
            Squad(id=uuid4(), guild_id=12345, name=f"Squad-{p.prefix}-Core", discord_role_id=900000 + i),
            Squad(id=uuid4(), guild_id=12345, name=f"Squad-{p.prefix}-QA", discord_role_id=910000 + i),
        ]
        for i, p in enumerate(projects)
    }

    embed = build_hub_welcome_embed(
        channel_name="engineering-hub",
        bound_projects=projects,
        project_squads_map=squads_map,
    )

    assert len(embed.description) <= 4096
    assert _calculate_total_embed_length(embed) <= 6000


def test_dependency_embed_handles_extreme_prerequisites_and_dependents() -> None:
    """Dependency view embed must respect 1024-character field limits when lists are large."""
    prereqs = [
        Task(
            id=uuid4(),
            guild_id=12345,
            creator_discord_id=111,
            short_id=f"P-{i}",
            title=f"Prerequisite item with extra detailed descriptive text {i}",
            status=TaskStatus.IN_PROGRESS,
        )
        for i in range(60)
    ]
    dependents = [
        Task(
            id=uuid4(),
            guild_id=12345,
            creator_discord_id=111,
            short_id=f"D-{i}",
            title=f"Dependent item with extra detailed descriptive text {i}",
            status=TaskStatus.NOT_STARTED,
        )
        for i in range(60)
    ]
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="CENTRAL-1",
        title="Central Hub Task",
        status=TaskStatus.IN_PROGRESS,
    )

    embed = build_dependency_embed(task, prereqs, dependents)
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert len(field.name) <= 256
        assert len(field.value) <= 1024
    assert _calculate_total_embed_length(embed) <= 6000


def test_select_menus_cap_at_25_options() -> None:
    """Select dropdowns must cap at <= 25 options to prevent Discord component limits from crashing views."""
    task_service = MagicMock()
    task = Task(
        id=uuid4(),
        guild_id=12345,
        creator_discord_id=111,
        short_id="SEL-1",
        title="Target Task",
        status=TaskStatus.NOT_STARTED,
    )
    sibling_tasks = [
        Task(
            id=uuid4(),
            guild_id=12345,
            creator_discord_id=111,
            short_id=f"SIB-{i}",
            title=f"Sibling Task {i}",
            status=TaskStatus.NOT_STARTED,
        )
        for i in range(60)
    ]

    # TaskDependencyView with 60 sibling tasks
    dep_view = TaskDependencyView(
        task_service=task_service,
        task=task,
        sibling_tasks=sibling_tasks,
        prerequisites=[],
        dependents=[],
    )
    select_items = [item for item in dep_view.children if isinstance(item, discord.ui.Select)]
    assert len(select_items) == 1
    assert len(select_items[0].options) <= 25

    # HubTaskProjectSelectView with 50 projects
    projects = [
        Project(
            id=uuid4(),
            guild_id=12345,
            name=f"Project {i}",
            prefix=f"PRJ{i}",
        )
        for i in range(50)
    ]
    hub_select_view = HubTaskProjectSelectView(
        task_service=task_service,
        channel_projects=projects,
        target_channel=None,
        allow_standalone=True,
    )
    hub_select = next(item for item in hub_select_view.children if isinstance(item, discord.ui.Select))
    assert len(hub_select.options) <= 25
