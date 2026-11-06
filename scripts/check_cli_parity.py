#!/usr/bin/env python3
"""E6 public CLI surface tracer; exact Python 3.12 bytes and exit statuses."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'scripts')]
from parity_oracle import require_python312
from check_cursor_activation_parity import snapshot

RUST = REPO / 'rust/target/debug/agent-workbench-rs'


def dispatch_pair(base, command, profile='default', variant='json'):
    """Exercise normalized values through real domain output and filesystem effects."""
    from check_rust_plan_parity import fixture
    directory = base / 'dispatch'
    results = []
    for prefix in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
        root = fixture(directory, '--json')
        profile_path = root / 'profiles/pstack.json'
        data = json.loads(profile_path.read_text())
        data['id'] = profile
        (profile_path.parent / f'{profile}.json').write_text(json.dumps(data))
        state = directory / 'state'
        destination = directory / 'destination'
        # A deterministic private authority makes actual install state bytes comparable.
        key = state / f'agent-workbench/market/{profile}/.agents.json.transaction-key'
        key.parent.mkdir(parents=True)
        key.write_bytes(bytes(range(32)))
        key.chmod(0o600)
        env = dict(os.environ, HOME=str(directory / 'home'), XDG_STATE_HOME=str(state),
                   PYTHONPATH=str(REPO), COLUMNS='80', PYTHONDONTWRITEBYTECODE='1')
        globals_ = ['--root=ignored', '--ro=--json', '--profile=missing', f'--prof={profile}']
        common = ['--target=bad', '--tar=agents', '--destination=ignored', f'--dest={destination}',
                  '--state-dir=ignored', f'--state-d={state}', '--js', '--json']
        if command == 'uninstall':
            seed = subprocess.run([*prefix, '--root', str(root), '--profile', profile, 'install',
                                   '--target', 'agents', '--destination', str(destination),
                                   '--state-dir', str(state)], cwd=directory, env=env, capture_output=True)
            assert seed.returncode == 0, ('uninstall setup', seed)
        if command in ('plan', 'install', 'status', 'trust', 'uninstall'):
            local = common + ([] if command == 'uninstall' else [
                '--comp=skills', '--component=skills', '--component=skills', '--tr=opaque',
                '--trust=--json', '--ad', '--adopt'])
        elif command == 'validate':
            local = ['--no-g', '--j', '--json']
        elif command == 'render':
            local = ['--ch', '--check']
        else:
            local = ['--source=bad', '--sou=local']
        if variant == 'human':
            local = [arg for arg in local if arg not in ('--js', '--j', '--json')]
        cwd = root if variant == 'empty-root' else directory
        if variant == 'empty-root':
            globals_ = ['--root=', f'--profile={profile}']
        result = subprocess.run([*prefix, *globals_, command, *local], cwd=cwd, env=env, capture_output=True)
        results.append((result.returncode, result.stdout, result.stderr, snapshot(directory)))
    expected = 2 if command == 'update' else 1 if command == 'render' else 0
    assert results[0][0] == expected, ('dispatch oracle', command, results[0][:3])
    assert results[0] == results[1], ('dispatch', command, profile, 'Python', results[0][:3], 'Rust', results[1][:3],
                                    'python-only', [r for r in results[0][3] if r not in results[1][3]],
                                    'rust-only', [r for r in results[1][3] if r not in results[0][3]])
    shutil.rmtree(directory)


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    commands = ('', 'validate', 'render', 'plan', 'install', 'status', 'trust', 'uninstall', 'update')
    cases = [
        (f'{command or "root"}-help-{alias}', ([command] if command else []) + [alias])
        for command in commands for alias in ('--help', '-h', '--h', '--he', '--hel', '-hh')
    ]
    cases.append(('missing-value-before-help', ['plan', '--target', '--help']))
    errors = {'missing-value-before-help'}
    for command in commands:
        prefix = [command] if command else []
        probes = {
            'explicit-help-value': ['--help=bad'],
            'help-cluster-invalid': ['-hx'],
            'missing-required': [],
            'unknown-option': ['--bogus'],
            'unknown-positional': ['garbage'],
            'terminator': ['--', '--help'],
        }
        if command in ('validate', 'render'):
            probes.pop('missing-required')
        if command in ('plan', 'install', 'status', 'trust', 'uninstall', 'update'):
            key = '--source' if command == 'update' else '--target'
            probes.update({'missing-value-help': [key, '--help'],
                           'required-before-extra': ['--bogus', 'x'],
                           'global-after-command': [key, 'x', '--profile', 'pstack'],
                           'unknown-after-required': [key, 'x', '--bogus', 'x']})
        if command in ('plan', 'install', 'status', 'trust'):
            probes.update({'invalid-component-help': ['--component', 'bad', '--help'],
                           'ambiguous-before-help': ['--t', '--help'],
                           'ambiguous-after-help': ['--help', '--t']})
        for label, arguments in probes.items():
            name = f'{command or "root"}-{label}'
            cases.append((name, prefix + arguments))
            if label not in ('help-cluster-invalid', 'ambiguous-after-help'):
                errors.add(name)
        help_probes: dict[str, list[str] | None] = {'help-after-extra': ['--bogus', 'x', '--help'],
                       'help-before-invalid': ['--help', '--help=bad'],
                       'long-cluster': ['-hhhhhhh']}
        if command:
            help_probes['global-help'] = None
        if command in ('plan', 'install', 'status', 'trust', 'uninstall', 'update'):
            key = '--source' if command == 'update' else '--target'
            help_probes.update({'help-after-value': [key, 'anything', '--help'],
                               'help-after-equals-value': [key + '=--help', '--help']})
        for label, arguments in help_probes.items():
            argv = ['--pro=pstack', command, '--help'] if arguments is None else prefix + arguments
            # At root, an extra positional is immediately an invalid command.
            if not command and label == 'help-after-extra':
                continue
            cases.append((f'{command or "root"}-{label}', argv))
    # Every declared option crosses missing-value/help or explicit-flag precedence.
    local_values = {
        '': ['--root', '--profile'],
        'update': ['--source'],
        **{command: ['--target', '--destination', '--project-root', '--state-dir']
           + ([] if command == 'uninstall' else ['--component', '--trust'])
           for command in ('plan', 'install', 'status', 'trust', 'uninstall')},
    }
    local_flags = {'validate': ['--json', '--no-generated-check'], 'render': ['--check'],
                   **{command: ['--json'] + ([] if command == 'uninstall' else ['--adopt'])
                      for command in ('plan', 'install', 'status', 'trust', 'uninstall')}}
    for command, keys in local_values.items():
        for key in keys:
            name = f'{command or "root"}-missing-{key}'
            cases.append((name, ([command] if command else []) + [key, '--help']))
            errors.add(name)
            value = 'skills' if key == '--component' else '--json'
            cases.append((f'{command or "root"}-equals-help-{key}',
                          ([command] if command else []) + [f'{key}={value}', '--help']))
    for command, keys in local_flags.items():
        for key in keys:
            name = f'{command}-explicit-{key}'
            cases.append((name, [command, key + '=true', '--help']))
            errors.add(name)
    for name, argv, error in [
        ('root-global-missing-command', ['--root=.', '--profile=pstack'], True),
        ('root-no-command-abbreviation', ['p', '--help'], True),
        ('root-unknown-before-command', ['--bogus', 'plan', '--target=x'], True),
        ('root-unknown-value-before-command', ['--bogus', 'value', 'plan', '--help'], True),
        ('root-terminator-command-help', ['--', 'plan', '--help'], False),
        ('root-terminator-only', ['--'], True),
        ('plan-terminator-after-required', ['plan', '--target=x', '--', '--help'], True),
        ('plan-unknown-before-missing', ['plan', '--bogus', '--target', '--help'], True),
        ('plan-invalid-component-before-ambiguous', ['plan', '--component=bad', '--t'], True),
        ('plan-help-before-invalid-component', ['plan', '--help', '--component=bad'], False),
        ('plan-short-help-explicit', ['plan', '-h=bad'], True),
        ('plan-unknown-short-before-help', ['plan', '-x', '--help'], False),
        ('plan-double-scope-extras', ['--bad', 'plan', '--target=x', '--other', 'v'], True),
    ]:
        cases.append((name, argv))
        if error:
            errors.add(name)
    cases.extend([
        ('unknown-target-workspace-first', ['--profile=--json', 'plan', '--target=--help']),
        ('unknown-target-valid-workspace', ['--profile=pstack', 'plan', '--target=--json']),
    ])
    errors.update(('unknown-target-workspace-first', 'unknown-target-valid-workspace'))
    dispatch: dict[str, tuple[str, ...]] = {f'dispatch-{command}': (command, 'default') for command in commands if command}
    dispatch['dispatch-custom-profile'] = ('plan', 'custom')
    for command in ('plan', 'install', 'status', 'trust', 'uninstall', 'validate'):
        dispatch[f'dispatch-{command}-human'] = (command, 'default', 'human')
    for command in ('plan', 'validate', 'render', 'update'):
        dispatch[f'dispatch-{command}-empty-root'] = (command, 'default', 'empty-root')
    cases.extend((name, []) for name in dispatch)
    assert len({name for name, _ in cases}) == len(cases), "duplicate CLI row name"
    selected = set(sys.argv[1:])
    assert not selected.difference(name for name, _ in cases), 'unknown CLI row selector'
    count = 0
    with tempfile.TemporaryDirectory(prefix='cli-parity-') as directory:
        base = Path(directory)
        from check_rust_plan_parity import fixture
        repository = fixture(base)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_STATE_HOME=str(base / 'state'), COLUMNS='80',
                   PYTHONDONTWRITEBYTECODE='1')
        for name, arguments in cases:
            if selected and name not in selected:
                continue
            if name in dispatch:
                dispatch_pair(base, *dispatch[name])
                count += 1
                continue
            if name.startswith('unknown-target-'):
                arguments = ['--root', str(repository), *arguments]
            before = snapshot(base)
            results = []
            for prefix in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
                result = subprocess.run([*prefix, *arguments], cwd=REPO, env=env, capture_output=True)
                results.append((result.returncode, result.stdout, result.stderr))
                assert snapshot(base) == before, (name, 'CLI inspection mutated fixture')
            expected = 2 if name in errors else 0
            assert results[0][0] == expected, (name, 'oracle status', results[0])
            assert results[0] == results[1], (name, arguments, 'Python', results[0], 'Rust', results[1])
            count += 1
    assert count == (len(selected) if selected else len(cases)), 'missing selected CLI rows'
    print(f'CLI surface parity: {count} paired public rows')


if __name__ == '__main__':
    main()
