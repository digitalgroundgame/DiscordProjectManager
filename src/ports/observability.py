from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SystemMetricsSnapshot:
    """Operational metrics snapshot of the platform."""

    total_tasks: int
    total_projects: int
    pending_outbox_events: int


@runtime_checkable
class SystemMetricsQueryPort(Protocol):
    """Port for querying platform metrics and connectivity probes."""

    async def get_metrics_snapshot(self) -> SystemMetricsSnapshot:
        """Collects current platform metrics snapshot."""
        ...

    async def check_health(self) -> bool:
        """Performs a connectivity probe to verify datastore health."""
        ...
