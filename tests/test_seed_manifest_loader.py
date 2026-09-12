from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.domain.enums import PriorityLevel, TaskStatus
from src.services.seed.manifest_loader import (
    SeedManifest,
    load_seed_manifest,
)


def test_load_base_manifest(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    manifest_data = {
        "squads": [
            {"name": "Backend", "role_name": "Backend Squad"},
        ],
        "projects": [
            {
                "name": "Platform Core",
                "prefix": "CORE",
                "category": "Core Engineering",
                "description": "Core backend services",
                "squads": ["Backend"],
            }
        ],
        "channels": [
            {
                "name": "🎯-single-project",
                "type": "forum",
                "topic": "Core workspace",
                "projects": ["CORE"],
            }
        ],
        "tasks": [
            {
                "slug": "setup_db",
                "title": "Configure PostgreSQL pooling",
                "project": "CORE",
                "priority": "HIGH",
                "status": "COMPLETED",
                "body": "Connection pool configuration",
                "prerequisites": [],
            },
            {
                "slug": "auth_jwt",
                "title": "Implement JWT validation",
                "project": "CORE",
                "priority": "HIGH",
                "status": "IN_PROGRESS",
                "days_offset": 2,
                "body": "Deliver JWT auth",
                "prerequisites": ["setup_db"],
            },
        ],
    }
    with open(base_dir / "manifest.yaml", "w") as f:
        yaml.safe_dump(manifest_data, f)

    manifest = load_seed_manifest(tmp_path)

    assert isinstance(manifest, SeedManifest)
    assert len(manifest.squads) == 1
    assert manifest.squads[0].name == "Backend"
    assert manifest.squads[0].role_name == "Backend Squad"

    assert len(manifest.projects) == 1
    assert manifest.projects[0].name == "Platform Core"
    assert manifest.projects[0].prefix == "CORE"
    assert manifest.projects[0].squads == ["Backend"]

    assert len(manifest.channels) == 1
    assert manifest.channels[0].name == "🎯-single-project"
    assert manifest.channels[0].type == "forum"
    assert manifest.channels[0].projects == ["CORE"]

    assert len(manifest.tasks) == 2
    task1 = manifest.tasks[0]
    assert task1.slug == "setup_db"
    assert task1.priority == PriorityLevel.HIGH
    assert task1.status == TaskStatus.COMPLETED
    assert task1.prerequisites == []

    task2 = manifest.tasks[1]
    assert task2.slug == "auth_jwt"
    assert task2.status == TaskStatus.IN_PROGRESS
    assert task2.prerequisites == ["setup_db"]
    assert task2.days_offset == 2


def test_load_profile_overlay(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)

    base_manifest = {
        "squads": [{"name": "Core", "role_name": "Core Team"}],
        "projects": [{"name": "Base Project", "prefix": "BASE"}],
        "channels": [{"name": "🎯-base", "type": "forum", "projects": ["BASE"]}],
        "tasks": [{"title": "Base Task", "project": "BASE", "slug": "base_task"}],
    }
    with open(base_dir / "manifest.yaml", "w") as f:
        yaml.safe_dump(base_manifest, f)

    profile_manifest = {
        "projects": [{"name": "Tech Tree Lab", "prefix": "TREE"}],
        "channels": [{"name": "🌳-tech-tree", "type": "forum", "projects": ["TREE"]}],
        "tasks": [
            {"title": "Kernel", "project": "TREE", "slug": "kernel"},
            {"title": "Storage", "project": "TREE", "slug": "storage", "prerequisites": ["kernel"]},
        ],
    }
    with open(profiles_dir / "tech-tree.yaml", "w") as f:
        yaml.safe_dump(profile_manifest, f)

    # 1. Test loading with specific profile
    manifest = load_seed_manifest(tmp_path, profile_name="tech-tree")
    assert len(manifest.projects) == 2
    assert {p.prefix for p in manifest.projects} == {"BASE", "TREE"}
    assert len(manifest.channels) == 2
    assert len(manifest.tasks) == 3
    assert {t.slug for t in manifest.tasks} == {"base_task", "kernel", "storage"}

    # 2. Test profile 'all'
    manifest_all = load_seed_manifest(tmp_path, profile_name="all")
    assert len(manifest_all.projects) == 2


def test_validate_unknown_project_reference(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    manifest_data = {
        "projects": [{"name": "Core", "prefix": "CORE"}],
        "tasks": [{"title": "Invalid Task", "project": "NONEXISTENT", "slug": "t1"}],
    }
    with open(base_dir / "manifest.yaml", "w") as f:
        yaml.safe_dump(manifest_data, f)

    with pytest.raises(ValueError, match="references unknown project prefix 'NONEXISTENT'"):
        load_seed_manifest(tmp_path)


def test_validate_unknown_prerequisite_slug(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    manifest_data = {
        "projects": [{"name": "Core", "prefix": "CORE"}],
        "tasks": [
            {"title": "Task 1", "project": "CORE", "slug": "t1", "prerequisites": ["missing_slug"]},
        ],
    }
    with open(base_dir / "manifest.yaml", "w") as f:
        yaml.safe_dump(manifest_data, f)

    with pytest.raises(ValueError, match="unknown prerequisite slug 'missing_slug'"):
        load_seed_manifest(tmp_path)


def test_validate_cyclic_prerequisites(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    manifest_data = {
        "projects": [{"name": "Core", "prefix": "CORE"}],
        "tasks": [
            {"title": "Task A", "project": "CORE", "slug": "task_a", "prerequisites": ["task_b"]},
            {"title": "Task B", "project": "CORE", "slug": "task_b", "prerequisites": ["task_a"]},
        ],
    }
    with open(base_dir / "manifest.yaml", "w") as f:
        yaml.safe_dump(manifest_data, f)

    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        load_seed_manifest(tmp_path)


def test_real_seeds_manifest_validity():
    real_seeds_dir = Path(__file__).resolve().parent.parent / "seeds"
    if not real_seeds_dir.exists():
        pytest.skip("seeds directory does not exist")

    # Validate base
    base_manifest = load_seed_manifest(real_seeds_dir)
    assert len(base_manifest.squads) >= 1
    assert len(base_manifest.projects) >= 1
    assert len(base_manifest.tasks) >= 1

    # Validate that seeded projects have realistic example trees (prerequisites)
    tasks_with_prereqs = [t for t in base_manifest.tasks if t.prerequisites]
    assert len(tasks_with_prereqs) > 0, "Seeded projects should have DAG example trees"
    projects_with_trees = {t.project for t in tasks_with_prereqs}
    assert len(projects_with_trees) >= 5, "Multiple seeded projects must have example trees"

    # Validate loading with all profiles loads cleanly
    all_manifest = load_seed_manifest(real_seeds_dir, profile_name="all")
    assert len(all_manifest.projects) >= len(base_manifest.projects)
