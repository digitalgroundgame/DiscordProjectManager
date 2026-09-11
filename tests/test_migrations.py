import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic.script import ScriptDirectory

from src.adapters.db.session import get_alembic_config, run_migrations
from src.adapters.db.tables import Base


def test_alembic_configuration_validity():
    """Validates that alembic.ini is discovered and loads the migration directory."""
    cfg = get_alembic_config()
    assert Path(cfg.config_file_name).is_file()

    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    assert len(heads) == 1
    known_revisions = {rev.revision for rev in script_dir.walk_revisions()}
    assert heads[0] in known_revisions


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


@pytest.mark.asyncio
async def test_run_migrations_auto_stamps_unversioned_db_to_base_and_upgrades():
    """Validates that unversioned databases are stamped to the base revision, then upgraded to head with drift check."""
    mock_conn = AsyncMock()
    mock_conn.run_sync.return_value = ["projects", "tasks"]
    mock_conn_ctx = AsyncMock()
    mock_conn_ctx.__aenter__.return_value = mock_conn
    mock_conn_ctx.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn_ctx

    with (
        patch("src.adapters.db.session.engine", mock_engine),
        patch("src.adapters.db.session.command.stamp") as mock_stamp,
        patch("src.adapters.db.session.command.upgrade") as mock_upgrade,
        patch("src.adapters.db.session.command.check") as mock_check,
    ):
        await run_migrations(max_retries=1)

        # Assert stamped to base revision (0001), NOT "head"
        mock_stamp.assert_called_once()
        assert mock_stamp.call_args[0][1] == "0001"
        # Assert upgraded to head
        mock_upgrade.assert_called_once()
        assert mock_upgrade.call_args[0][1] == "head"
        # Assert schema check was performed
        mock_check.assert_called_once()


@pytest.mark.asyncio
async def test_run_migrations_logs_warning_on_schema_drift(caplog):
    """Validates that run_migrations logs a warning when schema drift is detected."""
    mock_conn = AsyncMock()
    mock_conn.run_sync.return_value = ["alembic_version"]
    res_mock = MagicMock()
    res_mock.fetchall.return_value = [("0001",)]
    mock_conn.execute.return_value = res_mock
    mock_conn_ctx = AsyncMock()
    mock_conn_ctx.__aenter__.return_value = mock_conn
    mock_conn_ctx.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn_ctx

    with (
        patch("src.adapters.db.session.engine", mock_engine),
        patch("src.adapters.db.session.command.upgrade"),
        patch("src.adapters.db.session.command.check", side_effect=RuntimeError("Autogenerate diff detected")),
    ):
        with caplog.at_level(logging.WARNING):
            await run_migrations(max_retries=1)

        assert any("Database schema drift detected" in record.message for record in caplog.records)
