"""Protocols and value types for Discord Workspaces and Thread Lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

import discord

from src.domain.enums import PriorityLevel
from src.domain.models import Project, Squad, Task, TaskHistory

TaskControlPanel = Literal["quick_controls", "dependencies", "history"]
_UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class TaskWorkspaceRef:
    """References to a provisioned Discord Thread Workspace."""

    thread_id: int
    message_id: int
    channel_id: int
    jump_url: str


@dataclass(frozen=True, slots=True)
class SyncWorkspaceResult:
    """Result of synchronizing a Discord thread workspace with a Task's domain state."""

    success: bool
    title_renamed: bool = False
    title_deferred: bool = False
    cooldown_remaining_seconds: float = 0.0

    def __bool__(self) -> bool:
        return self.success


@runtime_checkable
class TaskWorkspacePort(Protocol):
    """Port defining the lifecycle operations for Discord-native task execution workspaces.

    Every task mapped to Discord is anchored in a dedicated Discord Thread
    (either within a ForumChannel or a standard TextChannel). Thread workspaces
    contain an interactive Task Action Card as their starter message.

    Thread Invariant:
        When a task is COMPLETED or ARCHIVED, its thread workspace MUST be kept in
        an archived state in Discord. If a mutation or comment is posted to an archived thread,
        the implementation guarantees safe temporary unarchival and state restoration.
    """

    async def provision_workspace(
        self,
        task: Task,
        *,
        project: Project | None = None,
        target_container: discord.abc.GuildChannel | discord.Thread | int | None = None,
        preferred_channel_id: int | None = None,
    ) -> TaskWorkspaceRef:
        """Provisions a new Discord Thread Workspace and mounts the Task Action Card."""
        ...

    async def sync_workspace(
        self,
        task: Task,
        *,
        project: Project | None = None,
        project_name: str | None = None,
        sync_title: bool = False,
        sync_tags: bool = True,
        sync_archive: bool = True,
        sync_starter_card: bool = True,
    ) -> SyncWorkspaceResult | bool:
        """Synchronizes an existing Thread Workspace with the current Task domain model state."""
        ...

    async def refresh_action_card(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        project: Project | None = None,
        project_name: str | None = None,
    ) -> None:
        """Refreshes the interactive Task Action Card in response to a Discord component interaction."""
        ...

    async def post_activity(
        self,
        task: Task,
        content: str,
        *,
        embed: discord.Embed | None = None,
        rearchive_if_completed: bool = True,
    ) -> discord.Message | None:
        """Posts an activity update, note, or Outbox Event notification into the Task's Thread Workspace."""
        ...

    async def delete_workspace(
        self,
        task: Task,
        *,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Permanently deletes or archives the Discord thread workspace and logs audit notification."""
        ...

    async def render_task_controls(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        panel: TaskControlPanel = "quick_controls",
        prerequisites: list[Task] | None = None,
        dependents: list[Task] | None = None,
        history: list[TaskHistory] | None = None,
        sibling_tasks: list[Task] | None = None,
    ) -> None:
        """Renders interactive ephemeral control panels (Quick Controls, Dependencies, Audit Trail)."""
        ...

    async def handle_action(
        self,
        interaction: discord.Interaction,
        action: str,
        task_id: UUID,
    ) -> None:
        """Dispatches and executes a component interaction action (e.g. from Task Action Card) for a task."""
        ...

    async def save_task_controls(
        self,
        interaction: discord.Interaction,
        task: Task,
        *,
        priority: PriorityLevel | None = None,
        assignee_id: Any = _UNSET,
        due_at: Any = _UNSET,
        clear_due_at: bool = False,
        watchers: list[int] | None = None,
        title: str | None = None,
        body: str | None = None,
        clear_body: bool = False,
    ) -> Task | None:
        """Applies staged task control adjustments atomically, syncing thread tags and action card."""
        ...


# Backwards compatibility alias
ITaskDiscordWorkspace = TaskWorkspacePort


@dataclass(frozen=True, slots=True)
class ProjectProvisionSpec:
    """Input specification for provisioning a Project and its Discord Workspace."""

    guild_id: int
    name: str
    prefix: str | None = None
    role: discord.Role | int | None = None
    roles: list[discord.Role | int] | None = None
    channel: discord.abc.GuildChannel | int | None = None
    lead_discord_id: int | None = None
    description: str | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceRef:
    """Immutable result reference describing the fully provisioned Project and Discord presence."""

    project: Project
    squad: Squad | None = None
    channel_id: int | None = None
    control_hub_thread_id: int | None = None
    control_hub_message_id: int | None = None
    tags_created: int = 0
    jump_url: str | None = None
    warnings: tuple[str, ...] = ()


@runtime_checkable
class IProjectDiscordWorkspace(Protocol):
    """Deep interface for Project Workspace lifecycle, Squad role binding, and Control Hub provisioning.

    Invariants:
      - Channel Type Invariant: Projects bound to Discord must target a Forum Channel
        (or validated Text Channel fallback). Non-matching channel types (e.g. threads or voice)
        are rejected before mutating persistent state.
      - Squad 1:1 Mapping Invariant: When a Squad role is provided, a Squad domain entity is
        atomically created/retrieved and mapped 1:1 to the Project in local persistence.
      - Single Control Hub Invariant: Exactly one pinned Control Hub exists per Forum Channel.
        Projects bound to that channel share the unified Control Hub post.
      - Tag Boundary Invariant: Standard PM tags (Status, Priority, Unassigned) and per-project
        tags are applied idempotently without exceeding Discord's 20-tag limit.
      - Fault-Tolerant Degradation: Persistent DB state is prioritized. Non-fatal Discord API
        warnings (e.g. pin limit reached) are returned in `warnings` without rolling back DB state.
    """

    async def provision_project(self, spec: ProjectProvisionSpec) -> ProjectWorkspaceRef:
        """Atomically provisions a Project, Squad role binding, Forum tags, and pinned Control Hub."""
        ...

    async def sync_control_hub(
        self,
        channel: discord.abc.GuildChannel | int,
        *,
        project_id: UUID | None = None,
        guild_id: int | None = None,
    ) -> ProjectWorkspaceRef | None:
        """Synchronizes or repairs the pinned Control Hub post and standard tags in a channel."""
        ...

    async def rebind_channel(
        self,
        project_id: UUID,
        new_channel: discord.abc.GuildChannel | int | None,
    ) -> ProjectWorkspaceRef:
        """Migrates a Project to a new channel, updating DB links, tags, and Control Hubs."""
        ...

    async def rebuild_workspace(
        self,
        project_id: UUID,
        *,
        guild: discord.Guild,
        target_channel: discord.abc.GuildChannel | int | None = None,
        progress_callback: Any | None = None,
    ) -> RebuildWorkspaceResult:
        """Reconstructs and reconciles a Project's Discord presence from database state."""
        ...


@dataclass(frozen=True, slots=True)
class RebuildProgress:
    """Progress snapshot during workspace rebuilding."""

    step: str
    current: int
    total: int
    message: str


@dataclass(frozen=True, slots=True)
class RebuildWorkspaceResult:
    """Result of a Project Workspace rebuild and reconciliation operation."""

    project: Project
    channel_id: int
    forum_created: bool
    tags_created: int
    hub_rebuilt: bool
    tasks_reconciled: int
    tasks_recreated: int
    tasks_archived: int
    warnings: tuple[str, ...] = ()
