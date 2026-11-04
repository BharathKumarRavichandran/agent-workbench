#!/usr/bin/env python3
"""Public Cursor CLI differential lifecycle; no serialized Python plans in Rust.

Both engines run at the same lexical fixture paths. State authority is seeded
with a private deterministic test key so signed ownership bytes compare exactly.
All artifacts/configuration/home paths are disposable beneath explicit TMPDIR.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from test_workbench import Fixture
from test_components import configure

RUST = REPO / 'rust/target/debug/agent-workbench-rs'


def snapshot(root):
    rows = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        kind = 'link' if stat.S_ISLNK(info.st_mode) else 'dir' if stat.S_ISDIR(info.st_mode) else 'file'
        content = os.readlink(path) if kind == 'link' else path.read_bytes().hex() if kind == 'file' else None
        rows.append((str(path.relative_to(root)), kind, stat.S_IMODE(info.st_mode), content))
    return rows


def readonly_identity(root):
    return {str(p.relative_to(root)): (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            for p in root.rglob('*') for s in [p.lstat()]}


def prepare(base, case='mixed', target='cursor'):
    root = base / 'fixture'
    if root.exists(): shutil.rmtree(root)
    root.mkdir()
    fixture = Fixture(root / 'repo')
    profile = configure(fixture)
    data = json.loads(profile.read_text()); data['id'] = 'pstack'
    (profile.parent / 'pstack.json').write_text(json.dumps(data))
    (root / 'home').mkdir()
    source = fixture.root / 'plugins/sample/hooks/stop.sh'
    source.write_text('#!/bin/sh\ntouch "' + str(root / 'PAYLOAD_EXECUTED') + '"\n')
    source.chmod(0o751)
    if target == 'claude':
        command = 'sh ./hooks/stop.sh --hello' if case == 'shell-hook' else 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh" --hello'
        (source.parent / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [
            {'matcher': '*', 'hooks': [{'type': 'command', 'command': command, 'timeout': 20},
                                     {'type': 'prompt', 'prompt': 'An inert prompt', 'model': 'preserve'}]}]}}))
    elif case == 'shell-hook':
        (source.parent / 'hooks.json').write_text(json.dumps({
            'version': 1, 'hooks': {'stop': [{'command': 'sh ./hooks/stop.sh --hello', 'loop_limit': None}]}}))
    file = root / f'state/agent-workbench/test-marketplace/pstack/{target}.json'
    file.parent.mkdir(parents=True)
    key = file.with_name('.' + file.name + '.transaction-key')
    key.write_bytes(bytes(range(32))); key.chmod(0o600)
    return root, fixture.root


def lifecycle(base, case='mixed', target='cursor'):
    expected = []
    for engine in ('python', 'rust'):
        root, repository = prepare(base, case, target)
        tokens = []
        components = ['hooks'] if case in {'hooks-only', 'shell-hook'} or (target == 'claude' and case == 'user') else ['mcp'] if case == 'mcp-only' else ['hooks', 'mcp']
        env = dict(os.environ, HOME=str(root / 'home'), XDG_STATE_HOME=str(root / 'state'),
                   PYTHONDONTWRITEBYTECODE='1',
                   HERMES_HOME=str(root / 'home/hermes'), CODEX_HOME=str(root / 'home/codex'))
        actions = [('untrusted-plan','plan',False), ('read-only-trust','trust',False),
                   ('granted-plan','plan',True), ('install','install',True),
                   ('keep-plan','plan',False), ('status','status',False),
                   ('reinstall','install',False), ('uninstall','uninstall',False),
                   ('after-uninstall','plan',False)]
        for index, (label, action, granted) in enumerate(actions):
            prefix = [sys.executable, '-m', 'agent_workbench'] if engine == 'python' else [str(RUST)]
            command = prefix + ['--root', str(repository), '--profile', 'pstack', action,
                '--target',target,'--state-dir',str(root / 'state'),'--json']
            if case != 'user': command += ['--project-root', str(root / 'project')]
            if action != 'uninstall':
                for component in components: command += ['--component', component]
            if granted:
                assert tokens, 'real public plan must supply content-bound grant tokens'
                for token in tokens: command += ['--trust', token]
            before = readonly_identity(root) if action == 'trust' else None
            result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, umask=0o022)
            if before is not None:
                assert readonly_identity(root) == before, (engine, label, 'trust inspection mutated filesystem')
            assert not (root / 'PAYLOAD_EXECUTED').exists(), (engine, label, 'hook payload executed')
            row = (result.returncode, result.stdout, result.stderr, snapshot(root))
            if engine == 'python':
                assert result.returncode == (1 if label in {'untrusted-plan','read-only-trust','after-uninstall'} else 0), (label, result)
                expected.append(row)
            else:
                assert row == expected[index], (label, 'public CLI parity mismatch',
                    dict(python=expected[index][:3], rust=row[:3]),
                    dict(python_only=[item for item in expected[index][3] if item not in row[3]],
                         rust_only=[item for item in row[3] if item not in expected[index][3]]))
            if label == 'untrusted-plan':
                operations = json.loads(result.stdout)
                tokens = sorted({item['trustToken'] for item in operations if item.get('trustToken')})
        shutil.rmtree(root)
    return len(expected)


def main():
    assert os.environ.get('TMPDIR'), 'explicit isolated scratch TMPDIR required'
    build = subprocess.run(['cargo','build','--quiet','--manifest-path',str(REPO / 'rust/Cargo.toml')],cwd=REPO)
    assert build.returncode == 0
    with tempfile.TemporaryDirectory(prefix='cursor-activation-cli-',dir=os.environ['TMPDIR']) as raw:
        cases = sys.argv[1:] or ['mixed', 'hooks-only', 'mcp-only', 'user', 'shell-hook']
        rows = 0
        for case in cases:
            rows += lifecycle(Path(raw), case)
            print(f'Cursor public lifecycle passed: {case}', flush=True)
    print(f'Cursor public activation: {rows} paired real CLI lifecycle rows passed; exact output/state/documents/modes and read-only trust')


if __name__ == '__main__': main()
