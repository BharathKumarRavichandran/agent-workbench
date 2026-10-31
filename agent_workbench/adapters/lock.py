"""Reproducibility lock report renderer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Mapping

from ..artifacts import inspect_catalog
from ..diagnostics import Issue
from ..inspection import load_manifest
from ..model import ArtifactKind, Catalog, ResolvedPlugin, ResolvedProfile, SourceKind
from .base import ArtifactInspector, GeneratedFile


class LockReportRenderer:
    id = "catalog-lock"
    required_artifact = ArtifactKind("agent-skills")

    def __init__(self, inspectors: Mapping[ArtifactKind, ArtifactInspector]) -> None:
        self.inspectors = inspectors

    def validate(self, repository: Path, plugin: ResolvedPlugin) -> tuple[Issue, ...]:
        return ()

    def render(self, repository: Path, catalog: Catalog, profile: ResolvedProfile) -> tuple[GeneratedFile, ...]:
        inspection = inspect_catalog(repository, catalog, self.inspectors)
        if inspection.issues:
            raise ValueError("catalog artifact inspection failed")
        sources = []
        for source in catalog.sources:
            revision = None
            if source.kind is SourceKind.GIT_SUBMODULE:
                revision = subprocess.check_output(
                    ["git", "-C", str(source.path.resolve(repository)), "rev-parse", "HEAD"],
                    text=True,
                ).strip()
            sources.append(
                {
                    "id": str(source.id),
                    "kind": source.kind.value,
                    "path": source.path.value,
                    "upstream": source.upstream,
                    "branch": source.branch,
                    "revision": revision,
                }
            )

        plugins = []
        for resolved in profile.plugins:
            version = None
            for agent, kind in (("cursor", ArtifactKind("cursor-plugin")), ("claude", ArtifactKind("claude-plugin"))):
                package = resolved.artifact_paths.get(kind)
                if package is not None:
                    version = load_manifest(package, agent).get("version")
                    if version:
                        break
            components = {
                str(kind): len(inventory.components)
                for (plugin_id, kind), inventory in inspection.inventories.items()
                if plugin_id == resolved.plugin.id and inventory.components
            }
            plugins.append(
                {
                    "id": str(resolved.plugin.id),
                    "source": str(resolved.source.id),
                    "version": version,
                    "skills": components.get("agent-skills", 0),
                    "components": dict(sorted(components.items())),
                    "artifacts": {str(kind): path.relative_to(repository).as_posix() for kind, path in sorted(resolved.artifact_paths.items())},
                }
            )

        document = {
            "schemaVersion": 2,
            "marketplace": catalog.marketplace.id,
            "sources": sources,
            "plugins": plugins,
        }
        content = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
        return (GeneratedFile(repository / "catalog" / "lock.json", content, self.id),)
