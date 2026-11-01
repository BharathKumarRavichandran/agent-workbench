#!/usr/bin/env python3
"""Black-box Python/Rust parity matrix for the Hermes skills lifecycle."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
RUST = REPOSITORY / "rust" / "target" / "debug" / "agent-workbench-rs"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPOSITORY, text=True, capture_output=True, check=False)


def fixture(base: Path, name: str = "checkout") -> Path:
    root = base / name
    source = root / "plugins/pstack/skills/example"
    source.mkdir(parents=True)
    (root / "catalog").mkdir()
    (root / "profiles").mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\n", encoding="utf-8"
    )
    (root / "catalog/plugins.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "marketplace": {
                    "id": "market",
                    "name": "Market",
                    "owner": {"name": "Owner"},
                    "description": "Description",
                },
                "sources": [{"id": "local", "kind": "local", "path": "plugins"}],
                "plugins": [
                    {
                        "id": "pstack",
                        "source": "local",
                        "artifacts": {"agent-skills": {"path": "pstack/skills"}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "profiles/pstack.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "id": "pstack",
                "plugins": {"include": ["pstack"], "exclude": []},
                "targets": [{"id": "hermes"}],
            }
        ),
        encoding="utf-8",
    )
    return root


class Side:
    def __init__(self, implementation: str, base: Path) -> None:
        self.implementation = implementation
        self.base = base.resolve()
        self.root = fixture(self.base)
        self.destination = self.base / "destination"
        self.state = self.base / "state"

    @property
    def source(self) -> Path:
        return self.root / "plugins/pstack/skills/example"

    @property
    def state_file(self) -> Path:
        return self.state / "agent-workbench/market/pstack/hermes.json"

    def command(self, action: str, *extra: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
        common = [
            "--root",
            str(root or self.root),
            "--profile",
            "pstack",
            action,
            "--target",
            "hermes",
            "--destination",
            str(self.destination),
            "--state-dir",
            str(self.state),
            "--json",
            *extra,
        ]
        command = (
            [sys.executable, "-m", "agent_workbench", *common]
            if self.implementation == "python"
            else [str(RUST), *common]
        )
        return run(command)

    def normalize(self, text: str) -> str:
        return text.replace(str(self.base), "<BASE>")


def compare(label: str, python: Side, rust: Side, py: subprocess.CompletedProcess[str], rs: subprocess.CompletedProcess[str]) -> None:
    expected = (py.returncode, python.normalize(py.stdout), python.normalize(py.stderr))
    actual = (rs.returncode, rust.normalize(rs.stdout), rust.normalize(rs.stderr))
    if expected != actual:
        raise AssertionError(f"{label} mismatch\nPython: {expected!r}\nRust:   {actual!r}")


def compare_state(label: str, python: Side, rust: Side) -> None:
    py = python.normalize(python.state_file.read_text(encoding="utf-8"))
    rs = rust.normalize(rust.state_file.read_text(encoding="utf-8"))
    if py != rs:
        raise AssertionError(f"{label} state mismatch\nPython:\n{py}\nRust:\n{rs}")
    py_mode = stat.S_IMODE(python.state_file.stat().st_mode)
    rs_mode = stat.S_IMODE(rust.state_file.stat().st_mode)
    if py_mode != rs_mode:
        raise AssertionError(
            f"{label} state mode mismatch: Python={py_mode:o}, Rust={rs_mode:o}"
        )


def pair(base: Path, name: str) -> tuple[Side, Side]:
    return Side("python", base / name / "python"), Side("rust", base / name / "rust")


def install_status_uninstall(base: Path) -> None:
    absent_python, absent_rust = pair(base, "absent-uninstall")
    compare(
        "absent-state uninstall",
        absent_python,
        absent_rust,
        absent_python.command("uninstall"),
        absent_rust.command("uninstall"),
    )
    python, rust = pair(base, "clean")
    compare("clean install", python, rust, python.command("install"), rust.command("install"))
    compare_state("clean install", python, rust)
    for side in (python, rust):
        target = os.readlink(side.destination / "example")
        assert not Path(target).is_absolute(), target
    compare("clean status", python, rust, python.command("status"), rust.command("status"))
    for side in (python, rust):
        saved = side.state_file.with_name("saved.json")
        side.state_file.rename(saved)
        side.state_file.symlink_to(saved)
    compare(
        "symlinked state status",
        python,
        rust,
        python.command("status"),
        rust.command("status"),
    )
    compare("clean uninstall", python, rust, python.command("uninstall"), rust.command("uninstall"))
    assert not python.state_file.exists() and not rust.state_file.exists()


def adoption_and_conflicts(base: Path) -> None:
    python, rust = pair(base, "adopt")
    for side in (python, rust):
        side.destination.mkdir(parents=True)
        (side.destination / "example").symlink_to(side.source, target_is_directory=True)
    compare("adoption", python, rust, python.command("install", "--adopt"), rust.command("install", "--adopt"))
    compare_state("adoption", python, rust)

    python, rust = pair(base, "foreign")
    for side in (python, rust):
        side.destination.mkdir(parents=True)
        (side.destination / "example").write_text("foreign", encoding="utf-8")
    compare("foreign conflict", python, rust, python.command("status"), rust.command("status"))

    python, rust = pair(base, "modified")
    compare("modified setup", python, rust, python.command("install"), rust.command("install"))
    for side in (python, rust):
        (side.destination / "example").unlink()
        (side.destination / "example").symlink_to("foreign", target_is_directory=True)
    compare("modified conflict", python, rust, python.command("status"), rust.command("status"))
    compare("modified uninstall", python, rust, python.command("uninstall"), rust.command("uninstall"))


def relocation(base: Path) -> None:
    python, rust = pair(base, "relocation")
    compare("relocation setup", python, rust, python.command("install"), rust.command("install"))
    second_roots = []
    for side in (python, rust):
        second = side.base / "relocated"
        shutil.copytree(side.root, second, symlinks=True)
        second_roots.append(second)
    compare(
        "checkout relocation",
        python,
        rust,
        python.command("install", root=second_roots[0]),
        rust.command("install", root=second_roots[1]),
    )
    compare_state("checkout relocation", python, rust)


def v2_state(side: Side) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "repository": str(side.root),
        "marketplace": "market",
        "profile": "pstack",
        "target": "hermes",
        "destinationRoot": str(side.destination),
        "entries": {
            "skill:example": {
                "key": "skill:example",
                "plugin": "pstack",
                "source": "plugins/pstack/skills/example",
                "destination": str(side.destination / "example"),
            }
        },
    }


def seed_v2(side: Side) -> None:
    side.destination.mkdir(parents=True)
    (side.destination / "example").symlink_to(side.source, target_is_directory=True)
    side.state_file.parent.mkdir(parents=True)
    side.state_file.write_text(json.dumps(v2_state(side), indent=2) + "\n", encoding="utf-8")


def v2_compatibility(base: Path) -> None:
    python, rust = pair(base, "v2-migrate")
    seed_v2(python)
    seed_v2(rust)
    compare("v2 migration", python, rust, python.command("install"), rust.command("install"))
    compare_state("v2 migration", python, rust)

    python, rust = pair(base, "v2-uninstall")
    seed_v2(python)
    seed_v2(rust)
    compare("v2 uninstall", python, rust, python.command("uninstall"), rust.command("uninstall"))


def state_rejection(base: Path) -> None:
    cases = {
        "v4": {"schemaVersion": 4},
        "null": {"schemaVersion": None},
        "duplicate": '{"schemaVersion":3,"schemaVersion":3}',
    }
    for name, value in cases.items():
        python, rust = pair(base, f"reject-{name}")
        for side in (python, rust):
            side.state_file.parent.mkdir(parents=True)
            text = value if isinstance(value, str) else json.dumps(value)
            side.state_file.write_text(text, encoding="utf-8")
        py, rs = python.command("status"), rust.command("status")
        if py.returncode != 2 or rs.returncode != 2:
            raise AssertionError(f"state rejection {name}: Python={py.returncode}, Rust={rs.returncode}")

    python, rust = pair(base, "broken-state-link")
    for side in (python, rust):
        side.state_file.parent.mkdir(parents=True)
        side.state_file.symlink_to(side.state_file.with_name("missing.json"))
    compare(
        "broken state symlink uninstall",
        python,
        rust,
        python.command("uninstall"),
        rust.command("uninstall"),
    )
    if not python.state_file.is_symlink() or not rust.state_file.is_symlink():
        raise AssertionError("broken state symlink cleanup differs")


def main() -> int:
    build = run(["cargo", "build", "--quiet", "--manifest-path", "rust/Cargo.toml"])
    if build.returncode:
        sys.stderr.write(build.stderr)
        return build.returncode
    try:
        with tempfile.TemporaryDirectory(prefix="agent-workbench-lifecycle-") as raw:
            base = Path(raw).resolve()
            install_status_uninstall(base)
            adoption_and_conflicts(base)
            relocation(base)
            v2_compatibility(base)
            state_rejection(base)
    except AssertionError as error:
        print(error, file=sys.stderr)
        return 1
    print("lifecycle parity: clean/adopt/conflict/relocation/v2/rejection scenarios match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
