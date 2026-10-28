"""Task dependency graph, tech tree rendering, and payload helper operations."""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.domain.enums import PriorityLevel, TaskHistoryAction
from src.domain.exceptions import TaskNotFoundError, ValidationError
from src.domain.models import Task, TaskHistory
from src.domain.tech_tree import (
    CircularDependencyError,
    SelfDependencyError,
    TechTree,
)
from src.ports.workspace import SyncWorkspaceResult, TaskWorkspacePort, TaskWorkspaceRef
from src.services.base import TransactionalService

if TYPE_CHECKING:
    from src.ports.renderer import TechTreeRendererPort
    from src.ports.repositories import ITaskRepo
    from src.services.project_service import ProjectService


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


def _build_task_payload(task: Task, **extras: Any) -> dict[str, Any]:
    """Helper constructing standardized transactional outbox payloads for task events."""
    payload: dict[str, Any] = {
        "task_id": str(task.id),
        "short_id": task.short_id,
        "title": task.title,
        "body": task.body,
        "status": task.status.value,
        "priority": task.priority.value,
        "guild_id": task.guild_id,
        "project_id": str(task.project_id) if task.project_id else None,
        "creator_discord_id": task.creator_discord_id,
        "assignee_discord_id": task.assignee_discord_id,
        "watchers": list(task.watchers),
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "version": task.version,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "discord_thread_id": task.discord_thread_id,
        "discord_message_id": task.discord_message_id,
    }
    payload.update(extras)
    return payload


def _compute_task_details_diff(
    current_task: Task,
    *,
    title: str | None,
    body: str | None,
    clear_body: bool,
    due_at: datetime | None,
    clear_due_at: bool,
    watchers: list[int] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Computes structured field diffs and human-readable change descriptions for task detail edits."""
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
    return diffs, changes


class TaskGraphServiceMixin(TransactionalService):
    """Mixin providing dependency DAG mutations, workspace orchestration, and TechTree rendering."""

    task_repo: ITaskRepo
    project_service: ProjectService
    workspace: TaskWorkspacePort | None
    tree_renderer: TechTreeRendererPort | None

    async def provision_task_workspace(
        self,
        task_id: UUID,
        *,
        target_container: int | Any = None,
        preferred_channel_id: int | None = None,
        workspace: TaskWorkspacePort | None = None,
    ) -> tuple[Task, TaskWorkspaceRef | None]:
        """Provisions an execution workspace (e.g. Discord Thread) for an existing task."""
        task = await self.get_by_id(task_id)  # type: ignore[attr-defined]
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
            await self.update_discord_message_ids(  # type: ignore[attr-defined]
                task.id,
                discord_message_id=ref.message_id,
                discord_thread_id=ref.thread_id,
            )
            updated = await self.get_by_id(task.id)  # type: ignore[attr-defined]
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
        task = await self.create_task(  # type: ignore[attr-defined]
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

        if prerequisite_short_ids:
            explicit_prereqs = parse_prerequisite_short_ids(prerequisite_short_ids)
            for p_key in explicit_prereqs:
                p_task = await self.get_by_short_id(guild_id, p_key)  # type: ignore[attr-defined]
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
        task = await self.get_by_id(task_id)  # type: ignore[attr-defined]
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

    async def add_dependency(
        self,
        guild_id: int,
        task_short_id: str,
        depends_on_short_id: str,
        actor_discord_id: int | None = None,
    ) -> bool:
        """Adds a dependency indicating that task_short_id depends on depends_on_short_id."""
        async with self._transaction() as uow:  # type: ignore[attr-defined]
            task = await uow.tasks.get_by_short_id(guild_id, task_short_id.upper())
            if not task:
                raise TaskNotFoundError(f"Task '{task_short_id}' was not found in this server.")

            depends_on_task = await uow.tasks.get_by_short_id(guild_id, depends_on_short_id.upper())
            if not depends_on_task:
                raise TaskNotFoundError(f"Prerequisite task '{depends_on_short_id}' was not found in this server.")

            if task.id == depends_on_task.id:
                raise ValidationError("A task cannot depend on itself.")

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
        async with self._transaction() as uow:  # type: ignore[attr-defined]
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

        prereqs = [t for pid in prereq_ids if (t := await self.task_repo.get_by_id(pid)) is not None]
        dependents = [t for did in dependent_ids if (t := await self.task_repo.get_by_id(did)) is not None]
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
        limit: int = 1000,
    ) -> TechTree:
        """Loads and returns an analyzed TechTree domain model for a project."""
        tasks, _ = await self.task_repo.list_tasks(
            guild_id=guild_id,
            project_id=project_id,
            include_archived=False,
            limit=limit,
            count_total=False,
        )
        task_ids = [t.id for t in tasks]
        deps = await self.task_repo.get_dependencies_for_tasks(task_ids) if tasks else []
        return TechTree.build(tasks, deps, member_resolver=member_resolver)

    async def get_project_tree(
        self,
        guild_id: int,
        project_id: UUID,
        member_resolver: Any = None,
        limit: int = 1000,
    ) -> TechTree:
        """Alias for get_project_tech_tree."""
        return await self.get_project_tech_tree(
            guild_id=guild_id,
            project_id=project_id,
            member_resolver=member_resolver,
            limit=limit,
        )

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
        renderer: TechTreeRendererPort | None = None,
    ) -> io.BytesIO:
        project = await self.project_service.get_by_id(project_id)
        project_name = project.name if project else "Project Tech Tree"
        tree = await self.get_project_tech_tree(guild_id, project_id, member_resolver=member_resolver)
        effective_renderer = renderer or self.tree_renderer
        return tree.to_png(title=f"Tech Tree: {project_name}", orientation=orientation, renderer=effective_renderer)
