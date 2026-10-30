#!/usr/bin/env python3
"""Validate the catalog, package roots, skills, and generated files."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "catalog" / "plugins.json").read_text(encoding="utf-8"))
NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


def validate_skill(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"frontmatter must start at byte zero: {path}")
    closing = text.find("\n---\n", 4)
    if closing == -1:
        raise ValueError(f"frontmatter is not closed: {path}")
    frontmatter = text[4:closing]
    name = NAME.search(frontmatter)
    if not name or not DESCRIPTION.search(frontmatter):
        raise ValueError(f"frontmatter needs name and description: {path}")
    return name.group(1).strip("\"'")


def main() -> int:
    ids = [plugin["id"] for plugin in CATALOG["plugins"]]
    if len(ids) != len(set(ids)):
        raise ValueError("plugin ids must be unique")

    skill_names: dict[str, Path] = {}
    for plugin in CATALOG["plugins"]:
        for agent in ("cursor", "claude"):
            package = ROOT / plugin["packages"][agent]
            manifest = package / f".{agent}-plugin" / "plugin.json"
            if not manifest.is_file():
                raise FileNotFoundError(manifest)
            json.loads(manifest.read_text(encoding="utf-8"))

        skills = ROOT / plugin["packages"]["skills"]
        if not skills.is_dir():
            raise FileNotFoundError(skills)
        for skill_file in sorted(skills.glob("*/SKILL.md")):
            name = validate_skill(skill_file)
            if name in skill_names:
                raise ValueError(f"duplicate skill name {name}: {skill_names[name]} and {skill_file}")
            skill_names[name] = skill_file

    subprocess.run(["python3", "scripts/render_marketplaces.py", "--check"], cwd=ROOT, check=True)
    print(f"validated {len(ids)} plugins and {len(skill_names)} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
