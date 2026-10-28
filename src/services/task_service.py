from __future__ import annotations

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
from src.domain.tech_tree import resolve_member_name
from src.ports.renderer import TechTreeRendererPort
from src.ports.repositories import ITaskRepo
from src.ports.unit_of_work import IUnitOfWork
from src.ports.workspace import TaskWorkspacePort
from src.services.base import TransactionalService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.task_graph_ops import (
    TaskGraphServiceMixin,
    _build_task_payload,
    _compute_task_details_diff,
    parse_inline_dependencies,
    parse_prerequisite_short_ids,
)

__all__ = [
    "StaleVersionError",
    "TaskService",
    "_build_task_payload",
    "parse_inline_dependencies",
    "parse_prerequisite_short_ids",
    "resolve_member_name",
]


class TaskService(TaskGraphServiceMixin, TransactionalService):
    def __init__(
        self,
        task_repo: ITaskRepo,
        project_service: ProjectService,
        outbox_service: OutboxService,
        uow: IUnitOfWork | None = None,
        workspace: TaskWorkspacePort | None = None,
        tree_renderer: TechTreeRendererPort | None = None,
    ):
        self.task_repo = task_repo
        self.project_service = project_service
        self.outbox_service = outbox_service
        self.uow = uow
        self.workspace = workspace
        self.tree_renderer = tree_renderer

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[IUnitOfWork]:
        """Manages an atomic transaction scope via UnitOfWork if configured, or fallback if omitted."""
        async with super()._transaction(
            tasks=self.task_repo,
            projects=self.project_service.project_repo,
            outbox=self.outbox_service.outbox_repo,
        ) as active_uow:
            yield active_uow

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
                payload=_build_task_payload(saved_task),
                outbox_repo=uow.outbox,
            )

            # Link any prerequisites specified explicitly or found in body tags (atomically within transaction)
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
                payload=_build_task_payload(
                    updated_task,
                    old_status=old_status.value,
                    new_status=new_status.value,
                    actor_discord_id=actor_discord_id,
                    completed_at=updated_task.completed_at.isoformat() if updated_task.completed_at else None,
                    notes=notes,
                ),
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
                            payload=_build_task_payload(
                                dep_task,
                                actor_discord_id=actor_discord_id,
                                unblocked_by_task_id=str(task_id),
                                unblocked_by_short_id=updated_task.short_id,
                                unblocked_by_title=updated_task.title,
                                prerequisites=[{"short_id": pt.short_id, "title": pt.title} for pt in prereq_tasks],
                            ),
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
                payload=_build_task_payload(
                    task,
                    actor_discord_id=actor_discord_id,
                    note=note_text.strip(),
                    notes=note_text.strip(),
                    is_completed=task.status == TaskStatus.COMPLETED,
                ),
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
                payload=_build_task_payload(
                    updated_task,
                    actor_discord_id=actor_discord_id,
                    old_assignee_id=old_assignee,
                    new_assignee_id=new_assignee_id,
                    is_completed=updated_task.status == TaskStatus.COMPLETED,
                    update_type="assignee",
                ),
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
                payload=_build_task_payload(
                    updated_task,
                    actor_discord_id=actor_discord_id,
                    old_priority=old_priority.value,
                    new_priority=new_priority.value,
                    is_completed=updated_task.status == TaskStatus.COMPLETED,
                    update_type="priority",
                ),
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

            diffs, changes = _compute_task_details_diff(
                current_task,
                title=title,
                body=body,
                clear_body=clear_body,
                due_at=due_at,
                clear_due_at=clear_due_at,
                watchers=watchers,
            )

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
                    payload=_build_task_payload(
                        updated_task,
                        actor_discord_id=actor_discord_id,
                        old_watchers=current_task.watchers,
                        changes=changes,
                        diffs=diffs,
                        is_completed=updated_task.status == TaskStatus.COMPLETED,
                        update_type="details",
                    ),
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
