"""Typed domain model for the agent extension workbench."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Mapping, NewType

PluginId = NewType("PluginId", str)
SourceId = NewType("SourceId", str)
ArtifactKind = NewType("ArtifactKind", str)
ProfileId = NewType("ProfileId", str)
TargetId = NewType("TargetId", str)


@dataclass(frozen=True)
class RepoPath:
    value: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.value)
        if not self.value or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"repository path must be relative and contained: {self.value!r}")

    def resolve(self, root: Path) -> Path:
        candidate = (root / self.value).resolve()
        repository = root.resolve()
        if candidate != repository and repository not in candidate.parents:
            raise ValueError(f"repository path escapes checkout: {self.value!r}")
        return candidate


class SourceKind(str, Enum):
    LOCAL = "local"
    CURATED_COPY = "curated-copy"
    GIT_SUBMODULE = "git-submodule"


@dataclass(frozen=True)
class Marketplace:
    id: str
    name: str
    owner_name: str
    description: str


@dataclass(frozen=True)
class Source:
    id: SourceId
    kind: SourceKind
    path: RepoPath
    upstream: str | None = None
    branch: str | None = None


@dataclass(frozen=True)
class Artifact:
    kind: ArtifactKind
    path: RepoPath


@dataclass(frozen=True)
class Provenance:
    upstream: str
    notice: RepoPath | None = None


@dataclass(frozen=True)
class Plugin:
    id: PluginId
    source: SourceId
    artifacts: Mapping[ArtifactKind, Artifact]
    provenance: Provenance | None = None


@dataclass(frozen=True)
class Catalog:
    schema_version: int
    marketplace: Marketplace
    sources: tuple[Source, ...]
    plugins: tuple[Plugin, ...]

    def source_map(self) -> dict[SourceId, Source]:
        return {source.id: source for source in self.sources}


@dataclass(frozen=True)
class ProfileTarget:
    id: TargetId
    destination: str | None = None


@dataclass(frozen=True)
class Profile:
    schema_version: int
    id: ProfileId
    include_plugins: tuple[str, ...]
    exclude_plugins: tuple[PluginId, ...]
    targets: tuple[ProfileTarget, ...]


@dataclass(frozen=True)
class ResolvedPlugin:
    plugin: Plugin
    source: Source
    artifact_paths: Mapping[ArtifactKind, Path]


@dataclass(frozen=True)
class ResolvedProfile:
    profile: Profile
    plugins: tuple[ResolvedPlugin, ...]


@dataclass(frozen=True)
class ComponentInfo:
    name: str
    path: str


@dataclass(frozen=True)
class ArtifactInventory:
    plugin_id: PluginId
    kind: ArtifactKind
    root: Path
    components: tuple[ComponentInfo, ...]
