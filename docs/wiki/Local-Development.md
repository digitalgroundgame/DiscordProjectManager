# 💻 Local Development & Seeding Guide

This guide covers local environment setup, NixOS/devenv support, common `make` commands, and the declarative development database seeder.

---

## 🛠️ Prerequisites

- **Python 3.13+**
- **PostgreSQL 16+** (or Docker for `docker compose up -d postgres`)
- **[uv](https://docs.astral.sh/uv/)** (recommended package manager)
- Discord Bot Application Token ([Discord Developer Portal](https://discord.com/developers/applications))

---

## ⚡ Standard Setup (`uv`)

```bash
# 1. Clone the repository and configure environment
cp .env.example .env
# Edit .env with your DISCORD_BOT_TOKEN and DISCORD_CLIENT_ID

# 2. Install dependencies
make install       # or: uv sync --all-extras

# 3. Start PostgreSQL container in background
make db-up         # or: docker compose up -d postgres

# 4. Run test suite (uses in-memory SQLite; does not require PostgreSQL)
make test          # or: uv run pytest -v tests/

# 5. Run the application
make run           # or: uv run python -m src.main
```

---

## ❄️ NixOS & Devenv Support

For developers on NixOS or using [`devenv`](https://devenv.sh/):

```bash
# Enter the devenv shell (auto-provisions Python 3.13, uv, postgresql, docker, and libraries)
devenv shell

# Or automatically load with direnv:
direnv allow
```

Inside the devenv shell, all `make` and `uv` commands work directly without requiring global dependencies.

---

## 📋 Common Makefile Commands

| Target | Command | Purpose |
| :--- | :--- | :--- |
| `make test` | `uv run pytest -v tests/` | Run full test suite |
| `make test-cov` | `uv run pytest --cov=src` | Run tests with coverage reporting |
| `make lint` | `uv run ruff check .` | Run Ruff linter |
| `make format` | `uv run ruff format .` | Auto-format Python code |
| `make check` | Lint + Format-Check + Test | Run all quality and regression checks |
| `make sync-commands` | `uv run python -m src.cli sync-commands` | Sync Discord slash commands on demand |
| `make db-up` | `docker compose up -d postgres` | Start local Postgres container |
| `make db-down` | `docker compose stop postgres` | Stop local Postgres container |
| `make db-migrate` | `uv run alembic upgrade head` | Apply pending database migrations |
| `make db-revision`| `uv run alembic revision ...` | Create new migration revision |
| `make db-shell` | Interactive psql shell | Connect to local Postgres database |
| `make seed` | `uv run python scripts/seed.py --no-reset` | Declarative sync (non-destructive) |
| `make db-reset` | `uv run python scripts/seed.py` | Destructive wipe & re-seed |

---

## 🌱 Declarative Seeding Engine

DGG-PM features a declarative seeding engine powered by YAML manifests (`seeds/base/manifest.yaml`):

### 1. Non-Destructive Seed (`make seed`)
```bash
make seed
```
- Reuses existing Discord forum channels and task threads.
- Reconciles database records against Discord snowflakes without deleting active data.
- Syncs PM forum tags and updates Control Hubs.

### 2. Hard Reset & Re-Seed (`make db-reset`)
```bash
make db-reset
```
- Completely wipes and re-creates database tables.
- Deletes the existing PM Discord category/channels.
- Re-provisions fresh project forum channels, tags, pinned Control Hubs, tasks, and tech trees.

### 3. Fast Database-Only Seed (`--no-discord`)
```bash
uv run python scripts/seed.py --no-discord
```
- Seeds database entities instantly without making Discord API calls.
- Ideal for offline development, local API testing, or CI test database population.
