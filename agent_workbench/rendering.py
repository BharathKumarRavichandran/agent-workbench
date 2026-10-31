"""Deterministic generated-file rendering."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .adapters.base import GeneratedFile
from .workspace import Workspace


def _safe_output_path(repository: Path, output: Path) -> Path:
    root = repository.resolve()
    lexical = output.absolute()
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise ValueError(f"generated output escapes repository: {output}") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"generated output has a symlinked output path: {current}")
    resolved = lexical.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"generated output escapes repository: {output}")
    return lexical


def generated_files(workspace: Workspace) -> tuple[GeneratedFile, ...]:
    files: list[GeneratedFile] = []
    seen: dict[Path, str] = {}
    for renderer in workspace.registry.renderers:
        for generated in renderer.render(workspace.root, workspace.catalog, workspace.resolved):
            path = _safe_output_path(workspace.root, generated.path)
            if path in seen:
                raise ValueError(f"generated output collision at {path}: {seen[path]} and {generated.producer}")
            seen[path] = generated.producer
            files.append(generated)
    return tuple(files)


def _write_generated(repository: Path, output: Path, content: bytes) -> None:
    root = repository.resolve()
    relative = output.absolute().relative_to(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    temporary = f".{relative.name}.{secrets.token_hex(8)}"
    try:
        for component in relative.parts[:-1]:
            try:
                child = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.mkdir(component, mode=0o755, dir_fd=descriptor)
                child = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(child.st_mode):
                raise ValueError(f"generated output has a non-directory parent: {output}")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        temporary_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=descriptor,
        )
        try:
            with os.fdopen(temporary_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                existing = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and not stat.S_ISREG(existing.st_mode):
                raise ValueError(f"generated output may only replace a regular file: {output}")
            os.replace(temporary, relative.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            temporary = ""
        finally:
            if temporary:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass
    finally:
        os.close(descriptor)


def render(workspace: Workspace, check: bool = False) -> tuple[Path, ...]:
    if str(workspace.profile.id) != "default":
        raise ValueError("canonical generated files may only be rendered with the default profile")
    canonical = Workspace.load(workspace.root, registry=workspace.registry)
    stale: list[Path] = []
    for generated in generated_files(canonical):
        output = _safe_output_path(canonical.root, generated.path)
        current = output.read_bytes() if output.is_file() else None
        if current == generated.content:
            continue
        stale.append(output)
        if check:
            continue
        _write_generated(canonical.root, output, generated.content)
    return tuple(stale)
