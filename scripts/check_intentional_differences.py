#!/usr/bin/env python3
"""F6: record authorized Python/Rust differences and required migration."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from check_python_baseline_upgrade import (
    REPO,
    require_pinned_baseline,
    reverse_and_downgrade,
    same_tree_status,
)
from check_release_executable import python_cli
from check_validate_parity import git_revision_rows
from parity_oracle import require_python312

RUST = REPO / "rust/target/debug/agent-workbench-rs"
EXCEPTIONS = REPO / "rust/PARITY_EXCEPTIONS.md"
CONTRACT = REPO / "rust/SECTION_F_CONTRACT.md"
README = REPO / "README.md"
UMBRELLA = REPO / "scripts/check_rust_plan_parity.py"
TODO = REPO / "TODO.md"

# Authorized differences only. Do not invent new divergences here.
CATALOG = (
    {
        "id": "E1",
        "command": "install/uninstall apply (Cursor/Claude private driver)",
        "boundary": "ownership-state parent replaced immediately before or after final rename/unlink",
        "python_stdout": "empty or success operations",
        "python_stderr": "none on directory replacement; Git-unrelated",
        "python_exit": "0 on regular-directory replacement; nonzero on symlink nofollow sync",
        "python_state": "overwrites or removes foreign state; original detached state retained",
        "rust_stdout": "empty on failure",
        "rust_stderr": "typed parent-identity failure",
        "rust_exit": "nonzero",
        "rust_state": "foreign replacement bytes/mode/inode unchanged; authenticated journal retained",
        "evidence": "rust/src/transaction/state_parent_contract.py",
        "umbrella": "state_parent_contract.py",
    },
    {
        "id": "E2",
        "command": "install apply (Cursor/Claude private driver)",
        "boundary": "foreign directory recreated at consumed .agent-workbench-copy-* staging name",
        "python_stdout": "success operations when no injected fsync fault",
        "python_stderr": "none on false success",
        "python_exit": "0 without injected fault",
        "python_state": "recursively deletes the foreign staging replacement; journal removed on false success",
        "rust_stdout": "empty on conflict",
        "rust_stderr": "typed foreign-stage conflict",
        "rust_exit": "nonzero",
        "rust_state": "foreign inode/bytes/modes preserved; authenticated journal retained",
        "evidence": "rust/src/transaction/stage_cleanup_contract.py",
        "umbrella": "stage_cleanup_contract.py",
    },
    {
        "id": "VAL-GIT-1",
        "command": "validate",
        "boundary": "uncaught git rev-parse HEAD failure during generated-file lock rendering",
        "python_stdout": "empty",
        "python_stderr": "raw Git stderr plus CalledProcessError traceback",
        "python_exit": "1",
        "python_state": "unchanged",
        "rust_stdout": "empty",
        "rust_stderr": "raw Git stderr only",
        "rust_exit": "1",
        "rust_state": "unchanged",
        "evidence": "scripts/check_validate_parity.py::git_revision_rows",
        "umbrella": "check_validate_parity.py",
    },
    {
        "id": "RENDER-GIT-1",
        "command": "render / render --check",
        "boundary": "uncaught git rev-parse HEAD failure during lock rendering",
        "python_stdout": "empty",
        "python_stderr": "raw Git stderr plus CalledProcessError traceback",
        "python_exit": "1",
        "python_state": "unchanged",
        "rust_stdout": "empty",
        "rust_stderr": "raw Git stderr only",
        "rust_exit": "1",
        "rust_state": "unchanged",
        "evidence": "scripts/check_render_parity.py git-nonrepository git-no-head",
        "umbrella": "check_render_parity.py",
    },
    {
        "id": "UPDATE-GIT-1",
        "command": "update --source sample",
        "boundary": "uncaught source-update Git failures, including after rollback",
        "python_stdout": "empty except dirty-checkout Git stdout",
        "python_stderr": "actual Git streams plus CalledProcessError traceback",
        "python_exit": "1",
        "python_state": "pre-snapshot failures remain pre-mutation; rollback matches Python",
        "rust_stdout": "matches Python without traceback",
        "rust_stderr": "actual Git streams without traceback",
        "rust_exit": "1",
        "rust_state": "matches Python filesystem and Git semantic state",
        "evidence": "scripts/check_update_parity.py missing-branch",
        "umbrella": "check_update_parity.py",
    },
    {
        "id": "UPDATE-CONFIG-1",
        "command": "update --source sample",
        "boundary": ".gitmodules configparser.Error family during registration parse",
        "python_stdout": "empty",
        "python_stderr": "traceback plus exact final configparser.<Class>Error message",
        "python_exit": "1",
        "python_state": "unchanged",
        "rust_stdout": "empty",
        "rust_stderr": "exact final configparser.<Class>Error message without stack frames",
        "rust_exit": "1",
        "rust_state": "unchanged",
        "evidence": "scripts/check_update_parity.py config-duplicate-section",
        "umbrella": "check_update_parity.py",
    },
)

MIGRATION = (
    {
        "id": "PYTHON-TO-RUST-UPGRADE",
        "command": "status after python3 -m agent_workbench install",
        "boundary": "producer-written ownership consumed in place by Rust",
        "python_stdout": "status operations",
        "python_stderr": "empty on success",
        "python_exit": "0",
        "python_state": "inert snapshot; v3 skills / v4 activation retained",
        "rust_stdout": "matches Python",
        "rust_stderr": "matches Python",
        "rust_exit": "0",
        "rust_state": "inert snapshot; no rewrite of producer bytes on status",
        "evidence": "scripts/check_python_baseline_upgrade.py tracer",
        "umbrella": "check_python_baseline_upgrade.py",
    },
    {
        "id": "REVERSE-STATUS-NOT-SUPPORTED",
        "command": "python3 -m agent_workbench status on Rust-written ownership",
        "boundary": "unsupported reverse upgrade",
        "python_stdout": "status or error report",
        "python_stderr": "error class if rejected",
        "python_exit": "0, 1, or 2",
        "python_state": "Rust evidence bytes unchanged",
        "rust_stdout": "not a reverse consumer",
        "rust_stderr": "not a reverse consumer",
        "rust_exit": "n/a",
        "rust_state": "producer for this row",
        "evidence": "scripts/check_python_baseline_upgrade.py reverse_and_downgrade",
        "umbrella": "check_python_baseline_upgrade.py",
    },
    {
        "id": "REJECT-V4-AS-V3",
        "command": "status on v4 activation rewritten as v3",
        "boundary": "unsupported schema downgrade with slots kept or dropped",
        "python_stdout": "empty or error report",
        "python_stderr": "schema rejection",
        "python_exit": "nonzero",
        "python_state": "file bytes unchanged",
        "rust_stdout": "empty or error report",
        "rust_stderr": "schema rejection",
        "rust_exit": "nonzero",
        "rust_state": "file bytes unchanged",
        "evidence": "scripts/check_python_baseline_upgrade.py reverse_and_downgrade",
        "umbrella": "check_python_baseline_upgrade.py",
    },
)


def exception_ids() -> list[str]:
    found = []
    for line in EXCEPTIONS.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and " — " in line:
            found.append(line[3:].split(" — ", 1)[0].strip())
    return found


def documentation_records_catalog() -> None:
    readme = README.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "## Intentional Python/Rust differences" in readme, readme
    parts = contract.split("## F6 — intentional differences and migration", 1)
    assert len(parts) == 2, contract
    body = parts[1]
    assert "Acceptance-to-test map" in body, body[:500]
    commands = readme.split("## Commands", 1)[1].split("## ", 1)[0]
    assert "agent-workbench validate" in commands
    assert "python3 -m agent_workbench validate" not in commands
    for row in (*CATALOG, *MIGRATION):
        assert row["id"] in readme, f"README missing {row['id']}"
        for field in (
            "id",
            "command",
            "boundary",
            "python_stdout",
            "python_stderr",
            "python_exit",
            "python_state",
            "rust_stdout",
            "rust_stderr",
            "rust_exit",
            "rust_state",
            "evidence",
        ):
            assert row[field] in body, f"SECTION_F_CONTRACT missing {field} for {row['id']}"


def catalog_matches_exceptions() -> None:
    documented = exception_ids()
    catalogued = [row["id"] for row in CATALOG]
    assert documented == catalogued, (documented, catalogued)
    extra = {row["id"] for row in CATALOG} - set(documented)
    assert not extra, extra


def evidence_is_wired() -> None:
    umbrella = UMBRELLA.read_text(encoding="utf-8")
    assert "check_intentional_differences.py" in umbrella
    for row in (*CATALOG, *MIGRATION):
        assert row["umbrella"] in umbrella, row["id"]
        path = REPO / row["evidence"].split()[0].split("::", 1)[0]
        assert path.is_file(), path
    todo = TODO.read_text(encoding="utf-8")
    assert "- [ ] Remove Python runtime code" in todo


def default_engine_unchanged() -> None:
    result = python_cli(REPO, ["--help"])
    assert result.returncode == 0, result.stderr
    assert result.args[:3] == [sys.executable, "-m", "agent_workbench"]
    text = result.stdout.decode()
    assert "usage: agent-workbench" in text


def paired_git_validate() -> None:
    git_revision_rows()


def paired_git_render() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/check_render_parity.py"), "git-nonrepository", "git-no-head"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout.split("passed")[-1]


def paired_update_exceptions() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/check_update_parity.py"),
            "missing-branch",
            "config-duplicate-section",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def exception_tests_assert_separately() -> None:
    validate = (REPO / "scripts/check_validate_parity.py").read_text(encoding="utf-8")
    assert "assert py.returncode == 1 and py.stdout == b''" in validate
    assert "CalledProcessError" in validate
    assert "(rs.returncode,rs.stdout,rs.stderr) == (1,b'',git.stderr)" in validate
    render = (REPO / "scripts/check_render_parity.py").read_text(encoding="utf-8")
    assert "if command[0] == sys.executable:" in render
    assert "assert b'Traceback' not in stderr" in render
    update = (REPO / "scripts/check_update_parity.py").read_text(encoding="utf-8")
    assert "if engine[0] == sys.executable:" in update
    assert "config_diagnostic" in update
    assert "assert b'Traceback' not in stderr" in update
    parent = (REPO / "rust/src/transaction/state_parent_contract.py").read_text(encoding="utf-8")
    assert "Python" in parent and "Rust" in parent
    stage = (REPO / "rust/src/transaction/stage_cleanup_contract.py").read_text(encoding="utf-8")
    assert "Python" in stage and "Rust" in stage
    for path in (
        REPO / "scripts/check_validate_parity.py",
        REPO / "scripts/check_render_parity.py",
        REPO / "scripts/check_update_parity.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert "normalize_traceback" not in text
        assert "generic normalizer" not in text.lower()


def migration_rows(base: Path) -> int:
    return same_tree_status(base, "hermes") + reverse_and_downgrade(base)


def main() -> int:
    require_python312()
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    documentation_records_catalog()
    catalog_matches_exceptions()
    evidence_is_wired()
    default_engine_unchanged()
    exception_tests_assert_separately()
    build = subprocess.run(
        ["cargo", "build", "--quiet", "--manifest-path", str(REPO / "rust/Cargo.toml")],
        cwd=REPO,
    )
    if build.returncode:
        sys.stderr.write(build.stderr.decode() if isinstance(build.stderr, bytes) else (build.stderr or ""))
        return build.returncode
    assert RUST.is_file(), RUST
    paired_git_validate()
    paired_git_render()
    paired_update_exceptions()
    with tempfile.TemporaryDirectory(prefix="section-f6-", dir=os.environ["TMPDIR"]) as raw:
        rows = migration_rows(Path(raw))
    print(
        f"F6 intentional differences: {len(CATALOG)} authorized exceptions, "
        f"{len(MIGRATION)} migration directions, {rows} producer/rewrite rows; "
        f"oracle python3 -m agent_workbench; default CLI is Rust"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
