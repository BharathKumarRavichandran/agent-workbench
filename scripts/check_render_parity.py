#!/usr/bin/env python3
"""E3 public deterministic render differential at identical lexical paths."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from parity_oracle import require_python312
from check_validate_parity import ROOT, RUST, snapshot


def git_env():
    return dict(
        os.environ,
        GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_OPTIONAL_LOCKS='0',
        GIT_AUTHOR_DATE='2020-01-01T00:00:00+00:00',
        GIT_COMMITTER_DATE='2020-01-01T00:00:00+00:00',
    )


def git(source, *arguments):
    subprocess.run(
        ['git', '-C', str(source), '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', *arguments],
        env=git_env(),
        check=True,
        capture_output=True,
    )


def fixture(root):
    (root / 'catalog').mkdir(parents=True)
    (root / 'profiles').mkdir()
    catalog = {'schemaVersion': 2, 'marketplace': {'id': 'sample', 'name': 'Sample',
               'owner': {'name': 'Owner'}, 'description': 'Description'}, 'sources': [], 'plugins': []}
    profile = {'schemaVersion': 1, 'id': 'default', 'plugins': {'include': ['*'], 'exclude': []}, 'targets': []}
    (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
    (root / 'profiles/default.json').write_text(json.dumps(profile))


def setup_case(root, case):
    fixture(root)
    if case == 'empty':
        return
    if case in ('native', 'mixed'):
        catalog = json.loads((root / 'catalog/plugins.json').read_text())
        catalog['marketplace']['name'] = 'Márket 🚀'
        catalog['sources'] = [{'id': 'local', 'kind': 'local', 'path': 'source'}]
        catalog['plugins'] = [{'id': 'sample', 'source': 'local', 'artifacts': {
            'cursor-plugin': {'path': 'native'}, 'claude-plugin': {'path': 'native'}}}]
        (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
        for agent in ('cursor', 'claude'):
            parent = root / f'source/native/.{agent}-plugin'
            parent.mkdir(parents=True)
            (parent / 'plugin.json').write_text(json.dumps({
                'name': 'sample', 'description': 'Description é 🚀',
                'version': {'z': 1e-7, 'a': [False, 123456789012345678901234567890]}}))
        if case == 'mixed':
            artifacts = catalog['plugins'][0]['artifacts']
            for kind, path in [('agent-skills', 'skills'), ('agents', 'agents'), ('rules', 'rules'),
                               ('commands', 'commands'), ('hooks', 'hooks'), ('mcp', 'mcp.json')]:
                artifacts[kind] = {'path': path}
            files = {'skills/one/SKILL.md': '---\nname: one\ndescription: Sample\n---\n',
                     'agents/reviewer.md': '---\nname: reviewer\ndescription: Sample\n---\n',
                     'rules/sample.mdc': '---\ndescription: Sample\n---\n',
                     'commands/sample.md': '---\ndescription: Sample\n---\n',
                     'hooks/hooks.json': '{"version":1,"hooks":{"stop":[{"command":"not-executed"}]}}',
                     'mcp.json': '{"mcpServers":{"remote":{"url":"https://example.invalid/not-contacted"}}}'}
            for relative, text in files.items():
                path = root / 'source' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
    elif case == 'nondefault':
        profile = json.loads((root / 'profiles/default.json').read_text())
        profile['id'] = 'subset'
        (root / 'profiles/subset.json').write_text(json.dumps(profile))
    elif case == 'malformed-catalog':
        (root / 'catalog/plugins.json').write_text('{')
    elif case == 'catalog-parent-symlink':
        (root / 'catalog').rename(root / 'real-catalog')
        (root / 'catalog').symlink_to('real-catalog')
    elif case == 'readonly-root':
        assert os.geteuid() != 0, 'readonly-root requires non-root execution'
        root.chmod(0o555)
    elif case in ('readonly-cursor-parent', 'readonly-claude-parent'):
        assert os.geteuid() != 0, 'readonly parents require non-root execution'
        agent = case.split('-')[1]
        (root / f'.{agent}-plugin').mkdir(mode=0o555)
    elif case == 'catalog-lock-symlink':
        (root / 'foreign').write_bytes(b'preserve')
        (root / 'catalog/lock.json').symlink_to('../foreign')
    elif case == 'cursor-parent-symlink':
        (root / 'foreign').mkdir()
        (root / '.cursor-plugin').symlink_to('foreign')
    elif case == 'cursor-parent-file':
        (root / '.cursor-plugin').write_bytes(b'foreign parent')
    elif case == 'output-directory':
        (root / '.claude-plugin/marketplace.json').mkdir(parents=True)
    elif case == 'parent-file':
        (root / '.claude-plugin').write_bytes(b'foreign parent')
    elif case in ('output-symlink', 'parent-symlink'):
        (root / 'foreign').mkdir()
        (root / 'foreign/sentinel').write_bytes(b'preserve')
        if case == 'parent-symlink':
            (root / '.claude-plugin').symlink_to('foreign')
        else:
            (root / '.claude-plugin').mkdir()
            (root / '.claude-plugin/marketplace.json').symlink_to('../foreign/sentinel')
    elif case in ('git-nonrepository', 'git-no-head'):
        catalog = json.loads((root / 'catalog/plugins.json').read_text())
        catalog['sources'] = [{'id': 'sample', 'kind': 'git-submodule', 'path': 'source',
                               'upstream': 'https://example.invalid/not-contacted', 'branch': 'main'}]
        (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
        (root / 'source').mkdir()
        if case == 'git-no-head':
            git(root / 'source', 'init', '-q')
    elif case == 'git-valid':
        catalog = json.loads((root / 'catalog/plugins.json').read_text())
        catalog['sources'] = [{'id': 'sample', 'kind': 'git-submodule', 'path': 'source',
                               'upstream': 'https://example.invalid/not-contacted', 'branch': 'main'}]
        (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
        source = root / 'source'
        source.mkdir()
        (source / 'README').write_text('fixture')
        git(source, 'init', '-q', '-b', 'main')
        git(source, 'add', 'README')
        git(
            source,
            '-c',
            'user.name=Fixture',
            '-c',
            'user.email=fixture@example.invalid',
            '-c',
            'commit.gpgsign=false',
            'commit',
            '-qm',
            'fixture',
        )
        assert not (source / '.git/objects/maintenance.lock').exists()
    elif case == 'stale-modes':
        for rel in ('.cursor-plugin/marketplace.json', '.claude-plugin/marketplace.json', 'catalog/lock.json'):
            path = root / rel
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'stale')
            path.chmod(0o640)
    else:
        raise AssertionError(case)


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    cases = sys.argv[1:] or ['empty', 'native', 'mixed', 'git-valid', 'stale-modes', 'nondefault', 'malformed-catalog',
                             'output-directory', 'parent-file', 'output-symlink', 'parent-symlink',
                             'git-nonrepository', 'git-no-head', 'catalog-parent-symlink',
                             'readonly-root', 'cursor-parent-symlink', 'cursor-parent-file',
                             'readonly-cursor-parent', 'readonly-claude-parent', 'catalog-lock-symlink']
    total_rows = 0
    with tempfile.TemporaryDirectory(prefix='render-parity-', dir=os.environ['TMPDIR']) as raw:
        root = Path(raw) / 'repository'
        for case in cases:
            rows = []
            git_index = None
            for command in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
                try:
                    setup_case(root, case)
                    if case == 'git-valid':
                        # Fixture-only Git index embeds setup inode/timestamp metadata.
                        # Reuse its initial bytes; neither renderer mutates it.
                        index_path = root / 'source/.git/index'
                        if git_index is None:
                            git_index = index_path.read_bytes()
                        else:
                            index_path.write_bytes(git_index)
                    outputs = []
                    for flags in (['--check'], [], ['--check'], []):
                        before = snapshot(root)
                        prefix = ['--profile', 'subset'] if case == 'nondefault' else []
                        result = subprocess.run([*command, '--root', str(root), *prefix, 'render', *flags],
                                                cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                                capture_output=True, timeout=30, umask=0o022)
                        typed = [(row[0], row[1], row[-1]) for row in snapshot(root)]
                        stderr = result.stderr
                        if case in ('readonly-cursor-parent', 'readonly-claude-parent') and not flags:
                            pattern = rb'\.marketplace\.json\.[0-9a-f]{16}(?=[\'\"])'
                            assert len(re.findall(pattern, stderr)) == 1, stderr
                            stderr = re.sub(pattern, b'.marketplace.json.<nonce>', stderr)
                        if case in ('git-nonrepository', 'git-no-head'):
                            assert result.returncode == 1 and result.stdout == b'', result
                            if command[0] == sys.executable:
                                stderr, separator, traceback = stderr.partition(b'Traceback (most recent call last):\n')
                                assert separator and b'subprocess.CalledProcessError:' in traceback, result
                            else:
                                assert b'Traceback' not in stderr and b'error:' not in stderr, result
                            assert snapshot(root) == before, 'Git failure mutated fixture'
                        outputs.append((result.returncode, result.stdout, stderr, typed))
                        if flags or (case in ('empty', 'native', 'mixed', 'git-valid', 'stale-modes') and len(outputs) == 4):
                            assert snapshot(root) == before, 'check/no-op render mutated fixture'
                    if case in ('catalog-parent-symlink', 'catalog-lock-symlink'):
                        for flags in (['--json'], ['--json', '--no-generated-check']):
                            before = snapshot(root)
                            result = subprocess.run([*command, '--root', str(root), 'validate', *flags],
                                                    cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                                    capture_output=True, timeout=30, umask=0o022)
                            outputs.append((result.returncode, result.stdout, result.stderr,
                                            [(row[0], row[1], row[-1]) for row in snapshot(root)]))
                            assert snapshot(root) == before, 'validation mutated fixture'
                    rows.append(outputs)
                finally:
                    if root.exists():
                        root.chmod(0o755)
                        for directory in root.rglob("*"):
                            if directory.is_dir() and not directory.is_symlink():
                                directory.chmod(0o755)
                        shutil.rmtree(root)
            assert rows[0] == rows[1], (case, rows)
            total_rows += len(rows[0])
            print(f'render parity: {case} {len(rows[0])} paired public rows passed')
        print(f'render parity: {total_rows} paired public rows passed')


if __name__ == '__main__':
    main()
