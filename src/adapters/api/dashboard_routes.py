"""Read-only REST endpoints for Project Workspaces, Kanban/Matrix boards, and Tech Tree DAGs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.adapters.api.dependencies import (
    get_auth_service,
    get_current_principal,
    get_member_directory,
    get_project_service,
    get_squad_service,
    get_task_service,
    get_workspace_service,
)
from src.domain.actor import ActorPrincipal
from src.domain.enums import TaskStatus
from src.domain.models import Project, Squad, Task
from src.ports.member_directory import MemberDirectoryPort
from src.services.auth_service import AuthService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.workspace_service import WorkspaceService

dashboard_router = APIRouter(tags=["dashboard"])


async def _resolve_display_names(
    member_directory: MemberDirectoryPort | None,
    guild_id: int,
    user_ids: set[int],
) -> dict[int, str]:
    """Resolves a mapping of user_id -> display_name for a set of Discord user IDs."""
    names: dict[int, str] = {}
    if not member_directory or not guild_id:
        for uid in user_ids:
            names[uid] = f"User {uid}"
        return names

    for uid in user_ids:
        resolved = await member_directory.get_member_display_name(guild_id, uid)
        names[uid] = resolved or f"User {uid}"
    return names


def _extract_task_tags(task: Task) -> list[str]:
    """Extracts string tags from task.metadata_json for portal filtering."""
    tags: list[str] = []
    meta = task.metadata_json or {}
    raw_tags = meta.get("tags")
    if isinstance(raw_tags, list):
        for item in raw_tags:
            if item and str(item).strip():
                tags.append(str(item).strip())
    elif isinstance(raw_tags, str) and raw_tags.strip():
        tags.extend(t.strip() for t in raw_tags.split(",") if t.strip())
    if meta.get("blocked") is True and "blocked" not in [t.lower() for t in tags]:
        tags.append("blocked")
    if meta.get("category") and str(meta["category"]).strip():
        tags.append(str(meta["category"]).strip())
    return list(dict.fromkeys(tags))


def _serialize_squad(squad: Squad) -> dict[str, Any]:
    return {
        "id": str(squad.id),
        "name": squad.name,
        "discord_role_id": squad.discord_role_id,
        "discord_role_id_str": str(squad.discord_role_id),
    }


def _serialize_project_summary(
    project: Project,
    squads: list[Squad],
    tasks: list[Task],
    *,
    lead_name: str | None = None,
    can_create_task: bool = False,
    is_project_lead: bool = False,
) -> dict[str, Any]:
    active_tasks = [t for t in tasks if not t.is_archived]
    total = len(active_tasks)
    completed = sum(1 for t in active_tasks if t.is_completed)
    in_progress = sum(1 for t in active_tasks if t.status == TaskStatus.IN_PROGRESS)
    not_started = sum(1 for t in active_tasks if t.status == TaskStatus.NOT_STARTED)
    overdue = sum(1 for t in active_tasks if t.is_overdue)
    pct = int((completed / total) * 100) if total > 0 else 0

    return {
        "id": str(project.id),
        "guild_id": project.guild_id,
        "guild_id_str": str(project.guild_id),
        "name": project.name,
        "prefix": project.prefix,
        "description": project.description or "",
        "category": project.category,
        "discord_channel_id": project.discord_channel_id,
        "discord_channel_id_str": str(project.discord_channel_id) if project.discord_channel_id else None,
        "discord_jump_url": project.discord_jump_url,
        "discord_app_url": project.discord_app_url,
        "lead_discord_id": project.lead_discord_id,
        "lead_discord_id_str": str(project.lead_discord_id) if project.lead_discord_id else None,
        "lead_name": lead_name,
        "is_archived": project.is_archived,
        "squads": [_serialize_squad(s) for s in squads],
        "metrics": {
            "total_tasks": total,
            "completed_tasks": completed,
            "in_progress_tasks": in_progress,
            "not_started_tasks": not_started,
            "overdue_tasks": overdue,
            "percent_complete": pct,
        },
        "permissions": {
            "can_create_task": can_create_task,
            "is_project_lead": is_project_lead,
        },
    }


@dashboard_router.get("/api/projects")
async def list_visible_projects(
    request: Request,
    include_archived: bool = Query(default=False),
    principal: ActorPrincipal = Depends(get_current_principal),
    project_service: ProjectService = Depends(get_project_service),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """Lists all projects the caller is authorized to view along with summary task metrics."""
    guild_id = principal.guild_id or 0
    all_projects = await project_service.list_projects(guild_id, include_archived=include_archived)
    visible_projects = await auth_service.filter_visible_projects(principal, all_projects)

    member_directory = get_member_directory(request)
    lead_ids = {p.lead_discord_id for p in visible_projects if p.lead_discord_id}
    lead_names = await _resolve_display_names(member_directory, guild_id, lead_ids)

    workspaces_by_id = await workspace_service.get_projects_workspaces(
        guild_id,
        visible_projects,
        include_archived_tasks=include_archived,
    )

    serialized: list[dict[str, Any]] = []
    for proj in visible_projects:
        ws = workspaces_by_id.get(proj.id) or await workspace_service.get_project_workspace(
            proj.id,
            project=proj,
            include_archived_tasks=include_archived,
        )
        project_role_ids = await auth_service.get_project_role_ids(proj, squads=ws.squads)
        can_create = await auth_service.can_create_task_in_project(
            principal,
            proj.id,
            guild_id=proj.guild_id,
            project=proj,
            project_role_ids=project_role_ids,
        )
        is_lead = await auth_service.is_project_lead(principal, proj.id, project=proj)
        serialized.append(
            _serialize_project_summary(
                proj,
                ws.squads,
                ws.tasks,
                lead_name=lead_names.get(proj.lead_discord_id) if proj.lead_discord_id else None,
                can_create_task=can_create,
                is_project_lead=is_lead,
            )
        )

    return {"projects": serialized}


@dashboard_router.get("/api/projects/{project_id}/workspace")
async def get_project_workspace_endpoint(
    project_id: UUID,
    request: Request,
    include_archived: bool = Query(default=False),
    principal: ActorPrincipal = Depends(get_current_principal),
    project_service: ProjectService = Depends(get_project_service),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    task_service: TaskService = Depends(get_task_service),
    squad_service: SquadService = Depends(get_squad_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """Returns consolidated project workspace data for Kanban and Table Matrix views."""
    project = await project_service.get_by_id(project_id)
    if not project or (principal.guild_id and project.guild_id != principal.guild_id):
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    ws = await workspace_service.get_project_workspace(
        project.id,
        project=project,
        include_archived_tasks=include_archived,
    )
    project_role_ids = await auth_service.get_project_role_ids(project, squads=ws.squads)

    if not await auth_service.can_view_project(principal, project, project_role_ids=project_role_ids):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view this project workspace.",
        )

    member_directory = get_member_directory(request)
    user_ids: set[int] = set()
    if project.lead_discord_id:
        user_ids.add(project.lead_discord_id)
    for t in ws.tasks:
        user_ids.add(t.creator_discord_id)
        if t.assignee_discord_id:
            user_ids.add(t.assignee_discord_id)

    name_map = await _resolve_display_names(member_directory, project.guild_id, user_ids)
    active_ws_tasks = [t for t in ws.tasks if not t.is_archived]
    tree = await task_service.get_project_tech_tree(
        project.guild_id,
        project.id,
        member_resolver=name_map,
        tasks=active_ws_tasks,
    )

    # Map users to squads in this project so tasks can be filtered by squad
    squad_member_map: dict[int, list[dict[str, str]]] = {}
    seen_squad_ids: set[UUID] = set()
    for sq in ws.squads:
        if sq.id in seen_squad_ids:
            continue
        seen_squad_ids.add(sq.id)
        members = await squad_service.list_members(sq.id)
        for m in members:
            squad_member_map.setdefault(m.user_discord_id, []).append({"id": str(sq.id), "name": sq.name})

    default_squads = [{"id": str(s.id), "name": s.name} for s in ws.squads]

    all_tags: set[str] = set()
    assignees_seen: dict[int, str] = {}
    tasks_payload: list[dict[str, Any]] = []

    for t in ws.tasks:
        if not include_archived and t.is_archived:
            continue
        node = tree.nodes.get(t.id)
        readiness_state = node.state.value if node else ("complete" if t.is_completed else "available")
        prereqs = (
            [
                {"id": str(p.id), "short_id": p.short_id, "title": p.title, "state": p.state.value}
                for p in tree.prerequisites(t.id)
            ]
            if node
            else []
        )
        dependents = (
            [
                {"id": str(d.id), "short_id": d.short_id, "title": d.title, "state": d.state.value}
                for d in tree.dependents(t.id)
            ]
            if node
            else []
        )

        tags = _extract_task_tags(t)
        all_tags.update(tags)

        assignee_name = name_map.get(t.assignee_discord_id) if t.assignee_discord_id else None
        if t.assignee_discord_id and assignee_name:
            assignees_seen[t.assignee_discord_id] = assignee_name

        task_squads = (
            squad_member_map.get(t.assignee_discord_id)
            if (t.assignee_discord_id and t.assignee_discord_id in squad_member_map)
            else default_squads
        )

        can_mutate = await auth_service.can_mutate_task(
            principal,
            t,
            project=project,
            project_role_ids=project_role_ids,
        )
        can_delete = await auth_service.can_delete_task(
            principal,
            t,
            project=project,
        )

        tasks_payload.append(
            {
                "id": str(t.id),
                "short_id": t.short_id,
                "task_number": t.task_number,
                "version": t.version,
                "title": t.title,
                "body": t.body or "",
                "status": t.status.value,
                "priority": t.priority.value,
                "readiness_state": readiness_state,
                "is_completed": t.is_completed,
                "is_archived": t.is_archived,
                "is_overdue": t.is_overdue,
                "creator_discord_id": t.creator_discord_id,
                "creator_discord_id_str": str(t.creator_discord_id),
                "creator_name": name_map.get(t.creator_discord_id, f"User {t.creator_discord_id}"),
                "assignee_discord_id": t.assignee_discord_id,
                "assignee_discord_id_str": str(t.assignee_discord_id) if t.assignee_discord_id else None,
                "assignee_name": assignee_name,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "discord_thread_id": t.discord_thread_id,
                "discord_thread_id_str": str(t.discord_thread_id) if t.discord_thread_id else None,
                "discord_message_id": t.discord_message_id,
                "discord_message_id_str": str(t.discord_message_id) if t.discord_message_id else None,
                "discord_jump_url": t.discord_jump_url or project.discord_jump_url,
                "discord_app_url": t.discord_app_url or project.discord_app_url,
                "has_dedicated_thread": t.discord_thread_id is not None,
                "tags": tags,
                "squads": task_squads,
                "prerequisites": prereqs,
                "dependents": dependents,
                "permissions": {
                    "can_mutate": can_mutate,
                    "can_delete": can_delete,
                },
            }
        )

    can_create = await auth_service.can_create_task_in_project(
        principal,
        project.id,
        guild_id=project.guild_id,
        project=project,
        project_role_ids=project_role_ids,
    )
    is_lead = await auth_service.is_project_lead(principal, project.id, project=project)

    return {
        "project": _serialize_project_summary(
            project,
            ws.squads,
            ws.tasks,
            lead_name=name_map.get(project.lead_discord_id) if project.lead_discord_id else None,
            can_create_task=can_create,
            is_project_lead=is_lead,
        ),
        "squads": [_serialize_squad(s) for s in ws.squads],
        "tasks": tasks_payload,
        "filters": {
            "tags": sorted(all_tags),
            "assignees": [
                {"user_id": uid, "user_id_str": str(uid), "name": uname}
                for uid, uname in sorted(assignees_seen.items(), key=lambda item: item[1].lower())
            ],
        },
    }


@dashboard_router.get("/api/projects/{project_id}/tech-tree")
async def get_project_tech_tree_endpoint(
    project_id: UUID,
    request: Request,
    principal: ActorPrincipal = Depends(get_current_principal),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """Returns the analyzed Tech Tree DAG (nodes, edges, readiness states, critical path, and bottlenecks)."""
    project = await project_service.get_by_id(project_id)
    if not project or (principal.guild_id and project.guild_id != principal.guild_id):
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    if not await auth_service.can_view_project(principal, project):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view this project's Tech Tree.",
        )

    tasks, _ = await task_service.list_tasks(
        guild_id=project.guild_id,
        project_id=project.id,
        include_archived=False,
        limit=1000,
    )
    task_by_id = {t.id: t for t in tasks}

    member_directory = get_member_directory(request)
    assignee_ids = {t.assignee_discord_id for t in tasks if t.assignee_discord_id}
    name_map = await _resolve_display_names(member_directory, project.guild_id, assignee_ids)

    tree = await task_service.get_project_tech_tree(
        project.guild_id,
        project.id,
        member_resolver=name_map,
        tasks=tasks,
    )

    metrics = tree.metrics()
    crit_nodes = tree.critical_path()
    crit_ids = {n.id for n in crit_nodes}
    bottlenecks = tree.bottlenecks(limit=5)
    bottleneck_map = {b.node.id: b for b in bottlenecks}

    nodes_payload: list[dict[str, Any]] = []
    edges_payload: list[dict[str, Any]] = []

    for uid, node in tree.nodes.items():
        task = task_by_id.get(uid)
        b_info = bottleneck_map.get(uid)
        prereqs = [
            {
                "id": str(p.id),
                "short_id": p.short_id,
                "title": p.title,
                "state": p.state.value,
            }
            for p in tree.prerequisites(uid)
        ]
        dependents = [
            {
                "id": str(d.id),
                "short_id": d.short_id,
                "title": d.title,
                "state": d.state.value,
            }
            for d in tree.dependents(uid)
        ]

        nodes_payload.append(
            {
                "id": str(node.id),
                "short_id": node.short_id,
                "title": node.title,
                "body": node.body,
                "status": node.status.value,
                "priority": node.priority.value,
                "state": node.state.value,
                "depth_layer": node.depth_layer,
                "assignee_discord_id": node.assignee_discord_id,
                "assignee_discord_id_str": str(node.assignee_discord_id) if node.assignee_discord_id else None,
                "assignee_name": node.assignee_name,
                "due_at": task.due_at.isoformat() if (task and task.due_at) else None,
                "is_overdue": task.is_overdue if task else False,
                "version": task.version if task else 1,
                "discord_thread_id": task.discord_thread_id if task else None,
                "discord_thread_id_str": str(task.discord_thread_id) if (task and task.discord_thread_id) else None,
                "discord_jump_url": (task.discord_jump_url if task else None) or project.discord_jump_url,
                "discord_app_url": (task.discord_app_url if task else None) or project.discord_app_url,
                "has_dedicated_thread": bool(task and task.discord_thread_id),
                "is_on_critical_path": uid in crit_ids,
                "blocked_dependents_count": b_info.blocked_dependents_count if b_info else 0,
                "prerequisites": prereqs,
                "dependents": dependents,
            }
        )

        for prereq_id in node.prerequisite_ids:
            if prereq_id in tree.nodes:
                prereq_node = tree.nodes[prereq_id]
                edges_payload.append(
                    {
                        "id": f"{prereq_node.short_id}->{node.short_id}",
                        "source": str(prereq_id),
                        "target": str(uid),
                        "source_short_id": prereq_node.short_id,
                        "target_short_id": node.short_id,
                        "is_critical_edge": (prereq_id in crit_ids and uid in crit_ids),
                    }
                )

    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "prefix": project.prefix,
            "discord_jump_url": project.discord_jump_url,
            "discord_app_url": project.discord_app_url,
        },
        "metrics": {
            "total_tasks": metrics.total_tasks,
            "completed_tasks": metrics.completed_tasks,
            "active_tasks": metrics.active_tasks,
            "available_tasks": metrics.available_tasks,
            "locked_tasks": metrics.locked_tasks,
            "blocked_tasks": metrics.blocked_tasks,
            "percent_complete": metrics.percent_complete,
            "max_depth": metrics.max_depth,
            "total_dependencies": metrics.total_dependencies,
        },
        "critical_path": [
            {"id": str(n.id), "short_id": n.short_id, "title": n.title, "state": n.state.value} for n in crit_nodes
        ],
        "bottlenecks": [
            {
                "id": str(b.node.id),
                "short_id": b.node.short_id,
                "title": b.node.title,
                "state": b.node.state.value,
                "blocked_dependents_count": b.blocked_dependents_count,
                "direct_dependents_count": b.direct_dependents_count,
            }
            for b in bottlenecks
        ],
        "nodes": nodes_payload,
        "edges": edges_payload,
    }
