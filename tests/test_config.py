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


def test_gateway_retry_settings(monkeypatch):
    """Verifies that gateway retry configuration has sensible defaults and supports overrides."""
    monkeypatch.delenv("GATEWAY_RETRY_INITIAL_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("GATEWAY_RETRY_MAX_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("GATEWAY_RETRY_BACKOFF_FACTOR", raising=False)
    monkeypatch.delenv("GATEWAY_RETRY_JITTER", raising=False)
    monkeypatch.delenv("GATEWAY_MAX_RETRIES", raising=False)

    cfg = Settings(_env_file=None)
    assert cfg.GATEWAY_RETRY_INITIAL_DELAY_SECONDS == 2.0
    assert cfg.GATEWAY_RETRY_MAX_DELAY_SECONDS == 60.0
    assert cfg.GATEWAY_RETRY_BACKOFF_FACTOR == 2.0
    assert cfg.GATEWAY_RETRY_JITTER == 0.2
    assert cfg.GATEWAY_MAX_RETRIES is None

    custom = Settings(
        _env_file=None,
        GATEWAY_RETRY_INITIAL_DELAY_SECONDS=1.0,
        GATEWAY_RETRY_MAX_DELAY_SECONDS=30.0,
        GATEWAY_RETRY_BACKOFF_FACTOR=1.5,
        GATEWAY_RETRY_JITTER=0.1,
        GATEWAY_MAX_RETRIES=10,
    )
    assert custom.GATEWAY_RETRY_INITIAL_DELAY_SECONDS == 1.0
    assert custom.GATEWAY_RETRY_MAX_DELAY_SECONDS == 30.0
    assert custom.GATEWAY_RETRY_BACKOFF_FACTOR == 1.5
    assert custom.GATEWAY_RETRY_JITTER == 0.1
    assert custom.GATEWAY_MAX_RETRIES == 10


def test_minimal_configuration_uses_static_defaults(monkeypatch):
    """Verifies that providing only secrets leaves all operational tuning parameters at static defaults."""
    for key in (
        "DISCORD_GUILD_ID",
        "ENVIRONMENT",
        "AUTO_RUN_MIGRATIONS",
        "OUTBOX_POLL_INTERVAL_SECONDS",
        "OUTBOX_BATCH_SIZE",
        "OUTBOX_MAX_RETRIES",
        "OUTBOX_BACKOFF_CAP_SECONDS",
        "OUTBOX_MAX_RETENTION_HOURS",
        "OUTBOX_RECLAIM_LOOKBACK_HOURS",
        "API_HOST",
        "API_PORT",
        "DEBUG",
        "API_METRICS_TOKEN",
        "SYNC_COMMANDS_ON_STARTUP",
        "STARTUP_CRASH_BACKOFF_SECONDS",
        "GATEWAY_RETRY_INITIAL_DELAY_SECONDS",
        "GATEWAY_RETRY_MAX_DELAY_SECONDS",
        "GATEWAY_RETRY_BACKOFF_FACTOR",
        "GATEWAY_RETRY_JITTER",
        "GATEWAY_MAX_RETRIES",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = Settings(
        _env_file=None,
        DISCORD_BOT_TOKEN="mock_token",
        DISCORD_CLIENT_ID="123456",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )
    # Secrets & IDs
    assert cfg.DISCORD_BOT_TOKEN == "mock_token"
    assert cfg.DISCORD_CLIENT_ID == "123456"
    assert cfg.DISCORD_GUILD_ID is None

    # Static Outbox tuning defaults
    assert cfg.OUTBOX_POLL_INTERVAL_SECONDS == 5.0
    assert cfg.OUTBOX_BATCH_SIZE == 10
    assert cfg.OUTBOX_MAX_RETRIES == 150
    assert cfg.OUTBOX_BACKOFF_CAP_SECONDS == 600.0
    assert cfg.OUTBOX_MAX_RETENTION_HOURS == 48.0
    assert cfg.OUTBOX_RECLAIM_LOOKBACK_HOURS == 24.0

    # Static API defaults
    assert cfg.API_HOST == "0.0.0.0"
    assert cfg.API_PORT == 8000
    assert cfg.DEBUG is False
    assert cfg.API_METRICS_TOKEN == ""

    # Static Gateway supervisor defaults
    assert cfg.GATEWAY_RETRY_INITIAL_DELAY_SECONDS == 2.0
    assert cfg.GATEWAY_RETRY_MAX_DELAY_SECONDS == 60.0
    assert cfg.GATEWAY_RETRY_BACKOFF_FACTOR == 2.0
    assert cfg.GATEWAY_RETRY_JITTER == 0.2
    assert cfg.GATEWAY_MAX_RETRIES is None
