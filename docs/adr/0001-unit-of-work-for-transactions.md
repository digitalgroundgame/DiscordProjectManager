# Unit of Work for Transaction Boundaries

## Context and Decision
The application core services need to execute atomic multi-repository mutations (e.g. persisting a task, appending audit history, and enqueuing an outbox event in the same database transaction) without coupling domain ports to persistence implementation details. We decided to use an explicit `IUnitOfWork` interface that owns transaction-scoped repositories (`uow.tasks`, `uow.projects`, `uow.outbox`), rather than passing SQLAlchemy `session` objects into repository methods.

## Considered Options
- **Passing `session: Any` to repository methods (Rejected)**: Leaked ORM plumbing across the hexagonal port seam into domain services, caused split-brain session bugs in tests, and led to repositories accidentally committing early.
- **Ambient / Thread-local session context (Rejected)**: Implicit, prone to hidden coupling, and difficult to manage cleanly in async asyncio concurrency.
- **Unit of Work owning scoped repositories (Chosen)**: Provides an explicit transaction boundary (`async with uow:`), automatic rollback on unhandled exceptions, and clean repository signatures devoid of session parameters.

## Consequences
- Application services (such as `TaskService`) depend on `IUnitOfWork` for transactions and do not pass `session` arguments.
- Repositories within a Unit of Work flush changes rather than committing prematurely, preserving atomicity.
