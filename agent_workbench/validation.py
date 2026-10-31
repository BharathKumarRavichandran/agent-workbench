"""Layered workspace validation."""

from __future__ import annotations

from pathlib import Path

from .diagnostics import Issue, Severity, ValidationReport
from .artifacts import inspect_catalog
from .model import ArtifactKind
from .rendering import render
from .workspace import Workspace


def validate(workspace: Workspace, check_generated: bool = True) -> ValidationReport:
    issues: list[Issue] = []
    known_targets = set(workspace.registry.install_targets)
    profile_targets = [target.id for target in workspace.profile.targets]
    if len(profile_targets) != len(set(profile_targets)):
        issues.append(Issue("PROFILE_TARGET_DUPLICATE", Severity.ERROR, f"profile:{workspace.profile.id}", "profile target ids must be unique"))
    for target in profile_targets:
        if target not in known_targets:
            issues.append(Issue("PROFILE_TARGET_UNKNOWN", Severity.ERROR, f"profile:{workspace.profile.id}", f"unknown install target: {target}"))

    inspection = inspect_catalog(workspace.root, workspace.catalog, workspace.registry.artifact_inspectors)
    issues.extend(inspection.issues)
    for resolved in inspection.plugins:
        if resolved.plugin.provenance and resolved.plugin.provenance.notice:
            notice = resolved.plugin.provenance.notice.resolve(workspace.root)
            if not notice.is_file():
                issues.append(Issue("PROVENANCE_NOTICE_MISSING", Severity.ERROR, str(notice), "provenance notice does not exist"))

    for renderer in workspace.registry.renderers:
        for resolved_plugin in inspection.plugins:
            issues.extend(renderer.validate(workspace.root, resolved_plugin))

    names: dict[str, Path] = {}
    for resolved in inspection.plugins:
        inventory = inspection.inventories.get((resolved.plugin.id, ArtifactKind("agent-skills")))
        if inventory is None:
            continue
        for skill in inventory.components:
            path = inventory.root / skill.path
            previous = names.get(skill.name)
            if previous:
                issues.append(Issue("SKILL_NAME_DUPLICATE", Severity.ERROR, str(path), f"skill {skill.name!r} also exists at {previous}"))
            else:
                names[skill.name] = path

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
