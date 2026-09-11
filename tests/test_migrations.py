from pathlib import Path

from alembic.script import ScriptDirectory

from src.adapters.db.session import get_alembic_config
from src.adapters.db.tables import Base


def test_alembic_configuration_validity():
    """Validates that alembic.ini is discovered and loads the migration directory."""
    cfg = get_alembic_config()
    assert Path(cfg.config_file_name).is_file()

    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    assert len(heads) == 1
    assert heads[0] == "0001"


def test_initial_migration_metadata_matches_tables():
    """Validates that Base.metadata has all expected core domain tables matching the initial revision."""
    expected_tables = {
        "projects",
        "squads",
        "squad_members",
        "project_squads",
        "tasks",
        "task_watchers",
        "task_history",
        "task_dependencies",
        "outbox_events",
        "user_preferences",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(actual_tables), f"Missing tables: {expected_tables - actual_tables}"


def test_migration_revision_history():
    """Validates that migration revision history forms a continuous DAG up to head."""
    cfg = get_alembic_config()
    script_dir = ScriptDirectory.from_config(cfg)

    revisions = list(script_dir.walk_revisions())
    assert len(revisions) >= 1
    initial_rev = revisions[-1]
    assert initial_rev.revision == "0001"
    assert initial_rev.down_revision is None
