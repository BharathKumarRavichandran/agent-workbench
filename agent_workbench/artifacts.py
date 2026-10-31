"""Catalog-wide artifact inspection and component inventory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .adapters.base import ArtifactInspector
from .diagnostics import Issue, Severity
from .model import ArtifactInventory, ArtifactKind, Catalog, PluginId, ResolvedPlugin


@dataclass(frozen=True)
class CatalogInspection:
    plugins: tuple[ResolvedPlugin, ...]
    inventories: Mapping[tuple[PluginId, ArtifactKind], ArtifactInventory]
    issues: tuple[Issue, ...]


def inspect_catalog(
    repository: Path,
    catalog: Catalog,
    inspectors: Mapping[ArtifactKind, ArtifactInspector],
) -> CatalogInspection:
    issues: list[Issue] = []
    inventories: dict[tuple[PluginId, ArtifactKind], ArtifactInventory] = {}
    resolved_plugins: list[ResolvedPlugin] = []
    sources = catalog.source_map()

    for source in catalog.sources:
        source_path = source.path.resolve(repository)
        if not source_path.exists():
            issues.append(Issue("SOURCE_PATH_MISSING", Severity.ERROR, str(source_path), "source path does not exist"))

    for plugin in catalog.plugins:
        source = sources.get(plugin.source)
        if source is None:
            issues.append(Issue("PLUGIN_SOURCE_UNKNOWN", Severity.ERROR, f"plugin:{plugin.id}", f"unknown source: {plugin.source}"))
            continue
        source_root = source.path.resolve(repository)
        resolved = ResolvedPlugin(
            plugin,
            source,
            {kind: artifact.path.resolve(source_root) for kind, artifact in plugin.artifacts.items()},
        )
        resolved_plugins.append(resolved)
        for kind, path in resolved.artifact_paths.items():
            inspector = inspectors.get(kind)
            if inspector is None:
                issues.append(Issue("ARTIFACT_KIND_UNKNOWN", Severity.ERROR, f"plugin:{plugin.id}", f"unknown artifact kind: {kind}"))
                continue
            if not path.exists():
                issues.append(Issue("ARTIFACT_PATH_MISSING", Severity.ERROR, str(path), "artifact path does not exist"))
                continue
            try:
                inventories[(plugin.id, kind)] = inspector.inspect(resolved)
            except (OSError, ValueError) as error:
                issues.append(Issue("ARTIFACT_INVALID", Severity.ERROR, str(path), f"{kind}: {error}"))

    return CatalogInspection(tuple(resolved_plugins), inventories, tuple(issues))


def component_counts(
    plugins: tuple[ResolvedPlugin, ...],
    inventories: Mapping[tuple[PluginId, ArtifactKind], ArtifactInventory],
) -> dict[ArtifactKind, int]:
    selected = {plugin.plugin.id for plugin in plugins}
    counts: dict[ArtifactKind, int] = {}
    for (plugin_id, kind), inventory in inventories.items():
        if plugin_id in selected and inventory.components:
            counts[kind] = counts.get(kind, 0) + len(inventory.components)
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))
