"""Executable parity-oracle contract; production Python is not changed."""
import json
import os
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


def oracle_pythonpath(*extra: str) -> str:
    """Keep the materialized pinned producer first; append extra roots without replacing it."""
    parts = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    for item in extra:
        if item and item not in parts:
            parts.append(item)
    return os.pathsep.join(parts)


if __name__ == '__main__':
    require_python312()
    print(f'parity oracle: Python {sys.version.split()[0]}; PATH python3 is Python 3.12')
