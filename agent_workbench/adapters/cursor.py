"""Cursor marketplace adapter."""

from __future__ import annotations

import json
from pathlib import Path

from ..diagnostics import Issue, Severity
from ..inspection import load_manifest
from ..model import ArtifactKind, Catalog, ResolvedPlugin, ResolvedProfile
from .base import GeneratedFile


class CursorMarketplaceRenderer:
    id = "cursor-marketplace"
    required_artifact = ArtifactKind("cursor-plugin")

    def validate(self, repository: Path, plugin: ResolvedPlugin) -> tuple[Issue, ...]:
        package = plugin.artifact_paths.get(self.required_artifact)
        if package is None:
            return ()
        path = package / ".cursor-plugin" / "plugin.json"
        if not path.is_file():
            return (Issue("CURSOR_MANIFEST_MISSING", Severity.ERROR, str(path), "Cursor plugin manifest is missing"),)
        try:
            manifest = load_manifest(package, "cursor")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return (Issue("CURSOR_MANIFEST_INVALID", Severity.ERROR, str(path), str(error)),)
        if manifest.get("name") != plugin.plugin.id:
            return (Issue("CURSOR_NAME_MISMATCH", Severity.ERROR, str(path), f"manifest name must be {plugin.plugin.id}"),)
        return ()

    def render(self, repository: Path, catalog: Catalog, profile: ResolvedProfile) -> tuple[GeneratedFile, ...]:
        plugins = []
        for resolved in profile.plugins:
            package = resolved.artifact_paths.get(self.required_artifact)
            if package is None:
                continue
            manifest = load_manifest(package, "cursor")
            source = package.relative_to(repository.resolve()).as_posix()
            plugins.append({"name": str(resolved.plugin.id), "source": source, "description": manifest["description"]})
        document = {
            "name": catalog.marketplace.name,
            "owner": {"name": catalog.marketplace.owner_name},
            "metadata": {"description": catalog.marketplace.description},
            "plugins": plugins,
        }
        content = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
        return (GeneratedFile(repository / ".cursor-plugin" / "marketplace.json", content, self.id),)
