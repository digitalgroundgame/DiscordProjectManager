# Clean ambient virtualenv variables for deterministic uv execution
unexport UV_PROJECT_ENVIRONMENT
unexport VIRTUAL_ENV

PYTHON ?= uv run python
UV ?= uv
DOCKER_COMPOSE ?= docker compose

.DEFAULT_GOAL := help

.PHONY: help
help: ## Display this help screen
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Environment & Dependencies ---

.PHONY: install
install: ## Sync dependencies and create venv via uv
	$(UV) sync --all-extras

.PHONY: sync
sync: install ## Alias for install

# --- Development & Execution ---

.PHONY: run
run: ## Run the Discord bot application locally
	$(PYTHON) -m src.main

.PHONY: dev
dev: run ## Alias for run

# --- Testing & Quality ---

.PHONY: test
test: ## Run the pytest test suite (in-memory SQLite, fast)
	$(UV) run --all-extras pytest -n 4 tests/

.PHONY: test-cov
test-cov: ## Run test suite with coverage report
	$(UV) run --all-extras pytest -n 4 --cov=src --cov-report=term-missing tests/

.PHONY: lint
lint: ## Run ruff linter
	$(UV) run ruff check .

.PHONY: lint-fix
lint-fix: ## Run ruff linter and apply fixes
	$(UV) run ruff check --fix .

.PHONY: format
format: ## Format code with ruff
	$(UV) run ruff format .

.PHONY: format-check
format-check: ## Check code formatting with ruff
	$(UV) run ruff format --check .

.PHONY: check
check: lint format-check test ## Run lint, format-check, and tests

# --- Database Management (PostgreSQL) ---

.PHONY: db-up
db-up: ## Start PostgreSQL container in background
	$(DOCKER_COMPOSE) up -d postgres

.PHONY: db-down
db-down: ## Stop PostgreSQL container
	$(DOCKER_COMPOSE) stop postgres

.PHONY: db-init
db-init: ## Initialize database schema tables
	$(PYTHON) -c 'import asyncio; from src.adapters.db.session import init_db; asyncio.run(init_db())'

.PHONY: db-migrate
db-migrate: ## Run pending Alembic migrations
	$(UV) run alembic upgrade head

.PHONY: db-check
db-check: ## Check if any migrations are pending or models out of sync
	$(UV) run alembic check

.PHONY: db-revision
db-revision: ## Generate a sequential migration revision (usage: make db-revision MSG="add_feature")
	$(PYTHON) scripts/generate_revision.py $(if $(MSG),-m "$(MSG)",)

.PHONY: db-clear
db-clear: ## Wipe all database tables (development safe)
	$(PYTHON) scripts/clear_db.py

.PHONY: seed
seed: ## Seed declarative state preserving Discord channels and threads (usage: make seed [PROFILE=...])
	$(PYTHON) scripts/seed.py --no-reset $(if $(PROFILE),--profile $(PROFILE),) $(if $(GUILD_ID),--guild-id $(GUILD_ID),)

.PHONY: db-reset
db-reset: ## Wipe database tables and Discord category, then re-seed fresh (usage: make db-reset [PROFILE=...])
	$(PYTHON) scripts/seed.py $(if $(PROFILE),--profile $(PROFILE),) $(if $(GUILD_ID),--guild-id $(GUILD_ID),)

.PHONY: db-seed-tree
db-seed-tree: ## Seed development projects with DAG example trees
	$(PYTHON) scripts/seed.py $(if $(GUILD_ID),--guild-id $(GUILD_ID),)

.PHONY: db-shell
db-shell: ## Open interactive psql shell inside postgres container
	$(DOCKER_COMPOSE) exec postgres psql -U postgres -d dgg_pm

# --- Full Container Lifecycle (Docker Compose) ---

.PHONY: docker-up
docker-up: ## Start all services (app + postgres) via Docker Compose
	$(DOCKER_COMPOSE) up -d

.PHONY: docker-down
docker-down: ## Stop all Docker Compose services
	$(DOCKER_COMPOSE) down

.PHONY: docker-build
docker-build: ## Rebuild and restart the application container
	$(DOCKER_COMPOSE) up -d --build app

.PHONY: docker-logs
docker-logs: ## Follow logs of Docker Compose services
	$(DOCKER_COMPOSE) logs -f
