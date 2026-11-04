#!/usr/bin/env python3
"""Real recovery rejection precedence; authenticated intent, no expected FS mocks."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from apply_contract import ActivationTests, C, REPO, installation, observe, serialize, transactions
from agent_workbench import shared_config
from authority_contract import snapshot as raw_observe


def run_case(target, defect):
    from test_claude_activation import ClaudeActivationTests
    fixture = (ActivationTests if target == 'cursor' else ClaudeActivationTests)()
    fixture.setUp()
    try:
        plan = fixture.trusted((C.HOOKS, C.MCP))
        request = serialize(plan)
        plan.state_file.parent.mkdir(parents=True, exist_ok=True)
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        key.write_bytes(bytes(range(32))); key.chmod(0o600)
        unlink = os.unlink
        hit = False
        def interrupt(name, *args, **kwargs):
            nonlocal hit
            if name == '.agent-workbench-transaction.json':
                hit = True
                raise KeyboardInterrupt('retained real apply journal')
            return unlink(name, *args, **kwargs)
        with patch.object(os, 'unlink', interrupt), patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {interrupt}):
            try:
                installation.apply_plan(plan)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError('apply interruption not reached')
        assert hit
        roots = {c: Path(p) for c, p in plan.next_state.destination_roots.items()}
        journal = transactions._journal_path(roots, target)
        record = json.loads(journal.read_bytes())
        if defect == 'missing-resource-parent':
            root = roots[C.HOOKS]
            root.rename(root.with_name('detached-hooks'))
        else:
            if defect == 'outside-parent':
                info = fixture.base.stat()
                record['parents'][str(fixture.base)] = [info.st_dev, info.st_ino]
            elif defect == 'outside-document':
                record['documents'][0]['path'] = str(fixture.base / 'outside.json')
            elif defect == 'invalid-quarantine':
                record['documents'][0]['quarantine'] = '../foreign'
            elif defect in ('parent-before-document', 'parent-before-resource', 'evidence-order'):
                state_parent = str(plan.state_file.parent)
                record['parents'][state_parent][1] += 1
                if defect == 'parent-before-document':
                    document = Path(record['documents'][0]['path'])
                    record['documents'][0]['path'] = str(document.with_name('outside.json'))
                elif defect == 'parent-before-resource':
                    record['paths'][0]['path'] = str(fixture.base / 'outside-resource')
                else:
                    root = roots[C.HOOKS]
                    root.rename(root.with_name('detached-hooks'))
                    record['parents'] = {state_parent: record['parents'][state_parent],
                                         **{p: identity for p, identity in record['parents'].items() if p != state_parent}}
            else:
                raise AssertionError(defect)
            record.pop('mac')
            record['mac'] = hmac.new(bytes(range(32)), transactions._canonical(record), hashlib.sha256).hexdigest()
            journal.write_bytes(shared_config.encode(record))
        parent_defect = defect in ('parent-before-document', 'parent-before-resource', 'evidence-order')
        if not parent_defect:
            plan.state_file.write_bytes(b'{"foreign":true}\n')
        before = raw_observe(fixture.base)
        try:
            transactions.recover(plan.state_file, roots, target)
        except (OSError, ValueError) as error:
            expected = (type(error).__name__, str(error))
        else:
            raise AssertionError('Python accepted foreign state')
        assert expected == ('ValueError', 'recovery parent identity changed; journal retained' if parent_defect else
                            'recovery blocked by externally modified ownership state; journal retained')
        assert raw_observe(fixture.base) == before, 'Python mutated rejected intent'
        with tempfile.TemporaryDirectory(prefix='aw-recovery-order-', dir=os.environ['TMPDIR']) as temp:
            base = Path(temp)
            (base / 'request.json').write_text(json.dumps(request))
            subprocess.run(['cargo', 'test', '--quiet', '--lib', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
                            'transaction::apply_parity_tests::differential_apply', '--', '--ignored', '--exact'],
                           cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=temp, AW_APPLY_SCENARIO='recover-only'),
                           check=True, stdout=subprocess.PIPE)
            actual = json.loads((base / 'result.json').read_text())
        assert not actual['accepted']
        assert raw_observe(fixture.base) == before, 'Rust mutated rejected intent'
        assert (actual['error_class'], actual['diagnostic']) == expected, (target, defect, expected, actual)
    finally:
        fixture.doCleanups()


def main():
    rows = [(target, defect) for target in ('cursor', 'claude') for defect in
            ('missing-resource-parent', 'outside-parent', 'outside-document', 'invalid-quarantine',
             'parent-before-document', 'parent-before-resource', 'evidence-order')]
    for target, defect in rows:
        run_case(target, defect)
    print(f'recovery validation order: {len(rows)} paired actual-journal combined-defect rows passed; exact diagnostics and unchanged snapshots')


if __name__ == '__main__':
    main()
