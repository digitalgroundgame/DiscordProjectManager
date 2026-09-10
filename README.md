# dgg-pm: Discord-Native Task Management Platform

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
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
- Python 3.12+
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
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dgg_pm
```

### 3. Required Discord Bot Permissions & Intents
When inviting the bot to your Discord server, ensure the following permissions are granted:
- **Bot Permissions:** `Manage Channels` (for auto-tagging Forum channels), `Manage Threads`, `View Channels`, `Send Messages`, `Send Messages in Threads`, `Create Public Threads`, `Manage Messages`, `Embed Links`, `Read Message History`.
- **Privileged Gateway Intents:** `Guilds`, `GuildMembers`. *(Note: `MessageContent` is explicitly **NOT** required).*

### 4. Running with Docker Compose
```bash
docker-compose up --build
```

### 5. Running with devenv (Nix)
With [`devenv`](https://devenv.sh/) installed:
```bash
# Enter the devenv developer shell (installs Python 3.12, dependencies via uv, PostgreSQL 16, tools)
devenv shell

# Start background services (PostgreSQL & app)
devenv up

# Run helper scripts inside the devenv shell
run-tests    # Execute pytest test suite
run-app      # Launch platform
db-init      # Initialize database schema
db-clear     # Wipe/truncate PostgreSQL database tables
db-reset     # Wipe database tables and re-seed test data
db-shell     # Connect to local PostgreSQL
format       # Autoformat with ruff
lint         # Lint check with ruff
```

### 6. Running Locally (Standard Python)
```bash
pip install -e ".[dev]"
pytest
python src/main.py
```
