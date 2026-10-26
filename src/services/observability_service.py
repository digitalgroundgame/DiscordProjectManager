from __future__ import annotations

from src.ports.observability import SystemMetricsQueryPort, SystemMetricsSnapshot


class ObservabilityService:
    """Application service for system health checks and platform metrics."""

    def __init__(self, metrics_port: SystemMetricsQueryPort) -> None:
        self._metrics_port = metrics_port

    async def get_metrics(self) -> SystemMetricsSnapshot:
        return await self._metrics_port.get_metrics_snapshot()

    async def check_health(self) -> bool:
        return await self._metrics_port.check_health()
