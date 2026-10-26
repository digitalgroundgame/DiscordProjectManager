"""In-memory fake implementation of INotificationDispatcher."""

from __future__ import annotations

from src.domain.models import OutboxEvent
from src.ports.notifier import INotificationDispatcher


class FakeNotificationDispatcher(INotificationDispatcher):
    """In-memory test double recording all dispatched outbox events."""

    def __init__(self) -> None:
        self.dispatched_events: list[OutboxEvent] = []

    async def dispatch_event(self, event: OutboxEvent) -> None:
        self.dispatched_events.append(event)

    def clear(self) -> None:
        self.dispatched_events.clear()
