from src.config import Settings


def test_auto_run_migrations_defaults_to_false(monkeypatch):
    """Verifies that AUTO_RUN_MIGRATIONS defaults to False for safe production deployments."""
    monkeypatch.delenv("AUTO_RUN_MIGRATIONS", raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.AUTO_RUN_MIGRATIONS is False
