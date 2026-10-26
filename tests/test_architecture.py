"""Architectural Fitness Tests for 4-Layer Hexagonal Architecture.

Enforces strict unidirectional dependency boundaries:
Bootstrap -> Adapters (Driving / Driven) -> Application (Ports / Services) -> Domain (Pure Entities & Policies)

See docs/adr/0007-hexagonal-architecture.md and GitHub Issue #66.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PORTS_DIR = SRC_DIR / "ports"
DOMAIN_DIR = SRC_DIR / "domain"
SERVICES_DIR = SRC_DIR / "services"
ADAPTERS_DIR = SRC_DIR / "adapters"


def _extract_imported_modules(py_file: Path) -> list[tuple[int, str]]:
    """Return a list of (line_number, module_name) for all import statements in a file."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))

    return imports


def test_ports_have_no_vendor_discord_imports() -> None:
    """Core ports must remain vendor-agnostic and never import 'discord'."""
    assert PORTS_DIR.is_dir(), f"Ports directory not found at {PORTS_DIR}"

    violating_files: list[tuple[str, str]] = []

    for py_file in PORTS_DIR.glob("*.py"):
        for _, mod in _extract_imported_modules(py_file):
            if mod == "discord" or mod.startswith("discord."):
                violating_files.append((py_file.name, mod))

    assert not violating_files, (
        "Hexagonal architecture violation: vendor SDK 'discord' imported in src/ports/:\n"
        + "\n".join(f"  - {filename} imports '{mod}'" for filename, mod in violating_files)
    )


def test_ports_have_no_framework_or_adapter_imports() -> None:
    """Core ports must not depend on concrete adapters, database ORMs, or web frameworks."""
    forbidden_prefixes = (
        "discord",
        "src.adapters",
        "sqlalchemy",
        "fastapi",
        "PIL",
        "uvicorn",
    )

    violating_files: list[tuple[str, str]] = []

    for py_file in PORTS_DIR.glob("*.py"):
        for _, mod in _extract_imported_modules(py_file):
            if any(mod == f or mod.startswith(f"{f}.") for f in forbidden_prefixes):
                violating_files.append((py_file.name, mod))

    assert not violating_files, (
        "Hexagonal architecture violation: framework/adapter imported in src/ports/:\n"
        + "\n".join(f"  - {filename} imports '{mod}'" for filename, mod in violating_files)
    )


def test_domain_layer_isolation() -> None:
    """Domain layer must be pure enterprise logic with zero outward dependencies."""
    forbidden_prefixes = (
        "discord",
        "sqlalchemy",
        "fastapi",
        "PIL",
        "uvicorn",
        "src.adapters",
        "src.services",
        "src.ports",
    )

    # Zero domain exceptions allowed: pure enterprise entities & policies
    known_exceptions: set[tuple[str, str]] = set()

    violating_files: list[tuple[str, str]] = []

    for py_file in DOMAIN_DIR.glob("*.py"):
        for _, mod in _extract_imported_modules(py_file):
            for prefix in forbidden_prefixes:
                if mod == prefix or mod.startswith(f"{prefix}."):
                    if (py_file.name, mod) not in known_exceptions:
                        violating_files.append((py_file.name, mod))

    assert not violating_files, (
        "Hexagonal architecture violation: impure dependency imported in src/domain/:\n"
        + "\n".join(f"  - {filename} imports '{mod}'" for filename, mod in violating_files)
    )


def test_services_layer_isolation() -> None:
    """Application services must depend only on Domain and Ports, never vendor SDKs or concrete adapters."""
    forbidden_prefixes = (
        "discord",
        "sqlalchemy",
        "fastapi",
        "PIL",
        "uvicorn",
        "src.adapters",
        "src.config",
        "src.bootstrap",
    )

    # Zero exceptions in application services layer: pure domain & port dependencies only
    known_exceptions: set[tuple[str, str]] = set()

    violating_files: list[tuple[str, str]] = []

    for py_file in SERVICES_DIR.glob("*.py"):
        for _, mod in _extract_imported_modules(py_file):
            for prefix in forbidden_prefixes:
                if mod == prefix or mod.startswith(f"{prefix}."):
                    if (py_file.name, mod) not in known_exceptions:
                        violating_files.append((py_file.name, mod))

    assert not violating_files, (
        "Hexagonal architecture violation: framework/adapter imported in src/services/:\n"
        + "\n".join(f"  - {filename} imports '{mod}'" for filename, mod in violating_files)
    )


def test_driving_adapters_do_not_import_db_adapter() -> None:
    """Driving adapters (api, discord_bot) must not directly import concrete DB adapters or SQLAlchemy."""
    forbidden_prefixes = (
        "src.adapters.db",
        "sqlalchemy",
    )

    # Zero exceptions in driving adapters (api, discord_bot)
    known_exceptions: set[tuple[str, str]] = set()

    violating_files: list[tuple[str, str]] = []

    for driving_subdir in ("api", "discord_bot"):
        target_dir = ADAPTERS_DIR / driving_subdir
        if not target_dir.is_dir():
            continue
        for py_file in target_dir.rglob("*.py"):
            rel_path = str(py_file.relative_to(SRC_DIR.parent))
            for _, mod in _extract_imported_modules(py_file):
                for prefix in forbidden_prefixes:
                    if mod == prefix or mod.startswith(f"{prefix}."):
                        if (rel_path, mod) not in known_exceptions:
                            violating_files.append((rel_path, mod))

    assert not violating_files, (
        "Hexagonal architecture violation: driving adapter directly imports DB adapter/ORM:\n"
        + "\n".join(f"  - {path} imports '{mod}'" for path, mod in violating_files)
    )


def test_no_unittest_mock_in_production_code() -> None:
    """Production code under src/ must never import unittest.mock (use protocol test doubles in tests/fakes/)."""
    known_mock_exceptions: set[str] = set()

    violating_files: list[tuple[str, str]] = []

    for py_file in SRC_DIR.rglob("*.py"):
        rel_path = str(py_file.relative_to(SRC_DIR.parent))
        for _, mod in _extract_imported_modules(py_file):
            if mod == "unittest.mock" or mod.startswith("unittest.mock."):
                if rel_path not in known_mock_exceptions:
                    violating_files.append((rel_path, mod))

    assert not violating_files, "Architecture violation: unittest.mock imported in production code:\n" + "\n".join(
        f"  - {path} imports '{mod}'" for path, mod in violating_files
    )


def test_no_import_time_database_engine_or_singletons() -> None:
    """Importing modules under src/ must never instantiate database engines or global singletons."""
    import subprocess
    import sys

    verification_code = """
import importlib
import pkgutil
import src

for module_info in pkgutil.walk_packages(src.__path__, prefix='src.'):
    mod_name = module_info.name
    if 'migrations' in mod_name or mod_name.endswith('.__main__'):
        continue
    importlib.import_module(mod_name)

from src.adapters.db import session
assert session._engine is None, f'session._engine was eagerly instantiated: {session._engine}'
assert session._session_factory is None, (
    f'session._session_factory was eagerly instantiated: {session._session_factory}'
)

from src.adapters.discord_bot import menu_manager
assert menu_manager._default_menu_manager is None, 'menu_manager._default_menu_manager was eagerly instantiated'

from src import config
assert config._settings is None, f'config._settings was eagerly instantiated: {config._settings}'

from src.domain.tech_tree import TechTree
assert not hasattr(TechTree, "register_default_renderers"), "TechTree has register_default_renderers global method"
assert getattr(TechTree, "_default_png_renderer", None) is None
assert getattr(TechTree, "_default_mermaid_renderer", None) is None
"""
    result = subprocess.run(
        [sys.executable, "-c", verification_code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Importing src/ modules triggered eager singletons:\n{result.stderr}\n{result.stdout}"
    )


def test_postgres_project_repo_create_and_update_role_do_not_query_squad_table() -> None:
    """PostgresProjectRepo.create and update_role_id must not reference or query SquadTable (Issue #84)."""
    repo_file = ADAPTERS_DIR / "db" / "repos" / "project_repo.py"
    assert repo_file.is_file(), f"File not found: {repo_file}"

    tree = ast.parse(repo_file.read_text(encoding="utf-8"), filename=str(repo_file))
    target_methods = {"create", "update_role_id"}

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in target_methods:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "SquadTable":
                    raise AssertionError(
                        f"PostgresProjectRepo.{node.name} directly references SquadTable at line {child.lineno}"
                    )


def test_discord_bot_adapter_modules_do_not_exceed_600_lines() -> None:
    """Every module under src/adapters/discord_bot/ must strictly observe the 600-line ceiling (AGENTS.md)."""
    discord_bot_dir = ADAPTERS_DIR / "discord_bot"
    assert discord_bot_dir.is_dir(), f"Directory not found: {discord_bot_dir}"

    oversized_files: list[tuple[str, int]] = []
    for py_file in discord_bot_dir.rglob("*.py"):
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        if line_count > 600:
            oversized_files.append((str(py_file.relative_to(SRC_DIR.parent)), line_count))

    assert not oversized_files, (
        "Modularity violation: Discord adapter modules exceed 600 lines (AGENTS.md):\n"
        + "\n".join(f"  - {path}: {count} lines" for path, count in oversized_files)
    )
