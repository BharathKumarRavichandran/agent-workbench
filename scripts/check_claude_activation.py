#!/usr/bin/env python3
"""Exercise exact Claude activation through real CLI processes in isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='workbench-claude-activation-') as directory:
        base = Path(directory).resolve()
        home = base / 'home'
        home.mkdir()
        root = base / 'catalog-repo'
        plugin = root / 'plugins/lifecycle'
        hooks = plugin / 'hooks'
        hooks.mkdir(parents=True)
        script = hooks / 'stop.sh'
        sentinel = base / 'EXECUTED'
        script.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(sentinel)) + '\n')
        script.chmod(0o755)
        native = {'hooks': {'Stop': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh" --hello', 'timeout': 20},
            {'type': 'prompt', 'prompt': 'Preserve exactly', 'model': 'inert'}]}]}}
        (hooks / 'hooks.json').write_text(json.dumps(native))
        server = {'command': 'never-start-this', 'args': ['portable-package'],
                  'env': {'KEEP': '${UNCHANGED}'}, 'custom': True}
        (plugin / 'mcp.json').write_text(json.dumps({'mcpServers': {'sample': server}}))
        (root / 'catalog').mkdir()
        (root / 'profiles').mkdir()
        (root / 'catalog/plugins.json').write_text(json.dumps({
            'schemaVersion': 2,
            'marketplace': {'id': 'integration', 'name': 'integration',
                            'owner': {'name': 'Integration'}, 'description': 'Isolated CLI verification'},
            'sources': [{'id': 'local', 'kind': 'local', 'path': 'plugins'}],
            'plugins': [{'id': 'lifecycle', 'source': 'local', 'artifacts': {
                'hooks': {'path': 'lifecycle/hooks'}, 'mcp': {'path': 'lifecycle/mcp.json'}}}]}))
        project = base / 'project with spaces'
        project.mkdir()
        (root / 'profiles/default.json').write_text(json.dumps({
            'schemaVersion': 2, 'id': 'default', 'plugins': {'include': ['*'], 'exclude': []},
            'targets': [{'id': 'claude', 'components': ['hooks', 'mcp'], 'projectRoot': str(project)}]}))
        state = base / 'state'
        binary = repository / 'rust/target/debug/agent-workbench-rs'
        environment = dict(os.environ, HOME=str(home))
        environment.pop('PYTHONPATH', None)

        def cli(command: str, flags: list[str] | None = None, expected: int = 0):
            result = subprocess.run([str(binary), '--root', str(root),
                command, '--target', 'claude', '--state-dir', str(state), '--json'] + (flags or []),
                cwd=repository, env=environment, capture_output=True, text=True)
            if result.returncode != expected:
                raise AssertionError(f'{command} failed: {result.stderr}\n{result.stdout}')
            return json.loads(result.stdout)

        pending = cli('trust', expected=1)
        grants = sorted({op['trustToken'] for op in pending if op.get('trustToken')})
        assert len(grants) == 2 and not (project / '.claude').exists()
        assert {op['kind'] for op in cli('install', [part for token in grants for part in ('--trust', token)])} == {'create'}
        config = project / '.claude'
        bundle = config / '.agent-workbench-hooks/lifecycle'
        settings_path = config / 'settings.json'
        mcp_path = project / '.mcp.json'
        settings = json.loads(settings_path.read_text())
        mcp = json.loads(mcp_path.read_text())
        assert set(settings) == {'hooks'}
        expected = native['hooks']['Stop'][0]
        expected['hooks'][0]['command'] = 'sh "' + str(bundle / 'hooks/stop.sh') + '" --hello'
        assert settings['hooks']['Stop'] == [expected]
        assert mcp == {'mcpServers': {'sample': server}}
        for source in hooks.rglob('*'):
            copied = bundle / 'hooks' / source.relative_to(hooks)
            assert not copied.is_symlink()
            assert copied.read_bytes() == source.read_bytes()
            assert copied.stat().st_mode & 0o777 == source.stat().st_mode & 0o777
        state_file = state / 'agent-workbench/integration/default/claude.json'
        owned = json.loads(state_file.read_text())
        assert owned['schemaVersion'] == 4 and len(owned['slots']) == 2
        assert {op['kind'] for op in cli('install')} == {'keep'}
        foreign_group = {'matcher': 'foreign', 'hooks': [{'type': 'command', 'command': 'keep-me'}]}
        settings['hooks']['Stop'].append(foreign_group)
        settings['permissions'] = {'allow': ['Read']}
        foreign_server = {'url': 'https://foreign.invalid/mcp'}
        mcp['mcpServers']['foreign'] = foreign_server
        mcp['foreignKey'] = True
        settings_path.write_text(json.dumps(settings))
        mcp_path.write_text(json.dumps(mcp))
        assert {op['kind'] for op in cli('status')} == {'keep'}
        assert {op['kind'] for op in cli('uninstall')} == {'remove-owned'}
        assert json.loads(settings_path.read_text()) == {
            'hooks': {'Stop': [foreign_group]}, 'permissions': {'allow': ['Read']}}
        assert json.loads(mcp_path.read_text()) == {'mcpServers': {'foreign': foreign_server}, 'foreignKey': True}
        assert not bundle.exists() and not state_file.exists() and not sentinel.exists()
        assert not (config / '.agent-workbench-transaction.json').exists()
        assert not list(project.glob('.agent-workbench-document-*'))
        assert not (home / '.claude').exists() and not (home / '.claude.json').exists()
        assert json.loads((config / '.agent-workbench-owners.json').read_text())['slots'] == {}
        print('PASS: Claude-native nested hooks + exact MCP; 2 grants; copied bytes/modes; '
              'reinstall all keep; foreign settings/groups/servers preserved; owned activation '
              'and bundle removed in order; no execution, real-home writes, or journal.')


if __name__ == '__main__':
    main()
