"""D5 construction-only differential driver; Python production stays frozen.

Run directly, or call authority_rows(base) from the main parity gate.
Every fixture and cargo target is under the supplied scratch root.
Partial-write rows really buffer seven bytes before raising; context-manager
close flushes that retained prefix. Flush rows raise after the real flush has
published all bytes (not a pre-write approximation). Close rows close the real
handle after fsync before raising, skipping parent sync/rename. The cleanup row
independently reaches failed rename and failed unlink, retaining the signed stage.
Rust faults write through the actual held descriptor and exist only in cfg(test).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile
from unittest.mock import patch


def snapshot(root):
    """Literal residual names, types, modes, link targets, and bytes (no name folding)."""
    result = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
                'directory' if stat.S_ISDIR(info.st_mode) else
                'file' if stat.S_ISREG(info.st_mode) else 'other')
        result[str(path.relative_to(root))] = (
            kind, stat.S_IMODE(info.st_mode), info.st_nlink,
            os.readlink(path) if kind == 'symlink' else
            path.read_bytes().hex() if kind == 'file' else None,
        )
    return result


def outcome(operation):
    try:
        operation()
        return {'accepted': True}
    except (OSError, ValueError) as error:
        return {'accepted': False, 'error_class':
                'ValueError' if isinstance(error, ValueError) else 'OSError'}


def authority_rows(base: Path, only=None):
    from agent_workbench import bundles, shared_config as shared, transactions
    repo = Path(__file__).resolve().parents[3]
    base = base.resolve()
    scratch = Path(os.environ['TMPDIR']).resolve()
    assert base.is_relative_to(scratch), (base, scratch)
    base.mkdir(parents=True)
    scenarios = ['key-create', 'key-chmod', 'key-entropy', 'key-partial-write',
                 'key-flush', 'key-close', 'journal-partial-write',
                 'journal-flush', 'journal-close', 'journal-cleanup',
                 'key-public', 'key-short']
    if only:
        scenarios = [only]
    env = dict(os.environ, TMPDIR=str(scratch), AW_AUTHORITY_BASE=str(base),
               CARGO_TARGET_DIR=str(scratch / 'authority-cargo-target'))
    for scenario in scenarios:
        case = base / scenario
        case.mkdir()
        config = case / 'config'
        config.mkdir()
        file = case / 'state.json'
        key = case / '.state.json.transaction-key'
        journal = config / '.agent-workbench-transaction.json'
        record = {'version': 1, 'stateFile': str(file), 'parents': {},
                  'stateBefore': None, 'stateAfter': None, 'documents': [], 'paths': []}

        def setup():
            if scenario in ('key-public', 'key-short'):
                key.write_bytes(bytes(range(7 if scenario == 'key-short' else 32)))
                key.chmod(0o644 if scenario == 'key-public' else 0o600)
            if scenario.startswith('journal-'):
                key.write_bytes(bytes(range(32)))
                key.chmod(0o600)
                journal.write_bytes(b'{"old":true}\n')
            # Untrusted lookalike names must survive literally, with their types.
            (config / '.agent-workbench-json-UNTRUSTED').write_bytes(b'foreign')
            (config / '.agent-workbench-json-link').symlink_to('missing-untrusted')

        setup()
        hits = []
        real_open, real_chmod, real_fdopen = os.open, os.fchmod, os.fdopen
        real_replace, real_unlink = os.replace, os.unlink

        def opened(name, flags, *args, **kwargs):
            if scenario == 'key-create' and flags & os.O_EXCL:
                hits.append('KeyCreate')
                raise OSError('authority injected create failure')
            return real_open(name, flags, *args, **kwargs)

        def chmod(fd, mode):
            if scenario == 'key-chmod':
                hits.append('KeyChmod')
                raise OSError('authority injected chmod failure')
            return real_chmod(fd, mode)

        def entropy(n):
            if scenario == 'key-entropy':
                hits.append('KeyEntropy')
                raise OSError('authority injected entropy failure')
            return bytes(range(n))

        class BufferedFailure:
            def __init__(self, handle):
                self.handle = handle
                self.failed = False
            def __enter__(self):
                self.handle.__enter__()
                return self
            def __exit__(self, *args):
                # Real buffered close flushes bytes buffered by a partial write.
                result = self.handle.__exit__(*args)
                if scenario.endswith('-close'):
                    hits.append(scenario)
                    raise OSError('authority injected close failure')
                return result
            def fileno(self):
                return self.handle.fileno()
            def write(self, content):
                if scenario.endswith('-partial-write'):
                    self.handle.write(content[:7])
                    hits.append(scenario)
                    raise OSError('authority injected partial buffered write failure')
                return self.handle.write(content)
            def flush(self):
                self.handle.flush()
                if scenario.endswith('-flush'):
                    hits.append(scenario)
                    raise OSError('authority injected post-flush failure')

        def fdopen(fd, mode, *args, **kwargs):
            handle = real_fdopen(fd, mode, *args, **kwargs)
            return BufferedFailure(handle) if mode == 'wb' else handle

        def replace(*args, **kwargs):
            if scenario == 'journal-cleanup':
                hits.append('JournalRename')
                raise OSError('authority injected stage publication failure')
            return real_replace(*args, **kwargs)

        def unlink(name, *args, **kwargs):
            if scenario == 'journal-cleanup' and str(name).startswith('.agent-workbench-json-'):
                hits.append('journal-cleanup')
                raise OSError('authority injected cleanup failure')
            return real_unlink(name, *args, **kwargs)

        def operation():
            if scenario.startswith('key-'):
                transactions._journal_key(file, True)
            else:
                signed = dict(record)
                signed['mac'] = hmac.new(transactions._journal_key(file, True),
                                         transactions._canonical(record), hashlib.sha256).hexdigest()
                parent = bundles.open_directory(config)
                try:
                    shared.replace_document(journal, shared.encode(signed), parent)
                finally:
                    os.close(parent)

        with patch.object(os, 'open', opened), patch.object(os, 'fchmod', chmod), \
             patch.object(os, 'urandom', entropy), patch.object(os, 'fdopen', fdopen), \
             patch.object(os, 'replace', replace), patch.object(os, 'unlink', unlink), \
             patch.object(secrets, 'token_hex', lambda n: bytes(range(n)).hex()):
            expected = outcome(operation)
        assert hits or scenario in ('key-public', 'key-short'), (scenario, 'Python fault never reached')
        before_retry = snapshot(case)
        with patch.object(os, 'urandom', lambda n: bytes(range(n))):
            retry = outcome(lambda: transactions._journal_key(file, True)) if scenario.startswith('key-') else None
        after_retry = snapshot(case)
        if scenario != 'key-create':
            assert before_retry == after_retry, (scenario, 'retry repaired authority')
        if scenario in ('key-chmod', 'key-entropy', 'key-partial-write', 'key-public', 'key-short'):
            assert retry == {'accepted': False, 'error_class': 'ValueError'}, (scenario, retry)
        expected = {'outcome': expected, 'hits': hits, 'retry': retry}
        expected_snapshot = snapshot(case)
        shutil.rmtree(case)
        case.mkdir()
        config.mkdir()
        setup()
        row = {'scenario': scenario, 'file': str(file), 'config': str(config),
               'record_json': list(shared.encode(record))}
        (base / 'row.json').write_text(json.dumps(row))
        result = subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(repo / 'rust/Cargo.toml'),
                                 '--lib', 'transaction::authority_parity_tests::differential_authority',
                                 '--', '--ignored', '--exact'], env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        actual = json.loads((base / 'result.json').read_text())
        assert actual == expected, (scenario, expected, actual)
        assert snapshot(case) == expected_snapshot, (scenario, expected_snapshot, snapshot(case))
    print(f'authority construction parity: {len(scenarios)} rows passed; independently reached faults, literal typed residue, retries')


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    with tempfile.TemporaryDirectory(prefix='authority-contract-', dir=os.environ['TMPDIR']) as root:
        authority_rows(Path(root) / 'rows', sys.argv[1] if len(sys.argv) > 1 else None)
