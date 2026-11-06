#!/usr/bin/env python3
"""Section E1 public inspection/capability differential, isolated lexical replay."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from check_cursor_activation_edges import Replay
from check_cursor_activation_parity import prepare, snapshot
import check_cursor_activation_edges as replay_module

# Test-only baseline selection for replaying the recorded RED against frozen HEAD.
if os.environ.get('AW_E1_RUST_BINARY'):
    replay_module.RUST = Path(os.environ['AW_E1_RUST_BINARY'])


def scenario(r, case):
    source = r.repository / 'plugins/sample'
    components = ('agents',)
    expected_error = None
    if case == 'inventory-before-state':
        (source / 'agents/reviewer.md').write_text('not frontmatter\n')
        r.state_file.write_text('{"schemaVersion":999}')
        expected_error = 'frontmatter must start at byte zero'
    elif case == 'skills-absent':
        components = ('skills',)
        path = r.repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        catalog['plugins'][0]['artifacts'].pop('agent-skills')
        path.write_text(json.dumps(catalog))
    elif case in {'agent-name', 'agent-mismatch', 'agent-description', 'document-order', 'commands-before-state', 'rules-before-state', 'skills-before-state', 'request-order', 'skip-no-root', 'late-hook-state', 'late-mcp-state'}:
        if case == 'agent-name':
            (source / 'agents/reviewer.md').write_text('---\nname: ../bad\ndescription: ok\n---\n')
            expected_error = 'component name must be one safe filename component'
        elif case == 'agent-mismatch':
            (source / 'agents/reviewer.md').write_text('---\nname: wrong\ndescription: ok\n---\n')
            expected_error = 'component name must match filename stem'
        elif case == 'agent-description':
            (source / 'agents/reviewer.md').write_text('---\nname: reviewer\n---\n')
            expected_error = 'component frontmatter requires description'
        elif case == 'document-order':
            (source / 'agents/z-last.md').write_text('broken z\n')
            (source / 'agents/a-first.md').write_text('broken a\n')
            expected_error = 'a-first.md'
        elif case == 'skip-no-root':
            components = ('rules',)
            r.project = False
            (source / 'rules/sample-rule.mdc').write_text('broken\n')
        else:
            if case == 'commands-before-state':
                r.target = 'claude'
            r.state_file.write_text('{"schemaVersion":999}')
            if case in {'commands-before-state', 'rules-before-state', 'skills-before-state'}:
                component = case.removesuffix('-before-state')
                components = (component,)
                file = {'commands': 'commands/sample-command.md', 'rules': 'rules/sample-rule.mdc', 'skills': 'skills/sample-skill/SKILL.md'}[component]
                (source / file).write_text('broken\n')
                expected_error = 'frontmatter must start at byte zero'
            elif case == 'request-order':
                components = ('agents', 'hooks')
                (source / 'agents/reviewer.md').write_text('broken\n')
                (source / 'hooks/hooks.json').write_text('{"hooks":[]}')
                expected_error = 'frontmatter must start at byte zero'
            elif case == 'late-hook-state':
                components = ('hooks',)
                (source / 'hooks/hooks.json').write_text('{"version":1,"hooks":{"stop":[{"command":"./hooks/missing.sh"}]}}')
                expected_error = 'unsupported installation state'
            else:
                components = ('mcp',)
                (source / 'mcp.json').write_text('{"mcpServers":{"server":{"command":"./missing.py"}}}')
                expected_error = 'unsupported installation state'
    elif case == 'unicode-frontmatter':
        (source / 'agents/reviewer.md').write_text('---\nname: reviewer\u2028description: ok\n---\n')
    elif case in {'mcp-bom', 'mcp-utf16', 'hook-bom', 'hook-utf16'}:
        component, encoding = case.split('-')
        components = ('mcp',) if component == 'mcp' else ('hooks',)
        path = source / ('mcp.json' if component == 'mcp' else 'hooks/hooks.json')
        text = '{"mcpServers":{}}' if component == 'mcp' else '{"version":1,"hooks":{}}'
        path.write_bytes(text.encode('utf-8-sig' if encoding == 'bom' else 'utf-16'))
        expected_error = 'Unexpected UTF-8 BOM' if encoding == 'bom' else "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    elif case in {'hook-array', 'mcp-array', 'mcp-syntax'}:
        components = ('hooks',) if case == 'hook-array' else ('mcp',)
        path = source / ('hooks/hooks.json' if case == 'hook-array' else 'mcp.json')
        path.write_text('{"mcpServers":' if case == 'mcp-syntax' else '[]')
        expected_error = 'Expecting value' if case == 'mcp-syntax' else 'JSON document must be an object:'
    elif case == 'profile-component-override':
        path = r.repository / 'profiles/pstack.json'
        profile = json.loads(path.read_text())
        for target in profile['targets']:
            target['components'] = ['hooks']
        path.write_text(json.dumps(profile))
        (source / 'hooks/hooks.json').write_text('not JSON; explicit agents must bypass this')
    elif case == 'profile-root-override':
        path = r.repository / 'profiles/pstack.json'
        profile = json.loads(path.read_text())
        for target in profile['targets']:
            target['projectRoot'] = str(r.root / 'wrong-project')
        path.write_text(json.dumps(profile))
    elif case == 'capability-order':
        components = ('rules', 'commands', 'mcp')
        r.project = False
    elif case == 'plugin-request-order':
        path = r.repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        shutil.copytree(source, source.with_name('second'))
        plugin = json.loads(json.dumps(catalog['plugins'][0]))
        plugin['id'] = 'second'
        for item in plugin['artifacts'].values():
            item['path'] = item['path'].replace('sample/', 'second/')
        catalog['plugins'].append(plugin)
        path.write_text(json.dumps(catalog))
        components = ('agents', 'hooks')
        (source / 'hooks/hooks.json').write_text('{"hooks":[]}')
        (source.with_name('second') / 'agents/reviewer.md').write_text('broken\n')
        expected_error = 'hooks.json requires a hooks object'
    elif case.startswith('unsupported-'):
        r.target = case.removeprefix('unsupported-')
        components = ('hooks', 'mcp', 'agents', 'rules', 'commands')
        (source / 'hooks/hooks.json').write_text('malformed but rejected before inspection')
    else:
        raise AssertionError(case)
    before = snapshot(r.root)
    for action in ('plan', 'trust', 'status', 'install'):
        result = r.call(action, components)
        if expected_error:
            assert result.returncode == 2 and expected_error in result.stderr, result
        elif case in {'skip-no-root', 'capability-order'}:
            assert result.returncode == 1 and 'PROJECT_ROOT_REQUIRED' in result.stdout, result
        elif case.startswith('unsupported-'):
            assert result.returncode == 1 and result.stdout.count('COMPONENT_UNSUPPORTED') == 5, result
        else:
            assert result.returncode == 0, result
        if action != 'install' or expected_error or case.startswith('unsupported-'):
            assert snapshot(r.root) == before


def main():
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    common = ['inventory-before-state', 'skills-absent', 'agent-name', 'agent-mismatch',
              'agent-description', 'document-order', 'skills-before-state', 'request-order',
              'profile-component-override', 'profile-root-override', 'plugin-request-order', 'late-mcp-state',
              'hook-array', 'mcp-array', 'mcp-syntax', 'unicode-frontmatter',
              'mcp-bom', 'mcp-utf16', 'hook-bom', 'hook-utf16']
    cases = sys.argv[1:] or ([case + '@' + target for target in ('cursor', 'claude') for case in common]
                            + ['commands-before-state', 'rules-before-state', 'skip-no-root',
                               'late-hook-state', 'capability-order']
                            + ['unsupported-' + target for target in ('agents', 'codex', 'grok', 'hermes')])
    count = 0
    with tempfile.TemporaryDirectory(prefix='e1-inspection-', dir=os.environ['TMPDIR']) as raw:
        for case in cases:
            expected = None
            for engine in ('python', 'rust'):
                label, _, target = case.partition('@')
                target = target or 'cursor'
                root, repository = prepare(Path(raw), target=target)
                r = Replay(root, repository, engine, target=target)
                scenario(r, label)
                if expected is None:
                    expected = r.rows
                else:
                    assert r.rows == expected, (case, 'public inspection mismatch',
                                               [row[:3] for row in expected], [row[:3] for row in r.rows])
                shutil.rmtree(root)
            count += len(expected)
            print(f'E1 passed: {case} ({len(expected)} paired rows)', flush=True)
    print(f'E1 public inspection: {count} paired rows passed')


if __name__ == '__main__':
    main()
