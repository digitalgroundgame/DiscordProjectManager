# 🏛️ System Architecture

**DGG-PM** is structured using **Hexagonal Architecture (Ports and Adapters)** to decouple domain logic from Discord APIs, database frameworks, and background dispatchers.

---

## Architecture Overview

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
|   - SquadService: MapDiscordRoles, ManageSquadLeads, SelfHealingPruning           |
|   - OutboxService: EnqueueEvent, ScheduleTieredReminders, CancelTaskReminders     |
+-------------------------------------+---------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|                              DOMAIN MODEL (CORE)                                  |
|                                                                                   |
|   - Entities: Task, TaskHistory, Project, Squad, SquadMember, OutboxEvent         |
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

## Architectural Layers

### 1. Driving Adapters (`src/adapters/`)
Primary entry points that receive user interactions and requests:
- **Discord Bot Adapter** (`src/adapters/discord_bot/`):
  - Application slash command tree (`/pm`).
  - Interactive Views (Action Cards, Pinned Control Hubs, Dropdown selectors).
  - Modal interaction listeners for zero-command inputs.
- **API & Health Engine** (`src/adapters/api/`):
  - Lightweight FastAPI ASGI endpoints exposing container readiness/liveness (`/healthz`).

### 2. Application Layer (`src/services/`)
Orchestrates business workflows and coordinates transactions:
- **`TaskService`**: Enforces task creation, assignee validation, priority management, and state machine transitions via Optimistic Concurrency Control (Compare-And-Swap).
- **`ProjectService`**: Manages project containers, forum channel bindings, sequential short ID counters, and workspace rebuilds.
- **`SquadService`**: Handles just-in-time Discord role mapping, lead designations, and self-healing orphan pruning.
- **`OutboxService`**: Atomic event staging and reminder scheduling.

### 3. Core Domain Model (`src/domain/`)
Pure Python domain models without dependencies on frameworks or databases:
- **Entities**: `Task`, `Project`, `Squad`, `SquadMember`, `ProjectSquad`, `OutboxEvent`.
- **Enums**: `TaskStatus`, `PriorityLevel`, `NotificationPreference`, `EventType`.
- **Exceptions**: Domain-specific error hierarchies (`EntityNotFoundError`, `AuthorizationError`, `OptimisticLockError`).

### 4. Driven Adapters (`src/adapters/db/`, `src/adapters/worker/`)
Secondary adapters that interact with infrastructure:
- **Postgres Repositories** (`src/adapters/db/postgres_repo.py`):
  - Asynchronous SQLAlchemy 2.0 repositories utilizing `asyncpg`.
  - Atomically managed via the Unit of Work (`src/adapters/db/unit_of_work.py`).
- **Transactional Outbox Worker** (`src/adapters/worker/outbox_worker.py`):
  - Highly resilient background event processor.
  - Queries pending notifications using `FOR UPDATE SKIP LOCKED`.
  - Enforces Discord rate limits with dynamic `Retry-After` backoff and exponential backoff retry policies.

---

## Data Standardization (RFC 5545 & Microsoft Graph)

To facilitate external calendar and productivity tool integrations without data transformation friction, DGG-PM models tasks around established industry standards:
- **RFC 5545 (`VTODO`)**: Task titles, due dates, priority tiers, and completion stamps conform to standard iCalendar task objects.
- **Microsoft Graph (`todoTask`)**: Status state transitions and task importance levels map 1:1 with Microsoft Graph To-Do schemas.

---

## Concurrency & Integrity Guarantees

1. **Optimistic Concurrency Control (CAS)**:
   - Every task carries an updated timestamp / version token.
   - Status updates require the expected current status, eliminating lost-update race conditions in high-concurrency Discord thread discussions.
2. **Transactional Outbox**:
   - Database state changes and notification events are saved inside the same database transaction.
   - Eliminates dual-write vulnerabilities: if a notification fails to send, database state is never rolled back, and the outbox worker automatically retries dispatch.
