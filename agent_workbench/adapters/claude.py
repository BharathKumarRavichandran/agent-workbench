"""Claude-compatible marketplace adapter."""

from __future__ import annotations

import json
from pathlib import Path

from ..diagnostics import Issue, Severity
from ..inspection import load_manifest
from ..model import ArtifactKind, Catalog, ResolvedPlugin, ResolvedProfile
from .base import GeneratedFile


class ClaudeMarketplaceRenderer:
    id = "claude-marketplace"
    required_artifact = ArtifactKind("claude-plugin")

    def validate(self, repository: Path, plugin: ResolvedPlugin) -> tuple[Issue, ...]:
        package = plugin.artifact_paths.get(self.required_artifact)
        if package is None:
            return ()
        path = package / ".claude-plugin" / "plugin.json"
        if not path.is_file():
            return (Issue("CLAUDE_MANIFEST_MISSING", Severity.ERROR, str(path), "Claude plugin manifest is missing"),)
        try:
            manifest = load_manifest(package, "claude")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return (Issue("CLAUDE_MANIFEST_INVALID", Severity.ERROR, str(path), str(error)),)
        if manifest.get("name") != plugin.plugin.id:
            return (Issue("CLAUDE_NAME_MISMATCH", Severity.ERROR, str(path), f"manifest name must be {plugin.plugin.id}"),)
        return ()

    def render(self, repository: Path, catalog: Catalog, profile: ResolvedProfile) -> tuple[GeneratedFile, ...]:
        plugins = []
        for resolved in profile.plugins:
            package = resolved.artifact_paths.get(self.required_artifact)
            if package is None:
                continue
            manifest = load_manifest(package, "claude")
            source = "./" + package.relative_to(repository.resolve()).as_posix()
            plugins.append({"name": str(resolved.plugin.id), "source": source, "description": manifest["description"]})
        document = {
            "name": catalog.marketplace.name,
            "owner": {"name": catalog.marketplace.owner_name},
            "plugins": plugins,
        }
        content = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
        return (GeneratedFile(repository / ".claude-plugin" / "marketplace.json", content, self.id),)
