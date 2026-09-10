from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.enums import (
    EventType,
    NotificationPreference,
    OutboxStatus,
    PriorityLevel,
    SquadRoleType,
    TaskHistoryAction,
    TaskStatus,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Task(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    guild_id: int
    project_id: UUID = Field(default_factory=uuid4)
    task_number: int = 1
    short_id: str
    version: int = 1
    title: str
    body: str | None = None
    status: TaskStatus = TaskStatus.NOT_STARTED
    priority: PriorityLevel = PriorityLevel.NORMAL
    creator_discord_id: int
    assignee_discord_id: int | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    discord_message_id: int | None = None
    discord_thread_id: int | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    watchers: list[int] = Field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class TaskHistory(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    actor_discord_id: int
    action: TaskHistoryAction
    old_status: TaskStatus | None = None
    new_status: TaskStatus | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskDependency(DomainModel):
    task_id: UUID
    depends_on_task_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Project(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    guild_id: int
    name: str
    prefix: str
    next_task_number: int = 1
    description: str | None = None
    discord_channel_id: int | None = None
    discord_role_ids: list[int] = Field(default_factory=list)
    lead_discord_id: int | None = None
    category: str | None = None
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def _handle_legacy_role_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            legacy_role = data.get("discord_role_id")
            role_ids = list(data.get("discord_role_ids") or [])
            if legacy_role and legacy_role not in role_ids:
                role_ids.insert(0, legacy_role)
            data["discord_role_ids"] = role_ids
        return data

    @property
    def discord_role_id(self) -> int | None:
        """Primary squad Discord role for backward compatibility."""
        return self.discord_role_ids[0] if self.discord_role_ids else None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Squad(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    guild_id: int
    name: str
    discord_role_id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


Team = Squad


class SquadMember(DomainModel):
    squad_id: UUID
    user_discord_id: int
    role_type: SquadRoleType = SquadRoleType.MEMBER
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def _handle_legacy_team_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "team_id" in data and "squad_id" not in data:
                data["squad_id"] = data["team_id"]
        return data

    @property
    def team_id(self) -> UUID:
        return self.squad_id


TeamMember = SquadMember


class ProjectSquad(DomainModel):
    project_id: UUID
    squad_id: UUID
    start_date: datetime | None = None
    timeline: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _handle_legacy_team_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "team_id" in data and "squad_id" not in data:
                data["squad_id"] = data["team_id"]
        return data

    @property
    def team_id(self) -> UUID:
        return self.squad_id


ProjectTeam = ProjectSquad


class OutboxEvent(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    idempotency_key: str
    event_type: EventType
    payload: dict[str, Any]
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    scheduled_for: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None


class UserPreference(DomainModel):
    guild_id: int
    user_discord_id: int
    notify_preference: NotificationPreference = NotificationPreference.DM
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
