"""Reusable in-memory test doubles (fakes) for hexagonal architecture ports.

Use these test doubles instead of unittest.mock when testing domain logic and application use cases.
"""

from __future__ import annotations

from tests.fakes.fake_notifier import FakeNotificationDispatcher
from tests.fakes.fake_uow import FakeUnitOfWork

__all__ = [
    "FakeNotificationDispatcher",
    "FakeUnitOfWork",
]
