#!/usr/bin/env python3
"""Owned D6 public CLI differential tracer; real plans and exact filesystem bytes."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from test_workbench import Fixture
from test_components import configure
from agent_workbench import cli

BIN = REPO / 'rust/target/debug/agent-workbench-rs'

def snapshot(base):
    result = []
    for path in sorted(base.rglob('*')):
        if path.is_symlink():
            result.append((str(path.relative_to(base)), 'link', path.lstat().st_mode & 0o7777, os.readlink(path)))
        elif path.is_dir():
            result.append((str(path.relative_to(base)), 'dir', path.stat().st_mode & 0o7777, None))
        else:
            result.append((str(path.relative_to(base)), 'file', path.stat().st_mode & 0o7777, path.read_bytes()))
    return result

def run():
    with tempfile.TemporaryDirectory(prefix='aw-d6-', dir=os.environ['TMPDIR']) as temporary:
        base = Path(temporary).resolve()
        results = []
        for engine in ('python', 'rust'):
            home = base / 'home'; home.mkdir()
            os.environ['HOME'] = str(home)
            f = Fixture(base / 'repo'); configure(f)
            # Existing Rust supports pstack only; the first slice isolates executable activation.
            profile = json.loads((f.root/'profiles/default.json').read_text()); profile['id'] = 'pstack'
            (f.root/'profiles/pstack.json').write_text(json.dumps(profile))
            state = base/'state'
            key = state/'agent-workbench/fixture/pstack/.cursor.json.transaction-key'
            # Discover the fixture marketplace identity rather than assume it.
            market = json.loads((f.root/'catalog/plugins.json').read_text())['marketplace']['id']
            key = state/f'agent-workbench/{market}/pstack/.cursor.json.transaction-key'
            key.parent.mkdir(parents=True); key.write_bytes(bytes(range(32))); key.chmod(0o600)
            common = ['--root', str(f.root), '--profile', 'pstack']
            options = ['--target', 'cursor', '--project-root', str(base/'project'), '--state-dir', str(state), '--json']
            selections = ['--component', 'hooks', '--component', 'mcp']
            def invoke(action, grants=()):
                args = common + [action] + options + ([] if action == 'uninstall' else selections) + [v for g in grants for v in ('--trust',g)]
                if engine == 'rust':
                    p = subprocess.run([str(BIN), *args], capture_output=True, text=True)
                    return p.returncode, p.stdout, p.stderr
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(args)
                return code, out.getvalue(), err.getvalue()
            steps = []
            first = invoke('plan'); steps.append(first)
            assert first[0] == 1 and first[1], (engine, 'untrusted plan', first)
            grants = sorted({o['trustToken'] for o in json.loads(first[1]) if o.get('trustToken')})
            for action, explicit in [('trust', ()), ('install', grants), ('plan', ()), ('status', ()), ('install', ()), ('uninstall', ())]:
                got = invoke(action, explicit)
                expected = 1 if action == 'trust' else 0
                assert got[0] == expected, (engine, action, got)
                steps.append(got)
                steps.append(snapshot(base/'project') + snapshot(state))
            results.append(steps)
            for child in list(base.iterdir()):
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        assert results[0] == results[1], next((f'step {i}:\nPY={a!r}\nRS={b!r}' for i,(a,b) in enumerate(zip(*results)) if a != b), 'length mismatch')
    print('D6 activation tracer: exact public output and typed filesystem lifecycle passed')

if __name__ == '__main__': run()
