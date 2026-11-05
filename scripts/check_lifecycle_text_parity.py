#!/usr/bin/env python3
"""E5 actual CLI byte/exit/typed-tree differential at identical lexical paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'scripts')]
from parity_oracle import require_python312
from check_rust_plan_parity import fixture, TARGETS
from check_cursor_activation_parity import snapshot

RUST = REPO / 'rust/target/debug/agent-workbench-rs'


def skills(base, target):
    expected = []
    for engine in ('python', 'rust'):
        root = base / 'fixture'
        root.mkdir()
        repository = fixture(root)
        env = dict(os.environ, HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1',
                   XDG_STATE_HOME=str(root / 'state'), HERMES_HOME=str(root / 'hermes'),
                   CODEX_HOME=str(root / 'codex'))
        for index, action in enumerate(('plan', 'install', 'status', 'install', 'uninstall', 'uninstall')):
            prefix = [sys.executable, '-m', 'agent_workbench'] if engine == 'python' else [str(RUST)]
            command = prefix + ['--root', str(repository), '--profile', 'pstack', action,
                                '--target', target, '--state-dir', str(root / 'state'),
                                '--destination', str(root / 'destination')]
            result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, umask=0o022)
            row = (result.returncode, result.stdout, result.stderr, snapshot(root))
            if engine == 'python':
                assert result.returncode == 0, (command, row[:3])
                expected.append(row)
            else:
                assert row == expected[index], (target, action, expected[index][:3], row[:3],
                    [r for r in expected[index][3] if r not in row[3]],
                    [r for r in row[3] if r not in expected[index][3]])
        shutil.rmtree(root)
    return len(expected)


def skills_edges(base, target):
    expected = []
    for engine in ('python', 'rust'):
        root = base / 'fixture'
        root.mkdir()
        repository = fixture(root)
        destination = root / 'destination'
        destination.mkdir()
        source = repository / 'plugins/pstack/skills/example'
        env = dict(os.environ, HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1',
                   HERMES_HOME=str(root / 'hermes'), CODEX_HOME=str(root / 'codex'))
        prefix = [sys.executable, '-m', 'agent_workbench'] if engine == 'python' else [str(RUST)]
        def call(action, *extra):
            command = prefix + ['--root', str(repository), '--profile', 'pstack', action,
                                '--target', target, '--state-dir', str(root / 'state'),
                                '--destination', str(destination), *extra]
            result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, umask=0o022)
            return result.returncode, result.stdout, result.stderr, snapshot(root)
        rows = []
        foreign = destination / 'example'
        foreign.write_bytes(b'foreign')
        for action in ('plan', 'status', 'install'):
            rows.append(call(action))
            assert rows[-1][0] == 1
        foreign.unlink()
        foreign.symlink_to(os.path.relpath(source, destination), target_is_directory=True)
        rows.append(call('plan'))
        assert rows[-1][0] == 1
        for action in ('plan', 'install'):
            rows.append(call(action, '--adopt'))
            assert rows[-1][0] == 0
        rows.append(call('status'))
        # A catalog path change must replace only the recorded owned link.
        moved = source.parent.with_name('relocated-skills')
        source.parent.rename(moved)
        path = repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        catalog['plugins'][0]['artifacts']['agent-skills']['path'] = 'pstack/relocated-skills'
        path.write_text(json.dumps(catalog))
        for action in ('plan', 'install', 'uninstall'):
            rows.append(call(action))
            assert rows[-1][0] == 0
        unsupported = 'commands' if target == 'cursor' else 'rules' if target == 'claude' else 'agents'
        rows.append(call('plan', '--component', unsupported))
        assert rows[-1][0] == 1
        if target == 'cursor':
            rows.append(call('plan', '--component', 'rules'))
            assert rows[-1][0] == 1, rows[-1][:3]
        if engine == 'python':
            expected = rows
        else:
            assert rows == expected, (target, [(row[:3], actual[:3]) for row, actual in zip(expected, rows) if row != actual])
        shutil.rmtree(root)
    return len(expected)


def native(base, target):
    from check_cursor_activation_parity import prepare
    expected = []
    for engine in ('python', 'rust'):
        root, repository = prepare(base, target=target)
        env = dict(os.environ, HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1',
                   XDG_STATE_HOME=str(root / 'state'), HERMES_HOME=str(root / 'hermes'),
                   CODEX_HOME=str(root / 'codex'))
        tokens = []
        actions = [('plan', False), ('trust', False), ('status', False), ('install', False),
                   ('plan', True), ('install', True), ('status', False), ('install', False),
                   ('uninstall', False), ('uninstall', False)]
        for index, (action, granted) in enumerate(actions):
            prefix = [sys.executable, '-m', 'agent_workbench'] if engine == 'python' else [str(RUST)]
            command = prefix + ['--root', str(repository), '--profile', 'pstack', action,
                                '--target', target, '--state-dir', str(root / 'state'),
                                '--project-root', str(root / 'project')]
            if action != 'uninstall':
                for component in ['skills', 'agents', 'hooks', 'mcp', 'rules' if target == 'cursor' else 'commands']:
                    command += ['--component', component]
            if granted:
                assert tokens
                for token in tokens:
                    command += ['--trust', token]
            # JSON is independently exercised through the same actual CLI before
            # each human command. Only inspect via plan (never double-apply).
            inspect = command.copy()
            inspect[inspect.index(action)] = 'plan' if action == 'install' else action
            if action != 'uninstall':
                probe = subprocess.run(inspect + ['--json'], cwd=REPO, env=env, capture_output=True, umask=0o022)
                ops = json.loads(probe.stdout)
                if index == 0:
                    tokens = sorted({op['trustToken'] for op in ops if op.get('trustToken')})
                probe_row = (probe.returncode, probe.stdout, probe.stderr, snapshot(root))
            else:
                probe_row = None
            result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, umask=0o022)
            row = (result.returncode, result.stdout, result.stderr, snapshot(root), probe_row)
            if engine == 'python':
                assert result.returncode == (1 if index < 4 else 0), (action, row[:3])
                expected.append(row)
            else:
                assert row == expected[index], (target, action, index, expected[index][:3], row[:3],
                    [r for r in expected[index][3] if r not in row[3]],
                    [r for r in row[3] if r not in expected[index][3]])
            assert not (root / 'PAYLOAD_EXECUTED').exists()
        shutil.rmtree(root)
    return len(expected) + sum(row[4] is not None for row in expected)


def apply_failure(base, target, native=False, json_output=False, unbuffered: str | None = '1', large=False):
    """Independent descriptor holds the real lock; plan succeeds, apply fails."""
    import fcntl
    from check_cursor_activation_parity import prepare
    expected = []
    for engine in ('python', 'rust'):
        if native:
            root, repository = prepare(base, target=target)
            marketplace = 'test-marketplace'
        else:
            root = base / 'fixture'
            root.mkdir()
            repository = fixture(root)
            marketplace = 'market'
            if large:
                for number in range(80):
                    skill = repository / f'plugins/pstack/skills/skill-{number:03}'
                    skill.mkdir()
                    (skill / 'SKILL.md').write_text(f'---\nname: skill-{number:03}\ndescription: Sample\n---\n')
        env = dict(os.environ, HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1',
                   XDG_STATE_HOME=str(root / 'state'), HERMES_HOME=str(root / 'hermes'),
                   CODEX_HOME=str(root / 'codex'))
        if unbuffered is None:
            env.pop('PYTHONUNBUFFERED', None)
        else:
            env['PYTHONUNBUFFERED'] = unbuffered
        prefix = [sys.executable, '-m', 'agent_workbench'] if engine == 'python' else [str(RUST)]
        common = ['--target', target, '--state-dir', str(root / 'state')]
        if native:
            common += ['--project-root', str(root / 'project')]
        else:
            common += ['--destination', str(root / 'destination')]
        def command(action):
            args = prefix + ['--root', str(repository), '--profile', 'pstack', action] + common
            if native and action != 'uninstall':
                args += ['--component', 'hooks', '--component', 'mcp']
            return args
        grants = []
        if native:
            probe = subprocess.run(command('plan') + ['--json'], cwd=REPO, env=env, capture_output=True)
            grants = [x for token in sorted({o['trustToken'] for o in json.loads(probe.stdout) if o.get('trustToken')})
                      for x in ['--trust', token]]
        state = root / f'state/agent-workbench/{marketplace}/pstack/{target}.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        lock = state.with_name('.' + state.name + '.lock')
        lock.touch(mode=0o600)
        for index, action in enumerate(('install', 'uninstall')):
            if action == 'uninstall':
                seed = subprocess.run(command('install') + grants + ['--json'], cwd=REPO, env=env, capture_output=True, umask=0o022)
                assert seed.returncode == 0, seed
            before = snapshot(root)
            with lock.open('r+b') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                args = command(action) + (grants if action == 'install' else [])
                if json_output:
                    args += ['--json']
                result = subprocess.run(args, cwd=REPO, env=env, capture_output=True, umask=0o022)
                merged = subprocess.run(args, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, umask=0o022)
                assert merged.returncode == result.returncode
            row = (result.returncode, result.stdout, result.stderr, snapshot(root), merged.stdout)
            if engine == 'python':
                assert result.returncode == 2 and result.stdout and b'installation state is locked by another operation:' in result.stderr, row[:3]
                assert snapshot(root) == before
                expected.append(row)
            else:
                assert row == expected[index], (target, native, json_output, action,
                    'merged error offsets', expected[index][-1].find(b'error:'), row[-1].find(b'error:'),
                    'separate outputs equal', row[:3] == expected[index][:3],
                    'snapshot equal', row[3] == expected[index][3])
        shutil.rmtree(root)
    return len(expected)


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    subprocess.run(['cargo', 'build', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml')], check=True)
    with tempfile.TemporaryDirectory(prefix='lifecycle-text-', dir=os.environ['TMPDIR']) as raw:
        rows = 0
        for case in (sys.argv[1:] or [*TARGETS, 'native-cursor', 'native-claude', 'edges', 'failures']):
            if case == 'edges':
                for target in TARGETS:
                    rows += skills_edges(Path(raw), target)
            elif case == 'failures':
                for unbuffered in (None, '', '0', '1', 'false'):
                    for target in TARGETS:
                        for json_output in (False, True):
                            rows += apply_failure(Path(raw), target, json_output=json_output, unbuffered=unbuffered)
                    for target in ('cursor', 'claude'):
                        for json_output in (False, True):
                            rows += apply_failure(Path(raw), target, native=True, json_output=json_output, unbuffered=unbuffered)
                for unbuffered in (None, '1'):
                    for json_output in (False, True):
                        rows += apply_failure(Path(raw), 'hermes', json_output=json_output,
                                              unbuffered=unbuffered, large=True)
            else:
                rows += native(Path(raw), case.removeprefix('native-')) if case.startswith('native-') else skills(Path(raw), case)
    print(f'lifecycle text parity: {rows} public pairs')


if __name__ == '__main__':
    main()
