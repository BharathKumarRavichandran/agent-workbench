#!/usr/bin/env python3
"""User-authorized exception E1; real Python/Rust apply, disposable fixtures only."""
from __future__ import annotations
import itertools
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from unittest.mock import patch

from apply_contract import REPO, serialize, observe
from test_activation import ActivationTests
from test_workbench import Fixture
from test_components import configure
from agent_workbench import installation, shared_config, transactions, state
from agent_workbench.workspace import Workspace
from agent_workbench.model import ComponentKind as C


def typed(path):
    """Literal path observation: no dereference/encoding/mode/identity erasure."""
    try:
        s = path.lstat()
    except FileNotFoundError:
        return ('missing',)
    kind = 'link' if stat.S_ISLNK(s.st_mode) else 'dir' if stat.S_ISDIR(s.st_mode) else 'file'
    content = os.readlink(path) if kind == 'link' else path.read_bytes() if kind == 'file' else None
    return kind, stat.S_IMODE(s.st_mode), s.st_dev, s.st_ino, content


def rust(base, scenario):
    p = subprocess.run(['cargo', 'test', '--quiet', '--lib', '--manifest-path', str(REPO/'rust/Cargo.toml'),
                        'transaction::apply_parity_tests::differential_apply', '--', '--ignored', '--exact'],
                       cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=str(base), AW_APPLY_SCENARIO=scenario), capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads((base/'result.json').read_bytes())


def run_case(target, replacement, uninstall, post=False):
    final = []
    with tempfile.TemporaryDirectory(prefix='aw-state-parent-', dir=os.environ['TMPDIR']) as tmp:
        base = Path(tmp)
        for engine in ('python', 'rust'):
            f = ActivationTests(); f.setUp()
            try:
                shutil.rmtree(f.base)
                f.base = base/'fixture'; f.base.mkdir()
                f.fixture = Fixture(f.base/'repo'); configure(f.fixture)
                if target == 'claude':
                    (f.fixture.root/'plugins/sample/hooks/hooks.json').write_bytes(shared_config.encode({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"'}]}]}}))
                f.workspace = Workspace.load(f.fixture.root)
                f.project = f.base/'project'; f.state = f.base/'state'
                def trusted():
                    kw = dict(components=(C.HOOKS, C.MCP), project_root=f.project)
                    p = installation.plan_install(f.workspace, target, f.state, **kw)
                    tokens = tuple({o.trust_token for o in p.all_operations if o.trust_token})
                    return installation.plan_install(f.workspace, target, f.state, trust=tokens, **kw)
                plan = trusted(); plan.state_file.parent.mkdir(parents=True)
                key = plan.state_file.parent/('.'+plan.state_file.name+'.transaction-key')
                key.write_bytes(bytes(range(32))); key.chmod(0o600)
                installation.apply_plan(plan)
                # Equivalent old state, deliberately distinct bytes/mode/identity.
                plan.state_file.write_bytes(json.dumps(json.loads(plan.state_file.read_bytes()), separators=(',', ':')).encode())
                plan.state_file.chmod(0o640)
                plan = installation.plan_uninstall(f.workspace, target, f.state, project_root=f.project) if uninstall else trusted()
                old = typed(plan.state_file)
                parent = plan.state_file.parent
                parent_before = typed(parent)
                detached = f.base/'detached-state'; outside = f.base/'outside-state'; outside.mkdir()
                sentinel = outside/plan.state_file.name
                sentinel.write_bytes(b'foreign outside state\n'); sentinel.chmod(0o640)
                sentinel_before = typed(sentinel)
                request = serialize(plan); request['uninstall'] = uninstall
                (base/'request.json').write_text(json.dumps(request))
                hit = False; staged = None
                def swap(source=None):
                    nonlocal hit, staged
                    assert not hit
                    hit = True
                    if source is not None:
                        staged = Path(source).read_bytes()
                        shutil.copyfile(source, outside/Path(source).name)
                    parent.rename(detached)
                    if replacement == 'outside': parent.symlink_to(outside, target_is_directory=True)
                    else: outside.rename(parent)
                replace = os.replace; unlink = Path.unlink
                def replaced(source, destination, *args, **kwargs):
                    if not post and Path(destination) == plan.state_file and not hit: swap(source)
                    return replace(source, destination, *args, **kwargs)
                def unlinked(path, *args, **kwargs):
                    if not post and path == plan.state_file and not hit: swap()
                    return unlink(path, *args, **kwargs)
                sync_parent = state._sync_parent
                def synced(path):
                    if post and path == plan.state_file and not hit: swap()
                    return sync_parent(path)
                if engine == 'python':
                    try:
                        with patch.object(os, 'replace', replaced), patch.object(Path, 'unlink', unlinked), patch.object(state, '_sync_parent', synced):
                            (installation.apply_uninstall if uninstall else installation.apply_plan)(plan)
                        accepted = True
                    except (OSError, ValueError): accepted = False
                    assert hit, 'Python final state seam not reached'
                else:
                    result = rust(base, f'state-parent-{replacement}-'+('post-' if post else '')+('uninstall' if uninstall else 'install'))
                    assert result['hit'], result
                    accepted = result['accepted']
                live = outside if replacement == 'outside' else parent
                outside_image = {p.name: typed(p) for p in live.iterdir()}
                if engine == 'rust' and not post and not uninstall:
                    seeds = [p for p in live.iterdir() if p.name != plan.state_file.name]
                    assert len(seeds) == 1
                    assert typed(seeds[0])[0:2] == ('file', 0o644)
                    assert json.loads(seeds[0].read_bytes()) == json.loads(bytes(request['state_after']))
                else:
                    assert set(outside_image) == (set() if engine == 'python' and not post and uninstall else {plan.state_file.name})
                # Explicitly retain lexical kind and the original detached inode.
                assert typed(parent)[0] == ('link' if replacement == 'outside' else 'dir')
                assert typed(detached) == parent_before
                journal = f.project/('.cursor' if target == 'cursor' else '.claude')/'.agent-workbench-transaction.json'
                if post:
                    assert typed(live/plan.state_file.name) == sentinel_before
                    if uninstall: assert typed(detached/plan.state_file.name) == ('missing',)
                    else:
                        published = typed(detached/plan.state_file.name)
                        assert published[0:2] == ('file', 0o600)
                        assert published[2:4] != old[2:4]
                        assert json.loads(published[4]) == json.loads(bytes(request['state_after']))
                    if engine == 'python':
                        assert accepted == (replacement == 'root')
                        assert journal.exists() == (replacement == 'outside')
                    else:
                        assert not accepted, result
                        assert journal.exists(), 'post-publication parent swap lost intent'
                elif engine == 'python':
                    assert typed(detached/plan.state_file.name) == old
                    if uninstall: assert typed(live/plan.state_file.name) == ('missing',)
                    else:
                        assert (live/plan.state_file.name).read_bytes() == staged
                        assert typed(live/plan.state_file.name)[1] == 0o644
                        assert typed(live/plan.state_file.name)[2:4] != sentinel_before[2:4]
                        assert any(p.read_bytes() == staged for p in detached.glob('.'+plan.state_file.name+'.*') if p.is_file() and p != detached/key.name and not p.name.endswith('.lock'))
                    assert accepted == (replacement == 'root')
                    assert journal.exists() == (replacement == 'outside')
                else:
                    assert not accepted, result
                    assert typed(live/plan.state_file.name) == sentinel_before, 'Rust touched foreign state'
                    assert typed(detached/plan.state_file.name) == old, 'Rust published/removed state in detached parent (missing late revalidation)'
                    assert journal.exists(), 'Rust discarded authenticated intent'
                if journal.exists():
                    # Authenticate actual bytes using the retained authority; no
                    # normalization can erase MAC, path or parent identity loss.
                    import hashlib, hmac
                    record = json.loads(journal.read_bytes())
                    mac = record.pop('mac')
                    assert hmac.compare_digest(mac, hmac.new((detached/key.name).read_bytes(), transactions._canonical(record), hashlib.sha256).hexdigest())
                    assert record['stateFile'] == str(plan.state_file)
                    assert record['parents'][str(parent)] == list(parent_before[2:4])
                if parent.is_symlink(): parent.unlink()
                else: parent.rename(outside)
                detached.rename(parent)
                if journal.exists():
                    transactions._journal_record(journal.read_bytes(), plan.state_file)
                    if engine == 'rust': assert rust(base, 'recover-only')['accepted']
                    else:
                        roots = {c: Path(p) for c, p in plan.next_state.destination_roots.items()}
                        if plan.previous_state: roots.update({c: Path(p) for c, p in plan.previous_state.destination_roots.items()})
                        transactions.recover(plan.state_file, roots, target)
                retry = installation.plan_uninstall(f.workspace, target, f.state, project_root=f.project) if uninstall else trusted()
                if engine == 'rust':
                    r = serialize(retry); r['uninstall'] = uninstall
                    (base/'request.json').write_text(json.dumps(r))
                    assert rust(base, '')['accepted']
                elif uninstall and replacement == 'root' and not post:
                    # Oracle falsely completed after deleting the foreign state
                    # and journal. Reattachment alone cannot repair its stale
                    # owned resources; record that conflict, do not hide it.
                    assert retry.has_conflicts
                    assert typed(plan.state_file) == old
                else: (installation.apply_uninstall if uninstall else installation.apply_plan)(retry)
                assert not journal.exists()
                assert {p.name: typed(p) for p in outside.iterdir()} == outside_image, 'repair/retry changed replacement evidence'
                # The oracle's abandoned synced temporary is part of E1, not
                # something to normalize out of a whole-tree comparison.
                expected_names = {plan.state_file.name, key.name, '.'+plan.state_file.name+'.lock'}
                if engine == 'python' and not uninstall and not post:
                    abandoned = [p for p in parent.iterdir() if p.name not in expected_names]
                    assert len(abandoned) == 1
                    assert abandoned[0].read_bytes() == staged
                    assert typed(abandoned[0])[1] == 0o600
                else:
                    assert {p.name for p in parent.iterdir()} <= expected_names
                final.append((observe(f.project), observe(plan.state_file), observe(parent/key.name), observe(parent/('.'+plan.state_file.name+'.lock'))))
            finally:
                f.doCleanups(); shutil.rmtree(base/'fixture', ignore_errors=True)
        if uninstall and replacement == 'root' and not post:
            assert final[0][0] == final[1][0], 'uninstalled project differs'
            assert final[1][1] == [('.', 'missing', None, None)], 'Rust retry did not uninstall state'
            assert final[0][2:] == final[1][2:]
        else:
            assert final[0] == final[1], (target, replacement, uninstall, final)


def run():
    rows = list(itertools.product(('cursor', 'claude'), ('outside', 'root'), (False, True), (False, True)))
    previous = os.umask(0o022)
    try:
        for row in rows: run_case(*row)
    finally:
        os.umask(previous)
    print(f'state parent exception E1: {len(rows)} paired real-plan install/uninstall rows passed; exact detached state, outside identity/mode/bytes, authenticated intent, reattach/recovery/retry')


if __name__ == '__main__': run()
