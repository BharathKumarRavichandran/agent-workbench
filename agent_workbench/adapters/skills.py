"""Parameterized filesystem target for standard Agent Skills."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..model import ArtifactKind, TargetId, ComponentKind, Representation


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
    capabilities: Mapping[ComponentKind, Representation] = field(
        default_factory=lambda: {ComponentKind.SKILLS: Representation.SKILL_DIRECTORY}
    )

    def default_destination(self) -> Path:
        return self.destination.resolve()

    def destination_roots(self, skills: Path, project_root: Path | None) -> dict[ComponentKind, Path]:
        base = project_root / f".{self.id}" if project_root else self.default_destination().parent
        roots = {component: base / component.value
                 for component in self.capabilities
                 if component is not ComponentKind.RULES or project_root is not None}
        roots[ComponentKind.SKILLS] = skills
        return roots
