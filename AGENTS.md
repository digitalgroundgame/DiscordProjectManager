# Project Instructions & Workspace Rules

## Development Environment

This project uses modern Python tooling with [`uv`](https://docs.astral.sh/uv/) and Docker Compose, with first-class NixOS support via [`devenv.nix`](devenv.nix) and `direnv`.

### Rules for Tool & Command Execution

1. **Tool Invocation**:
   - Prefer using `make <target>` or `uv run <cmd>` for executing tests, linting, formatting, and database operations.
   - Do NOT use `pip` directly. Dependencies are managed via `pyproject.toml` and `uv.lock`.

2. **Common Commands (`Makefile` shortcuts)**:
   - **Run Test Suite**: `make test` (or `uv run pytest -v tests/`)
   - **Run Application**: `make run` (or `uv run python -m src.main`)
   - **Linting**: `make lint` (or `uv run ruff check .`)
   - **Lint & Fix**: `make lint-fix` (or `uv run ruff check --fix .`)
   - **Formatting**: `make format` (or `uv run ruff format .`)
   - **Full Check**: `make check` (runs lint, format-check, and tests)
   - **Sync Dependencies**: `make sync` (or `uv run uv sync --all-extras`)
   - **Sync Slash Commands**: `make sync-commands` (or `uv run python -m src.cli sync-commands`)

   - **Database Up**: `make db-up` (`docker compose up -d postgres`)
   - **Database Down**: `make db-down` (`docker compose stop postgres`)
   - **Database Initialization**: `make db-init`
   - **Database Migrations**: `make db-migrate` (or `uv run alembic upgrade head`)
   - **Database Check**: `make db-check` (or `uv run alembic check`)
   - **Database Revision**: `make db-revision MSG="description"`
   - **Database Clear/Wipe**: `make db-clear`
   - **Declarative Seeding (Non-destructive)**: `make seed` (or `uv run python scripts/seed.py --no-reset`)
   - **Database Reset & Full Re-seed**: `make db-reset` (or `uv run python scripts/seed.py`)
   - **Database Shell**: `make db-shell`

3. **Background Services**:
   - PostgreSQL 18 is managed via Docker Compose (`make db-up` / `docker compose up -d postgres`).
   - Unit and integration tests run against an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`) and do not require PostgreSQL to be running.

4. **NixOS Support & Tool Availability**:
   - NixOS developers can use `direnv` (`use devenv` in `.envrc`) or `devenv shell` to automatically populate Python 3.13, `uv`, `psql`, `docker`, and required system libraries in their environment.
   - If any needed CLI utility or dependency is missing from the environment, use `nix-shell -p <package>` (or enter a `nix-shell`) to run or provide it on demand.

5. **Deployment & App Container Rebuild**:
   - When finished making code changes/updates, rebuild and restart the application container by running `make docker-build` (or `docker compose up -d --build app`).

## Hexagonal Architecture & Boundary Rules

See [ADR 0007: 4-Layer Hexagonal Architecture](docs/adr/0007-hexagonal-architecture.md) for full rationale and design.

1. **Layer Dependency Rule**:
   ```text
   Bootstrap -> Adapters (Driving / Driven) -> Application (Ports / Services) -> Domain (Pure Entities & Policies)
   ```
   - **Domain (`src/domain/`)**: Pure enterprise entities, value objects, exceptions, enums. Zero external framework dependencies (`discord`, `sqlalchemy`, `fastapi`, `PIL`).
   - **Ports & Application (`src/ports/`, `src/services/`)**: Ports define interfaces; services orchestrate business use cases. Atomic unit of work (`async with uow:`) for all mutations. Never import concrete adapters or vendor SDKs.
   - **Adapters (`src/adapters/`)**: Driving (`discord_bot`, `api`, `cli`) and Driven (`db`, `rendering`, `worker`). UI views communicate through services/ports, never directly querying ORM sessions or repositories.
   - **Bootstrap (`src/bootstrap/`)**: Composition root. Instantiates configuration, engines, session factories, repositories, and wires services. Eliminates import-time singletons.

2. **Automated Architectural Fitness Tests**:
   - Run `uv run pytest tests/test_architecture.py` to assert layer isolation.
   - No `unittest.mock` imports allowed in production code under `src/`. Use in-memory doubles from `tests/fakes/` instead.

3. **Strangler Fig Migration & Re-export Policy**:
   - When moving classes or modules during phased refactorings, maintain backwards-compatible re-exports in legacy module locations until Phase 5 to prevent breaking existing views/tests mid-migration.

## LLM Agent Workflow Protocol

When working on GitHub issues or project migration tasks:

1. **Branch Naming**:
   - Create a feature branch named `arch/<issue-number>-<short-slug>` (e.g. `arch/53-actor-principal`).

2. **Implementation & Verification**:
   - **Baseline**: Verify tests pass with `uv run pytest tests/` (or `direnv exec . uv run pytest`).
   - **TDD First**: Add unit tests (or architectural boundary tests) verifying the requirement.
   - **Implement**: Make focused, modular edits. Avoid editing God modules all at once; decompose into subpackages if modules exceed ~600 lines.
   - **Full Check**: Run `make check` (or `uv run ruff check . && uv run ruff format --check . && uv run pytest -v tests/`).

3. **Commit & Pull Request Conventions**:
   - Conventional commit message: `refactor(<layer>): <description> (fixes #<issue-number>)`
   - Link the corresponding issue number in the PR description and check off the completed acceptance criteria.

