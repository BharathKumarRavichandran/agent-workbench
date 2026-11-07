#!/usr/bin/env python3
"""F1: clean-install and upgrade from pinned-Python ownership state.

No Git tags or GitHub releases exist. The producer is in-tree
`python3 -m agent_workbench` at Section E merge
04502a0054fcd3c666e06f4e20ef7603293cfed3. Rust consumes that CLI-written
state; it does not consume Python plans or results.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "tests")]
from parity_oracle import require_python312
from check_rust_plan_parity import TARGETS, fixture
from check_cursor_activation_parity import prepare
from agent_workbench.state import load_state, write_state

RUST = REPO / "rust/target/debug/agent-workbench-rs"
PINNED_BASELINE = "04502a0054fcd3c666e06f4e20ef7603293cfed3"
PINNED_PYTHON_TREE = "01c263007031df3ea599813796e2d3c127f4b4ce"


def require_pinned_baseline() -> None:
    require_python312()
    tags = subprocess.run(["git", "tag", "-l"], cwd=REPO, capture_output=True, text=True, check=True)
    if tags.stdout.strip():
        raise SystemExit(f"git tags exist; update the F1 pin before claiming coverage:\n{tags.stdout}")
    releases = subprocess.run(["gh", "release", "list"], cwd=REPO, capture_output=True, text=True)
    if releases.returncode == 0 and releases.stdout.strip():
        raise SystemExit(f"GitHub releases exist; update the F1 pin before claiming coverage:\n{releases.stdout}")
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD:agent_workbench"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    if tree != PINNED_PYTHON_TREE:
        raise SystemExit(
            f"production package agent_workbench drifted from pinned tree {PINNED_PYTHON_TREE}: {tree}"
        )
    has_pin = subprocess.run(
        ["git", "cat-file", "-e", f"{PINNED_BASELINE}^{{commit}}"],
        cwd=REPO,
        capture_output=True,
    )
    if has_pin.returncode == 0:
        present = subprocess.run(
            ["git", "merge-base", "--is-ancestor", PINNED_BASELINE, "HEAD"], cwd=REPO
        )
        if present.returncode:
            raise SystemExit(f"pinned Python baseline {PINNED_BASELINE} is not an ancestor of HEAD")


def env_for(root: Path) -> dict[str, str]:
    return dict(
        os.environ,
        HOME=str(root / "home"),
        PYTHONDONTWRITEBYTECODE="1",
        XDG_STATE_HOME=str(root / "state"),
        HERMES_HOME=str(root / "hermes-home"),
        CODEX_HOME=str(root / "codex-home"),
    )


def prefix(engine: str) -> list[str]:
    return [sys.executable, "-m", "agent_workbench"] if engine == "python" else [str(RUST)]


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd=REPO, env=env_for(root), capture_output=True, umask=0o022)


def snapshot(root: Path, base: Path | None = None) -> list[tuple[str, str, int, str | None]]:
    origin = base or root
    rows = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        kind = "link" if stat.S_ISLNK(info.st_mode) else "dir" if stat.S_ISDIR(info.st_mode) else "file"
        rel = str(path.relative_to(root))
        if kind == "link":
            content = os.readlink(path).replace(str(origin), "<BASE>")
        elif kind == "file":
            raw = path.read_bytes()
            if path.name.endswith(".transaction-key") and len(raw) == 32:
                content = "<random-32-byte-key>"
            else:
                content = raw.replace(str(origin).encode(), b"<BASE>").hex()
        else:
            content = None
        rows.append((rel, kind, stat.S_IMODE(info.st_mode), content))
    return rows


def skills_common(root: Path, repository: Path, target: str, action: str, *extra: str) -> list[str]:
    return [
        "--root",
        str(repository),
        "--profile",
        "pstack",
        action,
        "--target",
        target,
        "--state-dir",
        str(root / "state"),
        "--destination",
        str(root / "destination"),
        "--json",
        *extra,
    ]


def state_file(root: Path, target: str, marketplace: str = "market") -> Path:
    return root / "state/agent-workbench" / marketplace / "pstack" / f"{target}.json"


def python_install_skills(root: Path, target: str) -> Path:
    repository = fixture(root)
    result = run(prefix("python") + skills_common(root, repository, target, "install"), root)
    assert result.returncode == 0, result.stderr
    owned = json.loads(state_file(root, target).read_text())
    assert owned["schemaVersion"] == 3, owned["schemaVersion"]
    return repository


def fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def same_tree_status(base: Path, target: str) -> int:
    root = fresh(base / f"skills-status-{target}")
    repository = python_install_skills(root, target)
    before = snapshot(root)
    py = run(prefix("python") + skills_common(root, repository, target, "status"), root)
    assert snapshot(root) == before
    rs = run(prefix("rust") + skills_common(root, repository, target, "status"), root)
    assert snapshot(root) == before
    assert (py.returncode, py.stdout, py.stderr) == (rs.returncode, rs.stdout, rs.stderr), (
        target,
        py.returncode,
        rs.returncode,
        py.stdout,
        rs.stdout,
        py.stderr,
        rs.stderr,
    )
    assert py.returncode == 0
    return 1


def same_tree_reinstall(base: Path, target: str) -> int:
    root = fresh(base / f"skills-reinstall-{target}")
    repository = python_install_skills(root, target)
    before = snapshot(root)
    py = run(prefix("python") + skills_common(root, repository, target, "install"), root)
    assert snapshot(root) == before
    rs = run(prefix("rust") + skills_common(root, repository, target, "install"), root)
    assert snapshot(root) == before
    assert (py.returncode, py.stdout, py.stderr) == (rs.returncode, rs.stdout, rs.stderr)
    assert py.returncode == 0
    kinds = {item["kind"] for item in json.loads(py.stdout)}
    assert kinds <= {"keep"}, kinds
    return 1


def paired_after_python(base: Path, name: str, target: str, mutate, consume: str, *extra: str) -> tuple:
    rows = []
    for engine in ("python", "rust"):
        root = fresh(base / name / engine)
        repository = python_install_skills(root, target)
        mutate(root, repository)
        result = run(prefix(consume if engine == "rust" else "python") + skills_common(root, repository, target, extra[0], *extra[1:]), root)
        rows.append((result.returncode, result.stdout.replace(str(root).encode(), b"<BASE>"), result.stderr.replace(str(root).encode(), b"<BASE>"), snapshot(root, root)))
    assert rows[0] == rows[1], (name, target, rows[0][:3], rows[1][:3])
    return rows[0]


def skills_uninstall(base: Path, target: str) -> int:
    def mutate(root, repository):
        return None

    row = paired_after_python(base, f"skills-uninstall-{target}", target, mutate, "rust", "uninstall")
    assert row[0] == 0
    return 1


def skills_foreign(base: Path, target: str) -> int:
    def mutate(root, repository):
        path = root / "destination/example"
        path.unlink()
        path.write_bytes(b"foreign")

    row = paired_after_python(base, f"skills-foreign-{target}", target, mutate, "rust", "status")
    assert row[0] == 1
    return 1


def skills_modified(base: Path, target: str) -> int:
    def mutate(root, repository):
        path = root / "destination/example"
        path.unlink()
        path.symlink_to("foreign", target_is_directory=True)

    row = paired_after_python(base, f"skills-modified-{target}", target, mutate, "rust", "status")
    assert row[0] == 1
    return 1


def skills_clean(base: Path, target: str) -> int:
    expected = None
    for engine in ("python", "rust"):
        root = fresh(base / f"skills-clean-{target}" / engine)
        repository = fixture(root)
        result = run(prefix(engine) + skills_common(root, repository, target, "install"), root)
        row = (result.returncode, result.stdout.replace(str(root).encode(), b"<BASE>"), result.stderr.replace(str(root).encode(), b"<BASE>"), snapshot(root, root))
        assert result.returncode == 0, result.stderr
        assert json.loads(state_file(root, target).read_text())["schemaVersion"] == 3
        if expected is None:
            expected = row
        else:
            assert row == expected, (target, expected[:3], row[:3])
    return 1


def rewrite_v2(path: Path) -> None:
    state = load_state(path)
    write_state(path, replace(state, schema_version=2, destination_roots={}, slots={}))
    payload = json.loads(path.read_text())
    assert payload["schemaVersion"] == 2
    assert "destinationRoots" not in payload and "slots" not in payload


def skills_v2(base: Path, target: str, action: str) -> int:
    expected = None
    for engine in ("python", "rust"):
        root = fresh(base / f"skills-v2-{action}-{target}" / engine)
        repository = python_install_skills(root, target)
        rewrite_v2(state_file(root, target))
        result = run(prefix(engine) + skills_common(root, repository, target, action), root)
        row = (result.returncode, result.stdout.replace(str(root).encode(), b"<BASE>"), result.stderr.replace(str(root).encode(), b"<BASE>"), snapshot(root, root))
        assert result.returncode == 0, result.stderr
        if action == "install":
            assert json.loads(state_file(root, target).read_text())["schemaVersion"] == 3
        if expected is None:
            expected = row
        else:
            assert row == expected, (target, action, expected[:3], row[:3])
    return 1


def add_copied(repository: Path) -> None:
    agents = repository / "plugins/pstack/agents/sample.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("---\nname: sample\ndescription: Sample.\n---\nagent\n", encoding="utf-8")
    rules = repository / "plugins/pstack/rules/sample.mdc"
    rules.parent.mkdir(parents=True)
    rules.write_text("---\ndescription: Sample.\n---\nrule\n", encoding="utf-8")
    commands = repository / "plugins/pstack/commands/sample.md"
    commands.parent.mkdir(parents=True)
    commands.write_text("---\nargument-hint: value\n---\ncommand\n", encoding="utf-8")
    catalog_path = repository / "catalog/plugins.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["plugins"][0]["artifacts"].update(
        {"agents": {"path": "pstack/agents"}, "rules": {"path": "pstack/rules"}, "commands": {"path": "pstack/commands"}}
    )
    catalog_path.write_text(json.dumps(catalog))


def copied_handoff(base: Path, target: str) -> int:
    components = ["skills", "agents", "rules"] if target == "cursor" else ["agents", "commands"]
    rows = 0
    for phase, extra, mutate in (
        ("status", ["status"], None),
        ("reinstall", ["install"], None),
        ("uninstall", ["uninstall"], None),
        (
            "conflict",
            ["status"],
            lambda root: (
                (root / "project/.cursor/agents/sample.md").write_bytes(b"changed")
                if target == "cursor"
                else (root / "home/.claude/agents/sample.md").write_bytes(b"changed")
            ),
        ),
        (
            "foreign",
            ["status"],
            lambda root: (
                (root / "project/.cursor/agents/foreign.md").write_bytes(b"foreign")
                if target == "cursor"
                else (root / "home/.claude/agents/foreign.md").write_bytes(b"foreign")
            ),
        ),
    ):
        expected = None
        for engine in ("python", "rust"):
            root = fresh(base / f"copied-{target}-{phase}" / engine)
            repository = fixture(root)
            add_copied(repository)
            args = ["--component=" + item for item in components]
            if target == "cursor":
                args += ["--project-root", str(root / "project")]
            install = run(
                prefix("python")
                + [
                    "--root",
                    str(repository),
                    "--profile",
                    "pstack",
                    "install",
                    "--target",
                    target,
                    "--state-dir",
                    str(root / "state"),
                    "--destination",
                    str(root / "destination"),
                    "--json",
                    *args,
                ],
                root,
            )
            assert install.returncode == 0, install.stderr
            owned = json.loads(state_file(root, target).read_text())
            assert owned["schemaVersion"] >= 3
            if mutate is not None:
                mutate(root)
            consume = engine
            command = [
                "--root",
                str(repository),
                "--profile",
                "pstack",
                extra[0],
                "--target",
                target,
                "--state-dir",
                str(root / "state"),
                "--destination",
                str(root / "destination"),
                "--json",
            ]
            if extra[0] != "uninstall":
                command += args
            elif target == "cursor":
                command += ["--project-root", str(root / "project")]
            result = run(prefix(consume) + command, root)
            row = (
                result.returncode,
                result.stdout.replace(str(root).encode(), b"<BASE>"),
                result.stderr.replace(str(root).encode(), b"<BASE>"),
                snapshot(root, root),
            )
            if expected is None:
                expected = row
            else:
                assert row == expected, (target, phase, expected[:3], row[:3])
            if phase == "foreign":
                leftover = (
                    root / "project/.cursor/agents/foreign.md"
                    if target == "cursor"
                    else root / "home/.claude/agents/foreign.md"
                )
                assert leftover.read_bytes() == b"foreign"
        rows += 1
    return rows


def activation_args(root: Path, repository: Path, target: str, action: str) -> list[str]:
    args = [
        "--root",
        str(repository),
        "--profile",
        "pstack",
        action,
        "--target",
        target,
        "--state-dir",
        str(root / "state"),
        "--project-root",
        str(root / "project"),
        "--json",
    ]
    if action != "uninstall":
        args += ["--component", "hooks", "--component", "mcp"]
    return args


def python_install_activation(root_parent: Path, target: str) -> tuple[Path, Path]:
    root, repository = prepare(root_parent, "mixed", target)
    source = repository / "plugins/sample/hooks/stop.sh"
    source.write_text("#!/bin/sh\nexit 0\n")
    source.chmod(0o751)
    for key in root.rglob("*.transaction-key"):
        key.unlink()
    plan = run(prefix("python") + activation_args(root, repository, target, "plan"), root)
    tokens = sorted({item["trustToken"] for item in json.loads(plan.stdout) if item.get("trustToken")})
    grants = [part for token in tokens for part in ("--trust", token)]
    install = run(prefix("python") + activation_args(root, repository, target, "install") + grants, root)
    assert install.returncode == 0, install.stderr
    owned = json.loads(state_file(root, target, "test-marketplace").read_text())
    assert owned["schemaVersion"] == 4 and owned["slots"]
    assert not (root / "PAYLOAD_EXECUTED").exists()
    return root, repository


def add_foreign_activation(root: Path, target: str) -> None:
    config = root / "project" / (".cursor" if target == "cursor" else ".claude")
    document = config / ("hooks.json" if target == "cursor" else "settings.json")
    payload = json.loads(document.read_text())
    payload["foreignKey"] = {"preserve": True}
    document.write_text(json.dumps(payload))
    mcp = (root / "project/.cursor/mcp.json") if target == "cursor" else (root / "project/.mcp.json")
    servers = json.loads(mcp.read_text())
    servers["foreignKey"] = "preserve"
    mcp.write_text(json.dumps(servers))


def activation_same_tree(base: Path, target: str, action: str) -> int:
    root, repository = python_install_activation(fresh(base / f"activation-{target}-{action}"), target)
    before = snapshot(root)
    py = run(prefix("python") + activation_args(root, repository, target, action), root)
    assert snapshot(root) == before
    rs = run(prefix("rust") + activation_args(root, repository, target, action), root)
    assert snapshot(root) == before
    assert (py.returncode, py.stdout, py.stderr) == (rs.returncode, rs.stdout, rs.stderr)
    assert py.returncode == 0
    if action == "install":
        assert {item["kind"] for item in json.loads(py.stdout)} <= {"keep"}
    return 1


def public_ops(stdout: bytes, root: Path) -> list[dict]:
    ops = json.loads(stdout.decode().replace(str(root), "<BASE>"))
    rows = []
    for op in ops:
        row = {key: op[key] for key in ("kind", "component", "resourceKind", "reason", "diagnostic", "trustRequired") if key in op}
        for field in ("destination", "source", "document"):
            if field in op and isinstance(op[field], str):
                row[field] = op[field].replace(str(root), "<BASE>")
        rows.append(row)
    return rows


def activation_uninstall(base: Path, target: str, *, foreign: bool) -> int:
    expected = None
    for engine in ("python", "rust"):
        root, repository = python_install_activation(
            fresh(base / f"activation-{target}-{'foreign-' if foreign else ''}uninstall" / engine), target
        )
        if foreign:
            add_foreign_activation(root, target)
        result = run(prefix(engine) + activation_args(root, repository, target, "uninstall"), root)
        assert not (root / "PAYLOAD_EXECUTED").exists()
        row = (
            result.returncode,
            public_ops(result.stdout, root),
            result.stderr.replace(str(root).encode(), b"<BASE>"),
            snapshot(root, root),
        )
        if foreign:
            document = root / "project" / (".cursor" if target == "cursor" else ".claude") / (
                "hooks.json" if target == "cursor" else "settings.json"
            )
            assert json.loads(document.read_text())["foreignKey"] == {"preserve": True}
        if expected is None:
            expected = row
        else:
            assert row == expected, (target, foreign, expected[:3], row[:3])
        assert result.returncode == 0
    return 1


def activation_handoff(base: Path, target: str) -> int:
    return (
        activation_same_tree(base, target, "status")
        + activation_same_tree(base, target, "install")
        + activation_uninstall(base, target, foreign=False)
        + activation_uninstall(base, target, foreign=True)
    )


def reverse_and_downgrade(base: Path) -> int:
    rows = 0
    root = fresh(base / "reverse-rust-python")
    repository = fixture(root)
    install = run(prefix("rust") + skills_common(root, repository, "hermes", "install"), root)
    assert install.returncode == 0, install.stderr
    before = state_file(root, "hermes").read_bytes()
    status = run(prefix("python") + skills_common(root, repository, "hermes", "status"), root)
    assert state_file(root, "hermes").read_bytes() == before, "reverse status discarded Rust evidence"
    assert status.returncode in {0, 1, 2}
    rows += 1

    for name, mutate in (
        (
            "slots-kept",
            lambda data: data.__setitem__("schemaVersion", 3),
        ),
        (
            "slots-dropped",
            lambda data: (data.__setitem__("schemaVersion", 3), data.pop("slots", None)),
        ),
    ):
        parent = fresh(base / f"downgrade-{name}")
        root, repository = prepare(parent, "mixed", "cursor")
        for key in root.rglob("*.transaction-key"):
            key.unlink()
        plan = run(
            prefix("python")
            + [
                "--root",
                str(repository),
                "--profile",
                "pstack",
                "plan",
                "--target",
                "cursor",
                "--state-dir",
                str(root / "state"),
                "--project-root",
                str(root / "project"),
                "--json",
                "--component",
                "hooks",
                "--component",
                "mcp",
            ],
            root,
        )
        tokens = sorted({item["trustToken"] for item in json.loads(plan.stdout) if item.get("trustToken")})
        grants = [part for token in tokens for part in ("--trust", token)]
        install = run(
            prefix("python")
            + [
                "--root",
                str(repository),
                "--profile",
                "pstack",
                "install",
                "--target",
                "cursor",
                "--state-dir",
                str(root / "state"),
                "--project-root",
                str(root / "project"),
                "--json",
                "--component",
                "hooks",
                "--component",
                "mcp",
                *grants,
            ],
            root,
        )
        assert install.returncode == 0, install.stderr
        path = state_file(root, "cursor", "test-marketplace")
        original = path.read_bytes()
        data = json.loads(original)
        mutate(data)
        path.write_text(json.dumps(data, indent=2) + "\n")
        rewritten = path.read_bytes()
        for engine in ("python", "rust"):
            result = run(
                prefix(engine)
                + [
                    "--root",
                    str(repository),
                    "--profile",
                    "pstack",
                    "status",
                    "--target",
                    "cursor",
                    "--state-dir",
                    str(root / "state"),
                    "--project-root",
                    str(root / "project"),
                    "--json",
                    "--component",
                    "hooks",
                    "--component",
                    "mcp",
                ],
                root,
            )
            assert result.returncode != 0, (name, engine, result.stdout)
            assert path.read_bytes() == rewritten, (name, engine, "downgrade status mutated evidence")
        path.write_bytes(original)
        rows += 1
    return rows


def main() -> int:
    require_pinned_baseline()
    assert os.environ.get("TMPDIR"), "explicit isolated scratch TMPDIR required"
    build = subprocess.run(["cargo", "build", "--quiet", "--manifest-path", str(REPO / "rust/Cargo.toml")], cwd=REPO)
    if build.returncode:
        sys.stderr.write(build.stderr)
        return build.returncode
    selected = sys.argv[1:] or ["all"]
    if selected not in (["tracer"], ["all"]) and "all" not in selected:
        raise SystemExit(f"unknown F1 selection: {selected}")
    with tempfile.TemporaryDirectory(prefix="section-f1-", dir=os.environ["TMPDIR"]) as raw:
        base = Path(raw)
        rows = 0
        if selected == ["tracer"] or "all" in selected:
            rows += same_tree_status(base, "hermes")
            print("F1 tracer GREEN: Python hermes install then same-tree Python/Rust status match", flush=True)
            if selected == ["tracer"]:
                print(f"python baseline upgrade: {rows} tracer rows; pin {PINNED_BASELINE}")
                return 0
        if "all" in selected:
            for target in TARGETS:
                rows += skills_clean(base, target)
                rows += same_tree_status(base, target)
                rows += same_tree_reinstall(base, target)
                rows += skills_uninstall(base, target)
                rows += skills_foreign(base, target)
                rows += skills_modified(base, target)
                rows += skills_v2(base, target, "install")
                rows += skills_v2(base, target, "uninstall")
            rows += copied_handoff(base, "cursor")
            rows += copied_handoff(base, "claude")
            rows += activation_handoff(base, "cursor")
            rows += activation_handoff(base, "claude")
            rows += reverse_and_downgrade(base)
    print(
        f"python baseline upgrade: {rows} clean-install/upgrade rows; "
        f"pinned producer {PINNED_BASELINE}; no release tags"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
