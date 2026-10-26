"""Rendering adapters for TechTree diagrams (Pillow PNG and Mermaid text)."""

from __future__ import annotations

from src.adapters.rendering.mermaid_renderer import MermaidTechTreeRenderer
from src.adapters.rendering.pillow_renderer import (
    THEME,
    PillowTechTreeRenderer,
    compute_layers,
    plan_layout,
    render_tree,
)
from src.domain.tech_tree import TechTree

# Auto-register default renderers onto pure domain TechTree
TechTree.register_default_renderers(
    png_renderer=PillowTechTreeRenderer(),
    mermaid_renderer=MermaidTechTreeRenderer(),
)

__all__ = [
    "THEME",
    "MermaidTechTreeRenderer",
    "PillowTechTreeRenderer",
    "compute_layers",
    "plan_layout",
    "render_tree",
]
