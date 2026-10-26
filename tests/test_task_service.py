"""Unit tests for TaskService atomic prerequisite rollback and TransactionalService inheritance."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.domain.exceptions import ValidationError
from src.services.auth_service import AuthService
from src.services.base import TransactionalService
from src.services.calendar_service import CalendarService
from src.services.outbox_service import OutboxService
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService
from src.services.user_service import UserService


def test_all_transactional_services_inherit_transactional_service() -> None:
    """All 6 transactional application services must inherit from TransactionalService (Issue #82)."""
    for service_cls in (
        TaskService,
        ProjectService,
        SquadService,
        AuthService,
        CalendarService,
        UserService,
    ):
        assert issubclass(service_cls, TransactionalService), (
            f"{service_cls.__name__} does not inherit from TransactionalService"
        )


@pytest.mark.asyncio
async def test_create_task_rolls_back_persisted_row_when_post_insert_dependency_fails(
    async_engine: AsyncEngine,
) -> None:
    """Verify TaskService.create_task rolls back task insert if add_dependency fails inside the transaction."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow = SqlAlchemyUnitOfWork(session_factory)
    proj_svc = ProjectService(uow.projects, uow=uow)
    outbox_svc = OutboxService(uow.outbox)
    task_svc = TaskService(uow.tasks, proj_svc, outbox_svc, uow=uow)

    project = await proj_svc.create_project(guild_id=88888, name="Rollback Check", prefix="RBC")
    t1 = await task_svc.create_task(
        guild_id=88888,
        title="First Task",
        creator_discord_id=101,
        project_id=project.id,
    )
    assert t1.short_id == "RBC-1"

    # Pre-seed a dependency edge from RBC-1 -> RBC-2's future slot by hooking add_dependency
    # so that after RBC-2 is inserted by uow.tasks.create(task), linking RBC-2 -> RBC-1 detects a cycle.
    orig_validate_edge = task_svc.add_dependency

    async def cycle_injecting_add_dependency(
        guild_id: int,
        task_short_id: str,
        depends_on_short_id: str,
        actor_discord_id: int | None = None,
    ) -> bool:
        # At this point in create_task, RBC-2 has ALREADY been inserted into the active transaction session!
        created_in_tx = await uow.tasks.get_by_short_id(guild_id, task_short_id)
        assert created_in_tx is not None
        # Add reverse edge RBC-1 depends on RBC-2 first so RBC-2 -> RBC-1 triggers CircularDependencyError
        await uow.tasks.add_dependency(t1.id, created_in_tx.id)
        return await orig_validate_edge(guild_id, task_short_id, depends_on_short_id, actor_discord_id)

    task_svc.add_dependency = cycle_injecting_add_dependency  # type: ignore[method-assign]

    with pytest.raises(ValidationError, match="circular loop"):
        await task_svc.create_task(
            guild_id=88888,
            title="Second Task Introducing Cycle",
            creator_discord_id=101,
            project_id=project.id,
            prerequisite_short_ids=["RBC-1"],
        )

    # Confirm both the RBC-2 task row and the injected dependency were rolled back atomically
    tasks, count = await uow.tasks.list_tasks(guild_id=88888, project_id=project.id)
    assert count == 1
    assert [t.short_id for t in tasks] == ["RBC-1"]
