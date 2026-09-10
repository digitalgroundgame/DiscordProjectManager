import ast
from pathlib import Path


def test_ports_have_no_vendor_discord_imports():
    """Architectural Fitness Test:

    In Hexagonal Architecture, core ports must remain independent of external vendor SDKs.
    No file within src/ports/ is permitted to import 'discord'.
    """
    ports_dir = Path(__file__).resolve().parent.parent / "src" / "ports"
    assert ports_dir.is_dir(), f"Ports directory not found at {ports_dir}"

    violating_files: list[tuple[str, str]] = []

    for py_file in ports_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "discord" or alias.name.startswith("discord."):
                        violating_files.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module == "discord" or (node.module and node.module.startswith("discord.")):
                    violating_files.append((py_file.name, node.module))

    assert not violating_files, (
        "Hexagonal architecture violation: vendor SDK 'discord' imported in src/ports/:\n"
        + "\n".join(f"  - {filename} imports '{mod}'" for filename, mod in violating_files)
    )
