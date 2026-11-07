#!/usr/bin/env python3
"""F7: make the Rust agent-workbench executable the documented default CLI."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from check_intentional_differences import CATALOG, MIGRATION
from check_python_baseline_upgrade import (
    REPO,
    env_for,
    python_install_skills,
    require_pinned_baseline,
    reverse_and_downgrade,
    skills_common,
    snapshot,
)
from check_release_executable import (
    EXECUTABLE,
    installed_binary,
    invoke as invoke_release,
    package,
    python_cli,
)
from parity_oracle import require_python312

WRAPPER = REPO / "scripts" / "agent-workbench"
CONTRACT = REPO / "rust/SECTION_F_CONTRACT.md"
README = REPO / "README.md"
UMBRELLA = REPO / "scripts/check_rust_plan_parity.py"
TODO = REPO / "TODO.md"
MAKE_DEFAULT_TARGETS = ("render", "validate", "update", "plan-hermes")
ELF_OR_MACHO = (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")
F6_IDS = tuple(row["id"] for row in (*CATALOG, *MIGRATION))


def file_kind(path: Path) -> str:
    header = path.read_bytes()[:4]
    if header in ELF_OR_MACHO:
        return "rust-binary"
    text = path.read_text(encoding="utf-8")
    if "-m" in text and "agent_workbench" in text:
        return "python-wrapper"
    if text.startswith("#!/bin/sh") and "exec" in text:
        return "rust-wrapper"
    raise AssertionError(f"unclassified invocation file {path}: {header!r}")


def invoke_wrapper(root: Path, argv: list[str], *, path_prefix: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    env = env_for(root)
    if path_prefix is not None:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [str(WRAPPER), *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        umask=0o022,
    )


def documentation_records_cutover() -> None:
    readme = README.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")
    parts = contract.split("## F7 — default cutover", 1)
    assert len(parts) == 2, "SECTION_F_CONTRACT missing F7 default cutover"
    body = parts[1]
    assert "Acceptance-to-test map" in body, body[:500]
    assert "python3 -m agent_workbench still runs" in body
    assert "F8" in body
    commands = readme.split("## Commands", 1)[1].split("## ", 1)[0]
    assert "agent-workbench validate" in commands
    assert "python3 -m agent_workbench" not in commands
    assert "The documented default CLI is the Rust `agent-workbench` executable" in readme or (
        "Rust is the default CLI" in readme
    )
    assert "python3 -m agent_workbench" in readme
    for exception_id in F6_IDS:
        assert exception_id in readme, exception_id
        assert exception_id in body or exception_id in contract
    for evidence in (
        "documented_default_is_rust",
        "leftover_documented_default_is_not_python",
        "makefile_dispatches_to_default",
        "wrapper_dispatches_to_rust",
        "oracle_module_still_runs",
        "migration_through_default",
        "f6_catalog_required",
    ):
        assert evidence in body, evidence


def leftover_documented_default_is_not_python() -> None:
    readme = README.read_text(encoding="utf-8")
    commands = readme.split("## Commands", 1)[1].split("## ", 1)[0]
    assert "python3 -m agent_workbench" not in commands, commands
    assert "-m agent_workbench" not in WRAPPER.read_text(encoding="utf-8")
    for target in MAKE_DEFAULT_TARGETS:
        result = subprocess.run(
            ["make", "-n", target],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "-m agent_workbench" not in result.stdout, (target, result.stdout)
        assert "agent-workbench" in result.stdout, (target, result.stdout)


def f6_catalog_required() -> None:
    umbrella = UMBRELLA.read_text(encoding="utf-8")
    assert "check_intentional_differences.py" in umbrella
    assert "check_default_cutover.py" in umbrella
    assert [row["id"] for row in CATALOG] == [
        "E1",
        "E2",
        "VAL-GIT-1",
        "RENDER-GIT-1",
        "UPDATE-GIT-1",
        "UPDATE-CONFIG-1",
    ]
    assert [row["id"] for row in MIGRATION] == [
        "PYTHON-TO-RUST-UPGRADE",
        "REVERSE-STATUS-NOT-SUPPORTED",
        "REJECT-V4-AS-V3",
    ]
    for path in (
        REPO / "scripts/check_validate_parity.py",
        REPO / "scripts/check_render_parity.py",
        REPO / "scripts/check_update_parity.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert "normalize_traceback" not in text
        assert "generic normalizer" not in text.lower()
    todo = TODO.read_text(encoding="utf-8")
    assert "- [ ] Remove Python runtime code" in todo
    assert (REPO / "agent_workbench").is_dir()
    assert (REPO / "tests").is_dir()


def documented_default_is_rust(prefix: Path, root: Path) -> None:
    binary = installed_binary(prefix)
    assert file_kind(binary) == "rust-binary"
    result = invoke_release(binary, root, ["--help"])
    assert result.returncode == 0, result.stderr
    text = result.stdout.decode()
    assert "usage: agent-workbench" in text, text
    assert "agent-workbench-rs" not in text
    assert result.args[0] == EXECUTABLE


def makefile_dispatches_to_default() -> None:
    dry = subprocess.run(
        ["make", "-n", "install-dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "scripts/install_skills.py" in dry.stdout, dry.stdout
    release = subprocess.run(
        ["make", "-n", "rust-release"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "package_release.py" in release.stdout, release.stdout
    leftover_documented_default_is_not_python()


def wrapper_dispatches_to_rust(prefix: Path, root: Path) -> None:
    assert WRAPPER.is_file(), WRAPPER
    mode = stat.S_IMODE(WRAPPER.stat().st_mode)
    assert mode & 0o111, mode
    assert file_kind(WRAPPER) == "rust-wrapper"
    text = WRAPPER.read_text(encoding="utf-8")
    assert "-m agent_workbench" not in text
    assert "python3 -m" not in text
    names = sorted(path.name for path in (prefix / "bin").iterdir())
    assert names == [EXECUTABLE], names
    assert file_kind(installed_binary(prefix)) == "rust-binary"
    wrapped = invoke_wrapper(root, ["--help"], path_prefix=prefix / "bin")
    binary = invoke_release(installed_binary(prefix), root, ["--help"])
    assert wrapped.returncode == binary.returncode == 0, wrapped.stderr
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
        binary.returncode,
        binary.stdout,
        binary.stderr,
    )


def oracle_module_still_runs(root: Path) -> None:
    result = python_cli(root, ["--help"])
    assert result.returncode == 0, result.stderr
    assert result.args[:3] == [sys.executable, "-m", "agent_workbench"]
    assert "usage: agent-workbench" in result.stdout.decode()
    for path in (
        REPO / "scripts/validate.py",
        REPO / "scripts/render_marketplaces.py",
        REPO / "scripts/update.py",
        REPO / "scripts/install_skills.py",
    ):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first == "#!/usr/bin/env python3", (path, first)
        shebang = subprocess.run(
            [sys.executable, str(path), "--help"],
            cwd=REPO,
            env=env_for(root),
            capture_output=True,
        )
        assert shebang.returncode == 0, (path, shebang.stderr)


def migration_through_default(prefix: Path, base: Path) -> int:
    rows = 0
    status_root = base / "upgrade-status"
    status_root.mkdir()
    repository = python_install_skills(status_root, "hermes")
    argv = skills_common(status_root, repository, "hermes", "status")
    before = snapshot(status_root)
    oracle = python_cli(status_root, argv)
    assert snapshot(status_root) == before
    defaulted = invoke_release(installed_binary(prefix), status_root, argv)
    assert snapshot(status_root) == before
    wrapped = invoke_wrapper(status_root, argv, path_prefix=prefix / "bin")
    assert snapshot(status_root) == before
    assert (oracle.returncode, oracle.stdout, oracle.stderr) == (
        defaulted.returncode,
        defaulted.stdout,
        defaulted.stderr,
    )
    assert (oracle.returncode, oracle.stdout, oracle.stderr) == (
        wrapped.returncode,
        wrapped.stdout,
        wrapped.stderr,
    )
    assert oracle.returncode == 0
    rows += 1

    reinstall_root = base / "upgrade-reinstall"
    reinstall_root.mkdir()
    repository = python_install_skills(reinstall_root, "hermes")
    argv = skills_common(reinstall_root, repository, "hermes", "install")
    before = snapshot(reinstall_root)
    oracle = python_cli(reinstall_root, argv)
    assert snapshot(reinstall_root) == before
    defaulted = invoke_release(installed_binary(prefix), reinstall_root, argv)
    assert snapshot(reinstall_root) == before
    assert (oracle.returncode, oracle.stdout, oracle.stderr) == (
        defaulted.returncode,
        defaulted.stdout,
        defaulted.stderr,
    )
    assert oracle.returncode == 0
    rows += 1

    expected = None
    for name, consumer in (("oracle", None), ("default", "release"), ("wrapper", "wrapper")):
        root = base / "uninstall" / name
        root.mkdir(parents=True)
        repository = python_install_skills(root, "hermes")
        argv = skills_common(root, repository, "hermes", "uninstall")
        if consumer is None:
            result = python_cli(root, argv)
        elif consumer == "release":
            result = invoke_release(installed_binary(prefix), root, argv)
        else:
            result = invoke_wrapper(root, argv, path_prefix=prefix / "bin")
        row = (
            result.returncode,
            result.stdout.replace(str(root).encode(), b"<BASE>"),
            result.stderr.replace(str(root).encode(), b"<BASE>"),
            snapshot(root, root),
        )
        assert result.returncode == 0, result.stderr
        if expected is None:
            expected = row
        else:
            assert row == expected, (name, expected[:3], row[:3])
        rows += 1
    rows += reverse_and_downgrade(base / "reverse")
    return rows


def main() -> int:
    require_python312()
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    documentation_records_cutover()
    leftover_documented_default_is_not_python()
    f6_catalog_required()
    makefile_dispatches_to_default()
    with tempfile.TemporaryDirectory(prefix="section-f7-", dir=os.environ["TMPDIR"]) as raw:
        base = Path(raw)
        help_root = base / "help"
        help_root.mkdir()
        oracle_module_still_runs(help_root)
        prefix = base / "prefix"
        package(prefix)
        documented_default_is_rust(prefix, help_root)
        wrapper_dispatches_to_rust(prefix, help_root)
        rows = migration_through_default(prefix, base)
    print(
        f"F7 default cutover: documented CLI is {EXECUTABLE}; "
        f"oracle python3 -m agent_workbench remains; "
        f"{len(CATALOG)} F6 exceptions unchanged; {rows} default-Rust migration rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
