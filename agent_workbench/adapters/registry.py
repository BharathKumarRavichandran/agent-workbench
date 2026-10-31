"""Explicit built-in adapter registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..model import ArtifactKind, TargetId
from .base import ArtifactInspector, InstallTarget, MarketplaceRenderer
from .claude import ClaudeMarketplaceRenderer
from .components import OpaqueArtifactInspector, built_in_component_inspectors
from .cursor import CursorMarketplaceRenderer
from .lock import LockReportRenderer
from .skills import DestinationSpec, SkillDirectoryTarget


@dataclass(frozen=True)
class AdapterRegistry:
    renderers: tuple[MarketplaceRenderer, ...]
    install_targets: Mapping[TargetId, InstallTarget]
    artifact_inspectors: Mapping[ArtifactKind, ArtifactInspector] = field(default_factory=dict)


def build_registry() -> AdapterRegistry:
    home = str(Path.home())
    targets = (
        SkillDirectoryTarget(TargetId("cursor"), DestinationSpec(None, f"{home}/.cursor", ("skills",))),
        SkillDirectoryTarget(TargetId("agents"), DestinationSpec(None, f"{home}/.agents", ("skills",))),
        SkillDirectoryTarget(TargetId("claude"), DestinationSpec(None, f"{home}/.claude", ("skills",))),
        SkillDirectoryTarget(TargetId("codex"), DestinationSpec("CODEX_HOME", f"{home}/.codex", ("skills",))),
        SkillDirectoryTarget(TargetId("grok"), DestinationSpec(None, f"{home}/.grok", ("skills",))),
        SkillDirectoryTarget(TargetId("hermes"), DestinationSpec("HERMES_HOME", f"{home}/.hermes", ("skills",))),
    )
    inspectors = (
        OpaqueArtifactInspector(ArtifactKind("cursor-plugin")),
        OpaqueArtifactInspector(ArtifactKind("claude-plugin")),
        *built_in_component_inspectors(),
    )
    inspector_map = {inspector.kind: inspector for inspector in inspectors}
    return AdapterRegistry(
        artifact_inspectors=inspector_map,
        renderers=(CursorMarketplaceRenderer(), ClaudeMarketplaceRenderer(), LockReportRenderer(inspector_map)),
        install_targets={target.id: target for target in targets},
    )
