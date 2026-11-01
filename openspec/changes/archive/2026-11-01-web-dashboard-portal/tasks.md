# Tasks

## 1. Configuration, Composition Root & Authorization Visibility

- [x] 1.1 Add `discord_jump_url` properties to `Task` and `Project` in `src/domain/models.py`, add `DISCORD_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`, and `SESSION_SECRET_KEY` settings to `src/config.py`, and wire `project_service`, `task_service` (with `task_service.workspace = bot.workspace`), `squad_service`, `workspace_service`, `auth_service`, and `member_directory` onto `api_app.state` in `src/bootstrap/container.py`; verify with `uv run pytest tests/test_architecture.py`
- [x] 1.2 Update `AuthService.can_view_project` in `src/services/auth_service.py` to authorize designated Guild Lead users (`guild_lead_user_repo`) and Guild Lead roles (`guild_lead_role_repo`) alongside Server Managers, Project Leads, and Squad roles; verify with unit tests in `tests/test_auth_service.py`

## 2. Discord OAuth2 & Session Authentication Adapter

- [x] 2.1 Implement stateless HMAC-SHA256 session cookie signing/verification (`src/adapters/api/session.py`) and FastAPI service/actor dependencies (`src/adapters/api/dependencies.py`) that resolve the caller's live `ActorPrincipal` via `MemberDirectoryPort`; verify with unit tests in `tests/test_web_dashboard_api.py`
- [x] 2.2 Implement `/auth/login`, `/auth/callback`, `/auth/logout`, and `/api/me` in `src/adapters/api/auth_routes.py` including the `ENVIRONMENT=development` local login fallback when `DISCORD_CLIENT_SECRET` is unset; verify OAuth2 callback, dev login, non-member denial, and logout flows in `tests/test_web_dashboard_api.py`

## 3. Read-Only Dashboard REST Endpoints

- [x] 3.1 Implement `/api/projects`, `/api/projects/{project_id}/workspace`, and `/api/projects/{project_id}/tech-tree` in `src/adapters/api/dashboard_routes.py` using `ProjectService`, `WorkspaceService`, `TaskService`, and `AuthService`, including `discord_jump_url` (with fallback to the project Forum Channel URL) and task `version` on serialized payloads; verify project visibility filtering, `403 Forbidden` on unauthorized projects, workspace task/squad serialization, Discord jump URLs, and `TechTree` readiness/metrics output in `tests/test_web_dashboard_api.py`

## 4. Zero-Build Web Portal Frontend (Tech Tree DAG, Kanban & Matrix Views)

- [x] 4.1 Create the responsive web portal static assets (`src/adapters/api/static/index.html`, `styles.css`, `app.js`) and mount `/dashboard` (plus `/` redirect) in `src/adapters/api/app.py`, implementing the Interactive Tech Tree DAG canvas (Cytoscape.js + Dagre, `LR`/`TB` orientation, readiness state badges, critical path toggle, and slide-out Node Inspector panel with Discord Thread links) and filterable Kanban/Matrix views (grouped by Status, Priority, or Assignee; filtered by Overdue State, Squad, Tags, Due Date, and search); verify static asset serving and portal routes in `tests/test_web_dashboard_api.py`
- [x] 4.2 Run full lint, format, and test suite verification (`make check`) and verify all architectural boundary and line-count fitness assertions pass (`uv run pytest -v tests/`)
