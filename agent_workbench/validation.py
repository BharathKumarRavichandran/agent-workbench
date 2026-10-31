"""Layered workspace validation."""

from __future__ import annotations

from pathlib import Path

from .diagnostics import Issue, Severity, ValidationReport
from .inspection import discover_skills
from .model import ArtifactKind, ResolvedPlugin
from .rendering import render
from .workspace import Workspace


def validate(workspace: Workspace, check_generated: bool = True) -> ValidationReport:
    issues: list[Issue] = []
    known_artifacts = {renderer.required_artifact for renderer in workspace.registry.renderers}
    known_artifacts.update(target.required_artifact for target in workspace.registry.install_targets.values())
    known_targets = set(workspace.registry.install_targets)
    profile_targets = [target.id for target in workspace.profile.targets]
    if len(profile_targets) != len(set(profile_targets)):
        issues.append(Issue("PROFILE_TARGET_DUPLICATE", Severity.ERROR, f"profile:{workspace.profile.id}", "profile target ids must be unique"))
    for target in profile_targets:
        if target not in known_targets:
            issues.append(Issue("PROFILE_TARGET_UNKNOWN", Severity.ERROR, f"profile:{workspace.profile.id}", f"unknown install target: {target}"))

    sources = workspace.catalog.source_map()
    for catalog_source in workspace.catalog.sources:
        source_path = catalog_source.path.resolve(workspace.root)
        if not source_path.exists():
            issues.append(Issue("SOURCE_PATH_MISSING", Severity.ERROR, str(source_path), "source path does not exist"))

    catalog_plugins: list[ResolvedPlugin] = []
    for plugin in workspace.catalog.plugins:
        plugin_source = sources.get(plugin.source)
        if plugin_source is None:
            issues.append(Issue("PLUGIN_SOURCE_UNKNOWN", Severity.ERROR, f"plugin:{plugin.id}", f"unknown source: {plugin.source}"))
            continue
        source_root = plugin_source.path.resolve(workspace.root)
        catalog_plugins.append(
            ResolvedPlugin(
                plugin,
                plugin_source,
                {kind: artifact.path.resolve(source_root) for kind, artifact in plugin.artifacts.items()},
            )
        )

    for resolved in catalog_plugins:
        for kind, path in resolved.artifact_paths.items():
            if kind not in known_artifacts:
                issues.append(Issue("ARTIFACT_KIND_UNKNOWN", Severity.ERROR, f"plugin:{resolved.plugin.id}", f"unknown artifact kind: {kind}"))
            if not path.exists():
                issues.append(Issue("ARTIFACT_PATH_MISSING", Severity.ERROR, str(path), "artifact path does not exist"))
        if resolved.plugin.provenance and resolved.plugin.provenance.notice:
            notice = resolved.plugin.provenance.notice.resolve(workspace.root)
            if not notice.is_file():
                issues.append(Issue("PROVENANCE_NOTICE_MISSING", Severity.ERROR, str(notice), "provenance notice does not exist"))

    for renderer in workspace.registry.renderers:
        for resolved_plugin in catalog_plugins:
            issues.extend(renderer.validate(workspace.root, resolved_plugin))

    names: dict[str, Path] = {}
    for resolved in catalog_plugins:
        root = resolved.artifact_paths.get(ArtifactKind("agent-skills"))
        if root is None:
            continue
        try:
            skills = discover_skills(root)
        except (OSError, ValueError) as error:
            issues.append(Issue("SKILLS_INVALID", Severity.ERROR, str(root), str(error)))
            continue
        for skill in skills:
            previous = names.get(skill.name)
            if previous:
                issues.append(Issue("SKILL_NAME_DUPLICATE", Severity.ERROR, str(skill.root), f"skill {skill.name!r} also exists at {previous}"))
            else:
                names[skill.name] = skill.root

    if check_generated:
        try:
            canonical = (
                workspace
                if str(workspace.profile.id) == "default"
                else Workspace.load(workspace.root, registry=workspace.registry)
            )
            stale = render(canonical, check=True)
        except (OSError, ValueError, KeyError) as error:
            issues.append(Issue("RENDER_FAILED", Severity.ERROR, str(workspace.root), str(error)))
        else:
            for path in stale:
                issues.append(Issue("GENERATED_FILE_STALE", Severity.ERROR, str(path), "generated file differs from catalog; run render"))

    return ValidationReport(tuple(issues))
