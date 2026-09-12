from enum import StrEnum


class TaskStatus(StrEnum):
    NOT_STARTED = "notStarted"
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"

    @property
    def rfc5545_status(self) -> str:
        mapping = {
            TaskStatus.NOT_STARTED: "NEEDS-ACTION",
            TaskStatus.IN_PROGRESS: "IN-PROCESS",
            TaskStatus.COMPLETED: "COMPLETED",
        }
        return mapping[self]

    @classmethod
    def from_rfc5545(cls, val: str) -> "TaskStatus":
        mapping = {
            "NEEDS-ACTION": cls.NOT_STARTED,
            "IN-PROCESS": cls.IN_PROGRESS,
            "COMPLETED": cls.COMPLETED,
        }
        return mapping.get(val.upper(), cls.NOT_STARTED)


class PriorityLevel(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

    @property
    def rfc5545_priority(self) -> int:
        """RFC 5545: 1 is highest priority, 5 is medium/normal, 9 is lowest."""
        mapping = {
            PriorityLevel.HIGH: 1,
            PriorityLevel.NORMAL: 5,
            PriorityLevel.LOW: 9,
        }
        return mapping[self]

    @classmethod
    def from_rfc5545(cls, val: int) -> "PriorityLevel":
        if 1 <= val <= 3:
            return cls.HIGH
        elif 4 <= val <= 6:
            return cls.NORMAL
        else:
            return cls.LOW


class SquadRoleType(StrEnum):
    MEMBER = "member"
    LEAD = "lead"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
    TASK_NOTE_ADDED = "TASK_NOTE_ADDED"
    TASK_DUE_REMINDER = "TASK_DUE_REMINDER"
    TASK_UPDATED = "TASK_UPDATED"


class TaskHistoryAction(StrEnum):
    CREATED = "CREATED"
    STATUS_CHANGE = "STATUS_CHANGE"
    NOTE_ADDED = "NOTE_ADDED"
    ASSIGNED = "ASSIGNED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    UPDATED = "UPDATED"
    ARCHIVED = "ARCHIVED"
    UNARCHIVED = "UNARCHIVED"


class NotificationPreference(StrEnum):
    DM = "dm"
    CHANNEL = "channel"
    BOTH = "both"
    NONE = "none"
