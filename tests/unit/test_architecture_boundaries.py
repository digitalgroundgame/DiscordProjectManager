"""Unit test suite for architectural boundary ratchets and layer fitness.

See docs/adr/0007-hexagonal-architecture.md and GitHub Issues #66, #87.
"""

from __future__ import annotations

from tests.test_architecture import (
    test_discord_bot_adapter_modules_do_not_exceed_600_lines,
    test_domain_layer_isolation,
    test_driving_adapters_do_not_import_db_adapter,
    test_no_import_time_database_engine_or_singletons,
    test_no_unittest_mock_in_production_code,
    test_ports_have_no_framework_or_adapter_imports,
    test_ports_have_no_vendor_discord_imports,
    test_postgres_project_repo_create_and_update_role_do_not_query_squad_table,
    test_project_repo_does_not_query_other_aggregates,
    test_services_layer_isolation,
)

__all__ = [
    "test_discord_bot_adapter_modules_do_not_exceed_600_lines",
    "test_domain_layer_isolation",
    "test_driving_adapters_do_not_import_db_adapter",
    "test_no_import_time_database_engine_or_singletons",
    "test_no_unittest_mock_in_production_code",
    "test_ports_have_no_framework_or_adapter_imports",
    "test_ports_have_no_vendor_discord_imports",
    "test_postgres_project_repo_create_and_update_role_do_not_query_squad_table",
    "test_project_repo_does_not_query_other_aggregates",
    "test_services_layer_isolation",
]
