"""One target resource lock and an authenticated rollback journal across paths and state.

The journal is durable before any activation changes. A retry by its owning
profile rolls back to the complete prior state. Other profiles fail closed until
that repair completes. Journal paths are checked against adapter roots afresh.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .installation import InstallPlan, InstallOperation, _DestinationRoot

from . import bundles, shared_config
from .adapters.activation import ACTIVATION_POLICIES
from .model import ComponentKind, DirectoryCopy, DirectorySymlink


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _journal_key(file: Path, create: bool = False) -> bytes:
    """Private authority belongs to the exact profile state file, never the project."""
    parent = bundles.open_directory(file.parent)
    name = '.' + file.name + '.transaction-key'
    try:
        if create:
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(os.urandom(32))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fsync(parent)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size != 32):
                raise ValueError('unsafe private transaction key')
            key = handle.read(33)
            if len(key) != 32:
                raise ValueError('unsafe private transaction key')
            return key
    finally:
        os.close(parent)


def _authenticate(data: dict[str, object], file: Path) -> None:
    signature = data.pop('mac', None)
    try:
        key = _journal_key(file)
    except (OSError, ValueError) as error:
        raise ValueError('unauthenticated recovery journal; journal retained') from error
    expected = hmac.new(key, _canonical(data), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not re.fullmatch('[0-9a-f]{64}', signature) or not hmac.compare_digest(signature, expected):
        raise ValueError('unauthenticated recovery journal; journal retained')


def _journal_record(saved: bytes, file: Path) -> dict[str, object]:
    data = shared_config.parse(saved)
    if data.get('version') != 1 or data.get('stateFile') != str(file):
        raise ValueError('pending recovery belongs to another profile; run status for its owning profile')
    _authenticate(data, file)
    return data


def _pack(content: bytes | None) -> str | None:
    return base64.b64encode(content).decode() if content is not None else None


def _unpack(value: str | None) -> bytes | None:
    return base64.b64decode(value, validate=True) if value is not None else None


def _tree_data(tree: bundles.Tree) -> dict[str, object]:
    return {'directories': list(tree.directories),
            'files': [[item.path, item.mode, _pack(item.content)] for item in tree.files]}


def _tree(value: dict[str, object]) -> bundles.Tree:
    directories = tuple((name, mode) for name, mode in value['directories'])
    files = tuple(bundles.TreeFile(name, mode, _unpack(content)) for name, mode, content in value['files'])
    for name, mode in directories + tuple((item.path, item.mode) for item in files):
        if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name or not isinstance(mode, int) or mode < 0 or mode > 0o777:
            raise ValueError('unsafe journal bundle record')
    return bundles.Tree(directories, files)


def _path_revision(fd: int, name: str) -> tuple[int, ...] | None:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _capture_at(fd: int, operation: InstallOperation) -> dict[str, object] | None:
    from .installation import _read_regular
    name = operation.destination.name
    before = _path_revision(fd, name)
    if before is None:
        return None
    if isinstance(operation.resource, DirectoryCopy):
        value = {'tree': _tree_data(bundles.read_tree(name, fd))}
    elif isinstance(operation.resource, DirectorySymlink):
        value = {'link': os.readlink(name, dir_fd=fd)}
    else:
        value = {'file': _pack(_read_regular(name, fd))}
    if _path_revision(fd, name) != before:
        raise ValueError('managed path changed while reading; journal retained')
    return value


def _capture(root: _DestinationRoot, operation: InstallOperation) -> dict[str, object] | None:
    return _capture_at(root.fd, operation)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _discard_snapshot(root: _DestinationRoot, name: str) -> None:
    import stat
    try:
        info = os.stat(name, dir_fd=root.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        bundles.delete_tree(name, root.fd)
    else:
        os.unlink(name, dir_fd=root.fd)


@dataclass(frozen=True)
class _PathRestore:
    before: dict[str, object] | None
    expected: dict[str, object] | None
    allowed: tuple[object, ...]
    revision: tuple[int, ...] | None


def _restore_path(root: _DestinationRoot, operation: InstallOperation,
                  restore: _PathRestore, quarantine: str) -> None:
    value, expected, allowed = restore.before, restore.expected, restore.allowed

    def capture(name: str) -> dict[str, object] | None:
        return _capture(root, replace(operation, destination=operation.destination.with_name(name)))

    def check(content: object) -> None:
        if _fingerprint(content) not in {_fingerprint(item) for item in allowed}:
            raise ValueError('recovery blocked by modified managed path; journal retained')

    name = operation.destination.name
    current = capture(name)
    if (_fingerprint(current) != _fingerprint(expected)
            or _path_revision(root.fd, name) != restore.revision):
        raise ValueError('recovery blocked by modified managed path; journal retained')
    # A prior recovery may have captured an entry before a second crash.
    saved = capture(quarantine)
    if saved is not None:
        check(saved)
    if _fingerprint(current) != _fingerprint(value):
        if current is not None:
            if saved is not None:
                raise ValueError('recovery blocked by occupied path quarantine; journal retained')
            bundles.rename_new(name, quarantine, root.fd)
            os.fsync(root.fd)
            saved = capture(quarantine)
            captured_revision = _path_revision(root.fd, quarantine)
            if (_fingerprint(saved) != _fingerprint(expected)
                    or captured_revision is None or restore.revision is None
                    or captured_revision[:5] != restore.revision[:5]):
                # Return a captured foreign object only without replacement.
                try:
                    bundles.rename_new(quarantine, name, root.fd)
                    os.fsync(root.fd)
                except FileExistsError:
                    pass
                raise ValueError('recovery blocked by modified managed path during capture; journal retained')
        if value is not None:
            if 'tree' in value:
                temporary = quarantine + '-tree'
                staged = capture(temporary)
                if staged is not None:
                    if _fingerprint(staged) != _fingerprint(value):
                        raise ValueError('recovery blocked by modified staged path; journal retained')
                else:
                    bundles.write_tree(temporary, root.fd, _tree(value['tree']))
                bundles.rename_new(temporary, name, root.fd)
            elif 'link' in value:
                os.symlink(value['link'], name, dir_fd=root.fd)
            else:
                temporary = quarantine + '-file'
                staged = capture(temporary)
                if staged is not None and _fingerprint(staged) != _fingerprint(value):
                    raise ValueError('recovery blocked by modified staged path; journal retained')
                if staged is None:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=root.fd)
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(_unpack(value['file']))
                        handle.flush()
                        os.fsync(handle.fileno())
                bundles.rename_new(temporary, name, root.fd)
        elif capture(name) is not None:
            raise ValueError('recovery blocked by new managed path; journal retained')
    os.fsync(root.fd)
    # Only discard authenticated captured content after publication is durable.
    if _fingerprint(capture(quarantine)) != _fingerprint(saved):
        raise ValueError('recovery blocked by modified path quarantine; journal retained')
    _discard_snapshot(root, quarantine)
    os.fsync(root.fd)


def _discard_path_evidence(root: _DestinationRoot, operation: InstallOperation,
                           item: dict[str, object]) -> None:
    name = item['quarantine']
    try:
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root.fd)
    except FileNotFoundError:
        return
    try:
        if set(os.listdir(child)) - {'entry'}:
            raise ValueError('recovery blocked by foreign path evidence; journal retained')
        captured = _capture_at(child, replace(operation, destination=operation.destination.with_name('entry')))
        before = item['before']
        if captured is not None and _fingerprint(captured) != _fingerprint(before):
            # Apply may have died part way through deleting the old bundle.
            if before is None or 'tree' not in captured or 'tree' not in before:
                raise ValueError('recovery blocked by modified path evidence; journal retained')
            partial, complete = _tree(captured['tree']), _tree(before['tree'])
            if (not set(partial.directories) <= set(complete.directories)
                    or not set(partial.files) <= set(complete.files)):
                raise ValueError('recovery blocked by modified path evidence; journal retained')
    finally:
        os.close(child)
    bundles.delete_tree(name, root.fd)
    os.fsync(root.fd)


def _journal_path(roots: dict[ComponentKind, Path], target_id: str = "cursor") -> Path:
    return roots[ComponentKind.HOOKS].parent / ACTIVATION_POLICIES[target_id].journal_name


@contextmanager
def _document_lock(base: Path, create: bool, target_id: str = "cursor") -> Iterator[None]:
    from .installation import _DestinationRoot, DestinationSnapshot, _destination_anchor, _destination_identity
    try:
        anchor, identity = _destination_anchor(base)
    except OSError:
        raise ValueError('unsafe target config directory')
    if not create and not base.exists():
        # There can be no document or journal yet. Apply will create and lock it.
        yield
        return
    snapshot = DestinationSnapshot(base, _destination_identity(base), anchor, identity)
    with _DestinationRoot(snapshot, create=create) as root:
        import fcntl
        fd = os.open(ACTIVATION_POLICIES[target_id].lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=root.fd)
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('document lock must be a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FileExistsError(f'target document is locked by another operation: {base}') from error
            root.verify()
            yield
            root.verify()
        finally:
            os.close(fd)


def recover(file: Path, roots: dict[ComponentKind, Path], target_id: str = "cursor") -> None:
    journal = _journal_path(roots, target_id)
    saved = shared_config.snapshot(journal).content
    if saved is None:
        return
    # Reject untrusted project bytes without even creating a config/state lock.
    # Read and authenticate again under both locks before any restoration.
    _journal_record(saved, file)
    from .installation import _state_lock
    with _state_lock(file), _document_lock(journal.parent, False, target_id):
        _recover_locked(file, roots, target_id)


def _document_quarantines(item: dict[str, object]) -> tuple[str, ...]:
    name = item.get('quarantine')
    if not isinstance(name, str) or not re.fullmatch(r'\.agent-workbench-document-[0-9a-f]{16}', name):
        raise ValueError('invalid journal document quarantine')
    return tuple(name + suffix for suffix in ('-remove', '-activate', '-recover-remove', '-recover-before'))


def _check_document_quarantines(item: dict[str, object], fd: int) -> bool:
    from .installation import _read_regular
    evidence = False
    allowed = [_unpack(item[key]) for key in ('before', 'removed', 'after')]
    for name in _document_quarantines(item):
        try:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            continue
        try:
            if set(os.listdir(child)) - {'entry', 'new', 'recovery'}:
                raise ValueError('recovery blocked by foreign document quarantine; journal retained')
            for entry in os.listdir(child):
                if entry in {'new', 'recovery'}:
                    # Staging may have stopped mid-write; only a prefix of an
                    # authenticated planned document can be our partial temp.
                    partial = _read_regular(entry, child)
                    matches = any(value is not None and value.startswith(partial) for value in allowed)
                else:
                    matches = shared_config.snapshot(Path(entry), child).content in allowed
                    evidence = True
                if not matches:
                    raise ValueError('recovery blocked by modified document quarantine; journal retained')
        finally:
            os.close(child)
    return evidence


def _prepare_document_recovery(item: dict[str, object], current: shared_config.DocumentSnapshot,
                               fd: int) -> shared_config.DocumentSnapshot:
    if shared_config.snapshot(current.path, fd) != current:
        raise ValueError('recovery blocked by modified document; journal retained')
    evidence = _check_document_quarantines(item, fd)
    if current.content is None and evidence:
        # Publish the deactivated document from authenticated journal bytes.
        # The captured entry remains evidence through publication AND fsync.
        content = _unpack(item['removed'])
        if content is None:
            content = _unpack(item['before'])
        if content is not None:
            for name in _document_quarantines(item):
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    continue
                try:
                    try:
                        os.unlink('recovery', dir_fd=child)
                    except FileNotFoundError:
                        pass
                    out = os.open('recovery', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child)
                    with os.fdopen(out, 'wb') as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    bundles.rename_new('recovery', current.path.name, child, fd)
                    os.fsync(fd)
                    current = shared_config.snapshot(current.path, fd)
                    if current.content != content:
                        raise ValueError('recovery blocked by modified document publication; journal retained')
                    break
                finally:
                    os.close(child)
    os.fsync(fd)
    _check_document_quarantines(item, fd)
    for name in _document_quarantines(item):
        try:
            bundles.delete_tree(name, fd)
        except FileNotFoundError:
            pass
    os.fsync(fd)
    return current


class _RecoveryParents:
    """Hold and revalidate every authenticated document/resource parent."""
    def __init__(self) -> None:
        self.fds = {}
        self.identities = {}

    def open(self, path: Path, expected: tuple[int, int] | None) -> None:
        fd = bundles.open_directory(path)
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        if expected is not None and identity != expected:
            os.close(fd)
            raise ValueError('recovery parent identity changed; journal retained')
        if path in self.fds:
            os.close(fd)
            if self.identities[path] != identity:
                raise ValueError('parent identity changed; journal retained')
            return
        self.fds[path] = fd
        self.identities[path] = identity

    def verify(self) -> None:
        for path, identity in self.identities.items():
            fd = bundles.open_directory(path)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != identity:
                    raise ValueError('recovery parent identity changed; journal retained')
            finally:
                os.close(fd)

    def get(self, path: Path) -> int:
        self.verify()
        return self.fds[path]

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)


def _recover_locked(file: Path, roots: dict[ComponentKind, Path], target_id: str = "cursor") -> None:
    from .installation import _DestinationRoot, DestinationSnapshot, InstallOperation, OperationKind
    journal = _journal_path(roots, target_id)
    fd = bundles.open_directory(journal.parent)
    parents = _RecoveryParents()
    try:
        saved = shared_config.snapshot(journal, fd).content
        if saved is None:
            return
        data = _journal_record(saved, file)
        state_before = _unpack(data['stateBefore'])
        state_after = _unpack(data['stateAfter'])
        state_item = {'path': str(file), 'before': data['stateBefore'],
                      'removed': data['stateBefore'], 'after': data['stateAfter'],
                      'quarantine': '.agent-workbench-document-' + hashlib.sha256(str(file).encode()).hexdigest()[:16]}
        state_fd = bundles.open_directory(file.parent)
        try:
            state_evidence = _check_document_quarantines(state_item, state_fd)
            state_snapshot = shared_config.snapshot(file, state_fd)
        finally:
            os.close(state_fd)
        if state_snapshot.content not in (state_before, state_after) and not (state_snapshot.content is None and state_evidence):
            raise ValueError('recovery blocked by externally modified ownership state; journal retained')
        allowed_documents = {str(shared_config.document_path(roots, component, target_id)) for component in (ComponentKind.HOOKS, ComponentKind.MCP) if component in roots}
        allowed_documents.add(str(journal.parent / ACTIVATION_POLICIES[target_id].ledger_name))
        documents = data['documents']
        authorized_parents = {Path(path).parent for path in allowed_documents} | set(roots.values()) | {file.parent}
        evidence = data.get('parents')
        if evidence is None and target_id != 'cursor':
            raise ValueError('missing authenticated parent identities; journal retained')
        for path, identity in (evidence or {}).items():
            if Path(path) not in authorized_parents:
                raise ValueError('journal parent is outside adapter-authorized roots')
            parents.open(Path(path), tuple(identity))
        required = ({Path(item['path']).parent for item in documents}
                    | {Path(item['path']).parent for item in data['paths']} | {file.parent, journal.parent})
        if evidence is not None and not required <= set(parents.fds):
            raise ValueError('missing authenticated parent identities; journal retained')
        for path in required:
            if path not in parents.fds:
                parents.open(path, None)  # Legacy authenticated Cursor journals.
        journal_info = os.fstat(fd)
        if (journal_info.st_dev, journal_info.st_ino) != parents.identities[journal.parent]:
            raise ValueError('recovery journal parent changed; journal retained')
        document_snapshots = {}
        for item in documents:
            path = Path(item['path'])
            if str(path) not in allowed_documents:
                raise ValueError('journal document is outside adapter-authorized paths')
            parent_fd = parents.get(path.parent)
            quarantined = _check_document_quarantines(item, parent_fd)
            captured = shared_config.snapshot(path, parent_fd)
            document_snapshots[str(path)] = captured
            current = captured.content
            if current not in [_unpack(item[key]) for key in ('before', 'removed', 'after')] and not (current is None and quarantined):
                raise ValueError('recovery blocked by externally modified document; journal retained')
        with ExitStack() as stack:
            records = []
            for item in data['paths']:
                component = ComponentKind(item['component'])
                path = Path(item['path'])
                if component not in roots or path.parent != roots[component] or path.name in {'', '.', '..'}:
                    raise ValueError('journal path is outside adapter-authorized roots')
                if component is ComponentKind.MCP or (component is ComponentKind.HOOKS and item['resource'] != 'directory-copy'):
                    raise ValueError('journal resource disagrees with component')
                identity = parents.identities[path.parent]
                root = stack.enter_context(_DestinationRoot(DestinationSnapshot(path.parent, identity, path.parent, identity), False))
                from .model import FileCopy
                resource = (DirectoryCopy('') if item['resource'] == 'directory-copy' else
                            DirectorySymlink() if item['resource'] == 'directory-symlink' else FileCopy(''))
                operation = InstallOperation(OperationKind.KEEP, '', path, None, '', component=component, resource=resource)
                quarantine = item['quarantine']
                if not re.fullmatch(r'\.agent-workbench-quarantine-[0-9a-f]{16}', quarantine):
                    raise ValueError('invalid journal quarantine')
                for evidence in (item['before'], item['after']):
                    if evidence is not None:
                        expected_field = {'directory-copy': 'tree', 'directory-symlink': 'link', 'file-copy': 'file'}.get(item['resource'])
                        if expected_field is None or set(evidence) != {expected_field}:
                            raise ValueError('invalid journal resource evidence')
                        if expected_field == 'tree':
                            _tree(evidence['tree'])
                        elif expected_field == 'file':
                            _unpack(evidence['file'])
                current = _capture(root, operation)
                if _fingerprint(current) not in {_fingerprint(item['before']), _fingerprint(item['after']), _fingerprint(None)}:
                    raise ValueError('recovery blocked by modified managed path; journal retained')
                records.append((root, operation, item, current, _path_revision(root.fd, path.name)))
            # Deactivate changing slots before undoing bundle mutations. Restore
            # old slots only after every prior bundle is back in place.
            for item in documents:
                fd_document = parents.get(Path(item['path']).parent)
                current = document_snapshots[item['path']]
                # Restore an interrupted document durably before discarding
                # its capture evidence. Keep hooks deactivated during rollback.
                current = _prepare_document_recovery(item, current, fd_document)
                document_snapshots[item['path']] = current
                if current.content != _unpack(item['removed']):
                    document_snapshots[item['path']] = shared_config.compare_replace_document(
                        current, _unpack(item['removed']), fd_document, item['quarantine'] + '-recover-remove')
                elif shared_config.snapshot(current.path, fd_document) != current:
                    raise ValueError('recovery blocked by modified document; journal retained')
            for root, operation, item, current, revision in reversed(records):
                parents.verify()
                _restore_path(root, operation, _PathRestore(item['before'], current,
                              (item['before'], item['after'], None), revision), item['quarantine'] + '-rollback')
                quarantine = item['quarantine']
                if not quarantine.startswith('.agent-workbench-quarantine-') or Path(quarantine).name != quarantine:
                    raise ValueError('invalid journal quarantine')
                _discard_path_evidence(root, operation, item)
            for item in documents:
                fd_document = parents.get(Path(item['path']).parent)
                current = document_snapshots[item['path']]
                if current.content != _unpack(item['before']):
                    shared_config.compare_replace_document(current, _unpack(item['before']), fd_document,
                                                           item['quarantine'] + '-recover-before')
            # The state path comes from the current API request, never the journal.
            state_fd = os.dup(parents.get(file.parent))
            try:
                if shared_config.snapshot(file, state_fd) != state_snapshot:
                    raise ValueError('recovery blocked by externally modified ownership state; journal retained')
                current_state = _prepare_document_recovery(state_item, state_snapshot, state_fd)
                if current_state.content != state_before:
                    shared_config.compare_replace_document(current_state, state_before, state_fd,
                        state_item['quarantine'] + '-recover-before')
            finally:
                os.close(state_fd)
            parents.verify()
            os.unlink(journal.name, dir_fd=fd)
            os.fsync(fd)
    finally:
        parents.close()
        os.close(fd)


def apply_lifecycle(plan: InstallPlan, uninstall: bool) -> None:
    from . import installation as install
    if plan.has_conflicts:
        raise ValueError('installation plan has conflicts')
    roots = {component: Path(root) for component, root in plan.next_state.destination_roots.items()}
    if plan.previous_state:
        roots.update({component: Path(root) for component, root in plan.previous_state.destination_roots.items()})
    target_id = str(plan.target)
    journal = _journal_path(roots, target_id)
    with _document_lock(journal.parent, True, target_id), ExitStack() as stack:
        _recover_locked(plan.state_file, roots, target_id)
        install._current_entries(plan)
        needed = ({operation.destination.parent for operation in plan.operations}
                  | {document.before.path.parent for document in plan.documents} | {journal.parent})
        if target_id == 'claude' and ComponentKind.MCP in roots:
            needed.add(roots[ComponentKind.MCP])
        opened = {snapshot.destination: stack.enter_context(install._DestinationRoot(snapshot, True))
                  for snapshot in plan.roots if snapshot.destination in needed}
        doc_root = opened[journal.parent]
        parents = stack.enter_context(_RecoveryParents())
        for path, root in opened.items():
            parents.open(path, root.identity)
        parents.open(plan.state_file.parent, None)
        for document in plan.documents:
            if shared_config.snapshot(document.before.path, opened[document.before.path.parent].fd) != document.before:
                raise ValueError('shared document changed since planning')
        for item in plan.executable_sources:
            if item.state.component is ComponentKind.HOOKS:
                actual = bundles.source_tree(item.source).digest
            else:
                actual = hashlib.sha256(install._source_content(plan.repository, item.source)).hexdigest()
            if actual != item.source_digest:
                raise ValueError('executable source changed since planning')
        paths = []
        contents = {}
        for operation in plan.operations:
            root = opened[operation.destination.parent]
            if operation.kind is not install.OperationKind.REMOVE_OWNED:
                contents[operation.key] = install._validate_install_source(plan, operation)
            before = _capture(root, operation)
            if operation.kind in {install.OperationKind.KEEP, install.OperationKind.ADOPT}:
                if not root.same_resource(operation):
                    raise install._changed(operation)
            if operation.kind is install.OperationKind.CREATE and before is not None:
                raise install._changed(operation)
            if operation.kind in {install.OperationKind.REMOVE_OWNED, install.OperationKind.REPLACE_OWNED}:
                if isinstance(operation.resource, DirectorySymlink):
                    matches = before is not None and before.get('link') == str(operation.expected_link)
                elif isinstance(operation.resource, DirectoryCopy):
                    matches = before is not None and _tree(before['tree']).digest == operation.expected_digest
                else:
                    matches = before is not None and hashlib.sha256(_unpack(before['file'])).hexdigest() == operation.expected_digest
                if not matches:
                    raise install._changed(operation)
            if operation.kind is install.OperationKind.REMOVE_OWNED:
                after = None
            elif isinstance(operation.resource, DirectoryCopy):
                after = {'tree': _tree_data(contents[operation.key])}
            elif isinstance(operation.resource, DirectorySymlink):
                after = {'link': str(install._relative_link(operation.source, operation.destination.parent))}
            else:
                after = {'file': _pack(contents[operation.key])}
            paths.append({'path': str(operation.destination), 'component': operation.component.value,
                          'resource': operation.resource.kind, 'before': before, 'after': after,
                          'quarantine': '.agent-workbench-quarantine-' + os.urandom(8).hex()})
        parents.verify()
        for document in plan.documents:
            if shared_config.snapshot(document.before.path, opened[document.before.path.parent].fd) != document.before:
                raise ValueError('shared document changed since planning')
        from .trust import record_proof
        signed_entries = {key: replace(item, trust_proof=record_proof(item, plan.state_file, True))
                          if isinstance(item.resource, DirectoryCopy) else item
                          for key, item in plan.next_state.entries.items()}
        signed_slots = {key: replace(item, trust_proof=record_proof(item, plan.state_file, True))
                        for key, item in plan.next_state.slots.items()}
        plan = replace(plan, next_state=replace(plan.next_state, entries=signed_entries, slots=signed_slots))
        record = {'version': 1, 'stateFile': str(plan.state_file),
                  'parents': {str(path): list(identity) for path, identity in parents.identities.items()},
                  'stateBefore': _pack(plan.state_file.read_bytes() if plan.state_file.exists() else None),
                  'stateAfter': _pack(None if uninstall else shared_config.encode(plan.next_state.as_dict())),
                  'documents': [{'path': str(item.before.path), 'before': _pack(item.before.content),
                                 'removed': _pack(item.removed), 'after': _pack(item.after),
                                 'quarantine': '.agent-workbench-document-' + os.urandom(8).hex()} for item in plan.documents],
                  'paths': paths}
        record['mac'] = hmac.new(_journal_key(plan.state_file, True), _canonical(record), hashlib.sha256).hexdigest()
        parents.verify()
        shared_config.replace_document(journal, shared_config.encode(record), doc_root.fd)
        try:
            document_snapshots = {str(document.before.path): document.before for document in plan.documents}
            for document, item in zip(plan.documents, record['documents']):
                for root in opened.values():
                    root.verify()
                if document.removed != document.before.content:
                    document_snapshots[str(document.before.path)] = shared_config.compare_replace_document(
                        document.before, document.removed, opened[document.before.path.parent].fd, item['quarantine'] + '-remove')
            for operation, item in zip(plan.operations, paths):
                parents.verify()
                for document in plan.documents:
                    current = document_snapshots[str(document.before.path)]
                    if shared_config.snapshot(current.path, opened[current.path.parent].fd) != current:
                        raise ValueError('shared document changed during transaction')
                root = opened[operation.destination.parent]
                root.verify()
                if operation.kind in {install.OperationKind.KEEP, install.OperationKind.ADOPT}:
                    continue
                if operation.kind in {install.OperationKind.REMOVE_OWNED, install.OperationKind.REPLACE_OWNED}:
                    moved = root.quarantine(operation, item['quarantine'])
                    root.discard(moved)
                if operation.kind is not install.OperationKind.REMOVE_OWNED:
                    root.create(operation, contents[operation.key])
                root.verify()
            for root in opened.values():
                root.verify()
            for operation in plan.operations:
                if operation.kind is not install.OperationKind.REMOVE_OWNED and not opened[operation.destination.parent].same_resource(operation):
                    raise install._changed(operation)
            for document, item in zip(plan.documents, record['documents']):
                parents.verify()
                parent = opened[document.before.path.parent]
                parent.verify()
                current = document_snapshots[str(document.before.path)]
                if shared_config.snapshot(current.path, parent.fd) != current:
                    raise ValueError('shared document changed during transaction')
                if document.after != document.removed:
                    shared_config.compare_replace_document(current, document.after, parent.fd, item['quarantine'] + '-activate')
            for root in opened.values():
                root.verify()
                os.fsync(root.fd)
            parents.verify()
            if uninstall:
                install.remove_state(plan.state_file)
            else:
                install.write_state(plan.state_file, plan.next_state)
            os.fsync(doc_root.fd)
            os.unlink(journal.name, dir_fd=doc_root.fd)
            os.fsync(doc_root.fd)
        except Exception:
            # If repair also fails, the durable intent and backups survive. The
            # next status/plan/apply must repair before it can plan new work.
            _recover_locked(plan.state_file, roots, target_id)
            raise
