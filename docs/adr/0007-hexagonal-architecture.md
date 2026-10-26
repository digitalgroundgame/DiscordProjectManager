# 4-Layer Hexagonal Architecture (Ports and Adapters)

## Context and Decision
As `DiscordProjectManager` (`dgg-pm`) expanded to support complex workspace synchronization, tech trees, and role-based permissions, framework dependencies leaked across boundaries:
- `discord.py` types and mention formatting leaked into domain models, history records, and `AuthService`.
- Database transaction boundaries were fragmented across non-atomic repository reads and writes (causing TOCTOU race hazards).
- Circular import cycles (17+ lazy in-function imports) accumulated across Discord UI views.
- Module-import-time singletons and database engine creation hindered isolated testing and clean DI wiring.
- God modules (`project_menu.py` with ~3,000 lines and `pm_cog.py` with ~2,000 lines) made code changes brittle and difficult for AI agents and human contributors to reason about.

We decided to refactor `DiscordProjectManager` into a strict **4-Layer Hexagonal Architecture** governed by the unidirectional dependency rule:
```text
Bootstrap -> Adapters (Driving / Driven) -> Application (Ports / Services) -> Domain (Pure Entities & Policies)
```

### Layer Definitions & Responsibilities

1. **Domain Layer (`src/domain/`)**:
   - Contains pure enterprise business models (`Task`, `Project`, `Squad`), value objects (`ActorPrincipal`), enums, domain exceptions, and business policies (e.g. `TechTree` DAG validation).
   - **Strict Invariant**: Zero outward dependencies. Must NEVER import `discord`, `sqlalchemy`, `fastapi`, `PIL`, `src.ports`, `src.services`, or `src.adapters`.

2. **Application / Ports Layer (`src/ports/` & `src/services/`)**:
   - `src/ports/`: Abstract protocols for secondary/driven adapters (`IUnitOfWork`, repository interfaces, `TaskWorkspacePort`, `ProjectWorkspacePort`, `TechTreeRenderer`, `NotifierPort`) and primary/driving navigation (`DiscordViewRouter`).
   - `src/services/`: Application use cases coordinating business workflows. Every mutating service operation executes within an atomic transaction boundary using `async with uow:` (no reads outside the UoW session).
   - **Strict Invariant**: Depends ONLY on `src/domain/` and sibling `src/ports/`. Must NEVER import from `src.adapters`, concrete ORM models, or vendor SDKs (`discord`, `sqlalchemy`, `fastapi`).

3. **Adapters Layer (`src/adapters/`)**:
   - **Driving Adapters**: Receive external triggers and adapt them into domain requests. Includes Discord UI views, modals, and cogs (`src/adapters/discord_bot/`), FastAPI endpoints (`src/adapters/api/`), and CLI commands (`src/cli.py`).
   - **Driven Adapters**: Implement outbound ports defined by the application layer. Includes SQLAlchemy persistence (`src/adapters/db/`), Pillow/Mermaid rendering (`src/adapters/rendering/`), and transactional outbox dispatchers (`src/adapters/worker/`).
   - **Strict Invariant**: UI views and endpoints communicate with the system exclusively through Application services and Ports, never performing direct cross-adapter calls into database sessions.

4. **Bootstrap Layer (`src/bootstrap/`)**:
   - The Composition Root (`src/bootstrap/container.py`).
   - Responsible for loading `Settings`, constructing the SQLAlchemy `AsyncEngine` / `async_sessionmaker`, initializing `SqlAlchemyUnitOfWork`, instantiating driven adapters, wiring application services, and configuring the Discord bot client.
   - **Strict Invariant**: No module under `src/` creates live database engines or global mutable singletons at import time.

### Migration & Transitive Compatibility (Strangler Fig Pattern)
To ensure the system remains deployable and all 400+ unit tests remain green during the 5-phase migration:
- **Transitional Re-exports**: When relocating classes (e.g., splitting `postgres_repo.py` or moving `tree_render.py` to `src/adapters/rendering/`), keep deprecated aliases at the original module path until Phase 5 cleanup.
- **Architectural Boundary Enforcement**: Incremental AST boundary tests in `tests/test_architecture.py` run on every commit and CI build to prevent backsliding.
- **In-Memory Fakes**: Core ports are accompanied by reusable in-memory doubles in `tests/fakes/` to replace brittle `unittest.mock` usage.

## Considered Options
- **Status Quo (Monolithic Services with Leaky Abstractions - Rejected)**: Led to 17+ circular import cycles, tight coupling to Discord SDKs, and difficulty maintaining or scaling bot capabilities.
- **Traditional 3-Tier Layered Architecture (Rejected)**: Places the database at the center, coupling domain logic to SQLAlchemy ORM tables and complicating multi-adapter support (CLI, API, Bot).
- **Hexagonal Architecture with Explicit Composition Root (Chosen)**: Decouples domain logic from Discord and database infrastructure, enables isolated unit testing with in-memory test doubles, prevents circular dependencies, and establishes explicit modular seams.

## Consequences
- Domain models and services are 100% testable in pure Python without running Discord or database services.
- Atomic Unit of Work guarantees consistency across multi-entity operations (e.g. task mutations with audit logs and outbox events).
- Views and cogs are decoupled through the `DiscordViewRouter` protocol, eliminating lazy import cycles.
- Import-time side effects are eliminated; configuration and dependencies are passed explicitly via the composition root.
