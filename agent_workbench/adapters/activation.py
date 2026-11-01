"""Target-owned exact activation formats and authorized document layout."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..model import ComponentKind, Representation


@dataclass(frozen=True)
class ActivationPolicy:
    hooks_file: str
    mcp_file: str
    plugin_variable: str
    hook_representation: Representation
    hook_version: bool
    lock_name: str = '.agent-workbench.lock'
    journal_name: str = '.agent-workbench-transaction.json'
    ledger_name: str = '.agent-workbench-owners.json'

    def document_path(self, roots: Mapping[ComponentKind, Path], component: ComponentKind) -> Path:
        if component is ComponentKind.HOOKS:
            return roots[ComponentKind.HOOKS].parent / self.hooks_file
        return roots[ComponentKind.MCP] / self.mcp_file


ACTIVATION_POLICIES = {
    'cursor': ActivationPolicy('hooks.json', 'mcp.json', 'CURSOR_PLUGIN_ROOT', Representation.HOOK_BUNDLE, True),
    'claude': ActivationPolicy('settings.json', '.mcp.json', 'CLAUDE_PLUGIN_ROOT', Representation.CLAUDE_HOOK_BUNDLE, False),
}
