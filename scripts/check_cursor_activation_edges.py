#!/usr/bin/env python3
"""Public Cursor acceptance edges, replayed at identical isolated lexical paths."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from check_cursor_activation_parity import REPO, RUST, prepare, snapshot, readonly_identity


class Replay:
    def __init__(self, root, repository, engine, target='cursor'):
        self.root, self.repository, self.engine = root, repository, engine
        self.target = target
        self.rows = []
        self.profile = 'pstack'
        self.project = True
        self.extra = []
        self.snapshotter = snapshot
        self.timeout: float | None = None

    def call(self, action, components=('hooks', 'mcp'), tokens=(), adopt=False):
        prefix = [sys.executable, '-m', 'agent_workbench'] if self.engine == 'python' else [str(RUST)]
        command = prefix + ['--root', str(self.repository), '--profile', self.profile, action,
                            '--target', self.target, '--state-dir', str(self.root / 'state'), '--json']
        if self.project:
            command += ['--project-root', str(self.root / 'project')]
        if action != 'uninstall':
            for component in components:
                command += ['--component', component]
        for token in tokens:
            command += ['--trust', token]
        if adopt:
            command += ['--adopt']
        command += self.extra
        env = dict(os.environ, HOME=str(self.root / 'home'), XDG_STATE_HOME=str(self.root / 'state'),
                   PYTHONDONTWRITEBYTECODE='1', HERMES_HOME=str(self.root / 'home/hermes'),
                   CODEX_HOME=str(self.root / 'home/codex'))
        before = readonly_identity(self.root) if action == 'trust' else None
        result = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True, umask=0o022, timeout=self.timeout)
        if before is not None:
            assert before == readonly_identity(self.root), (self.engine, 'trust mutated fixture')
        assert not (self.root / 'PAYLOAD_EXECUTED').exists()
        self.rows.append((result.returncode, result.stdout, result.stderr, self.snapshotter(self.root)))
        return result

    @property
    def hook_config(self):
        return self.root / 'project' / f'.{self.target}' / ('settings.json' if self.target == 'claude' else 'hooks.json')

    @property
    def mcp_config(self):
        return self.root / 'project/.mcp.json' if self.target == 'claude' else self.root / 'project/.cursor/mcp.json'

    @property
    def bundle(self):
        return self.root / 'project' / f'.{self.target}' / '.agent-workbench-hooks/sample'

    @property
    def state_file(self):
        return self.root / 'state/agent-workbench/test-marketplace' / self.profile / f'{self.target}.json'

    def grants(self, components=('hooks', 'mcp')):
        result = self.call('plan', components)
        assert result.returncode in (0, 1), result
        return sorted({item['trustToken'] for item in json.loads(result.stdout) if item.get('trustToken')})

    def install(self, components=('hooks', 'mcp'), adopt=False):
        tokens = self.grants(components)
        result = self.call('install', components, tokens, adopt)
        assert result.returncode == 0, (self.engine, result)
        return tokens


def configure_case(r, case):
    source = r.repository / 'plugins/sample'
    hooks = source / 'hooks/hooks.json'
    catalog_path = r.repository / 'catalog/plugins.json'
    catalog = json.loads(catalog_path.read_text())
    if case == 'multi-event-order':
        data = json.loads(hooks.read_text())
        event = 'Stop' if r.target == 'claude' else 'stop'
        entry = json.loads(json.dumps(data['hooks'][event][0]))
        entry['matcher'] = 'second'
        data['hooks'][event].append(entry)
        data['hooks']['SessionStart' if r.target == 'claude' else 'start'] = [json.loads(json.dumps(entry))]
        hooks.write_text(json.dumps(data))
        (source / 'mcp.json').write_text('{"mcpServers":{"z-server":{"command":"never-run-z"},"a-server":{"command":"never-run-a"}}}')
    elif case.startswith('order-'):
        component = case.removeprefix('order-')
        r.state_file.write_text('{"schemaVersion":999}')
        if component == 'hooks':
            hooks.write_text('{"hooks":[]}')
        else:
            (source / 'mcp.json').write_text('{"mcpServers":[]}')
    elif case.startswith('inspect-'):
        if case == 'inspect-incompatible-many':
            if r.target == 'claude':
                hooks.write_text(json.dumps({'version': 1, 'hooks': {'stop': [
                    {'command': 'echo inert'}, {'command': 'echo inert2'}]}}))
            else:
                hooks.write_text(json.dumps({'hooks': {'Stop': [
                    {'hooks': [{'type': 'command', 'command': 'echo inert'}]},
                    {'hooks': [{'type': 'command', 'command': 'echo inert2'}]}]}}))
        else:
            values = {'inspect-mcp-name': {'mcpServers': {'../unsafe': {'command': 'never-run'}}},
                      'inspect-mcp-object': {'mcpServers': {'sample-server': []}},
                      'inspect-mcp-missing': {}}
            (source / 'mcp.json').write_text(json.dumps(values[case]))
    elif case.startswith('reject-hook-'):
        commands = {
            'escape': './hooks/../../escape.sh',
            'absolute': str(r.root / 'outside.sh'),
            'variable': '${CURSOR_PLUGIN_ROOT}/hooks/stop.sh' if r.target == 'claude' else '${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh',
            'missing': './hooks/missing.sh',
            'empty': '',
        }
        command = commands[case.removeprefix('reject-hook-')]
        data = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': command}]}]}} if r.target == 'claude' else {'version': 1, 'hooks': {'stop': [{'command': command}]}}
        hooks.write_text(json.dumps(data))
    elif case.startswith('reject-mcp-'):
        values = {
            'relative': {'command': './server.py'},
            'variable': {'command': 'python', 'env': {'ROOT': '${CURSOR_PLUGIN_ROOT}'}},
            'args': {'command': 'python', 'args': ['./server.py']},
        }
        (source / 'mcp.json').write_text(json.dumps({'mcpServers': {'sample-server': values[case.removeprefix('reject-mcp-')]}}))
    elif case == 'empty-hooks':
        if r.target == 'claude':
            hooks.write_text('{"hooks":{}}')
            catalog['plugins'][0]['artifacts']['claude-hooks'] = catalog['plugins'][0]['artifacts'].pop('hooks')
            catalog_path.write_text(json.dumps(catalog))
        else:
            hooks.write_text('{"version":1,"hooks":{}}')
    elif case == 'native-hooks':
        catalog['plugins'][0]['artifacts'][f'{r.target}-hooks'] = catalog['plugins'][0]['artifacts'].pop('hooks')
        catalog_path.write_text(json.dumps(catalog))
    elif case == 'profile-overrides':
        path = r.repository / 'profiles/pstack.json'
        profile = json.loads(path.read_text())
        for target in profile['targets']:
            if target['id'] == r.target:
                target.update(components=['skills'], projectRoot=str(r.root / 'wrong-project'),
                              destination=str(r.root / 'wrong-skills'))
        path.write_text(json.dumps(profile))
        r.extra = ['--destination', str(r.root / 'chosen-skills')]
    elif case == 'foreign-fields':
        r.hook_config.parent.mkdir(parents=True)
        if r.target == 'claude':
            r.hook_config.write_text('{"permissions":{"allow":["Read"]},"version":"foreign","hooks":{"Stop":[{"matcher":"foreign","hooks":[{"type":"command","command":"keep"}]}],"SessionStart":[]}}')
        else:
            r.hook_config.write_text('{"version":1,"other":true,"hooks":{"stop":[{"command":"foreign-command","matcher":"keep"}],"otherEvent":[]}}')
        r.mcp_config.write_text('{"other":42,"mcpServers":{"foreign":{"type":"http","url":"https://foreign.invalid","headers":{"key":"PRIVATE_TEST_SENTINEL"}}}}')
    elif case in {'custom-profile', 'ownership-handoff', 'owned-slot-adoption'}:
        profile_path = r.repository / 'profiles/pstack.json'
        profile = json.loads(profile_path.read_text())
        profile['id'] = 'other'
        if case == 'ownership-handoff':
            shutil.copytree(source, source.with_name('second'))
            (source.with_name('second') / 'mcp.json').write_text('{"mcpServers":{"second-server":{"command":"never-run-this"}}}')
            plugin = json.loads(json.dumps(catalog['plugins'][0]))
            plugin['id'] = 'second'
            for artifact in plugin['artifacts'].values():
                artifact['path'] = artifact['path'].replace('sample', 'second')
            catalog['plugins'].append(plugin)
            catalog_path.write_text(json.dumps(catalog))
            profile['plugins']['include'] = ['second']
            first = json.loads(profile_path.read_text())
            first['plugins']['include'] = ['sample']
            profile_path.write_text(json.dumps(first))
        (profile_path.parent / 'other.json').write_text(json.dumps(profile))
        key = r.root / f'state/agent-workbench/test-marketplace/other/.{r.target}.json.transaction-key'
        key.parent.mkdir(parents=True)
        key.write_bytes(bytes(range(32)))
        key.chmod(0o600)
        if case == 'custom-profile':
            r.profile = 'other'
    elif case == 'native-ambiguous':
        catalog['plugins'][0]['artifacts'][f'{r.target}-hooks'] = dict(catalog['plugins'][0]['artifacts']['hooks'])
        catalog_path.write_text(json.dumps(catalog))


def scenario(r, case):
    configure_case(r, case)
    if case.startswith('order-'):
        before = snapshot(r.root)
        for action in ('plan', 'trust', 'status', 'install'):
            result = r.call(action, (case.removeprefix('order-'),))
            assert result.returncode == 2 and result.stderr.startswith('error: '), result
            assert snapshot(r.root) == before
        return
    if case.startswith('inspect-'):
        before = snapshot(r.root)
        for action in ('plan', 'trust', 'status', 'install'):
            result = r.call(action)
            if case == 'inspect-incompatible-many':
                assert result.returncode == 1
                assert sum(item.get('diagnostic') == 'REPRESENTATION_UNSUPPORTED' for item in json.loads(result.stdout)) == 2
            else:
                assert result.returncode == 2 and result.stderr.startswith('error: '), result
            assert snapshot(r.root) == before, (case, action, 'rejected fixture mutated')
        return
    if case.startswith('reject-'):
        before = snapshot(r.root)
        for action in ('plan', 'trust', 'status', 'install'):
            result = r.call(action)
            assert result.returncode == 2 and result.stderr.startswith('error: '), result
            assert snapshot(r.root) == before, (case, action, 'rejected fixture mutated')
        return
    if case == 'native-ambiguous':
        result = r.call('plan')
        assert result.returncode == 2 and 'multiple hook artifacts' in result.stderr, result
        return
    if case == 'missing-project':
        r.project = False
        result = r.call('install', ('rules', 'hooks', 'mcp'))
        assert result.returncode == 1 and 'PROJECT_ROOT_REQUIRED' in result.stdout, result
        return
    components = ('hooks',) if case == 'empty-hooks' else ('skills', 'agents', 'commands' if r.target == 'claude' else 'rules', 'hooks', 'mcp') if case == 'mixed-paths' else ('hooks', 'mcp')
    tokens = r.install(components)
    r.call('plan', components)
    r.call('status', components)
    if case == 'changed-trust':
        path = r.repository / 'plugins/sample/hooks/stop.sh'
        path.write_text(path.read_text() + '# changed executable bytes\n')
        result = r.call('install', components, tokens)
        assert result.returncode == 1 and 'TRUST_REQUIRED' in result.stdout, result
        r.call('trust', components)
        r.install(components)
    elif case == 'narrowing':
        r.install(('mcp',))
        assert not r.bundle.exists()
        r.install(('hooks',))
    elif case == 'foreign-bundle':
        path = r.bundle / 'hooks/stop.sh'
        original = path.read_bytes()
        path.write_bytes(b'foreign replacement\n')
        result = r.call('uninstall')
        assert result.returncode == 1, result
        path.write_bytes(original)
    elif case == 'referenced-bundle':
        path = r.hook_config
        original = path.read_bytes()
        data = json.loads(original)
        if r.target == 'claude':
            data['hooks']['Stop'].append({'matcher': 'foreign', 'hooks': [{'type': 'command', 'command': str(r.bundle / 'hooks/stop.sh')}]})
        else:
            data['hooks']['stop'].append({'command': str(r.bundle / 'hooks/stop.sh'), 'matcher': 'foreign'})
        path.write_text(json.dumps(data))
        result = r.call('uninstall')
        assert result.returncode == 1 and 'HOOK_BUNDLE_REFERENCED' in result.stdout, result
        path.write_bytes(original)
    elif case == 'foreign-document':
        path = r.mcp_config
        original = path.read_bytes()
        data = json.loads(original)
        data['mcpServers']['sample-server'] = {'command': 'foreign-command'}
        path.write_text(json.dumps(data))
        result = r.call('uninstall')
        assert result.returncode == 1, result
        path.write_bytes(original)
    elif case in {'ownership-handoff', 'owned-slot-adoption'}:
        r.profile = 'other'
        if case == 'owned-slot-adoption':
            grants = r.grants(('mcp',))
            result = r.call('install', ('mcp',), grants, adopt=True)
            assert result.returncode == 1 and 'SLOT_OWNED' in result.stdout, result
        else:
            r.install()
            r.call('status')
        r.profile = 'pstack'
        result = r.call('uninstall')
        assert result.returncode == 0, result
        r.profile = 'other'
        if case == 'owned-slot-adoption':
            r.install(('mcp',), adopt=True)
        result = r.call('uninstall')
        assert result.returncode == 0, result
        return
    elif case == 'forged-state':
        path = r.state_file
        original = path.read_bytes()
        data = json.loads(original)
        next(iter(data['slots'].values()))['document'] = str(r.root / 'outside.json')
        path.write_text(json.dumps(data))
        (r.root / 'outside.json').write_text('{"preserve":true}')
        result = r.call('uninstall')
        assert result.returncode == 2, result
        assert (r.root / 'outside.json').read_text() == '{"preserve":true}'
        path.write_bytes(original)
    r.call('install', components)
    result = r.call('uninstall')
    assert result.returncode == 0, (r.engine, result)
    r.call('plan', components)


def main(target='cursor'):
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    cases = sys.argv[1:] or ['empty-hooks', 'native-hooks', 'profile-overrides', 'foreign-fields',
                             'mixed-paths', 'changed-trust', 'narrowing', 'foreign-bundle',
                             'referenced-bundle', 'foreign-document', 'forged-state',
                             'missing-project', 'native-ambiguous', 'custom-profile',
                             'ownership-handoff', 'owned-slot-adoption',
                             'reject-hook-escape', 'reject-hook-absolute', 'reject-hook-variable',
                             'reject-hook-missing', 'reject-hook-empty', 'reject-mcp-relative',
                             'reject-mcp-variable', 'reject-mcp-args', 'inspect-incompatible-many',
                             'inspect-mcp-name', 'inspect-mcp-object', 'inspect-mcp-missing',
                             'order-hooks', 'order-mcp', 'multi-event-order']
    rows = 0
    with tempfile.TemporaryDirectory(prefix='cursor-edges-', dir=os.environ['TMPDIR']) as raw:
        base = Path(raw)
        for case in cases:
            expected = None
            for engine in ('python', 'rust'):
                root, repository = prepare(base, target=target)
                runner = Replay(root, repository, engine, target=target)
                scenario(runner, case)
                if expected is None:
                    expected = runner.rows
                else:
                    assert len(runner.rows) == len(expected), (case, 'row count mismatch')
                    for index, (py, rs) in enumerate(zip(expected, runner.rows)):
                        assert py == rs, (case, index, 'public CLI mismatch',
                            {'python': py[:3], 'rust': rs[:3]},
                            {'python_only': [v for v in py[3] if v not in rs[3]],
                             'rust_only': [v for v in rs[3] if v not in py[3]]})
                shutil.rmtree(root)
            rows += len(expected)
            print(f'{target} acceptance edge passed: {case} ({len(expected)} paired rows)', flush=True)
    print(f'{target} public edges: {rows} paired rows passed')


if __name__ == '__main__':
    main()
