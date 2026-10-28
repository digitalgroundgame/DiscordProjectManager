# 🏛️ System Architecture

**DGG-PM** uses a strict **4-Layer Hexagonal Architecture (Ports and Adapters)** to decouple pure domain logic from Discord APIs, SQLAlchemy persistence, rendering engines, and background dispatchers.

> **Canonical Specification**: See [`docs/SPEC.md`](../SPEC.md) for the complete as-built system specification, port contracts, and automated CI boundary ratchets, and [`docs/adr/0007-hexagonal-architecture.md`](../adr/0007-hexagonal-architecture.md) for the architectural decision record.

---

## Architecture Overview

```text
Bootstrap (src/bootstrap/container.py)
  │
  ├──► Driving Adapters (src/adapters/discord_bot/, src/adapters/api/, src/cli.py)
  │      ├── PmCog (ProjectCommandsMixin, SquadCommandsMixin, TaskCommandsMixin, TaskActionsMixin)
  │      ├── DiscordViewRouter & Modular Views (project/, squad_*, task_*, hub_*)
  │      └── FastAPI Engine (/health, /metrics, /calendar/{token}.ics)
  │
  ├──► Application & Ports Layer (src/ports/, src/services/)
  │      ├── Ports: IUnitOfWork, I*Repo, TaskWorkspacePort, ProjectWorkspacePort,
  │      │          MemberDirectoryPort, TechTreeRendererPort, INotificationDispatcher
  │      └── Services (TransactionalService): TaskService, ProjectService, SquadService,
  │                    AuthService, OutboxService, CalendarService, UserService
  │
  ├──► Domain Layer (src/domain/)
  │      ├── Entities & Value Objects: Task, Project, Squad, ActorPrincipal, OutboxEvent
  │      └── Pure Policies: TechTree DAG, Orthogonal Overdue State, RFC 5545 / MS Graph
  │
  └──► Driven Adapters (src/adapters/db/, src/adapters/rendering/, src/adapters/worker/)
         ├── SqlAlchemyUnitOfWork (ContextVar task-local re-entrant transactions)
         ├── Aggregate Repositories (repos/task_repo, project_repo, squad_repo, outbox_repo)
         ├── PillowTechTreeRenderer & MermaidTechTreeRenderer
         └── OutboxWorker (FOR UPDATE SKIP LOCKED + exponential backoff)
```

---

## Key Architectural Guarantees

1. **Pure Domain & Service Isolation**:
   - `src/domain/` and `src/services/` have zero dependencies on `discord.py`, `sqlalchemy`, `fastapi`, or `PIL`.
   - Authorization operates on `ActorPrincipal` value objects and `MemberDirectoryPort`.
2. **Task-Local Re-Entrant Unit of Work (`SqlAlchemyUnitOfWork`)**:
   - Uses `contextvars.ContextVar` so concurrent Discord interactions never share mutable session state, while nested service calls within the same coroutine automatically join the outer atomic transaction.
3. **Compare-And-Swap (CAS) & Transactional Outbox**:
   - Task status transitions validate `expected_version` atomically, enqueueing `OutboxEvent` records in the same database transaction for asynchronous delivery by `OutboxWorker`.
4. **Automated CI Ratchets (`tests/test_architecture.py`)**:
   - AST boundary tests enforce layer imports, forbid `unittest.mock` and import-time singletons in `src/`, prevent cross-aggregate table queries, and cap modules in `src/adapters/discord_bot/` and `src/services/` at ≤ 600 lines.
