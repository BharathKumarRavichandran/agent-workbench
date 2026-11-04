#!/usr/bin/env python3
"""Interrupted independently planned activation, real public recovery and retry."""
from __future__ import annotations
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from check_cursor_activation_edges import Replay
from check_cursor_activation_parity import REPO, prepare
sys.path.insert(0, str(REPO / 'rust/src/transaction'))
from apply_contract import observe
from agent_workbench import cli, installation, shared_config


def interrupt(base, runner, phase, uninstall):
    args = ['--root', str(runner.repository), '--profile', 'pstack',
            'uninstall' if uninstall else 'install', '--target', runner.target,
            '--project-root', str(runner.root / 'project'), '--state-dir', str(runner.root / 'state'), '--json']
    if not uninstall:
        for component in ('skills', 'agents', 'hooks', 'mcp'):
            args += ['--component', component]
        for token in runner.grants(('skills', 'agents', 'hooks', 'mcp')):
            args += ['--trust', token]
    if runner.engine == 'rust':
        (base / 'args.json').write_text(json.dumps(args))
        edge = 'JournalDurable' if phase == 'journal' else 'BeforeState'
        result = subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
                                 '--lib', 'activation_parity_tests::differential_public_activation',
                                 '--', '--ignored', '--exact'], cwd=REPO, capture_output=True, text=True,
            env=dict(os.environ, AW_PUBLIC_ACTIVATION_BASE=str(base),
                     AW_PUBLIC_ACTIVATION_EDGE=edge))
        assert result.returncode == 0 and '1 passed' in result.stdout, result.stdout + result.stderr
        assert json.loads((base / 'result.json').read_text())['hit']
    else:
        hit = False
        original = shared_config.replace_document
        def publish(path, content, fd):
            nonlocal hit
            original(path, content, fd)
            if path.name == '.agent-workbench-transaction.json':
                hit = True
                raise KeyboardInterrupt('public activation interruption')
        def state(*args):
            nonlocal hit
            hit = True
            raise KeyboardInterrupt('public activation interruption')
        owner = shared_config if phase == 'journal' else installation
        name = 'replace_document' if phase == 'journal' else 'remove_state' if uninstall else 'write_state'
        try:
            with patch.object(owner, name, publish if phase == 'journal' else state), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                cli.main(args)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('oracle interruption not reached')
        assert hit
    journal = runner.hook_config.parent / '.agent-workbench-transaction.json'
    assert journal.exists() and journal.stat().st_mode & 0o777 == 0o600
    return observe(runner.root)


def main(target='cursor'):
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    rows = 0
    with tempfile.TemporaryDirectory(prefix='cursor-public-recovery-', dir=os.environ['TMPDIR']) as raw:
        base = Path(raw)
        for uninstall in (False, True):
            for phase in ('journal', 'before-state'):
                expected = None
                for engine in ('python', 'rust'):
                    root, repository = prepare(base, target=target)
                    r = Replay(root, repository, engine, target=target)
                    r.snapshotter = observe
                    if uninstall:
                        r.install(('skills', 'agents', 'hooks', 'mcp'))
                    failed = interrupt(base, r, phase, uninstall)
                    # Trust must not recover, re-authorize, or mutate pending evidence.
                    result = r.call('trust', ('hooks', 'mcp'))
                    assert result.returncode == 2 and 'pending recovery' in result.stderr, result
                    pending = observe(root)
                    assert failed == pending
                    result = r.call('status', ('skills', 'agents', 'hooks', 'mcp'))
                    assert result.returncode == (0 if uninstall else 1), result
                    assert not (r.hook_config.parent / '.agent-workbench-transaction.json').exists()
                    r.install(('skills', 'agents', 'hooks', 'mcp'))
                    result = r.call('uninstall')
                    assert result.returncode == 0, result
                    observed = (failed, pending, r.rows)
                    if expected is None:
                        expected = observed
                    else:
                        # Journal MAC/parent identities/nonces are validated by observe before normalization.
                        assert observed[:2] == expected[:2], (phase, uninstall, 'interrupted authenticated snapshot mismatch',
                            [v for v in expected[0] if v not in failed], [v for v in failed if v not in expected[0]])
                        assert observed[2] == expected[2], (phase, uninstall, 'public recovery/retry output or bytes differ')
                    shutil.rmtree(root)
                rows += 1
                print(f'Public {target} recovery passed: uninstall={uninstall} phase={phase}', flush=True)
    print(f'{target} public recovery: {rows} paired independently planned interruptions, trust refusal, status recovery, retry and uninstall passed')


if __name__ == '__main__':
    main()
