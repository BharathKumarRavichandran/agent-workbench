#!/usr/bin/env python3
"""F5: preserve documented Python invocations and an explicit transition wrapper."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from check_python_baseline_upgrade import (
    REPO,
    env_for,
    python_install_skills,
    require_pinned_baseline,
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

WRAPPER = REPO / "scripts" / "agent-workbench"
SHEBANG_WRAPPERS = (
    REPO / "scripts" / "validate.py",
    REPO / "scripts" / "render_marketplaces.py",
    REPO / "scripts" / "update.py",
    REPO / "scripts" / "install_skills.py",
)
ELF_OR_MACHO = (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")


def file_kind(path: Path) -> str:
    header = path.read_bytes()[:4]
    if header in ELF_OR_MACHO:
        return "rust-binary"
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!/usr/bin/env python3") and "-m" in text and "agent_workbench" in text:
        return "python-wrapper"
    if text.startswith("#!/bin/sh") and "exec" in text and "-m agent_workbench" not in text:
        return "rust-wrapper"
    raise AssertionError(f"unclassified invocation file {path}: {header!r}")


def invoke_wrapper(root: Path, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = env_for(root)
    return subprocess.run(
        [str(WRAPPER), *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        umask=0o022,
    )


def documented_module_form(root: Path) -> None:
    result = python_cli(root, ["--help"])
    assert result.returncode == 0, result.stderr
    text = result.stdout.decode()
    assert "usage: agent-workbench" in text, text
    assert result.args[1] == "-m"
    assert result.args[2] == "agent_workbench"


def makefile_stays_on_python_module() -> None:
    dry = subprocess.run(
        ["make", "-n", "install-dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "scripts/install_skills.py" not in dry.stdout, dry.stdout
    assert "agent-workbench" in dry.stdout or "plan" in dry.stdout
    release = subprocess.run(
        ["make", "-n", "rust-release"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "package_release.py" in release.stdout, release.stdout
    assert str(WRAPPER) not in release.stdout
    assert "scripts/agent-workbench" not in release.stdout


def shebang_entry_points_still_run(root: Path) -> None:
    env = env_for(root)
    for path in SHEBANG_WRAPPERS:
        assert path.is_file(), path
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first == "#!/usr/bin/env python3", (path, first)
        result = subprocess.run(
            [sys.executable, str(path), "--help"],
            cwd=REPO,
            env=env,
            capture_output=True,
        )
        assert result.returncode == 0, (path, result.stdout, result.stderr)
        assert result.stdout, path


def explicit_wrapper_is_python(prefix: Path | None = None) -> None:
    assert WRAPPER.is_file(), WRAPPER
    mode = stat.S_IMODE(WRAPPER.stat().st_mode)
    assert mode & 0o111, mode
    assert file_kind(WRAPPER) == "rust-wrapper"
    text = WRAPPER.read_text(encoding="utf-8")
    assert "-m agent_workbench" not in text
    if prefix is not None:
        names = sorted(path.name for path in (prefix / "bin").iterdir())
        assert names == [EXECUTABLE], names
        assert file_kind(installed_binary(prefix)) == "rust-binary"


def three_forms_distinguishable(prefix: Path, root: Path) -> None:
    wrapped = invoke_wrapper(root, ["--help"])
    binary = invoke_release(installed_binary(prefix), root, ["--help"])
    assert wrapped.returncode == binary.returncode == 0
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
        binary.returncode,
        binary.stdout,
        binary.stderr,
    )
    kinds = {
        "rust-wrapper": file_kind(WRAPPER),
        "rust-binary": file_kind(installed_binary(prefix)),
    }
    assert kinds["rust-wrapper"] == "rust-wrapper"
    assert kinds["rust-binary"] == "rust-binary"
    assert len(set(kinds.values())) == 2


def default_engine_unchanged() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    commands = readme.split("## Commands", 1)[1].split("## ", 1)[0]
    assert "agent-workbench validate" in commands
    assert "agent-workbench --profile pstack plan --target hermes" in commands
    assert "python3 -m agent_workbench validate" not in commands
    assert "python3 -m agent_workbench" not in readme
    assert "scripts/agent-workbench" in readme


def upgrade_uninstall_through_preserved(base: Path) -> int:
    rows = 0
    status_root = base / "upgrade-status"
    status_root.mkdir()
    repository = python_install_skills(status_root, "hermes")
    argv = skills_common(status_root, repository, "hermes", "status")
    before = snapshot(status_root)
    module = python_cli(status_root, argv)
    assert snapshot(status_root) == before
    wrapped = invoke_wrapper(status_root, argv)
    assert snapshot(status_root) == before
    assert (module.returncode, module.stdout, module.stderr) == (
        wrapped.returncode,
        wrapped.stdout,
        wrapped.stderr,
    )
    assert module.returncode == 0
    rows += 1

    expected = None
    for name, consumer in (("module", python_cli), ("wrapper", invoke_wrapper)):
        root = base / "uninstall" / name
        root.mkdir(parents=True)
        repository = python_install_skills(root, "hermes")
        argv = skills_common(root, repository, "hermes", "uninstall")
        result = consumer(root, argv)
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
            assert row == expected, (expected[:3], row[:3])
        rows += 1
    return rows


def main() -> int:
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    with tempfile.TemporaryDirectory(prefix="section-f5-", dir=os.environ["TMPDIR"]) as raw:
        base = Path(raw)
        help_root = base / "help"
        help_root.mkdir()
        makefile_stays_on_python_module()
        explicit_wrapper_is_python()
        default_engine_unchanged()
        prefix = base / "prefix"
        package(prefix)
        explicit_wrapper_is_python(prefix)
        three_forms_distinguishable(prefix, help_root)
        rows = upgrade_uninstall_through_preserved(base)
    print(
        f"F5 invocation compatibility: Makefile and explicit {WRAPPER.relative_to(REPO)}; "
        f"{rows} upgrade/uninstall rows; default CLI is Rust"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
