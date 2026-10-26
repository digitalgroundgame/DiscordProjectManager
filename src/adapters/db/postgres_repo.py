"""PostgreSQL repository implementations (re-exported from src.adapters.db.repos).

This module maintains backwards compatibility during the 4-layer hexagonal
architecture migration (Issue #60). All concrete repository classes have been
decomposed into cohesive, per-aggregate modules under src/adapters/db/repos/.
"""

from __future__ import annotations

from src.adapters.db.repos import (
    BasePostgresRepo,
    PostgresGuildLeadRoleRepository,
    PostgresGuildLeadUserRepository,
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserCalendarTokenRepo,
    PostgresUserPreferenceRepo,
    _to_domain_outbox,
    _to_domain_project,
    _to_domain_squad,
    _to_domain_squad_member,
    _to_domain_task,
)

__all__ = [
    "BasePostgresRepo",
    "PostgresGuildLeadRoleRepository",
    "PostgresGuildLeadUserRepository",
    "PostgresOutboxRepo",
    "PostgresProjectRepo",
    "PostgresSquadRepo",
    "PostgresTaskRepo",
    "PostgresUserCalendarTokenRepo",
    "PostgresUserPreferenceRepo",
    "_to_domain_outbox",
    "_to_domain_project",
    "_to_domain_squad",
    "_to_domain_squad_member",
    "_to_domain_task",
]
