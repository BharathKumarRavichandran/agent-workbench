#!/usr/bin/env python3
"""Exercise trusted Cursor activation with real catalog artifacts in isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='workbench-cursor-activation-') as directory:
        base = Path(directory).resolve()
        root = base / 'catalog-repo'
        plugin = root / 'plugins/lifecycle'
        plugin.mkdir(parents=True)
        shutil.copytree(repository / 'vendor/cursor-plugins/ralph-loop/hooks', plugin / 'hooks')
        shutil.copyfile(repository / 'vendor/cursor-plugins/third_party/playwright/mcp.json', plugin / 'mcp.json')
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
            'targets': [{'id': 'cursor', 'components': ['hooks', 'mcp'], 'projectRoot': str(project)}]}))
        state = base / 'state'
        binary = repository / 'rust/target/debug/agent-workbench-rs'
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)

        def cli(command: str, flags: list[str] | None = None, expected: int = 0) -> list[dict[str, object]]:
            result = subprocess.run([str(binary), '--root', str(root),
                command, '--target', 'cursor', '--state-dir', str(state), '--json'] + (flags or []),
                cwd=repository, env=environment, capture_output=True, text=True)
            if result.returncode != expected:
                raise AssertionError(f'{command} failed: {result.stderr}')
            return json.loads(result.stdout)

        pending = cli('trust', expected=1)
        grants = sorted({operation['trustToken'] for operation in pending if operation.get('trustToken')})
        assert len(grants) == 2
        flags = [part for token in grants for part in ('--trust', token)]
        created = cli('install', flags)
        assert {operation['kind'] for operation in created} == {'create'}
        config = project / '.cursor'
        bundle = config / '.agent-workbench-hooks/lifecycle'
        assert not bundle.is_symlink()
        for source in (plugin / 'hooks').rglob('*'):
            copy = bundle / 'hooks' / source.relative_to(plugin / 'hooks')
            if source.is_file():
                assert source.read_bytes() == copy.read_bytes()
                assert source.stat().st_mode & 0o777 == copy.stat().st_mode & 0o777
        hooks = json.loads((config / 'hooks.json').read_text())
        servers = json.loads((config / 'mcp.json').read_text())
        assert set(servers['mcpServers']) == {'playwright'}
        for entries in hooks['hooks'].values():
            for entry in entries:
                assert str(bundle / 'hooks') in entry['command']
                assert str(root) not in entry['command']
                assert Path(shlex.split(entry['command'])[0]).is_file()
        state_file = state / 'agent-workbench/integration/default/cursor.json'
        owned = json.loads(state_file.read_text())
        assert owned['schemaVersion'] == 4 and len(owned['slots']) == 3
        assert owned['entries']['hook:lifecycle']['resourceKind'] == 'directory-copy'
        assert {operation['kind'] for operation in cli('install')} == {'keep'}
        hooks['foreignKey'] = {'preserve': True}
        foreign_hook = {'command': 'foreign-command --preserve'}
        hooks['hooks']['stop'].append(foreign_hook)
        servers['foreignKey'] = 'preserve'
        foreign_server = {'url': 'https://foreign.invalid/mcp'}
        servers['mcpServers']['foreign'] = foreign_server
        (config / 'hooks.json').write_text(json.dumps(hooks))
        (config / 'mcp.json').write_text(json.dumps(servers))
        assert {operation['kind'] for operation in cli('status')} == {'keep'}
        removed = cli('uninstall')
        assert {operation['kind'] for operation in removed} == {'remove-owned'}
        remaining_hooks = json.loads((config / 'hooks.json').read_text())
        remaining_mcp = json.loads((config / 'mcp.json').read_text())
        assert remaining_hooks['hooks']['stop'] == [foreign_hook]
        assert remaining_hooks['hooks']['afterAgentResponse'] == []
        assert remaining_hooks['foreignKey'] == {'preserve': True}
        assert remaining_mcp == {'foreignKey': 'preserve', 'mcpServers': {'foreign': foreign_server}}
        assert not bundle.exists() and not state_file.exists()
        assert not (config / '.agent-workbench-transaction.json').exists()
        assert json.loads((config / '.agent-workbench-owners.json').read_text())['slots'] == {}
        print('PASS: real ralph-loop hook bundle + Playwright MCP; 2 grants; 1 copied bundle; '
              '3 active slots; executable modes and rewritten paths verified; reinstall all keep; '
              'foreign hook/server/keys preserved; uninstall removed all managed activation and state; no journal remains.')


if __name__ == '__main__':
    main()
