#!/usr/bin/env python3
"""Advance tracked upstream submodules and refresh generated files."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    run("git", "submodule", "update", "--init", "--recursive", "--remote")
    run("python3", "scripts/render_marketplaces.py")
    run("python3", "scripts/validate.py")
    subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        cwd=ROOT,
        check=True,
    )
    print("Review and commit changed submodule pointers and generated manifests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
