from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from src.domain.enums import PriorityLevel, TaskStatus


class SquadSeedSpec(BaseModel):
    name: str
    role_name: str | None = None


class ProjectSeedSpec(BaseModel):
    name: str
    prefix: str
    category: str | None = None
    description: str | None = None
    squads: list[str] = Field(default_factory=list)
    archived: bool = False


class ChannelSeedSpec(BaseModel):
    name: str
    type: str = "forum"  # "forum" or "text"
    topic: str | None = None
    projects: list[str] = Field(default_factory=list)


class TaskSeedSpec(BaseModel):
    slug: str | None = None
    title: str
    project: str
    priority: PriorityLevel = PriorityLevel.NORMAL
    status: TaskStatus = TaskStatus.NOT_STARTED
    days_offset: int | None = None
    body: str | None = None
    prerequisites: list[str] = Field(default_factory=list)
    assignee: str | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, val: Any) -> Any:
        if isinstance(val, str):
            v = val.strip().lower()
            if v in ("low", "normal", "high"):
                return PriorityLevel(v)
            if v == "critical":
                return PriorityLevel.HIGH
        return val

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, val: Any) -> Any:
        if isinstance(val, str):
            v = val.strip().lower().replace("_", "").replace("-", "")
            mapping = {
                "notstarted": TaskStatus.NOT_STARTED,
                "inprogress": TaskStatus.IN_PROGRESS,
                "completed": TaskStatus.COMPLETED,
            }
            if v in mapping:
                return mapping[v]
        return val


class SeedManifest(BaseModel):
    squads: list[SquadSeedSpec] = Field(default_factory=list)
    projects: list[ProjectSeedSpec] = Field(default_factory=list)
    channels: list[ChannelSeedSpec] = Field(default_factory=list)
    tasks: list[TaskSeedSpec] = Field(default_factory=list)

    @field_validator("tasks")
    @classmethod
    def _validate_unique_slugs(cls, tasks: list[TaskSeedSpec]) -> list[TaskSeedSpec]:
        seen_slugs: set[str] = set()
        for t in tasks:
            if t.slug:
                if t.slug in seen_slugs:
                    raise ValueError(f"Duplicate task slug detected: '{t.slug}'")
                seen_slugs.add(t.slug)
        return tasks

    def validate_integrity(self) -> None:
        valid_prefixes = {p.prefix.upper() for p in self.projects}
        for t in self.tasks:
            if valid_prefixes and t.project.upper() not in valid_prefixes:
                raise ValueError(f"Task '{t.title}' references unknown project prefix '{t.project}'")

        slug_map = {t.slug: t for t in self.tasks if t.slug}
        for t in self.tasks:
            for prereq in t.prerequisites:
                if prereq not in slug_map:
                    raise ValueError(f"Task '{t.title}' references unknown prerequisite slug '{prereq}'")

        # Cycle detection using DFS
        visited: dict[str, int] = {}  # 0 = unvisited, 1 = visiting, 2 = visited

        def visit(node: str, path: list[str]) -> None:
            visited[node] = 1
            task = slug_map.get(node)
            if task:
                for neighbor in task.prerequisites:
                    state = visited.get(neighbor, 0)
                    if state == 1:
                        cycle_path = " -> ".join([*path, neighbor])
                        raise ValueError(f"Cyclic dependency detected in tasks: {cycle_path}")
                    if state == 0:
                        visit(neighbor, [*path, neighbor])
            visited[node] = 2

        for slug in slug_map:
            if visited.get(slug, 0) == 0:
                visit(slug, [slug])


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        content = yaml.safe_load(f) or {}
    return content


def load_seed_manifest(root_dir: Path | str, profile_name: str | None = None) -> SeedManifest:
    root = Path(root_dir)
    base_dir = root / "base"
    base_file = base_dir / "manifest.yaml"

    data: dict[str, Any] = {
        "squads": [],
        "projects": [],
        "channels": [],
        "tasks": [],
    }

    def _merge_data(source: dict[str, Any]) -> None:
        for key in ("squads", "projects", "channels", "tasks"):
            if key in source and isinstance(source[key], list):
                data[key].extend(source[key])

    if base_file.exists():
        base_data = _load_yaml_file(base_file)
        _merge_data(base_data)

    profiles_dir = root / "profiles"
    if profile_name and profiles_dir.exists():
        normalized_profile = profile_name.strip().lower()
        if normalized_profile == "all":
            profile_files = sorted(list(profiles_dir.glob("*.yaml")) + list(profiles_dir.glob("*.yml")))
            for pfile in profile_files:
                _merge_data(_load_yaml_file(pfile))
        else:
            profile_file = profiles_dir / f"{normalized_profile}.yaml"
            if not profile_file.exists():
                profile_file = profiles_dir / f"{normalized_profile}.yml"
            if not profile_file.exists():
                raise FileNotFoundError(f"Profile '{profile_name}' not found in {profiles_dir}")
            _merge_data(_load_yaml_file(profile_file))

    manifest = SeedManifest.model_validate(data)
    manifest.validate_integrity()
    return manifest
