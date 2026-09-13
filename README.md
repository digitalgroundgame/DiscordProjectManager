# dgg-pm: Discord-Native Task Management Platform

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Hexagonal](https://img.shields.io/badge/architecture-hexagonal-green.svg)](#architecture)
[![Schema: RFC 5545 & MS Graph](https://img.shields.io/badge/schema-RFC%205545%20%2F%20MS%20Graph-orange.svg)](#data-standardization)

A self-hosted, zero-signup Discord-native project management platform built to eliminate context-switching by embedding task workflows directly into Discord text channels, threads, and direct messages.

---

## 📚 Documentation & Wiki

Detailed guides, command references, and architecture documents are available in the **[DGG-PM Wiki](docs/wiki/Home.md)**:
- 🚀 **[Workflow & Quickstart Guide](docs/wiki/Workflow-Guide.md)**: End-to-end setup and zero-command daily workflows.
- ⌨️ **[Slash Commands Reference (`/pm`)](docs/wiki/Slash-Commands-Reference.md)**: Complete parameter and permission breakdown.
- 👥 **[Teams & Authorization Matrix](docs/wiki/Teams-and-Authorization.md)**: Discord-native role rosters and self-healing leads.
- 📌 **[Forum Channels & Interactive Hubs](docs/wiki/Forum-Channels-and-Hubs.md)**: Tag auto-provisioning and pinned control centers.
- 🗄️ **[Database Migrations Manual (Alembic)](docs/migrations.md)**: Complete development and production migration runbook.

---

## Key Features

- **Unified `/pm` Slash Command Group**: Single isolated namespace eliminating server command clutter and bot collisions.
- **Pinned Forum Control Hubs**: Permanent interactive dashboards (`📌 📊 Control Hub`) pinned in project forums for zero-command task creation and management.
- **Discord-Native Squad Rosters**: Functional teams are mapped directly to live Discord server roles with automatic self-healing for orphaned leads.
- **Dedicated Task Channels & Threads**: Tasks automatically spawn dedicated discussion threads with real-time interactive Action Cards (`[ ⏳ To Do ]`, `[ 🟡 In Progress ]`, `[ 🟢 Complete ]`, `[ ⚡ Priority ]`, `[ 👤 Reassign ]`).
- **Human-Friendly Short IDs & Autocomplete**: Sequential project-prefixed IDs (e.g. `INF-1`, `PRJ-42`) with atomic SQL counter generation and channel-scoped autocomplete.
- **Audit History & Optimistic Concurrency Control (CAS)**: Complete lifecycle history in `task_history` table and version-checked updates preventing lost update races.
- **Postgres-Native Transactional Outbox**: At-least-once reminder and notification dispatch with `FOR UPDATE SKIP LOCKED`, unique `idempotency_key` deduplication, and dynamic Discord 429 `Retry-After` backoff.
- **RFC 5545 & Microsoft Graph Schema Portability**: Native mapping to `VTODO` and `todoTask` data standards from Day 1 for future calendar integration.

---

## System Architecture

```
+-----------------------------------------------------------------------------------+
|                                DRIVING ADAPTERS                                   |
|                                                                                   |
|   +------------------------------------+   +----------------------------------+   |
|   |       Discord Bot Adapter          |   |        FastAPI Service Engine    |   |
|   |  (discord.py: Slash / UI / Modals) |   |    (/healthz, /metrics, schemas) |   |
|   +-----------------+------------------+   +----------------+-----------------+   |
+---------------------|---------------------------------------|---------------------+
                      |                                       |
                      v                                       v
+-----------------------------------------------------------------------------------+
|                            APPLICATION / USE CASE LAYER                           |
|                                                                                   |
|   - TaskService: CreateTask, UpdateStatus (CAS), AddNote, FilterTasks, Autocomplete|
|   - ProjectService: CreateProject, BindChannel, GenerateShortId, Archive/Restore   |
|   - TeamService: CreateTeam, SyncDiscordRoles                                     |
|   - OutboxService: EnqueueEvent, ScheduleTieredReminders, CancelTaskReminders     |
+-------------------------------------+---------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|                              DOMAIN MODEL (CORE)                                  |
|                                                                                   |
|   - Entities: Task, TaskHistory, Project, Team, OutboxEvent                       |
|   - Value Objects: TaskStatus, PriorityLevel, ShortTaskId, IsoTimestamp            |
|   - State Machine: notStarted -> inProgress -> completed (with CAS validation)    |
|   - Schema Standard Mappings: RFC 5545 (VTODO) / MS Graph (todoTask)              |
+-------------------------------------+---------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|                                DRIVEN ADAPTERS                                    |
|                                                                                   |
|   +------------------------------------+   +----------------------------------+   |
|   |    PostgreSQL Relational Repo      |   |  Transactional Outbox Dispatcher |   |
|   |   (SQLAlchemy Async + Alembic)     |   |   (Async Worker SKIP LOCKED)     |   |
|   +------------------------------------+   +----------------------------------+   |
+-----------------------------------------------------------------------------------+
```

---

## Discord Slash Commands (`/pm`)

All bot operations are isolated under the unified `/pm` slash command namespace:

| Slash Command | Required Permission | Description |
| :--- | :--- | :--- |
| **`/pm menu`** | Standard Member | Open the master interactive Control Hub |
| **`/pm help`** | Standard Member | Display operational command guide and wiki links |
| **`/pm settings`** | Standard Member | Configure personal notification delivery (`dm`, `channel`, `both`, `silent`) |
| **`/pm setup-hub`** | `Manage Server` | Post and pin an interactive Control Center in a Forum or Text Channel |
| **`/pm tree`** | Standard Member | Render the visual tech tree DAG dependency graph |
| **`/pm task create`** | Squad Member / Manager | Create a project task, provision thread workspace & action card |
| **`/pm task status`** | Assignee / Lead / Manager | Update execution status (CAS optimistic concurrency control) |
| **`/pm task assign`** | Squad Member / Manager | Assign or unassign a member from a task |
| **`/pm task depend`** | Squad Member / Manager | Link prerequisite dependency (`task` requires `depends_on`) |
| **`/pm task list`** | Standard Member | Filter and browse active tasks with interactive pagination |
| **`/pm task history`** | Standard Member | View full chronological audit trail of a task |
| **`/pm project create`**| `Manage Server` | Instantiate project container, map Squad role, provision forum |
| **`/pm project list`**  | Standard Member | List all active project containers and bound channels |
| **`/pm squad create`**  | `Manage Server` | Define a functional squad mapped 1:1 with a Discord role |
| **`/pm squad lead`**    | `Manage Server` / Lead | Designate or remove a Squad Lead |
| **`/pm squad list`**    | Standard Member | Display all configured squads, leads, and member rosters |

*For complete parameters, options, and permission breakdowns, see the [Slash Commands Reference](docs/wiki/Slash-Commands-Reference.md).*

---

## Quickstart & Setup

### 1. Prerequisites
- Python 3.13+
- PostgreSQL 16+ (or Docker)
- Discord Bot Application Token ([Discord Developer Portal](https://discord.com/developers/applications))

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in your Discord Bot credentials:
```bash
cp .env.example .env
```
Edit `.env`:
```env
DISCORD_BOT_TOKEN=your_token_here
DISCORD_CLIENT_ID=your_client_id_here
DISCORD_GUILD_ID=your_test_guild_id   # Optional: faster command syncing in dev
DATABASE_URL=postgresql+asyncpg://postgres:postgrespassword@localhost:5432/dgg_pm
```

### 3. Discord Developer Portal Configuration
When configuring your application in the [Discord Developer Portal](https://discord.com/developers/applications) and inviting the bot:
- **Privileged Gateway Intents**: Ensure **Server Members Intent** (`GuildMembers`) is enabled under the **Bot** tab. *(Note: `Message Content` is explicitly **NOT** required).*
- **Bot Permissions**: Verify the bot invite URL contains the following permissions:
  - `Manage Channels` (for auto-tagging Forum channels)
  - `Manage Threads`
  - `View Channels`
  - `Send Messages`
  - `Send Messages in Threads`
  - `Create Public Threads`
  - `Manage Messages`
  - `Embed Links`
  - `Read Message History`

### 4. Local Development (Standard Python / uv)

DGG-PM uses [`uv`](https://docs.astral.sh/uv/) for ultra-fast, cross-platform Python package management and `docker compose` for services.

```bash
# 1. Install dependencies & initialize virtual environment
make install     # or: uv sync --all-extras

# 2. Start PostgreSQL container in the background
make db-up       # or: docker compose up -d postgres

# 3. Run the test suite (uses in-memory SQLite; does not even require Postgres)
make test        # or: uv run pytest -v tests/

# 4. Start the application
make run         # or: uv run python -m src.main
```

#### Handy `Makefile` Shortcuts:
```bash
make help        # List all available targets and descriptions
make test        # Run pytest test suite
make test-cov    # Run tests with coverage report
make lint        # Check code with ruff
make format      # Autoformat with ruff
make check       # Run lint, format check, and tests
make db-up       # Start Postgres container
make db-down     # Stop Postgres container
make db-migrate  # Apply pending Alembic migrations
make db-check    # Check migration status
make seed        # Declarative sync (non-destructive; preserves Discord channels & threads)
make db-reset    # Hard wipe tables & Discord category and re-seed from scratch
make db-shell    # Open interactive psql shell
```

#### Declarative Seeding:
DGG-PM features a declarative seeding engine powered by YAML manifests (`seeds/base/manifest.yaml`):
```bash
# Non-destructive seed (preserves channels, reuses existing task threads, syncs DB & tags)
make seed

# Destructive reset (drops tables, deletes PM Discord category, and re-provisions cleanly)
make db-reset

# Fast database-only seed (no Discord API calls required, ideal for offline/CI test DBs)
uv run python scripts/seed.py --no-discord
```


### 5. NixOS / Nix Flakes Development

If you are developing on **NixOS** or using **Nix**:

```bash
# Enter the development shell (provides Python 3.13, uv, postgresql client, gnumake, docker, and C libraries)
nix develop

# Or with direnv (recommended):
direnv allow
```

Once inside the Nix shell, all standard `make` and `uv` commands work directly without additional configuration.

### 6. Running with Docker Compose (Full Stack)
To run both the application and PostgreSQL in containers:
```bash
docker compose up -d --build
```

### 7. Deploying to Coolify

DGG-PM is pre-configured for seamless hosting on [Coolify](https://coolify.io).

#### Recommended Branch Strategy
* **`main`**: Active development and integration branch.
* **`production`**: Dedicated deployment branch that Coolify tracks for automated builds.
* **Promoting releases to production**:
  ```bash
  # Fast-forward production to current main:
  git push origin main:production
  ```
  *(Alternatively, open and merge a Pull Request from `main` into `production` on GitHub.)*

#### Option A: Docker Compose Stack (Recommended)
1. In Coolify, create a new resource ➔ **Docker Compose**.
2. Point Coolify to this repository and set **Branch** to `production`.
3. Configure your environment variables in Coolify:
   - `DISCORD_BOT_TOKEN`: Your Discord bot token
   - `DISCORD_CLIENT_ID`: Your Discord bot application ID
   - `DISCORD_GUILD_ID`: *(Optional)* Guild ID for instant slash command registration
4. Click **Deploy**. Migrations run automatically on startup and the container healthcheck monitors `/healthz`.

#### Option B: Standalone Application + Coolify PostgreSQL
1. Create a PostgreSQL 18 database service in Coolify.
2. Create a new **Application** pointing to this repository (Build Pack: **Dockerfile**) and set **Branch** to `production`.
3. Configure environment variables in the Coolify Application settings:
   - `DATABASE_URL`: Your Coolify PostgreSQL connection string (standard `postgres://` and `postgresql://` are auto-normalized to asyncpg)
   - `AUTO_RUN_MIGRATIONS`: `true`
   - `DISCORD_BOT_TOKEN`: Your Discord bot token
   - `DISCORD_CLIENT_ID`: Your Discord bot application ID
   - `DISCORD_GUILD_ID`: *(Optional)*
4. In Coolify Application Settings, set:
   - **Port**: `8000`
   - **Health Check Path**: `/healthz`
