"""Protocols and value types for Discord Workspaces and Thread Lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

import discord

from src.domain.models import Project, Task, TaskHistory, Team

TaskControlPanel = Literal["quick_controls", "dependencies", "history"]


@dataclass(frozen=True, slots=True)
class TaskWorkspaceRef:
    """References to a provisioned Discord Thread Workspace."""

    thread_id: int
    message_id: int
    channel_id: int
    jump_url: str


@runtime_checkable
class ITaskDiscordWorkspace(Protocol):
    """Deep Interface for managing a Task's Discord presence, Thread Workspace, and lifecycle.

    Invariants:
      - 1:1 Workspace Mapping: Every provisioned Task workspace corresponds to exactly
        one parent Forum Channel or Text Channel thread containing one root Task Action Card.
      - Tag Boundary: Forum Channel tags applied to a thread workspace never exceed Discord's
        5-tag limit and preserve existing non-managed custom tags.
      - Archive Invariant: Closed or archived tasks are guaranteed to remain archived after
        any background event or interaction update, preventing sidebar channel pollution.
      - Thread Unarchive Safety: Updates to archived threads never throw Discord HTTP errors;
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
    ) -> bool:
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
    team: Team | None = None
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
      - Squad 1:1 Mapping Invariant: When a Squad role is provided, a Team domain entity is
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
