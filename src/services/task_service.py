from __future__ import annotations

import io
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.domain.enums import (
    EventType,
    PriorityLevel,
    TaskHistoryAction,
    TaskStatus,
)
from src.domain.exceptions import (
    ProjectNotFoundError,
    StaleVersionError,
    TaskNotFoundError,
    ValidationError,
)
from src.domain.models import Task, TaskHistory
from src.domain.tech_tree import (
    CircularDependencyError,
    SelfDependencyError,
    TechTree,
    resolve_member_name,
)
from src.ports.repositories import ITaskRepo
from src.ports.unit_of_work import IUnitOfWork, NullUnitOfWork
from src.ports.workspace import SyncWorkspaceResult, TaskWorkspacePort, TaskWorkspaceRef
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService

__all__ = [
    "StaleVersionError",
    "TaskService",
    "parse_inline_dependencies",
    "parse_prerequisite_short_ids",
    "resolve_member_name",
]


def parse_prerequisite_short_ids(raw: str | list[str] | None) -> list[str]:
    """Normalizes comma- or whitespace-separated prerequisite task short IDs."""
    if not raw:
        return []
    chunks = [raw] if isinstance(raw, str) else list(raw)
    keys: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        for part in str(chunk).split(","):
            part_str = part.strip()
            if not part_str:
                continue
            bracket_match = re.match(r"^\[([A-Za-z0-9_-]+)\]", part_str)
            if bracket_match:
                keys.append(bracket_match.group(1).upper())
                continue
            for token in part_str.split():
                clean = token.strip().lstrip("#[").rstrip("]").strip().upper()
                if clean:
                    keys.append(clean)
    return list(dict.fromkeys(keys))


def parse_inline_dependencies(text: str | None) -> list[str]:
    """Extracts task keys from phrases like 'Requires: #INF-1, INF-2' or 'Blocked by: INF-3'."""
    if not text:
        return []
    keys: list[str] = []
    pattern = r"(?:requires|depends\s+on|blocked\s+by|after)[:\s]+([A-Za-z0-9_#,\s-]+?)(?:\.|\n|$)"
    matches = re.finditer(pattern, text, re.IGNORECASE)
    for m in matches:
        raw_items = m.group(1).split(",")
        for item in raw_items:
            clean = item.strip().lstrip("#").strip().upper()
            if clean and "-" in clean and len(clean) <= 20:
                keys.append(clean)
    return list(dict.fromkeys(keys))


class TaskService:
    def __init__(
        self,
        task_repo: ITaskRepo,
        project_service: ProjectService,
        outbox_service: OutboxService,
        uow: IUnitOfWork | None = None,
        workspace: TaskWorkspacePort | None = None,
    ):
        self.task_repo = task_repo
        self.project_service = project_service
        self.outbox_service = outbox_service
        self.uow = uow
        self.workspace = workspace

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[IUnitOfWork]:
        """Manages an atomic transaction scope via UnitOfWork if configured, or fallback if omitted."""
        if self.uow is not None:
            async with self.uow as active_uow:
                yield active_uow
        else:
            yield NullUnitOfWork(
                tasks=self.task_repo,
                projects=self.project_service.project_repo,
                outbox=self.outbox_service.outbox_repo,
            )

    async def create_task(
        self,
        guild_id: int,
        title: str,
        creator_discord_id: int,
        project_name: str | None = None,
        project_id: UUID | None = None,
        assignee_discord_id: int | None = None,
        due_at: datetime | None = None,
        priority: PriorityLevel = PriorityLevel.NORMAL,
        body: str | None = None,
        watchers: list[int] | None = None,
        metadata_json: dict[str, Any] | None = None,
        prerequisite_short_ids: list[str] | str | None = None,
    ) -> Task:
        async with self._transaction() as uow:
            # Resolve project if project_name given
            resolved_project_id = project_id
            if project_name and not resolved_project_id:
                project = await uow.projects.get_by_name(guild_id, project_name)
                if not project:
                    raise ProjectNotFoundError(f"Project '{project_name}' was not found in this server.")
                resolved_project_id = project.id

            if not resolved_project_id:
                raise ValidationError("A project container must be specified for every task.")

            explicit_prereqs = parse_prerequisite_short_ids(prerequisite_short_ids)
            for prereq_key in explicit_prereqs:
                depends_on_task = await uow.tasks.get_by_short_id(guild_id, prereq_key)
                if not depends_on_task:
                    raise TaskNotFoundError(f"Prerequisite task '{prereq_key}' was not found in this server.")

            clean_watchers = list(set(watchers or []))

            # Allocate short ID within transaction
            task_number, short_id = await self.project_service.allocate_next_short_id(
                resolved_project_id, project_repo=uow.projects
            )

            task = Task(
                id=uuid4(),
                guild_id=guild_id,
                project_id=resolved_project_id,
                task_number=task_number,
                short_id=short_id,
                version=1,
                title=title.strip(),
                body=body.strip() if body else None,
                status=TaskStatus.NOT_STARTED,
                priority=priority,
                creator_discord_id=creator_discord_id,
                assignee_discord_id=assignee_discord_id,
                due_at=due_at,
                metadata_json=metadata_json or {},
                watchers=clean_watchers,
            )

            # 1. Persist task
            saved_task = await uow.tasks.create(task)

            # 2. Audit log creation
            history = TaskHistory(
                task_id=saved_task.id,
                actor_discord_id=creator_discord_id,
                action=TaskHistoryAction.CREATED,
                new_status=TaskStatus.NOT_STARTED,
                notes=f"Task '{title}' created with ID {short_id}",
            )
            await uow.tasks.add_history(history)

            # 3. Schedule tiered reminders if due_at set
            if due_at:
                await self.outbox_service.schedule_task_reminders(saved_task, outbox_repo=uow.outbox)

            # 4. Enqueue creation event
            await self.outbox_service.enqueue_event(
                event_type=EventType.TASK_CREATED,
                idempotency_key=f"task_created:{saved_task.id}",
                payload={
                    "task_id": str(saved_task.id),
                    "short_id": saved_task.short_id,
                    "title": saved_task.title,
                    "body": saved_task.body,
                    "status": saved_task.status.value,
                    "priority": saved_task.priority.value,
                    "guild_id": saved_task.guild_id,
                    "project_id": str(saved_task.project_id) if saved_task.project_id else None,
                    "creator_discord_id": saved_task.creator_discord_id,
                    "assignee_discord_id": saved_task.assignee_discord_id,
                    "watchers": saved_task.watchers,
                    "due_at": saved_task.due_at.isoformat() if saved_task.due_at else None,
                    "version": saved_task.version,
                    "created_at": saved_task.created_at.isoformat() if saved_task.created_at else None,
                },
                outbox_repo=uow.outbox,
            )

        # Link any prerequisites specified explicitly or found in body tags
        target_prereqs = list(explicit_prereqs)
        if not target_prereqs and saved_task.body:
            target_prereqs = parse_inline_dependencies(saved_task.body)
        for prereq_key in target_prereqs:
            try:
                await self.add_dependency(
                    guild_id=guild_id,
                    task_short_id=saved_task.short_id,
                    depends_on_short_id=prereq_key,
                    actor_discord_id=creator_discord_id,
                )
            except Exception:
                if prereq_key in explicit_prereqs:
                    raise
                pass

        return saved_task

    async def provision_task_workspace(
        self,
        task_id: UUID,
        *,
        target_container: int | Any = None,
        preferred_channel_id: int | None = None,
        workspace: TaskWorkspacePort | None = None,
    ) -> tuple[Task, TaskWorkspaceRef | None]:
        """Provisions an execution workspace (e.g. Discord Thread) for an existing task."""
        task = await self.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        effective_ws = workspace or self.workspace
        if not effective_ws:
            return task, None

        project = None
        if task.project_id:
            project = await self.project_service.get_by_id(task.project_id)

        ref = await effective_ws.provision_workspace(
            task,
            project=project,
            target_container=target_container,
            preferred_channel_id=preferred_channel_id,
        )

        if ref:
            await self.update_discord_message_ids(
                task.id,
                discord_message_id=ref.message_id,
                discord_thread_id=ref.thread_id,
            )
            updated = await self.get_by_id(task.id)
            if updated:
                task = updated

        return task, ref

    async def create_task_with_workspace(
        self,
        *,
        guild_id: int,
        title: str,
        creator_discord_id: int,
        project_name: str | None = None,
        project_id: UUID | None = None,
        assignee_discord_id: int | None = None,
        due_at: datetime | None = None,
        priority: PriorityLevel = PriorityLevel.NORMAL,
        body: str | None = None,
        watchers: list[int] | None = None,
        metadata_json: dict[str, Any] | None = None,
        prerequisite_short_ids: list[str] | str | None = None,
        target_container: int | Any = None,
        preferred_channel_id: int | None = None,
        workspace: TaskWorkspacePort | None = None,
    ) -> tuple[Task, TaskWorkspaceRef | None]:
        """Creates a task and provisions its execution workspace in one cohesive application workflow."""
        task = await self.create_task(
            guild_id=guild_id,
            title=title,
            creator_discord_id=creator_discord_id,
            project_id=project_id,
            project_name=project_name,
            assignee_discord_id=assignee_discord_id,
            due_at=due_at,
            priority=priority,
            body=body,
            watchers=watchers,
            metadata_json=metadata_json,
            prerequisite_short_ids=prerequisite_short_ids,
        )

        effective_ws = workspace or self.workspace
        if not effective_ws:
            return task, None

        task, ref = await self.provision_task_workspace(
            task.id,
            target_container=target_container,
            preferred_channel_id=preferred_channel_id,
            workspace=effective_ws,
        )

        # Synchronize any prerequisite workspace starter cards so dependency lists stay up to date
        if prerequisite_short_ids:
            explicit_prereqs = parse_prerequisite_short_ids(prerequisite_short_ids)
            for p_key in explicit_prereqs:
                p_task = await self.get_by_short_id(guild_id, p_key)
                if p_task:
                    try:
                        await effective_ws.sync_workspace(p_task)
                    except Exception:
                        pass

        return task, ref

    async def sync_task_workspace(
        self,
        task_id: UUID,
        *,
        sync_title: bool = False,
        sync_tags: bool = True,
        sync_archive: bool = True,
        sync_starter_card: bool = True,
        workspace: TaskWorkspacePort | None = None,
    ) -> SyncWorkspaceResult | bool:
        """Synchronizes an execution workspace starter card and tags with current domain state."""
        task = await self.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        effective_ws = workspace or self.workspace
        if not effective_ws:
            return False

        project = None
        if task.project_id:
            project = await self.project_service.get_by_id(task.project_id)

        return await effective_ws.sync_workspace(
            task,
            project=project,
            sync_title=sync_title,
            sync_tags=sync_tags,
            sync_archive=sync_archive,
            sync_starter_card=sync_starter_card,
        )

    async def update_status(
        self,
        task_id: UUID,
        new_status: TaskStatus,
        expected_version: int,
        actor_discord_id: int,
        notes: str | None = None,
    ) -> Task:
        async with self._transaction() as uow:
            current_task = await uow.tasks.get_by_id(task_id)
            if not current_task:
                raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

            old_status = current_task.status
            completed_at = datetime.now(UTC) if new_status == TaskStatus.COMPLETED else None

            updated_task = await uow.tasks.update_status_cas(
                task_id=task_id,
                expected_version=expected_version,
                new_status=new_status,
                completed_at=completed_at,
            )
            if not updated_task:
                raise StaleVersionError(
                    f"Task {current_task.short_id} was already modified by another user. Please refresh."
                )

            # Audit log status transition
            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.STATUS_CHANGE,
                old_status=old_status,
                new_status=new_status,
                notes=notes,
            )
            await uow.tasks.add_history(history)

            # If completed, cancel pending reminder events
            if new_status == TaskStatus.COMPLETED:
                await self.outbox_service.cancel_task_reminders(task_id, outbox_repo=uow.outbox)

            # Enqueue status changed event
            await self.outbox_service.enqueue_event(
                event_type=EventType.TASK_STATUS_CHANGED,
                idempotency_key=f"task_status:{task_id}:v{updated_task.version}",
                payload={
                    "task_id": str(task_id),
                    "short_id": updated_task.short_id,
                    "title": updated_task.title,
                    "body": updated_task.body,
                    "priority": updated_task.priority.value,
                    "guild_id": updated_task.guild_id,
                    "project_id": str(updated_task.project_id) if updated_task.project_id else None,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "actor_discord_id": actor_discord_id,
                    "creator_discord_id": updated_task.creator_discord_id,
                    "assignee_discord_id": updated_task.assignee_discord_id,
                    "watchers": updated_task.watchers,
                    "due_at": updated_task.due_at.isoformat() if updated_task.due_at else None,
                    "completed_at": updated_task.completed_at.isoformat() if updated_task.completed_at else None,
                    "version": updated_task.version,
                    "created_at": updated_task.created_at.isoformat() if updated_task.created_at else None,
                    "notes": notes,
                    "discord_thread_id": updated_task.discord_thread_id,
                    "discord_message_id": updated_task.discord_message_id,
                },
                outbox_repo=uow.outbox,
            )

            # Evaluate downstream dependents if this task just transitioned to COMPLETED
            if new_status == TaskStatus.COMPLETED and old_status != TaskStatus.COMPLETED:
                dependent_ids = await uow.tasks.get_dependent_ids(task_id)
                for dep_id in dependent_ids:
                    dep_task = await uow.tasks.get_by_id(dep_id)
                    if not dep_task or dep_task.is_archived or dep_task.status != TaskStatus.NOT_STARTED:
                        continue

                    prereq_ids = await uow.tasks.get_prerequisite_ids(dep_id)
                    if not prereq_ids:
                        continue

                    prereq_tasks: list[Task] = []
                    all_completed = True
                    for pid in prereq_ids:
                        if pid == task_id:
                            prereq_tasks.append(updated_task)
                        else:
                            pt = await uow.tasks.get_by_id(pid)
                            if not pt or pt.status != TaskStatus.COMPLETED:
                                all_completed = False
                                break
                            prereq_tasks.append(pt)

                    if all_completed:
                        await self.outbox_service.enqueue_event(
                            event_type=EventType.TASK_UNBLOCKED,
                            idempotency_key=f"task_unblocked:{dep_task.id}:by:{task_id}:v{updated_task.version}",
                            payload={
                                "task_id": str(dep_task.id),
                                "short_id": dep_task.short_id,
                                "title": dep_task.title,
                                "body": dep_task.body,
                                "status": dep_task.status.value,
                                "priority": dep_task.priority.value,
                                "guild_id": dep_task.guild_id,
                                "project_id": str(dep_task.project_id) if dep_task.project_id else None,
                                "actor_discord_id": actor_discord_id,
                                "creator_discord_id": dep_task.creator_discord_id,
                                "assignee_discord_id": dep_task.assignee_discord_id,
                                "watchers": dep_task.watchers,
                                "due_at": dep_task.due_at.isoformat() if dep_task.due_at else None,
                                "version": dep_task.version,
                                "created_at": dep_task.created_at.isoformat() if dep_task.created_at else None,
                                "discord_thread_id": dep_task.discord_thread_id,
                                "discord_message_id": dep_task.discord_message_id,
                                "unblocked_by_task_id": str(task_id),
                                "unblocked_by_short_id": updated_task.short_id,
                                "unblocked_by_title": updated_task.title,
                                "prerequisites": [{"short_id": pt.short_id, "title": pt.title} for pt in prereq_tasks],
                            },
                            outbox_repo=uow.outbox,
                        )

        return updated_task

    async def add_note(
        self,
        task_id: UUID,
        actor_discord_id: int,
        note_text: str,
    ) -> TaskHistory:
        async with self._transaction() as uow:
            task = await uow.tasks.get_by_id(task_id)
            if not task:
                raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")
            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.NOTE_ADDED,
                notes=note_text.strip(),
            )
            saved_history = await uow.tasks.add_history(history)

            # Enqueue note event for thread/DM notification
            await self.outbox_service.enqueue_event(
                event_type=EventType.TASK_NOTE_ADDED,
                idempotency_key=f"task_note:{saved_history.id}",
                payload={
                    "task_id": str(task_id),
                    "short_id": task.short_id,
                    "title": task.title,
                    "body": task.body,
                    "priority": task.priority.value,
                    "guild_id": task.guild_id,
                    "project_id": str(task.project_id) if task.project_id else None,
                    "actor_discord_id": actor_discord_id,
                    "creator_discord_id": task.creator_discord_id,
                    "assignee_discord_id": task.assignee_discord_id,
                    "watchers": task.watchers,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                    "version": task.version,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "note": note_text.strip(),
                    "status": task.status.value,
                    "is_completed": task.status == TaskStatus.COMPLETED,
                    "discord_thread_id": task.discord_thread_id,
                    "discord_message_id": task.discord_message_id,
                },
                outbox_repo=uow.outbox,
            )

        return saved_history

    async def update_assignee(
        self,
        task_id: UUID,
        new_assignee_id: int | None,
        actor_discord_id: int,
    ) -> Task:
        async with self._transaction() as uow:
            current_task = await uow.tasks.get_by_id(task_id)
            if not current_task:
                raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

            old_assignee = current_task.assignee_discord_id
            if old_assignee == new_assignee_id:
                return current_task

            clear = new_assignee_id is None
            updated_task = await uow.tasks.update_task(
                task_id=task_id,
                assignee_discord_id=new_assignee_id,
                clear_assignee=clear,
            )
            if not updated_task:
                raise ValidationError(f"Failed to update assignee for task {task_id}.")

            note = f"Assigned to {new_assignee_id}" if new_assignee_id else "Removed assignee (unassigned)"
            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.ASSIGNED,
                notes=note,
                diff_payload={
                    "field": "assignee",
                    "old_value": old_assignee,
                    "new_value": new_assignee_id,
                },
            )
            await uow.tasks.add_history(history)

            # Enqueue event
            await self.outbox_service.enqueue_event(
                event_type=EventType.TASK_UPDATED,
                idempotency_key=f"task_assignee:{task_id}:v{updated_task.version}",
                payload={
                    "task_id": str(task_id),
                    "short_id": updated_task.short_id,
                    "title": updated_task.title,
                    "body": updated_task.body,
                    "priority": updated_task.priority.value,
                    "guild_id": updated_task.guild_id,
                    "project_id": str(updated_task.project_id) if updated_task.project_id else None,
                    "actor_discord_id": actor_discord_id,
                    "creator_discord_id": updated_task.creator_discord_id,
                    "old_assignee_id": old_assignee,
                    "new_assignee_id": new_assignee_id,
                    "assignee_discord_id": new_assignee_id,
                    "watchers": updated_task.watchers,
                    "due_at": updated_task.due_at.isoformat() if updated_task.due_at else None,
                    "version": updated_task.version,
                    "created_at": updated_task.created_at.isoformat() if updated_task.created_at else None,
                    "status": updated_task.status.value,
                    "is_completed": updated_task.status == TaskStatus.COMPLETED,
                    "update_type": "assignee",
                    "discord_thread_id": updated_task.discord_thread_id,
                    "discord_message_id": updated_task.discord_message_id,
                },
                outbox_repo=uow.outbox,
            )

        return updated_task

    async def update_priority(
        self,
        task_id: UUID,
        new_priority: PriorityLevel,
        actor_discord_id: int,
    ) -> Task:
        async with self._transaction() as uow:
            current_task = await uow.tasks.get_by_id(task_id)
            if not current_task:
                raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

            old_priority = current_task.priority
            if old_priority == new_priority:
                return current_task

            updated_task = await uow.tasks.update_task(
                task_id=task_id,
                priority=new_priority,
            )
            if not updated_task:
                raise ValidationError(f"Failed to update priority for task {task_id}.")

            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.PRIORITY_CHANGED,
                notes=f"Priority changed from {old_priority.value} to {new_priority.value}",
            )
            await uow.tasks.add_history(history)

            await self.outbox_service.enqueue_event(
                event_type=EventType.TASK_UPDATED,
                idempotency_key=f"task_priority:{task_id}:v{updated_task.version}",
                payload={
                    "task_id": str(task_id),
                    "short_id": updated_task.short_id,
                    "title": updated_task.title,
                    "body": updated_task.body,
                    "guild_id": updated_task.guild_id,
                    "project_id": str(updated_task.project_id) if updated_task.project_id else None,
                    "actor_discord_id": actor_discord_id,
                    "creator_discord_id": updated_task.creator_discord_id,
                    "old_priority": old_priority.value,
                    "new_priority": new_priority.value,
                    "priority": new_priority.value,
                    "assignee_discord_id": updated_task.assignee_discord_id,
                    "watchers": updated_task.watchers,
                    "due_at": updated_task.due_at.isoformat() if updated_task.due_at else None,
                    "version": updated_task.version,
                    "created_at": updated_task.created_at.isoformat() if updated_task.created_at else None,
                    "status": updated_task.status.value,
                    "is_completed": updated_task.status == TaskStatus.COMPLETED,
                    "update_type": "priority",
                    "discord_thread_id": updated_task.discord_thread_id,
                    "discord_message_id": updated_task.discord_message_id,
                },
                outbox_repo=uow.outbox,
            )

        return updated_task

    async def update_details(
        self,
        task_id: UUID,
        actor_discord_id: int,
        title: str | None = None,
        body: str | None = None,
        clear_body: bool = False,
        due_at: datetime | None = None,
        clear_due_at: bool = False,
        watchers: list[int] | None = None,
    ) -> Task:
        async with self._transaction() as uow:
            current_task = await uow.tasks.get_by_id(task_id)
            if not current_task:
                raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

            diffs: list[dict[str, Any]] = []
            changes: list[str] = []
            if title is not None and title.strip() != current_task.title:
                diffs.append({"field": "title", "old_value": current_task.title, "new_value": title.strip()})
                changes.append(f"Title: {current_task.title} -> {title.strip()}")
            if clear_body and current_task.body:
                diffs.append({"field": "body", "old_value": current_task.body, "new_value": None})
                changes.append("Description removed")
            elif body is not None and body.strip() != (current_task.body or ""):
                diffs.append({"field": "body", "old_value": current_task.body, "new_value": body.strip()})
                changes.append("Description updated")
            if clear_due_at and current_task.due_at:
                diffs.append({"field": "due_at", "old_value": current_task.due_at.isoformat(), "new_value": None})
                changes.append("Due date removed")
            elif due_at is not None and due_at != current_task.due_at:
                due_str = due_at.strftime("%Y-%m-%d %H:%M UTC")
                diffs.append(
                    {
                        "field": "due_at",
                        "old_value": current_task.due_at.isoformat() if current_task.due_at else None,
                        "new_value": due_str,
                    }
                )
                changes.append(f"Due date set to {due_str}")
            if watchers is not None and set(watchers) != set(current_task.watchers):
                added = sorted(set(watchers) - set(current_task.watchers))
                removed = sorted(set(current_task.watchers) - set(watchers))
                if added:
                    diffs.append({"field": "watchers", "action": "added", "user_ids": added})
                    changes.append(f"Added watchers: {', '.join(str(u) for u in added)}")
                if removed:
                    diffs.append({"field": "watchers", "action": "removed", "user_ids": removed})
                    changes.append(f"Removed watchers: {', '.join(str(u) for u in removed)}")

            updated_task = await uow.tasks.update_task(
                task_id=task_id,
                title=title,
                body=body,
                clear_body=clear_body,
                due_at=due_at,
                clear_due_at=clear_due_at,
                watchers=watchers,
            )
            if not updated_task:
                raise ValidationError(f"Failed to update task {task_id}.")

            # If due_at changed, reschedule outbox reminders
            if clear_due_at:
                await self.outbox_service.cancel_task_reminders(task_id, outbox_repo=uow.outbox)
            elif due_at is not None and due_at != current_task.due_at:
                await self.outbox_service.cancel_task_reminders(task_id, outbox_repo=uow.outbox)
                if not updated_task.is_completed and not updated_task.is_archived:
                    await self.outbox_service.schedule_task_reminders(updated_task, outbox_repo=uow.outbox)

            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.UPDATED,
                notes="Task details updated",
            )
            await uow.tasks.add_history(history)

            if changes:
                await self.outbox_service.enqueue_event(
                    event_type=EventType.TASK_UPDATED,
                    idempotency_key=f"task_details:{task_id}:v{updated_task.version}",
                    payload={
                        "task_id": str(task_id),
                        "short_id": updated_task.short_id,
                        "title": updated_task.title,
                        "body": updated_task.body,
                        "priority": updated_task.priority.value,
                        "guild_id": updated_task.guild_id,
                        "project_id": str(updated_task.project_id) if updated_task.project_id else None,
                        "actor_discord_id": actor_discord_id,
                        "creator_discord_id": updated_task.creator_discord_id,
                        "assignee_discord_id": updated_task.assignee_discord_id,
                        "watchers": updated_task.watchers,
                        "old_watchers": current_task.watchers,
                        "due_at": updated_task.due_at.isoformat() if updated_task.due_at else None,
                        "version": updated_task.version,
                        "created_at": updated_task.created_at.isoformat() if updated_task.created_at else None,
                        "changes": changes,
                        "diffs": diffs,
                        "status": updated_task.status.value,
                        "is_completed": updated_task.status == TaskStatus.COMPLETED,
                        "update_type": "details",
                        "discord_thread_id": updated_task.discord_thread_id,
                        "discord_message_id": updated_task.discord_message_id,
                    },
                    outbox_repo=uow.outbox,
                )

        return updated_task

    async def update_discord_message_ids(
        self,
        task_id: UUID,
        discord_message_id: int,
        discord_thread_id: int | None = None,
    ) -> None:
        async with self._transaction() as uow:
            await uow.tasks.update_discord_message(task_id, discord_message_id, discord_thread_id)

    async def get_by_id(self, task_id: UUID) -> Task | None:
        return await self.task_repo.get_by_id(task_id)

    async def get_by_short_id(self, guild_id: int, short_id: str) -> Task | None:
        return await self.task_repo.get_by_short_id(guild_id, short_id.upper())

    async def get_by_thread_id(self, guild_id: int, thread_id: int) -> Task | None:
        return await self.task_repo.get_by_thread_id(guild_id, thread_id)

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
        return await self.task_repo.list_tasks(
            guild_id=guild_id,
            project_id=project_id,
            assignee_discord_id=assignee_discord_id,
            status=status,
            include_archived=include_archived,
            exclude_completed=exclude_completed,
            overdue_only=overdue_only,
            limit=limit,
            offset=offset,
        )

    async def search_for_autocomplete(
        self,
        guild_id: int,
        query: str,
        project_id: UUID | None = None,
        limit: int = 25,
    ) -> list[Task]:
        return await self.task_repo.search_for_autocomplete(
            guild_id=guild_id,
            query=query,
            project_id=project_id,
            limit=limit,
        )

    async def archive_task(self, task_id: UUID, actor_discord_id: int) -> Task | None:
        async with self._transaction() as uow:
            task = await uow.tasks.set_archived(task_id, is_archived=True)
            if task:
                await self.outbox_service.cancel_task_reminders(task_id, outbox_repo=uow.outbox)
                history = TaskHistory(
                    task_id=task_id,
                    actor_discord_id=actor_discord_id,
                    action=TaskHistoryAction.ARCHIVED,
                    notes="Task archived",
                )
                await uow.tasks.add_history(history)
        return task

    async def unarchive_task(self, task_id: UUID, actor_discord_id: int) -> Task | None:
        async with self._transaction() as uow:
            task = await uow.tasks.set_archived(task_id, is_archived=False)
            if task:
                if task.due_at and not task.is_completed:
                    await self.outbox_service.schedule_task_reminders(task, outbox_repo=uow.outbox)
                history = TaskHistory(
                    task_id=task_id,
                    actor_discord_id=actor_discord_id,
                    action=TaskHistoryAction.UNARCHIVED,
                    notes="Task unarchived and restored",
                )
                await uow.tasks.add_history(history)
        return task

    async def delete_task(self, task_id: UUID, actor_discord_id: int | None = None) -> Task | None:
        """Permanently deletes a task, its dependencies, history, watchers, and cancels pending reminders."""
        async with self._transaction() as uow:
            task = await uow.tasks.get_by_id(task_id)
            if not task:
                return None

            await self.outbox_service.cancel_task_reminders(task_id, outbox_repo=uow.outbox)
            deleted = await uow.tasks.delete(task_id)
            if not deleted:
                return None

        return task

    async def get_history(self, task_id: UUID) -> list[TaskHistory]:
        return await self.task_repo.get_history(task_id)

    async def add_dependency(
        self,
        guild_id: int,
        task_short_id: str,
        depends_on_short_id: str,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Adds a dependency indicating that task_short_id depends on depends_on_short_id."""
        async with self._transaction() as uow:
            task = await uow.tasks.get_by_short_id(guild_id, task_short_id.upper())
            if not task:
                raise TaskNotFoundError(f"Task '{task_short_id}' was not found in this server.")

            depends_on_task = await uow.tasks.get_by_short_id(guild_id, depends_on_short_id.upper())
            if not depends_on_task:
                raise TaskNotFoundError(f"Prerequisite task '{depends_on_short_id}' was not found in this server.")

            if task.id == depends_on_task.id:
                raise ValidationError("A task cannot depend on itself.")

            # Cycle detection via TechTree domain module
            guild_deps = await uow.tasks.get_all_guild_dependencies(guild_id)
            tree = TechTree([task, depends_on_task], guild_deps)
            try:
                tree.validate_edge(task.id, depends_on_task.id)
            except SelfDependencyError as e:
                raise ValidationError(str(e)) from e
            except CircularDependencyError as e:
                raise ValidationError(str(e)) from e

            res = await uow.tasks.add_dependency(task.id, depends_on_task.id)
            if actor_discord_id:
                history = TaskHistory(
                    task_id=task.id,
                    actor_discord_id=actor_discord_id,
                    action=TaskHistoryAction.UPDATED,
                    notes=f"Added prerequisite dependency on {depends_on_short_id}",
                )
                await uow.tasks.add_history(history)
            return res

    async def remove_dependency(
        self,
        guild_id: int,
        task_short_id: str,
        depends_on_short_id: str,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Removes a dependency between task_short_id and depends_on_short_id."""
        async with self._transaction() as uow:
            task = await uow.tasks.get_by_short_id(guild_id, task_short_id.upper())
            if not task:
                raise TaskNotFoundError(f"Task '{task_short_id}' was not found in this server.")

            depends_on_task = await uow.tasks.get_by_short_id(guild_id, depends_on_short_id.upper())
            if not depends_on_task:
                raise TaskNotFoundError(f"Prerequisite task '{depends_on_short_id}' was not found in this server.")

            res = await uow.tasks.remove_dependency(task.id, depends_on_task.id)
            if actor_discord_id and res:
                history = TaskHistory(
                    task_id=task.id,
                    actor_discord_id=actor_discord_id,
                    action=TaskHistoryAction.UPDATED,
                    notes=f"Removed prerequisite dependency on {depends_on_short_id}",
                )
                await uow.tasks.add_history(history)
            return res

    async def get_task_dependencies(self, task_id: UUID) -> tuple[list[Task], list[Task]]:
        """Returns (prerequisites, dependents) for a given task."""
        prereq_ids = await self.task_repo.get_prerequisite_ids(task_id)
        dependent_ids = await self.task_repo.get_dependent_ids(task_id)

        prereqs = []
        for pid in prereq_ids:
            t = await self.task_repo.get_by_id(pid)
            if t:
                prereqs.append(t)

        dependents = []
        for did in dependent_ids:
            t = await self.task_repo.get_by_id(did)
            if t:
                dependents.append(t)

        return prereqs, dependents

    async def get_unresolved_prerequisites(self, task_id: UUID) -> list[Task]:
        """Returns all prerequisite tasks that are not yet marked COMPLETED."""
        prereqs, _ = await self.get_task_dependencies(task_id)
        return [p for p in prereqs if not p.is_completed]

    async def get_project_tech_tree(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
    ) -> TechTree:
        """Loads and returns an analyzed TechTree domain model for a project."""
        tasks, _ = await self.task_repo.list_tasks(
            guild_id=guild_id,
            project_id=project_id,
            include_archived=False,
            limit=500,
        )
        task_ids = [t.id for t in tasks]
        deps = await self.task_repo.get_dependencies_for_tasks(task_ids) if tasks else []
        return TechTree.build(tasks, deps, member_resolver=member_resolver)

    async def get_project_tree_data(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        tree = await self.get_project_tech_tree(guild_id, project_id, member_resolver=member_resolver)
        return tree.to_nodes_data()

    async def render_project_tree(
        self,
        guild_id: int,
        project_id: UUID,
        orientation: str = "lr",
        member_resolver: Any = None,
    ) -> io.BytesIO:
        project = await self.project_service.get_by_id(project_id)
        project_name = project.name if project else "Project Tech Tree"
        tree = await self.get_project_tech_tree(guild_id, project_id, member_resolver=member_resolver)
        return tree.to_png(title=f"Tech Tree: {project_name}", orientation=orientation)
