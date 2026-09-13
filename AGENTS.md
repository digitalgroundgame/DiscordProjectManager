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
   - **Sync Dependencies**: `make sync` (or `uv sync --all-extras`)
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
