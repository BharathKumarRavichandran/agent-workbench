#!/usr/bin/env python3
"""F4: one release `agent-workbench` executable with install/upgrade/uninstall paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from check_python_baseline_upgrade import (
    REPO,
    env_for,
    fixture,
    python_install_skills,
    require_pinned_baseline,
    skills_common,
    snapshot,
    state_file,
)

PACKAGER = REPO / "scripts" / "package_release.py"
EXECUTABLE = "agent-workbench"


def installed_binary(prefix: Path) -> Path:
    return prefix / "bin" / EXECUTABLE


def package(prefix: Path) -> Path:
    result = subprocess.run(
        [sys.executable, str(PACKAGER), "--prefix", str(prefix)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    binary = installed_binary(prefix)
    assert binary.is_file(), binary
    mode = stat.S_IMODE(binary.stat().st_mode)
    assert mode == 0o755, mode
    assert not (prefix / "bin" / "agent-workbench-rs").exists()
    names = sorted(path.name for path in (prefix / "bin").iterdir())
    assert names == [EXECUTABLE], names
    header = binary.read_bytes()[:4]
    assert header in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"), header
    return binary


def invoke(binary: Path, root: Path, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = env_for(root)
    env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [EXECUTABLE, *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        umask=0o022,
    )


def python_cli(root: Path, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = env_for(root)
    return subprocess.run(
        [sys.executable, "-m", "agent_workbench", *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        umask=0o022,
    )


def help_uses_installed_name(binary: Path, root: Path) -> None:
    result = invoke(binary, root, ["--help"])
    assert result.returncode == 0, result.stderr
    text = result.stdout.decode()
    assert "usage: agent-workbench" in text, text
    assert "agent-workbench-rs" not in text
    assert "agent-workbench-rs" not in result.stderr.decode()


def upgrade_status(binary: Path, base: Path) -> int:
    root = base / "upgrade-status"
    root.mkdir()
    repository = python_install_skills(root, "hermes")
    argv = skills_common(root, repository, "hermes", "status")
    before = snapshot(root)
    py = python_cli(root, argv)
    assert snapshot(root) == before
    rs = invoke(binary, root, argv)
    assert snapshot(root) == before
    assert (py.returncode, py.stdout, py.stderr) == (rs.returncode, rs.stdout, rs.stderr)
    assert py.returncode == 0
    return 1


def upgrade_reinstall(binary: Path, base: Path) -> int:
    root = base / "upgrade-reinstall"
    root.mkdir()
    repository = python_install_skills(root, "hermes")
    argv = skills_common(root, repository, "hermes", "install")
    before = snapshot(root)
    py = python_cli(root, argv)
    assert snapshot(root) == before
    rs = invoke(binary, root, argv)
    assert snapshot(root) == before
    assert (py.returncode, py.stdout, py.stderr) == (rs.returncode, rs.stdout, rs.stderr)
    assert py.returncode == 0
    kinds = {item["kind"] for item in json.loads(py.stdout)}
    assert kinds <= {"keep"}, kinds
    return 1


def uninstall_handoff(binary: Path, base: Path) -> int:
    rows = []
    for name, consumer in (("python", None), ("release", binary)):
        root = base / "uninstall" / name
        root.mkdir(parents=True)
        repository = python_install_skills(root, "hermes")
        argv = skills_common(root, repository, "hermes", "uninstall")
        result = python_cli(root, argv) if consumer is None else invoke(consumer, root, argv)
        rows.append(
            (
                result.returncode,
                result.stdout.replace(str(root).encode(), b"<BASE>"),
                result.stderr.replace(str(root).encode(), b"<BASE>"),
                snapshot(root, root),
            )
        )
        assert result.returncode == 0, result.stderr
    assert rows[0] == rows[1], (rows[0][:3], rows[1][:3])
    return 1


def clean_install(binary: Path, base: Path) -> int:
    expected = None
    for name, consumer in (("python", None), ("release", binary)):
        root = base / "clean" / name
        root.mkdir(parents=True)
        repository = fixture(root)
        argv = skills_common(root, repository, "hermes", "install")
        result = python_cli(root, argv) if consumer is None else invoke(consumer, root, argv)
        row = (
            result.returncode,
            result.stdout.replace(str(root).encode(), b"<BASE>"),
            result.stderr.replace(str(root).encode(), b"<BASE>"),
            snapshot(root, root),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(state_file(root, "hermes").read_text())["schemaVersion"] == 3
        if expected is None:
            expected = row
        else:
            assert row == expected, (expected[:3], row[:3])
    return 1


def main() -> int:
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    with tempfile.TemporaryDirectory(prefix="section-f4-", dir=os.environ["TMPDIR"]) as raw:
        base = Path(raw)
        prefix = base / "prefix"
        binary = package(prefix)
        (base / "help").mkdir()
        help_uses_installed_name(binary, base / "help")
        rows = 0
        rows += upgrade_status(binary, base)
        rows += upgrade_reinstall(binary, base)
        rows += uninstall_handoff(binary, base)
        rows += clean_install(binary, base)
    print(
        f"F4 release executable: {rows} install/upgrade/uninstall rows; "
        f"single {EXECUTABLE} at $PREFIX/bin; no GitHub release"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
