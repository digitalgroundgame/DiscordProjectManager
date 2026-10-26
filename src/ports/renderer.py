"""Port interfaces for rendering domain models (such as TechTree)."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

T_RenderOutput = TypeVar("T_RenderOutput")


class TechTreeRenderer(Protocol[T_RenderOutput]):
    """Seam protocol for swappable Tech Tree output renderers."""

    def render(self, tree: Any, **kwargs: Any) -> T_RenderOutput:
        """Renders a TechTree domain instance into output format T_RenderOutput."""
        ...
