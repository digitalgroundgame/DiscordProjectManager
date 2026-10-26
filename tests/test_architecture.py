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

    # Known temporary exceptions tracked by migration issues:
    # - ("tech_tree.py", "src.services.tree_render"): Tracked by Issue #54 (Arch 1.2)
    known_exceptions = {
        ("tech_tree.py", "src.services.tree_render"),
    }

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


def test_no_unittest_mock_in_production_code() -> None:
    """Production code under src/ must never import unittest.mock (use protocol test doubles in tests/fakes/)."""
    # Known temporary violations tracked by Issue #64 (Arch 4.4):
    known_mock_exceptions = {
        "src/adapters/discord_bot/menu_manager.py",
        "src/adapters/discord_bot/views/task_buttons.py",
    }

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
