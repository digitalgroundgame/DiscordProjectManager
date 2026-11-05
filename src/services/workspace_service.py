"""Workspace application service orchestrating workspace data loading and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.domain.exceptions import ProjectNotFoundError
from src.domain.models import Project, Squad, Task
from src.ports.repositories import IProjectRepo, ISquadRepo, ITaskRepo
from src.ports.unit_of_work import IUnitOfWork
from src.services.base import TransactionalService


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceView:
    """Consolidated domain view for a Project Workspace."""

    project: Project
    squads: list[Squad]
    tasks: list[Task]


class WorkspaceService(TransactionalService):
    """Application service providing unified workspace domain queries and orchestration."""

    def __init__(
        self,
        project_repo: IProjectRepo | Any,
        task_repo: ITaskRepo | Any,
        squad_repo: ISquadRepo | Any | None = None,
        uow: IUnitOfWork | None = None,
    ) -> None:
        self.project_repo = getattr(project_repo, "project_repo", project_repo)
        self.task_repo = getattr(task_repo, "task_repo", task_repo)
        self.squad_repo = getattr(squad_repo, "squad_repo", squad_repo) if squad_repo else None
        self.uow = uow or getattr(task_repo, "uow", None) or getattr(project_repo, "uow", None)

    async def get_project_workspace(
        self,
        project_id: UUID,
        *,
        project: Project | None = None,
        include_archived_tasks: bool = True,
        task_limit: int = 1000,
    ) -> ProjectWorkspaceView:
        """Retrieves complete project workspace context (project, linked squads, and tasks) in O(1) queries."""
        resolved_project = project or await self.project_repo.get_by_id(project_id)
        if not resolved_project:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.")

        squads = await self.project_repo.list_squads_for_project(resolved_project.id)
        tasks, _ = await self.task_repo.list_tasks(
            guild_id=resolved_project.guild_id,
            project_id=resolved_project.id,
            include_archived=include_archived_tasks,
            limit=task_limit,
            count_total=False,
        )

        return ProjectWorkspaceView(
            project=resolved_project,
            squads=squads,
            tasks=tasks,
        )

    async def get_projects_workspaces(
        self,
        guild_id: int,
        projects: list[Project],
        *,
        include_archived_tasks: bool = True,
        task_limit: int = 10000,
    ) -> dict[UUID, ProjectWorkspaceView]:
        """Batch-loads workspace views for a list of projects in a guild without per-project task queries."""
        if not projects:
            return {}

        all_tasks, _ = await self.task_repo.list_tasks(
            guild_id=guild_id,
            include_archived=include_archived_tasks,
            limit=task_limit,
            count_total=False,
        )
        tasks_by_project: dict[UUID, list[Task]] = {p.id: [] for p in projects}
        for t in all_tasks:
            if t.project_id in tasks_by_project:
                tasks_by_project[t.project_id].append(t)

        workspaces: dict[UUID, ProjectWorkspaceView] = {}
        for proj in projects:
            squads = await self.project_repo.list_squads_for_project(proj.id)
            workspaces[proj.id] = ProjectWorkspaceView(
                project=proj,
                squads=squads,
                tasks=tasks_by_project.get(proj.id, []),
            )
        return workspaces
