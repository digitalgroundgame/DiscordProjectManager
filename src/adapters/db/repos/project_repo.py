from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.adapters.db.repos.base import BasePostgresRepo
from src.adapters.db.tables import (
    ProjectSquadTable,
    ProjectTable,
)
from src.domain.models import Project, ProjectSquad, Squad
from src.ports.repositories import IProjectRepo


def _to_domain_project(row: ProjectTable) -> Project:
    role_ids: list[int] = []
    squads = getattr(row, "squads", []) or []
    for ps in squads:
        squad = getattr(ps, "squad", None)
        if squad and getattr(squad, "discord_role_id", None) is not None:
            rid = squad.discord_role_id
            if rid not in role_ids:
                role_ids.append(rid)

    return Project(
        id=row.id,
        guild_id=row.guild_id,
        name=row.name,
        prefix=row.prefix,
        next_task_number=row.next_task_number,
        description=row.description,
        discord_channel_id=row.discord_channel_id,
        discord_role_ids=role_ids,
        lead_discord_id=row.lead_discord_id,
        category=row.category,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresProjectRepo(BasePostgresRepo, IProjectRepo):
    async def create(self, project: Project) -> Project:
        async with self._get_session() as session:
            row = ProjectTable(
                id=project.id,
                guild_id=project.guild_id,
                name=project.name,
                prefix=project.prefix.upper(),
                next_task_number=project.next_task_number,
                description=project.description,
                discord_channel_id=project.discord_channel_id,
                lead_discord_id=project.lead_discord_id,
                category=project.category,
                archived_at=project.archived_at,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            session.add(row)

            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return project

    async def get_by_id(self, project_id: UUID) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                select(ProjectTable)
                .options(selectinload(ProjectTable.squads).joinedload(ProjectSquadTable.squad))
                .where(ProjectTable.id == project_id)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_name(self, guild_id: int, name: str) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                select(ProjectTable)
                .options(selectinload(ProjectTable.squads).joinedload(ProjectSquadTable.squad))
                .where(
                    ProjectTable.guild_id == guild_id,
                    func.upper(ProjectTable.name) == name.strip().upper(),
                )
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_prefix(self, guild_id: int, prefix: str) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                select(ProjectTable)
                .options(selectinload(ProjectTable.squads).joinedload(ProjectSquadTable.squad))
                .where(
                    ProjectTable.guild_id == guild_id,
                    func.upper(ProjectTable.prefix) == prefix.strip().upper(),
                )
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def get_by_channel_id(self, guild_id: int, channel_id: int) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                select(ProjectTable)
                .options(selectinload(ProjectTable.squads).joinedload(ProjectSquadTable.squad))
                .where(
                    ProjectTable.guild_id == guild_id,
                    ProjectTable.discord_channel_id == channel_id,
                )
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return _to_domain_project(row) if row else None

    async def increment_task_number_atomic(
        self, project_id: UUID, session: AsyncSession | None = None
    ) -> tuple[int, str]:
        async with self._get_session(session) as sess:
            now = datetime.now(UTC)
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(
                    next_task_number=ProjectTable.next_task_number + 1,
                    updated_at=now,
                )
                .returning(ProjectTable.next_task_number - 1, ProjectTable.prefix)
            )
            res = await sess.execute(stmt)
            row = res.first()
            if not row:
                raise ValueError(f"Project with ID {project_id} not found for atomic counter update")
            if self._should_commit(session):
                await sess.commit()
            else:
                await sess.flush()
            return row[0], row[1]

    async def list_projects(self, guild_id: int, include_archived: bool = False) -> list[Project]:
        async with self._get_session() as session:
            filters = [ProjectTable.guild_id == guild_id]
            if not include_archived:
                filters.append(ProjectTable.archived_at.is_(None))
            stmt = (
                select(ProjectTable)
                .options(selectinload(ProjectTable.squads).joinedload(ProjectSquadTable.squad))
                .where(*filters)
                .order_by(ProjectTable.name.asc())
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [_to_domain_project(r) for r in rows]

    async def set_archived(self, project_id: UUID, is_archived: bool) -> Project | None:
        async with self._get_session() as session:
            now = datetime.now(UTC) if is_archived else None
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(archived_at=now, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None

            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return _to_domain_project(row)

    async def update_channel_id(self, project_id: UUID, discord_channel_id: int | None) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(discord_channel_id=discord_channel_id, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return _to_domain_project(row)

    async def update_role_id(
        self,
        project_id: UUID,
        discord_role_id: int | None,
        squad_id: UUID | None = None,
    ) -> Project | None:
        async with self._get_session() as session:
            proj_stmt = select(ProjectTable).where(ProjectTable.id == project_id)
            res = await session.execute(proj_stmt)
            proj_row = res.scalar_one_or_none()
            if not proj_row:
                return None

            del_stmt = delete(ProjectSquadTable).where(ProjectSquadTable.project_id == project_id)
            await session.execute(del_stmt)

            if squad_id is not None:
                pt_row = ProjectSquadTable(project_id=project_id, squad_id=squad_id)
                await session.merge(pt_row)

            proj_row.updated_at = datetime.now(UTC)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return await self.get_by_id(project_id)

    async def update_lead_id(self, project_id: UUID, lead_discord_id: int | None) -> Project | None:
        async with self._get_session() as session:
            stmt = (
                update(ProjectTable)
                .where(ProjectTable.id == project_id)
                .values(lead_discord_id=lead_discord_id, updated_at=datetime.now(UTC))
                .returning(ProjectTable)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if not row:
                return None
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()
            return _to_domain_project(row)

    async def assign_squad(self, project_squad: ProjectSquad) -> None:
        async with self._get_session() as session:
            row = ProjectSquadTable(
                project_id=project_squad.project_id,
                squad_id=project_squad.squad_id,
                start_date=project_squad.start_date,
                timeline=project_squad.timeline,
            )
            await session.merge(row)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def remove_squad(self, project_id: UUID, squad_id: UUID) -> None:
        async with self._get_session() as session:
            stmt = delete(ProjectSquadTable).where(
                ProjectSquadTable.project_id == project_id,
                ProjectSquadTable.squad_id == squad_id,
            )
            await session.execute(stmt)
            if self._should_commit(None):
                await session.commit()
            else:
                await session.flush()

    async def list_squads_for_project(self, project_id: UUID) -> list[Squad]:
        async with self._get_session() as session:
            stmt = (
                select(ProjectSquadTable)
                .options(joinedload(ProjectSquadTable.squad))
                .where(ProjectSquadTable.project_id == project_id)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [
                Squad(
                    id=ps.squad.id,
                    guild_id=ps.squad.guild_id,
                    name=ps.squad.name,
                    discord_role_id=ps.squad.discord_role_id,
                    created_at=ps.squad.created_at,
                )
                for ps in rows
                if ps.squad is not None
            ]
