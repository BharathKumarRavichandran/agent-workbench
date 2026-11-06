#!/usr/bin/env python3
"""E6 strict public help/usage and Unicode negative-value differentials."""
from __future__ import annotations

import difflib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from parity_oracle import require_python312

RUST = REPO / 'rust/target/debug/agent-workbench-rs'
COMMANDS = ('', 'validate', 'render', 'plan', 'install', 'status', 'trust', 'uninstall', 'update')


def rows(group):
    if group in ('all', 'unicode'):
        for value in ('-١', '-۱۲', '-१.٢', '-.１', '-𝟡', '-²', '-½', '-一', '-١.', '-١e٢', '-١\n'):
            accepted = value in ('-١', '-۱۲', '-१.٢', '-.１', '-𝟡', '-١\n')
            for command, key in (('', '--profile'), ('plan', '--target'), ('update', '--source')):
                yield f'unicode:{command or "root"}:{value!r}', '80', ([command] if command else []) + [key, value, '--help'], 0 if accepted else 2
        # A single public operand covers the complete frozen Unicode Nd repertoire.
        decimal = ''.join(chr(i) for i in range(sys.maxunicode + 1) if unicodedata.category(chr(i)) == 'Nd')
        for command, key in (('', '--profile'), ('plan', '--target'), ('update', '--source')):
            yield f'unicode:{command or "root"}:all-Nd', '80', ([command] if command else []) + [key, '-' + decimal, '--help'], 0
        # Adjacent non-Nd code points prevent silently widening to is_numeric().
        boundaries = sorted({ord(c) + delta for c in decimal for delta in (-1, 1)
                             if unicodedata.category(chr(ord(c) + delta)) != 'Nd'})
        for point in boundaries:
            yield f'unicode:non-Nd-boundary:{point:x}', '80', ['plan', '--target', '-' + chr(point), '--help'], 2
    if group in ('all', 'width'):
        for columns in (None, '', 'invalid', '20', '40', '60', '80', '100', '120', '1', '0', '-1', '2147483647', '9223372036854775808', '８０', '8_0', '  +80  ', '²', '_80', '80_', '8__0', '8.0', '9' * 4300, '9' * 4301):
            for command in COMMANDS:
                prefix = [command] if command else []
                yield f'width:{columns!r}:{command or "root"}:help', columns, prefix + ['--help'], 0
                if command not in ('validate', 'render'):
                    yield f'width:{columns!r}:{command or "root"}:required', columns, prefix, 2
                else:
                    yield f'width:{columns!r}:{command}:error', columns, prefix + ['--help=bad'], 2
        # Bounded sweeps target prog-length and hyphen/description wrap transitions.
        for columns in range(20, 81):
            yield f'wrap-threshold:{columns}:plan', str(columns), ['plan', '--help'], 0
        for columns in range(27, 45):
            yield f'prog-threshold:{columns}:root', str(columns), ['--help'], 0


def main():
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    group = sys.argv[1] if len(sys.argv) > 1 else 'all'
    failures = []
    count = 0
    with tempfile.TemporaryDirectory(prefix='e6-help-', dir=os.environ['TMPDIR']) as name:
        base = Path(name)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_STATE_HOME=str(base / 'state'),
                   PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
        for label, columns, argv, expected in rows(group):
            env.pop('COLUMNS', None)
            if columns is not None:
                env['COLUMNS'] = columns
            results = []
            for prefix in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
                p = subprocess.run([*prefix, *argv], cwd=base, env=env, capture_output=True)
                results.append((p.returncode, p.stdout, p.stderr))
            assert results[0][0] == expected, (label, 'oracle expectation', results[0])
            count += 1
            if results[0] != results[1]:
                failures.append(label)
                if len(failures) <= 4:
                    print(label, 'statuses', results[0][0], results[1][0])
                    for i in (1, 2):
                        print(''.join(difflib.unified_diff(results[0][i].decode().splitlines(True), results[1][i].decode().splitlines(True), fromfile='Python', tofile='Rust')))
        assert not list(base.rglob('*')), 'help/parser rejection unexpectedly mutated fixture'
    print(f'E6 help edges: {count - len(failures)}/{count} public pairs GREEN; {len(failures)} mismatches')
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
