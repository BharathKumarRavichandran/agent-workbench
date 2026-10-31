"""Parameterized filesystem target for standard Agent Skills."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..inspection import discover_skills
from ..model import ArtifactKind, ResolvedProfile, TargetId
from .base import DesiredEntry


@dataclass(frozen=True)
class DestinationSpec:
    environment: str | None
    fallback: str
    suffix: tuple[str, ...] = ()

    def resolve(self) -> Path:
        base = os.environ.get(self.environment) if self.environment else None
        path = Path(base).expanduser() if base else Path(self.fallback).expanduser()
        return path.joinpath(*self.suffix).resolve()


@dataclass(frozen=True)
class SkillDirectoryTarget:
    id: TargetId
    destination: DestinationSpec
    required_artifact: ArtifactKind = ArtifactKind("agent-skills")

    def default_destination(self) -> Path:
        return self.destination.resolve()

    def desired_entries(self, profile: ResolvedProfile, destination: Path) -> tuple[DesiredEntry, ...]:
        entries: list[DesiredEntry] = []
        names: dict[str, str] = {}
        for resolved in profile.plugins:
            skills_root = resolved.artifact_paths.get(self.required_artifact)
            if skills_root is None:
                continue
            for skill in discover_skills(skills_root):
                previous = names.get(skill.name)
                if previous:
                    raise ValueError(f"duplicate skill name {skill.name!r} in {previous} and {resolved.plugin.id}")
                names[skill.name] = str(resolved.plugin.id)
                entries.append(
                    DesiredEntry(
                        key=f"skill:{skill.name}",
                        plugin_id=str(resolved.plugin.id),
                        source=skill.root,
                        destination=destination / skill.name,
                    )
                )
        return tuple(entries)
