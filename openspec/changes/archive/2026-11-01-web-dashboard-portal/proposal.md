# Proposal

## Why

While Discord serves as the primary day-to-day execution surface for task creation and thread discussions, project leads, leadership, and stakeholders need a browser-accessible, high-level view of multi-project roadmaps, filterable Kanban/Matrix boards, and interactive Tech Tree DAGs without context-switching through dozens of individual Discord threads. Building a lightweight, read-only web dashboard directly on the existing FastAPI adapter (`src/adapters/api/`) unlocks rich visual exploration while sharing the exact same PostgreSQL tables, pure domain models, and `ActorPrincipal` authorization rules.

## What Changes

- **Discord OAuth2 & Session Authentication (`src/adapters/api/auth_routes.py`)**:
  - Add `/auth/login`, `/auth/callback`, `/auth/logout`, and `/api/me` endpoints using Discord OAuth2 (`identify` scope) and signed HTTP-only session cookies.
  - Resolve the authenticated user's live guild roles and administrative permissions via the in-process `MemberDirectoryPort` (`DiscordMemberDirectory`) into a domain `ActorPrincipal`.
  - Provide a zero-config development authentication fallback when `ENVIRONMENT=development` and `DISCORD_CLIENT_SECRET` is unconfigured so developers can explore seeded workspaces locally.
- **Read-Only Dashboard REST API (`src/adapters/api/dashboard_routes.py`)**:
  - Add `/api/projects` returning active (and optionally archived) projects filtered by `AuthService.filter_visible_projects`, enriched with task completion metrics, overdue counts, and mapped Squads.
  - Add `/api/projects/{project_id}/workspace` returning the project workspace context (`WorkspaceService.get_project_workspace`), including tasks,overdue flags, squad metadata, tags, and Discord thread deep-link URLs.
  - Add `/api/projects/{project_id}/tech-tree` returning the analyzed `TechTree` DAG (`TaskService.get_project_tech_tree`), including node readiness states (`complete`, `active`, `available`, `locked`, `blocked`), topological depth layers, critical path, bottleneck metrics, and dependency edges.
- **Zero-Build Responsive Web Portal (`src/adapters/api/static/`)**:
  - Serve a responsive, zero-Node-build web UI mounted at `/dashboard` (with `/` redirecting to `/dashboard`) featuring:
    1. **Interactive Tech Tree DAG Visualizer**: Client-side pan/zoom/fit DAG rendered with Cytoscape.js + Dagre (`LR` and `TB` orientations), live readiness state styling, critical-path highlighting, and a slide-out Node Inspector panel with prerequisite/dependent links and Discord Thread deep-links.
    2. **Kanban & Table Matrix Views**: Project task boards grouped by Status, Priority, or Assignee, with real-time filtering by Overdue State, Squad, Tags (`metadata_json`), Due Dates, and text search.
- **Authorization Visibility Alignment (`src/services/auth_service.py`)**:
  - Update `AuthService.can_view_project` to also recognize configured Guild Lead users (`IGuildLeadUserRepository`) and Guild Lead roles (`IGuildLeadRoleRepository`) alongside Server Managers, Project Leads, and mapped Squad members, and enforce project visibility across web dashboard API endpoints.

## Capabilities

### New Capabilities
- `web-dashboard-portal`: Browser-based read-only portal providing Discord OAuth2 session authentication, multi-project workspace overview, filterable Kanban and Table Matrix views, and an interactive Tech Tree DAG visualizer with node inspection.

### Modified Capabilities
- `project-visibility-authorization`: Enforces role-based project visibility across web portal endpoints and recognizes configured Guild Lead users and roles in `can_view_project`.

## Impact

- **Driving API Adapter (`src/adapters/api/`)**: Expanded from a single module into modular route and static asset files (`app.py`, `dependencies.py`, `auth_routes.py`, `dashboard_routes.py`, `static/`).
- **Composition Root & Config (`src/bootstrap/container.py`, `src/config.py`)**: Wires `project_service`, `task_service`, `squad_service`, `workspace_service`, `auth_service`, and `member_directory` onto `api_app.state`; adds `DISCORD_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`, and `SESSION_SECRET_KEY` settings.
- **Authorization Service (`src/services/auth_service.py`)**: Extends `can_view_project` to check guild-wide lead roles/users.
- **Database & Infrastructure**: Zero database schema migrations or new tables required; zero Node.js build steps added to `Dockerfile`.
