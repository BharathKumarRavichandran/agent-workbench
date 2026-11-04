#!/usr/bin/env python3
"""Real Claude-native CLI lifecycle at identical isolated fixture paths."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from check_cursor_activation_parity import REPO, lifecycle


def main():
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    subprocess.run(['cargo', 'build', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml')], cwd=REPO, check=True)
    rows = 0
    with tempfile.TemporaryDirectory(prefix='claude-activation-cli-', dir=os.environ['TMPDIR']) as raw:
        for case in sys.argv[1:] or ['mixed', 'hooks-only', 'mcp-only', 'user', 'shell-hook']:
            rows += lifecycle(Path(raw), case, target='claude')
            print(f'Claude native public lifecycle passed: {case}', flush=True)
    print(f'Claude native public activation: {rows} paired real CLI rows passed; exact output/state/documents/modes and read-only trust')


if __name__ == '__main__':
    main()
