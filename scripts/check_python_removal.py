#!/usr/bin/env python3
"""F8: remove the Python runtime after default-Rust release candidates pass."""
from __future__ import annotations

import os
from pathlib import Path
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
    state_file,
)
from check_release_executable import (
    installed_binary,
    invoke as invoke_release,
    package,
)
from parity_oracle import require_python312

CONTRACT = REPO / "rust/SECTION_F_CONTRACT.md"
README = REPO / "README.md"
UMBRELLA = REPO / "scripts/check_rust_plan_parity.py"
TODO = REPO / "TODO.md"
WORKFLOW = REPO / ".github/workflows/validate.yml"
MAKEFILE = REPO / "Makefile"
WRAPPER = REPO / "scripts" / "agent-workbench"
ORACLE_WRAPPERS = (
    REPO / "scripts/validate.py",
    REPO / "scripts/render_marketplaces.py",
    REPO / "scripts/update.py",
    REPO / "scripts/install_skills.py",
)
F6_IDS = tuple(row["id"] for row in (*CATALOG, *MIGRATION))
EVIDENCE = (
    "documentation_records_removal",
    "leftover_python_runtime_removed",
    "catalog_ci_uses_default_cli",
    "python_module_not_supported",
    "shebang_oracle_wrappers_removed",
    "clean_install_release_candidate",
    "existing_state_release_candidate",
    "reverse_and_downgrade_fail_closed",
    "f6_catalog_required",
)


def documentation_records_removal() -> None:
    readme = README.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")
    parts = contract.split("## F8 — Python removal", 1)
    assert len(parts) == 2, "SECTION_F_CONTRACT missing F8 Python removal"
    body = parts[1]
    assert "Acceptance-to-test map" in body, body[:500]
    commands = readme.split("## Commands", 1)[1].split("## ", 1)[0]
    assert "agent-workbench validate" in commands
    assert "python3 -m agent_workbench" not in commands
    assert "Python runtime removed" in readme or "Python runtime is removed" in readme
    for exception_id in F6_IDS:
        assert exception_id in readme, exception_id
        assert exception_id in contract, exception_id
    for evidence in EVIDENCE:
        assert evidence in body, evidence
    todo = TODO.read_text(encoding="utf-8")
    assert "Remove Python runtime code" in todo


def leftover_python_runtime_removed() -> None:
    assert not (REPO / "agent_workbench").exists(), "in-tree agent_workbench/ must be removed"
    assert not (REPO / "tests").exists(), "Python package tests/ must be removed"
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "unittest discover" not in makefile
    assert "agent_workbench/*.py" not in makefile
    assert "scripts/install_skills.py" not in makefile
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "python3 -m agent_workbench" not in workflow
    assert "unittest discover" not in workflow
    assert "install_skills.py" not in workflow
    umbrella = UMBRELLA.read_text(encoding="utf-8")
    assert "check_python_removal.py" in umbrella
    layout = README.read_text(encoding="utf-8")
    repo_layout = layout.split("## Repository layout", 1)[1].split("## ", 1)[0]
    assert "agent_workbench/" not in repo_layout
    assert "tests/" not in repo_layout


def catalog_ci_uses_default_cli() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "python3 -m agent_workbench" not in workflow
    assert "agent-workbench validate" in workflow or "scripts/agent-workbench validate" in workflow
    assert "render --check" in workflow
    assert "plan --target" in workflow
    makefile = MAKEFILE.read_text(encoding="utf-8")
    dry = subprocess.run(
        ["make", "-n", "install-dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "install_skills.py" not in dry.stdout, dry.stdout
    assert "agent-workbench" in dry.stdout or "$(CLI)" in makefile
    test = subprocess.run(
        ["make", "-n", "test"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "unittest" not in test.stdout, test.stdout
    assert "cargo test" in test.stdout, test.stdout
    parity_job = workflow.split("rust-plan-parity:", 1)[1]
    checkout = parity_job.split("uses: actions/checkout@v4", 1)[1].split("- uses:", 1)[0]
    assert "fetch-depth: 0" in checkout, checkout


def python_module_not_supported(root: Path) -> None:
    env = env_for(root)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "agent_workbench", "--help"],
        cwd=REPO,
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, result.stdout
    combined = (result.stderr + result.stdout).decode(errors="replace")
    assert "No module named" in combined or "agent_workbench" in combined


def shebang_oracle_wrappers_removed() -> None:
    for path in ORACLE_WRAPPERS:
        assert not path.exists(), path
    text = WRAPPER.read_text(encoding="utf-8")
    assert "-m agent_workbench" not in text
    assert "python3 -m" not in text


def f6_catalog_required() -> None:
    umbrella = UMBRELLA.read_text(encoding="utf-8")
    assert "check_intentional_differences.py" in umbrella
    assert "check_python_baseline_upgrade.py" in umbrella
    assert "check_python_removal.py" in umbrella
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


def clean_install_release_candidate(prefix: Path, base: Path) -> int:
    root = base / "clean-rc"
    root.mkdir()
    from check_rust_plan_parity import fixture

    repository = fixture(root)
    argv = skills_common(root, repository, "hermes", "install")
    result = invoke_release(installed_binary(prefix), root, argv)
    assert result.returncode == 0, result.stderr
    assert json_schema(root) == 3
    status = invoke_release(
        installed_binary(prefix), root, skills_common(root, repository, "hermes", "status")
    )
    assert status.returncode == 0, status.stderr
    uninstall = invoke_release(
        installed_binary(prefix),
        root,
        skills_common(root, repository, "hermes", "uninstall"),
    )
    assert uninstall.returncode == 0, uninstall.stderr
    return 1


def existing_state_release_candidate(prefix: Path, base: Path) -> int:
    rows = 0
    status_root = base / "existing-status"
    status_root.mkdir()
    repository = python_install_skills(status_root, "hermes")
    argv = skills_common(status_root, repository, "hermes", "status")
    before = snapshot(status_root)
    defaulted = invoke_release(installed_binary(prefix), status_root, argv)
    assert snapshot(status_root) == before
    assert defaulted.returncode == 0, defaulted.stderr
    rows += 1

    reinstall_root = base / "existing-reinstall"
    reinstall_root.mkdir()
    repository = python_install_skills(reinstall_root, "hermes")
    argv = skills_common(reinstall_root, repository, "hermes", "install")
    before = snapshot(reinstall_root)
    defaulted = invoke_release(installed_binary(prefix), reinstall_root, argv)
    assert snapshot(reinstall_root) == before
    assert defaulted.returncode == 0, defaulted.stderr
    rows += 1

    uninstall_root = base / "existing-uninstall"
    uninstall_root.mkdir()
    repository = python_install_skills(uninstall_root, "hermes")
    result = invoke_release(
        installed_binary(prefix),
        uninstall_root,
        skills_common(uninstall_root, repository, "hermes", "uninstall"),
    )
    assert result.returncode == 0, result.stderr
    rows += 1
    return rows


def reverse_and_downgrade_fail_closed(base: Path) -> int:
    return reverse_and_downgrade(base / "reverse")


def json_schema(root: Path) -> int:
    import json

    return json.loads(state_file(root, "hermes").read_text())["schemaVersion"]


def main() -> int:
    require_python312()
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    documentation_records_removal()
    leftover_python_runtime_removed()
    catalog_ci_uses_default_cli()
    f6_catalog_required()
    shebang_oracle_wrappers_removed()
    with tempfile.TemporaryDirectory(prefix="section-f8-", dir=os.environ["TMPDIR"]) as raw:
        base = Path(raw)
        help_root = base / "help"
        help_root.mkdir()
        python_module_not_supported(help_root)
        prefix = base / "prefix"
        package(prefix)
        rows = clean_install_release_candidate(prefix, base)
        rows += existing_state_release_candidate(prefix, base)
        rows += reverse_and_downgrade_fail_closed(base)
    print(
        f"F8 Python removal: default Rust CLI release candidates passed; "
        f"in-tree agent_workbench/ removed; {len(CATALOG)} F6 exceptions unchanged; "
        f"{rows} clean-install/existing-state/fail-closed rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
