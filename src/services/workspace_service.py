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
        include_archived_tasks: bool = True,
        task_limit: int = 1000,
    ) -> ProjectWorkspaceView:
        """Retrieves complete project workspace context (project, linked squads, and tasks) in O(1) queries."""
        project = await self.project_repo.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.")

        squads = await self.project_repo.list_squads_for_project(project_id)
        tasks, _ = await self.task_repo.list_tasks(
            guild_id=project.guild_id,
            project_id=project.id,
            include_archived=include_archived_tasks,
            limit=task_limit,
            count_total=False,
        )

        return ProjectWorkspaceView(
            project=project,
            squads=squads,
            tasks=tasks,
        )
