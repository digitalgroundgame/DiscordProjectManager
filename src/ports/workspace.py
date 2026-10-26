"""Protocols and value types for Task and Project Workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from src.domain.enums import PriorityLevel
from src.domain.models import Project, Squad, Task, TaskHistory

TaskControlPanel = Literal["quick_controls", "dependencies", "history"]
_UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class TaskWorkspaceRef:
    """References to a provisioned task execution workspace."""

    thread_id: int
    message_id: int
    channel_id: int
    jump_url: str


@dataclass(frozen=True, slots=True)
class SyncWorkspaceResult:
    """Result of synchronizing a workspace thread with a Task's domain state."""

    success: bool
    title_renamed: bool = False
    title_deferred: bool = False
    cooldown_remaining_seconds: float = 0.0

    def __bool__(self) -> bool:
        return self.success


@runtime_checkable
class TaskWorkspacePort(Protocol):
    """Port defining the lifecycle operations for task execution workspaces.

    Every task mapped to an execution workspace is anchored in a dedicated discussion
    thread or channel containing an interactive action card / starter message.
    """

    async def provision_workspace(
        self,
        task: Task,
        *,
        project: Project | None = None,
        target_container: int | Any = None,
        preferred_channel_id: int | None = None,
    ) -> TaskWorkspaceRef:
        """Provisions a new execution workspace and mounts the Task Action Card."""
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
        """Synchronizes an existing execution workspace with the current Task domain model state."""
        ...

    async def refresh_action_card(
        self,
        interaction: Any,
        task: Task,
        *,
        project: Project | None = None,
        project_name: str | None = None,
    ) -> None:
        """Refreshes the interactive Task Action Card."""
        ...

    async def post_activity(
        self,
        task: Task,
        content: str,
        *,
        embed: Any | None = None,
        rearchive_if_completed: bool = True,
    ) -> Any | None:
        """Posts an activity update, note, or event notification into the Task's workspace."""
        ...

    async def delete_workspace(
        self,
        task: Task,
        *,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Permanently deletes or archives the workspace."""
        ...

    async def render_task_controls(
        self,
        interaction: Any,
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
        interaction: Any,
        action: str,
        task_id: UUID,
    ) -> None:
        """Dispatches and executes a component interaction action for a task."""
        ...

    async def save_task_controls(
        self,
        interaction: Any,
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
        """Applies staged task control adjustments atomically, syncing tags and action card."""
        ...


@dataclass(frozen=True, slots=True)
class ProjectProvisionSpec:
    """Input specification for provisioning a Project and its Workspace."""

    guild_id: int
    name: str
    prefix: str | None = None
    role: int | Any = None
    roles: list[int | Any] | None = None
    channel: int | Any = None
    lead_discord_id: int | None = None
    description: str | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceRef:
    """Immutable result reference describing the fully provisioned Project and workspace presence."""

    project: Project
    squad: Squad | None = None
    channel_id: int | None = None
    control_hub_thread_id: int | None = None
    control_hub_message_id: int | None = None
    tags_created: int = 0
    jump_url: str | None = None
    warnings: tuple[str, ...] = ()


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


@runtime_checkable
class ProjectWorkspacePort(Protocol):
    """Port for Project Workspace lifecycle, Squad role binding, and Control Hub provisioning."""

    async def provision_project(self, spec: ProjectProvisionSpec) -> ProjectWorkspaceRef:
        """Atomically provisions a Project, Squad role binding, tags, and pinned Control Hub."""
        ...

    async def sync_control_hub(
        self,
        channel: int | Any,
        *,
        project_id: UUID | None = None,
        guild_id: int | None = None,
    ) -> ProjectWorkspaceRef | None:
        """Synchronizes or repairs the pinned Control Hub post and standard tags in a channel."""
        ...

    async def rebind_channel(
        self,
        project_id: UUID,
        new_channel: int | Any | None,
    ) -> ProjectWorkspaceRef:
        """Migrates a Project to a new channel, updating DB links, tags, and Control Hubs."""
        ...

    async def rebuild_workspace(
        self,
        project_id: UUID,
        *,
        guild: Any = None,
        guild_id: int | None = None,
        target_channel: int | Any | None = None,
        progress_callback: Any | None = None,
    ) -> RebuildWorkspaceResult:
        """Reconstructs and reconciles a Project's workspace presence from database state."""
        ...
