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
