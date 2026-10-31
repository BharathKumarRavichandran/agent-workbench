"""Explicit built-in adapter registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..model import TargetId
from .base import InstallTarget, MarketplaceRenderer
from .claude import ClaudeMarketplaceRenderer
from .cursor import CursorMarketplaceRenderer
from .lock import LockReportRenderer
from .skills import DestinationSpec, SkillDirectoryTarget


@dataclass(frozen=True)
class AdapterRegistry:
    renderers: tuple[MarketplaceRenderer, ...]
    install_targets: Mapping[TargetId, InstallTarget]


def build_registry() -> AdapterRegistry:
    home = str(Path.home())
    targets = (
        SkillDirectoryTarget(TargetId("agents"), DestinationSpec(None, f"{home}/.agents", ("skills",))),
        SkillDirectoryTarget(TargetId("claude"), DestinationSpec(None, f"{home}/.claude", ("skills",))),
        SkillDirectoryTarget(TargetId("codex"), DestinationSpec("CODEX_HOME", f"{home}/.codex", ("skills",))),
        SkillDirectoryTarget(TargetId("grok"), DestinationSpec(None, f"{home}/.grok", ("skills",))),
        SkillDirectoryTarget(TargetId("hermes"), DestinationSpec("HERMES_HOME", f"{home}/.hermes", ("skills",))),
    )
    return AdapterRegistry(
        renderers=(CursorMarketplaceRenderer(), ClaudeMarketplaceRenderer(), LockReportRenderer()),
        install_targets={target.id: target for target in targets},
    )
