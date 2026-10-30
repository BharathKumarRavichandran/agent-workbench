#!/usr/bin/env python3
"""Link catalog skills into an agent's user-level skill directory."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "catalog" / "plugins.json").read_text(encoding="utf-8"))


def default_destination(target: str) -> Path:
    home = Path.home()
    destinations = {
        "agents": home / ".agents" / "skills",
        "claude": home / ".claude" / "skills",
        "codex": Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills",
        "grok": home / ".grok" / "skills",
        "hermes": Path(os.environ.get("HERMES_HOME", home / ".hermes")) / "skills",
    }
    return destinations[target]


def skill_directories(plugin: dict) -> list[Path]:
    root = ROOT / plugin["packages"]["skills"]
    if not root.is_dir():
        raise FileNotFoundError(f"skills directory is missing: {root}")
    return sorted(path for path in root.iterdir() if (path / "SKILL.md").is_file())


def link_skill(source: Path, destination: Path, dry_run: bool) -> None:
    target = destination / source.name
    relative_source = Path(os.path.relpath(source, destination))

    if target.is_symlink():
        if target.readlink() == relative_source:
            print(f"ok      {target}")
            return
        raise FileExistsError(f"refusing to replace foreign symlink: {target}")
    if target.exists():
        raise FileExistsError(f"refusing to replace existing path: {target}")

    print(f"link    {target} -> {relative_source}")
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        target.symlink_to(relative_source, target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["agents", "claude", "codex", "grok", "hermes"], required=True)
    parser.add_argument("--plugin", action="append", dest="plugins", help="plugin id; repeatable (default: all)")
    parser.add_argument("--dest", type=Path, help="override the target skill directory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = set(args.plugins or [plugin["id"] for plugin in CATALOG["plugins"]])
    known = {plugin["id"] for plugin in CATALOG["plugins"]}
    unknown = selected - known
    if unknown:
        parser.error(f"unknown plugin(s): {', '.join(sorted(unknown))}")

    destination = (args.dest or default_destination(args.target)).expanduser().resolve()
    for plugin in CATALOG["plugins"]:
        if plugin["id"] not in selected:
            continue
        for skill in skill_directories(plugin):
            link_skill(skill, destination, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
