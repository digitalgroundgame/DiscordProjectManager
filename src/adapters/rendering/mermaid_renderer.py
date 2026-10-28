"""Mermaid DAG syntax renderer for TechTree domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.domain.tech_tree import TechTree


class MermaidTechTreeRenderer:
    """Concrete adapter formatting a TechTree into a Mermaid DAG codeblock for Discord text/threads."""

    def render(
        self,
        tree: TechTree,
        orientation: str = "lr",
        **kwargs: Any,
    ) -> str:
        orient_tag = "TB" if orientation.lower() in ("tb", "vertical") else "LR"
        lines = [f"graph {orient_tag}"]

        # Class styles for readiness states
        lines.append("    classDef complete fill:#0a2e1d,stroke:#57f287,stroke-width:2px,color:#f0fdf4;")
        lines.append("    classDef active fill:#062846,stroke:#38bdf8,stroke-width:2px,color:#f0f9ff;")
        lines.append("    classDef available fill:#102a5c,stroke:#818cf8,stroke-width:2px,color:#e0e7ff;")
        lines.append("    classDef locked fill:#0a101f,stroke:#334155,stroke-width:1px,color:#94a3b8;")
        lines.append("    classDef blocked fill:#3b0d0c,stroke:#ed4245,stroke-width:2px,color:#fef2f2;")

        nodes, edges = tree.to_nodes_data() if hasattr(tree, "to_nodes_data") else ([], [])
        for n in nodes:
            key = n["key"]
            name = n["name"].replace('"', "'")
            state = n.get("state", "locked")
            lines.append(f'    {key}["{key}: {name}"]:::{state}')

        for src, dst in edges:
            lines.append(f"    {src} --> {dst}")

        return "\n".join(lines)
