"""Shared inspection of native manifests and Agent Skills."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

_NAME = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.MULTILINE)
_DESCRIPTION = re.compile(r"^description:\s*.+$", re.MULTILINE)


@dataclass(frozen=True)
class SkillInfo:
    name: str
    root: Path


def _safe_skill_name(value: str, path: Path) -> str:
    if (
        value in {".", ".."}
        or Path(value).is_absolute()
        or PurePath(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"skill name must be one safe filename component: {path}: {value!r}")
    return value


def load_manifest(package: Path, agent: str) -> dict[str, object]:
    path = package / f".{agent}-plugin" / "plugin.json"
    if not path.is_file():
        path = package / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be an object: {path}")
    for field in ("name", "description"):
        value = data.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"manifest {field} must be a non-empty string: {path}")
    return data


def _require_contained(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"skill content escapes its declared root: {path}") from error


def _validate_skill_tree(root: Path, skill: Path) -> None:
    for current, directories, files in os.walk(skill, followlinks=False):
        current_path = Path(current)
        _require_contained(root, current_path)
        for name in directories + files:
            child = current_path / name
            if child.is_symlink():
                raise ValueError(f"skill content may not be a symlink: {child}")
            _require_contained(root, child)


def discover_skills(root: Path) -> tuple[SkillInfo, ...]:
    if root.is_symlink():
        raise ValueError(f"skills root may not be a symlink: {root}")
    if not root.is_dir():
        raise ValueError(f"skills root does not exist: {root}")
    canonical_root = root.resolve()
    skills: list[SkillInfo] = []
    for directory in sorted(root.iterdir()):
        if directory.is_symlink():
            raise ValueError(f"skill directory may not be a symlink: {directory}")
        if not directory.is_dir():
            continue
        resolved_directory = directory.resolve()
        if canonical_root not in resolved_directory.parents:
            raise ValueError(f"skill directory escapes skills root: {directory}")
        skill_file = directory / "SKILL.md"
        if skill_file.is_symlink():
            raise ValueError(f"SKILL.md may not be a symlink: {skill_file}")
        if not skill_file.is_file():
            continue
        resolved_skill = skill_file.resolve()
        if canonical_root not in resolved_skill.parents:
            raise ValueError(f"SKILL.md escapes skills root: {skill_file}")
        _validate_skill_tree(canonical_root, directory)
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"skill frontmatter must start at byte zero: {skill_file}")
        closing = text.find("\n---\n", 4)
        if closing == -1:
            raise ValueError(f"skill frontmatter is not closed: {skill_file}")
        frontmatter = text[4:closing]
        name = _NAME.search(frontmatter)
        if not name or not _DESCRIPTION.search(frontmatter):
            raise ValueError(f"skill frontmatter needs name and description: {skill_file}")
        skills.append(SkillInfo(_safe_skill_name(name.group(1).strip(), skill_file), directory))
    return tuple(skills)
