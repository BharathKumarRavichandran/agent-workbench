"""Logical Cursor slots, complete document revisions, and inert hook compilation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .installation import OperationKind

from .bundles import Tree, open_directory
from .model import ComponentKind
from .state import SlotState, _reject_duplicate_keys


def parse(content: bytes) -> dict[str, Any]:
    data = json.loads(content, object_pairs_hook=_reject_duplicate_keys,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON number')))
    if not isinstance(data, dict):
        raise ValueError('shared JSON document must be an object')
    return data


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class DocumentSnapshot:
    path: Path
    content: bytes | None
    revision: tuple[int, int, int, int, int] | None


def snapshot(path: Path, parent_fd: int | None = None) -> DocumentSnapshot:
    owned_fd = None
    try:
        if parent_fd is None:
            try:
                owned_fd = open_directory(path.parent)
            except FileNotFoundError:
                return DocumentSnapshot(path, None, None)
            parent_fd = owned_fd
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return DocumentSnapshot(path, None, None)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'shared document must be a regular file: {path}')
            content = handle.read()
            after = os.fstat(handle.fileno())
            revision = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            if revision(info) != revision(after):
                raise ValueError('shared document changed while reading')
            parse(content)
            return DocumentSnapshot(path, content, revision(info))
    finally:
        if owned_fd is not None:
            os.close(owned_fd)


def replace_document(path: Path, content: bytes | None, parent_fd: int) -> None:
    import secrets
    if content is None:
        try:
            os.unlink(path.name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    else:
        name = '.agent-workbench-json-' + secrets.token_hex(8)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    os.fsync(parent_fd)


def compile_hooks(tree: Tree, destination: Path) -> dict[str, list[dict[str, Any]]]:
    files = {item.path: item.content for item in tree.files}
    data = parse(files['hooks/hooks.json'])
    if type(data.get('version', 1)) is not int or data.get('version', 1) != 1 or not isinstance(data.get('hooks'), dict):
        raise ValueError('hooks require version 1 and a hooks object')
    # Replace only local references. Shell operators and the rest of the command
    # are retained verbatim. Interpreting or executing shell text is forbidden.
    local = re.compile(r'\$\{CURSOR_PLUGIN_ROOT\}(?:/[^\s\'";|&<>]*)?|(?<![\w/])(?:\.\.?/|hooks/)[^\s\'";|&<>]*')

    def command(text: str) -> str:
        if not isinstance(text, str) or not text:
            raise ValueError('hook command must be a non-empty string')
        def rewrite(match: re.Match[str]) -> str:
            value = match.group()
            if value.startswith('${CURSOR_PLUGIN_ROOT}'):
                relative = value[len('${CURSOR_PLUGIN_ROOT}/'):]
            elif value.startswith('./hooks/') or value.startswith('hooks/'):
                relative = value.removeprefix('./')
            else:
                relative = 'hooks/' + value
            relative = posixpath.normpath(relative)
            if relative.startswith('../') or relative not in files:
                raise ValueError('hook local file reference escapes or is absent from copied bundle')
            result = str(destination / relative)
            # Keep the surrounding quote, if any, and escape for that context.
            prefix = text[:match.start()]
            quote = None
            index = 0
            while index < len(prefix):
                character = prefix[index]
                if character == "\\" and quote != "'":
                    index += 2
                    continue
                if character in "\"'":
                    quote = None if quote == character else (character if quote is None else quote)
                index += 1
            if quote == '"':
                return result.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
            if quote == "'":
                return result.replace("'", "'\\''")
            return shlex.quote(result)
        # Absolute script paths cannot become managed references. System shell
        # executables are allowed; arbitrary absolute payload references are not.
        for token in shlex.split(text):
            if '/' in token and '://' not in token and not token.startswith(('./', '../', 'hooks/', '${CURSOR_PLUGIN_ROOT}', '/')):
                raise ValueError('hook unresolved local file reference escapes copied bundle')
            if token.startswith('/') and token not in {'/bin/sh', '/bin/bash', '/bin/zsh', '/usr/bin/env', '/usr/bin/python3'}:
                raise ValueError('hook absolute local reference escapes copied bundle')
        return local.sub(rewrite, text)

    result = copy.deepcopy(data['hooks'])
    for event, entries in result.items():
        if not event or not isinstance(entries, list):
            raise ValueError('hook events must map to arrays')
        for entry in entries:
            if not isinstance(entry, dict) or 'command' not in entry:
                raise ValueError('hook entry requires command')
            entry['command'] = command(entry['command'])
    return result


@dataclass(frozen=True)
class DesiredSlot:
    state: SlotState
    value: dict[str, Any]
    source: Path
    source_digest: str
    granted: bool


@dataclass(frozen=True)
class SlotOperation:
    kind: OperationKind
    key: str
    destination: Path
    component: ComponentKind
    slot: str
    reason: str
    diagnostic: str | None = None
    trust_token: str | None = None
    trust_required: bool = False
    source: None = None

    def as_dict(self) -> dict[str, object]:
        return {'kind': self.kind.value, 'key': self.key, 'component': self.component.value,
                'resourceKind': 'shared-json-slot', 'document': str(self.destination),
                'destination': str(self.destination), 'slot': self.slot, 'reason': self.reason,
                'diagnostic': self.diagnostic, 'trustToken': self.trust_token,
                'trustRequired': self.trust_required}


@dataclass(frozen=True)
class DocumentPlan:
    before: DocumentSnapshot
    removed: bytes | None
    after: bytes | None
    operations: tuple[SlotOperation, ...]


def document_path(roots: Mapping[ComponentKind, Path], component: ComponentKind) -> Path:
    base = roots[ComponentKind.HOOKS].parent
    return base / ('hooks.json' if component is ComponentKind.HOOKS else 'mcp.json')


def hook_document_references_bundle(content: bytes | None, bundle: Path) -> bool:
    """Return whether any still-active hook command names this managed bundle."""
    if content is None:
        return False
    data = parse(content)
    hooks = data.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('hooks must be an object')
    path = str(bundle)
    boundary = re.compile(
        r'(?<![\w/])' + re.escape(path) + r'(?=$|/|[\s\'";|&<>])'
    )
    for entries in hooks.values():
        if not isinstance(entries, list):
            raise ValueError('hook events must contain arrays')
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('hook entries must be objects')
            command = entry.get('command')
            if isinstance(command, str) and boundary.search(command):
                return True
    return False


def plan_document(before: DocumentSnapshot, component: ComponentKind,
                  desired: tuple[DesiredSlot, ...], owned: Mapping[str, SlotState],
                  adopt: bool) -> tuple[DocumentPlan, dict[str, SlotState]]:
    from .installation import OperationKind as Op
    original = parse(before.content) if before.content is not None else {}
    if not desired and not owned:
        return DocumentPlan(before, before.content, before.content, ()), {}
    data = copy.deepcopy(original)
    field = 'hooks' if component is ComponentKind.HOOKS else 'mcpServers'
    if field in data and not isinstance(data[field], dict):
        raise ValueError(f'{field} must be an object: {before.path}')
    if component is ComponentKind.HOOKS and 'version' in data and (type(data['version']) is not int or data['version'] != 1):
        raise ValueError('shared hooks document requires version 1')
    slots = data.setdefault(field, {})
    if component is ComponentKind.HOOKS and any(not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries) for entries in slots.values()):
        raise ValueError('hook events must contain object arrays')
    if component is ComponentKind.MCP and any(not isinstance(item, dict) for item in slots.values()):
        raise ValueError('MCP servers must be objects')
    baseline = copy.deepcopy(data)
    operations = []
    next_slots = {}
    wanted = {item.state.key: item for item in desired}
    # Remove the old exact entries first, including changed selections. A missing
    # owned slot is an external edit, not permission to claim a replacement.
    for key, old in sorted(owned.items()):
        if component is ComponentKind.MCP:
            matches = old.slot in slots and digest(slots[old.slot]) == old.digest
        else:
            event, _, fingerprint = old.slot.rpartition(':')
            entries = slots.get(event, [])
            matches = sum(digest(entry) == old.digest for entry in entries) == 1
        if not matches:
            operations.append(SlotOperation(Op.CONFLICT, key, before.path, component, old.slot,
                              'owned slot was externally modified or is ambiguous', 'OWNED_SLOT_MODIFIED'))
            continue
        if key in wanted and wanted[key].state == old:
            continue
        if component is ComponentKind.MCP:
            del slots[old.slot]
        else:
            slots[event] = [entry for entry in entries if digest(entry) != old.digest]
        if key not in wanted:
            operations.append(SlotOperation(Op.REMOVE_OWNED, key, before.path, component, old.slot, 'remove unchanged owned slot'))
    removed = encode(data) if data != baseline else before.content
    seen = set()
    for desired_slot in desired:
        item = desired_slot.state
        identity = item.slot
        collision = identity in seen
        seen.add(identity)
        prior = owned.get(item.key)
        if component is ComponentKind.MCP:
            exists = item.slot in slots
            matching = exists and digest(slots[item.slot]) == item.digest
            ambiguous = False
        else:
            event, _, fingerprint = item.slot.rpartition(':')
            entries = slots.get(event, [])
            count = sum(digest(entry) == item.digest for entry in entries)
            exists = count > 0
            matching = count == 1
            ambiguous = count > 1
        kind, reason, diagnostic = Op.CREATE, 'add shared document slot', None
        if collision or ambiguous:
            kind, reason, diagnostic = Op.CONFLICT, 'duplicate or ambiguous slot', 'SLOT_AMBIGUOUS'
        elif exists and not matching:
            kind, reason, diagnostic = Op.CONFLICT, 'slot contains foreign content', 'SLOT_COLLISION'
        elif prior is not None and prior == item:
            kind, reason = Op.KEEP, 'unchanged owned slot'
        elif matching:
            kind, reason = (Op.ADOPT, 'adopt matching unmanaged slot') if adopt else (Op.CONFLICT, 'matching unmanaged slot requires --adopt')
        if not desired_slot.granted:
            kind, reason, diagnostic = Op.CONFLICT, 'content-bound grant required; pass --trust ' + item.trust, 'TRUST_REQUIRED'
        operations.append(SlotOperation(kind, item.key, before.path, component, item.slot, reason,
                          diagnostic, item.trust, not desired_slot.granted))
        next_slots[item.key] = item
        if not exists and kind is not Op.CONFLICT:
            if component is ComponentKind.MCP:
                slots[item.slot] = desired_slot.value
            else:
                slots.setdefault(event, []).append(desired_slot.value)
    if desired and component is ComponentKind.HOOKS:
        data.setdefault('version', 1)
    after = encode(data) if data != original else before.content
    return DocumentPlan(before, removed, after, tuple(operations)), next_slots


def plan_ownership(base: Path, file: Path, documents: tuple[DocumentPlan, ...],
                   previous: Mapping[str, SlotState], desired: Mapping[str, SlotState]) -> tuple[tuple[DocumentPlan, ...], DocumentPlan]:
    """The ledger prevents two profile state files from adopting the same slot."""
    from .installation import OperationKind as Op
    before = snapshot(base / '.agent-workbench-owners.json')
    original = parse(before.content) if before.content is not None else {'version': 1, 'slots': {}}
    if set(original) != {'version', 'slots'} or original['version'] != 1 or not isinstance(original['slots'], dict):
        raise ValueError('invalid Cursor ownership ledger')
    for key, value in original['slots'].items():
        if not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(value, dict) or set(value) != {'owner', 'digest'} or not isinstance(value['owner'], str) or not re.fullmatch('[0-9a-f]{64}', value['digest']):
            raise ValueError('invalid Cursor ownership record')
    data = copy.deepcopy(original)
    identity = lambda item: digest([item.document, item.component.value, item.slot])
    for item in previous.values():
        key = identity(item)
        if key in data['slots'] and data['slots'][key]['owner'] == str(file):
            del data['slots'][key]
    rejected = set()
    for item in desired.values():
        key = identity(item)
        recorded = data['slots'].get(key)
        if recorded is not None and recorded['owner'] != str(file):
            rejected.add(item.key)
        else:
            data['slots'][key] = {'owner': str(file), 'digest': item.digest}
    from dataclasses import replace
    result = tuple(replace(document, operations=tuple(
        replace(operation, kind=Op.CONFLICT, reason='slot is owned by another profile', diagnostic='SLOT_OWNED')
        if operation.key in rejected else operation for operation in document.operations)) for document in documents)
    after = encode(data) if data != original else before.content
    return result, DocumentPlan(before, before.content, after, ())
