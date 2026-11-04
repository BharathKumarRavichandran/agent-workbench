#!/usr/bin/env python3
"""E2: authorized foreign replacement at the vacant bundle staging name.

Python's unsafe outcome remains explicit; Rust retains authenticated intent,
preserves the exact foreign inode/bytes/modes, and really recovers and retries.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from oracle_cleanup_race import CONTENT, NAME, REPO, run_case
from apply_contract import ActivationTests, C, installation, serialize, transactions


def rust_call(base, request, scenario):
    (base / 'request.json').write_text(json.dumps(request))
    completed = subprocess.run(['cargo', 'test', '--quiet', '--lib', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
        'transaction::apply_parity_tests::differential_apply', '--', '--ignored', '--exact'],
        cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=str(base), AW_APPLY_SCENARIO=scenario),
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads((base / 'result.json').read_text())


def verify_rust(target, scenario='foreign-stage-cleanup'):
    from test_claude_activation import ClaudeActivationTests
    fixture = (ActivationTests if target == 'cursor' else ClaudeActivationTests)()
    fixture.setUp()
    try:
        plan = fixture.trusted((C.HOOKS, C.MCP))
        request = serialize(plan)
        root = Path(plan.next_state.destination_roots[C.HOOKS])
        foreign = root / NAME
        journal = root.parent / '.agent-workbench-transaction.json'
        plan.state_file.parent.mkdir(parents=True, exist_ok=True)
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        key.write_bytes(bytes(range(32))); key.chmod(0o600)
        with tempfile.TemporaryDirectory(prefix='stage-cleanup-contract-', dir=os.environ['TMPDIR']) as temp:
            base = Path(temp)
            result = rust_call(base, request, scenario)
            assert result['hit'], result
            assert not result['accepted'], ('Rust falsely accepted foreign stage replacement', result)
            identity = json.loads((base / 'foreign-identity.json').read_text())
            def preserved():
                info = foreign.lstat()
                assert [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)] == identity
                assert (foreign / 'foreign.txt').read_bytes() == CONTENT
                assert stat.S_IMODE((foreign / 'foreign.txt').stat().st_mode) == 0o640
            preserved()
            assert journal.exists(), 'Rust discarded authenticated recovery evidence'
            assert stat.S_IMODE(journal.stat().st_mode) == 0o600
            transactions._journal_record(journal.read_bytes(), plan.state_file)
            assert not plan.state_file.exists(), 'failed cleanup published ownership state'
            result = rust_call(base, request, 'recover-only')
            assert result['accepted'], result
            preserved()
            assert not journal.exists()
            retry = fixture.trusted((C.HOOKS, C.MCP))
            result = rust_call(base, serialize(retry), '')
            assert result['accepted'], result
            assert plan.state_file.exists()
            preserved()
            uninstall = installation.plan_uninstall(fixture.workspace, target, fixture.state, project_root=fixture.project)
            removal = serialize(uninstall); removal['uninstall'] = True
            result = rust_call(base, removal, '')
            assert result['accepted'], result
            preserved()
            assert not plan.state_file.exists()
            assert not journal.exists()
        print(f'{target}: Rust rejected foreign stage cleanup; exact foreign inode/bytes/modes preserved through authenticated recovery, retry, and uninstall', flush=True)
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    for target in ('cursor', 'claude'):
        for scenario in ('foreign-stage-cleanup', 'foreign-stage-cleanup-sync'):
            oracle = run_case(target, 'python', scenario)
            assert oracle['apply_accepted'] == (not scenario.endswith('-sync')), oracle
            assert not oracle['foreign_replacement_preserved'] and not oracle['journal_retained'], oracle
            verify_rust(target, scenario)
    print('E2 stage cleanup: 8 paired target/engine/phase rows passed; intentional oracle difference and real Rust recovery/retry/uninstall')
