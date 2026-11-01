"""Plan and reconcile owned directory symlinks and copied component files."""

from __future__ import annotations

import os
import hashlib
import secrets
import stat
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from .adapters.base import InstallTarget, DesiredEntry
from .adapters.projections import project_paths
from .artifacts import COMPONENT_ARTIFACTS, semantic_exports
from .model import ArtifactInventory, ComponentExport, ResolvedPlugin
from .inspection import validate_skill_tree
from .model import TargetId, ComponentKind, DirectorySymlink, FileCopy, DirectoryCopy, PathResource
from .state import InstallState, StateEntry, load_state, remove_state, state_path, write_state
from .workspace import Workspace
from . import bundles, shared_config
from .adapters.activation import ACTIVATION_POLICIES
from .trust import grant_token, authenticated, reuse_proof
from .state import SlotState


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
    component: ComponentKind = ComponentKind.SKILLS
    resource: PathResource = DirectorySymlink()
    expected_digest: str | None = None
    diagnostic: str | None = None

    trust_token: str | None = None
    trust_required: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "destination": str(self.destination),
            "source": str(self.source) if self.source else None,
            "reason": self.reason,
            "component": self.component.value,
            "resourceKind": self.resource.kind if self.source is not None else None,
            "digest": self.resource.digest if isinstance(self.resource, (FileCopy, DirectoryCopy)) else None,
            "diagnostic": self.diagnostic,
            "trustToken": self.trust_token,
            "trustRequired": self.trust_required,
        }


@dataclass(frozen=True)
class DestinationSnapshot:
    destination: Path
    destination_identity: tuple[int, int] | None
    destination_anchor: Path
    destination_anchor_identity: tuple[int, int]


@dataclass(frozen=True)
class InstallPlan:
    target: TargetId
    repository: Path
    destination: Path
    operations: tuple[InstallOperation, ...]
    next_state: InstallState
    previous_state: InstallState | None
    state_file: Path
    destination_identity: tuple[int, int] | None
    destination_anchor: Path
    destination_anchor_identity: tuple[int, int]
    roots: tuple[DestinationSnapshot, ...]

    documents: tuple[shared_config.DocumentPlan, ...] = ()
    executable_sources: tuple[shared_config.DesiredSlot, ...] = ()

    @property
    def all_operations(self) -> tuple[InstallOperation | shared_config.SlotOperation, ...]:
        return self.operations + tuple(operation for document in self.documents for operation in document.operations)

    @property
    def has_conflicts(self) -> bool:
        return any(operation.kind is OperationKind.CONFLICT for operation in self.all_operations)


@dataclass
class _QuarantinedEntry:
    root: "_DestinationRoot"
    directory_name: str
    directory_fd: int | None


class _DestinationRoot:
    def __init__(self, plan: DestinationSnapshot, create: bool) -> None:
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
            fd = bundles.open_directory(self.path)
            try:
                info = os.fstat(fd)
                current = (info.st_dev, info.st_ino)
            finally:
                os.close(fd)
        except (OSError, ValueError) as error:
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

    def same_resource(self, operation: InstallOperation) -> bool:
        if isinstance(operation.resource, DirectorySymlink):
            assert operation.source is not None
            return self.same_link(operation, operation.source)
        if isinstance(operation.resource, DirectoryCopy):
            assert self.fd is not None
            return bundles.tree_digest(self._name(operation), self.fd) == operation.resource.digest
        if self.secure:
            assert self.fd is not None
            return _file_digest(self._name(operation), self.fd) == operation.resource.digest
        self.verify()
        return _file_digest(operation.destination) == operation.resource.digest

    def create(self, operation: InstallOperation, content: bytes | bundles.Tree | None) -> None:
        if isinstance(operation.resource, DirectorySymlink):
            self.create_link(operation)
            return
        if isinstance(operation.resource, DirectoryCopy):
            assert isinstance(content, bundles.Tree) and self.fd is not None
            name = self._name(operation)
            temporary = f".agent-workbench-copy-{secrets.token_hex(8)}"
            try:
                bundles.write_tree(temporary, self.fd, content)
                # Refuse foreign destinations. The document lock serializes all
                # workbench bundle writers; quarantine handles replacements.
                try:
                    os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise _changed(operation)
                bundles.rename_new(temporary, name, self.fd)
                os.fsync(self.fd)
            finally:
                try:
                    bundles.delete_tree(temporary, self.fd)
                except FileNotFoundError:
                    pass
            return
        assert content is not None
        name = self._name(operation)
        temporary = f".agent-workbench-copy-{secrets.token_hex(8)}"
        if not self.secure:
            raise OSError("managed copies require descriptor-relative filesystem operations")
        assert self.fd is not None
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o644, dir_fd=self.fd)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
            except FileExistsError as error:
                raise _changed(operation) from error
        finally:
            os.unlink(temporary, dir_fd=self.fd)

    def quarantine(self, operation: InstallOperation, directory_name: str | None = None) -> _QuarantinedEntry:
        if operation.expected_link is None and operation.expected_digest is None:
            raise ValueError(f"owned removal lacks expected ownership evidence: {operation.destination}")
        name = self._name(operation)
        directory_name = directory_name or f".agent-workbench-quarantine-{secrets.token_hex(8)}"
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
        if isinstance(operation.resource, DirectorySymlink):
            matches = self.quarantined_link(moved) == operation.expected_link
        elif isinstance(operation.resource, DirectoryCopy):
            assert moved.directory_fd is not None
            matches = bundles.tree_digest("entry", moved.directory_fd) == operation.expected_digest
        else:
            digest = (_file_digest("entry", moved.directory_fd) if moved.directory_fd is not None
                      else _file_digest(self.path / moved.directory_name / "entry"))
            matches = digest == operation.expected_digest
        if matches:
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
                if isinstance(operation.resource, DirectoryCopy):
                    bundles.rename_new("entry", name, moved.directory_fd, self.fd)
                else:
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
                metadata = os.stat("entry", dir_fd=moved.directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    bundles.delete_tree("entry", moved.directory_fd)
                else:
                    os.unlink("entry", dir_fd=moved.directory_fd)
            finally:
                self._close_quarantine(moved)
            os.rmdir(moved.directory_name, dir_fd=self.fd)
        else:
            directory = self.path / moved.directory_name
            (directory / "entry").unlink()
            directory.rmdir()


def _read_regular(path: Path | str, directory_fd: int | None = None) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError(f"copy requires a regular file: {path}")
        return handle.read()


def _file_digest(path: Path | str, directory_fd: int | None = None) -> str | None:
    try:
        return hashlib.sha256(_read_regular(path, directory_fd)).hexdigest()
    except (OSError, ValueError):
        return None


def _source_content(repository: Path, source: Path) -> bytes:
    relative = source.relative_to(repository)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(repository, flags)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return _read_regular(relative.name, descriptor)
    finally:
        os.close(descriptor)


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
    workspace: Workspace, target_id: str, file: Path,
    roots: dict[ComponentKind, Path],
) -> InstallState | None:
    previous = load_state(file)
    if previous is None:
        return None
    if (previous.marketplace, previous.profile, previous.target) != _state_metadata(workspace, target_id):
        raise ValueError("installation state metadata does not match marketplace, profile, and target")
    if Path(previous.destination_root) != roots[ComponentKind.SKILLS]:
        raise ValueError("recorded destination root differs from requested destination; pass the same explicit --destination used for installation")
    for component, recorded in previous.destination_roots.items():
        if component not in roots or Path(recorded) != roots[component]:
            raise ValueError("recorded destination root differs from adapter-authorized root; pass the same --project-root")
    for slot in previous.slots.values():
        if target_id not in ACTIVATION_POLICIES or Path(slot.document) != shared_config.document_path(roots, slot.component, target_id):
            raise ValueError("recorded document differs from adapter-authorized path")
    return previous


def _request_roots(workspace: Workspace, target: InstallTarget, destination: Path | None,
                   project_root: Path | None) -> dict[ComponentKind, Path]:
    selected = next(item for item in workspace.profile.targets if item.id == target.id)
    project = project_root or (Path(selected.project_root) if selected.project_root else None)
    if project is not None:
        project = project.expanduser().absolute()
        if ".." in project.parts:
            raise ValueError("project root must be contained")
        project = project.resolve()
    skills = destination or (Path(selected.destination) if selected.destination else None)
    if skills is None:
        skills = project / f".{target.id}" / "skills" if project else target.default_destination()
    skills = skills.expanduser().absolute()
    # Explicit legacy destinations retain their canonical path semantics. Project
    # destinations stay lexical so an adapter subdirectory cannot redirect them.
    if destination is not None or selected.destination is not None or project is None:
        if skills.is_symlink():
            raise ValueError(f"destination root may not be a symlink: {skills}")
        skills = skills.resolve()
    return target.destination_roots(skills, project)


def _empty_state(workspace: Workspace, target_id: str, roots: dict[ComponentKind, Path]) -> InstallState:
    return InstallState(3, str(workspace.root), workspace.catalog.marketplace.id,
                        str(workspace.profile.id), target_id, str(roots[ComponentKind.SKILLS]), {},
                        {component: str(root) for component, root in roots.items()})


def _make_plan(workspace: Workspace, target_id: str, file: Path,
               roots: dict[ComponentKind, Path], previous: InstallState | None,
               operations: list[InstallOperation], entries: dict[str, StateEntry]) -> InstallPlan:
    snapshots = []
    needed = {operation.destination.parent for operation in operations}
    needed.add(roots[ComponentKind.SKILLS])
    if ComponentKind.HOOKS in roots:
        needed.add(roots[ComponentKind.HOOKS].parent)
    if target_id in ACTIVATION_POLICIES and ComponentKind.MCP in roots:
        needed.add(roots[ComponentKind.MCP])
    for root in sorted((set(roots.values()) | ({roots[ComponentKind.HOOKS].parent} if ComponentKind.HOOKS in roots else set())) & needed):
        # Refuse redirected adapter directories, including existing parents.
        anchor, identity = _destination_anchor(root)
        current = root
        while current != current.parent:
            if current.is_symlink():
                raise ValueError(f"destination root may not contain a symlink: {current}")
            current = current.parent
        snapshots.append(DestinationSnapshot(root, _destination_identity(root), anchor, identity))
    destination = roots[ComponentKind.SKILLS]
    main = next(snapshot for snapshot in snapshots if snapshot.destination == destination)
    state_roots = {component: root for component, root in roots.items()
                   if component is ComponentKind.SKILLS or any(entry.component is component for entry in entries.values())}
    state = replace(_empty_state(workspace, target_id, state_roots), entries=entries)
    return InstallPlan(TargetId(target_id), workspace.root, destination, tuple(operations), state,
                       previous, file, main.destination_identity, main.destination_anchor,
                       main.destination_anchor_identity, tuple(snapshots))


def _matches_resource(destination: Path, source: Path, resource: PathResource) -> bool:
    if isinstance(resource, DirectoryCopy):
        return bundles.tree_digest(destination) == resource.digest
    if isinstance(resource, FileCopy):
        return _file_digest(destination) == resource.digest
    return _same_link(destination, source)


def _matches_owned(entry: StateEntry) -> bool:
    return _matches_resource(Path(entry.destination), Path(entry.source_repository) / entry.source,
                             entry.resource)


def _removal(key: str, entry: StateEntry, reason: str) -> InstallOperation | None:
    destination = Path(entry.destination)
    if _is_absent(destination):
        return None
    matches = _matches_owned(entry)
    return InstallOperation(
        OperationKind.REMOVE_OWNED if matches else OperationKind.CONFLICT,
        key, destination, Path(entry.source_repository) / entry.source,
        reason if matches else "managed path was modified",
        destination.readlink() if matches and isinstance(entry.resource, DirectorySymlink) else None,
        entry.component, entry.resource,
        entry.resource.digest if isinstance(entry.resource, (FileCopy, DirectoryCopy)) else None,
    )


def _protect_bundle_removal(operation: InstallOperation | None, entry: StateEntry,
                            documents: tuple[shared_config.DocumentPlan, ...],
                            roots: dict[ComponentKind, Path], target_id: str) -> InstallOperation | None:
    if not isinstance(entry.resource, DirectoryCopy):
        return operation
    hook_document = next(
        (document for document in documents
         if document.before.path == shared_config.document_path(roots, ComponentKind.HOOKS, target_id)),
        None,
    )
    if (
        operation
        and hook_document is not None
        and shared_config.hook_document_references_bundle(
            hook_document.after, Path(entry.destination)
        )
    ):
        operation = replace(
            operation,
            kind=OperationKind.CONFLICT,
            reason="an active hook still references the managed bundle",
            diagnostic="HOOK_BUNDLE_REFERENCED",
        )
    return operation


def _hook_inventories(workspace: Workspace, plugin: ResolvedPlugin) -> tuple[ArtifactInventory, ...]:
    return tuple(workspace.registry.artifact_inspectors[kind].inspect(plugin)
                 for kind in ('hooks', 'cursor-hooks', 'claude-hooks') if kind in plugin.artifact_paths)


def _hook_source(workspace: Workspace, plugin: ResolvedPlugin, target_id: str) -> Path | None:
    exact = [item.root for item in _hook_inventories(workspace, plugin)
             if item.representation is ACTIVATION_POLICIES[target_id].hook_representation]
    if len(exact) > 1:
        raise ValueError('multiple hook artifacts declare the same representation')
    return exact[0] if exact else None


def plan_install(
    workspace: Workspace, target_id: str, state_root: Path,
    destination: Path | None = None, adopt: bool = False,
    components: tuple[ComponentKind, ...] | None = None,
    project_root: Path | None = None,
    trust: tuple[str, ...] = (),
    repair: bool = True,
) -> InstallPlan:
    target = _target(workspace, target_id)
    selected = next(item for item in workspace.profile.targets if item.id == target.id)
    requested = components if components is not None else selected.components
    roots = _request_roots(workspace, target, destination, project_root)
    file = state_path(state_root, workspace.catalog.marketplace.id, str(workspace.profile.id), target_id)
    _recover_lifecycle(file, roots, repair, target_id)
    _, unsupported = project_paths(target.id, target.capabilities, requested, (), roots)
    rejected = {conflict.component for conflict in unsupported}
    exports = []
    for plugin in workspace.resolved.plugins:
        for component in requested:
            if component in rejected:
                continue
            if component is ComponentKind.HOOKS:
                inventories = _hook_inventories(workspace, plugin)
                exact = [item for item in inventories if item.representation is target.capabilities.get(component)]
                for inventory in exact or inventories:
                    # Empty bundles still carry an exact representation.
                    exports.extend(semantic_exports(inventory) or (ComponentExport(
                        plugin.plugin.id, component, '', inventory.root, inventory.representation),))
            else:
                artifact, _ = COMPONENT_ARTIFACTS[component]
                if artifact in plugin.artifact_paths:
                    inspector = workspace.registry.artifact_inspectors[artifact]
                    exports.extend(semantic_exports(inspector.inspect(plugin)))
    projected, conflicts = project_paths(target.id, target.capabilities, requested, tuple(exports), roots)
    desired = tuple(DesiredEntry(
        item.key, str(item.export.plugin_id), item.export.source, item.destination,
        item.export.component,
        DirectorySymlink() if item.export.component is ComponentKind.SKILLS else
        FileCopy(hashlib.sha256(_source_content(workspace.root, item.export.source)).hexdigest()),
    ) for item in projected)
    if target_id in ACTIVATION_POLICIES and ComponentKind.HOOKS in requested and ComponentKind.HOOKS not in rejected:
        for plugin in workspace.resolved.plugins:
            source = _hook_source(workspace, plugin, target_id)
            if source is not None:
                tree = bundles.source_tree(source)
                desired += (DesiredEntry(f"hook:{plugin.plugin.id}", str(plugin.plugin.id), source,
                    roots[ComponentKind.HOOKS] / str(plugin.plugin.id), ComponentKind.HOOKS,
                    DirectoryCopy(tree.digest)),)
    desired_by_key = {entry.key: entry for entry in desired}
    if len(desired_by_key) != len(desired) or len({entry.destination for entry in desired}) != len(desired):
        raise ValueError("desired installation keys and destinations must be unique")
    for entry in desired:
        _require_contained(roots[entry.component], entry.destination)
    previous = _validated_state(workspace, target_id, file, roots)
    ledger_before = shared_config.ownership_snapshot(roots[ComponentKind.HOOKS].parent, target_id) if target_id in ACTIVATION_POLICIES else None
    document_plans, slots, executable_sources = _plan_documents(workspace, target_id, roots, previous, tuple(item for item in requested if item not in rejected), adopt, trust, file, ledger_before)
    if document_plans:
        document_plans, ledger = shared_config.plan_ownership(file, document_plans, previous.slots if previous else {}, slots, ledger_before)
        document_plans += (ledger,)
    owned = previous.entries if previous else {}
    operations = [InstallOperation(OperationKind.CONFLICT, f"component:{conflict.component.value}",
                  roots.get(conflict.component, roots[ComponentKind.SKILLS]), None, conflict.reason,
                  component=conflict.component, diagnostic=conflict.code) for conflict in conflicts]
    next_entries: dict[str, StateEntry] = {}
    for entry in desired:
        prior = owned.get(entry.key)
        expected_link = None
        expected_digest = None
        same_owner = (prior is not None and Path(prior.destination) == entry.destination
                      and prior.component is entry.component
                      and type(prior.resource) is type(entry.resource))
        if _is_absent(entry.destination):
            kind, reason = OperationKind.CREATE, "destination is absent"
        elif same_owner and previous and not _matches_owned(prior):
            kind, reason = OperationKind.CONFLICT, "managed path was modified"
        elif _matches_resource(entry.destination, entry.source, entry.resource):
            if same_owner:
                kind, reason = OperationKind.KEEP, "managed resource already matches"
            elif adopt:
                kind, reason = OperationKind.ADOPT, "matching unmanaged resource explicitly adopted"
            else:
                kind, reason = OperationKind.CONFLICT, "matching resource is not recorded as owned; use --adopt"
        elif same_owner and previous and _matches_owned(prior):
            kind, reason = OperationKind.REPLACE_OWNED, "managed resource has a new source"
            if isinstance(prior.resource, (FileCopy, DirectoryCopy)):
                expected_digest = prior.resource.digest
            else:
                expected_link = entry.destination.readlink()
        else:
            kind, reason = OperationKind.CONFLICT, "destination is a foreign file or link"
        token = None
        required = False
        if isinstance(entry.resource, DirectoryCopy):
            plugin = next(plugin for plugin in workspace.resolved.plugins if str(plugin.plugin.id) == entry.plugin_id)
            token = grant_token(plugin, ComponentKind.HOOKS, target_id,
                                shared_config.document_path(roots, ComponentKind.HOOKS, target_id), entry.resource.digest, workspace.root, entry.source)
            required = token not in trust and not (prior is not None and prior.trust == token and authenticated(prior, file))
            if required:
                kind, reason = OperationKind.CONFLICT, "content-bound grant required; pass --trust " + token
        operations.append(InstallOperation(kind, entry.key, entry.destination, entry.source, reason,
                          expected_link, entry.component, entry.resource, expected_digest,
                          "TRUST_REQUIRED" if required else None, token, required))
        next_entries[entry.key] = StateEntry(
            trust=token,
            key=entry.key,
            plugin=entry.plugin_id,
            source=entry.source.relative_to(workspace.root).as_posix(),
            destination=str(entry.destination),
            component=entry.component,
            resource=entry.resource,
            source_repository=str(workspace.root),
        )
        next_entries[entry.key] = reuse_proof(next_entries[entry.key], prior, file)
    for key, prior in sorted(owned.items()):
        if key not in desired_by_key:
            assert previous is not None
            operation = _removal(key, prior, "managed entry is no longer selected")
            operation = _protect_bundle_removal(operation, prior, document_plans, roots, target_id)
            if operation:
                operations.append(operation)
    plan = _make_plan(workspace, target_id, file, roots, previous, operations, next_entries)
    if document_plans or any(isinstance(entry.resource, DirectoryCopy) for entry in next_entries.values()):
        plan = replace(plan, next_state=replace(plan.next_state, schema_version=4, slots=slots, destination_roots={component: str(root) for component, root in roots.items()
                           if component in {ComponentKind.HOOKS, ComponentKind.MCP} or component in plan.next_state.destination_roots}),
                       documents=document_plans, executable_sources=executable_sources)
    return plan


def _plan_documents(workspace: Workspace, target_id: str, roots: dict[ComponentKind, Path],
                    previous: InstallState | None, requested: tuple[ComponentKind, ...],
                    adopt: bool, trust: tuple[str, ...], file: Path, ledger_before: shared_config.DocumentSnapshot | None) -> tuple[tuple[shared_config.DocumentPlan, ...], dict[str, SlotState], tuple[shared_config.DesiredSlot, ...]]:
    if target_id not in ACTIVATION_POLICIES:
        return (), {}, ()
    documents = []
    states: dict[str, SlotState] = {}
    sources = []
    old_slots = previous.slots if previous else {}
    conflicts = shared_config.ownership_conflicts(ledger_before, file, old_slots)
    if conflicts:
        for component in (ComponentKind.HOOKS, ComponentKind.MCP):
            if component not in roots:
                continue
            before = shared_config.snapshot(shared_config.document_path(roots, component, target_id))
            operations = tuple(item for item in conflicts if item.component is component)
            documents.append(shared_config.DocumentPlan(before, before.content, before.content, operations))
        return tuple(documents), {}, ()
    for component in (ComponentKind.HOOKS, ComponentKind.MCP):
        owned = {key: value for key, value in old_slots.items() if value.component is component}
        has_bundle = component is ComponentKind.HOOKS and previous and any(entry.component is component for entry in previous.entries.values())
        if component not in roots:
            continue
        if component not in requested and not owned and not has_bundle:
            continue
        path = shared_config.document_path(roots, component, target_id)
        desired = []
        if component in requested:
            for plugin in workspace.resolved.plugins:
                source = (_hook_source(workspace, plugin, target_id) if component is ComponentKind.HOOKS
                          else plugin.artifact_paths.get(COMPONENT_ARTIFACTS[component][0]))
                if source is None:
                    continue
                if component is ComponentKind.HOOKS:
                    tree = bundles.source_tree(source)
                    content_digest = tree.digest
                    hooks = shared_config.compile_hooks(tree, roots[component] / str(plugin.plugin.id), target_id)
                    values = [(f"{event}:{shared_config.digest(entry)}", entry)
                              for event, entries in hooks.items() for entry in entries]
                else:
                    content = _source_content(workspace.root, source)
                    content_digest = hashlib.sha256(content).hexdigest()
                    data = shared_config.parse(content)
                    shared_config.validate_standalone_mcp(data, source)
                    servers = data.get('mcpServers')
                    if not isinstance(servers, dict) or any(not isinstance(value, dict) for value in servers.values()):
                        raise ValueError('MCP requires an mcpServers object of objects')
                    values = list(servers.items())
                token = grant_token(plugin, component, target_id, path, content_digest, workspace.root, source)
                recorded = any(item.plugin == plugin.plugin.id and item.trust == token and authenticated(item, file) for item in owned.values())
                if component is ComponentKind.HOOKS and previous:
                    bundle = previous.entries.get(f"hook:{plugin.plugin.id}")
                    recorded = recorded or (bundle is not None and bundle.trust == token and authenticated(bundle, file))
                granted = token in trust or recorded
                for name, value in values:
                    key = f"{component.value}:{plugin.plugin.id}:{name}"
                    state = SlotState(key, str(plugin.plugin.id), component, str(path), name,
                                      shared_config.digest(value), token)
                    state = reuse_proof(state, owned.get(key), file)
                    desired.append(shared_config.DesiredSlot(state, value, source, content_digest, granted))
                # An empty hook bundle still requires a grant on its path.
                if values:
                    sources.extend(desired[-len(values):])
        document, next_slots = shared_config.plan_document(shared_config.snapshot(path), component,
                                                          tuple(desired), owned, adopt, target_id)
        documents.append(document)
        states.update(next_slots)
    return tuple(documents), states, tuple(sources)


def _recover_lifecycle(file: Path, roots: dict[ComponentKind, Path], repair: bool = True, target_id: str = "cursor") -> None:
    if ComponentKind.HOOKS in roots:
        from .transactions import recover
        try:
            if not repair:
                journal = roots[ComponentKind.HOOKS].parent / ACTIVATION_POLICIES[target_id].journal_name
                if shared_config.snapshot(journal).content is not None:
                    raise ValueError('pending recovery; run status for the owning profile before requesting trust')
                return
            recover(file, roots, target_id)
        except OSError as error:
            if any(parent.is_symlink() for parent in (roots[ComponentKind.HOOKS].parent,) + tuple(roots[ComponentKind.HOOKS].parent.parents)):
                raise ValueError("destination root may not contain a symlink") from error
            raise


def _current_entries(plan: InstallPlan) -> dict[str, StateEntry]:
    current = load_state(plan.state_file)
    if current != plan.previous_state:
        raise ValueError("installation state changed since planning")
    return dict(current.entries) if current is not None else {}


def _write_progress(plan: InstallPlan, entries: dict[str, StateEntry]) -> None:
    roots = dict(plan.previous_state.destination_roots) if plan.previous_state else {}
    roots.update(plan.next_state.destination_roots)
    roots = {component: root for component, root in roots.items()
             if component is ComponentKind.SKILLS or any(entry.component is component for entry in entries.values())}
    state = InstallState(
        schema_version=plan.next_state.schema_version,
        repository=plan.next_state.repository,
        marketplace=plan.next_state.marketplace,
        profile=plan.next_state.profile,
        target=plan.next_state.target,
        destination_root=plan.next_state.destination_root,
        entries=dict(entries),
        destination_roots=roots,
        slots=plan.previous_state.slots if plan.previous_state else {},
    )
    write_state(plan.state_file, state)


def _remove_created_resource(root: _DestinationRoot, operation: InstallOperation) -> None:
    assert operation.source is not None
    cleanup = InstallOperation(
        kind=OperationKind.REMOVE_OWNED,
        key=operation.key,
        destination=operation.destination,
        source=operation.source,
        reason="roll back newly created resource",
        expected_link=_relative_link(operation.source, operation.destination.parent) if isinstance(operation.resource, DirectorySymlink) else None,
        component=operation.component,
        resource=operation.resource,
        expected_digest=operation.resource.digest if isinstance(operation.resource, (FileCopy, DirectoryCopy)) else None,
    )
    moved = root.quarantine(cleanup)
    root.discard(moved)


def _validate_install_source(plan: InstallPlan, operation: InstallOperation) -> bytes | bundles.Tree | None:
    if operation.source is None:
        raise ValueError(f"install operation has no source: {operation.destination}")
    if isinstance(operation.resource, DirectorySymlink):
        validate_skill_tree(plan.repository, operation.source)
        return None
    if isinstance(operation.resource, DirectoryCopy):
        tree = bundles.source_tree(operation.source)
        if tree.digest != operation.resource.digest:
            raise ValueError(f"copy source changed since planning: {operation.source}")
        return tree
    content = _source_content(plan.repository, operation.source)
    if hashlib.sha256(content).hexdigest() != operation.resource.digest:
        raise ValueError(f"copy source changed since planning: {operation.source}")
    return content


def _apply_plan_locked(plan: InstallPlan, uninstall: bool = False) -> None:
    if plan.has_conflicts:
        raise ValueError("installation plan has conflicts")
    entries = _current_entries(plan)
    for operation in plan.operations:
        if operation.kind is not OperationKind.REMOVE_OWNED:
            _validate_install_source(plan, operation)
    with ExitStack() as stack:
        needed = {operation.destination.parent for operation in plan.operations}
        roots = {snapshot.destination: stack.enter_context(_DestinationRoot(snapshot, create=not uninstall))
                 for snapshot in plan.roots if snapshot.destination in needed}
        for operation in plan.operations:
            root = roots[operation.destination.parent]
            root.verify()
            if operation.kind in {OperationKind.KEEP, OperationKind.ADOPT}:
                _validate_install_source(plan, operation)
                assert operation.source is not None
                if not root.same_resource(operation):
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
                content = _validate_install_source(plan, operation)
                root.create(operation, content)
                try:
                    root.verify()
                except Exception:
                    _remove_created_resource(root, operation)
                    raise
                entries[operation.key] = plan.next_state.entries[operation.key]
                try:
                    _write_progress(plan, entries)
                except Exception:
                    _remove_created_resource(root, operation)
                    raise
                continue
            if operation.kind is OperationKind.REPLACE_OWNED:
                content = _validate_install_source(plan, operation)
                assert operation.source is not None
                old = root.quarantine(operation)
                created = False
                try:
                    root.verify()
                    root.create(operation, content)
                    created = True
                    root.verify()
                except Exception:
                    if created:
                        _remove_created_resource(root, operation)
                    root.restore(old, operation)
                    raise
                entries[operation.key] = plan.next_state.entries[operation.key]
                try:
                    _write_progress(plan, entries)
                except Exception:
                    try:
                        _remove_created_resource(root, operation)
                        root.restore(old, operation)
                    except Exception as rollback_error:
                        raise RuntimeError(
                            f"replacement rollback failed; previous entry remains in {old.directory_name}"
                        ) from rollback_error
                    raise
                root.discard(old)
    if uninstall:
        remove_state(plan.state_file)
    else:
        write_state(plan.state_file, plan.next_state)


def apply_plan(plan: InstallPlan) -> None:
    with _state_lock(plan.state_file):
        if plan.documents:
            from .transactions import apply_lifecycle
            apply_lifecycle(plan, False)
        else:
            _apply_plan_locked(plan)


def plan_uninstall(
    workspace: Workspace, target_id: str, state_root: Path,
    destination: Path | None = None, project_root: Path | None = None,
) -> InstallPlan:
    target = _target(workspace, target_id)
    roots = _request_roots(workspace, target, destination, project_root)
    file = state_path(state_root, workspace.catalog.marketplace.id, str(workspace.profile.id), target_id)
    _recover_lifecycle(file, roots, target_id=target_id)
    previous = _validated_state(workspace, target_id, file, roots)
    ledger_before = shared_config.ownership_snapshot(roots[ComponentKind.HOOKS].parent, target_id) if target_id in ACTIVATION_POLICIES else None
    documents, _, sources = _plan_documents(workspace, target_id, roots, previous, (), False, (), file, ledger_before)
    if documents:
        documents, ledger = shared_config.plan_ownership(file, documents, previous.slots if previous else {}, {}, ledger_before)
        documents += (ledger,)
    operations = []
    if previous:
        for key, entry in sorted(previous.entries.items()):
            operation = _removal(key, entry, "remove managed entry")
            operation = _protect_bundle_removal(operation, entry, documents, roots, target_id)
            if operation:
                operations.append(operation)
    plan = _make_plan(workspace, target_id, file, roots, previous, operations, {})
    if documents:
        plan = replace(plan, next_state=replace(plan.next_state, schema_version=4, destination_roots={component: str(root) for component, root in roots.items()
                           if component in {ComponentKind.HOOKS, ComponentKind.MCP} or component in plan.next_state.destination_roots}), documents=documents)
    return plan


def apply_uninstall(plan: InstallPlan) -> None:
    with _state_lock(plan.state_file):
        if plan.documents:
            from .transactions import apply_lifecycle
            apply_lifecycle(plan, True)
        else:
            _apply_plan_locked(plan, uninstall=True)
