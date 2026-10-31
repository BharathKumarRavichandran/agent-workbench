"""Pure projections of existing formats; unsupported cells have no conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..model import ComponentExport, ComponentKind, Representation, TargetId


CAPABILITIES: Mapping[TargetId, Mapping[ComponentKind, Representation]] = {
    TargetId(target): {
        ComponentKind.SKILLS: Representation.SKILL_DIRECTORY,
        **({ComponentKind.AGENTS: Representation.MARKDOWN_AGENT,
            ComponentKind.RULES: Representation.CURSOR_RULE} if target == "cursor" else {}),
        **({ComponentKind.AGENTS: Representation.MARKDOWN_AGENT,
            ComponentKind.COMMANDS: Representation.LEGACY_COMMAND} if target == "claude" else {}),
    }
    for target in ("agents", "cursor", "claude", "codex", "hermes", "grok")
}


@dataclass(frozen=True)
class ProjectedPath:
    key: str
    export: ComponentExport
    destination: Path


@dataclass(frozen=True)
class ProjectionConflict:
    component: ComponentKind
    code: str
    reason: str


def project_paths(
    target: TargetId,
    components: tuple[ComponentKind, ...],
    exports: tuple[ComponentExport, ...],
    roots: Mapping[ComponentKind, Path],
) -> tuple[tuple[ProjectedPath, ...], tuple[ProjectionConflict, ...]]:
    supported = CAPABILITIES.get(target, {})
    conflicts = tuple(ProjectionConflict(component, "COMPONENT_UNSUPPORTED",
        f"{target} does not support current {component.value} format")
        for component in components if component not in supported)
    conflicts += tuple(ProjectionConflict(component, "PROJECT_ROOT_REQUIRED",
        f"{target} {component.value} requires --project-root")
        for component in components if component in supported and component not in roots)
    paths: list[ProjectedPath] = []
    prefixes = {ComponentKind.SKILLS: "skill", ComponentKind.AGENTS: "agent",
                ComponentKind.RULES: "rule", ComponentKind.COMMANDS: "command"}
    for export in exports:
        if export.component not in components or export.component not in supported or export.component not in roots:
            continue
        if export.representation is not supported[export.component]:
            conflicts += (ProjectionConflict(export.component, "REPRESENTATION_UNSUPPORTED",
                          f"{target} does not support {export.representation.value}"),)
            continue
        name = export.name if export.component is ComponentKind.SKILLS else export.source.name
        paths.append(ProjectedPath(f"{prefixes[export.component]}:{name}", export,
                                   roots[export.component] / name))
    return tuple(paths), conflicts
