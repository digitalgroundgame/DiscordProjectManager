from __future__ import annotations

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.repos.calendar_token_repo import PostgresUserCalendarTokenRepo
from src.adapters.db.repos.governance_repo import (
    PostgresGuildLeadRoleRepository,
    PostgresGuildLeadUserRepository,
)
from src.adapters.db.repos.outbox_repo import (
    PostgresOutboxRepo,
    _to_domain_outbox,
)
from src.adapters.db.repos.project_repo import (
    PostgresProjectRepo,
    _to_domain_project,
)
from src.adapters.db.repos.squad_repo import (
    PostgresSquadRepo,
    _to_domain_squad,
    _to_domain_squad_member,
)
from src.adapters.db.repos.task_repo import (
    PostgresTaskRepo,
    _to_domain_task,
)
from src.adapters.db.repos.user_pref_repo import PostgresUserPreferenceRepo

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
