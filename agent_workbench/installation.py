"""Plan and apply safe skill-directory installations."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from .adapters.base import InstallTarget
from .model import TargetId
from .state import InstallState, StateEntry, load_state, remove_state, state_path, write_state
from .workspace import Workspace


class OperationKind(str, Enum):
    CREATE = "create"
    KEEP = "keep"
    ADOPT = "adopt"
    REPLACE_OWNED = "replace-owned"
    REMOVE_OWNED = "remove-owned"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class InstallOperation:
    kind: OperationKind
    key: str
    destination: Path
    source: Path | None
    reason: str
    expected_link: Path | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "destination": str(self.destination),
            "source": str(self.source) if self.source else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class InstallPlan:
    target: TargetId
    destination: Path
    operations: tuple[InstallOperation, ...]
    next_state: InstallState
    previous_state: InstallState | None
    state_file: Path
    destination_identity: tuple[int, int] | None
    destination_anchor: Path
    destination_anchor_identity: tuple[int, int]

    @property
    def has_conflicts(self) -> bool:
        return any(operation.kind is OperationKind.CONFLICT for operation in self.operations)


@dataclass
class _QuarantinedEntry:
    root: "_DestinationRoot"
    directory_name: str
    directory_fd: int | None


class _DestinationRoot:
    def __init__(self, plan: InstallPlan, create: bool) -> None:
        self.path = plan.destination
        self.expected = plan.destination_identity
        self.fd: int | None = None
        self.secure = all(
            function in os.supports_dir_fd
            for function in (os.stat, os.readlink, os.rename, os.symlink, os.unlink, os.mkdir, os.rmdir)
        )
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if self.secure:
            try:
                descriptor = os.open(plan.destination_anchor, flags)
            except OSError as error:
                raise FileExistsError(
                    f"destination ancestor changed since planning: {plan.destination_anchor}"
                ) from error
            try:
                metadata = os.fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) != plan.destination_anchor_identity:
                    raise FileExistsError(
                        f"destination ancestor changed since planning: {plan.destination_anchor}"
                    )
                for component in self.path.relative_to(plan.destination_anchor).parts:
                    try:
                        child = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        if not create:
                            raise FileExistsError(f"destination root changed since planning: {self.path}")
                        os.mkdir(component, mode=0o755, dir_fd=descriptor)
                        child = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                    if not stat.S_ISDIR(child.st_mode):
                        raise FileExistsError(f"destination root changed since planning: {self.path}")
                    next_descriptor = os.open(component, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = next_descriptor
                identity = os.fstat(descriptor)
                actual_identity = (identity.st_dev, identity.st_ino)
                if self.expected is not None and actual_identity != self.expected:
                    raise FileExistsError(f"destination root changed since planning: {self.path}")
                self.fd = descriptor
                self.identity = actual_identity
            except Exception:
                os.close(descriptor)
                raise
            return

        if create and self.expected is None:
            self.path.mkdir(parents=True, exist_ok=True)
        try:
            identity = _destination_identity(self.path)
        except ValueError as error:
            raise FileExistsError(f"destination root changed since planning: {self.path}") from error
        if identity is None or (self.expected is not None and identity != self.expected):
            raise FileExistsError(f"destination root changed since planning: {self.path}")
        self.identity = identity
        try:
            self.fd = os.open(self.path, flags)
        except (AttributeError, NotImplementedError, OSError):
            self.fd = None
        if self.fd is not None:
            metadata = os.fstat(self.fd)
            if (metadata.st_dev, metadata.st_ino) != self.identity:
                os.close(self.fd)
                self.fd = None
                raise FileExistsError(f"destination root changed since planning: {self.path}")

    def __enter__(self) -> "_DestinationRoot":
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _name(self, operation: InstallOperation) -> str:
        _require_contained(self.path, operation.destination)
        return operation.destination.name

    def verify(self) -> None:
        if self.fd is not None:
            metadata = os.fstat(self.fd)
            if (metadata.st_dev, metadata.st_ino) != self.identity:
                raise FileExistsError(f"destination root changed since planning: {self.path}")
        try:
            current = _destination_identity(self.path)
        except ValueError as error:
            raise FileExistsError(f"destination root changed since planning: {self.path}") from error
        if current != self.identity:
            raise FileExistsError(f"destination root changed since planning: {self.path}")

    def readlink(self, operation: InstallOperation) -> Path:
        name = self._name(operation)
        if self.secure:
            assert self.fd is not None
            return Path(os.readlink(name, dir_fd=self.fd))
        self.verify()
        return operation.destination.readlink()

    def same_link(self, operation: InstallOperation, source: Path) -> bool:
        try:
            target = self.readlink(operation)
        except (FileNotFoundError, OSError):
            return False
        return (self.path / target).resolve(strict=False) == source.resolve(strict=False)

    def create_link(self, operation: InstallOperation) -> None:
        assert operation.source is not None
        name = self._name(operation)
        target = _relative_link(operation.source, self.path)
        try:
            if self.secure:
                assert self.fd is not None
                os.symlink(str(target), name, target_is_directory=True, dir_fd=self.fd)
            else:
                self.verify()
                operation.destination.symlink_to(target, target_is_directory=True)
        except FileExistsError as error:
            raise _changed(operation) from error

    def quarantine(self, operation: InstallOperation) -> _QuarantinedEntry:
        if operation.expected_link is None:
            raise ValueError(f"owned removal lacks an expected link target: {operation.destination}")
        name = self._name(operation)
        directory_name = f".agent-workbench-quarantine-{secrets.token_hex(8)}"
        if self.secure:
            assert self.fd is not None
            os.mkdir(directory_name, mode=0o700, dir_fd=self.fd)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(directory_name, flags, dir_fd=self.fd)
            try:
                os.rename(name, "entry", src_dir_fd=self.fd, dst_dir_fd=directory_fd)
            except Exception:
                os.close(directory_fd)
                os.rmdir(directory_name, dir_fd=self.fd)
                raise
            moved = _QuarantinedEntry(self, directory_name, directory_fd)
        else:
            self.verify()
            directory = self.path / directory_name
            directory.mkdir(mode=0o700)
            moved_path = directory / "entry"
            try:
                os.rename(str(operation.destination), str(moved_path))
            except Exception:
                directory.rmdir()
                raise
            moved = _QuarantinedEntry(self, directory_name, None)
        if self.quarantined_link(moved) == operation.expected_link:
            return moved
        self.restore(moved, operation)
        raise _changed(operation)

    def quarantined_link(self, moved: _QuarantinedEntry) -> Path | None:
        try:
            if moved.directory_fd is not None:
                return Path(os.readlink("entry", dir_fd=moved.directory_fd))
            path = self.path / moved.directory_name / "entry"
            return path.readlink() if path.is_symlink() else None
        except OSError:
            return None

    @staticmethod
    def _close_quarantine(moved: _QuarantinedEntry) -> None:
        if moved.directory_fd is not None:
            os.close(moved.directory_fd)
            moved.directory_fd = None

    def restore(self, moved: _QuarantinedEntry, operation: InstallOperation) -> None:
        name = self._name(operation)
        try:
            if moved.directory_fd is not None:
                assert self.fd is not None
                os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                raise FileExistsError
            if not _is_absent(operation.destination):
                raise FileExistsError
        except FileNotFoundError:
            pass
        except FileExistsError as error:
            self._close_quarantine(moved)
            recovery = self.path / moved.directory_name / "entry"
            raise FileExistsError(
                f"destination changed while restoring quarantined entry; recover it from {recovery}"
            ) from error
        if moved.directory_fd is not None:
            assert self.fd is not None
            try:
                os.rename("entry", name, src_dir_fd=moved.directory_fd, dst_dir_fd=self.fd)
            finally:
                self._close_quarantine(moved)
            os.rmdir(moved.directory_name, dir_fd=self.fd)
        else:
            directory = self.path / moved.directory_name
            os.rename(str(directory / "entry"), str(operation.destination))
            directory.rmdir()

    def discard(self, moved: _QuarantinedEntry) -> None:
        if moved.directory_fd is not None:
            assert self.fd is not None
            try:
                os.unlink("entry", dir_fd=moved.directory_fd)
            finally:
                self._close_quarantine(moved)
            os.rmdir(moved.directory_name, dir_fd=self.fd)
        else:
            directory = self.path / moved.directory_name
            (directory / "entry").unlink()
            directory.rmdir()


def _same_link(path: Path, source: Path) -> bool:
    return path.is_symlink() and path.resolve(strict=False) == source.resolve(strict=False)


def _relative_link(source: Path, destination_parent: Path) -> Path:
    return Path(os.path.relpath(source, destination_parent))


def _is_absent(path: Path) -> bool:
    return not path.exists() and not path.is_symlink()


def _require_contained(root: Path, destination: Path) -> None:
    installation_root = root.absolute()
    candidate = destination.absolute()
    if candidate.parent != installation_root or candidate == installation_root:
        raise ValueError(f"generated destination must be a direct child of installation root: {destination}")


def _destination_identity(path: Path) -> tuple[int, int] | None:
    if path.is_symlink():
        raise ValueError(f"destination root may not be a symlink: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"destination root must be a directory: {path}")
    return metadata.st_dev, metadata.st_ino


def _destination_anchor(path: Path) -> tuple[Path, tuple[int, int]]:
    candidate = path
    while True:
        identity = _destination_identity(candidate)
        if identity is not None:
            return candidate, identity
        parent = candidate.parent
        if parent == candidate:
            raise ValueError(f"destination has no existing directory ancestor: {path}")
        candidate = parent


def _changed(operation: InstallOperation) -> FileExistsError:
    return FileExistsError(f"destination changed since planning: {operation.destination}")


@contextmanager
def _state_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    if fcntl is not None:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FileExistsError(f"installation state is locked by another operation: {path}") from error
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        return

    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise FileExistsError(f"installation state is locked by another operation: {path}") from error
    try:
        yield
    finally:
        lock_path.rmdir()


def _target(workspace: Workspace, target_id: str) -> InstallTarget:
    target = workspace.registry.install_targets.get(TargetId(target_id))
    if target is None:
        raise ValueError(f"unknown install target: {target_id}")
    allowed = {target.id for target in workspace.profile.targets}
    if target.id not in allowed:
        raise ValueError(f"target {target_id} is not enabled by profile {workspace.profile.id}")
    return target


def _state_metadata(workspace: Workspace, target_id: str) -> tuple[str, str, str]:
    return workspace.catalog.marketplace.id, str(workspace.profile.id), target_id


def _validated_state(
    workspace: Workspace,
    target_id: str,
    file: Path,
    destination_root: Path,
) -> InstallState | None:
    previous = load_state(file)
    if previous is None:
        return None
    expected_metadata = _state_metadata(workspace, target_id)
    actual_metadata = (previous.marketplace, previous.profile, previous.target)
    if actual_metadata != expected_metadata:
        raise ValueError("installation state metadata does not match marketplace, profile, and target")
    recorded_root = Path(previous.destination_root).resolve()
    if recorded_root != destination_root.resolve():
        raise ValueError(
            f"recorded destination root differs from requested destination: {recorded_root}; "
            "pass the same explicit --destination used for installation"
        )
    repository = Path(previous.repository)
    for key, entry in previous.entries.items():
        if entry.key != key:
            raise ValueError(f"installation state entry key does not match mapping key: {key!r}")
        expected_name = key.partition(":")[2]
        source = repository.joinpath(*Path(entry.source).parts)
        if source == repository or repository not in source.parents:
            raise ValueError(f"installation state source escapes repository: {entry.source!r}")
        destination = Path(entry.destination)
        if destination.name != expected_name:
            raise ValueError(f"installation state entry key does not match destination: {key!r}")
        _require_contained(recorded_root, destination)
    return previous


def _empty_state(workspace: Workspace, target_id: str, destination_root: Path) -> InstallState:
    return InstallState(
        schema_version=2,
        repository=str(workspace.root),
        marketplace=workspace.catalog.marketplace.id,
        profile=str(workspace.profile.id),
        target=target_id,
        destination_root=str(destination_root.resolve()),
        entries={},
    )


def plan_install(
    workspace: Workspace,
    target_id: str,
    state_root: Path,
    destination: Path | None = None,
    adopt: bool = False,
) -> InstallPlan:
    target = _target(workspace, target_id)
    selected = next(item for item in workspace.profile.targets if item.id == target.id)
    destination_path = (destination or (Path(selected.destination).expanduser() if selected.destination else target.default_destination())).resolve()
    desired = target.desired_entries(workspace.resolved, destination_path)
    for entry in desired:
        _require_contained(destination_path, entry.destination)
    desired_by_key = {entry.key: entry for entry in desired}
    if len(desired_by_key) != len(desired):
        raise ValueError("desired installation keys must be unique")

    file = state_path(state_root, workspace.catalog.marketplace.id, str(workspace.profile.id), target_id)
    previous = _validated_state(workspace, target_id, file, destination_path)
    owned = previous.entries if previous else {}
    operations: list[InstallOperation] = []
    next_entries: dict[str, StateEntry] = {}

    for entry in desired:
        prior = owned.get(entry.key)
        expected_link = None
        if _is_absent(entry.destination):
            kind, reason = OperationKind.CREATE, "destination is absent"
        elif _same_link(entry.destination, entry.source):
            if prior:
                kind, reason = OperationKind.KEEP, "managed link already matches"
            elif adopt:
                kind, reason = OperationKind.ADOPT, "matching unmanaged link explicitly adopted"
            else:
                kind, reason = OperationKind.CONFLICT, "matching link is not recorded as owned; use --adopt"
        elif prior and previous and Path(prior.destination) == entry.destination and entry.destination.is_symlink():
            prior_source = Path(previous.repository).joinpath(*Path(prior.source).parts)
            if _same_link(entry.destination, prior_source):
                kind, reason = OperationKind.REPLACE_OWNED, "managed link points to a stale source"
                expected_link = entry.destination.readlink()
            else:
                kind, reason = OperationKind.CONFLICT, "managed path was changed to a foreign link"
        else:
            kind, reason = OperationKind.CONFLICT, "destination is a foreign file or link"
        operations.append(InstallOperation(kind, entry.key, entry.destination, entry.source, reason, expected_link))
        next_entries[entry.key] = StateEntry(
            key=entry.key,
            plugin=entry.plugin_id,
            source=entry.source.relative_to(workspace.root).as_posix(),
            destination=str(entry.destination),
        )

    for key, prior in sorted(owned.items()):
        if key in desired_by_key:
            continue
        removal_destination = Path(prior.destination)
        prior_repository = Path(previous.repository) if previous else workspace.root
        expected_source = prior_repository.joinpath(*Path(prior.source).parts)
        if _is_absent(removal_destination):
            continue
        if _same_link(removal_destination, expected_source):
            operations.append(
                InstallOperation(
                    OperationKind.REMOVE_OWNED,
                    key,
                    removal_destination,
                    expected_source,
                    "managed entry is no longer selected",
                    removal_destination.readlink(),
                )
            )
        else:
            operations.append(InstallOperation(OperationKind.CONFLICT, key, removal_destination, expected_source, "stale state points at a modified path"))

    state = InstallState(
        schema_version=2,
        repository=str(workspace.root),
        marketplace=workspace.catalog.marketplace.id,
        profile=str(workspace.profile.id),
        target=target_id,
        destination_root=str(destination_path),
        entries=next_entries,
    )
    destination_identity = _destination_identity(destination_path)
    destination_anchor, destination_anchor_identity = _destination_anchor(destination_path)
    return InstallPlan(
        target=TargetId(target_id),
        destination=destination_path,
        operations=tuple(operations),
        next_state=state,
        previous_state=previous,
        state_file=file,
        destination_identity=destination_identity,
        destination_anchor=destination_anchor,
        destination_anchor_identity=destination_anchor_identity,
    )


def _current_entries(plan: InstallPlan) -> dict[str, StateEntry]:
    current = load_state(plan.state_file)
    if current != plan.previous_state:
        raise ValueError("installation state changed since planning")
    return dict(current.entries) if current is not None else {}


def _write_progress(plan: InstallPlan, entries: dict[str, StateEntry]) -> None:
    state = InstallState(
        schema_version=plan.next_state.schema_version,
        repository=plan.next_state.repository,
        marketplace=plan.next_state.marketplace,
        profile=plan.next_state.profile,
        target=plan.next_state.target,
        destination_root=plan.next_state.destination_root,
        entries=dict(entries),
    )
    write_state(plan.state_file, state)


def _remove_created_link(root: _DestinationRoot, operation: InstallOperation) -> None:
    assert operation.source is not None
    cleanup = InstallOperation(
        kind=OperationKind.REMOVE_OWNED,
        key=operation.key,
        destination=operation.destination,
        source=operation.source,
        reason="roll back newly created link",
        expected_link=_relative_link(operation.source, operation.destination.parent),
    )
    moved = root.quarantine(cleanup)
    root.discard(moved)


def _apply_plan_locked(plan: InstallPlan) -> None:
    if plan.has_conflicts:
        raise ValueError("installation plan has conflicts")
    entries = _current_entries(plan)
    with _DestinationRoot(plan, create=True) as root:
        for operation in plan.operations:
            root.verify()
            if operation.kind in {OperationKind.KEEP, OperationKind.ADOPT}:
                assert operation.source is not None
                if not root.same_link(operation, operation.source):
                    raise _changed(operation)
                entries[operation.key] = plan.next_state.entries[operation.key]
                _write_progress(plan, entries)
                continue
            if operation.kind is OperationKind.REMOVE_OWNED:
                moved = root.quarantine(operation)
                try:
                    root.verify()
                except Exception:
                    root.restore(moved, operation)
                    raise
                entries.pop(operation.key, None)
                try:
                    _write_progress(plan, entries)
                except Exception:
                    root.restore(moved, operation)
                    raise
                root.discard(moved)
                continue
            if operation.kind is OperationKind.CREATE:
                root.create_link(operation)
                try:
                    root.verify()
                except Exception:
                    _remove_created_link(root, operation)
                    raise
                entries[operation.key] = plan.next_state.entries[operation.key]
                try:
                    _write_progress(plan, entries)
                except Exception:
                    _remove_created_link(root, operation)
                    raise
                continue
            if operation.kind is OperationKind.REPLACE_OWNED:
                assert operation.source is not None
                old = root.quarantine(operation)
                try:
                    root.verify()
                    root.create_link(operation)
                    root.verify()
                except Exception:
                    if root.same_link(operation, operation.source):
                        _remove_created_link(root, operation)
                    root.restore(old, operation)
                    raise
                entries[operation.key] = plan.next_state.entries[operation.key]
                try:
                    _write_progress(plan, entries)
                except Exception:
                    try:
                        _remove_created_link(root, operation)
                        root.restore(old, operation)
                    except Exception as rollback_error:
                        raise RuntimeError(
                            f"replacement rollback failed; previous entry remains in {old.directory_name}"
                        ) from rollback_error
                    raise
                root.discard(old)
    write_state(plan.state_file, plan.next_state)


def apply_plan(plan: InstallPlan) -> None:
    with _state_lock(plan.state_file):
        _apply_plan_locked(plan)


def plan_uninstall(
    workspace: Workspace,
    target_id: str,
    state_root: Path,
    destination: Path | None = None,
) -> InstallPlan:
    target = _target(workspace, target_id)
    selected = next(item for item in workspace.profile.targets if item.id == target.id)
    installation_root = (
        destination.expanduser()
        if destination
        else (Path(selected.destination).expanduser() if selected.destination else target.default_destination())
    ).resolve()
    file = state_path(state_root, workspace.catalog.marketplace.id, str(workspace.profile.id), target_id)
    previous = _validated_state(workspace, target_id, file, installation_root)
    destination_identity = _destination_identity(installation_root)
    destination_anchor, destination_anchor_identity = _destination_anchor(installation_root)
    if previous is None:
        return InstallPlan(
            target=TargetId(target_id),
            destination=installation_root,
            operations=(),
            next_state=_empty_state(workspace, target_id, installation_root),
            previous_state=None,
            state_file=file,
            destination_identity=destination_identity,
            destination_anchor=destination_anchor,
            destination_anchor_identity=destination_anchor_identity,
        )
    operations: list[InstallOperation] = []
    for key, entry in sorted(previous.entries.items()):
        removal_destination = Path(entry.destination)
        source = Path(previous.repository).joinpath(*Path(entry.source).parts)
        if _is_absent(removal_destination):
            continue
        if _same_link(removal_destination, source):
            operations.append(
                InstallOperation(
                    OperationKind.REMOVE_OWNED,
                    key,
                    removal_destination,
                    source,
                    "remove managed entry",
                    removal_destination.readlink(),
                )
            )
        else:
            operations.append(InstallOperation(OperationKind.CONFLICT, key, removal_destination, source, "managed path was modified"))
    return InstallPlan(
        target=TargetId(target_id),
        destination=installation_root,
        operations=tuple(operations),
        next_state=_empty_state(workspace, target_id, installation_root),
        previous_state=previous,
        state_file=file,
        destination_identity=destination_identity,
        destination_anchor=destination_anchor,
        destination_anchor_identity=destination_anchor_identity,
    )


def _apply_uninstall_locked(plan: InstallPlan) -> None:
    if plan.has_conflicts:
        raise ValueError("uninstall plan has conflicts")
    entries = _current_entries(plan)
    if not plan.operations:
        remove_state(plan.state_file)
        return
    with _DestinationRoot(plan, create=False) as root:
        for operation in plan.operations:
            root.verify()
            if operation.kind is OperationKind.REMOVE_OWNED:
                moved = root.quarantine(operation)
                try:
                    root.verify()
                except Exception:
                    root.restore(moved, operation)
                    raise
                entries.pop(operation.key, None)
                try:
                    _write_progress(plan, entries)
                except Exception:
                    root.restore(moved, operation)
                    raise
                root.discard(moved)
    remove_state(plan.state_file)


def apply_uninstall(plan: InstallPlan) -> None:
    with _state_lock(plan.state_file):
        _apply_uninstall_locked(plan)
