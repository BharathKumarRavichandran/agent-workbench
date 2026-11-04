#!/usr/bin/env python3
"""Claude matcher groups/handlers are native data, never Cursor translation."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import tempfile

from check_cursor_activation_edges import Replay
from check_cursor_activation_parity import prepare, snapshot

CASES = ['prompt-only', 'empty-group', 'unknown-handler', 'multiple-groups',
         'invalid-handler', 'missing-handler-type', 'invalid-matcher', 'cursor-version',
         'mixed-shapes', 'bare-missing-script', 'user-mcp-rejected', 'profile-selection']


def run_case(base, case):
    expected = None
    for engine in ('python', 'rust'):
        root, repository = prepare(base, target='claude')
        r = Replay(root, repository, engine, target='claude')
        source = repository / 'plugins/sample/hooks/hooks.json'
        native = json.loads(source.read_text())
        group = native['hooks']['Stop'][0]
        reject = False
        if case == 'prompt-only':
            group['hooks'] = [{'type': 'prompt', 'prompt': '${CURSOR_PLUGIN_ROOT} $(touch NEVER_RUN); ./missing.sh', 'model': 'preserve'}]
        elif case == 'empty-group':
            group['hooks'] = []
        elif case == 'unknown-handler':
            group['hooks'] = [{'type': 'future-native-type', 'command': './missing.sh', 'nested': {'preserve': True}}]
        elif case == 'multiple-groups':
            native['hooks']['SessionStart'] = [{'hooks': [{'type': 'command', 'command': 'sh ./stop.sh'}]}]
            native['hooks']['Stop'].append({'matcher': '', 'hooks': [{'type': 'prompt', 'prompt': 'preserve'}]})
        elif case == 'invalid-handler':
            group['hooks'] = [False]; reject = True
        elif case == 'missing-handler-type':
            group['hooks'] = [{'command': 'echo inert'}]; reject = True
        elif case == 'invalid-matcher':
            group['matcher'] = None; reject = True
        elif case == 'cursor-version':
            native['version'] = 1; reject = True
        elif case == 'mixed-shapes':
            native['hooks']['Stop'].append({'command': 'echo inert'}); reject = True
        elif case == 'bare-missing-script':
            group['hooks'] = [{'type': 'command', 'command': 'sh missing.sh'}]; reject = True
        elif case == 'profile-selection':
            path = repository / 'profiles/pstack.json'
            profile = json.loads(path.read_text())
            for target in profile['targets']:
                if target['id'] == 'claude':
                    target['components'] = ['hooks', 'mcp']
                    target['projectRoot'] = str(root / 'project')
            path.write_text(json.dumps(profile))
            r.project = False
        source.write_text(json.dumps(native))
        if case == 'user-mcp-rejected':
            r.project = False
            before = snapshot(root)
            for action in ('plan', 'trust', 'status', 'install'):
                result = r.call(action, ('mcp',))
                assert result.returncode == 1 and 'PROJECT_ROOT_REQUIRED' in result.stdout, result
                assert snapshot(root) == before
            assert not (root / 'home/.claude.json').exists()
        elif reject:
            before = snapshot(root)
            for action in ('plan', 'trust', 'status', 'install'):
                result = r.call(action, ('hooks',))
                assert result.returncode == 2, result
                assert snapshot(root) == before
        else:
            components = () if case == 'profile-selection' else ('hooks', 'mcp')
            r.call('trust', components)
            r.install(components)
            r.call('plan', components)
            r.call('status', components)
            r.call('install', components)
            settings = json.loads(r.hook_config.read_text())
            assert 'version' not in settings
            if case in {'prompt-only', 'empty-group', 'unknown-handler'}:
                assert settings['hooks']['Stop'][0] == group
            result = r.call('uninstall')
            assert result.returncode == 0, result
        if expected is None:
            expected = r.rows
        else:
            assert len(expected) == len(r.rows)
            for index, (py, rs) in enumerate(zip(expected, r.rows)):
                differences = [(a[0], a[1:3], bytes.fromhex(a[3]).decode(errors='replace') if a[1] == 'file' else a[3]) for a in py[3] if a not in rs[3]]
                rust_differences = [(a[0], a[1:3], bytes.fromhex(a[3]).decode(errors='replace') if a[1] == 'file' else a[3]) for a in rs[3] if a not in py[3]]
                assert py == rs, (case, index, py[:3] if py[:3] != rs[:3] else 'output matches', rs[:3] if py[:3] != rs[:3] else '', differences, rust_differences)
        shutil.rmtree(root)
    return len(expected)


def main():
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    rows = 0
    with tempfile.TemporaryDirectory(prefix='claude-native-', dir=os.environ['TMPDIR']) as raw:
        for case in CASES:
            count = run_case(Path(raw), case)
            rows += count
            print(f'Claude native contract passed: {case} ({count} paired rows)', flush=True)
    print(f'Claude native contract: {rows} paired public CLI rows passed')


if __name__ == '__main__':
    main()
