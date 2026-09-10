# Mandatory Project Containers for Tasks

## Context and Decision
Early design discussions in `project-thoughts.md` proposed a `/task-standalone` command for quick personal reminders decoupled from project containers. However, supporting standalone tasks introduced significant architectural complexity: non-deterministic short IDs (`TASK-XXXXX` vs sequential `PRJ-N`), lack of mapped Squad roles, missing Tech Tree DAG anchors, and ambiguity around where Discord thread workspaces should be provisioned.

We decided that for Phase 1 MVP, all tasks must belong to a Project container (`TaskTable.project_id` is non-nullable). Servers needing personal or ad-hoc task tracking should configure a general catch-all project container (e.g. `OPS` or `GENERAL`).

## Considered Options
- **First-Class Standalone Tasks (Deferred)**: Would allow ad-hoc reminders without a project container, but fragments short ID schemes, permission matrices, and forum workspace routing. Deferred for potential revisiting in post-MVP phases if personal capture demands it.
- **Mandatory Project Containers with Catch-All Projects (Chosen)**: Preserves strict relational integrity, uniform sequential short IDs (`INF-42`), deterministic Forum Channel thread provisioning, and clean Tech Tree DAG rendering.

## Consequences
- `TaskTable.project_id` is enforced as `nullable=False` in PostgreSQL.
- Task short IDs are guaranteed to follow project-scoped sequential prefixes.
- Standalone task generation logic is removed from `TaskService`.
- Re-evaluating standalone or personal inbox tasks remains open for consideration in future phases (e.g. alongside personal calendar sync in Phase 2).
