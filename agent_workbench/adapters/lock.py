"""Reproducibility lock report renderer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..diagnostics import Issue
from ..inspection import discover_skills, load_manifest
from ..model import ArtifactKind, Catalog, ResolvedPlugin, ResolvedProfile, SourceKind
from .base import GeneratedFile


class LockReportRenderer:
    id = "catalog-lock"
    required_artifact = ArtifactKind("agent-skills")

    def validate(self, repository: Path, plugin: ResolvedPlugin) -> tuple[Issue, ...]:
        return ()

    def render(self, repository: Path, catalog: Catalog, profile: ResolvedProfile) -> tuple[GeneratedFile, ...]:
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
            skills_root = resolved.artifact_paths.get(ArtifactKind("agent-skills"))
            skills = discover_skills(skills_root) if skills_root is not None else ()
            plugins.append(
                {
                    "id": str(resolved.plugin.id),
                    "source": str(resolved.source.id),
                    "version": version,
                    "skills": len(skills),
                    "artifacts": {str(kind): path.relative_to(repository).as_posix() for kind, path in sorted(resolved.artifact_paths.items())},
                }
            )

        document = {
            "schemaVersion": 1,
            "marketplace": catalog.marketplace.id,
            "sources": sources,
            "plugins": plugins,
        }
        content = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
        return (GeneratedFile(repository / "catalog" / "lock.json", content, self.id),)
