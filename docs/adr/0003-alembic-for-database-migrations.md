# Alembic for Database Migrations

## Context and Decision
Previously, database schemas in `dgg-pm` were managed via SQLAlchemy's `Base.metadata.create_all()` executed on startup and in developer reset scripts. As the data model evolved to support task dependencies, squad mappings, outbox delivery, and user notification preferences, raw DDL creation became insufficient for tracking schema evolution, rolling out non-destructive changes, and supporting multi-container production deployments.

We decided to adopt Alembic as the official schema migration management tool, integrated directly within the Hexagonal persistence adapter (`src/adapters/db/migrations/`). Migrations utilize Alembic's native asynchronous runner over `asyncpg`, reusing the core application `settings.DATABASE_URL` without requiring synchronous database drivers. Schema migrations run automatically on startup in development when `AUTO_RUN_MIGRATIONS=true` (with safe baseline auto-stamping for legacy unversioned schemas), while remaining fully decoupled for production entrypoint execution.

## Considered Options
- **`Base.metadata.create_all` on startup (Rejected)**: Cannot perform schema migrations, alters, column additions, or index changes on existing production tables without dropping and losing data.
- **Dual driver setup with synchronous psycopg2 (Rejected)**: Required introducing additional synchronous PostgreSQL driver dependencies and managing conflicting driver URL schemes (`postgresql://` vs `postgresql+asyncpg://`).
- **External CLI/entrypoint-only migrations without startup hook (Rejected)**: While ideal for strictly decoupled container clusters, it increased friction for local development and test environments running `devenv up` or `run-app`.
- **Alembic with native async runner and configurable startup execution (Chosen)**: Utilizes SQLAlchemy's native async engine with `asyncpg`, collocates migrations under the Hexagonal persistence adapter, and provides configurable startup migration execution (`AUTO_RUN_MIGRATIONS`) alongside standard CLI commands (`db-migrate`, `db-revision`).

## Consequences
- Schema changes must now be recorded as Alembic revisions (`devenv shell -- db-revision -m "..."` or `alembic revision --autogenerate`).
- Migration scripts and configuration are packaged into Docker images via `COPY alembic.ini .` and `src/adapters/db/migrations/`.
- Development database resets (`clear_db.py` / `db-reset`) drop schemas completely and rebuild using `alembic upgrade head`.
- Automated in-memory SQLite unit tests continue using fast `Base.metadata.create_all`, while migration health and revisions are verified via `tests/test_migrations.py`.
