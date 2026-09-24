import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ProjectTable(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guild_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    prefix = Column(String(10), nullable=False)
    next_task_number = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=True)
    discord_channel_id = Column(BigInteger, nullable=True, index=True)
    lead_discord_id = Column(BigInteger, nullable=True, index=True)
    category = Column(String(100), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tasks = relationship("TaskTable", back_populates="project", cascade="all, delete-orphan")
    squads = relationship("ProjectSquadTable", back_populates="project", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("guild_id", "name", name="uq_project_guild_name"),
        UniqueConstraint("guild_id", "prefix", name="uq_project_guild_prefix"),
    )


class SquadTable(Base):
    __tablename__ = "squads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guild_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    discord_role_id = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    members = relationship("SquadMemberTable", back_populates="squad", cascade="all, delete-orphan")
    projects = relationship("ProjectSquadTable", back_populates="squad", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("guild_id", "name", name="uq_squad_guild_name"),)


class SquadMemberTable(Base):
    __tablename__ = "squad_members"

    squad_id = Column(UUID(as_uuid=True), ForeignKey("squads.id", ondelete="CASCADE"), primary_key=True)
    user_discord_id = Column(BigInteger, primary_key=True)
    role_type = Column(String(20), nullable=False, default="member")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    squad = relationship("SquadTable", back_populates="members")


class ProjectSquadTable(Base):
    __tablename__ = "project_squads"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    squad_id = Column(UUID(as_uuid=True), ForeignKey("squads.id", ondelete="CASCADE"), primary_key=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    timeline = Column(String(100), nullable=True)

    project = relationship("ProjectTable", back_populates="squads")
    squad = relationship("SquadTable", back_populates="projects", lazy="selectin")


class TaskTable(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guild_id = Column(BigInteger, nullable=False, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    task_number = Column(Integer, nullable=False)
    short_id = Column(String(20), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="notStarted")
    priority = Column(String(20), nullable=False, default="normal")
    creator_discord_id = Column(BigInteger, nullable=False)
    assignee_discord_id = Column(BigInteger, nullable=True, index=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    discord_message_id = Column(BigInteger, nullable=True)
    discord_thread_id = Column(BigInteger, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project = relationship("ProjectTable", back_populates="tasks")
    watchers = relationship("TaskWatcherTable", back_populates="task", cascade="all, delete-orphan")
    history = relationship("TaskHistoryTable", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("guild_id", "short_id", name="uq_task_guild_short_id"),)


class TaskWatcherTable(Base):
    __tablename__ = "task_watchers"

    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    user_discord_id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    task = relationship("TaskTable", back_populates="watchers")


class TaskHistoryTable(Base):
    __tablename__ = "task_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_discord_id = Column(BigInteger, nullable=False)
    action = Column(String(50), nullable=False)
    old_status = Column(String(30), nullable=True)
    new_status = Column(String(30), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    task = relationship("TaskTable", back_populates="history")


class TaskDependencyTable(Base):
    __tablename__ = "task_dependencies"

    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_task_id = Column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    task = relationship("TaskTable", foreign_keys=[task_id])
    depends_on = relationship("TaskTable", foreign_keys=[depends_on_task_id])


class OutboxEventTable(Base):
    __tablename__ = "outbox_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(String(120), nullable=False, unique=True, index=True)
    event_type = Column(String(50), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(30), nullable=False, default="PENDING", index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    scheduled_for = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_outbox_pending_scheduled", "status", "scheduled_for"),)


class UserPreferenceTable(Base):
    __tablename__ = "user_preferences"

    guild_id = Column(BigInteger, primary_key=True)
    user_discord_id = Column(BigInteger, primary_key=True)
    notify_preference = Column(String(20), nullable=False, default="dm")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class GuildLeadRoleTable(Base):
    __tablename__ = "guild_lead_roles"

    guild_id = Column(BigInteger, primary_key=True)
    discord_role_id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class GuildLeadUserTable(Base):
    __tablename__ = "guild_lead_users"

    guild_id = Column(BigInteger, primary_key=True)
    user_discord_id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
