#!/usr/bin/env python3
"""D5 real interrupted-journal surrogate acceptance and authentication parity.

Run: TMPDIR=<scratch> python3 rust/src/transaction/wire_contract.py
No production Python changes; reuse authorized parent inodes for both engines.
"""
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / 'rust/src/transaction'), str(REPO), str(REPO / 'tests')]
from apply_contract import ActivationTests, C, installation, serialize, transactions
from agent_workbench import shared_config


def snapshot(root):
    result = []
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        kind = 'link' if p.is_symlink() else 'dir' if p.is_dir() else 'file'
        content = os.readlink(p) if kind == 'link' else p.read_bytes().hex() if kind == 'file' else None
        result.append((str(p.relative_to(root)), kind, stat.S_IMODE(s.st_mode), content))
    return result


def run_case(case):
    fixture = ActivationTests()
    fixture.setUp()
    try:
        plan = fixture.trusted((C.HOOKS, C.MCP, C.SKILLS))
        request = serialize(plan)
        plan.state_file.parent.mkdir(parents=True, exist_ok=True)
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        key.write_bytes(bytes(range(32)))
        key.chmod(0o600)
        original = shared_config.replace_document
        hit = False
        def interrupt(path, *args, **kwargs):
            nonlocal hit
            result = original(path, *args, **kwargs)
            if path.name == '.agent-workbench-transaction.json':
                hit = True
                raise KeyboardInterrupt('after durable journal, before resource mutation')
            return result
        with patch.object(shared_config, 'replace_document', interrupt):
            try:
                installation.apply_plan(plan)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError('expected interruption')
        assert hit
        roots = {c: Path(p) for c, p in plan.next_state.destination_roots.items()}
        journal = transactions._journal_path(roots)
        record = json.loads(journal.read_bytes())
        row = next(p for p in record['paths'] if p['resource'] == 'directory-copy')
        assert row['before'] is None and not Path(row['path']).exists()
        replay_live = None
        if case in ('link-low-capture', 'link-low-restore'):
            row = next(p for p in record['paths'] if p['resource'] == 'directory-symlink')
            raw_target = b'not-present-\xff'
            row['before'] = {'link': os.fsdecode(raw_target)}
            if case == 'link-low-capture':
                os.symlink(raw_target, os.fsencode(row['path']))
                replay_live = raw_target
                row['after'] = row['before']
        elif case in ('tree-low-restore', 'basename-low-restore', 'basename-low-inert'):
            if case == 'tree-low-restore':
                row['before'] = {'tree': {'directories': [['', 0o755]], 'files': [['raw-\udcff', 0o600, 'eA==']]}}
            else:
                row['path'] = str(Path(row['path']).with_name('managed-\udcff'))
                if case == 'basename-low-restore':
                    row['before'] = {'tree': {'directories': [['', 0o755]], 'files': [['content', 0o600, 'eA==']]}}
        elif case == 'tree-high-restore':
            row['before'] = {'tree': {
                'directories': [['', 0o755], ['safe', 0o750]],
                'files': [['safe/prefix', 0o600, 'cHJlZml4'], ['safe/raw-\ud800', 0o600, 'eA==']],
            }}
        elif case.startswith('raw-'):
            # The two-codepoint key must NOT merge: only adjacent JSON escapes
            # combine, unlike a raw high surrogate followed by an escaped low.
            record['\ud800\udc00'] = 'raw-high-plus-escaped-low'
            record['\ud800'] = '\udcff'
            record['rawValue'] = '\ud800\udc00'
            record['\ue000'] = 'BMP sorts before supplementary, after surrogate'
            record['\U00010000'] = 'escaped supplementary pair'
        elif case.startswith('invalid-utf8-'):
            record['auditNote'] = 'INVALID_UTF8'
        elif case in ('top-high', 'top-high-utf16', 'top-high-utf32'):
            record['auditNote'] = '\ud800'
        elif case == 'path-field-low':
            row['auditNote'] = '\udcff'
        elif case == 'unused-tree-path-low':
            row['after']['tree']['files'].append(['unused-\udcff', 0o600, 'eA=='])
            assert transactions._tree(row['after']['tree']).files[-1].path == 'unused-\udcff'
        elif case in ('unknown-keys', 'tamper-value', 'tamper-key'):
            record['\ud800'] = {'\udcff': ['\ud800', '\U00010000', '\ue000']}
            record['\U00010000'] = 'supplementary key sorts after surrogate and BMP'
            record['\ue000'] = 'BMP key'
        elif case != 'control':
            raise AssertionError(case)
        record.pop('mac')
        record['mac'] = hmac.new(bytes(range(32)), transactions._canonical(record), hashlib.sha256).hexdigest()
        if case == 'tamper-value':
            record['\ud800']['\udcff'][0] = '\ud801'
        elif case == 'tamper-key':
            record['\ud801'] = record.pop('\ud800')
        saved = (json.dumps(record, ensure_ascii=True, indent=2) + '\n').encode('ascii')
        if case.startswith('raw-'):
            text = saved.decode('ascii').replace(
                '"\\ud800\\udc00": "raw-high-plus-escaped-low"',
                '"\ud800\\udc00": "raw-high-plus-escaped-low"',
            ).replace('"\\ud800":', '"\ud800":').replace('"\\udcff"', '"\udcff"').replace(
                '"rawValue": "\\ud800\\udc00"', '"rawValue": "\ud800\\udc00"',
            )
            saved = text.encode(case.removeprefix('raw-'), errors='surrogatepass')
            decoded = json.loads(saved)
            assert decoded == record, (case, repr(decoded))
            assert '\ud800\udc00' in decoded and '\U00010000' in decoded
            print(f'{case}: Python json.loads(bytes) preserves raw surrogate codepoints', flush=True)
        elif case.startswith('invalid-utf8-'):
            bad = {
                'overlong': b'\xc0\xaf', 'overlong3': b'\xe0\x80\x80',
                'overlong4': b'\xf0\x80\x80\x80', 'truncated': b'\xed\xa0',
                'continuation': b'\xedA\x80', 'out-of-range': b'\xf4\x90\x80\x80',
            }[case.removeprefix('invalid-utf8-')]
            saved = saved.replace(b'INVALID_UTF8', bad)
            try:
                json.loads(saved)
            except UnicodeDecodeError:
                print(f'{case}: Python json.loads(bytes) rejects malformed encoding', flush=True)
            else:
                raise AssertionError('Python unexpectedly accepted malformed UTF-8')
        elif case == 'top-high-utf16':
            saved = saved.decode('ascii').encode('utf-16')
        elif case == 'top-high-utf32':
            saved = saved.decode('ascii').encode('utf-32')
        journal.write_bytes(saved)
        publication_failure = case == 'tree-high-restore'
        accepted = not publication_failure and not case.startswith(('tamper-', 'invalid-utf8-'))
        stage = Path(row['path']).with_name(row['quarantine'] + '-rollback-tree')
        before = snapshot(fixture.base)
        try:
            transactions.recover(plan.state_file, roots)
        except ValueError as error:
            assert not accepted, str(error)
            if case.startswith('tamper-'):
                assert 'unauthenticated' in str(error), str(error)
            if publication_failure:
                assert isinstance(error, UnicodeEncodeError), repr(error)
                assert journal.read_bytes() == saved
                assert (stage / 'safe/prefix').read_bytes() == b'prefix'
                assert stat.S_IMODE(stage.stat().st_mode) == 0o700
                print(f'{case}: Python {type(error).__name__}; partial stage retained', flush=True)
                retained = snapshot(fixture.base)
                try:
                    transactions.recover(plan.state_file, roots)
                except ValueError as retry:
                    assert 'modified staged path' in str(retry), str(retry)
                else:
                    raise AssertionError('partial stage must block retry')
                assert snapshot(fixture.base) == retained
            else:
                assert snapshot(fixture.base) == before
        else:
            assert accepted
            assert not journal.exists()
            assert not plan.state_file.exists()
            if case.startswith('link-low-'):
                assert os.readlink(os.fsencode(row['path'])) == b'not-present-\xff'
            elif case in ('tree-low-restore', 'basename-low-restore'):
                name = 'raw-\udcff' if case == 'tree-low-restore' else 'content'
                assert (Path(row['path']) / name).read_bytes() == b'x'
            else:
                assert not Path(row['path']).exists()
        expected = snapshot(fixture.base)
        if publication_failure:
            import shutil
            shutil.rmtree(stage)
        if case in ('tree-low-restore', 'basename-low-restore'):
            import shutil
            shutil.rmtree(row['path'])
        if case.startswith('link-low-'):
            os.unlink(row['path'])
            if replay_live is not None:
                os.symlink(replay_live, os.fsencode(row['path']))
        journal.write_bytes(saved)
        with tempfile.TemporaryDirectory(prefix='d5-wire-driver-') as temp:
            base = Path(temp)
            (base / 'request.json').write_text(json.dumps(request))
            command = ['cargo', 'test', '--quiet', '--lib', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
                       'transaction::apply_parity_tests::differential_apply', '--', '--ignored', '--exact']
            completed = subprocess.run(command, cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=temp, AW_APPLY_SCENARIO='recover-only'), capture_output=True, text=True)
            assert completed.returncode == 0, completed.stdout + completed.stderr
            result = json.loads((base / 'result.json').read_text())
            if publication_failure:
                retained = snapshot(fixture.base)
                retry = subprocess.run(command, cwd=REPO, env=dict(os.environ, AW_APPLY_BASE=temp, AW_APPLY_SCENARIO='recover-only'), capture_output=True, text=True)
                assert retry.returncode == 0, retry.stdout + retry.stderr
                retry_result = json.loads((base / 'result.json').read_text())
                assert not retry_result['accepted']
                assert 'modified staged path' in retry_result['diagnostic'], retry_result
                assert snapshot(fixture.base) == retained
        assert result['accepted'] == accepted, (case, result)
        assert snapshot(fixture.base) == expected, case
        if not accepted:
            if case.startswith('tamper-'):
                assert 'unauthenticated' in result['diagnostic'], result
            assert journal.read_bytes() == saved
        print(f'{case}: Python/Rust {"accepted" if accepted else "rejected"}; exact filesystem parity', flush=True)
    finally:
        fixture.doCleanups()


def publication_probe():
    supported = True
    with tempfile.TemporaryDirectory(prefix='d5-wire-filename-probe-') as temp:
        for kind in ('file', 'directory'):
            raw = os.fsencode(temp) + b'/raw-\xff-' + kind.encode('ascii')
            try:
                if kind == 'directory':
                    os.mkdir(raw)
                else:
                    with open(raw, 'xb') as stream:
                        stream.write(b'probe')
            except OSError as error:
                print(f'raw filename {kind} publication probe: {sys.platform} errno={error.errno} ({errno.errorcode.get(error.errno or 0)})', flush=True)
                assert sys.platform == 'darwin' and error.errno == errno.EILSEQ, error
                supported = False
            else:
                assert os.path.basename(raw) in os.listdir(os.fsencode(temp))
                print(f'raw filename {kind} publication probe: supported on {sys.platform}', flush=True)
    return supported


if __name__ == '__main__':
    supported = publication_probe()
    cases = sys.argv[1:] or (
        'control', 'top-high', 'top-high-utf16', 'top-high-utf32',
        'path-field-low', 'unused-tree-path-low', 'unknown-keys',
        'tamper-value', 'tamper-key', 'link-low-capture', 'link-low-restore',
        'basename-low-inert', 'tree-high-restore',
        *(f'raw-{encoding}' for encoding in (
            'utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be',
            'utf-32', 'utf-32-le', 'utf-32-be')),
        *(f'invalid-utf8-{kind}' for kind in (
            'overlong', 'overlong3', 'overlong4', 'truncated', 'continuation', 'out-of-range')),
        *(('tree-low-restore', 'basename-low-restore') if supported else ()),
    )
    assert len(cases) == len(set(cases)), 'duplicate differential rows'
    for case in cases:
        run_case(case)
    print(f'{len(cases)} real-journal wire differential rows passed; raw filename publication {"executed" if supported else "rejected by actual platform probes (not publication coverage)"}')
