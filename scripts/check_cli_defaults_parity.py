#!/usr/bin/env python3
"""E6 real lifecycle defaults, literal nulls and disagreeing overrides."""
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
from parity_oracle import oracle_pythonpath, require_python312
from check_cursor_activation_parity import snapshot
from check_rust_plan_parity import fixture
RUST = REPO / 'rust/target/debug/agent-workbench-rs'


def scenario(base, name, *, environment=None, options=(), profile_options=None,
             expected=0, target='agents', explicit_destination=True, explicit_state=False,
             globals_=(), profile_name='pstack', plan_only=False, catalog_annotation=None,
             profile_annotation=None):
    directory = base / 'case'
    results = []
    for prefix in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
        root = fixture(directory)
        profile = json.loads((root / 'profiles/pstack.json').read_text())
        profile['id'] = profile_name
        profile['schemaVersion'] = 2
        selected = next(item for item in profile['targets'] if item['id'] == target)
        if profile_options:
            selected.update({key: value.replace('<BASE>', str(directory)) if isinstance(value, str) else value
                             for key, value in profile_options.items()})
        if profile_annotation is not None:
            profile.update(profile_annotation)
        (root / f'profiles/{profile_name}.json').write_text(json.dumps(profile))
        if catalog_annotation is not None:
            path = root / 'catalog/plugins.json'
            catalog = json.loads(path.read_text())
            catalog.update(catalog_annotation)
            path.write_text(json.dumps(catalog))
        env = dict(os.environ, HOME=str(directory / 'home'), PYTHONPATH=oracle_pythonpath(str(REPO)),
                   PYTHONDONTWRITEBYTECODE='1', COLUMNS='80')
        env.pop('XDG_STATE_HOME', None)
        env.pop('CODEX_HOME', None)
        env.pop('HERMES_HOME', None)
        for key, value in (environment or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value.replace('<BASE>', str(directory))
        # The key is fixture authority, not a Python-produced plan or result.
        # Seed every possible state root, keeping all fixture paths disposable.
        for state in (directory / 'home/.local/state', directory / '.local/state',
                      directory / 'state', directory / 'state-relative', directory / 'home/xdg',
                      directory / 'home/cli-state', directory / 'null', directory):
            key = state / f'agent-workbench/market/{profile_name}/{target}.json'
            key = key.with_name('.' + key.name + '.transaction-key')
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_bytes(bytes(range(32)))
            key.chmod(0o600)
        args = ['--root', str(root), '--profile', profile_name, *globals_]
        local = ['--target', target, '--json']
        if explicit_destination:
            local += ['--destination', str(directory / 'destination')]
        if explicit_state:
            local += ['--state-dir', str(directory / 'state')]
        local += [value.replace('<BASE>', str(directory)) for value in options]
        steps = []
        commands = ('plan', 'install', 'status', 'uninstall') if expected == 0 and not plan_only else ('plan',)
        for command in commands:
            accepted = [arg for arg in local if not (command == 'uninstall' and
                        arg.startswith(('--component=', '--trust=', '--adopt')))]
            result = subprocess.run([*prefix, *args, command, *accepted], cwd=directory,
                                    env=env, capture_output=True, umask=0o022)
            assert result.returncode == expected or prefix == [str(RUST)], (name, command, 'oracle status', result)
            steps.append((result.returncode, result.stdout, result.stderr, snapshot(directory)))
        results.append(steps)
    for command, python, rust in zip(commands, *results):
        assert python[:3] == rust[:3], (name, command, 'Python', python[:3], 'Rust', rust[:3])
        assert python[3] == rust[3], (name, command, 'python-only', [r for r in python[3] if r not in rust[3]],
                                     'rust-only', [r for r in rust[3] if r not in python[3]])
    shutil.rmtree(directory)
    return len(commands)


def missing_roots(base):
    env = dict(os.environ, HOME=str(base / 'home'), XDG_STATE_HOME=str(base / 'state'),
               PYTHONPATH=oracle_pythonpath(str(REPO)), PYTHONDONTWRITEBYTECODE='1')
    before = snapshot(base)
    count = 0
    for root in ('missing', './missing', str(base / 'missing')):
        for command in ('validate', 'render', 'plan', 'install', 'status', 'trust', 'uninstall', 'update'):
            extra = ['--source=null'] if command == 'update' else ([] if command in ('validate', 'render') else ['--target=bogus'])
            rows = []
            for engine in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
                result = subprocess.run([*engine, '--root', root, command, *extra],
                                        cwd=base, env=env, capture_output=True)
                rows.append((result.returncode, result.stdout, result.stderr))
            assert rows[0][0] == 2 and not rows[0][1], (root, command, 'oracle', rows[0])
            assert str(base / 'missing/catalog/plugins.json').encode() in rows[0][2], rows[0]
            assert rows[0] == rows[1], (root, command, 'Python', rows[0], 'Rust', rows[1])
            assert snapshot(base) == before, (root, command, 'missing-root command mutated fixture')
            count += 1
    return count


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    cases = {
        'xdg-omitted': {},
        'xdg-empty': {'environment': {'XDG_STATE_HOME': ''}},
        'xdg-relative': {'environment': {'XDG_STATE_HOME': 'state-relative'}},
        'xdg-absolute': {'environment': {'XDG_STATE_HOME': '<BASE>/state'}},
        'xdg-tilde': {'environment': {'XDG_STATE_HOME': '~/xdg'}},
        'state-tilde': {'options': ('--state-dir', '~/cli-state')},
        'state-empty': {'options': ('--state-dir=',)},
        'state-null': {'options': ('--state-dir=null',)},
        'destination-null': {'options': ('--destination=null',)},
        'destination-empty': {'options': ('--destination=',)},
        'project-null': {'options': ('--project-root=null',)},
        'project-empty': {'options': ('--project-root=',)},
        'trust-null': {'options': ('--trust=null',)},
        'component-null': {'options': ('--component=null',), 'expected': 2},
        'target-null': {'options': ('--target=null',), 'expected': 2},
        'profile-null': {'globals_': ('--profile=null',), 'expected': 2},
        'root-null': {'globals_': ('--root=null',), 'expected': 2},
        'wire-null-destination': {'profile_options': {'destination': None}, 'expected': 2},
        'wire-null-project': {'profile_options': {'projectRoot': None}, 'expected': 2},
        'wire-null-components': {'profile_options': {'components': None}, 'expected': 2},
        'profile-named-null': {'profile_name': 'null'},
        'home-empty': {'environment': {'HOME': ''}, 'explicit_state': True,
                       'options': ('--project-root', '<BASE>/project')},
        'home-empty-default-plan': {'environment': {'HOME': ''}, 'explicit_state': True,
                                    'explicit_destination': False, 'plan_only': True},
        'home-relative': {'environment': {'HOME': '.'}},
        # No fallback state/destination may touch the account's real home.
        'home-absent-explicit': {'environment': {'HOME': None}, 'explicit_state': True,
                                 'options': ('--project-root', '<BASE>/project')},
        'override-disagrees': {'profile_options': {'destination': '<BASE>/profile-destination',
                                                  'projectRoot': '<BASE>/profile-project', 'components': ['hooks']},
                               'options': ('--project-root=cli-project', '--component=skills', '--component=skills')},
    }
    for target, variable in [('codex', 'CODEX_HOME'), ('hermes', 'HERMES_HOME')]:
        for label, value in [('absent', None), ('empty', ''), ('relative', 'target-home'), ('tilde', '~/target-home'),
                             ('absolute', '<BASE>/target-home')]:
            cases[f'{target}-{label}'] = {'target': target, 'explicit_destination': False,
                                          'environment': {variable: value}}
    for target in ('agents', 'claude', 'codex', 'cursor', 'grok', 'hermes'):
        for document in ('catalog', 'profile'):
            for label, value in [('surrogate', '\ud800'), ('nan', float('nan')),
                                 ('nested', {'ignored': ['\udfff', float('inf'), None]})]:
                cases[f'annotation-{target}-{document}-{label}'] = {
                    'target': target, f'{document}_annotation': {'$schema': value}}
    selected = set(sys.argv[1:])
    updates = {'update-normalized': ('success', None), 'update-source-null': ('unknown-source', 'null')}
    natives = {'native-cursor': 'cursor', 'native-claude': 'claude'}
    assert not selected.difference(cases.keys() | updates.keys() | natives.keys() | {'missing-roots'}), ('unknown default row', selected)
    count = 0
    with tempfile.TemporaryDirectory(prefix='cli-defaults-') as directory:
        for name, kwargs in cases.items():
            if not selected or name in selected:
                count += scenario(Path(directory), name, **kwargs)
        from check_update_parity import run_case
        for name, (case, source) in updates.items():
            if not selected or name in selected:
                path = Path(directory) / name
                path.mkdir()
                run_case(path, case, normalized=True, source_value=source)
                count += 2  # Separate-stream and merged-stream public replays.
        from check_lifecycle_text_parity import native
        for name, target in natives.items():
            if not selected or name in selected:
                count += native(Path(directory), target, normalized=True)
        if not selected or 'missing-roots' in selected:
            count += missing_roots(Path(directory))
    print(f'CLI defaults parity: {count} paired public lifecycle executions')


if __name__ == '__main__':
    main()
