"""Command-line interface for the agent workbench."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import ComponentKind
from .artifacts import component_counts, inspect_catalog
from .installation import apply_plan, apply_uninstall, plan_install, plan_uninstall
from .rendering import render
from .sources import update_source
from .state import default_state_root
from .validation import validate
from .workspace import Workspace


def _print_operations(plan: object, as_json: bool) -> None:
    operations = getattr(plan, "operations")
    if as_json:
        print(json.dumps([operation.as_dict() for operation in operations], indent=2))
        return
    for operation in operations:
        source = f" -> {operation.source}" if operation.source else ""
        resource = operation.resource.kind if operation.source is not None else "unsupported"
        reason = f"{operation.diagnostic}: {operation.reason}" if operation.diagnostic else operation.reason
        print(f"{operation.kind.value:14} {operation.component.value}/{resource} "
              f"{operation.destination}{source}  ({reason})")


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace.load(Path(args.root), args.profile)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-workbench")
    parser.add_argument("--root", default=".", help="repository checkout")
    parser.add_argument("--profile", default="default")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validation = subcommands.add_parser("validate")
    validation.add_argument("--json", action="store_true")
    validation.add_argument("--no-generated-check", action="store_true")

    rendering = subcommands.add_parser("render")
    rendering.add_argument("--check", action="store_true")

    for name in ("plan", "install", "status"):
        command = subcommands.add_parser(name)
        command.add_argument("--target", required=True)
        command.add_argument("--destination", type=Path, help="skills directory override")
        command.add_argument("--component", action="append", choices=[kind.value for kind in ComponentKind])
        command.add_argument("--project-root", type=Path)
        command.add_argument("--state-dir", type=Path, default=default_state_root())
        command.add_argument("--adopt", action="store_true")
        command.add_argument("--json", action="store_true")

    uninstall = subcommands.add_parser("uninstall")
    uninstall.add_argument("--target", required=True)
    uninstall.add_argument("--destination", type=Path)
    uninstall.add_argument("--project-root", type=Path)
    uninstall.add_argument("--state-dir", type=Path, default=default_state_root())
    uninstall.add_argument("--json", action="store_true")

    update = subcommands.add_parser("update")
    update.add_argument("--source", required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    workspace = _workspace(args)

    if args.command == "validate":
        report = validate(workspace, check_generated=not args.no_generated_check)
        if args.json:
            print(json.dumps([issue.as_dict() for issue in report.issues], indent=2))
        elif report.issues:
            for issue in report.issues:
                print(f"{issue.severity.value}: {issue.code}: {issue.location}: {issue.message}")
        else:
            inspection = inspect_catalog(workspace.root, workspace.catalog, workspace.registry.artifact_inspectors)
            counts = component_counts(workspace.resolved.plugins, inspection.inventories)
            summary = ", ".join(f"{kind}={count}" for kind, count in counts.items()) or "no components"
            print(f"validated {len(workspace.resolved.plugins)} plugins: {summary}")
        return 0 if report.valid else 1

    if args.command == "render":
        stale = render(workspace, check=args.check)
        if args.check and stale:
            for path in stale:
                print(f"stale {path.relative_to(workspace.root)}")
            return 1
        for path in stale:
            print(f"wrote {path.relative_to(workspace.root)}")
        return 0

    if args.command in {"plan", "install", "status"}:
        components = (tuple(dict.fromkeys(ComponentKind(value) for value in args.component))
                      if args.component is not None else None)
        plan = plan_install(workspace, args.target, args.state_dir, args.destination,
                            args.adopt, components, args.project_root)
        _print_operations(plan, args.json)
        if args.command == "install":
            if plan.has_conflicts:
                return 1
            apply_plan(plan)
        return 1 if plan.has_conflicts else 0

    if args.command == "uninstall":
        plan = plan_uninstall(workspace, args.target, args.state_dir, args.destination, args.project_root)
        _print_operations(plan, args.json)
        if not plan.has_conflicts:
            apply_uninstall(plan)
        return 1 if plan.has_conflicts else 0

    if args.command == "update":
        result = update_source(workspace, args.source)
        print(f"{result.source}: {result.before} -> {result.after}")
        return 0

    raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
