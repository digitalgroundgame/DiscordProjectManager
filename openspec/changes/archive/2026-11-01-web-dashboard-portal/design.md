# Design

## Context

`dgg-pm` already runs a FastAPI application (`src/adapters/api/app.py`) via Uvicorn in the same asyncio event loop as `DggPmBot` and `OutboxWorker` (`src/main.py`). Currently, `api_app` only exposes `/healthz`, `/metrics`, and `/calendar/{token}.ics`. Issue #21 introduces a read-only browser portal overlay for exploring Project Workspaces, Kanban/Matrix boards, and interactive Tech Tree DAGs without duplicating database queries or diverging from domain authorization policies.

See `proposal.md` for motivation and capability scope.

## Goals / Non-Goals

**Goals:**
- Expose read-only JSON endpoints and a responsive web UI from the existing FastAPI server (`src/adapters/api/`) with zero database schema changes.
- Reuse `ActorPrincipal`, `DiscordMemberDirectory`, and `AuthService` so web permissions mirror Discord permissions in real time.
- Deliver an interactive Tech Tree DAG visualizer (pan, zoom, `LR`/`TB` orientation, critical-path highlight, node inspector drawer) and filterable Kanban/Matrix views using a zero-Node-build static frontend.
- Preserve all 4-layer hexagonal architectural fitness tests (`tests/test_architecture.py`), including the 600-line module limit and strict ban on ORM imports in driving adapters.

**Non-Goals:**
- In-browser task or project mutations (creating, editing, or dragging tasks to mutate status) are out of scope for this read-only portal milestone.
- External non-Discord user account databases or separate frontend container deployments.
- Introducing a Node.js/npm/Vite build pipeline to `Dockerfile` or `devenv.nix`.

## Decisions

### 1. In-Process `ActorPrincipal` Resolution via `MemberDirectoryPort` + Minimal OAuth2
- **Decision**: Authenticate browser sessions with Discord OAuth2 using only the `identify` scope, store the authenticated `user_id` (plus display metadata) in an HMAC-SHA256 signed HTTP-only cookie, and resolve live roles and admin flags on API requests via `DiscordMemberDirectory.get_member_principal(guild_id, user_id)`.
- **Rationale**: Because `DggPmBot` runs in the same Python process and maintains an in-memory guild cache (with REST fallback), we do not need the `guilds.members.read` OAuth token refreshes or separate role-caching tables. Role promotions or revocations in Discord take effect immediately on the web portal.
- **Development Fallback**: When `ENVIRONMENT == "development"` and `DISCORD_CLIENT_SECRET` is unset, `/auth/login` mints a local development session mapped to a server-manager `ActorPrincipal` (or a query-specified `user_id`) so developers can test the dashboard against `make seed` data without registering a Discord OAuth redirect URL.
- **Alternatives Considered**:
  - *Storing Discord OAuth access tokens in PostgreSQL and polling `/users/@me/guilds/{id}/member`*: Rejected due to Discord per-user rate limits, token refresh complexity, and unnecessary database state.

### 2. Zero-Build Static ES Module Frontend with Cytoscape.js & Dagre
- **Decision**: Serve static HTML, CSS, and vanilla ES module JavaScript directly from `src/adapters/api/static/` mounted on FastAPI (`/dashboard`, with `/` redirecting to `/dashboard`). Use Cytoscape.js + `cytoscape-dagre` (loaded via CDN with pinned versions) for interactive DAG layout and pan/zoom canvas rendering.
- **Rationale**: Keeps `Dockerfile`, `pyproject.toml`, and `make check` 100% Python-native while providing hardware-accelerated canvas pan/zoom, automatic hierarchical DAG layout (`LR` and `TB`), readiness state styling, and click-to-inspect events.
- **Alternatives Considered**:
  - *React + Vite + XYFlow multi-stage Docker build*: Rejected to avoid adding Node.js toolchain maintenance for a read-only portal.
  - *Server-side Pillow PNGs in browser*: Rejected because Issue #21 specifically requires interactive pan, zoom, and clickable node inspector panels.

### 3. Modular Decomposition of `src/adapters/api/`
- **Decision**: Split the API driving adapter into focused modules under `src/adapters/api/`:
  - `app.py`: FastAPI initialization, router registration, `/healthz`, `/metrics`, and `/calendar/*` routes.
  - `dependencies.py`: FastAPI dependency functions resolving `ProjectService`, `TaskService`, `SquadService`, `WorkspaceService`, `AuthService`, `MemberDirectoryPort`, and the current request's `ActorPrincipal` from `api_app.state`.
  - `session.py`: Stateless HMAC-SHA256 cookie signing and verification using Python's standard library (`hmac`, `hashlib`, `base64`, `json`)—zero extra third-party crypto dependencies required.
  - `auth_routes.py`: `/auth/login`, `/auth/callback`, `/auth/logout`, and `/api/me`.
  - `dashboard_routes.py`: `/api/projects`, `/api/projects/{project_id}/workspace`, and `/api/projects/{project_id}/tech-tree`.
  - `static/`: `index.html`, `styles.css`, and `app.js`.
- **Rationale**: Keeps every Python module well below the 600-line ceiling and allows unit/integration testing of API routes by injecting fake services onto `api_app.state`.

### 4. Canonical Discord Deep-Link Bridge (`discord_jump_url`)
- **Decision**: Define `discord_jump_url` properties on `Task` (`https://discord.com/channels/{guild_id}/{discord_thread_id}/{discord_message_id}`, falling back to `discord_thread_id`) and `Project` (`https://discord.com/channels/{guild_id}/{discord_channel_id}`) in `src/domain/models.py`, include `discord_jump_url` in all serialized project/task/tech-tree JSON payloads, and render "Open in Discord" deep-link buttons on Project headers, Kanban cards, Matrix rows, and the Tech Tree Node Inspector.
- **Rationale**: Preserves Discord as the single canonical execution surface (Thread Workspaces, Task Action Cards, and Forum Control Hub) while letting the web portal serve as a high-level visual radar and one-click launcher into Discord. Also wire `task_service.workspace = bot.workspace` in `src/bootstrap/container.py` and include `version` in task payloads so future write extensions remain plug-and-play.

## Risks / Trade-offs

- **[Risk] Bot cache not yet populated or running in API-only mode without `DISCORD_BOT_TOKEN`** -> **Mitigation**: `DiscordMemberDirectory.get_member_principal` already falls back to `guild.fetch_member(user_id)`. In local dev mode (`ENVIRONMENT=development`) when the bot gateway is offline, allow session principal fallback so local UI testing remains unblocked.
- **[Risk] Multi-guild ambiguity if `DISCORD_GUILD_ID` is not set in `.env`** -> **Mitigation**: Resolve target `guild_id` from `settings.DISCORD_GUILD_ID` first; if unset, fall back to the first guild in `bot.guilds` or distinct `guild_id` from `ProjectService`, and accept an optional `?guild_id=` query parameter.
- **[Risk] Large project task counts slowing down `/api/projects` summary metrics** -> **Mitigation**: Use existing repository query methods per visible project and keep payload serialization lightweight.
