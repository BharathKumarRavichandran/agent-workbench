#!/usr/bin/env python3
"""Paired private E3 publication failures, with exact diagnostics and residue."""
import errno
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench import rendering
from check_validate_parity import ROOT, snapshot
from parity_oracle import require_python312


def normalize(error):
    # Only the renderer's freshly random 8-byte hex nonce is nondeterministic.
    pattern = r'\.marketplace\.json\.([0-9a-f]{16})(?=[\'\"])'
    matches = re.findall(pattern, error)
    if '.marketplace.json.' in error:
        assert len(matches) == 1, error
    return re.sub(pattern, '.marketplace.json.<nonce>', error)


def main():
    require_python312()
    assert os.environ.get('TMPDIR')
    phases = ('mkdir', 'root-open', 'parent-open', 'temp-open', 'write', 'fsync', 'rename')
    with tempfile.TemporaryDirectory(prefix='render-failures-', dir=os.environ['TMPDIR']) as raw:
        base = Path(raw)
        root = base / 'repository'
        for phase in phases:
            rows = []
            for engine in ('python', 'rust'):
                root.mkdir()
                if phase != 'mkdir':
                    (root / '.cursor-plugin').mkdir()
                if phase == 'rename':
                    (root / '.cursor-plugin/marketplace.json').write_bytes(b'previous')
                if engine == 'python':
                    hit = False

                    def fail(*args, **kwargs):
                        nonlocal hit
                        hit = True
                        if phase in ('write', 'fsync'):
                            raise OSError(errno.EIO, os.strerror(errno.EIO))
                        if phase == 'rename':
                            raise OSError(errno.EIO, os.strerror(errno.EIO), args[0], None, args[1])
                        raise OSError(errno.EIO, os.strerror(errno.EIO), os.fspath(args[0]))

                    original_open = os.open

                    def open_fault(path, *args, **kwargs):
                        selected = ((phase == 'root-open' and Path(path) == root)
                                    or (phase == 'parent-open' and path == '.cursor-plugin')
                                    or (phase == 'temp-open' and str(path).startswith('.marketplace.json.')))
                        return fail(path, *args, **kwargs) if selected else original_open(path, *args, **kwargs)

                    # BufferedWriter.write is read-only; a tiny delegating handle faults
                    # the actual writer boundary and retains context-manager cleanup.
                    original_fdopen = os.fdopen

                    class Handle:
                        def __init__(self, fd, mode):
                            self.handle = original_fdopen(fd, mode)
                        def __enter__(self):
                            self.handle.__enter__()
                            return self
                        def __exit__(self, *args):
                            return self.handle.__exit__(*args)
                        def write(self, content):
                            return fail() if phase == 'write' else self.handle.write(content)
                        def flush(self):
                            return self.handle.flush()
                        def fileno(self):
                            return self.handle.fileno()

                    with patch.object(rendering.os, 'open', open_fault), \
                         patch.object(rendering.os, 'mkdir', fail if phase == 'mkdir' else os.mkdir), \
                         patch.object(rendering.os, 'fdopen', Handle), \
                         patch.object(rendering.os, 'fsync', fail if phase == 'fsync' else os.fsync), \
                         patch.object(rendering.os, 'replace', fail if phase == 'rename' else os.replace):
                        try:
                            rendering.write_generated_file(root, root / '.cursor-plugin/marketplace.json', b'generated\n')
                        except OSError as exc:
                            error = str(exc)
                        else:
                            raise AssertionError('oracle fault not reached')
                else:
                    subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(ROOT / 'rust/Cargo.toml'),
                                    '--lib', 'rendering::parity_tests::differential_failure', '--', '--ignored', '--exact'],
                                   cwd=ROOT, env=dict(os.environ, AW_RENDER_BASE=str(base), AW_RENDER_FAULT=phase),
                                   check=True, capture_output=True)
                    result = json.loads((base / 'result.json').read_text())
                    hit, error = result['hit'], result['error']
                assert hit and error is not None, (engine, phase, hit, error)
                typed = [(row[0], row[1], row[-1]) for row in snapshot(root)]
                rows.append((normalize(error), typed))
                shutil.rmtree(root)
            assert rows[0] == rows[1], (phase, rows)
            print(f'render failure parity: {phase} passed')
        print(f'render failure parity: {len(phases)} paired private rows passed')


if __name__ == '__main__':
    main()
