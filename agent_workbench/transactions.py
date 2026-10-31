"""One Cursor document lock and a private rollback journal across paths and state.

The journal is durable before any activation changes. A retry by its owning
profile rolls back to the complete prior state. Other profiles fail closed until
that repair completes. Journal paths are checked against adapter roots afresh.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from contextlib import contextmanager, ExitStack
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .installation import InstallPlan, InstallOperation, _DestinationRoot

from . import bundles, shared_config
from .model import ComponentKind, DirectoryCopy, DirectorySymlink


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


def _capture(root: _DestinationRoot, operation: InstallOperation) -> dict[str, object] | None:
    from .installation import _read_regular
    try:
        os.stat(operation.destination.name, dir_fd=root.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if isinstance(operation.resource, DirectoryCopy):
        return {'tree': _tree_data(bundles.read_tree(operation.destination.name, root.fd))}
    if isinstance(operation.resource, DirectorySymlink):
        return {'link': str(root.readlink(operation))}
    return {'file': _pack(_read_regular(operation.destination.name, root.fd))}


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


def _restore_path(root: _DestinationRoot, operation: InstallOperation,
                  value: dict[str, object] | None, quarantine: str) -> None:
    current = _capture(root, operation)
    if _fingerprint(current) == _fingerprint(value):
        _discard_snapshot(root, quarantine)
        return
    name = operation.destination.name
    # Keep a partially deleted tree away from the active path, so another crash
    # during rollback still leaves either a complete snapshot or an absent path.
    _discard_snapshot(root, quarantine)
    if current is not None:
        bundles.rename_new(name, quarantine, root.fd)
        os.fsync(root.fd)
    if value is not None:
        if 'tree' in value:
            temporary = quarantine + '-tree'
            _discard_snapshot(root, temporary)
            bundles.write_tree(temporary, root.fd, _tree(value['tree']))
            bundles.rename_new(temporary, name, root.fd)
        elif 'link' in value:
            os.symlink(value['link'], name, dir_fd=root.fd)
        else:
            temporary = quarantine + '-file'
            _discard_snapshot(root, temporary)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=root.fd)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(_unpack(value['file']))
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, name, src_dir_fd=root.fd, dst_dir_fd=root.fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=root.fd)
    os.fsync(root.fd)
    _discard_snapshot(root, quarantine)


def _journal_path(roots: dict[ComponentKind, Path]) -> Path:
    return roots[ComponentKind.HOOKS].parent / '.agent-workbench-transaction.json'


@contextmanager
def _document_lock(base: Path, create: bool) -> Iterator[None]:
    from .installation import _DestinationRoot, DestinationSnapshot, _destination_anchor, _destination_identity
    try:
        anchor, identity = _destination_anchor(base)
    except OSError:
        raise ValueError('unsafe Cursor config directory')
    if not create and not base.exists():
        # There can be no document or journal yet. Apply will create and lock it.
        yield
        return
    snapshot = DestinationSnapshot(base, _destination_identity(base), anchor, identity)
    with _DestinationRoot(snapshot, create=create) as root:
        import fcntl
        fd = os.open('.agent-workbench.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=root.fd)
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('document lock must be a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FileExistsError(f'Cursor document is locked by another operation: {base}') from error
            root.verify()
            yield
            root.verify()
        finally:
            os.close(fd)


def recover(file: Path, roots: dict[ComponentKind, Path]) -> None:
    journal = _journal_path(roots)
    if shared_config.snapshot(journal).content is None:
        return
    from .installation import _state_lock
    with _state_lock(file), _document_lock(journal.parent, False):
        _recover_locked(file, roots)


def _recover_locked(file: Path, roots: dict[ComponentKind, Path]) -> None:
    from .installation import _DestinationRoot, DestinationSnapshot, InstallOperation, OperationKind, _destination_anchor, _destination_identity
    journal = _journal_path(roots)
    fd = bundles.open_directory(journal.parent)
    try:
        saved = shared_config.snapshot(journal, fd).content
        if saved is None:
            return
        data = shared_config.parse(saved)
        if data.get('version') != 1 or data.get('stateFile') != str(file):
            raise ValueError('pending Cursor recovery belongs to another profile; run status for its owning profile')
        state_before = _unpack(data['stateBefore'])
        state_after = _unpack(data['stateAfter'])
        if shared_config.snapshot(file).content not in (state_before, state_after):
            raise ValueError('recovery blocked by externally modified ownership state; journal retained')
        allowed_documents = {str(shared_config.document_path(roots, component)) for component in (ComponentKind.HOOKS, ComponentKind.MCP)}
        allowed_documents.add(str(journal.parent / '.agent-workbench-owners.json'))
        documents = data['documents']
        for item in documents:
            path = Path(item['path'])
            if str(path) not in allowed_documents:
                raise ValueError('journal document is outside adapter-authorized paths')
            current = shared_config.snapshot(path, fd).content
            if current not in [_unpack(item[key]) for key in ('before', 'removed', 'after')]:
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
                anchor, identity = _destination_anchor(path.parent)
                root = stack.enter_context(_DestinationRoot(DestinationSnapshot(path.parent, _destination_identity(path.parent), anchor, identity), True))
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
                records.append((root, operation, item))
            # Deactivate changing slots before undoing bundle mutations. Restore
            # old slots only after every prior bundle is back in place.
            for item in documents:
                shared_config.replace_document(Path(item['path']), _unpack(item['removed']), fd)
            for root, operation, item in reversed(records):
                _restore_path(root, operation, item['before'], item['quarantine'] + '-rollback')
                quarantine = item['quarantine']
                if not quarantine.startswith('.agent-workbench-quarantine-') or Path(quarantine).name != quarantine:
                    raise ValueError('invalid journal quarantine')
                try:
                    bundles.delete_tree(quarantine, root.fd)
                except FileNotFoundError:
                    pass
            for item in documents:
                shared_config.replace_document(Path(item['path']), _unpack(item['before']), fd)
            # The state path comes from the current API request, never the journal.
            from .state import remove_state
            before_state = _unpack(data['stateBefore'])
            if before_state is None:
                remove_state(file)
            else:
                state_fd = bundles.open_directory(file.parent)
                try:
                    shared_config.replace_document(file, before_state, state_fd)
                finally:
                    os.close(state_fd)
            os.unlink(journal.name, dir_fd=fd)
            os.fsync(fd)
    finally:
        os.close(fd)


def apply_lifecycle(plan: InstallPlan, uninstall: bool) -> None:
    from . import installation as install
    if plan.has_conflicts:
        raise ValueError('installation plan has conflicts')
    roots = {component: Path(root) for component, root in plan.next_state.destination_roots.items()}
    if plan.previous_state:
        roots.update({component: Path(root) for component, root in plan.previous_state.destination_roots.items()})
    roots[ComponentKind.HOOKS] = plan.documents[0].before.path.parent / '.agent-workbench-hooks'
    journal = _journal_path(roots)
    with _document_lock(journal.parent, True), ExitStack() as stack:
        _recover_locked(plan.state_file, roots)
        install._current_entries(plan)
        needed = {operation.destination.parent for operation in plan.operations} | {journal.parent}
        opened = {snapshot.destination: stack.enter_context(install._DestinationRoot(snapshot, True))
                  for snapshot in plan.roots if snapshot.destination in needed}
        doc_root = opened[journal.parent]
        for document in plan.documents:
            if shared_config.snapshot(document.before.path, doc_root.fd) != document.before:
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
        record = {'version': 1, 'stateFile': str(plan.state_file),
                  'stateBefore': _pack(plan.state_file.read_bytes() if plan.state_file.exists() else None),
                  'stateAfter': _pack(None if uninstall else shared_config.encode(plan.next_state.as_dict())),
                  'documents': [{'path': str(item.before.path), 'before': _pack(item.before.content),
                                 'removed': _pack(item.removed), 'after': _pack(item.after)} for item in plan.documents],
                  'paths': paths}
        shared_config.replace_document(journal, shared_config.encode(record), doc_root.fd)
        try:
            for document in plan.documents:
                if document.removed != document.before.content:
                    shared_config.replace_document(document.before.path, document.removed, doc_root.fd)
            for operation, item in zip(plan.operations, paths):
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
            for document in plan.documents:
                doc_root.verify()
                if shared_config.snapshot(document.before.path, doc_root.fd).content != document.removed:
                    raise ValueError('shared document changed during transaction')
                if document.after != document.removed:
                    shared_config.replace_document(document.before.path, document.after, doc_root.fd)
            for root in opened.values():
                root.verify()
                os.fsync(root.fd)
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
            _recover_locked(plan.state_file, roots)
            raise
