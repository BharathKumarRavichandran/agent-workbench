#!/usr/bin/env python3
"""E4 real public source updates, isolated reviewed-URL local Git fixtures."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

from parity_oracle import require_python312
from check_validate_parity import ROOT, RUST
from check_render_parity import fixture

UPSTREAM = 'https://example.invalid/reviewed-fixture.git'
GENERATED = ('.cursor-plugin/marketplace.json', '.claude-plugin/marketplace.json', 'catalog/lock.json')

# Literal ConfigParser fixtures; none is passed to Git as a declared remote.
CONFIG_CASES = {
    'config-duplicate-section': ('[submodule "source"]\npath=source\nurl={url}\n[submodule "source"]\n', 'DuplicateSectionError'),
    'config-missing-section': ('# comment\npath=source\n', 'MissingSectionHeaderError'),
    'config-malformed-lines': ('[submodule "source"]\npath=source\nurl={url}\nbroken\nalso broken\n', 'ParsingError'),
    'config-empty-key': ('[submodule "source"]\n=empty\npath=source\nurl={url}\n', 'ParsingError'),
    'config-empty-section': ('[]\npath=source\n', 'MissingSectionHeaderError'),
    'config-duplicate-case': ('[submodule "source"]\npath=source\nPaTh=source\n', 'DuplicateOptionError'),
    'config-default-duplicate': ('[DEFAULT]\npath=source\n[DEFAULT]\nPATH=source\n', 'DuplicateOptionError'),
    'config-parse-then-duplicate': ('[submodule "source"]\nbroken\npath=source\nPATH=source\n', 'DuplicateOptionError'),
    'config-malformed-eof': ('[submodule "source"]\npath=source\nurl={url}\nbroken', 'ParsingError'),
    'config-interpolation-syntax': ('[submodule "source"]\npath=%broken\nurl={url}\n', 'InterpolationSyntaxError'),
    'config-interpolation-reference': ('[submodule "source"]\npath=%(unterminated\nurl={url}\n', 'InterpolationSyntaxError'),
    'config-interpolation-missing': ('[submodule "source"]\npath=%(missing)s\nurl={url}\n', 'InterpolationMissingOptionError'),
    'config-interpolation-depth': ('[submodule "source"]\npath=%(path)s\nurl={url}\n', 'InterpolationDepthError'),
    'config-interpolation-nested': ('[DEFAULT]\nroot=%broken\n[submodule "source"]\npath=%(root)s\nurl={url}\n', 'InterpolationSyntaxError'),
    'config-interpolation-url': ('[submodule "source"]\npath=source\nurl=%(missing)s\n', 'InterpolationMissingOptionError'),
    'config-unicode-indent': ('[submodule "source"]\n\u00a0path = source\n  url = {url}\n', None),
    'config-control-whitespace': ('\x1c[submodule "source"]\npath = source\nurl = {url}\n', None),
    'config-blank-interpolation': ('[submodule "source"]\npath = source\n\n    %(missing)s\nurl = {url}\n', 'InterpolationMissingOptionError'),
    'config-blank-url': ('[submodule "source"]\npath = source\nurl = {url}\n\n    suffix\n', None),
    'config-empty-key-continuation': ('[submodule "source"]\n= ignored\n    not-an-option\npath = source\nurl = {url}\n', 'ParsingError'),
}


def git(root, env, *args):
    result = subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True, check=True)
    return result.stdout.strip()


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def setup(base, case):
    root = base / 'repository'
    upstream = base / 'upstream'
    home = base / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='file',
               GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1',
               GIT_AUTHOR_DATE='2020-01-01T00:00:00+00:00',
               GIT_COMMITTER_DATE='2020-01-01T00:00:00+00:00',
               GIT_AUTHOR_NAME='Fixture', GIT_COMMITTER_NAME='Fixture',
               GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_EMAIL='fixture@example.invalid',
               GIT_CONFIG_COUNT='3', GIT_CONFIG_KEY_0=f'url.{upstream.as_uri()}.insteadOf',
               GIT_CONFIG_VALUE_0=UPSTREAM, GIT_CONFIG_KEY_1='core.hooksPath',
               GIT_CONFIG_VALUE_1=os.devnull, GIT_CONFIG_KEY_2='commit.gpgsign', GIT_CONFIG_VALUE_2='false',
               LC_ALL='C', GIT_CONFIG_SYSTEM=os.devnull)
    upstream.mkdir()
    git(upstream, env, 'init', '-q', '-b', 'reviewed')
    put(upstream / 'skills/one/SKILL.md', '---\nname: one\ndescription: Old\n---\n')
    git(upstream, env, 'add', '.')
    git(upstream, env, 'commit', '-qm', 'old')
    old = git(upstream, env, 'rev-parse', 'HEAD')
    fixture(root)
    catalog = json.loads((root / 'catalog/plugins.json').read_text())
    catalog['sources'] = [{'id': 'sample', 'kind': 'git-submodule', 'path': 'source',
                           'upstream': UPSTREAM, 'branch': 'reviewed'}]
    catalog['plugins'] = [{'id': 'sample', 'source': 'sample', 'artifacts': {'agent-skills': {'path': 'skills'}}}]
    (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
    git(root, env, 'init', '-q', '-b', 'main')
    git(root, env, 'submodule', 'add', '-q', UPSTREAM, 'source')
    git(root, env, 'add', '.')
    git(root, env, 'commit', '-qm', 'fixture')
    put(upstream / 'skills/one/SKILL.md', '---\nname: one\ndescription: New\n---\n')
    git(upstream, env, 'add', '.')
    git(upstream, env, 'commit', '-qm', 'new')
    if case.startswith('nested-'):
        nested = base / 'nested-upstream'
        nested.mkdir()
        git(nested, env, 'init', '-q', '-b', 'main')
        put(nested / 'SKILL.md', 'invalid' if case == 'nested-invalid' else
            '---\nname: two\ndescription: Nested\n---\n')
        git(nested, env, 'add', '.')
        git(nested, env, 'commit', '-qm', 'nested')
        nested_url = 'https://example.invalid/nested-fixture.git'
        env.update(GIT_CONFIG_COUNT='4', GIT_CONFIG_KEY_3=f'url.{nested.as_uri()}.insteadOf',
                   GIT_CONFIG_VALUE_3=nested_url)
        git(upstream, env, 'submodule', 'add', '-q', nested_url, 'skills/two')
        if case == 'nested-git-failure':
            git(upstream, env, 'config', '-f', '.gitmodules', 'submodule.skills/two.url',
                (base / 'missing-local-repository').as_uri())
        git(upstream, env, 'add', '.')
        git(upstream, env, 'commit', '-qm', 'add nested')
    if case == 'candidate-invalid':
        put(upstream / 'skills/one/SKILL.md', 'invalid frontmatter')
        git(upstream, env, 'add', '.')
        git(upstream, env, 'commit', '-qm', 'invalid')
    new = git(upstream, env, 'rev-parse', 'HEAD')
    if case == 'reviewed-disagrees':
        git(upstream, env, 'checkout', '-qb', 'unreviewed', old.decode())
    if case == 'missing-branch':
        catalog['sources'][0]['branch'] = 'missing'
    elif case == 'checkout-dirty':
        put(root / 'source/skills/one/SKILL.md', '---\nname: one\ndescription: Dirty\n---\n')
    elif case == 'revision-failure':
        (root / 'source/.git').write_text('gitdir: /nonexistent/e4-isolated-no-repository\n')
    elif case == 'nondefault':
        profile = json.loads((root / 'profiles/default.json').read_text())
        profile['id'] = 'subset'
        put(root / 'profiles/subset.json', json.dumps(profile))
    elif case == 'local-source':
        catalog['sources'][0]['kind'] = 'local'
    elif case == 'curated-source':
        catalog['sources'][0]['kind'] = 'curated-copy'
        catalog['plugins'] = []
    elif case == 'no-registration':
        git(root, env, 'rm', '--cached', 'source')
    elif case == 'no-modules':
        (root / '.gitmodules').unlink()
    elif case == 'duplicate-path':
        with (root / '.gitmodules').open('a') as handle:
            handle.write('[submodule "other"]\npath = source\nurl = other\n')
    elif case in CONFIG_CASES:
        (root / '.gitmodules').write_text(CONFIG_CASES[case][0].replace('{url}', UPSTREAM))
    elif case == 'config-duplicate-key':
        (root / '.gitmodules').write_text('[submodule "source"]\npath = source\npath = source\nurl = ' + UPSTREAM + '\n')
    elif case == 'config-utf8':
        (root / '.gitmodules').write_bytes(b'[submodule "source"]\npath = source\nurl = \xff\n')
    elif case == 'config-case-colon':
        (root / '.gitmodules').write_text('; comment\n[submodule "source"]\nPATH: source\nURL: ' + UPSTREAM + '\n')
    elif case == 'config-unused-percent':
        with (root / '.gitmodules').open('a') as handle:
            handle.write('unused = %broken\n')
    elif case == 'config-defaults':
        (root / '.gitmodules').write_text('[DEFAULT]\nroot = source\norigin = ' + UPSTREAM +
                                         '\n[submodule "source"]\npath = %(root)s\nurl = %(origin)s\n')
    elif case == 'wrong-url':
        (root / '.gitmodules').write_text('[submodule "source"]\npath = source\nurl = https://example.invalid/other\n')
    elif case in ('source-link-inside', 'source-link-outside'):
        (root / 'source').rename(root / 'real-source')
        if case == 'source-link-inside':
            (root / 'source').symlink_to('real-source')
        else:
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'sentinel').write_bytes(b'untouched')
            (root / 'source').symlink_to(outside)
    elif case == 'output-link':
        put(root / 'foreign', 'preserve')
        (root / 'catalog/lock.json').symlink_to('../foreign')
    elif case == 'partial-render-restore':
        put(root / GENERATED[0], 'saved cursor')
        (root / GENERATED[0]).chmod(0o640)
        put(root / '.claude-plugin', 'non-directory parent')
    elif case == 'render-failure':
        (root / '.claude-plugin/marketplace.json').mkdir(parents=True)
    elif case == 'rollback-modes':
        catalog['sources'][0]['branch'] = 'missing'
        for relative in GENERATED:
            put(root / relative, 'old generated bytes')
            (root / relative).chmod(0o640)
    elif case.startswith('branch:'):
        catalog['sources'][0]['branch'] = case.partition(':')[2]
    elif case.startswith('path:'):
        catalog['sources'][0]['path'] = case.partition(':')[2]
    (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
    return root, env, old, new


def tree(root):
    """Complete working tree types/modes/bytes/literal links; Git has its own observer."""
    result = []
    def visit(path):
        for child in sorted(path.iterdir()):
            if child.name == '.git' and child.is_dir():
                continue  # Administrative database is compared semantically below, not hidden as application state.
            relative = child.relative_to(root).as_posix()
            mode = child.lstat().st_mode
            if child.is_symlink():
                result.append((relative, mode, os.readlink(child)))
            elif child.is_dir():
                result.append((relative, mode, None))
                visit(child)
            else:
                result.append((relative, mode, child.read_bytes()))
    visit(root)
    return result


def git_state(root, env):
    result = []
    for path in (root, root / 'source', root / 'source/skills/two'):
        if not path.is_dir():
            continue
        for args in [('rev-parse', 'HEAD'), ('symbolic-ref', '-q', 'HEAD'),
                     ('status', '--porcelain=v1'), ('ls-files', '--stage'), ('show-ref',),
                     ('config', '--local', '--list'), ('reflog', '--format=%gs')]:
            process = subprocess.run(['git', '-C', str(path), *args], env=env, capture_output=True)
            result.append((str(path.relative_to(root)), args, process.returncode, process.stdout, process.stderr))
    return result


def config_diagnostic(output):
    """UPDATE-CONFIG-1 retains the entire final class/message, not stack frames."""
    import re
    assert output.startswith(b'Traceback (most recent call last):\n'), output
    final = re.search(rb'^configparser\.[A-Za-z]+Error: ', output, re.MULTILINE)
    assert final, output
    return output[final.start():]


def run_case(base, case):
    root, env, old, new = setup(base, case)
    saved = base / 'saved'
    config_class = CONFIG_CASES.get(case, ('', None))[1]
    if case == 'config-duplicate-key':
        config_class = 'DuplicateOptionError'
    shutil.copytree(root, saved, symlinks=True)
    rows = []
    for engine in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
        options = ['--profile', 'subset'] if case == 'nondefault' else []
        source = 'missing' if case == 'unknown-source' else 'sample'
        command = [*engine, '--root', str(root), *options, 'update', '--source', source]
        before = tree(root)
        before_git = git_state(root, env)
        result = subprocess.run(command,
                                cwd=ROOT, env=env, capture_output=True, timeout=60, umask=0o022)
        stderr = result.stderr
        if config_class:
            assert result.returncode == 1 and not result.stdout, (case, engine, result)
            if engine[0] == sys.executable:
                stderr = config_diagnostic(stderr)
            assert stderr.startswith(f'configparser.{config_class}:'.encode()), stderr
        elif case in ('missing-branch', 'rollback-modes', 'revision-failure', 'checkout-dirty', 'nested-git-failure'):
            assert result.returncode == 1, result
            assert result.stdout == (b'M\tskills/one/SKILL.md\n' if case == 'checkout-dirty' else b''), result
            if engine[0] == sys.executable:
                stderr, separator, traceback = stderr.partition(b'Traceback (most recent call last):\n')
                assert separator and b'subprocess.CalledProcessError:' in traceback, result
            else:
                assert b'Traceback' not in stderr, result
        elif case in ('success', 'config-defaults', 'config-case-colon', 'config-unused-percent', 'config-control-whitespace', 'nested-success', 'reviewed-disagrees'):
            assert result.returncode == 0, (engine, result)
            assert result.stdout.endswith(b'sample: ' + old + b' -> ' + new + b'\n'), result
        else:
            assert result.returncode == 2 and (not result.stdout or case == 'nested-invalid') and b'error: ' in stderr, result
        if case == 'rollback-modes':
            assert all((root / relative).read_bytes() == b'old generated bytes' and
                       (root / relative).stat().st_mode & 0o777 == 0o600 for relative in GENERATED)
        if case == 'partial-render-restore':
            assert (root / GENERATED[0]).read_bytes() == b'saved cursor'
            assert (root / GENERATED[0]).stat().st_mode & 0o777 == 0o600
        after = tree(root)
        if case in ('nondefault', 'local-source', 'curated-source', 'unknown-source',
                    'no-registration', 'no-modules', 'duplicate-path', 'wrong-url',
                    'source-link-inside', 'source-link-outside', 'output-link', 'revision-failure') or case.startswith(('branch:', 'path:')):
            assert before == after, (case, 'pre-mutation rejection changed working tree')
        if case == 'source-link-outside':
            assert (base / 'outside/sentinel').read_bytes() == b'untouched'
        observed_git = git_state(root, env)
        if config_class:
            assert before == after and before_git == observed_git, (case, 'config failure mutated state')
        shutil.rmtree(root)
        shutil.copytree(saved, root, symlinks=True)
        # A second actual CLI execution with one descriptor pins cross-stream ordering,
        # independently from the exact separate stdout/stderr comparison above.
        merged = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60, umask=0o022)
        ordered = merged.stdout
        if engine[0] == sys.executable and config_class:
            ordered = config_diagnostic(ordered)
        elif engine[0] == sys.executable and result.returncode == 1:
            ordered, separator, traceback = ordered.partition(b'Traceback (most recent call last):\n')
            assert separator and b'subprocess.CalledProcessError:' in traceback, merged
        assert merged.returncode == result.returncode and tree(root) == after, (case, merged)
        assert git_state(root, env) == observed_git, (case, 'merged replay Git drift')
        rows.append((result.returncode, result.stdout, stderr, after, observed_git, ordered))
        shutil.rmtree(root)
        shutil.copytree(saved, root, symlinks=True)
    for label, expected, actual in zip(('status', 'stdout', 'stderr', 'typed working tree', 'Git semantic state', 'merged stream order'), rows[0], rows[1]):
        assert expected == actual, (case, label, expected, actual)
    print(f'update parity: {case} passed')


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    cases = sys.argv[1:] or ['success', 'missing-branch', 'rollback-modes', 'revision-failure',
                             'checkout-dirty', 'candidate-invalid', 'nondefault', 'local-source',
                             'curated-source', 'unknown-source', 'no-registration', 'no-modules',
                             'duplicate-path', 'wrong-url', 'source-link-inside', 'source-link-outside',
                             'output-link', 'render-failure', 'branch:--option', 'branch:a..b',
                             'branch:a b', 'branch:a/', 'branch:a@{b', 'path:../outside', 'path:/outside', 'config-defaults', 'config-case-colon',
                             'config-unused-percent', 'nested-success', 'nested-invalid', 'nested-git-failure',
                             'reviewed-disagrees', 'partial-render-restore', 'config-utf8', 'config-duplicate-key', *CONFIG_CASES]
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='update-parity-', dir=os.environ['TMPDIR']) as raw:
            run_case(Path(raw), case)
    print(f'update parity: {len(cases)} scenarios, {len(cases) * 2} paired public executions passed')


if __name__ == '__main__':
    main()
