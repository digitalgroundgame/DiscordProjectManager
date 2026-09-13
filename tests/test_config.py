from src.config import Settings


def test_auto_run_migrations_defaults_to_false(monkeypatch):
    """Verifies that AUTO_RUN_MIGRATIONS defaults to False for safe production deployments."""
    monkeypatch.delenv("AUTO_RUN_MIGRATIONS", raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.AUTO_RUN_MIGRATIONS is False


def test_database_url_normalization(monkeypatch):
    """Verifies that standard postgres:// and postgresql:// prefixes normalize to postgresql+asyncpg://."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    cfg1 = Settings(_env_file=None, DATABASE_URL="postgres://user:pass@db:5432/app")
    assert cfg1.DATABASE_URL == "postgresql+asyncpg://user:pass@db:5432/app"

    cfg2 = Settings(_env_file=None, DATABASE_URL="postgresql://user:pass@db:5432/app")
    assert cfg2.DATABASE_URL == "postgresql+asyncpg://user:pass@db:5432/app"

    cfg3 = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://user:pass@db:5432/app")
    assert cfg3.DATABASE_URL == "postgresql+asyncpg://user:pass@db:5432/app"

    cfg4 = Settings(_env_file=None, DATABASE_URL="sqlite+aiosqlite:///:memory:")
    assert cfg4.DATABASE_URL == "sqlite+aiosqlite:///:memory:"


def test_sync_commands_on_startup_defaults(monkeypatch):
    """Verifies that SYNC_COMMANDS_ON_STARTUP defaults to True in dev, False in prod, and respects explicit setting."""
    monkeypatch.delenv("SYNC_COMMANDS_ON_STARTUP", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    dev_cfg = Settings(_env_file=None, ENVIRONMENT="development")
    assert dev_cfg.SYNC_COMMANDS_ON_STARTUP is True

    prod_cfg = Settings(_env_file=None, ENVIRONMENT="production")
    assert prod_cfg.SYNC_COMMANDS_ON_STARTUP is False

    override_dev = Settings(_env_file=None, ENVIRONMENT="development", SYNC_COMMANDS_ON_STARTUP=False)
    assert override_dev.SYNC_COMMANDS_ON_STARTUP is False

    override_prod = Settings(_env_file=None, ENVIRONMENT="production", SYNC_COMMANDS_ON_STARTUP=True)
    assert override_prod.SYNC_COMMANDS_ON_STARTUP is True


def test_startup_crash_backoff_seconds_defaults(monkeypatch):
    """Verifies that STARTUP_CRASH_BACKOFF_SECONDS defaults to 30.0 seconds."""
    monkeypatch.delenv("STARTUP_CRASH_BACKOFF_SECONDS", raising=False)

    cfg = Settings(_env_file=None)
    assert cfg.STARTUP_CRASH_BACKOFF_SECONDS == 30.0

    custom = Settings(_env_file=None, STARTUP_CRASH_BACKOFF_SECONDS=10.5)
    assert custom.STARTUP_CRASH_BACKOFF_SECONDS == 10.5


def test_outbox_retention_and_backoff_settings(monkeypatch):
    """Verifies that outbox retention, max retries, and backoff cap have resilient defaults and support overrides."""
    monkeypatch.delenv("OUTBOX_MAX_RETRIES", raising=False)
    monkeypatch.delenv("OUTBOX_BACKOFF_CAP_SECONDS", raising=False)
    monkeypatch.delenv("OUTBOX_MAX_RETENTION_HOURS", raising=False)

    cfg = Settings(_env_file=None)
    assert cfg.OUTBOX_MAX_RETRIES == 150
    assert cfg.OUTBOX_BACKOFF_CAP_SECONDS == 600.0
    assert cfg.OUTBOX_MAX_RETENTION_HOURS == 48.0

    custom = Settings(
        _env_file=None,
        OUTBOX_MAX_RETRIES=20,
        OUTBOX_BACKOFF_CAP_SECONDS=300.0,
        OUTBOX_MAX_RETENTION_HOURS=24.0,
    )
    assert custom.OUTBOX_MAX_RETRIES == 20
    assert custom.OUTBOX_BACKOFF_CAP_SECONDS == 300.0
    assert custom.OUTBOX_MAX_RETENTION_HOURS == 24.0
