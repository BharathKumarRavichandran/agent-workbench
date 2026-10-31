"""Reviewed source update operations."""

from __future__ import annotations

import configparser
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Source, SourceKind
from .rendering import generated_files, render
from .validation import validate
from .workspace import Workspace


@dataclass(frozen=True)
class UpdateResult:
    source: str
    before: str
    after: str


def _source_path(workspace: Workspace, source: Source) -> Path:
    current = workspace.root
    for part in Path(source.path.value).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"source path contains a symlink: {source.path.value}")
    return source.path.resolve(workspace.root)


def _revision(workspace: Workspace, path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        cwd=workspace.root,
        text=True,
    ).strip()


def _verify_submodule_registration(workspace: Workspace, source: Source, path: Path) -> None:
    relative = path.relative_to(workspace.root).as_posix()
    staged = subprocess.check_output(
        ["git", "ls-files", "--stage", "--", relative],
        cwd=workspace.root,
        text=True,
    ).strip()
    if not staged.startswith("160000 "):
        raise ValueError(f"source is not a registered Git submodule: {relative}")

    modules = configparser.ConfigParser()
    modules.read(workspace.root / ".gitmodules", encoding="utf-8")
    matches = [section for section in modules.sections() if modules.get(section, "path", fallback="") == relative]
    if len(matches) != 1:
        raise ValueError(f"source has no unique .gitmodules entry: {relative}")
    configured = modules.get(matches[0], "url", fallback="")
    if configured != source.upstream:
        raise ValueError(
            f"submodule upstream differs from catalog: configured {configured!r}, catalog {source.upstream!r}"
        )


def _generated_snapshot(workspace: Workspace) -> dict[Path, bytes | None]:
    return {
        generated.path: generated.path.read_bytes() if generated.path.is_file() else None
        for generated in generated_files(workspace)
    }


def _restore_generated(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.exists() or path.is_symlink():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def update_source(workspace: Workspace, source_id: str) -> UpdateResult:
    if str(workspace.profile.id) != "default":
        raise ValueError("sources may only be updated with the default profile")
    source = next((item for item in workspace.catalog.sources if item.id == source_id), None)
    if source is None:
        raise ValueError(f"unknown source: {source_id}")
    if source.kind is not SourceKind.GIT_SUBMODULE:
        raise ValueError(f"source {source_id} is not updateable: {source.kind.value}")
    if source.branch is None or source.upstream is None:
        raise ValueError(f"source {source_id} has no reviewed branch and upstream")

    path = _source_path(workspace, source)
    _verify_submodule_registration(workspace, source, path)
    before = _revision(workspace, path)
    snapshot = _generated_snapshot(workspace)
    ref = f"refs/heads/{source.branch}"

    try:
        subprocess.run(
            ["git", "-C", str(path), "fetch", "--no-tags", source.upstream, ref],
            cwd=workspace.root,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"],
            cwd=workspace.root,
            check=True,
        )

        candidate = Workspace.load(workspace.root)
        report = validate(candidate, check_generated=False)
        if not report.valid:
            messages = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
            raise ValueError(f"updated source failed validation: {messages}")

        subprocess.run(
            ["git", "-C", str(path), "submodule", "update", "--init", "--recursive"],
            cwd=workspace.root,
            check=True,
        )
        candidate = Workspace.load(workspace.root)
        report = validate(candidate, check_generated=False)
        if not report.valid:
            messages = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
            raise ValueError(f"updated source failed validation after nested submodule update: {messages}")
        render(candidate)
        final = validate(candidate)
        if not final.valid:
            messages = "; ".join(f"{issue.code}: {issue.message}" for issue in final.issues)
            raise ValueError(f"updated source failed final validation: {messages}")
    except Exception:
        subprocess.run(
            ["git", "-C", str(path), "checkout", "--detach", before],
            cwd=workspace.root,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(path), "submodule", "update", "--init", "--recursive"],
            cwd=workspace.root,
            check=False,
        )
        _restore_generated(snapshot)
        raise

    after = _revision(workspace, path)
    return UpdateResult(source_id, before, after)
