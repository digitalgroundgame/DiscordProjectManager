from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from src.domain.enums import NotificationPreference, PriorityLevel, TaskStatus
from src.domain.models import (
    OutboxEvent,
    Project,
    ProjectSquad,
    ProjectTeam,
    Squad,
    SquadMember,
    Task,
    TaskHistory,
    Team,
    UserPreference,
)


class ITaskRepo(ABC):
    @abstractmethod
    async def create(self, task: Task, session: Any | None = None) -> Task:
        """Persists a new task and its initial watchers."""

    @abstractmethod
    async def get_by_id(self, task_id: UUID) -> Task | None:
        """Fetches a task by UUID."""

    @abstractmethod
    async def get_by_short_id(self, guild_id: int, short_id: str) -> Task | None:
        """Fetches a task by its short ID (e.g. INF-1) in a guild."""

    @abstractmethod
    async def get_by_thread_id(self, guild_id: int, thread_id: int) -> Task | None:
        """Fetches a task associated with a Discord discussion thread ID."""

    @abstractmethod
    async def update_status_cas(
        self,
        task_id: UUID,
        expected_version: int,
        new_status: TaskStatus,
        completed_at: datetime | None,
        session: Any | None = None,
    ) -> Task | None:
        """Optimistic concurrency update for task status. Returns updated Task or None on conflict."""

    @abstractmethod
    async def update_task(
        self,
        task_id: UUID,
        title: str | None = None,
        body: str | None = None,
        clear_body: bool = False,
        priority: PriorityLevel | None = None,
        assignee_discord_id: int | None = None,
        clear_assignee: bool = False,
        due_at: datetime | None = None,
        clear_due_at: bool = False,
        watchers: list[int] | None = None,
        session: Any | None = None,
    ) -> Task | None:
        """Updates task metadata attributes and increments version. Returns updated Task or None."""

    @abstractmethod
    async def update_discord_message(
        self,
        task_id: UUID,
        discord_message_id: int,
        discord_thread_id: int | None,
    ) -> None:
        """Updates Discord message and thread identifiers on a task."""

    @abstractmethod
    async def list_tasks(
        self,
        guild_id: int,
        project_id: UUID | None = None,
        assignee_discord_id: int | None = None,
        status: TaskStatus | None = None,
        include_archived: bool = False,
        exclude_completed: bool = False,
        overdue_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        """Lists tasks with filters. Returns (tasks, total_count)."""

    @abstractmethod
    async def search_for_autocomplete(
        self,
        guild_id: int,
        query: str,
        project_id: UUID | None = None,
        limit: int = 25,
    ) -> list[Task]:
        """Searches open tasks for slash command autocomplete."""

    @abstractmethod
    async def set_archived(self, task_id: UUID, is_archived: bool, session: Any | None = None) -> Task | None:
        """Soft-deletes or unarchives a task."""

    @abstractmethod
    async def add_history(self, history: TaskHistory, session: Any | None = None) -> TaskHistory:
        """Appends a task history audit entry."""

    @abstractmethod
    async def get_history(self, task_id: UUID) -> list[TaskHistory]:
        """Retrieves chronological audit history for a task."""

    @abstractmethod
    async def add_dependency(self, task_id: UUID, depends_on_task_id: UUID, session: Any | None = None) -> bool:
        """Adds a dependency indicating task_id depends on depends_on_task_id."""

    @abstractmethod
    async def remove_dependency(self, task_id: UUID, depends_on_task_id: UUID, session: Any | None = None) -> bool:
        """Removes a dependency between task_id and depends_on_task_id."""

    @abstractmethod
    async def get_prerequisite_ids(self, task_id: UUID, session: Any | None = None) -> list[UUID]:
        """Gets task UUIDs that task_id depends on (prerequisites)."""

    @abstractmethod
    async def get_dependent_ids(self, task_id: UUID, session: Any | None = None) -> list[UUID]:
        """Gets task UUIDs that depend on task_id (dependents)."""

    @abstractmethod
    async def get_dependencies_for_tasks(
        self, task_ids: list[UUID], session: Any | None = None
    ) -> list[tuple[UUID, UUID]]:
        """Gets list of (task_id, depends_on_task_id) pairs for a list of task IDs."""

    @abstractmethod
    async def get_all_guild_dependencies(self, guild_id: int, session: Any | None = None) -> list[tuple[UUID, UUID]]:
        """Gets all (task_id, depends_on_task_id) pairs in a guild."""


class IProjectRepo(ABC):
    @abstractmethod
    async def create(self, project: Project) -> Project:
        """Persists a new project."""

    @abstractmethod
    async def get_by_id(self, project_id: UUID) -> Project | None:
        """Fetches project by UUID."""

    @abstractmethod
    async def get_by_name(self, guild_id: int, name: str) -> Project | None:
        """Fetches project by name within a guild."""

    @abstractmethod
    async def get_by_prefix(self, guild_id: int, prefix: str) -> Project | None:
        """Fetches project by prefix within a guild."""

    @abstractmethod
    async def get_by_channel_id(self, guild_id: int, channel_id: int) -> Project | None:
        """Fetches project bound to a Discord channel/thread."""

    @abstractmethod
    async def increment_task_number_atomic(self, project_id: UUID, session: Any | None = None) -> tuple[int, str]:
        """Atomically increments next_task_number and returns (allocated_number, prefix)."""

    @abstractmethod
    async def list_projects(self, guild_id: int, include_archived: bool = False) -> list[Project]:
        """Lists all projects for a guild."""

    @abstractmethod
    async def set_archived(self, project_id: UUID, is_archived: bool) -> Project | None:
        """Soft-deletes or unarchives a project."""

    @abstractmethod
    async def update_channel_id(self, project_id: UUID, discord_channel_id: int | None) -> Project | None:
        """Updates the bound Discord channel ID for a project."""

    @abstractmethod
    async def update_role_id(self, project_id: UUID, discord_role_id: int | None) -> Project | None:
        """Updates the mapped contributor Discord role ID for a project."""

    @abstractmethod
    async def update_lead_id(self, project_id: UUID, lead_discord_id: int | None) -> Project | None:
        """Updates the designated Project Lead Discord user ID for a project."""

    @abstractmethod
    async def assign_squad(self, project_squad: ProjectSquad) -> None:
        """Maps a squad to a project."""

    @abstractmethod
    async def remove_squad(self, project_id: UUID, squad_id: UUID) -> None:
        """Unmaps a squad from a project."""

    @abstractmethod
    async def list_squads_for_project(self, project_id: UUID) -> list[Squad]:
        """Lists all squads mapped to a project."""

    async def assign_team(self, project_team: ProjectTeam) -> None:
        """Backward-compatible alias for assign_squad."""
        await self.assign_squad(project_team)

    async def remove_team(self, project_id: UUID, team_id: UUID) -> None:
        """Backward-compatible alias for remove_squad."""
        await self.remove_squad(project_id, team_id)

    async def list_teams_for_project(self, project_id: UUID) -> list[Team]:
        """Backward-compatible alias for list_squads_for_project."""
        return await self.list_squads_for_project(project_id)


class ISquadRepo(ABC):
    @abstractmethod
    async def create(self, squad: Squad) -> Squad:
        """Persists a new squad."""

    @abstractmethod
    async def get_by_id(self, squad_id: UUID) -> Squad | None:
        """Fetches squad by ID."""

    @abstractmethod
    async def get_by_name(self, guild_id: int, name: str) -> Squad | None:
        """Fetches squad by name within a guild."""

    @abstractmethod
    async def get_by_role_id(self, guild_id: int, role_id: int) -> Squad | None:
        """Fetches squad by Discord role ID."""

    @abstractmethod
    async def add_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        """Designates a user as a squad lead in database."""

    @abstractmethod
    async def remove_squad_lead(self, squad_id: UUID, user_discord_id: int) -> None:
        """Removes squad lead status for a user."""

    @abstractmethod
    async def list_squad_leads(self, squad_id: UUID) -> list[int]:
        """Lists Discord user IDs of all leads for a squad."""

    @abstractmethod
    async def assign_member(self, member: SquadMember) -> None:
        """Records or updates squad member domain role (LEAD/MEMBER)."""

    @abstractmethod
    async def is_squad_lead(self, squad_id: UUID, user_discord_id: int) -> bool:
        """Checks whether user has SquadRoleType.LEAD for the given squad."""

    @abstractmethod
    async def list_squads(self, guild_id: int) -> list[Squad]:
        """Lists all squads for a guild."""

    @abstractmethod
    async def list_members(self, squad_id: UUID) -> list[SquadMember]:
        """Lists all members assigned to a squad."""

    # Backward-compatible aliases
    async def add_team_lead(self, team_id: UUID, user_discord_id: int) -> None:
        await self.add_squad_lead(team_id, user_discord_id)

    async def remove_team_lead(self, team_id: UUID, user_discord_id: int) -> None:
        await self.remove_squad_lead(team_id, user_discord_id)

    async def list_team_leads(self, team_id: UUID) -> list[int]:
        return await self.list_squad_leads(team_id)

    async def is_team_lead(self, team_id: UUID, user_discord_id: int) -> bool:
        return await self.is_squad_lead(team_id, user_discord_id)

    async def list_teams(self, guild_id: int) -> list[Team]:
        return await self.list_squads(guild_id)


ITeamRepo = ISquadRepo


class IOutboxRepo(ABC):
    @abstractmethod
    async def enqueue(self, event: OutboxEvent, session: Any | None = None) -> OutboxEvent:
        """Persists an outbox event."""

    @abstractmethod
    async def fetch_pending_batch(self, limit: int = 10) -> list[OutboxEvent]:
        """Fetches and locks pending scheduled events using FOR UPDATE SKIP LOCKED."""

    @abstractmethod
    async def mark_processed(self, event_id: UUID) -> None:
        """Marks an outbox event as PROCESSED."""

    @abstractmethod
    async def reschedule_or_fail(
        self,
        event_id: UUID,
        retry_count: int,
        next_scheduled_for: datetime,
        failed: bool = False,
    ) -> None:
        """Updates an event with new scheduled time or FAILED status on rate limits/errors."""

    @abstractmethod
    async def cancel_task_reminders(self, task_id: UUID) -> int:
        """Cancels all pending reminder events for a task."""

    @abstractmethod
    async def reclaim_stale_processing(self) -> int:
        """Resets stuck PROCESSING events back to PENDING so they can be redelivered (crash recovery)."""


class IUserPreferenceRepo(ABC):
    @abstractmethod
    async def get_preference(self, guild_id: int, user_discord_id: int) -> UserPreference | None:
        """Fetches user notification preference."""

    @abstractmethod
    async def set_preference(
        self,
        guild_id: int,
        user_discord_id: int,
        notify_preference: NotificationPreference,
    ) -> UserPreference:
        """Sets or updates user notification preference."""

    @abstractmethod
    async def get_preferences_bulk(
        self,
        guild_id: int,
        user_ids: list[int],
    ) -> dict[int, NotificationPreference]:
        """Fetches preferences for multiple users in a guild."""
