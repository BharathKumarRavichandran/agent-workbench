#!/usr/bin/env python3
"""Install the release `agent-workbench` executable into PREFIX/bin.

Python remains the frozen oracle until F8. This installs the default Rust CLI and does not publish Git tags or GitHub releases; F1 still pins in-tree Python as the ownership producer.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "rust" / "target" / "release" / "agent-workbench-rs"
EXECUTABLE = "agent-workbench"


def package(prefix: Path) -> Path:
    prefix = prefix.expanduser()
    if prefix.exists() and not prefix.is_dir():
        raise SystemExit(f"prefix is not a directory: {prefix}")
    build = subprocess.run(
        ["cargo", "build", "--release", "--quiet", "--manifest-path", str(REPO / "rust" / "Cargo.toml")],
        cwd=REPO,
    )
    if build.returncode:
        raise SystemExit(build.returncode)
    if not SOURCE.is_file():
        raise SystemExit(f"release binary missing: {SOURCE}")
    destination_dir = prefix / "bin"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / EXECUTABLE
    shutil.copy2(SOURCE, destination)
    destination.chmod(0o755)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the release agent-workbench executable")
    parser.add_argument("--prefix", required=True, help="install prefix; binary lands in PREFIX/bin/agent-workbench")
    args = parser.parse_args()
    destination = package(Path(args.prefix))
    print(f"installed {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
