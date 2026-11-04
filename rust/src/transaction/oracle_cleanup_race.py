#!/usr/bin/env python3
"""E2 reproduction: foreign replacement of a vacant bundle stage.

Report actual Python/Rust outcomes by default. --require-preservation retains
the original safety assertion, intentionally RED for the frozen Python oracle.
E2 was explicitly authorized; this changes no Python production code.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from apply_contract import ActivationTests, C, installation, serialize
from agent_workbench import bundles

NAME = '.agent-workbench-copy-0123456789abcdef'
CONTENT = b'foreign replacement must survive'


def run_case(target, engine, scenario='foreign-stage-cleanup'):
    from test_claude_activation import ClaudeActivationTests
    fixture = (ActivationTests if target == 'cursor' else ClaudeActivationTests)()
    fixture.setUp()
    try:
        plan = fixture.trusted((C.HOOKS, C.MCP))
        request = serialize(plan)
        root = Path(plan.next_state.destination_roots[C.HOOKS])
        foreign = root / NAME
        plan.state_file.parent.mkdir(parents=True, exist_ok=True)
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        key.write_bytes(bytes(range(32)))
        key.chmod(0o600)
        identity = None
        if engine == 'python':
            real = bundles.delete_tree
            def cleanup(name, parent):
                nonlocal identity
                if str(name).startswith('.agent-workbench-copy-') and identity is None:
                    # Successful publication already renamed our stage away.
                    assert str(name) == NAME and not foreign.exists()
                    os.mkdir(name, 0o750, dir_fd=parent)
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        os.fchmod(child, 0o750)
                        out = os.open('foreign.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640, dir_fd=child)
                        try:
                            os.fchmod(out, 0o640)
                            os.write(out, CONTENT)
                        finally:
                            os.close(out)
                        info = os.fstat(child)
                        identity = [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)]
                    finally:
                        os.close(child)
                return real(name, parent)
            rename_new, fsync = bundles.rename_new, os.fsync
            published = sync_hit = False
            def rename(source, *args, **kwargs):
                nonlocal published
                result = rename_new(source, *args, **kwargs)
                if str(source).startswith('.agent-workbench-copy-'): published = True
                return result
            def sync(fd):
                nonlocal sync_hit
                if scenario.endswith('-sync') and published and not sync_hit:
                    sync_hit = True
                    raise OSError('injected after bundle rename')
                return fsync(fd)
            try:
                with patch.object(bundles, 'delete_tree', cleanup), patch.object(secrets, 'token_hex', lambda n: '0123456789abcdef'), patch.object(bundles, 'rename_new', rename), patch.object(os, 'fsync', sync):
                    installation.apply_plan(plan)
                accepted = True
            except OSError:
                assert scenario.endswith('-sync') and sync_hit
                accepted = False
        else:
            with tempfile.TemporaryDirectory(prefix='cleanup-race-driver-', dir=os.environ['TMPDIR']) as temp:
                base = Path(temp)
                (base / 'request.json').write_text(json.dumps(request))
                completed = subprocess.run(['cargo', 'test', '--quiet', '--lib', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
                    'transaction::apply_parity_tests::differential_apply', '--', '--ignored', '--exact'],
                    cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=temp, AW_APPLY_SCENARIO=scenario),
                    text=True, capture_output=True)
                assert completed.returncode == 0, completed.stdout + completed.stderr
                actual = json.loads((base / 'result.json').read_text())
                assert actual['hit'], actual
                identity = json.loads((base / 'foreign-identity.json').read_text())
                accepted = actual['accepted']
        assert identity is not None
        preserved = False
        if foreign.exists():
            info = foreign.lstat()
            preserved = ([info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)] == identity
                and (foreign / 'foreign.txt').read_bytes() == CONTENT
                and stat.S_IMODE((foreign / 'foreign.txt').stat().st_mode) == 0o640)
        result = dict(target=target, engine=engine, injected=True, apply_accepted=accepted,
            foreign_replacement_preserved=preserved,
            journal_retained=(root.parent / '.agent-workbench-transaction.json').exists())
        print(json.dumps(result), flush=True)
        return result
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    assert os.environ.get('TMPDIR'), 'Set TMPDIR to an isolated scratch root'
    rows = [run_case(target, engine) for target in ('cursor', 'claude') for engine in ('python', 'rust')]
    if '--require-preservation' in sys.argv:
        assert all(row['foreign_replacement_preserved'] for row in rows), 'foreign staging replacements were deleted'
    else:
        assert all(row['apply_accepted'] and not row['foreign_replacement_preserved'] and not row['journal_retained'] for row in rows if row['engine'] == 'python'), rows
        assert all(not row['apply_accepted'] and row['foreign_replacement_preserved'] and row['journal_retained'] for row in rows if row['engine'] == 'rust'), rows
        print('Verified 4 real apply reproductions: frozen Python deletes foreign staging replacements; E2 Rust preserves them and retains authenticated intent')
