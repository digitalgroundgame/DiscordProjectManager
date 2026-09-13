from __future__ import annotations

import io
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from src.domain.timeline import ProjectTimeline

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
from src.ports.repositories import IOutboxRepo, IProjectRepo, ITaskRepo
from src.ports.unit_of_work import IUnitOfWork
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService

__all__ = ["StaleVersionError", "TaskService", "parse_inline_dependencies", "resolve_member_name"]


class _NullUnitOfWork(IUnitOfWork):
    """Fallback Unit of Work when no transactional unit of work is injected."""

    def __init__(self, task_repo: ITaskRepo, project_repo: IProjectRepo, outbox_repo: IOutboxRepo):
        self.tasks = task_repo
        self.projects = project_repo
        self.outbox = outbox_repo
        self.squads = None  # type: ignore[assignment]
        self.user_prefs = None  # type: ignore[assignment]

    async def __aenter__(self) -> _NullUnitOfWork:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    @property
    def session(self) -> Any:
        return None


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
    ):
        self.task_repo = task_repo
        self.project_service = project_service
        self.outbox_service = outbox_service
        self.uow = uow

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[IUnitOfWork]:
        """Manages an atomic transaction scope via UnitOfWork if configured, or fallback if omitted."""
        if self.uow is not None:
            async with self.uow as active_uow:
                yield active_uow
        else:
            yield _NullUnitOfWork(
                self.task_repo,
                self.project_service.project_repo,
                self.outbox_service.outbox_repo,
            )

    async def create_task(
        self,
        guild_id: int,
        title: str,
        creator_discord_id: int,
        project_name: str | None = None,
        project_id: UUID | None = None,
        assignee_discord_id: int | None = None,
        start_at: datetime | None = None,
        due_at: datetime | None = None,
        priority: PriorityLevel = PriorityLevel.NORMAL,
        body: str | None = None,
        watchers: list[int] | None = None,
        metadata_json: dict[str, Any] | None = None,
        prerequisite_short_ids: list[str] | None = None,
    ) -> Task:
        # Resolve project if project_name given
        resolved_project_id = project_id
        if project_name and not resolved_project_id:
            project = await self.project_service.get_by_name(guild_id, project_name)
            if not project:
                raise ProjectNotFoundError(f"Project '{project_name}' was not found in this server.")
            resolved_project_id = project.id

        if not resolved_project_id:
            raise ValidationError("A project container must be specified for every task.")

        clean_watchers = list(set(watchers or []))

        async with self._transaction() as uow:
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
                start_at=start_at,
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
                    "guild_id": saved_task.guild_id,
                    "project_id": str(saved_task.project_id) if saved_task.project_id else None,
                    "creator_discord_id": saved_task.creator_discord_id,
                    "assignee_discord_id": saved_task.assignee_discord_id,
                    "watchers": saved_task.watchers,
                    "due_at": saved_task.due_at.isoformat() if saved_task.due_at else None,
                },
                outbox_repo=uow.outbox,
            )

        # Link any prerequisites specified explicitly or found in body tags
        target_prereqs = list(prerequisite_short_ids or [])
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
                pass

        return saved_task

    async def update_status(
        self,
        task_id: UUID,
        new_status: TaskStatus,
        expected_version: int,
        actor_discord_id: int,
        notes: str | None = None,
    ) -> Task:
        current_task = await self.task_repo.get_by_id(task_id)
        if not current_task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        old_status = current_task.status
        completed_at = datetime.now(UTC) if new_status == TaskStatus.COMPLETED else None

        async with self._transaction() as uow:
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
                    "guild_id": updated_task.guild_id,
                    "project_id": str(updated_task.project_id) if updated_task.project_id else None,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "actor_discord_id": actor_discord_id,
                    "assignee_discord_id": updated_task.assignee_discord_id,
                    "watchers": updated_task.watchers,
                    "notes": notes,
                    "discord_thread_id": updated_task.discord_thread_id,
                    "discord_message_id": updated_task.discord_message_id,
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
        task = await self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        async with self._transaction() as uow:
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
                    "guild_id": task.guild_id,
                    "actor_discord_id": actor_discord_id,
                    "assignee_discord_id": task.assignee_discord_id,
                    "watchers": task.watchers,
                    "note": note_text.strip(),
                    "status": task.status.value,
                    "is_completed": task.status == TaskStatus.COMPLETED,
                    "discord_thread_id": task.discord_thread_id,
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
        current_task = await self.task_repo.get_by_id(task_id)
        if not current_task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        old_assignee = current_task.assignee_discord_id
        if old_assignee == new_assignee_id:
            return current_task

        clear = new_assignee_id is None
        async with self._transaction() as uow:
            updated_task = await uow.tasks.update_task(
                task_id=task_id,
                assignee_discord_id=new_assignee_id,
                clear_assignee=clear,
            )
            if not updated_task:
                raise ValidationError(f"Failed to update assignee for task {task_id}.")

            note = f"Assigned to <@{new_assignee_id}>" if new_assignee_id else "Removed assignee (unassigned)"
            history = TaskHistory(
                task_id=task_id,
                actor_discord_id=actor_discord_id,
                action=TaskHistoryAction.ASSIGNED,
                notes=note,
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
                    "guild_id": updated_task.guild_id,
                    "actor_discord_id": actor_discord_id,
                    "old_assignee_id": old_assignee,
                    "new_assignee_id": new_assignee_id,
                    "assignee_discord_id": new_assignee_id,
                    "watchers": updated_task.watchers,
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
        current_task = await self.task_repo.get_by_id(task_id)
        if not current_task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        old_priority = current_task.priority
        if old_priority == new_priority:
            return current_task

        async with self._transaction() as uow:
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
                    "guild_id": updated_task.guild_id,
                    "actor_discord_id": actor_discord_id,
                    "old_priority": old_priority.value,
                    "new_priority": new_priority.value,
                    "priority": new_priority.value,
                    "assignee_discord_id": updated_task.assignee_discord_id,
                    "watchers": updated_task.watchers,
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
        current_task = await self.task_repo.get_by_id(task_id)
        if not current_task:
            raise TaskNotFoundError(f"Task with ID {task_id} does not exist.")

        changes: list[str] = []
        if title is not None and title.strip() != current_task.title:
            changes.append(f"Title: `{current_task.title}` ➔ **`{title.strip()}`**")
        if clear_body and current_task.body:
            changes.append("Description removed")
        elif body is not None and body.strip() != (current_task.body or ""):
            changes.append("Description updated")
        if clear_due_at and current_task.due_at:
            changes.append("Due date removed")
        elif due_at is not None and due_at != current_task.due_at:
            changes.append(f"Due date set to `{due_at.strftime('%Y-%m-%d %H:%M UTC')}`")
        if watchers is not None and set(watchers) != set(current_task.watchers):
            added = set(watchers) - set(current_task.watchers)
            removed = set(current_task.watchers) - set(watchers)
            if added:
                changes.append(f"Added watchers: {', '.join(f'<@{u}>' for u in sorted(added))}")
            if removed:
                changes.append(f"Removed watchers: {', '.join(f'<@{u}>' for u in sorted(removed))}")

        async with self._transaction() as uow:
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
                        "guild_id": updated_task.guild_id,
                        "actor_discord_id": actor_discord_id,
                        "assignee_discord_id": updated_task.assignee_discord_id,
                        "watchers": updated_task.watchers,
                        "old_watchers": current_task.watchers,
                        "changes": changes,
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
        await self.task_repo.update_discord_message(task_id, discord_message_id, discord_thread_id)

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
        task = await self.get_by_id(task_id)
        if not task:
            return None

        async with self._transaction() as uow:
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
        task = await self.get_by_short_id(guild_id, task_short_id)
        if not task:
            raise TaskNotFoundError(f"Task '{task_short_id}' was not found in this server.")

        depends_on_task = await self.get_by_short_id(guild_id, depends_on_short_id)
        if not depends_on_task:
            raise TaskNotFoundError(f"Prerequisite task '{depends_on_short_id}' was not found in this server.")

        if task.id == depends_on_task.id:
            raise ValidationError("A task cannot depend on itself.")

        # Cycle detection via TechTree domain module
        guild_deps = await self.task_repo.get_all_guild_dependencies(guild_id)
        tree = TechTree([task, depends_on_task], guild_deps)
        try:
            tree.validate_edge(task.id, depends_on_task.id)
        except SelfDependencyError as e:
            raise ValidationError(str(e)) from e
        except CircularDependencyError as e:
            raise ValidationError(str(e)) from e

        async with self._transaction() as uow:
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
        task = await self.get_by_short_id(guild_id, task_short_id)
        if not task:
            raise TaskNotFoundError(f"Task '{task_short_id}' was not found in this server.")

        depends_on_task = await self.get_by_short_id(guild_id, depends_on_short_id)
        if not depends_on_task:
            raise TaskNotFoundError(f"Prerequisite task '{depends_on_short_id}' was not found in this server.")

        async with self._transaction() as uow:
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

    async def get_project_timeline(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
    ) -> ProjectTimeline:
        from src.domain.timeline import ProjectTimeline

        tasks, _ = await self.task_repo.list_tasks(
            guild_id=guild_id,
            project_id=project_id,
            include_archived=False,
            limit=500,
        )
        task_ids = [t.id for t in tasks]
        deps = await self.task_repo.get_dependencies_for_tasks(task_ids) if tasks else []
        return ProjectTimeline.build(tasks, deps, member_resolver=member_resolver)

    async def render_project_timeline(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
    ) -> io.BytesIO:
        project = await self.project_service.get_by_id(project_id)
        project_name = project.name if project else "Project Timeline"
        timeline = await self.get_project_timeline(guild_id, project_id, member_resolver=member_resolver)
        return timeline.to_png(title=f"Timeline: {project_name}")

    async def export_project_timeline_mermaid(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
    ) -> str:
        timeline = await self.get_project_timeline(guild_id, project_id, member_resolver=member_resolver)
        return timeline.to_mermaid()
