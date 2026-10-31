"""Adapter contracts and shared result values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..diagnostics import Issue
from ..model import ArtifactInventory, ArtifactKind, Catalog, ResolvedPlugin, ResolvedProfile, TargetId, ComponentKind, DirectorySymlink, PathResource


@dataclass(frozen=True)
class GeneratedFile:
    path: Path
    content: bytes
    producer: str


@dataclass(frozen=True)
class DesiredEntry:
    key: str
    plugin_id: str
    source: Path
    destination: Path
    component: ComponentKind = ComponentKind.SKILLS
    resource: PathResource = DirectorySymlink()


class ArtifactInspector(Protocol):
    @property
    def kind(self) -> ArtifactKind: ...

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory: ...


class MarketplaceRenderer(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def required_artifact(self) -> ArtifactKind: ...

    def validate(self, repository: Path, plugin: ResolvedPlugin) -> tuple[Issue, ...]: ...

    def render(self, repository: Path, catalog: Catalog, profile: ResolvedProfile) -> tuple[GeneratedFile, ...]: ...


class InstallTarget(Protocol):
    @property
    def id(self) -> TargetId: ...

    @property
    def required_artifact(self) -> ArtifactKind: ...

    def default_destination(self) -> Path: ...

    def destination_roots(self, skills: Path, project_root: Path | None) -> dict[ComponentKind, Path]: ...
