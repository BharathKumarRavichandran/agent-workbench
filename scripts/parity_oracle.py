"""Executable parity-oracle contract; production Python is not changed."""
import json
import subprocess
import sys


def require_python312():
    """Fail before fixtures/builds when either oracle interpreter has drifted."""
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f'parity oracle requires Python 3.12, got {sys.version.split()[0]} ({sys.executable})')
    child = subprocess.run(
        ['python3', '-c', 'import json,sys; print(json.dumps(list(sys.version_info[:2])))'],
        capture_output=True, text=True, check=True,
    )
    if json.loads(child.stdout) != [3, 12]:
        raise SystemExit(f'parity subprocess python3 requires Python 3.12, got {child.stdout.strip()}; pin PATH as well as the top-level interpreter')


if __name__ == '__main__':
    require_python312()
    print(f'parity oracle: Python {sys.version.split()[0]}; PATH python3 is Python 3.12')
