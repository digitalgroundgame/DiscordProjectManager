"""Helper script to generate sequential Alembic migration revisions."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def get_next_revision_id(versions_dir: Path) -> str:
    """Computes the next 4-digit sequential revision ID (e.g. '0002') from existing versions."""
    max_rev = 0
    if versions_dir.is_dir():
        for file in versions_dir.glob("*.py"):
            match = re.match(r"^(\d+)", file.name)
            if match:
                max_rev = max(max_rev, int(match.group(1)))
    return f"{max_rev + 1:04d}"


def build_alembic_command(args: list[str], versions_dir: Path) -> list[str]:
    """Constructs the alembic revision command with auto-sequential rev-id if omitted."""
    has_rev_id = any(arg == "--rev-id" or arg.startswith("--rev-id=") for arg in args)
    clean_args = [arg for arg in args if arg not in ("--manual", "--no-autogenerate")]
    has_manual = len(clean_args) != len(args)
    has_auto = "--autogenerate" in clean_args

    cmd = ["alembic", "revision"]
    if not has_manual and not has_auto:
        cmd.append("--autogenerate")

    if not has_rev_id:
        next_id = get_next_revision_id(versions_dir)
        cmd.extend(["--rev-id", next_id])

    cmd.extend(clean_args)
    return cmd


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    versions_dir = project_root / "src" / "adapters" / "db" / "migrations" / "versions"
    cmd = build_alembic_command(sys.argv[1:], versions_dir)
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
