# DGG-PM Unified System & Architecture Specification

This document is the canonical as-built specification for **DiscordProjectManager (`dgg-pm`)**. Use it as the single source of truth for **Spec-Driven Development (SDD)**, feature design, and two-axis (`Standards` + `Spec`) code reviews.

- **Domain Glossary (Ubiquitous Language)**: [`CONTEXT.md`](../CONTEXT.md)
- **Architectural Decision Records**: [`docs/adr/`](adr/) (`0001`–`0007`)
- **Automated Ratchets**: [`tests/test_architecture.py`](../tests/test_architecture.py) & [`tests/unit/test_architecture_boundaries.py`](../tests/unit/test_architecture_boundaries.py)

---

## 1. 4-Layer Hexagonal Architecture & Dependency Invariants

All code under `src/` obeys the strict inward dependency direction ([ADR 0007](adr/0007-hexagonal-architecture.md)):

```text
Bootstrap -> Adapters (Driving / Driven) -> Application (Ports / Services) -> Domain (Pure Entities & Policies)
```

| Layer | Package | Allowed `src` Imports | Forbidden Imports & Invariants |
| :--- | :--- | :--- | :--- |
| **1. Domain** | `src/domain/` | `src.domain.*` only | Zero framework or outward imports (`discord`, `sqlalchemy`, `fastapi`, `PIL`, `src.ports`, `src.services`, `src.adapters`, `src.bootstrap`, `src.config`). |
| **2. Ports** | `src/ports/` | `src.domain.*`, `src.ports.*` | Zero vendor SDK or concrete adapter imports (`discord`, `sqlalchemy`, `fastapi`, `PIL`, `src.adapters`, `src.services`). |
| **3. Services** | `src/services/` | `src.domain.*`, `src.ports.*`, `src.services.*` | Zero adapter, ORM, config, or vendor SDK imports (`discord`, `sqlalchemy`, `fastapi`, `PIL`, `src.adapters`, `src.bootstrap`, `src.config`). All modules ≤ 600 lines. |
| **4. Adapters** | `src/adapters/` | `src.domain.*`, `src.ports.*`, `src.services.*`, sibling adapter packages | Driving adapters (`discord_bot`, `api`) never import `src.adapters.db` or `sqlalchemy`. All `src/adapters/discord_bot/` modules ≤ 600 lines. |
| **5. Bootstrap** | `src/bootstrap/` | All layers (`src.*`) | Composition root only (`container.py`). Zero import-time database engines or mutable global singletons across `src/`. |

---

## 2. Domain Layer Specification (`src/domain/`)

### 2.1 Core Entities & Value Objects (`models.py`, `actor.py`, `enums.py`)
- **`Task`**: Anchored to a mandatory `Project` container ([ADR 0002](adr/0002-mandatory-project-containers-for-tasks.md)) with sequential `task_number`, `short_id` (`<PREFIX>-<N>`), optimistic concurrency `version`, `status` (`notStarted` | `inProgress` | `completed`), `priority` (`low` | `normal` | `high` | `critical`), optional `due_at`, `assignee_discord_id`, `watchers`, and Discord thread/message snowflakes.
- **Orthogonal `Overdue State` ([ADR 0004](adr/0004-overdue-as-orthogonal-schedule-state.md))**: Computed property (`task.is_overdue`) true when `due_at < now`, `status != completed`, and `archived_at is None`. Never mutates `TaskStatus`.
- **`Project`**: Aggregates tasks under an uppercase server-unique `prefix`, optional bound `discord_channel_id`, `lead_discord_id`, and mapped Squad roles (`discord_role_ids`). Every server may provision a catch-all **Default Project** (`General Tasks`, prefix `TASK`).
- **`Squad` & `SquadMember`**: Maps 1:1 to a Discord Server Role (`discord_role_id`). **Squad Lead** authority requires both database designation (`role_type="lead"`) and actively holding the Squad's Discord role.
- **`ActorPrincipal` (`actor.py`)**: Immutable, platform-agnostic authorization principal (`user_id`, `guild_id`, `role_ids: frozenset[int]`, `is_guild_admin: bool`).
- **`OutboxEvent`**: Transactional event record (`event_type`, `idempotency_key`, `payload`, `status`, `retry_count`, `next_retry_at`).

### 2.2 Domain Policies (`tech_tree.py`, `standard_mappings.py`)
- **`TechTree` (`tech_tree.py`)**: Pure Directed Acyclic Graph (DAG) model validating prerequisite edges (`validate_edge` raises `SelfDependencyError` or `CircularDependencyError`), computing critical path / depth layers, and delegating visual output to injected `TechTreeRendererPort` implementations (`to_png(renderer=...)`, `to_mermaid(renderer=...)`). Contains zero global renderer state or Discord duck-typing.
- **Standard Mappings (`standard_mappings.py`)**: Serializes tasks to RFC 5545 `VEVENT`/`VTODO` (`to_rfc5545_vcalendar`) and Microsoft Graph `todoTask` payloads.

---

## 3. Application & Ports Layer Specification (`src/ports/`, `src/services/`)

### 3.1 Inbound & Outbound Ports (`src/ports/`)
- **`IUnitOfWork` (`unit_of_work.py`)**: Exposes aggregate repositories (`tasks`, `projects`, `squads`, `outbox`, `user_prefs`, `guild_lead_roles`, `guild_lead_users`, `calendar_tokens`) and atomic `commit()` / `rollback()` lifecycle.
- **Repository Ports (`repositories.py`)**: `ITaskRepo`, `IProjectRepo`, `ISquadRepo`, `IOutboxRepo`, `IUserPreferenceRepo`, `IGuildLeadRoleRepository`, `IGuildLeadUserRepository`, `IUserCalendarTokenRepo`.
- **`TaskWorkspacePort` & `ProjectWorkspacePort` (`workspace.py`)**: Platform-agnostic contracts for provisioning, synchronizing, and rebuilding Thread Workspaces, Task Action Cards, Forum Channels, and Control Hubs.
- **`MemberDirectoryPort` (`member_directory.py`)**: Resolves guild member `ActorPrincipal` and role names without leaking Discord REST/cache objects into services.
- **`TechTreeRendererPort` (`renderer.py`)**: Contract for rendering `TechTree` graphs to PNG bytes or Mermaid strings.
- **`INotificationDispatcher` (`notifier.py`)**: Dispatches transactional outbox events to external channels/DMs.
- **`SystemMetricsQueryPort` (`metrics.py`)**: Read-only system health and outbox queue metrics port.

### 3.2 Application Services (`src/services/`)
All transactional services inherit from **`TransactionalService` (`base.py`)** and execute mutations inside `async with self._transaction() as uow:` ([ADR 0001](adr/0001-unit-of-work-for-transactions.md)):
- **`TaskService` (`task_service.py` & `task_graph_ops.py`)**:
  - `create_task`: Atomically resolves project, validates prerequisites, allocates `short_id` via `ProjectService.allocate_next_short_id`, inserts `Task` + `TaskHistory`, schedules tiered due-date reminders, enqueues `TASK_CREATED` outbox event, and links explicit/inline prerequisite dependencies in a single transaction (rolling back task creation if any prerequisite fails or introduces a cycle).
  - `update_status`: Compare-And-Swap (`update_status_cas`) transition with `StaleVersionError` on version mismatch, automatic downstream `TASK_UNBLOCKED` evaluation on completion, and reminder cancellation.
  - `update_assignee`, `update_priority`, `update_details`, `archive_task`, `unarchive_task`, `delete_task`.
  - `TaskGraphServiceMixin`: `add_dependency`, `remove_dependency`, `get_task_dependencies`, `get_unresolved_prerequisites`, `get_project_tech_tree`, `render_project_tree`, `provision_task_workspace`, `create_task_with_workspace`, `sync_task_workspace`.
- **`ProjectService` (`project_service.py`)**: Project creation, role/squad linking (coordinating `uow.squads` and `uow.projects`), atomic task counter increment, archival/restoration, and workspace rebuild orchestration ([ADR 0006](adr/0006-reconciliation-based-workspace-rebuild.md)).
- **`SquadService` (`squad_service.py`)**: Squad CRUD, Squad Lead designation, roster management, and self-healing role pruning.
- **`AuthService` (`auth_service.py`)**: Evaluates authorization rules against `ActorPrincipal` and `MemberDirectoryPort` without live Discord SDK calls or raw Discord mention formatting.
- **`OutboxService` (`outbox_service.py`)**: Enqueues idempotent outbox events and schedules/cancels 24h, 1h, and due-time task reminders.
- **`CalendarService` (`calendar_service.py`)**: Manages user iCalendar feed tokens and builds `https://` and `webcal://` subscription URLs from injected `base_url`.
- **`UserService` (`user_service.py`)**: Manages user `NotificationPreference` (`DM`, `CHANNEL`, `BOTH`, `SILENT`).

---

## 4. Adapters & Bootstrap Specification (`src/adapters/`, `src/bootstrap/`)

### 4.1 Driven Persistence Adapter (`src/adapters/db/`)
- **`SqlAlchemyUnitOfWork` (`unit_of_work.py`)**: Stores active `_UnitOfWorkTransactionContext` in a task-local `contextvars.ContextVar` and exposes repositories via `_ContextRepoDescriptor`:
  - Concurrent `asyncio` tasks sharing a `SqlAlchemyUnitOfWork` instance receive completely isolated `AsyncSession` contexts.
  - Re-entrant `async with uow:` blocks in the same coroutine share the outer session (`depth > 1`) and only commit or roll back when the outermost block exits (`depth == 1`).
- **Aggregate-Scoped Repositories (`repos/`)**: Each repository queries only its own aggregate tables (`PostgresProjectRepo` never imports or queries `SquadTable` or `TaskTable`).

### 4.2 Driving Discord Bot Adapter (`src/adapters/discord_bot/`)
- **Cogs (`cogs/`)**: Modular command mixins (`ProjectCommandsMixin`, `SquadCommandsMixin`, `TaskCommandsMixin`, `TaskActionsMixin`) composed into `PmCog`.
- **`DiscordViewRouter` (`router.py`)**: Breaks circular view imports by routing intra-adapter transitions across `views/project/`, `views/squad_*`, `views/task_*`, and `views/hub_*`.
- **Workspace Synchronization (`task_workspace.py`, `task_workspace_sync.py`, `discord_notifier.py`)**: `DiscordTaskWorkspaceAdapter` is the single authority for Thread Workspace starter card, forum tag, title cooldown, and archive synchronization.

### 4.3 Composition Root (`src/bootstrap/container.py` & `src/main.py`)
- `create_container()` instantiates `Settings`, SQLAlchemy engine/sessionmaker, `SqlAlchemyUnitOfWork`, repositories, services, `DggPmBot`, `DiscordNotifier`, `OutboxWorker`, and FastAPI `uvicorn.Server` in one place.
- `run_app()` in `src/main.py` delegates lifecycle startup/shutdown directly to `container.init_db()`, `container.bot`, `container.worker`, and `container.uvicorn_server`.
