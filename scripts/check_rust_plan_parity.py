#!/usr/bin/env python3
"""Black-box clean-plan parity check for the first Rust slice."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPOSITORY, text=True, capture_output=True, check=False)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-workbench-parity-") as raw_temp:
        temp = Path(raw_temp).resolve()
        destination = temp / "destination"
        state = temp / "state"
        common = [
            "--root",
            ".",
            "--profile",
            "pstack",
            "plan",
            "--target",
            "hermes",
            "--destination",
            str(destination),
            "--state-dir",
            str(state),
            "--json",
        ]
        python = run([sys.executable, "-m", "agent_workbench", *common])
        rust = run(
            [
                "cargo",
                "run",
                "--quiet",
                "--manifest-path",
                "rust/Cargo.toml",
                "--",
                *common,
            ]
        )

        for name, result in (("Python", python), ("Rust", rust)):
            if result.returncode != 0:
                print(f"{name} plan exited {result.returncode}:\n{result.stderr}", file=sys.stderr)
                return 1
            if result.stderr:
                print(f"{name} plan wrote stderr:\n{result.stderr}", file=sys.stderr)
                return 1

        marker = "<TEMP>"
        python_output = python.stdout.replace(str(temp), marker)
        rust_output = rust.stdout.replace(str(temp), marker)
        if python_output != rust_output:
            import difflib

            sys.stderr.writelines(
                difflib.unified_diff(
                    python_output.splitlines(keepends=True),
                    rust_output.splitlines(keepends=True),
                    fromfile="python-plan.json",
                    tofile="rust-plan.json",
                )
            )
            return 1

        operations = json.loads(rust.stdout)
        fields = [
            "kind",
            "key",
            "destination",
            "source",
            "reason",
            "component",
            "resourceKind",
            "digest",
            "diagnostic",
            "trustToken",
            "trustRequired",
        ]
        if not operations or any(list(operation) != fields for operation in operations):
            print("Rust output does not preserve the ordered 11-field contract", file=sys.stderr)
            return 1
        print(f"plan parity: {len(operations)} operations match")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
