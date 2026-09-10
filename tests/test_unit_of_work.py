import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Project, Task
from src.ports.repositories import IOutboxRepo, IProjectRepo, ITaskRepo
from src.ports.unit_of_work import IUnitOfWork


@pytest.mark.asyncio
async def test_uow_provides_scoped_repositories_and_commits(async_engine: AsyncEngine):
    """Test that Unit of Work exposes scoped repositories and commits transactions without session passing."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow: IUnitOfWork = SqlAlchemyUnitOfWork(session_factory)

    guild_id = 999888777
    project = Project(
        guild_id=guild_id,
        name="Apollo Launch",
        prefix="APO",
        next_task_number=2,
    )

    # Use the Unit of Work as the shopping cart:
    async with uow:
        # 1. Verify repositories exist on the UoW seam
        assert isinstance(uow.tasks, ITaskRepo)
        assert isinstance(uow.projects, IProjectRepo)
        assert isinstance(uow.outbox, IOutboxRepo)

        # 2. Add project and task through the UoW's repositories with NO session parameter
        created_project = await uow.projects.create(project)

        task = Task(
            guild_id=guild_id,
            project_id=created_project.id,
            short_id="APO-1",
            task_number=1,
            title="Ignition Sequence",
            creator_discord_id=123,
            status=TaskStatus.NOT_STARTED,
            priority=PriorityLevel.HIGH,
            version=1,
        )
        await uow.tasks.create(task)
        await uow.commit()

    # 3. Verify in a separate Unit of Work that data was committed
    async with uow:
        loaded_task = await uow.tasks.get_by_short_id(guild_id, "APO-1")
        assert loaded_task is not None
        assert loaded_task.title == "Ignition Sequence"
        assert loaded_task.short_id == "APO-1"


@pytest.mark.asyncio
async def test_uow_rolls_back_on_exception(async_engine: AsyncEngine):
    """Test that Unit of Work rolls back all repository mutations if an error occurs."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow: IUnitOfWork = SqlAlchemyUnitOfWork(session_factory)

    guild_id = 11223344
    project = Project(
        guild_id=guild_id,
        name="Doomed Mission",
        prefix="DOOM",
    )

    with pytest.raises(RuntimeError, match="Engine explosion"):
        async with uow:
            await uow.projects.create(project)
            raise RuntimeError("Engine explosion")

    # Verify that the project was NOT committed to the database
    async with uow:
        found_project = await uow.projects.get_by_name(guild_id, "Doomed Mission")
        assert found_project is None


@pytest.mark.asyncio
async def test_task_service_rollback_on_outbox_failure(async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch):
    """Test that if Outbox enqueue fails during task creation, TaskService rolls back task persistence."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    uow: IUnitOfWork = SqlAlchemyUnitOfWork(session_factory)

    async with uow:
        project_repo = uow.projects
        outbox_repo = uow.outbox
        task_repo = uow.tasks

        from src.services.outbox_service import OutboxService
        from src.services.project_service import ProjectService
        from src.services.task_service import TaskService

        proj_svc = ProjectService(project_repo)
        outbox_svc = OutboxService(outbox_repo)
        task_svc = TaskService(task_repo, proj_svc, outbox_svc, uow=uow)

        p = await proj_svc.create_project(guild_id=55555, name="Secret Rocket", prefix="SR")
        await uow.commit()

    # Now monkeypatch outbox_service.enqueue_event to fail
    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("Simulated Outbox Failure")

    monkeypatch.setattr(outbox_svc, "enqueue_event", fail_enqueue)

    with pytest.raises(RuntimeError, match="Simulated Outbox Failure"):
        await task_svc.create_task(
            guild_id=55555,
            title="Ignite Boosters",
            creator_discord_id=999,
            project_id=p.id,
        )

    # In a fresh transaction, verify task was NOT persisted due to atomic rollback
    async with uow:
        tasks, count = await uow.tasks.list_tasks(guild_id=55555, project_id=p.id)
        assert count == 0
        assert len(tasks) == 0
