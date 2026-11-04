#!/usr/bin/env python3
"""Private D5 path differential contract; no real config, credentials or public CLI."""
from __future__ import annotations
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from agent_workbench import transactions as tx, bundles
from agent_workbench.model import DirectoryCopy, DirectorySymlink, FileCopy

@dataclass(frozen=True)
class Operation:
    destination: Path
    resource: object


def value(kind: str, text: str) -> dict[str, Any]:
    if kind == 'file': return {'file': base64.b64encode(text.encode()).decode()}
    if kind == 'link': return {'link': text}
    return {'tree': {'directories': [['', 0o751], ['sub', 0o750]],
                     'files': [['sub/data', 0o640, base64.b64encode(text.encode()).decode()]]}}


def create(root, name, snapshot):
    if snapshot is None: return
    fd = bundles.open_directory(root)
    try:
        if 'tree' in snapshot: bundles.write_tree(name, fd, tx._tree(snapshot['tree']))
        elif 'link' in snapshot: os.symlink(snapshot['link'], name, dir_fd=fd)
        else:
            (root/name).write_bytes(tx._unpack(snapshot['file']))
            (root/name).chmod(0o600)
    finally: os.close(fd)


def state(root):
    result = []
    if not root.exists(): return result
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        kind = 'link' if stat.S_ISLNK(s.st_mode) else 'dir' if p.is_dir() else 'file'
        if kind == 'file':
            # Observe unreadable mode-0/1 fixture bytes without changing the
            # permissions retained by either implementation.
            try:
                p.chmod(stat.S_IMODE(s.st_mode) | 0o400)
                data = p.read_bytes()
                content, byte_length = data.hex(), len(data)
            finally: p.chmod(stat.S_IMODE(s.st_mode))
        else:
            content = os.readlink(p) if kind == 'link' else None
            byte_length = None
        result.append((str(p.relative_to(root)), kind, stat.S_IMODE(s.st_mode), byte_length, content))
    return result


def error_kind(error):
    if error is None: return None
    for word in ['modified managed path during capture', 'modified managed path', 'occupied path quarantine',
                 'modified staged path', 'new managed path', 'modified path quarantine',
                 'foreign path evidence', 'modified path evidence', 'injected']:
        if word in error: return word
    if '[Errno 17]' in error or 'exist' in error.lower() or 'not empty' in error.lower(): return 'exists'
    return 'os-error'


def python_run(case):
    root = Path(case['root']); fd = bundles.open_directory(root)
    resource = {'file': FileCopy(''), 'tree': DirectoryCopy(''), 'link': DirectorySymlink()}[case['kind']]
    op = Operation(root/'item', resource)
    before = case['before']; expected = tx._capture_at(fd, op); revision = tx._path_revision(fd, 'item')
    if case.get('stale'): (root/'item').write_bytes(b'after')
    if case.get('detached'):
        root.rename(root.with_suffix('.detached')); root.mkdir()
    restore = tx._PathRestore(before, case.get('expected', expected), tuple(case.get('allowed', (before, expected))), revision)
    original_rename = bundles.rename_new; original_sync = os.fsync; original_symlink = os.symlink
    original_chmod = os.fchmod; original_fdopen = os.fdopen
    root_info = os.fstat(fd)
    root_identity = (root_info.st_dev, root_info.st_ino)
    sync_count = 0
    class Writer:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): self.handle.__enter__(); return self
        def __exit__(self, *args): return self.handle.__exit__(*args)
        def __getattr__(self, name): return getattr(self.handle, name)
        def write(self, data):
            edge('tree-file-write' if case['kind'] == 'tree' else 'file-write')
            return self.handle.write(data)
    def fdopen(*args, **kwargs): return Writer(original_fdopen(*args, **kwargs))
    def chmod(handle, mode):
        directory = stat.S_ISDIR(os.fstat(handle).st_mode)
        # delete_tree's permission widening is not a staging chmod.
        if (root/'q-tree').exists(): edge('tree-directory-chmod' if directory else 'tree-file-chmod')
        return original_chmod(handle, mode)
    fired = False
    edge_count = 0
    def edge(which):
        nonlocal fired, edge_count
        if fired or which != case.get('edge'): return
        edge_count += 1
        if edge_count != case.get('occurrence', 1): return
        fired = True
        if case.get('action') == 'fail': raise OSError('injected')
        if case.get('action') == 'foreign':
            (root/'item').unlink(missing_ok=True); (root/'item').write_bytes(b'foreign')
    def rename(source, destination, parent, *args):
        edge('capture' if source == 'item' else 'publish')
        return original_rename(source, destination, parent, *args)
    tree_sync_count = 0
    def sync(parent):
        nonlocal sync_count, tree_sync_count
        info = os.fstat(parent)
        if (info.st_dev, info.st_ino) == root_identity:
            sync_count += 1
            if case.get('discard'): edge('evidence-sync')
            elif sync_count == 1 and expected is not None and expected != before: edge('capture-sync')
            elif os.path.lexists(root/'q'): edge('publish-sync')
            else: edge('evidence-sync')
        elif stat.S_ISREG(info.st_mode):
            edge('tree-file-sync' if case['kind'] == 'tree' else 'file-sync')
        elif (root/'q-tree').exists():
            tree_sync_count += 1
            edge('tree-directory-sync' if tree_sync_count <= len(before['tree']['directories']) else 'tree-root-sync')
        if case.get('edge') == 'sync' and not (root/'q-file').exists() and (root/'item').exists(): edge('sync')
        return original_sync(parent)
    def symlink(*args, **kwargs):
        edge('publish')
        return original_symlink(*args, **kwargs)
    error = retry_error = None
    try:
        with patch.object(bundles, 'rename_new', rename), patch.object(os, 'fsync', sync), patch.object(os, 'symlink', symlink), patch.object(os, 'fchmod', chmod), patch.object(os, 'fdopen', fdopen):
            if case.get('discard'): tx._discard_path_evidence(SimpleNamespace(fd=fd), op, {'quarantine':'q','before':before})
            else: tx._restore_path(SimpleNamespace(fd=fd), op, restore, 'q')
    except (ValueError,OSError) as e: error = str(e)
    first_state = state(root)
    if case.get('retry'):
        try:
            if case.get('discard'):
                tx._discard_path_evidence(SimpleNamespace(fd=fd), op, {'quarantine':'q','before':before})
            else:
                current = tx._capture_at(fd,op)
                restore = tx._PathRestore(before,current,(before,current,expected),tx._path_revision(fd,'item'))
                tx._restore_path(SimpleNamespace(fd=fd),op,restore,'q')
        except (ValueError,OSError) as e: retry_error = str(e)
    os.close(fd)
    return {'error':error,'retry_error':retry_error,'captured':expected,'fired':fired,'first_state':first_state}


def main():
    os.umask(0o022)
    # tempfile respects the caller's TMPDIR and uses its platform default when
    # absent. Never overwrite it with a developer-machine-specific path.
    scratch = None
    subprocess.run(['cargo','test','--manifest-path',str(REPO/'rust/Cargo.toml'),'--lib','--no-run'],check=True)
    cases = []
    for kind in ['file','tree','link']:
        old, new = value(kind,'before'), value(kind,'after')
        for label, before, current in [('replace',old,new),('already',old,old),('absent',None,new),('restore-absent',old,None),('empty',None,None)]:
            cases.append(dict(label=f'{kind}-{label}',kind=kind,before=before,current=current))
        cases.append(dict(label=f'{kind}-occupied',kind=kind,before=old,current=new,saved=new))
        cases.append(dict(label=f'{kind}-foreign-saved',kind=kind,before=old,current=new,saved=value(kind,'foreign')))
        if kind != 'link': cases.append(dict(label=f'{kind}-staged-conflict',kind=kind,before=old,current=None,staged=new))
        cases.append(dict(label=f'{kind}-detached',kind=kind,before=old,current=new,detached=True))
        cases.append(dict(label=f'{kind}-retry',kind=kind,before=old,current=new,edge='publish',action='fail',retry=True))
    old,new=value('file','before'),value('file','after')
    cases.extend([dict(label='stale',kind='file',before=old,current=new,stale=True),
                  dict(label='capture-foreign',kind='file',before=old,current=new,edge='capture',action='foreign'),
                  dict(label='publish-noreplace',kind='file',before=old,current=None,edge='publish',action='foreign')])
    for kind in ['file','tree','link']:
        for label,entry in [('complete',value(kind,'before')),('foreign',value(kind,'foreign')),('empty',None)]:
            cases.append(dict(label=f'evidence-{kind}-{label}',kind=kind,before=value(kind,'before'),current=None,discard=True,entry=entry))
    partial=value('tree','before'); partial['tree']['files']=[]
    cases.append(dict(label='partial-tree-evidence',kind='tree',before=value('tree','before'),current=None,discard=True,entry=partial))
    cases.append(dict(label='foreign-evidence-name',kind='file',before=old,current=None,discard=True,entry=old,foreign_name=True))
    # Exact binary byte/length comparison; nothing random or content-shaped is
    # normalized by this harness (temporary root prefixes alone are relative).
    for label, data in [('binary', bytes(range(256)) + b'\x00\xff'), ('zero-bytes', b'')]:
        cases.append(dict(label=label, kind='file', before={'file': base64.b64encode(data).decode()}, current=value('file','after')))
    # Real _restore_path calls, including alias identity in expected/staged/saved
    # positions; decoded content equality must not authenticate different JSON.
    variants = []
    extra = value('tree', 'before'); extra['tree']['foreign'] = {'safe': True}
    variants.append(('extra-tree-fields', 'tree', extra))
    variants.append(('base64-padding-bits', 'file', {'file': 'YR=='}))
    variants.append(('base64-two-byte-padding-bits', 'file', {'file': 'YWJ='}))
    normalized = value('tree', 'before')
    normalized['tree']['directories'][1][0] = './sub'
    normalized['tree']['files'][0][0] = 'sub//data'
    variants.append(('normalized-paths', 'tree', normalized))
    normalized_file = value('tree', 'before')
    normalized_file['tree']['files'][0][0] = 'sub/./data'
    variants.append(('normalized-file-path', 'tree', normalized_file))
    boolean = value('tree', 'before'); boolean['tree']['files'][0][1] = True
    variants.append(('bool-mode', 'tree', boolean))
    duplicate = value('tree', 'before'); duplicate['tree']['directories'].append(['sub', 0o750])
    variants.append(('duplicate-directory', 'tree', duplicate))
    duplicate_file = value('tree', 'before'); duplicate_file['tree']['files'].append(['sub/./data', 0o640, 'eA=='])
    variants.append(('duplicate-file-alias', 'tree', duplicate_file))
    for label, kind, snapshot in variants:
        cases.append(dict(label=label+'-restore', kind=kind, before=snapshot, current=None))
        # Canonical images generated by the real Python writer/capture boundary.
        # Duplicate non-root records intentionally fail publication and leave a
        # partial stage, so those get a second recovery attempt instead.
        if label.startswith('duplicate-') or label == 'bool-mode':
            cases[-1]['retry'] = True
            continue
        with tempfile.TemporaryDirectory(dir=scratch, prefix='d5-canonical-') as temp:
            root = Path(temp); create(root, 'item', snapshot)
            fd = bundles.open_directory(root)
            try:
                op = Operation(root/'item', {'file': FileCopy(''), 'tree': DirectoryCopy('')}[kind])
                canonical = tx._capture_at(fd, op)
            finally: os.close(fd)
        cases.extend([
            dict(label=label+'-staged', kind=kind, before=snapshot, current=None, staged=canonical),
            dict(label=label+'-expected', kind=kind, before=None, current=canonical, expected=snapshot),
            dict(label=label+'-saved', kind=kind, before=None, current=None, saved=canonical, allowed=[snapshot]),
            dict(label=label+'-already', kind=kind, before=snapshot, current=canonical),
            dict(label=label+'-evidence', kind=kind, before=snapshot, current=None, discard=True, entry=canonical),
        ])
    # Persistence faults are actual Python writer/fsync calls, not mocked recovery.
    # First-failure residue and the unmocked retry are both contractual.
    for kind in ['file', 'tree', 'link']:
        for edge in ['capture-sync', 'publish-sync', 'evidence-sync']:
            cases.append(dict(label=f'{kind}-{edge}', kind=kind,
                              before=value(kind,'before'), current=value(kind,'after'),
                              edge=edge, action='fail', retry=True, precise=True))
        cases.append(dict(label=f'{kind}-discard-sync', kind=kind,
                          before=value(kind,'before'), current=None, discard=True,
                          entry=value(kind,'before'), edge='evidence-sync', action='fail', retry=True, precise=True))
    for kind, edges in [('file',['file-write','file-sync']),
                        ('tree',['tree-file-write','tree-file-chmod','tree-file-sync',
                                 'tree-directory-chmod','tree-directory-sync','tree-root-sync'])]:
        for edge in edges:
            for occurrence in ([1,2] if edge in ['tree-directory-chmod','tree-directory-sync'] else [1]):
                cases.append(dict(label=f'{edge}-{occurrence}',kind=kind,
                                  before=value(kind,'before'),current=value(kind,'after'),
                                  edge=edge,occurrence=occurrence,action='fail',retry=True,precise=True))
    with tempfile.TemporaryDirectory(dir=scratch,prefix='d5-path-') as directory:
        base=Path(directory)
        failures = []
        for index,case in enumerate(cases):
            results=[]; images=[]; raw=[]
            for language in ['python','rust']:
                root=base/f'{index}-{language}';root.mkdir()
                create(root,'item',case['current'])
                if 'saved' in case: create(root,'q',case['saved'])
                if 'staged' in case: create(root,'q-'+case['kind'],case['staged'])
                if case.get('discard'):
                    (root/'q').mkdir(); create(root/'q','entry',case['entry'])
                    if case.get('foreign_name'): (root/'q'/'foreign').write_bytes(b'x')
                request={**case,'root':str(root)}
                if language=='python': result=python_run(request)
                else:
                    inp=base/'input.json';out=base/'output.json';inp.write_text(json.dumps(request))
                    env={**os.environ,'AW_PATH_INPUT':str(inp),'AW_PATH_OUTPUT':str(out)}
                    subprocess.run(['cargo','test','--quiet','--manifest-path',str(REPO/'rust/Cargo.toml'),'--lib','path_contract_driver','--','--ignored','--exact','transaction::path_parity_tests::path_contract_driver'],env=env,check=True,stdout=subprocess.PIPE)
                    result=json.loads(out.read_text())
                raw.append(result)
                results.append({k:error_kind(result[k]) for k in ['error','retry_error']})
                images.append((state(root),state(root.with_suffix('.detached'))))
            precise_mismatch = False
            if case.get('precise'):
                # No error-kind reduction for application diagnostics in these rows.
                precise_mismatch = any(json.loads(json.dumps(raw[0].get(k))) != raw[1].get(k)
                                       for k in ['error','retry_error','first_state'])
                precise_mismatch |= not all(r.get('fired') and r['error'] == 'injected' for r in raw)
            if (precise_mismatch or json.loads(json.dumps(raw[0]['captured'])) != raw[1]['captured']
                    or results[0] != results[1] or images[0] != images[1]):
                failures.append((case['label'], results, raw, images))
                print('FAIL', case['label'], results)
            else: print('PASS',case['label'])
        assert not failures, failures
    print(f'{len(cases)} managed-path differential cases passed')

if __name__=='__main__': main()
