#!/usr/bin/env python3
"""Compatibility wrapper for profile-based skill installation."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_workbench.installation import apply_plan, plan_install
from agent_workbench.model import ProfileId
from agent_workbench.profiles import resolve_profile
from agent_workbench.state import default_state_root
from agent_workbench.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["agents", "claude", "codex", "grok", "hermes"], required=True)
    parser.add_argument("--plugin", action="append", dest="plugins")
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--state-dir", type=Path, default=default_state_root())
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = Workspace.load(ROOT)
    if args.plugins:
        selected = tuple(sorted(set(args.plugins)))
        profile = replace(
            workspace.profile,
            id=ProfileId("compat-" + "-".join(selected)),
            include_plugins=selected,
            exclude_plugins=(),
        )
        workspace = replace(workspace, profile=profile, resolved=resolve_profile(ROOT, workspace.catalog, profile))
    plan = plan_install(workspace, args.target, args.state_dir, args.dest, adopt=args.adopt)
    for operation in plan.operations:
        source = f" -> {operation.source}" if operation.source else ""
        print(f"{operation.kind.value:14} {operation.destination}{source}")
    if plan.has_conflicts:
        return 1
    if not args.dry_run:
        apply_plan(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
