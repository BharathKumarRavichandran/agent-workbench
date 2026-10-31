#!/usr/bin/env python3
"""Compatibility wrapper for workbench validation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_workbench.cli import main as workbench_main


def main() -> int:
    return workbench_main(["--root", str(ROOT), "validate", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
