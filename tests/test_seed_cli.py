from __future__ import annotations

from pathlib import Path

import pytest

from scripts.seed import check_production_safety_guard, run_seed
from src.config import settings


def test_seed_safety_guards(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="SAFETY BLOCK: Seeding script cannot be run in production"):
        check_production_safety_guard()

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(
        settings, "DATABASE_URL", "postgresql+asyncpg://user:pass@ep-cool-db.us-east-2.neon.tech/dgg_pm"
    )
    with pytest.raises(
        RuntimeError, match="SAFETY BLOCK: DATABASE_URL appears to point to a production/cloud database"
    ):
        check_production_safety_guard()


@pytest.mark.asyncio
async def test_run_seed_orchestration(async_engine, tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    with open(base_dir / "manifest.yaml", "w") as f:
        f.write(
            """
squads:
  - name: "Core"
projects:
  - name: "Platform Core"
    prefix: "CORE"
channels: []
tasks:
  - title: "Init DB"
    project: "CORE"
    slug: "init_db"
"""
        )

    guild_id = 4455667788
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    res = await run_seed(
        seeds_dir=tmp_path,
        guild_id=guild_id,
        profile_name=None,
        no_discord=True,
        reset=True,
        session_factory=session_factory,
    )

    assert res.created_tasks_count == 1
    assert "CORE" in res.projects_by_prefix
