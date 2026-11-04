#!/usr/bin/env python3
"""Private E1 inventory contract; no public validate/render command is enabled."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from check_cursor_activation_parity import REPO, prepare, snapshot
from agent_workbench.adapters.registry import build_registry
from agent_workbench.workspace import Workspace


def run_case(base, case):
    root, repository = prepare(base)
    workspace = Workspace.load(repository, 'pstack')
    plugin = workspace.resolved.plugins[0]
    source = repository / 'plugins/sample'
    if case == 'document-link':
        (source / 'agents/nested-link').symlink_to('../commands')
    elif case == 'document-root-file':
        import shutil
        shutil.rmtree(source / 'agents')
        (source / 'agents').write_text('not a directory')
    elif case == 'catalog-parent-file':
        import shutil
        shutil.rmtree(source / 'agents')
        (source / 'agents').write_text('not a directory')
        path = repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        catalog['plugins'][0]['artifacts']['agents']['path'] = 'sample/agents/child'
        path.write_text(json.dumps(catalog))
        workspace = Workspace.load(repository, 'pstack')
        plugin = workspace.resolved.plugins[0]
    elif case == 'unicode-frontmatter':
        (source / 'agents/sample-agent.md').write_text('---\nname: sample-agent\u2028description: ok\n---\n')
    elif case in {'mcp-utf8-truncated', 'mcp-utf8-continuation'}:
        suffix = b'\xc2' if case == 'mcp-utf8-truncated' else b'\xe2\x82x'
        (source / 'mcp.json').write_bytes(b'{"mcpServers":{}}' + suffix)
    elif case in {'mcp-bom', 'mcp-utf16', 'hook-bom', 'hook-utf16'}:
        component, encoding = case.split('-')
        path = source / ('mcp.json' if component == 'mcp' else 'hooks/hooks.json')
        text = '{"mcpServers":{}}' if component == 'mcp' else '{"version":1,"hooks":{}}'
        path.write_bytes(text.encode('utf-8-sig' if encoding == 'bom' else 'utf-16'))
    elif case == 'mcp-array':
        (source / 'mcp.json').write_text('[]')
    elif case == 'mcp-duplicate':
        (source / 'mcp.json').write_text('{"mcpServers":{},"mcpServers":{}}')
    elif case == 'hook-duplicate':
        (source / 'hooks/hooks.json').write_text('{"version":1,"version":1,"hooks":{}}')
    elif case in {'skill-missing-name', 'skill-unsafe-name', 'skill-crlf', 'skill-quotes', 'skill-missing-description', 'mcp-syntax', 'hook-array'}:
        skill = source / 'skills/sample-skill/SKILL.md'
        if case == 'skill-missing-name':
            skill.write_text('---\ndescription: sample\n---\n')
        elif case == 'skill-unsafe-name':
            skill.write_text('---\nname: ../bad\ndescription: sample\n---\n')
        elif case == 'skill-crlf':
            skill.write_bytes(b'---\r\nname: sample-skill\r\ndescription: sample\r\n---\r\n')
        elif case == 'skill-quotes':
            skill.write_text('---\nname: "bad\'quote"\ndescription: sample\n---\n')
        elif case == 'skill-missing-description':
            skill.write_text('---\nname: sample-skill\n---\n')
        elif case == 'mcp-syntax':
            (source / 'mcp.json').write_text('{"mcpServers":')
        else:
            (source / 'hooks/hooks.json').write_text('[]')
    elif case == 'skill-directory-link':
        (source / 'skills/alias').symlink_to('sample-skill')
    elif case == 'skill-manifest-link':
        skill = source / 'skills/sample-skill/SKILL.md'
        skill.rename(skill.with_name('REAL.md'))
        skill.symlink_to('REAL.md')
    elif case == 'skills-ignored-tree':
        ignored = source / 'skills/not-a-skill'
        ignored.mkdir()
        (ignored / 'unused-link').symlink_to('../sample-skill')
    elif case == 'skill-folded':
        (source / 'skills/sample-skill/SKILL.md').write_text('---\nname:\n  sample-skill\ndescription:\n  folded description\n---\n')
    elif case == 'combined-order':
        (source / 'hooks/hooks.json').write_text('{"hooks":[]}')
        (source / 'mcp.json').write_text('{"mcpServers":[]}')
        (source / 'agents/sample-agent.md').write_text('broken\n')
        path = repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        artifact_map = catalog['plugins'][0]['artifacts']
        catalog['plugins'][0]['artifacts'] = {key: artifact_map[key] for key in reversed(artifact_map)}
        path.write_text(json.dumps(catalog))
        workspace = Workspace.load(repository, 'pstack')
        plugin = workspace.resolved.plugins[0]
    elif case in {'missing-artifact', 'unknown-kind', 'extra-source'}:
        path = repository / 'catalog/plugins.json'
        catalog = json.loads(path.read_text())
        if case == 'missing-artifact':
            catalog['plugins'][0]['artifacts']['agents']['path'] = 'sample/absent-agents'
        elif case == 'unknown-kind':
            catalog['plugins'][0]['artifacts']['custom-format'] = {'path': 'sample'}
        else:
            extra = dict(catalog['sources'][0])
            extra.update(id='absent-source', path='absent-source')
            catalog['sources'].append(extra)
        path.write_text(json.dumps(catalog))
        workspace = Workspace.load(repository, 'pstack')
        plugin = workspace.resolved.plugins[0]
    elif case == 'opaque-file':
        import shutil
        shutil.rmtree(source / '.cursor-plugin')
        (source / '.cursor-plugin').write_text('opaque bytes')
    artifacts = []
    expected = []
    for kind, path in plugin.artifact_paths.items():
        if not path.exists() or kind not in workspace.registry.artifact_inspectors:
            continue
        artifacts.append({'kind': str(kind), 'path': str(path)})
        try:
            value = build_registry().artifact_inspectors[kind].inspect(plugin)
            expected.append({'inventory': {'kind': str(value.kind), 'root': str(value.root),
                'components': [[item.name, item.path] for item in value.components],
                'representation': value.representation.value if value.representation else None}})
        except (OSError, ValueError) as error:
            expected.append({'error': str(error)})
    from agent_workbench.artifacts import inspect_catalog, component_counts
    inspection = inspect_catalog(repository, workspace.catalog, workspace.registry.artifact_inspectors)
    catalog_expected = {
        'plugins': [str(p.plugin.id) for p in inspection.plugins],
        'inventories': [[str(plugin_id), {'kind': str(kind), 'root': str(value.root),
            'components': [[item.name, item.path] for item in value.components],
            'representation': value.representation.value if value.representation else None}]
            for (plugin_id, kind), value in inspection.inventories.items()],
        'issues': [[i.code, i.severity.value, i.location, i.message] for i in inspection.issues],
        'counts': {str(k): v for k, v in component_counts(inspection.plugins, inspection.inventories).items()},
    }
    from agent_workbench.artifacts import semantic_exports
    from agent_workbench.adapters.projections import BUILTIN_CAPABILITIES, project_paths
    from agent_workbench.model import ComponentKind, Representation, TargetId
    projection_requests, projection_expected = [], []
    exports = tuple(e for inventory in inspection.inventories.values() for e in semantic_exports(inventory))
    policies = list(BUILTIN_CAPABILITIES.items())
    policies += [(TargetId('new-agent'), {ComponentKind.SKILLS: Representation.SKILL_DIRECTORY}),
                 (TargetId('cursor'), {ComponentKind.COMMANDS: Representation.LEGACY_COMMAND,
                                      ComponentKind.HOOKS: Representation.CLAUDE_HOOK_BUNDLE})]
    for policy_index, (target_id, capabilities) in enumerate(policies):
        for missing in (False, True):
            requested = (ComponentKind.RULES, ComponentKind.COMMANDS, ComponentKind.SKILLS,
                         ComponentKind.HOOKS, ComponentKind.AGENTS, ComponentKind.MCP)
            roots = {c: root / 'projection' / c.value for c in ComponentKind if not missing or c == ComponentKind.SKILLS}
            paths, conflicts = project_paths(target_id, capabilities, requested, exports, roots)
            projection_requests.append({'target': str(target_id),
                'capabilities': {c.value: rep.value for c, rep in capabilities.items()} if policy_index == 7 else None,
                'requested': [c.value for c in requested], 'roots': {c.value: str(p) for c, p in roots.items()}})
            projection_expected.append({'paths': [[p.key, str(p.export.plugin_id), p.export.component.value,
                p.export.name, str(p.export.source), p.export.representation.value, str(p.destination)] for p in paths],
                'conflicts': [[c.component.value, c.code, c.reason] for c in conflicts]})
    (base / 'input.json').write_text(json.dumps({'repository': str(repository), 'artifacts': artifacts,
                                               'projections': projection_requests}))
    before = snapshot(root)
    result = subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
                             '--lib', 'artifact_parity_tests::differential_inspection', '--', '--ignored', '--exact'],
                            cwd=REPO, env=dict(os.environ, AW_ARTIFACT_BASE=str(base)), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    actual = json.loads((base / 'result.json').read_bytes())
    assert actual == expected, {'python': expected, 'rust': actual}
    catalog_actual = json.loads((base / 'catalog-result.json').read_bytes())
    assert catalog_actual == catalog_expected, {'python': catalog_expected, 'rust': catalog_actual}
    projection_actual = json.loads((base / 'projection-result.json').read_bytes())
    assert projection_actual == projection_expected, {'python': projection_expected, 'rust': projection_actual}
    assert snapshot(root) == before, 'inspection mutated fixture'
    print(f'E1 private inventory {case}: {len(expected)} artifact rows, catalog aggregation, '
          f'{len(projection_expected)} registered-capability projections passed')

def main():
    assert os.environ.get('TMPDIR')
    cases = sys.argv[1:] or ['valid', 'document-link', 'document-root-file', 'mcp-array', 'mcp-duplicate',
                            'hook-duplicate', 'combined-order', 'missing-artifact', 'unknown-kind',
                            'extra-source', 'skill-missing-name', 'skill-unsafe-name', 'skill-crlf',
                            'skill-quotes', 'skill-missing-description', 'skill-folded', 'mcp-syntax', 'hook-array',
                            'skill-directory-link', 'skill-manifest-link', 'skills-ignored-tree',
                            'catalog-parent-file', 'unicode-frontmatter',
                            'mcp-bom', 'mcp-utf16', 'hook-bom', 'hook-utf16',
                            'mcp-utf8-truncated', 'mcp-utf8-continuation']
    with tempfile.TemporaryDirectory(prefix='e1-artifacts-', dir=os.environ['TMPDIR']) as raw:
        for case in cases:
            run_case(Path(raw), case)


if __name__ == '__main__':
    main()
