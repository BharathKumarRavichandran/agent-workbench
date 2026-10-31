"""Catalog and profile resolution."""

from __future__ import annotations

from pathlib import Path

from .model import ArtifactKind, Catalog, PluginId, Profile, ResolvedPlugin, ResolvedProfile, SourceId


def resolve_profile(repository: Path, catalog: Catalog, profile: Profile) -> ResolvedProfile:
    sources = catalog.source_map()
    if len(sources) != len(catalog.sources):
        raise ValueError("source ids must be unique")
    plugin_ids = [plugin.id for plugin in catalog.plugins]
    if len(set(plugin_ids)) != len(plugin_ids):
        raise ValueError("plugin ids must be unique")

    known = set(plugin_ids)
    excluded = set(profile.exclude_plugins)
    unknown_excluded = excluded - known
    if unknown_excluded:
        raise ValueError(f"profile excludes unknown plugins: {', '.join(sorted(unknown_excluded))}")

    if profile.include_plugins == ("*",):
        selected = set(plugin_ids)
    else:
        selected = {PluginId(value) for value in profile.include_plugins}
        unknown = selected - known
        if unknown:
            raise ValueError(f"profile includes unknown plugins: {', '.join(sorted(unknown))}")
    selected -= excluded

    resolved: list[ResolvedPlugin] = []
    for plugin in catalog.plugins:
        if plugin.id not in selected:
            continue
        source = sources.get(SourceId(plugin.source))
        if source is None:
            raise ValueError(f"plugin {plugin.id} references unknown source {plugin.source}")
        source_root = source.path.resolve(repository)
        if not source_root.exists():
            raise ValueError(f"source path does not exist for {source.id}: {source.path.value}")
        artifact_paths = {
            ArtifactKind(kind): artifact.path.resolve(source_root)
            for kind, artifact in plugin.artifacts.items()
        }
        resolved.append(ResolvedPlugin(plugin, source, artifact_paths))
    return ResolvedProfile(profile, tuple(resolved))
