#!/usr/bin/env python3
"""Black-box Python/Rust parity matrix for skills-only target lifecycles."""

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
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))
RUST = REPOSITORY / "rust" / "target" / "debug" / "agent-workbench-rs"
TARGETS = ("agents", "claude", "codex", "cursor", "grok", "hermes")


def run(
    command: list[str], env: dict[str, str | None] | None = None, *, umask: int = -1
) -> subprocess.CompletedProcess[str]:
    process_env = None
    if env is not None:
        process_env = os.environ.copy()
        for key, value in env.items():
            if value is None:
                process_env.pop(key, None)
            else:
                process_env[key] = value
    return subprocess.run(
        command,
        cwd=REPOSITORY,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
        umask=umask,
    )


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
                "targets": [{"id": target} for target in TARGETS],
            }
        ),
        encoding="utf-8",
    )
    return root


class Side:
    def __init__(
        self, implementation: str, base: Path, target: str, initialize: bool = True
    ) -> None:
        self.implementation = implementation
        self.base = base.resolve()
        self.target = target
        self.root = fixture(self.base) if initialize else self.base / "checkout"
        self.destination = self.base / "destination"
        self.state = self.base / "state"

    @property
    def source(self) -> Path:
        return self.root / "plugins/pstack/skills/example"

    @property
    def state_file(self) -> Path:
        return self.state / f"agent-workbench/market/pstack/{self.target}.json"

    def command(
        self,
        action: str,
        *extra: str,
        root: Path | None = None,
        explicit_destination: bool = True,
        destination_value: str | None = None,
        environment: dict[str, str | None] | None = None,
        umask: int = -1,
    ) -> subprocess.CompletedProcess[str]:
        common = [
            "--root",
            str(root or self.root),
            "--profile",
            "pstack",
            action,
            "--target",
            self.target,
        ]
        if explicit_destination:
            common.extend(
                [
                    "--destination",
                    destination_value
                    if destination_value is not None
                    else str(self.destination),
                ]
            )
        common.extend(["--state-dir", str(self.state), "--json", *extra])
        command = (
            [sys.executable, "-m", "agent_workbench", *common]
            if self.implementation == "python"
            else [str(RUST), *common]
        )
        process_environment: dict[str, str | None] = {
            "HOME": str(self.base / "home"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CODEX_HOME": str(self.base / "codex-home"),
            "HERMES_HOME": str(self.base / "hermes-home"),
        }
        if environment is not None:
            process_environment.update(environment)
        return run(command, env=process_environment, umask=umask)

    def normalize(self, text: str) -> str:
        return text.replace(str(self.base), "<BASE>")


def compare(label: str, python: Side, rust: Side, py: subprocess.CompletedProcess[str], rs: subprocess.CompletedProcess[str]) -> None:
    expected = (py.returncode, python.normalize(py.stdout), python.normalize(py.stderr))
    actual = (rs.returncode, rust.normalize(rs.stdout), rust.normalize(rs.stderr))
    if expected != actual:
        raise AssertionError(f"{label} mismatch\nPython: {expected!r}\nRust:   {actual!r}")


def compare_error_class(
    label: str,
    python: Side,
    rust: Side,
    py: subprocess.CompletedProcess[str],
    rs: subprocess.CompletedProcess[str],
    expected_code: int,
) -> None:
    expected = (
        py.returncode,
        python.normalize(py.stdout),
        py.stderr.startswith("error: "),
    )
    actual = (
        rs.returncode,
        rust.normalize(rs.stdout),
        rs.stderr.startswith("error: "),
    )
    required = (expected_code, "", True)
    if expected != required or actual != required:
        raise AssertionError(
            f"{label} error-class mismatch\nPython: {expected!r}\nRust:   {actual!r}"
        )


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


def pair(base: Path, name: str, target: str) -> tuple[Side, Side]:
    return (
        Side("python", base / target / name / "python", target),
        Side("rust", base / target / name / "rust", target),
    )


def default_destination(base: Path, target: str) -> None:
    environment_variable = {
        "codex": "CODEX_HOME",
        "hermes": "HERMES_HOME",
    }.get(target)
    cases: list[tuple[str, dict[str, str | None]]] = [
        ("configured", {}),
        (
            "home-unset",
            {"HOME": None, "CODEX_HOME": None, "HERMES_HOME": None},
        ),
    ]
    if environment_variable is not None:
        cases.extend(
            [
                ("environment-empty", {environment_variable: ""}),
                ("environment-relative", {environment_variable: "relative-home"}),
                ("environment-tilde", {environment_variable: "~/custom-home"}),
            ]
        )

    configured: tuple[Side, Side] | None = None
    for name, environment in cases:
        python, rust = pair(base, f"default-destination-{name}", target)
        if name == "configured":
            configured = (python, rust)
        compare(
            f"{target} default destination {name}",
            python,
            rust,
            python.command(
                "plan", explicit_destination=False, environment=environment
            ),
            rust.command("plan", explicit_destination=False, environment=environment),
        )

    assert configured is not None
    python, rust = configured
    compare(
        f"{target} default install",
        python,
        rust,
        python.command("install", explicit_destination=False),
        rust.command("install", explicit_destination=False),
    )
    compare_state(f"{target} default install", python, rust)
    for side in (python, rust):
        roots = {
            "agents": side.base / "home/.agents/skills",
            "claude": side.base / "home/.claude/skills",
            "codex": side.base / "codex-home/skills",
            "cursor": side.base / "home/.cursor/skills",
            "grok": side.base / "home/.grok/skills",
            "hermes": side.base / "hermes-home/skills",
        }
        link = roots[target] / "example"
        if not link.is_symlink() or Path(os.readlink(link)).is_absolute():
            raise AssertionError(f"{target} default install did not create a relative link")
    compare(
        f"{target} default status",
        python,
        rust,
        python.command("status", explicit_destination=False),
        rust.command("status", explicit_destination=False),
    )
    compare(
        f"{target} default uninstall",
        python,
        rust,
        python.command("uninstall", explicit_destination=False),
        rust.command("uninstall", explicit_destination=False),
    )


def explicit_destination_expansion(base: Path, target: str) -> None:
    python, rust = pair(base, "explicit-destination-tilde", target)
    compare(
        f"{target} explicit destination tilde",
        python,
        rust,
        python.command("plan", destination_value="~/custom-skills"),
        rust.command("plan", destination_value="~/custom-skills"),
    )


def component_override(base: Path) -> None:
    for target in ("agents", "claude", "cursor"):
        python, rust = pair(base, f"component-override-{target}", target)
        for side in (python, rust):
            profile_path = side.root / "profiles/pstack.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["schemaVersion"] = 2
            selected = next(item for item in profile["targets"] if item["id"] == target)
            selected["components"] = ["skills", "agents"]
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
        compare(
            f"{target} explicit skills component override",
            python,
            rust,
            python.command("plan", "--component", "skills"),
            rust.command("plan", "--component", "skills"),
        )


def destination_precedence(base: Path) -> None:
    for target in TARGETS:
        python, rust = pair(base, f"{target}-destination-precedence", target)
        for side in (python, rust):
            profile_path = side.root / "profiles/pstack.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            selected = next(item for item in profile["targets"] if item["id"] == target)
            selected["destination"] = "~/profile-skills"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")

        profile_results = (
            python.command("plan", explicit_destination=False),
            rust.command("plan", explicit_destination=False),
        )
        compare(
            f"{target} profile destination overrides environment/default",
            python,
            rust,
            *profile_results,
        )
        cli_results = (
            python.command("plan", destination_value="~/cli-skills"),
            rust.command("plan", destination_value="~/cli-skills"),
        )
        compare(
            f"{target} CLI destination overrides profile",
            python,
            rust,
            *cli_results,
        )

        for side, profile_result, cli_result in zip(
            (python, rust), profile_results, cli_results
        ):
            destinations = (
                Path(json.loads(profile_result.stdout)[0]["destination"]),
                Path(json.loads(cli_result.stdout)[0]["destination"]),
            )
            expected = (
                side.base / "home/profile-skills/example",
                side.base / "home/cli-skills/example",
            )
            if destinations != expected:
                raise AssertionError(
                    f"{target} destination precedence mismatch: {destinations!r}"
                )


def shared_state_isolation(base: Path) -> None:
    implementations: dict[str, list[Side]] = {}
    for implementation in ("python", "rust"):
        shared = base / "shared-state" / implementation
        implementations[implementation] = [
            Side(implementation, shared, target, initialize=index == 0)
            for index, target in enumerate(TARGETS)
        ]

    for python, rust in zip(
        implementations["python"], implementations["rust"]
    ):
        compare(
            f"shared state install {python.target}",
            python,
            rust,
            python.command("install", explicit_destination=False),
            rust.command("install", explicit_destination=False),
        )
        compare_state(f"shared state install {python.target}", python, rust)

    expected = {f"{target}.json" for target in TARGETS}
    for sides in implementations.values():
        actual = {path.name for path in sides[0].state_file.parent.glob("*.json")}
        if actual != expected:
            raise AssertionError(f"target state isolation mismatch: {actual!r}")

    for python, rust in zip(
        implementations["python"], implementations["rust"]
    ):
        compare(
            f"shared state uninstall {python.target}",
            python,
            rust,
            python.command("uninstall", explicit_destination=False),
            rust.command("uninstall", explicit_destination=False),
        )


def install_status_uninstall(base: Path, target: str) -> None:
    absent_python, absent_rust = pair(base, "absent-uninstall", target)
    compare(
        "absent-state uninstall",
        absent_python,
        absent_rust,
        absent_python.command("uninstall"),
        absent_rust.command("uninstall"),
    )
    python, rust = pair(base, "clean", target)
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


def adoption_and_conflicts(base: Path, target: str) -> None:
    python, rust = pair(base, "adopt", target)
    for side in (python, rust):
        side.destination.mkdir(parents=True)
        (side.destination / "example").symlink_to(side.source, target_is_directory=True)
    compare("adoption", python, rust, python.command("install", "--adopt"), rust.command("install", "--adopt"))
    compare_state("adoption", python, rust)

    python, rust = pair(base, "foreign", target)
    for side in (python, rust):
        side.destination.mkdir(parents=True)
        (side.destination / "example").write_text("foreign", encoding="utf-8")
    compare("foreign conflict", python, rust, python.command("status"), rust.command("status"))

    python, rust = pair(base, "modified", target)
    compare("modified setup", python, rust, python.command("install"), rust.command("install"))
    for side in (python, rust):
        (side.destination / "example").unlink()
        (side.destination / "example").symlink_to("foreign", target_is_directory=True)
    compare("modified conflict", python, rust, python.command("status"), rust.command("status"))
    compare("modified uninstall", python, rust, python.command("uninstall"), rust.command("uninstall"))


def relocation(base: Path, target: str) -> None:
    python, rust = pair(base, "relocation", target)
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
        "target": side.target,
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


def v2_compatibility(base: Path, target: str) -> None:
    python, rust = pair(base, "v2-migrate", target)
    seed_v2(python)
    seed_v2(rust)
    compare("v2 migration", python, rust, python.command("install"), rust.command("install"))
    compare_state("v2 migration", python, rust)

    python, rust = pair(base, "v2-uninstall", target)
    seed_v2(python)
    seed_v2(rust)
    compare("v2 uninstall", python, rust, python.command("uninstall"), rust.command("uninstall"))


def state_rejection(base: Path, target: str) -> None:
    cases = {
        "v4": {"schemaVersion": 4},
        "null": {"schemaVersion": None},
        "duplicate": '{"schemaVersion":3,"schemaVersion":3}',
    }
    for name, value in cases.items():
        python, rust = pair(base, f"reject-{name}", target)
        for side in (python, rust):
            side.state_file.parent.mkdir(parents=True)
            text = value if isinstance(value, str) else json.dumps(value)
            side.state_file.write_text(text, encoding="utf-8")
        compare_error_class(
            f"state rejection {name}",
            python,
            rust,
            python.command("status"),
            rust.command("status"),
            2,
        )

    python, rust = pair(base, "broken-state-link", target)
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


def destination_root_authorization(base: Path) -> None:
    python, rust = pair(base, "roots", "hermes")
    for side in (python, rust):
        state = {
            "schemaVersion": 3,
            "repository": str(side.root),
            "marketplace": "market",
            "profile": "pstack",
            "target": "hermes",
            "destinationRoot": str(side.destination),
            "entries": {},
            "destinationRoots": {
                "skills": str(side.destination),
                "agents": str(side.base / "unauthorized-agents"),
            },
        }
        side.state_file.parent.mkdir(parents=True)
        side.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    compare(
        "unauthorized destination root",
        python,
        rust,
        python.command("status"),
        rust.command("status"),
    )
    python, rust = pair(base, "cursor-roots", "cursor")
    for side in (python, rust):
        state = {
            "schemaVersion": 3,
            "repository": str(side.root),
            "marketplace": "market",
            "profile": "pstack",
            "target": "cursor",
            "destinationRoot": str(side.destination),
            "entries": {},
            "destinationRoots": {
                "agents": str(side.base / "home" / ".cursor" / "agents"),
                "skills": str(side.destination),
            },
        }
        side.state_file.parent.mkdir(parents=True)
        side.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    compare(
        "authorized cursor agents root",
        python,
        rust,
        python.command("status"),
        rust.command("status"),
    )


def copied_agent_lifecycle(base: Path) -> None:
    python, rust = pair(base, "agent-copy", "cursor")
    for side in (python, rust):
        agent = side.root / "plugins/pstack/agents/sample-agent.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            "---\nname: sample-agent\ndescription: Example agent.\n---\nbody\n",
            encoding="utf-8",
        )
        catalog_path = side.root / "catalog/plugins.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["plugins"][0]["artifacts"]["agents"] = {"path": "pstack/agents"}
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    compare(
        "cursor agent copy install",
        python,
        rust,
        python.command("install", "--component", "agents"),
        rust.command("install", "--component", "agents"),
    )
    for side in (python, rust):
        installed = side.base / "home" / ".cursor" / "agents" / "sample-agent.md"
        assert installed.read_text(encoding="utf-8").endswith("body\n")
        assert stat.S_IMODE(installed.stat().st_mode) == 0o644
    compare(
        "cursor agent copy keep",
        python,
        rust,
        python.command("status", "--component", "agents"),
        rust.command("status", "--component", "agents"),
    )
    for side in (python, rust):
        installed = side.base / "home" / ".cursor" / "agents" / "sample-agent.md"
        installed.write_text("changed\n", encoding="utf-8")
    compare(
        "cursor agent copy conflict",
        python,
        rust,
        python.command("status", "--component", "agents"),
        rust.command("status", "--component", "agents"),
    )
    compare(
        "cursor agent copy conflict uninstall",
        python,
        rust,
        python.command("uninstall"),
        rust.command("uninstall"),
    )
    for side in (python, rust):
        installed = side.base / "home" / ".cursor" / "agents" / "sample-agent.md"
        assert installed.read_text(encoding="utf-8") == "changed\n"
        installed.write_bytes((side.root / "plugins/pstack/agents/sample-agent.md").read_bytes())
    compare(
        "cursor agent copy uninstall",
        python,
        rust,
        python.command("uninstall"),
        rust.command("uninstall"),
    )
    for side in (python, rust):
        installed = side.base / "home" / ".cursor" / "agents" / "sample-agent.md"
        assert not installed.exists()
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes((side.root / "plugins/pstack/agents/sample-agent.md").read_bytes())
    compare(
        "cursor agent copy adopt",
        python,
        rust,
        python.command("install", "--component", "agents", "--adopt"),
        rust.command("install", "--component", "agents", "--adopt"),
    )
    for side in (python, rust):
        (side.root / "plugins/pstack/agents/sample-agent.md").write_text(
            "---\nname: sample-agent\ndescription: Example agent.\n---\nreplaced\n",
            encoding="utf-8",
        )
    compare(
        "cursor agent copy replace",
        python,
        rust,
        python.command("install", "--component", "agents"),
        rust.command("install", "--component", "agents"),
    )


def add_document(side: Side, kind: str, name: str, body: str) -> Path:
    path = side.root / "plugins/pstack" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    catalog_path = side.root / "catalog/plugins.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["plugins"][0]["artifacts"][kind] = {"path": f"pstack/{kind}"}
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def cursor_project_paths(base: Path) -> None:
    python, rust = pair(base, "project-paths", "cursor")
    for side in (python, rust):
        add_document(side, "agents", "sample.md", "---\nname: sample\ndescription: Sample.\n---\nagent\n")
        add_document(side, "rules", "sample.mdc", "---\ndescription: Sample.\n---\nrule\n")
        add_document(side, "commands", "sample.md", "---\ndescription: Sample.\n---\ncommand\n")
    for component, diagnostic in (("rules", "PROJECT_ROOT_REQUIRED"), ("commands", "COMPONENT_UNSUPPORTED")):
        for action in ("plan", "install", "status"):
            py = python.command(action, "--component", component)
            rs = rust.command(action, "--component", component)
            assert py.returncode == 1 and json.loads(py.stdout)[0]["diagnostic"] == diagnostic
            compare(f"cursor {component} {action} rejection", python, rust, py, rs)
        for side in (python, rust):
            assert not side.state.exists() and not side.destination.exists()
            assert not (side.base / "home").exists()
    for action in ("plan", "install", "status", "uninstall"):
        results = []
        for side in (python, rust):
            extra = ["--project-root", str(side.base / "project")]
            if action != "uninstall":
                extra += ["--component", "skills", "--component", "agents", "--component", "rules"]
            results.append(side.command(action, *extra))
        assert results[0].returncode == 0, results[0].stderr
        compare(f"cursor project {action}", python, rust, *results)
        if action in ("install", "status"):
            compare_state(f"cursor project {action}", python, rust)
            for side in (python, rust):
                assert (side.destination / "example").is_symlink()
                for kind, filename in (("agents", "sample.md"), ("rules", "sample.mdc")):
                    installed = side.base / "project/.cursor" / kind / filename
                    assert installed.read_bytes() == (side.root / "plugins/pstack" / kind / filename).read_bytes()
    # Profile projectRoot is honored; a disagreeing CLI value overrides it.
    for side in (python, rust):
        profile_path = side.root / "profiles/pstack.json"
        profile = json.loads(profile_path.read_text())
        profile["schemaVersion"] = 2
        profile["targets"] = [{"id": "cursor", "components": ["agents", "rules"], "projectRoot": str(side.base / "profile-project")}]
        profile_path.write_text(json.dumps(profile))
    for override in (False, True):
        results = [side.command("plan", *(["--project-root", str(side.base / "cli-project")] if override else [])) for side in (python, rust)]
        assert results[0].returncode == 0
        compare(f"cursor profile project override={override}", python, rust, *results)


def claude_native_paths(base: Path) -> None:
    for project in (False, True):
        python, rust = pair(base, f"native-{project}", "claude")
        for side in (python, rust):
            add_document(side, "agents", "native.md", "---\nname: native\ndescription: Native agent.\n---\nagent bytes\n")
            add_document(side, "commands", "legacy.md", "---\nargument-hint: value\n---\nlegacy bytes\n")
            # Neither .mdc nor JSON is a legacy Markdown command.
            add_document(side, "commands", "ignored.mdc", "cursor rule")
            add_document(side, "commands", "ignored.json", '{"hooks": {}}')
        for action in ("plan", "install", "status", "uninstall"):
            results = []
            for side in (python, rust):
                extra = ["--project-root", str(side.base / "project")] if project else []
                if action != "uninstall":
                    extra += ["--component", "commands", "--component", "agents"]
                results.append(side.command(action, *extra))
            assert results[0].returncode == 0, results[0].stderr
            compare(f"claude native project={project} {action}", python, rust, *results)
            if action in ("install", "status"):
                compare_state("claude native", python, rust)
                for side in (python, rust):
                    root = side.base / ("project/.claude" if project else "home/.claude")
                    for kind, filename in (("agents", "native.md"), ("commands", "legacy.md")):
                        assert (root / kind / filename).read_bytes() == (side.root / "plugins/pstack" / kind / filename).read_bytes()
                    assert sorted(path.name for path in (root / "commands").iterdir()) == ["legacy.md"]
        results = [side.command("install", "--component", "rules", "--component", "agents") for side in (python, rust)]
        assert results[0].returncode == 1
        compare("claude does not translate rules", python, rust, *results)
        for side in (python, rust):
            assert not (side.base / "home/.claude/agents/native.md").exists()
    for kind, body in (("agents", "---\nname: wrong\ndescription: Agent.\n---\nbody"), ("agents", "---\nname: sample\n---\nbody"), ("commands", '{"hooks": {}}'), ("commands", "---\nnot closed")):
        python, rust = pair(base, f"invalid-{kind}-{len(body)}", "claude")
        for side in (python, rust):
            add_document(side, kind, "sample.md", body)
        results = [side.command("install", "--component", kind) for side in (python, rust)]
        compare_error_class(f"claude invalid {kind}", python, rust, results[0], results[1], 2)
        for side in (python, rust):
            assert not side.state.exists() and not side.destination.exists()
            assert not (side.base / "home/.claude").exists()


def copied_safety_rows(base: Path) -> None:
    import hashlib
    for mode in (0o600, 0o644, 0o755):
        python, rust = pair(base, f"copy-mode-{mode:o}", "cursor")
        for side in (python, rust):
            source = add_document(side, "agents", "sample.md", "---\r\nname: sample\r\ndescription: Bytes.\r\n---\r\nUnicode: café\r\n")
            source.chmod(mode)
        results = [side.command("install", "--component", "agents", umask=0o022) for side in (python, rust)]
        compare(f"copy source mode {mode:o}", python, rust, *results)
        assert results[0].returncode == 0
        compare_state("copy byte digest", python, rust)
        for side in (python, rust):
            assert stat.S_IMODE((side.root / "plugins/pstack/agents/sample.md").stat().st_mode) == mode
            content = (side.root / "plugins/pstack/agents/sample.md").read_bytes()
            installed = side.base / "home/.cursor/agents/sample.md"
            assert installed.read_bytes() == content
            assert stat.S_IMODE(installed.stat().st_mode) == 0o644
            assert json.loads(side.state_file.read_text())["entries"]["agent:sample.md"]["digest"] == hashlib.sha256(content).hexdigest()
    for shape in ("file", "nested", "root"):
        python, rust = pair(base, f"source-symlink-{shape}", "cursor")
        for side in (python, rust):
            source = add_document(side, "agents", "sample.md", "---\nname: sample\ndescription: Bytes.\n---\nbody\n")
            path = source.parent if shape == "root" else source
            if shape == "nested":
                path = source.parent / "nested/ignored.txt"
                path.parent.mkdir()
                path.write_text("ignored")
            moved = path.with_name(path.name + "-saved")
            path.rename(moved)
            path.symlink_to(moved)
        results = [side.command("install", "--component", "agents") for side in (python, rust)]
        if shape == "root":
            # RepoPath resolves an internal artifact alias before inspection.
            compare("copy canonical artifact alias", python, rust, *results)
            assert results[0].returncode == 0
            compare_state("copy canonical artifact alias", python, rust)
        else:
            compare_error_class(f"copy symlink {shape}", python, rust, results[0], results[1], 2)
            for side in (python, rust):
                assert not side.state.exists() and not (side.base / "home/.cursor").exists()
    python, rust = pair(base, "project-default-skills", "cursor")
    results = [side.command("install", "--project-root", str(side.base / "project"), explicit_destination=False) for side in (python, rust)]
    compare("project default skills root", python, rust, *results)
    compare_state("project default skills root", python, rust)


def python_copy_race(side: Side, scenario: str) -> bool:
    if str(REPOSITORY) not in sys.path:
        sys.path.insert(0, str(REPOSITORY))
    from unittest import mock
    from agent_workbench import installation as install
    from agent_workbench.model import ComponentKind as C
    from agent_workbench.workspace import Workspace
    with mock.patch.dict(os.environ, {"HOME": str(side.base / "home")}):
        workspace = Workspace.load(side.root, "pstack")
        project = side.base / "project"
        source = side.root / "plugins/pstack/agents/sample.md"
        agents = project / ".cursor/agents"
        components = (C.AGENTS,) if scenario.endswith("state-failure") else (C.SKILLS, C.AGENTS, C.RULES)
        def plan():
            return install.plan_install(workspace, "cursor", side.state, side.destination, components=components, project_root=project)
        removal = scenario.startswith("remove") or scenario == "selection-interrupted"
        if removal or scenario in ("replace-state-failure", "keep-source-change"):
            install.apply_plan(plan())
        if scenario == "replace-state-failure":
            source.write_text(source.read_text() + "updated\n")
        if scenario == "selection-interrupted":
            components = (C.SKILLS,)
        if scenario == "root-swap":
            agents.mkdir(parents=True)
        if scenario == "parent-swap":
            project.mkdir(parents=True)
        pending = install.plan_uninstall(workspace, "cursor", side.state, side.destination, project) if removal and scenario != "selection-interrupted" else plan()
        if scenario in ("source-change", "keep-source-change"):
            source.write_text("changed after planning")
        if scenario in ("source-symlink", "source-parent-symlink"):
            path = source if scenario == "source-symlink" else source.parent
            moved = path.with_suffix(".saved")
            path.rename(moved)
            path.symlink_to(moved)
        if scenario in ("root-swap", "parent-swap"):
            path = agents if scenario == "root-swap" else project
            path.rename(path.with_suffix(".detached"))
            path.mkdir()
        if scenario == "remove-modified":
            (agents / "sample.md").write_text("foreign after plan")
        real_create = install._DestinationRoot.create
        real_quarantine = install._DestinationRoot.quarantine
        real_write = install.write_state
        def swap_root(root):
            root.path.rename(root.path.with_suffix(".detached"))
            outside = side.base / "outside"
            outside.mkdir()
            root.path.symlink_to(outside)
        def create(root, operation, content):
            if scenario == "publish-root-swap" and root.path == agents:
                swap_root(root)
            result = real_create(root, operation, content)
            if scenario == "published-root-swap" and root.path == agents:
                swap_root(root)
            return result
        def quarantine(root, operation):
            if scenario in ("remove-interrupted", "selection-interrupted") and operation.component is C.RULES:
                raise OSError("injected failure")
            result = real_quarantine(root, operation)
            if scenario == "remove-root-swap" and root.path == agents:
                swap_root(root)
            return result
        def write(*args):
            if scenario.endswith("state-failure"):
                if scenario == "remove-foreign-state-failure":
                    (agents / "sample.md").write_text("foreign during rollback")
                raise OSError("injected failure")
            return real_write(*args)
        with mock.patch.object(install._DestinationRoot, "create", create), mock.patch.object(install._DestinationRoot, "quarantine", quarantine), mock.patch.object(install, "write_state", write):
            try:
                (install.apply_uninstall if removal and scenario != "selection-interrupted" else install.apply_plan)(pending)
            except (OSError, ValueError):
                return True
        return False


def copy_race_rows(base: Path) -> None:
    def snapshot(side: Side):
        found = {}
        def visit(path):
            import re
            key = re.sub(r"\.agent-workbench-quarantine-[^/]+", ".agent-workbench-quarantine-<ID>", str(path.relative_to(side.base)))
            if path.is_symlink():
                found[key] = ("link", side.normalize(str(path.readlink())))
            elif path.is_dir():
                found[key] = ("directory",)
                for child in sorted(path.iterdir()):
                    visit(child)
            elif path.exists():
                found[key] = ("file", stat.S_IMODE(path.stat().st_mode), path.read_bytes().hex())
        for name in ("project", "project.detached", "destination", "outside"):
            visit(side.base / name)
        if side.state_file.exists():
            found["state"] = (stat.S_IMODE(side.state_file.stat().st_mode), side.normalize(side.state_file.read_text()))
        return found
    for scenario in ("source-change", "keep-source-change", "source-symlink", "source-parent-symlink", "root-swap", "parent-swap", "publish-root-swap", "published-root-swap", "remove-root-swap", "create-state-failure", "replace-state-failure", "remove-state-failure", "remove-foreign-state-failure", "remove-modified", "remove-interrupted", "selection-interrupted"):
        python, rust = pair(base, f"race-{scenario}", "cursor")
        for side in (python, rust):
            add_document(side, "agents", "sample.md", "---\nname: sample\ndescription: Sample.\n---\nagent\n")
            add_document(side, "rules", "sample.mdc", "---\ndescription: Sample.\n---\nrule\n")
        py_failed = python_copy_race(python, scenario)
        result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib", "lifecycle::copy_parity_tests::differential_copy_race", "--", "--ignored", "--exact"], {"AW_COPY_CASE": scenario, "AW_COPY_BASE": str(rust.base)})
        assert result.returncode == 0, result.stdout + result.stderr
        rs_failed = json.loads((rust.base / "race-result.json").read_text())
        assert py_failed and rs_failed, f"{scenario}: failure Python={py_failed}, Rust={rs_failed}"
        py_snapshot, rs_snapshot = snapshot(python), snapshot(rust)
        assert py_snapshot == rs_snapshot, f"{scenario} filesystem mismatch\nPython: {py_snapshot!r}\nRust: {rs_snapshot!r}"
        if scenario in ("remove-interrupted", "selection-interrupted"):
            results = [side.command("uninstall", "--project-root", str(side.base / "project")) for side in (python, rust)]
            compare(f"{scenario} retry", python, rust, *results)
            assert results[0].returncode == 0
            assert not python.state_file.exists() and not rust.state_file.exists()


def state_wire_rows(base: Path) -> None:
    """Python load_state/as_dict is the wire oracle, not an authority oracle."""
    import copy
    from typing import Any
    if str(REPOSITORY) not in sys.path:
        sys.path.insert(0, str(REPOSITORY))
    from agent_workbench.state import load_state

    inputs = base / "inputs"
    inputs.mkdir(parents=True)
    state = {
        "schemaVersion": 4, "repository": "/repo", "marketplace": "market",
        "profile": "pstack", "target": "cursor", "destinationRoot": "/skills",
        "destinationRoots": {"skills": "/skills", "hooks": "/hooks", "mcp": "/config"},
        "entries": {"hook:bundle": {
            "key": "hook:bundle", "plugin": "pstack", "source": "plugins/hooks",
            "destination": "/hooks/bundle", "component": "hooks",
            "resourceKind": "directory-copy", "sourceRepository": "/repo",
            "digest": "a" * 64, "trust": "aw-trust-v1:" + "b" * 64,
            "trustProof": "aw-proof-v1:" + "c" * 64}},
        "slots": {"mcp:pstack:server": {
            "key": "mcp:pstack:server", "plugin": "pstack", "component": "mcp",
            "document": "/arbitrary/config.json", "slot": "server", "digest": "d" * 64,
            "trust": "aw-trust-v1:" + "e" * 64, "trustProof": "aw-proof-v1:" + "f" * 64}},
    }
    cases: dict[str, Any] = {"v4": state}
    for label, route in (("state", ()), ("entry", ("entries", "hook:bundle")),
                         ("slot", ("slots", "mcp:pstack:server"))):
        obj = state
        for key in route:
            obj = obj[key]
        for field in obj:
            for variant in ("missing", "null"):
                changed = copy.deepcopy(state)
                cursor = changed
                for key in route:
                    cursor = cursor[key]
                if variant == "missing":
                    del cursor[field]
                else:
                    cursor[field] = None
                cases[f"{label}-{field}-{variant}"] = changed
        changed = copy.deepcopy(state)
        cursor = changed
        for key in route:
            cursor = cursor[key]
        cursor["unknown"] = True
        cases[f"{label}-unknown"] = changed
    variants = {
        "bad-trust": ("entries", "hook:bundle", "trust", "aw-trust-v1:" + "A" * 64),
        "bad-proof": ("slots", "mcp:pstack:server", "trustProof", "aw-proof-v1:bad"),
        "bad-digest": ("entries", "hook:bundle", "digest", "a" * 63),
        "wrong-key": ("entries", "hook:bundle", "key", "hook:other"),
        "wrong-component": ("entries", "hook:bundle", "component", "agents"),
        "wrong-resource": ("entries", "hook:bundle", "resourceKind", "file-copy"),
        "wrong-slot-key": ("slots", "mcp:pstack:server", "slot", "other"),
        "wrong-slot-component": ("slots", "mcp:pstack:server", "component", "agents"),
        "slot-name-not-path": ("slots", "mcp:pstack:server", "slot", "../server"),
        "relative-document": ("slots", "mcp:pstack:server", "document", "config.json"),
        "parent-document": ("slots", "mcp:pstack:server", "document", "/a/../config.json"),
        "wrong-destination": ("entries", "hook:bundle", "destination", "/hooks/other"),
        "nested-destination": ("entries", "hook:bundle", "destination", "/hooks/nested/bundle"),
        "parent-source": ("entries", "hook:bundle", "source", "../hooks"),
        "normalized-source": ("entries", "hook:bundle", "source", "./plugins//hooks/"),
    }
    for label, (collection, key, field, value) in variants.items():
        changed = copy.deepcopy(state)
        changed[collection][key][field] = value
        cases[label] = changed
    for label, roots in (("missing-root", {"skills": "/skills"}),
                         ("wrong-skills-root", {"skills": "/other", "hooks": "/hooks"})):
        changed = copy.deepcopy(state)
        changed["destinationRoots"] = roots
        cases[label] = changed
    for version in (2, 3, 4):
        changed = copy.deepcopy(state)
        changed["schemaVersion"] = version
        changed["entries"] = {}
        if version < 4:
            del changed["slots"]
        if version == 2:
            del changed["destinationRoots"]
        cases[f"empty-v{version}"] = changed
    # Legacy entries and conditional evidence are part of the same wire contract.
    for version in (2, 3, 4):
        for component, prefix, kind in (("skills", "skill", "directory-symlink"),
                                         ("agents", "agent", "file-copy"),
                                         ("rules", "rule", "file-copy"),
                                         ("commands", "command", "file-copy"),
                                         ("hooks", "hook", "directory-copy")):
            changed = copy.deepcopy(state)
            changed["schemaVersion"] = version
            key = f"{prefix}:example"
            entry = {"key": key, "plugin": "pstack", "source": "./plugins//example/",
                     "destination": "/skills/example"}
            if version >= 3:
                entry.update(component=component, resourceKind=kind, sourceRepository="/repo")
                if kind != "directory-symlink":
                    entry["digest"] = "a" * 64
                if kind == "directory-copy":
                    entry["trust"] = "aw-trust-v1:" + "b" * 64
                changed["destinationRoots"][component] = "/skills"
            else:
                del changed["destinationRoots"]
            if version < 4:
                del changed["slots"]
            changed["entries"] = {key: entry}
            cases[f"v{version}-{component}"] = changed
            for field in ("digest", "trust", "trustProof"):
                extra = copy.deepcopy(changed)
                extra["entries"][key][field] = ("aw-proof-v1:" if field == "trustProof" else "aw-trust-v1:" if field == "trust" else "") + "a" * 64
                cases[f"v{version}-{component}-extra-{field}"] = extra
    for component in ("hooks", "mcp"):
        changed = copy.deepcopy(state)
        slot = changed["slots"].pop("mcp:pstack:server")
        key = f"{component}:pstack:../server"
        slot.update(key=key, component=component, slot="../server")
        changed["slots"][key] = slot
        cases[f"opaque-slot-{component}"] = changed
    for value in (True, 4.0, "4", -1, 5):
        changed = copy.deepcopy(state)
        changed["schemaVersion"] = value
        cases[f"version-{value!r}"] = changed
    # Cross-invalid rows pin first-error precedence, including insertion order.
    for label, edits in {
        "entry-before-metadata": [((), "repository", "relative"), (("entries", "hook:bundle"), "digest", "bad")],
        "entry-before-roots": [((), "destinationRoots", None), (("entries", "hook:bundle"), "plugin", "!")],
        "roots-before-slots": [((), "destinationRoots", {}), (("slots", "mcp:pstack:server"), "trustProof", "bad")],
        "destination-before-slots": [(("entries", "hook:bundle"), "destination", "/hooks/wrong"), (("slots", "mcp:pstack:server"), "trustProof", "bad")],
        "slots-before-metadata": [((), "repository", "relative"), (("slots", "mcp:pstack:server"), "trustProof", "bad")],
        "digest-before-entry-keys": [(("entries", "hook:bundle"), "digest", "bad"), (("entries", "hook:bundle"), "unknown", True)],
        "proof-before-slot-keys": [(("slots", "mcp:pstack:server"), "trustProof", "bad"), (("slots", "mcp:pstack:server"), "unknown", True)],
        "metadata-order": [((), "repository", "relative"), ((), "marketplace", "!")],
    }.items():
        changed = copy.deepcopy(state)
        for route, field, value in edits:
            cursor = changed
            for key in route:
                cursor = cursor[key]
            cursor[field] = value
        cases[f"order-{label}"] = changed
    for label, route in (("state", ()), ("entry", ("entries", "hook:bundle")),
                         ("slot", ("slots", "mcp:pstack:server"))):
        original = state
        for key in route:
            original = original[key]
        for field in original:
            for index, value in enumerate(("", "\x00", 0, 1.5, True, [], {})):
                changed = copy.deepcopy(state)
                cursor = changed
                for key in route:
                    cursor = cursor[key]
                cursor[field] = value
                cases[f"type-{label}-{field}-{index}"] = changed
    for version in (2, 3, 4):
        changed = copy.deepcopy(state)
        changed["schemaVersion"] = version
        changed["entries"] = copy.deepcopy(cases[f"v{version}-skills"])["entries"]
        if version < 4:
            changed.pop("slots")
        if version == 2:
            changed.pop("destinationRoots")
        changed["repository"] = "//repo//./"
        changed["destinationRoot"] = "/skills//./"
        changed["entries"]["skill:example"]["destination"] = "/skills//./example/"
        if version >= 3:
            changed["destinationRoots"]["skills"] = "/skills//./"
        cases[f"normalized-paths-v{version}"] = changed
    for collection in ("entries", "slots", "destinationRoots"):
        changed = copy.deepcopy(state)
        if collection == "entries":
            changed[collection] = {"hook:z": {"component": "invalid-first"}, "hook:a": {"component": "invalid-second"}}
        elif collection == "slots":
            changed[collection] = {"z": {"trustProof": "bad"}, "a": {}}
        else:
            changed[collection] = {"z": "relative", "a": "relative"}
        cases[f"order-insertion-{collection}"] = changed
    for version in (3, 4):
        changed = copy.deepcopy(cases[f"v{version}-skills"])
        changed["destinationRoots"]["agents"] = "/skills//./"
        entry = copy.deepcopy(changed["entries"]["skill:example"])
        entry.update(key="agent:example", component="agents", resourceKind="file-copy",
                     digest="a" * 64, destination="/skills//./example/")
        changed["entries"]["agent:example"] = entry
        cases[f"repeated-normalized-destination-v{version}"] = changed
    for label, field, value in (("quote", "component", "a'b"),
                                ("double-quote", "component", 'a"b'),
                                ("list", "component", [True, None, "x"]),
                                ("map", "component", {"z": False, "a": 0})):
        changed = copy.deepcopy(state)
        changed["entries"]["hook:bundle"][field] = value
        cases[f"repr-{label}"] = changed
    for label, route in (("state", ()), ("entry", ("entries", "hook:bundle")),
                         ("slot", ("slots", "mcp:pstack:server"))):
        changed = copy.deepcopy(state)
        cursor = changed
        for key in route:
            cursor = cursor[key]
        del cursor["key" if route else "repository"]
        del cursor["plugin" if route else "profile"]
        cursor["unknown"] = True
        cases[f"missing-before-unknown-{label}"] = changed
    cases["top-array"] = []
    cases["top-null"] = None
    expected = {}
    for label, value in cases.items():
        path = inputs / f"{label}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
    # Duplicate detection must operate before object decoding, at every depth.
    for label, token in (("top", '"schemaVersion": 4'), ("entry", '"digest": "' + "a" * 64 + '"'),
                         ("slot", '"slot": "server"')):
        text = json.dumps(state).replace(token, token + ", " + token, 1)
        (inputs / f"duplicate-{label}.json").write_text(text, encoding="utf-8")
    for label, text in {
        "quoted": "{\"a'b\": 1, \"a'b\": 2}",
        "escaped": '{"a\\u0000": 1, "a\\u0000": 2}',
        "nested-before-outer": '{"outer": 1, "outer": {"inner": 1, "inner": 2}}',
        "earlier-duplicate-later-child": '{"outer": 1, "outer": 2, "later": {"inner": 1, "inner": 2}}',
    }.items():
        (inputs / f"duplicate-{label}.json").write_text(text, encoding="utf-8")
    for path in sorted(inputs.iterdir()):
        try:
            loaded = load_state(path)
            assert loaded is not None
            expected[path.name] = {"accepted": True, "encoded": json.dumps(loaded.as_dict(), indent=2, ensure_ascii=False) + "\n"}
        except ValueError as error:
            expected[path.name] = {"accepted": False, "error_class": type(error).__name__, "error": str(error)}
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "state::parity_tests::differential_state_wire", "--", "--ignored", "--exact"],
                 {"AW_STATE_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "results.json").read_text())
    mismatches = {key: (value, actual.get(key)) for key, value in expected.items() if value != actual.get(key)}
    assert not mismatches, f"state wire mismatches: {mismatches!r}"
    print(f"state wire parity: {len(expected)} strict v2/v3/v4 cases match")


def state_v4_public_lifecycle(base: Path) -> None:
    for target in ('cursor', 'claude'):
        py, rs = pair(base, 'v4-public-lifecycle', target)
        for side in (py, rs):
            seed_v2(side)
            state = v2_state(side)
            state.update(schemaVersion=4, slots={}, destinationRoots={'skills': str(side.destination)})
            entries = state['entries']
            assert isinstance(entries, dict)
            entry = entries['skill:example']
            assert isinstance(entry, dict)
            entry.update(component='skills', resourceKind='directory-symlink', sourceRepository=str(side.root))
            side.state_file.write_text(json.dumps(state), encoding='utf-8')
        for action in ('plan', 'status', 'trust', 'install', 'uninstall'):
            before = [trust_snapshot(side.base) for side in (py, rs)]
            compare(f'v4 {target} {action}', py, rs, py.command(action), rs.command(action))
            if action in ('plan', 'status', 'trust'):
                assert before == [trust_snapshot(side.base) for side in (py, rs)]
            if py.state_file.exists():
                compare_state(f'v4 {target} {action}', py, rs)
            else:
                assert not rs.state_file.exists()
    print('state v4 public lifecycle: Cursor/Claude plan/status/trust/install/uninstall output, state bytes/modes and read-only inspection match')


def trust_contract_rows(base: Path) -> None:
    """D2 dependency-bound contract; activation planning remains gated on D6."""
    if str(REPOSITORY) not in sys.path:
        sys.path.insert(0, str(REPOSITORY))
    from agent_workbench.model import (ArtifactKind, ComponentKind, Plugin, Provenance,
                                      RepoPath, ResolvedPlugin, Source, SourceKind, PluginId, SourceId)
    from agent_workbench.trust import grant_token
    base.mkdir(parents=True)
    cases = []
    expected = []
    for component in ("hooks", "mcp"):
        for kind in ("local", "curated-copy", "git-submodule"):
            for variant in ("fallback", "override", "missing", "unicode", "changed", "null-source",
                            "source-id", "source-path", "source-upstream", "source-branch",
                            "plugin-upstream", "plugin-notice", "plugin-id", "controls", "path-document",
                            "path-repository", "path-fallback", "path-override", "target", "document", "repository"):
                unicode = variant == "unicode"
                source = dict(id="local", kind=kind, path="plugins",
                              upstream="https://source/é😀" if unicode else "https://source",
                              branch="main")
                provenance = dict(upstream="https://original/é😀", notice="NOTICE") if unicode else None
                plugin = dict(id="pstack", source="local", artifacts={})
                if provenance:
                    plugin["provenance"] = provenance
                fallback = None if variant == "missing" else str(base / "artifact")
                override = str(base / "alias") if variant == "override" else None
                digest = ("b" if variant == "changed" else "a") * 64
                document = str(base / ("é😀\t.json" if unicode else "document.json"))
                case = dict(source=source, plugin=plugin, component=component,
                            repository=str(base), document=document, digest=digest,
                            fallback=fallback, override=override, target="cursor")
                resolved = ResolvedPlugin(
                    Plugin(PluginId("pstack"), SourceId("local"), {}, Provenance(provenance["upstream"], RepoPath(provenance["notice"])) if provenance else None),
                    Source(SourceId("local"), SourceKind(kind), RepoPath(source["path"]), source["upstream"], source["branch"]),
                    {ArtifactKind(component): Path(fallback)} if fallback else {})
                # Independently perturb each bound identity, not only content.
                from dataclasses import replace
                if variant == "null-source":
                    case["source"] = {key: value for key, value in source.items() if key not in {"upstream", "branch"}}
                    resolved = replace(resolved, source=replace(resolved.source, upstream=None, branch=None))
                elif variant.startswith("source-"):
                    field = variant.removeprefix("source-")
                    source[field] = "other" if field != "upstream" else "https://other"
                    resolved = replace(resolved, source=replace(resolved.source, **{
                        field: SourceId(source[field]) if field == "id" else (RepoPath(source[field]) if field == "path" else source[field])}))
                elif variant == "plugin-id":
                    plugin["id"] = "other"
                    resolved = replace(resolved, plugin=replace(resolved.plugin, id=PluginId("other")))
                elif variant == "controls":
                    case["target"] = ''.join(chr(i) for i in range(32)) + '\x7f"\\/é😀'
                elif variant.startswith("path-"):
                    field = variant.removeprefix("path-")
                    # Pass lexical strings through pathlib on the Python side only.
                    case[field] = "//" + str(base).lstrip("/") + "//./nested///file/"
                    fallback = str(case["fallback"]) if case["fallback"] is not None else None
                    override = str(case["override"]) if case["override"] is not None else None
                    resolved = replace(resolved, artifact_paths={ArtifactKind(component): Path(fallback)} if fallback else {})
                elif variant.startswith("plugin-"):
                    notice = "OTHER-NOTICE" if variant == "plugin-notice" else None
                    plugin["provenance"] = dict(upstream="https://original", **({"notice": notice} if notice else {}))
                    resolved = replace(resolved, plugin=replace(resolved.plugin, provenance=Provenance("https://original", RepoPath(notice) if notice else None)))
                elif variant in ("target", "document", "repository"):
                    case[variant] = "claude" if variant == "target" else str(base / (variant + "-other"))
                cases.append(case)
                expected.append(grant_token(resolved, ComponentKind(component), str(case["target"]), Path(str(case["document"])), digest, Path(str(case["repository"])), Path(override) if override else None))
    (base / "tokens.json").write_text(json.dumps(cases))
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "trust::parity_tests::differential_trust", "--", "--ignored", "--exact"],
                 {"AW_TRUST_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "results.json").read_text())
    assert len(actual) == len(expected)
    mismatches = [(i, cases[i], e, a) for i, (e, a) in enumerate(zip(expected, actual)) if e != a]
    assert not mismatches, f"content-bound trust mismatches ({len(mismatches)}): {mismatches[:1]!r}"
    print(f"trust token parity: {len(expected)} dependency-bound cases match (D6 CLI not covered)")


def trust_proof_rows(base: Path) -> None:
    from dataclasses import replace
    from agent_workbench.model import ComponentKind as C, DirectoryCopy, FileCopy
    from agent_workbench.state import StateEntry, SlotState
    from agent_workbench.trust import authenticated, record_proof, reuse_proof
    base.mkdir(parents=True)
    rows, expected = [], []
    token = "aw-trust-v1:" + "a" * 64
    variants = ("valid", "missing-proof", "tampered", "transplanted", "missing-key",
                "public-key", "special-mode", "short-key", "long-key", "symlink-key",
                "hardlink-key", "directory-key", "fifo-key", "symlink-parent", "absent-parent",
                "changed-candidate", "already-proof", "wrong-proof",
                "changed-token", "explicit", "unrelated-explicit", "shared-slot", "other-plugin",
                "other-component", "bundle", "other-bundle", "no-prior", "changed-digest", "changed-with-proof")
    for record_type in ("entry", "slot", "hook-slot"):
        fields = (("key", "plugin", "source", "destination", "component", "resourceKind", "sourceRepository", "trust", "digest", "trustProof")
                  if record_type == "entry" else
                  ("key", "plugin", "component", "document", "slot", "digest", "trust", "trustProof"))
        for variant in (*variants, *("field-" + field for field in fields)):
            case = base / f"{record_type}-{variant}"
            case.mkdir()
            file = case / "state.json"
            key = case / ".state.json.transaction-key"
            key.write_bytes(bytes(range(32)))
            key.chmod(0o600)
            record = (StateEntry("hook:pstack", "pstack", "plugins/é😀", str(case / "hooks/pstack"), C.HOOKS,
                                 DirectoryCopy("b" * 64), str(base), token)
                      if record_type == "entry" else
                      SlotState(f"{'hooks' if record_type == 'hook-slot' else 'mcp'}:pstack:é😀", "pstack",
                                C.HOOKS if record_type == "hook-slot" else C.MCP, str(case / "doc"), "é😀", "b" * 64, token))
            signed = replace(record, trust_proof=record_proof(record, file))
            prior = signed
            bundle = None
            candidate = record
            if variant == "missing-proof":
                prior = record
            elif variant == "tampered":
                prior = replace(signed, digest="c" * 64) if record_type != "entry" else replace(signed, source="other")
            elif variant == "transplanted":
                file = case / "other.json"
                (case / ".other.json.transaction-key").write_bytes(bytes(range(32)))
                (case / ".other.json.transaction-key").chmod(0o600)
            elif variant == "missing-key":
                key.unlink()
            elif variant in ("public-key", "special-mode"):
                key.chmod(0o644 if variant == "public-key" else 0o4600)
            elif variant in ("short-key", "long-key"):
                key.write_bytes(b"x" * (31 if variant == "short-key" else 33))
            elif variant == "symlink-key":
                key.rename(case / "real-key")
                key.symlink_to("real-key")
            elif variant == "hardlink-key":
                os.link(key, case / "another-key")
            elif variant == "directory-key":
                key.unlink()
                key.mkdir(mode=0o600)
            elif variant == "fifo-key":
                key.unlink()
                os.mkfifo(key, 0o600)
            elif variant == "symlink-parent":
                (case / "alias").symlink_to(case, target_is_directory=True)
                file = case / "alias/state.json"
            elif variant == "absent-parent":
                file = case / "absent/state.json"
            elif variant == "changed-candidate":
                candidate = replace(record, document=str(case / "other-doc")) if record_type != "entry" else replace(record, source="other")
            elif variant == "already-proof":
                candidate = replace(record, trust_proof="aw-proof-v1:" + "d" * 64)
            elif variant == "wrong-proof":
                prior = replace(signed, trust_proof="aw-proof-v1:" + "d" * 64)
            elif variant == "changed-token":
                candidate = replace(record, trust="aw-trust-v1:" + "c" * 64)
            if variant in ("explicit", "unrelated-explicit", "no-prior"):
                prior = None
            elif variant in ("changed-digest", "changed-with-proof"):
                candidate = replace(record, resource=DirectoryCopy("c" * 64)) if record_type == "entry" else replace(record, digest="c" * 64)
                if variant == "changed-with-proof":
                    candidate = replace(candidate, trust_proof="aw-proof-v1:" + "d" * 64)
            elif variant in ("shared-slot", "other-plugin", "other-component") and record_type != "entry":
                if variant == "shared-slot":
                    prior = replace(record, key=f"{record.component.value}:pstack:second", slot="second")
                elif variant == "other-plugin":
                    prior = replace(record, key=f"{record.component.value}:other:é😀", plugin="other")
                else:
                    component = C.MCP if record.component is C.HOOKS else C.HOOKS
                    prior = replace(record, key=f"{component.value}:pstack:é😀", component=component)
                prior = replace(prior, trust_proof=record_proof(prior, file))
            elif variant in ("bundle", "other-bundle") and record_type != "entry":
                plugin_id = "other" if variant == "other-bundle" else "pstack"
                bundle = StateEntry(f"hook:{plugin_id}", plugin_id, "plugins/hooks", str(case / f"hooks/{plugin_id}"),
                                    C.HOOKS, DirectoryCopy("b" * 64), str(base), token)
                bundle = replace(bundle, trust_proof=record_proof(bundle, file))
                prior = None
            mutation = None
            saved_prior = prior
            if variant.startswith("field-"):
                field = variant.removeprefix("field-")
                attr = {"resourceKind": "resource", "sourceRepository": "source_repository", "trustProof": "trust_proof"}.get(field, field)
                value = ((C.MCP if record.component is C.HOOKS else C.HOOKS) if field == "component" else
                         FileCopy("b" * 64) if field == "resourceKind" else
                         DirectoryCopy("c" * 64) if field == "digest" and record_type == "entry" else
                         "c" * 64 if field == "digest" else
                         "aw-trust-v1:" + "c" * 64 if field == "trust" else
                         "aw-proof-v1:" + "c" * 64 if field == "trustProof" else
                         "other" if field in ("plugin", "source", "slot", "key") else str(case / "other"))
                if field == "digest" and record_type == "entry":
                    attr = "resource"
                prior = replace(signed, **{attr: value})
                mutation = dict(field=field, value=prior.as_dict()[field])
                assert not authenticated(prior, file), (record_type, field)
            explicit = [candidate.trust] if variant == "explicit" else (["unrelated"] if variant == "unrelated-explicit" else [])
            try:
                proof = record_proof(candidate, file)
            except (OSError, ValueError):
                proof = None
            recorded = prior is not None and prior.trust == candidate.trust and authenticated(prior, file)
            if record_type != "entry":
                recorded = recorded and prior is not None and prior.plugin == candidate.plugin and prior.component is candidate.component
                recorded = recorded or (candidate.component is C.HOOKS and bundle is not None and
                           bundle.key == f"hook:{candidate.plugin}" and bundle.trust == candidate.trust and authenticated(bundle, file))
            granted = candidate.trust in explicit or recorded
            expected.append(dict(proof=proof, authenticated=authenticated(prior, file),
                                 reused=(reuse_proof(candidate, prior if isinstance(prior, StateEntry) else None, file).as_dict()
                                         if isinstance(candidate, StateEntry) else
                                         reuse_proof(candidate, prior if isinstance(prior, SlotState) else None, file).as_dict()), granted=granted))
            # The driver loads actual state through the strict D1 decoder.
            def state_for(item):
                return dict(schemaVersion=4, repository=str(base), marketplace="market", profile="pstack", target="cursor",
                            destinationRoot=str(case), destinationRoots={"skills": str(case), "hooks": str(case / "hooks"), "mcp": str(case)},
                            entries=({item.key: item.as_dict()} if item is not None and record_type == "entry" else
                                     ({bundle.key: bundle.as_dict()} if item is None and bundle is not None else {})),
                            slots={item.key: item.as_dict()} if item is not None and record_type != "entry" else {})
            for name, item in (("prior", saved_prior), ("candidate", candidate)):
                (case / f"{name}.json").write_text(json.dumps(state_for(item)))
            rows.append(dict(file=str(file), prior=str(case / "prior.json"), candidate=str(case / "candidate.json"),
                             explicit=explicit, mutation=mutation))
    (base / "proofs.json").write_text(json.dumps(rows))
    before = trust_snapshot(base)
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "trust::parity_tests::differential_proofs", "--", "--ignored", "--exact"], {"AW_TRUST_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "proof-results.json").read_text())
    mismatches = [(rows[i], e, a) for i, (e, a) in enumerate(zip(expected, actual)) if e != a]
    assert not mismatches, f"persisted trust mismatches ({len(mismatches)}): {mismatches[:1]!r}"
    assert len(actual) == len(expected)
    assert trust_snapshot(base) == before, "trust verification mutated fixtures or created authority"
    print(f"persisted trust parity: {len(expected)} authentication/reuse/key-safety cases match")


def trust_inspection_rows(base: Path) -> None:
    from agent_workbench.installation import _recover_lifecycle
    from agent_workbench.model import ComponentKind as C
    from agent_workbench.adapters.activation import ACTIVATION_POLICIES
    base.mkdir(parents=True)
    rows, expected = [], []
    for target in ("cursor", "claude"):
        for pending in (False, True):
            case = base / f"{target}-{pending}"
            case.mkdir()
            journal = case / ACTIVATION_POLICIES[target].journal_name
            if pending:
                journal.write_text('{}')  # Even an unauthenticated journal blocks inspection.
            try:
                _recover_lifecycle(case / "state.json", {C.HOOKS: case / "hooks"}, repair=False, target_id=target)
                expected.append(None)
            except ValueError as error:
                expected.append(str(error))
            rows.append(str(journal))
    (base / "inspection.json").write_text(json.dumps(rows))
    before = trust_snapshot(base)
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "trust::parity_tests::differential_inspection", "--", "--ignored", "--exact"], {"AW_TRUST_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "inspection-results.json").read_text())
    assert actual == expected, f"read-only inspection mismatch: {actual!r} != {expected!r}"
    assert trust_snapshot(base) == before, "inspection mutated journal/state or created authority"
    print(f"trust inspection parity: {len(rows)} dependency-bound pending-journal cases match without writes")


def trust_cli_contract(base: Path) -> None:
    side = Side("rust", base / "trust-cli-rejection", "codex")
    rejection_before = trust_snapshot(side.base)
    token = "aw-trust-v1:" + "a" * 64
    # "trust" is read-only planning on non-executable resources; grants are
    # accepted, repeatable, and irrelevant to these resources in Python.
    for target in TARGETS:
        py = Side("python", base / f"trust-cli-{target}-python", target)
        rs = Side("rust", base / f"trust-cli-{target}-rust", target)
        for phase in ("clean", "installed", "conflict"):
            if phase == "installed":
                compare("trust seed", py, rs, py.command("install"), rs.command("install"))
            elif phase == "conflict":
                for side in (py, rs):
                    (side.destination / "example").unlink()
                    (side.destination / "example").mkdir()
            for action in ("trust", "plan", "status", "install"):
                before = [trust_snapshot(side.base) for side in (py, rs)]
                args = ("--component", "skills", "--trust", token, "--trust", "unrelated")
                compare(f"{phase} {target} {action} --trust", py, rs, py.command(action, *args), rs.command(action, *args))
                if action != "install":
                    assert [trust_snapshot(side.base) for side in (py, rs)] == before
                def normalized_snapshot(side):
                    return {name: (mode, uid, links, side.normalize(content.decode()) if isinstance(content, bytes) else side.normalize(content) if isinstance(content, str) else content)
                            for name, (mode, uid, links, content) in trust_snapshot(side.base).items()}
                assert normalized_snapshot(py) == normalized_snapshot(rs), (target, phase, action)
    for target in ("cursor", "claude"):
        py = Side("python", base / f"trust-pending-{target}-python", target)
        rs = Side("rust", base / f"trust-pending-{target}-rust", target)
        for side in (py, rs):
            journal = side.base / "home" / f".{target}" / ".agent-workbench-transaction.json"
            journal.parent.mkdir(parents=True)
            journal.write_text('{}')
        snapshots = [trust_snapshot(side.base) for side in (py, rs)]
        compare("trust pending journal", py, rs, py.command("trust"), rs.command("trust"))
        assert [trust_snapshot(side.base) for side in (py, rs)] == snapshots
    side = Side("rust", base / "trust-cli-rejection", "codex", initialize=False)
    for action, args in (("trust", ("--component", "hooks")),
                         ("trust", ("--component", "mcp", "--trust", token)),
                         ("install", ("--component", "hooks")),
                         ("install", ("--component", "hooks", "--trust", token)),
                         ("install", ("--component", "mcp", "--trust", token))):
        result = side.command(action, *args)
        assert result.returncode == 1 and 'COMPONENT_UNSUPPORTED' in result.stdout, result
        assert trust_snapshot(side.base) == rejection_before, "unsupported activation mutated fixtures"
    print("trust CLI: six-target skills exits/output/typed snapshots match; unsupported Codex activation remains fail-closed")


def trust_snapshot(base: Path):
    """Never follow fixture symlinks or open FIFOs; ignore only driver outputs."""
    result = {}
    for parent, directories, files in os.walk(base, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if path.name in {"proof-results.json", "inspection-results.json"}:
                continue
            info = path.lstat()
            content = path.read_bytes() if stat.S_ISREG(info.st_mode) else (os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
            result[str(path.relative_to(base))] = (info.st_mode, info.st_uid, info.st_nlink, content)
    return result


def bundle_rows(base: Path) -> None:
    """D3 inert snapshot dependency; no activation lifecycle is exercised."""
    if str(REPOSITORY) not in sys.path:
        sys.path.insert(0, str(REPOSITORY))
    from agent_workbench import bundles
    base.mkdir(parents=True)
    rows, expected = [], []
    for mask in (0o022, 0o077):
        case = base / f"snapshot-{mask:o}"
        case.mkdir()
        source = case / "source"
        source.mkdir()
        source.chmod(0o751)
        (source / "empty").mkdir()
        (source / "empty").chmod(0o711)
        for name, content, mode in (("run", b"#!/bin/sh\n\x00\xff", 0o751), ("é😀", b"raw\r\n", 0o640),
                                    ("\ue000", b"unicode sorting", 0o644), ("\n\x7f", b"controls", 0o640)):
            (source / name).write_bytes(content)
            (source / name).chmod(mode)
        tree = bundles.source_tree(source)
        rows.append(dict(source=str(source), umask=mask))
        expected.append(tree.digest)
    (base / "rows.json").write_text(json.dumps(rows))
    before = trust_snapshot(base)
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "bundle_parity_tests::differential_bundles", "--", "--ignored", "--exact"],
                 {"AW_BUNDLE_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "results.json").read_text())
    assert actual == expected, f"bundle snapshot mismatch: {actual!r} != {expected!r}"
    after = trust_snapshot(base)
    after.pop("results.json", None)
    assert after == before, "snapshot mutated fixture"
    print(f"bundle parity: {len(rows)} inert snapshot rows match (D5 lifecycle not covered)")


def bundle_snapshot(base: Path):
    """Typed lstat snapshot: presence, kind, exact mode/bytes, lexical links."""
    result: dict[str, tuple[str, int, bytes | str | None]] = {".": ("directory", stat.S_IMODE(base.lstat().st_mode), None)}
    for parent, directories, files in os.walk(base, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            info = path.lstat()
            kind = ("file" if stat.S_ISREG(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode)
                    else "symlink" if stat.S_ISLNK(info.st_mode) else "fifo" if stat.S_ISFIFO(info.st_mode) else "special")
            data = path.read_bytes() if kind == "file" else os.readlink(path) if kind == "symlink" else None
            result[str(path.relative_to(base))] = (kind, stat.S_IMODE(info.st_mode), data)
    return result


def bundle_publication_rows(base: Path) -> None:
    from types import SimpleNamespace
    from typing import cast
    from agent_workbench import bundles
    from agent_workbench.installation import (DestinationSnapshot, InstallOperation, InstallPlan, OperationKind,
                                              _DestinationRoot, _validate_install_source)
    from agent_workbench.model import ComponentKind, DirectoryCopy
    base.mkdir(parents=True)
    rows, expected, pairs = [], [], []
    for mask in (0o022, 0o077):
        for scenario in ("create", "keep", "conflict", "conflict-symlink", "cleanup", "mode-change", "byte-change",
                         "source-change", "source-mode", "symlink", "fifo", "special-file", "special-directory", "ancestor-symlink",
                         "detached", "during-read", "snapshot"):
            roots = [base / f"{mask:o}-{scenario}-{side}" for side in ("python", "rust")]
            for case in roots:
                case.mkdir()
                (case / "source").mkdir()
                (case / "source").chmod(0o751)
                (case / "source/nested").mkdir()
                (case / "source/nested").chmod(0o755)
                (case / "source/locked").mkdir()
                (case / "source/locked").chmod(0o555)
                (case / "source/nested/run").write_bytes(b"#!/bin/sh\n\x00\xff")
                (case / "source/nested/run").chmod(0o751)
                fixture_mask = os.umask(mask)
                try:
                    (case / "source/default-mode").write_bytes(b"umask-controlled source")
                finally:
                    os.umask(fixture_mask)
                (case / "source/é😀").write_bytes(b"\x00raw\xff\r\n")
                (case / "source/é😀").chmod(0o640)
                (case / "destination").mkdir()
                (case / "outside").mkdir()
                (case / "outside/sentinel").write_bytes(b"untouched")
            case = roots[0]
            source, destination = case / "source", case / "destination"
            planned = bundles.source_tree(source).digest
            # These are deterministic plan/apply changes, never timing races.
            for current in roots:
                path = current / "source/nested/run"
                if scenario == "source-change":
                    path.write_bytes(b"changed after plan")
                elif scenario == "source-mode":
                    path.chmod(0o750)
                elif scenario in ("symlink", "fifo"):
                    path.unlink()
                    if scenario == "symlink":
                        path.symlink_to("../../outside/sentinel")
                    else:
                        os.mkfifo(path, 0o600)
                elif scenario == "special-file":
                    path.chmod(0o4751)
                elif scenario == "special-directory":
                    (current / "source/nested").chmod(0o1755)
                elif scenario == "ancestor-symlink":
                    (current / "alias").symlink_to("source", target_is_directory=True)
                if scenario == "conflict":
                    (current / "destination/bundle").mkdir()
                    (current / "destination/bundle/foreign").write_bytes(b"foreign")
                elif scenario == "conflict-symlink":
                    (current / "destination/bundle").symlink_to("../outside", target_is_directory=True)
            if scenario == "ancestor-symlink":
                source = case / "alias/nested"
            operation = InstallOperation(OperationKind.CREATE, "hook:pstack", destination / "bundle", source, "test",
                                         component=ComponentKind.HOOKS, resource=DirectoryCopy(planned), expected_digest=planned)
            info = destination.stat()
            snapshot = DestinationSnapshot(destination, (info.st_dev, info.st_ino), destination, (info.st_dev, info.st_ino))
            old_mask = os.umask(mask)
            outcome = {"accepted": False, "same": None}
            try:
                with _DestinationRoot(snapshot, create=False) as root:
                    if scenario == "during-read":
                        from unittest.mock import patch
                        original_fstat = os.fstat
                        file_stats = 0
                        target_inode = (source / "nested/run").stat().st_ino
                        def changing_fstat(fd):
                            nonlocal file_stats
                            info = original_fstat(fd)
                            if stat.S_ISREG(info.st_mode) and info.st_ino == target_inode:
                                file_stats += 1
                                if file_stats == 2:
                                    (source / "nested/run").write_bytes(b"changed while reading")
                                    return original_fstat(fd)
                            return info
                        with patch.object(bundles.os, "fstat", changing_fstat):
                            tree = _validate_install_source(cast(InstallPlan, SimpleNamespace(repository=case)), operation)
                    else:
                        tree = _validate_install_source(cast(InstallPlan, SimpleNamespace(repository=case)), operation)
                    if scenario == "snapshot":
                        (source / "nested/run").write_bytes(b"changed after snapshot")
                    if scenario == "detached":
                        destination.rename(case / "detached")
                        destination.symlink_to("outside", target_is_directory=True)
                    root.create(operation, tree)
                    outcome["accepted"] = True
                    if scenario == "mode-change":
                        (destination / "bundle/hooks/nested/run").chmod(0o750)
                    elif scenario == "byte-change":
                        (destination / "bundle/hooks/nested/run").write_bytes(b"foreign edit")
                    outcome["same"] = root.same_resource(operation)
                    if scenario == "keep":
                        assert _validate_install_source(cast(InstallPlan, SimpleNamespace(repository=case)), operation) == tree
                    if scenario in ("cleanup", "detached"):
                        if scenario == "detached":
                            try:
                                root.verify()
                            except FileExistsError:
                                outcome["accepted"] = False
                            else:
                                raise AssertionError("detached root accepted")
                        moved = root.quarantine(operation)
                        root.discard(moved)
            except (OSError, ValueError) as error:
                if scenario == "during-read":
                    assert str(error) == "bundle changed while reading", str(error)
            finally:
                os.umask(old_mask)
            rejected = {"conflict", "conflict-symlink", "source-change", "source-mode", "symlink", "fifo",
                        "special-file", "special-directory", "ancestor-symlink", "during-read", "detached"}
            required = {"accepted": scenario not in rejected,
                        "same": (True if scenario == "detached" else None) if scenario in rejected else scenario not in {"mode-change", "byte-change"}}
            assert outcome == required, f"Python bundle oracle {scenario}: {outcome!r} != {required!r}"
            rows.append(dict(base=str(roots[1]), scenario=scenario, umask=mask, digest=planned))
            expected.append(outcome)
            pairs.append(roots)
    (base / "publication.json").write_text(json.dumps(rows))
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "bundle_parity_tests::differential_publication", "--", "--ignored", "--exact"],
                 {"AW_BUNDLE_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "publication-results.json").read_text())
    assert len(actual) == len(expected)
    for row, py, rs, (python, rust) in zip(rows, expected, actual, pairs):
        assert py == rs, f"bundle {row} outcome mismatch: {py!r} != {rs!r}"
        assert bundle_snapshot(python) == bundle_snapshot(rust), f"bundle {row} typed filesystem mismatch: {bundle_snapshot(python)!r} != {bundle_snapshot(rust)!r}"
    print(f"bundle publication parity: {len(rows)} source/destination/outside/detached byte/mode/type rows match (isolated D3, not D5)")


def bundle_name_rows(base: Path) -> None:
    """Synthetic inert rows cover POSIX surrogateescape even on APFS."""
    from agent_workbench.bundles import Tree, TreeFile
    base.mkdir(parents=True)
    rows, expected = [], []
    for names in ((b"bad\xff",), (b"\x80", "\ue000".encode(), "😀".encode()),
                  (b"\xc3\x28", b"\xed\xa0\x80", b"\xf0\x90\x80", b"a\n\x7f")):
        rows.append([list(name) for name in names])
        expected.append(Tree((("", 0o755),), tuple(TreeFile(os.fsdecode(name), 0o751, b"raw\x00\xff") for name in names)).digest)
    (base / "names.json").write_text(json.dumps(rows))
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib",
                  "bundle_parity_tests::differential_names", "--", "--ignored", "--exact"],
                 {"AW_BUNDLE_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "name-results.json").read_text())
    assert actual == expected, f"POSIX name digest mismatch: {actual!r} != {expected!r}"
    print(f"bundle names: {len(rows)} Unicode/surrogateescape digest rows match")


def shared_document_rows(base: Path) -> None:
    """D4 pure planner oracle; exact bytes, operations and next slot state."""
    from agent_workbench import shared_config as shared
    from agent_workbench.model import ComponentKind as C
    from agent_workbench.state import SlotState
    from typing import Any
    def slot(data):
        return SlotState(data['key'], data['plugin'], C(data['component']), data['document'], data['slot'], data['digest'], data['trust'], data.get('trustProof'))
    base.mkdir(parents=True)
    value = {"command": "server", "args": [], "env": {"Z": "é", "A": "x"}}
    state = SlotState("mcp:pstack:server", "pstack", C.MCP, "/config/mcp.json", "server", shared.digest(value), "aw-trust-v1:" + "a" * 64)
    rows: list[dict[str, Any]] = [dict(component="mcp", target="cursor", path=state.document,
                 content=' {"foreign": {"z": 1, "a": 2}, "mcpServers": {"unmanaged": {"command": "foreign"}}} \n',
                 desired=[dict(state=state.as_dict(), value=value, granted=True)], owned={}, adopt=False)]
    import copy
    for phase in ('empty', 'matching', 'foreign', 'owned', 'drift', 'missing-owned', 'uninstall', 'uninstall-drift', 'duplicate', 'untrusted', 'replace'):
        for adopt in (False, True):
            row = copy.deepcopy(rows[0])
            row['adopt'] = adopt
            if phase == 'empty': row['content'] = None
            else:
                current = {} if phase == 'missing-owned' else {'server': value if phase not in ('foreign', 'drift', 'uninstall-drift') else {'command': 'foreign'}}
                row['content'] = ' \t' + json.dumps({'z': {'b': 2, 'a': 1}, 'mcpServers': current, 'a': []}, ensure_ascii=False) + '\n\n'
            if phase in ('owned', 'drift', 'missing-owned', 'uninstall', 'uninstall-drift', 'replace'):
                row['owned'] = {state.key: state.as_dict()}
            if phase.startswith('uninstall'): row['desired'] = []
            if phase == 'duplicate': row['desired'] *= 2
            if phase == 'untrusted': row['desired'][0]['granted'] = False
            if phase == 'replace':
                changed = {'args': ['changed'], 'command': 'new'}
                row['desired'][0]['value'] = changed
                row['desired'][0]['state']['digest'] = shared.digest(changed)
            rows.append(row)
    for target in ('cursor', 'claude'):
        hook = {'command': 'sh /config/managed/pstack/hooks/run', 'timeout': 3}
        if target == 'claude': hook = {'matcher': 'Bash', 'hooks': [{'type': 'command', **hook}, {'type': 'prompt', 'prompt': 'leave native'}]}
        for phase in ('create', 'matching', 'owned', 'drift', 'missing', 'uninstall', 'ambiguous', 'duplicate', 'untrusted', 'replace'):
            row = dict(component='hooks', target=target, path='/config/settings.json', desired=[], owned={}, adopt=False)
            hs = SlotState('hooks:pstack:Stop:' + shared.digest(hook), 'pstack', C.HOOKS, row['path'], 'Stop:' + shared.digest(hook), shared.digest(hook), state.trust)
            row['desired'] = [dict(state=hs.as_dict(), value=hook, granted=phase != 'untrusted')]
            foreign = {'command': 'foreign'} if target == 'cursor' else {'hooks': [{'type': 'prompt', 'prompt': 'foreign'}]}
            entries = [] if phase in ('create', 'missing') else [hook]
            if phase == 'ambiguous': entries = [hook, hook]
            if phase == 'drift': entries = [foreign]
            row['content'] = ' ' + json.dumps({'z': {}, 'hooks': {'Stop': [foreign, *entries], 'Other': []}, 'a': 1}) + '\n'
            if phase in ('owned', 'drift', 'missing', 'uninstall', 'replace'): row['owned'] = {hs.key: hs.as_dict()}
            if phase == 'uninstall': row['desired'] = []
            if phase == 'duplicate': row['desired'] *= 2
            if phase == 'replace':
                row['desired'][0]['value'] = foreign
                row['desired'][0]['state']['digest'] = shared.digest(foreign)
            rows.append(row)
            adopted = copy.deepcopy(row)
            adopted['adopt'] = True
            rows.append(adopted)
    for component, target, contents in (
        ('mcp', 'cursor', ['{"mcpServers":null}', '{"mcpServers":{"a":[]}}']),
        ('hooks', 'cursor', ['{"hooks":[]}', '{"hooks":{"Stop":[1]}}', '{"version":true}', '{"version":1.0}', '{"version":2}']),
        ('hooks', 'claude', ['{"hooks":{"Stop":[{"command":"x"}]}}', '{"hooks":{"Stop":[{"hooks":null}]}}', '{"hooks":{"Stop":[{"hooks":[{}]}]}}', '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":""}]}]}}', '{"hooks":{"Stop":[{"hooks":[],"matcher":3}]}}', '{"hooks":{"Stop":[{}]}}', '{"hooks":{"":[]}}']),
    ):
        for content in contents:
            row = copy.deepcopy(rows[0])
            row.update(component=component, target=target, content=content)
            rows.append(row)
    for content in (None, ' {"z":1,"a":2} \n', '{"hooks":null}'):
        row = copy.deepcopy(rows[0]); row.update(content=content, desired=[], owned={}); rows.append(row)
    for number in (1e-7, 1e20, 1.0, -0.0, 1e-5, 1e16, 1e15, 1.2345678901234567, 10**40 + 1, -(10**40 + 1)):
        for rewrite in (False, True):
            row = copy.deepcopy(rows[0])
            row['content'] = ' \t' + json.dumps({'z': number, 'mcpServers': {}, 'a': {'b': number, 'a': 'é'}}) + '\n\n'
            if not rewrite:
                row.update(desired=[], owned={})
            rows.append(row)
    # Trust rejection overrides collision, ambiguity, matching/adoption and keep.
    for original in list(rows):
        if original['desired']:
            row = copy.deepcopy(original)
            for item in row['desired']: item['granted'] = False
            rows.append(row)
    # Owned removals/conflicts sort by key, not source mapping insertion order.
    for drift in (False, True):
        row = copy.deepcopy(rows[0])
        second = copy.deepcopy(state.as_dict())
        second.update(key='mcp:pstack:aaa', slot='aaa')
        row.update(owned={state.key: state.as_dict(), second['key']: second}, desired=[])
        row['content'] = json.dumps({'mcpServers': {'server': value, 'aaa': {'command': 'drift'} if drift else value}, 'z': 1e-7})
        rows.append(row)
    for old_number, new_number in (
        (True, 1), (1, 1.0), (0.0, -0.0),
        (10**40, 10**40 + 1), (-(10**40), -(10**40 + 1)),
        (2**200, float(2**200)), (float(2**200), 2**200),
        (2**200 + 1, float(2**200)), (float(2**200), 2**200 + 1),
        (-(2**200), -float(2**200)), (-(2**200 + 1), -float(2**200)),
        (10**400, 10**400 + 1), (True, 10**400),
    ):
        row = copy.deepcopy(rows[0])
        old_value = {'command': 'server', 'number': old_number}
        new_value = {'command': 'server', 'number': new_number}
        old_state = copy.deepcopy(state.as_dict()); old_state['digest'] = shared.digest(old_value)
        new_state = copy.deepcopy(state.as_dict()); new_state['digest'] = shared.digest(new_value)
        row.update(content=' ' + json.dumps({'mcpServers': {'server': old_value}}) + '\n',
                   owned={state.key: old_state}, desired=[dict(state=new_state, value=new_value, granted=True)])
        rows.append(row)
    for encoding in ('utf-8-sig', 'utf-16', 'utf-16-be', 'utf-32', 'utf-32-be'):
        for rewrite in (False, True):
            row = copy.deepcopy(rows[0])
            row['content_bytes'] = list(' {"z":1e-7,"mcpServers":{},"a":1e20}\n'.encode(encoding))
            row['content'] = None
            if not rewrite: row.update(desired=[], owned={})
            rows.append(row)
    expected = []
    for row in rows:
        desired = tuple(shared.DesiredSlot(slot(item["state"]), item["value"], Path("/source"), "b" * 64, item["granted"]) for item in row["desired"])
        owned = {key: slot(item) for key, item in row["owned"].items()}
        content = bytes(row['content_bytes']) if 'content_bytes' in row else row['content'].encode() if row['content'] is not None else None
        before = shared.DocumentSnapshot(Path(row["path"]), content, None)
        try:
            plan, slots = shared.plan_document(before, C(row["component"]), desired, owned, row["adopt"], row["target"])
        except ValueError as error:
            expected.append({'error': str(error)})
            continue
        expected.append(dict(removed=list(plan.removed) if plan.removed is not None else None,
                             after=list(plan.after) if plan.after is not None else None,
                             operations=[item.as_dict() for item in plan.operations],
                             operations_json=json.dumps([item.as_dict() for item in plan.operations], ensure_ascii=False, separators=(',', ':')),
                             slots={key: item.as_dict() for key, item in slots.items()}))
    import random, struct, math
    rng = random.Random(42)
    numbers = [1e-7, 1e20, 1.0, -0.0, 1e-5, 1e16, 1e15, 1.2345678901234567,
               10**40 + 1, -(10**40 + 1), 2**200, 10**400, -(10**400)]
    for _ in range(256):
        number = struct.unpack('!d', rng.randbytes(8))[0]
        if math.isfinite(number): numbers.append(number)
    for number in numbers:
        value = {'z': number, 'a': ['é', number]}
        rows.append(dict(mode='encode', value=value))
        expected.append(dict(encoded=shared.encode(value).decode(), digest=shared.digest(value)))
    (base / "rows.json").write_text(json.dumps(rows))
    result = run(["cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--lib", "shared_document_parity_tests::differential_shared_document", "--", "--ignored", "--exact"], {"AW_SHARED_BASE": str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / "results.json").read_text())
    mismatches = [(i, e, a) for i, (e, a) in enumerate(zip(expected, actual)) if e != a]
    assert len(actual) == len(expected)
    assert not mismatches, f"shared document mismatches ({len(mismatches)}): {mismatches[:1]!r}"
    print(f"shared document parity: {len(rows)} exact plan rows match (private D4 driver)")


def shared_ownership_rows(base: Path) -> None:
    from agent_workbench import shared_config as shared
    from agent_workbench.state import SlotState
    from agent_workbench.model import ComponentKind as C
    from agent_workbench.installation import OperationKind as Op
    base.mkdir(parents=True)
    rows, expected = [], []
    item = SlotState('mcp:p:server', 'p', C.MCP, '/config/mcp.json', 'server', 'b' * 64, 'aw-trust-v1:' + 'a' * 64)
    other = SlotState('mcp:p:other', 'p', C.MCP, '/config/mcp.json', 'other', 'c' * 64, item.trust)
    for scenario in ('new', 'same', 'other-profile', 'remove', 'missing-authority', 'wrong-digest', 'retain-other', 'multiple-owned', 'multiple-conflicts', 'partial-rejection', 'float-version'):
        records = {}
        if scenario not in ('new', 'missing-authority'):
            records[shared.slot_identity(item)] = {'owner': '/other.json' if scenario == 'other-profile' else '/owner.json', 'digest': 'd' * 64 if scenario == 'wrong-digest' else item.digest}
        if scenario == 'retain-other': records[shared.slot_identity(other)] = {'owner': '/other.json', 'digest': other.digest}
        content = None if scenario == 'new' else ' ' + json.dumps({'version': 1, 'slots': records}) + '\n'
        previous = {item.key: item} if scenario in ('same', 'remove', 'missing-authority', 'wrong-digest') else {}
        desired = {} if scenario == 'remove' else {item.key: item}
        operation = shared.SlotOperation(Op.ADOPT, item.key, Path(item.document), item.component, item.slot, 'adopt matching unmanaged slot', None, item.trust)
        operations = (operation,)
        if scenario in ('multiple-owned', 'multiple-conflicts', 'partial-rejection'):
            previous = {other.key: other, item.key: item} if scenario != 'partial-rejection' else {}
            desired = {item.key: item, other.key: other}
            records = {shared.slot_identity(other): {'owner': '/other.json' if scenario == 'partial-rejection' else '/owner.json', 'digest': other.digest},
                       shared.slot_identity(item): {'owner': '/owner.json', 'digest': item.digest}}
            if scenario == 'multiple-conflicts': records = {}
            content = ' ' + json.dumps({'slots': records, 'version': 1}) + '\n'
            operations += (shared.SlotOperation(Op.CONFLICT, other.key, Path(other.document), other.component, other.slot, 'trust required', 'TRUST_REQUIRED', other.trust, True),)
        if scenario == 'float-version':
            content = ' {"slots": {}, "version": 1.0}\n'
        before = shared.DocumentSnapshot(Path('/ledger.json'), content.encode() if content else None, None)
        document = shared.DocumentPlan(shared.DocumentSnapshot(Path(item.document), None, None), None, None, operations)
        documents, ledger = shared.plan_ownership(Path('/owner.json'), (document,), previous, desired, before)
        expected.append(dict(removed=list(ledger.removed) if ledger.removed is not None else None, after=list(ledger.after) if ledger.after is not None else None,
                             operations=[o.as_dict() for o in documents[0].operations], conflicts=[o.as_dict() for o in shared.ownership_conflicts(before, Path('/owner.json'), previous)],
                             operations_json=json.dumps([o.as_dict() for o in documents[0].operations], ensure_ascii=False, separators=(',', ':')),
                             conflicts_json=json.dumps([o.as_dict() for o in shared.ownership_conflicts(before, Path('/owner.json'), previous)], ensure_ascii=False, separators=(',', ':'))))
        rows.append(dict(mode='ownership', content=content, file='/owner.json', previous=[s.as_dict() for s in previous.values()], desired=[s.as_dict() for s in desired.values()], operations=[o.as_dict() for o in operations]))
    (base / 'rows.json').write_text(json.dumps(rows))
    result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'shared_document_parity_tests::differential_shared_document', '--', '--ignored', '--exact'], {'AW_SHARED_BASE': str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / 'results.json').read_text())
    assert actual == expected, f'ownership mismatch: {[(i,e,a) for i,(e,a) in enumerate(zip(expected,actual)) if e != a][:1]!r}'
    print(f'shared ownership parity: {len(rows)} multi-profile ledger rows match')


def shared_snapshot_rows(base: Path) -> None:
    from agent_workbench import shared_config as shared
    from unittest.mock import patch
    base.mkdir(parents=True)
    def readonly_snapshot(case):
        typed = bundle_snapshot(case)
        metadata = {}
        for name in typed:
            info = (case / name).lstat()
            metadata[name] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                              info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return typed, metadata
    rows, expected, before_rust = [], [], {}
    for scenario in ('regular', 'absent', 'missing-parent', 'symlink', 'parent-symlink', 'fifo', 'directory', 'during-read', 'ledger', 'ledger-bool', 'ledger-bad-key', 'ledger-bad-record', 'utf8-bom', 'utf16', 'utf16be', 'utf32', 'utf32be', 'invalid-utf8'):
        case = base / scenario; case.mkdir()
        path = case / 'document.json'
        content = ' {"z": 1, "a":2}\n'
        ledger = scenario.startswith('ledger')
        if ledger:
            path = case / '.agent-workbench-owners.json'
            data = {'version': True if scenario == 'ledger-bool' else 1, 'slots': {}}
            if scenario == 'ledger-bad-key': data['slots']['BAD'] = {'owner': '/owner', 'digest': 'a' * 64}
            if scenario == 'ledger-bad-record': data['slots']['a' * 64] = {'owner': '/owner', 'digest': 'BAD'}
            content = json.dumps(data)
        if scenario == 'missing-parent': path = case / 'absent/document.json'
        elif scenario == 'directory': path.mkdir()
        elif scenario == 'fifo': os.mkfifo(path)
        elif scenario != 'absent': path.write_text(content)
        if scenario == 'symlink': path.rename(case / 'real'); path.symlink_to('real')
        if scenario == 'parent-symlink':
            (case / 'alias').symlink_to(case, target_is_directory=True); path = case / 'alias/document.json'
        if scenario in ('utf8-bom', 'utf16', 'utf16be', 'utf32', 'utf32be', 'invalid-utf8'):
            raw = {'utf8-bom': content.encode('utf-8-sig'), 'utf16': content.encode('utf-16'),
                   'utf16be': b'\xfe\xff' + content.encode('utf-16-be'), 'utf32': content.encode('utf-32'),
                   'utf32be': b'\x00\x00\xfe\xff' + content.encode('utf-32-be'), 'invalid-utf8': b'{"bad":"\xff"}'}[scenario]
            path.write_bytes(raw)
        rust_path = path
        before_python = readonly_snapshot(case)
        calls = 0
        original = os.fstat
        def fstat(fd):
            nonlocal calls
            info = original(fd)
            if stat.S_ISREG(info.st_mode):
                calls += 1
                if calls == 2: path.write_text('{"changed":true}'); return original(fd)
            return info
        try:
            with patch.object(shared.os, 'fstat', fstat if scenario == 'during-read' else original):
                result = shared.ownership_snapshot(case) if ledger else shared.snapshot(path)
            expected.append({'accepted': True, 'content': list(result.content) if result.content is not None else None, 'revision': result.revision is not None})
        except (OSError, ValueError) as error:
            expected.append({'accepted': False, 'error_class': 'ValueError' if isinstance(error, ValueError) else 'OSError'})
        if scenario == 'during-read':
            assert path.read_bytes() == b'{"changed":true}'
            path.write_text(content)
        else:
            assert readonly_snapshot(case) == before_python, ('Python snapshot mutated fixture', scenario)
        before_rust[scenario] = readonly_snapshot(case)
        rows.append(dict(mode='snapshot', path=str(rust_path), scenario=scenario, ledger=ledger))
    (base / 'rows.json').write_text(json.dumps(rows))
    result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'shared_document_parity_tests::differential_shared_document', '--', '--ignored', '--exact'], {'AW_SHARED_BASE': str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / 'results.json').read_text())
    assert actual == expected, f'snapshot mismatch: {[(r,e,a) for r,e,a in zip(rows,expected,actual) if e != a][:1]!r}'
    for scenario, before in before_rust.items():
        case = base / scenario
        if scenario == 'during-read':
            after = readonly_snapshot(case)
            assert (case / 'document.json').read_bytes() == b'{"changed":true}'
            # Only the explicitly injected adversarial write is permitted.
            for index in (0, 1):
                assert {k:v for k,v in before[index].items() if k != 'document.json'} == {k:v for k,v in after[index].items() if k != 'document.json'}
        else:
            assert readonly_snapshot(case) == before, ('Rust snapshot mutated fixture', scenario)
    print(f'shared snapshot parity: {len(rows)} nofollow/read-drift/ledger validation rows match')


def shared_reference_rows(base: Path) -> None:
    from agent_workbench.shared_config import hook_document_references_bundle
    base.mkdir(parents=True)
    rows, expected = [], []
    bundle = Path('/config/managed/pstack')
    commands = [str(bundle) + '/hooks/run', './managed/pstack/hooks/run', 'managed/pstack/hooks/run', 'config/managed/pstack/hooks/run', 'xmanaged/pstack/run', '/other/managed/pstack/run', str(bundle) + '-other/run', "sh '/config/managed/pstack/hooks/run'", 'sh /config/managed/p\\stack/hooks/run', 'echo "unterminated /config/managed/pstack/hooks/run', 'echo /config/managed/pstack; next', 'echo /config/managed/pstackX', 'nothing']
    for command in commands:
        for nested in (False, True):
            entry = {'hooks': [{'type': 'command', 'command': command}]} if nested else {'command': command}
            content = json.dumps({'hooks': {'Stop': [entry]}})
            expected.append(hook_document_references_bundle(content.encode(), bundle))
            rows.append(dict(mode='reference', content=content, bundle=str(bundle)))
    for content in (None, '{"hooks":null}', '{"hooks":{"Stop":{}}}', '{"hooks":{"Stop":[1]}}', '{"hooks":{"Stop":[{"hooks":[1,{}, {"command":3}]}]}}', '{"hooks":{"Stop":[{"command":"/config/managed/pstack/run","hooks":false}]}}', '{"hooks":{"Stop":[{"command":"/config/managed/pstack/run"},1]}}', '{"hooks":{"Stop":[1,{"command":"/config/managed/pstack/run"}]}}'):
        try:
            expected.append(hook_document_references_bundle(content.encode() if content is not None else None, bundle))
        except ValueError as error:
            expected.append({'error': str(error)})
        rows.append(dict(mode='reference', content=content, bundle=str(bundle)))
    for encoding in ('utf-8-sig', 'utf-16', 'utf-16-be', 'utf-32', 'utf-32-be'):
        content = json.dumps({'hooks': {'Stop': [{'command': '/config/managed/pstack/run'}]}}).encode(encoding)
        expected.append(hook_document_references_bundle(content, bundle))
        rows.append(dict(mode='reference', content=None, content_bytes=list(content), bundle=str(bundle)))
    (base / 'rows.json').write_text(json.dumps(rows))
    result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'shared_document_parity_tests::differential_shared_document', '--', '--ignored', '--exact'], {'AW_SHARED_BASE': str(base)})
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / 'results.json').read_text())
    assert actual == expected, f'bundle reference mismatch: {[(r,e,a) for r,e,a in zip(rows,expected,actual) if e != a][:1]!r}'
    print(f'shared hook references: {len(rows)} native/foreign bundle removal guards match')


def transaction_publication_rows(base: Path) -> None:
    """D5 primitive tracer; not evidence for activation apply/recover."""
    from agent_workbench import shared_config as shared, bundles
    from unittest.mock import patch
    base.mkdir(parents=True)
    rows, expected, pairs = [], [], []
    scenarios = ('replace', 'create', 'remove', 'stale', 'before-capture',
                 'before-publish', 'fail-file-sync', 'fail-parent-sync', 'fail-capture-sync',
                 'fail-evidence-sync', 'fail-cleanup-sync', 'fail-entry-delete', 'fail-quarantine-remove')
    for scenario in scenarios:
        pair = []
        for implementation in ('python', 'rust'):
            case = base / scenario / implementation
            case.mkdir(parents=True)
            path = case / 'document.json'
            if scenario != 'create':
                path.write_bytes(b'{"before":true}\n'); path.chmod(0o640)
            pair.append(case)
        py, rs = pair
        path = py / 'document.json'
        snapshot = shared.snapshot(path)
        if scenario == 'stale': path.write_bytes(b'{"foreign":true}\n')
        parent = bundles.open_directory(py)
        rename, sync = bundles.rename_new, os.fsync
        def rename_new(source, destination, fd, destination_fd=None):
            if scenario == 'before-capture' and source == 'document.json':
                path.write_bytes(b'{"foreign":true}\n')
            if scenario == 'before-publish' and source == 'new':
                path.write_bytes(b'{"foreign":true}\n')
            return rename(source, destination, fd, destination_fd)
        sync_count = 0
        def fsync(fd):
            nonlocal sync_count
            sync_count += 1
            failure = {'fail-capture-sync': 1, 'fail-evidence-sync': 2,
                       'fail-file-sync': 3, 'fail-parent-sync': 4, 'fail-cleanup-sync': 5}.get(scenario)
            if sync_count == failure:
                raise OSError('injected sync failure')
            return sync(fd)
        unlink, rmdir = os.unlink, os.rmdir
        def unlink_entry(name, *args, **kwargs):
            if scenario == 'fail-entry-delete' and name == 'entry':
                raise OSError('injected evidence deletion failure')
            return unlink(name, *args, **kwargs)
        def rmdir_quarantine(name, *args, **kwargs):
            if scenario == 'fail-quarantine-remove' and name == 'q':
                raise OSError('injected quarantine removal failure')
            return rmdir(name, *args, **kwargs)
        try:
            with patch.object(bundles, 'rename_new', rename_new), patch.object(shared.os, 'fsync', fsync), patch.object(shared.os, 'unlink', unlink_entry), patch.object(shared.os, 'rmdir', rmdir_quarantine):
                value = shared.compare_replace_document(snapshot, None if scenario == 'remove' else b'{"after":true}\n', parent, 'q')
            expected.append({'accepted': True, 'content': list(value.content) if value.content is not None else None})
        except (OSError, ValueError) as error:
            expected.append({'accepted': False, 'error_class': 'ValueError' if isinstance(error, ValueError) else 'OSError'})
        finally:
            os.close(parent)
        rows.append({'case': str(rs), 'scenario': scenario})
        pairs.append(pair)
    (base / 'rows.json').write_text(json.dumps(rows))
    result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'transaction_parity_tests::differential_publication', '--', '--ignored', '--exact'], {'AW_TRANSACTION_BASE': str(base)}, umask=0o022)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((base / 'results.json').read_text())
    assert actual == expected, f'publication results mismatch: {[(r,e,a) for r,e,a in zip(rows,expected,actual) if e != a]!r}'
    for row, (py, rs) in zip(rows, pairs):
        assert bundle_snapshot(py) == bundle_snapshot(rs), (row, bundle_snapshot(py), bundle_snapshot(rs))
    print(f'transaction publication parity: {len(rows)} capture/stale/noreplace/fsync rows match')


def transaction_journal_rows(base: Path) -> None:
    """Authenticate a real interrupted Python lifecycle, not fabricated plan data."""
    from agent_workbench import shared_config as shared, transactions, installation
    from unittest.mock import patch
    sys.path.insert(0, str(REPOSITORY / 'tests'))
    from test_activation import ActivationTests
    base.mkdir(parents=True)
    fixture = ActivationTests()
    fixture.setUp()
    try:
        plan = fixture.trusted()
        original = shared.replace_document
        def interrupt(path, content, fd):
            original(path, content, fd)
            if path.name == '.agent-workbench-transaction.json':
                raise KeyboardInterrupt('durable journal before first activation mutation')
        with patch.object(shared, 'replace_document', interrupt):
            try: installation.apply_plan(plan)
            except KeyboardInterrupt: pass
            else: raise AssertionError('interruption not reached')
        journal = fixture.hook_config.parent / '.agent-workbench-transaction.json'
        saved = journal.read_bytes()
        record = json.loads(saved)
        rows, expected = [], []
        variants = [('valid', saved, plan.state_file),
                    ('reformatted', json.dumps(record, sort_keys=True).encode(), plan.state_file),
                    ('wrong-profile', saved, plan.state_file.with_name('other.json')),
                    ('duplicate', b'{"version":1,' + saved[1:], plan.state_file)]
        for field in record:
            changed = dict(record)
            changed[field] = 'tampered'
            variants.append(('tampered-' + field, shared.encode(changed), plan.state_file))
        for label, content, file in variants:
            try:
                transactions._journal_record(content, file)
                expected.append({'accepted': True})
            except (OSError, ValueError):
                expected.append({'accepted': False, 'error_class': 'ValueError'})
            rows.append({'mode': 'journal', 'label': label, 'content': list(content), 'file': str(file)})
        before = bundle_snapshot(fixture.base)
        (base / 'rows.json').write_text(json.dumps(rows))
        result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'transaction_parity_tests::differential_publication', '--', '--ignored', '--exact'], {'AW_TRANSACTION_BASE': str(base)})
        assert result.returncode == 0, result.stdout + result.stderr
        actual = json.loads((base / 'results.json').read_text())
        assert actual == expected, f'journal mismatch: {[(r["label"],e,a) for r,e,a in zip(rows,expected,actual) if e != a]!r}'
        assert bundle_snapshot(fixture.base) == before, 'authentication must be read-only'
        # Exercise the real Python recovery entry point after the durable split.
        roots = {component: Path(root) for component, root in plan.next_state.destination_roots.items()}
        with installation._state_lock(plan.state_file), transactions._document_lock(journal.parent, False):
            transactions._recover_locked(plan.state_file, roots)
        assert not journal.exists() and not fixture.bundle.exists() and not fixture.hook_config.exists()
        print(f'transaction journal authentication: {len(rows)} actual plan/apply-interrupt/tamper rows match (Python recovery only)')
    finally:
        fixture.doCleanups()


def transaction_recovery_rows(base: Path) -> None:
    """Real interrupted install/uninstall recovery at pinned identical lexical roots."""
    from agent_workbench import transactions, installation, shared_config as shared
    from agent_workbench.model import ComponentKind as C
    from unittest.mock import patch
    import hashlib
    sys.path.insert(0, str(REPOSITORY / 'tests'))
    from test_activation import ActivationTests
    base.mkdir(parents=True)
    scenarios = ('success', 'prior-state', 'foreign-document', 'foreign-state',
                 'prefix', 'bad-prefix', 'captured', 'state-captured', 'symlink-evidence',
                 'foreign-evidence', 'parent-swap', 'late-state', 'late-document',
                 'interrupt-deactivate', 'interrupt-restore', 'interrupt-state', 'interrupt-cleanup',
                 'interrupt-prepare-sync', 'interrupt-prepare-publish', 'unauthenticated',
                 'document-outside', 'parent-outside', 'missing-parent', 'quarantine-invalid',
                 'base64-invalid', 'state-lock-held', 'document-lock-held', 'reauthenticate', 'late-parent-swap')
    from test_claude_activation import ClaudeActivationTests
    jobs = [('cursor', False, scenario) for scenario in scenarios]
    managed_scenarios = ('install', 'uninstall', 'interrupt-deactivate', 'interrupt-restore',
                         'interrupt-state', 'interrupt-cleanup', 'unknown-fields', 'base64-alias',
                         'legacy-parents', 'foreign-path', 'resource-parent-swap',
                         'interrupt-path', 'late-capture', 'resource-quarantine', 'staged-partial',
                         'state-parent-swap', 'parent-swap', 'late-parent-swap', 'late-state',
                         'unauthenticated', 'reauthenticate')
    jobs += [(target, True, scenario) for target in ('cursor', 'claude') for scenario in managed_scenarios]
    for target, managed, scenario in jobs:
        fixture = (ActivationTests if target == 'cursor' else ClaudeActivationTests)(); fixture.setUp()
        held = None
        try:
            # A real previous install supplies nonempty prior ownership + documents.
            if scenario in ('prior-state', 'interrupt-restore', 'captured', 'interrupt-prepare-sync', 'interrupt-prepare-publish'):
                installation.apply_plan(fixture.trusted((C.MCP,)))
                source = fixture.fixture.root / 'plugins/sample/mcp.json'
                source.write_text(source.read_text().replace('https://', 'https://changed.'))
                # Not all fixtures use HTTP servers; change the selected JSON slot.
                data = json.loads(source.read_text())
                slot = next(iter(data['mcpServers'].values()))
                slot['env'] = {'TRACER': 'new-value'}
                source.write_text(json.dumps(data))
            components = (C.HOOKS, C.MCP) if managed else (C.MCP,)
            if scenario in ('uninstall', 'interrupt-restore', 'resource-quarantine', 'staged-partial') and managed:
                installation.apply_plan(fixture.trusted(components))
                plan = fixture.uninstall()
            else:
                plan = fixture.trusted(components)
            unlink = os.unlink
            def interrupt(name, *args, **kwargs):
                if name == '.agent-workbench-transaction.json':
                    raise KeyboardInterrupt('after documents, ledger and ownership publication')
                return unlink(name, *args, **kwargs)
            with patch.object(transactions.os, 'unlink', interrupt):
                try:
                    (installation.apply_uninstall if managed and scenario in ('uninstall', 'interrupt-restore', 'resource-quarantine', 'staged-partial') else installation.apply_plan)(plan)
                except KeyboardInterrupt: pass
                else: raise AssertionError('real apply interruption not reached')
            roots = {c: Path(p) for c, p in plan.next_state.destination_roots.items()}
            journal = transactions._journal_path(roots, target)
            record = json.loads(journal.read_bytes())
            assert bool(record['paths']) == managed
            item = next(item for item in record['documents'] if item['path'] == str(fixture.mcp_config))
            document = fixture.mcp_config
            if scenario in ('unknown-fields', 'base64-alias', 'legacy-parents'):
                import base64, hmac
                if scenario == 'unknown-fields':
                    record['foreign'] = {'safe': True}; item['extra'] = 7
                if scenario == 'legacy-parents': record.pop('parents')

                if scenario == 'base64-alias':
                    # Python validate=True accepts nonzero unused padding bits.
                    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
                    image = item['after']
                    if not image.endswith('='):
                        raw = base64.b64decode(image) + b' '
                        image = base64.b64encode(raw).decode()
                        document.write_bytes(raw)
                    index = -3 if image.endswith('==') else -2
                    image = image[:index] + alphabet[alphabet.index(image[index]) + 1] + image[index + 1:]
                    assert base64.b64decode(image, validate=True) == document.read_bytes()
                    item['after'] = image
                record.pop('mac')
                record['mac'] = hmac.new(transactions._journal_key(plan.state_file), transactions._canonical(record), hashlib.sha256).hexdigest()
                journal.write_bytes(shared.encode(record))
            if scenario == 'foreign-path': (fixture.bundle / 'stop.sh').write_bytes(b'foreign-resource')
            if scenario == 'resource-parent-swap':
                root = fixture.bundle.parent; root.rename(root.with_name('detached-resource')); root.mkdir()
                (root / 'foreign').write_bytes(b'outside lock')
            if scenario == 'state-parent-swap':
                root = plan.state_file.parent; detached = root.with_name('detached-state')
                root.rename(detached); root.mkdir()
                for p in detached.iterdir():
                    if p.is_file():
                        (root / p.name).write_bytes(p.read_bytes()); (root / p.name).chmod(p.stat().st_mode & 0o777)
            if scenario in ('resource-quarantine', 'staged-partial'):
                resource_item = record['paths'][0]
                root = Path(resource_item['path']).parent
                q = resource_item['quarantine']
                fd_resource = transactions.bundles.open_directory(root)
                try:
                    if scenario == 'resource-quarantine':
                        (root / q).mkdir(mode=0o700)
                        qfd = transactions.bundles.open_directory(root / q)
                        try: transactions.bundles.write_tree('entry', qfd, transactions._tree(resource_item['before']['tree']))
                        finally: os.close(qfd)
                        next(p for p in (root / q / 'entry').rglob('*') if p.is_file()).unlink()
                    else:
                        transactions.bundles.write_tree(q + '-rollback-tree', fd_resource, transactions._tree(resource_item['before']['tree']))
                        next(p for p in (root / (q + '-rollback-tree')).rglob('*') if p.is_file()).unlink()
                finally: os.close(fd_resource)
            if scenario == 'foreign-document': document.write_bytes(b'{"foreign":true}\n')
            if scenario == 'foreign-state': plan.state_file.write_bytes(b'{"foreign":true}\n')
            if scenario in ('prefix', 'bad-prefix', 'captured', 'foreign-evidence', 'symlink-evidence', 'interrupt-prepare-sync', 'interrupt-prepare-publish'):
                q = document.parent / (item['quarantine'] + '-activate'); q.mkdir(mode=0o700)
                if scenario in ('captured', 'interrupt-prepare-sync', 'interrupt-prepare-publish'): document.rename(q / 'entry')
                elif scenario == 'foreign-evidence': (q / 'foreign').write_bytes(b'keep')
                elif scenario == 'symlink-evidence': (q / 'new').symlink_to(plan.state_file)
                else: (q / 'new').write_bytes(b'BAD' if scenario == 'bad-prefix' else document.read_bytes()[:7])
            if scenario == 'state-captured':
                q = plan.state_file.parent / ('.agent-workbench-document-' + hashlib.sha256(str(plan.state_file).encode()).hexdigest()[:16] + '-activate')
                q.mkdir(mode=0o700); plan.state_file.rename(q / 'entry')
            if scenario == 'parent-swap':
                config = journal.parent
                detached = config.with_name('detached')
                config.rename(detached); config.mkdir()
                for p in detached.iterdir():
                    if p.is_file(): (config / p.name).write_bytes(p.read_bytes()); (config / p.name).chmod(p.stat().st_mode & 0o777)
            if scenario == 'unauthenticated':
                record['mac'] = '0' * 64; journal.write_bytes(shared.encode(record))
                # Authentication must not create either missing lock file.
                for lock in (journal.parent / '.agent-workbench.lock', plan.state_file.with_name('.' + plan.state_file.name + '.lock')):
                    lock.unlink(missing_ok=True)
            if scenario in ('document-outside', 'parent-outside', 'missing-parent', 'quarantine-invalid', 'base64-invalid'):
                import hmac
                if scenario == 'document-outside': item['path'] = str(fixture.base / 'outside.json')
                if scenario == 'parent-outside':
                    info = fixture.base.stat(); record['parents'][str(fixture.base)] = [info.st_dev, info.st_ino]
                if scenario == 'missing-parent': record['parents'].pop(str(plan.state_file.parent))
                if scenario == 'quarantine-invalid': item['quarantine'] = '../foreign'
                if scenario == 'base64-invalid': item['before'] = '!'
                record.pop('mac')
                record['mac'] = hmac.new(transactions._journal_key(plan.state_file), transactions._canonical(record), hashlib.sha256).hexdigest()
                journal.write_bytes(shared.encode(record))
            held = None
            if scenario in ('state-lock-held', 'document-lock-held'):
                import fcntl
                lock = plan.state_file.with_name('.' + plan.state_file.name + '.lock') if scenario == 'state-lock-held' else journal.parent / '.agent-workbench.lock'
                held = os.open(lock, os.O_RDWR); fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            initial = bundle_snapshot(fixture.base)
            initial_dirs = {p: p.stat().st_mode & 0o777 for p in fixture.base.rglob('*') if p.is_dir()}
            saved_files = {p: (p.read_bytes(), p.stat().st_mode & 0o777) for p in fixture.base.rglob('*') if p.is_file() and not p.is_symlink()}
            saved_links = {p: os.readlink(p) for p in fixture.base.rglob('*') if p.is_symlink()}
            original_compare, original_prepare, original_sync, original_rename = shared.compare_replace_document, transactions._prepare_document_recovery, os.fsync, transactions.bundles.rename_new
            hit = False
            def compare(current, content, fd, quarantine):
                nonlocal hit
                value = original_compare(current, content, fd, quarantine)
                if not hit and ((scenario == 'interrupt-deactivate' and quarantine.endswith('-recover-remove')) or (scenario == 'interrupt-restore' and quarantine.endswith('-recover-before'))):
                    hit = True; raise OSError('interrupted after durable document recovery publication')
                if not hit and scenario == 'late-state':
                    hit = True; plan.state_file.write_bytes(b'{"foreign":true}\n')
                return value
            def prepare(item, current, fd):
                nonlocal hit
                if not hit and scenario == 'late-document':
                    hit = True; current.path.write_bytes(b'{"foreign":true}\n')
                if not hit and scenario == 'interrupt-state' and current.path == plan.state_file:
                    hit = True; raise OSError('interrupted before ownership restoration')
                return original_prepare(item, current, fd)
            def fsync(fd):
                nonlocal hit
                if not hit and scenario == 'interrupt-prepare-sync' and os.fstat(fd).st_mode & 0o170000 == 0o100000:
                    hit = True; raise OSError('interrupted recovery staging fsync')
                return original_sync(fd)
            def rename(source, destination, fd, destination_fd=None):
                nonlocal hit
                if not hit and scenario == 'late-capture' and source == fixture.bundle.name:
                    hit = True; (fixture.bundle / 'stop.sh').write_bytes(b'foreign-resource')
                if not hit and scenario == 'interrupt-prepare-publish' and source == 'recovery':
                    hit = True; raise OSError('interrupted recovery publication')
                return original_rename(source, destination, fd, destination_fd)
            from contextlib import contextmanager
            original_lock = transactions._document_lock
            original_snapshot = shared.snapshot
            def snapshot(path, fd=None):
                nonlocal hit
                value = original_snapshot(path, fd)
                if not hit and scenario == 'late-parent-swap' and str(path) == record['documents'][-1]['path']:
                    hit = True
                    config = journal.parent; config.rename(config.with_name('detached'))
                    config.mkdir(); (config / 'foreign').write_bytes(b'outside lock')
                return value
            @contextmanager
            def document_lock(*args, **kwargs):
                with original_lock(*args, **kwargs):
                    if scenario == 'reauthenticate':
                        changed = json.loads(journal.read_bytes()); changed['mac'] = '0' * 64
                        journal.write_bytes(shared.encode(changed))
                    yield
            def cleanup(name, *args, **kwargs):
                nonlocal hit
                if not hit and scenario == 'interrupt-cleanup' and name == journal.name:
                    hit = True; raise OSError('interrupted final journal cleanup')
                return unlink(name, *args, **kwargs)
            def python_recover():
                try:
                    transactions.recover(plan.state_file, roots, target)
                    return {'accepted': True}
                except (OSError, ValueError) as error:
                    return {'accepted': False, 'error_class': 'ValueError' if isinstance(error, ValueError) else 'FileExistsError' if isinstance(error, FileExistsError) else 'OSError'}
            original_restore = transactions._restore_path
            def restore(*args, **kwargs):
                nonlocal hit
                value = original_restore(*args, **kwargs)
                if not hit and scenario == 'interrupt-path':
                    hit = True; raise OSError('interrupted after path restoration')
                return value
            with patch.object(transactions, '_restore_path', restore), patch.object(transactions.os, 'unlink', cleanup), patch.object(shared, 'snapshot', snapshot), patch.object(transactions, '_document_lock', document_lock), patch.object(shared, 'compare_replace_document', compare), patch.object(transactions, '_prepare_document_recovery', prepare), patch.object(transactions.os, 'fsync', fsync), patch.object(transactions.bundles, 'rename_new', rename):
                expected = python_recover()
            py_snapshot = bundle_snapshot(fixture.base)
            retry_expected = retry_snapshot = None
            if scenario.startswith('interrupt-'):
                assert hit and expected == {'accepted': False, 'error_class': 'OSError'}, (scenario, expected)
                retry_expected = python_recover(); assert retry_expected == {'accepted': True}
                retry_snapshot = bundle_snapshot(fixture.base)
            # Restore files only: authenticated parent identities remain unchanged.
            if scenario == 'late-parent-swap':
                assert hit and not expected['accepted'], (target, managed, expected)
                config = journal.parent; (config / 'foreign').unlink(); config.rmdir()
                config.with_name('detached').rename(config)
            for p in sorted(fixture.base.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                if held is not None and p == lock: continue
                if p.is_symlink() or p.is_file(): p.unlink()
                elif p.is_dir() and p not in initial_dirs: p.rmdir()
            for p in sorted(initial_dirs, key=lambda p: len(p.parts)):
                if not p.exists(): p.mkdir(mode=initial_dirs[p])
                p.chmod(initial_dirs[p])
            for p, (content, mode) in saved_files.items(): p.write_bytes(content); p.chmod(mode)
            for p, link_target in saved_links.items(): p.symlink_to(link_target)
            assert bundle_snapshot(fixture.base) == initial
            row = {'mode': 'recover', 'file': str(plan.state_file), 'document': str(document), 'target': target, 'roots': {c.value: str(p) for c,p in roots.items()}, 'scenario': scenario,
                   'resource': record['paths'][0]['path'] if managed else None, 'journal': str(journal)}
            def rust_recover(row):
                (base / 'rows.json').write_text(json.dumps([row]))
                result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'transaction_parity_tests::differential_publication', '--', '--ignored', '--exact'], {'AW_TRANSACTION_BASE': str(base)})
                assert result.returncode == 0, result.stdout + result.stderr
                return json.loads((base / 'results.json').read_text())[0]
            actual = rust_recover(row)
            assert actual == expected, (scenario, expected, actual)
            assert bundle_snapshot(fixture.base) == py_snapshot, (scenario, py_snapshot, bundle_snapshot(fixture.base))
            if retry_expected is not None:
                interrupted_dirs = {p: p.stat().st_mode & 0o777 for p in fixture.base.rglob('*') if p.is_dir()}
                interrupted_files = {p: (p.read_bytes(), p.stat().st_mode & 0o777) for p in fixture.base.rglob('*') if p.is_file()}
                # Python can consume Rust-interrupted recovery evidence (handoff).
                assert python_recover() == retry_expected
                assert bundle_snapshot(fixture.base) == retry_snapshot, (scenario, 'Rust -> Python retry')
                # The same Rust-interrupted evidence must also be retryable by Rust.
                for p in sorted(fixture.base.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                    if p.is_file(): p.unlink()
                    elif p.is_dir() and p not in interrupted_dirs: p.rmdir()
                for p, mode in sorted(interrupted_dirs.items(), key=lambda pair: len(pair[0].parts)):
                    if not p.exists(): p.mkdir(mode=mode)
                for p, (content, mode) in interrupted_files.items(): p.write_bytes(content); p.chmod(mode)
                assert rust_recover(dict(row, scenario='success')) == retry_expected
                assert bundle_snapshot(fixture.base) == retry_snapshot, (scenario, 'Rust -> Rust retry')
            elif actual['accepted']:
                assert not journal.exists()
                assert rust_recover(dict(row, scenario='success')) == {'accepted': True}
                if scenario == 'success' and not managed:
                    import copy, hmac
                    for gate in ('nonempty-paths', 'missing-image', 'duplicate-document', 'duplicate-quarantine', 'document-path-alias', 'parent-path-alias'):
                        changed = copy.deepcopy(record); changed.pop('mac')
                        if gate == 'nonempty-paths': changed['paths'] = [{'path': str(fixture.base / 'unimplemented-resource')}]

                        if gate == 'missing-image': changed['documents'][0].pop('before')
                        if gate == 'duplicate-document': changed['documents'].append(copy.deepcopy(changed['documents'][0]))
                        if gate == 'duplicate-quarantine': changed['documents'][1]['quarantine'] = changed['documents'][0]['quarantine']
                        if gate == 'document-path-alias':
                            path = Path(changed['documents'][0]['path']); changed['documents'][0]['path'] = str(path.parent) + '/./' + path.name
                        if gate == 'parent-path-alias':
                            path = next(iter(changed['parents'])); changed['parents'][path + '/.'] = changed['parents'].pop(path)
                        changed['mac'] = hmac.new(transactions._journal_key(plan.state_file), transactions._canonical(changed), hashlib.sha256).hexdigest()
                        journal.write_bytes(shared.encode(changed))
                        before_gate = bundle_snapshot(fixture.base)
                        assert rust_recover(dict(row, scenario='success')) == {'accepted': False, 'error_class': 'ValueError'}, gate
                        assert bundle_snapshot(fixture.base) == before_gate, (gate, 'rejected intent must not mutate')
                    journal.unlink()
        finally:
            if held is not None: os.close(held)
            fixture.doCleanups()
    print(f'transaction recovery parity: {len(jobs)} real apply/document/path/ledger/state/evidence/retry rows match; Rust -> Python interrupted recovery handoff')


def transaction_durable_rows(base: Path) -> None:
    """Sequential replay at identical paths: no journal byte normalization."""
    from agent_workbench import transactions, shared_config as shared, bundles
    from unittest.mock import patch
    import fcntl
    base.mkdir(parents=True)
    scenarios = [('key', s) for s in ('new', 'existing', 'public', 'short', 'symlink', 'hardlink', 'fifo', 'directory', 'key-create', 'key-chmod', 'key-entropy', 'key-write', 'key-sync', 'key-parent-sync')]
    scenarios += [('write-journal', s) for s in ('success', 'stage-write', 'stage-sync', 'rename', 'parent-sync')]
    scenarios += [('lock', s) for s in ('new', 'existing', 'symlink', 'fifo', 'directory', 'contended', 'missing-read', 'missing-create', 'root-swap')]
    for mode, scenario in scenarios:
        case = base / (mode + '-' + scenario)
        case.mkdir()
        file = case / 'state.json'
        key = case / '.state.json.transaction-key'
        config = case / 'config'
        config.mkdir()
        lock = config / '.agent-workbench.lock'
        journal = config / '.agent-workbench-transaction.json'
        # Use the adapter's actual lock name, not a guessed fixture token.
        from agent_workbench.adapters.activation import ACTIVATION_POLICIES
        lock = config / ACTIVATION_POLICIES['cursor'].lock_name
        def setup():
            if mode == 'key':
                if scenario in ('existing', 'public', 'short', 'hardlink'):
                    key.write_bytes(bytes(range(31 if scenario == 'short' else 32)))
                    key.chmod(0o644 if scenario == 'public' else 0o600)
                    if scenario == 'hardlink': os.link(key, case / 'other')
                elif scenario == 'symlink': key.symlink_to('absent')
                elif scenario == 'fifo': os.mkfifo(key)
                elif scenario == 'directory': key.mkdir()
            elif mode == 'write-journal':
                key.write_bytes(bytes(range(32))); key.chmod(0o600)
                journal.write_bytes(b'{"old":true}\n')
            else:
                if scenario in ('existing', 'contended'): lock.write_bytes(b'foreign lock bytes'); lock.chmod(0o644)
                elif scenario == 'symlink': lock.symlink_to('absent')
                elif scenario == 'fifo': os.mkfifo(lock)
                elif scenario == 'directory': lock.mkdir()
                elif scenario.startswith('missing-'): config.rmdir()
        setup()
        row: dict[str, object] = {'mode': mode, 'scenario': scenario, 'file': str(file), 'config': str(config), 'lock': lock.name}
        record = {'version': 1, 'stateFile': str(file), 'parents': {str(config): [1, 2]}, 'stateBefore': None, 'stateAfter': None, 'documents': [], 'paths': []}
        row['record_json'] = list(shared.encode(record))
        held = None
        if scenario == 'contended':
            held = os.open(lock, os.O_RDWR); fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        real_sync, real_write, real_replace = os.fsync, os.write, os.replace
        sync_count = 0
        def sync(fd):
            nonlocal sync_count
            sync_count += 1
            fail = {'key-sync': 1, 'key-parent-sync': 2, 'stage-sync': 1, 'parent-sync': 2}.get(scenario)
            if sync_count == fail: raise OSError('injected sync failure')
            return real_sync(fd)
        try:
            with patch.object(os, 'fsync', sync), patch.object(os, 'urandom', lambda n: bytes(range(n))):
                if mode == 'key':
                    # Buffered key write failure is injected at fdopen, before writing.
                    original_fdopen = os.fdopen
                    def fdopen(fd, *args, **kwargs):
                        if scenario == 'key-write':
                            os.close(fd); raise OSError('injected write failure')
                        return original_fdopen(fd, *args, **kwargs)
                    original_open = os.open
                    hit = False
                    def key_open(name, flags, *args, **kwargs):
                        nonlocal hit
                        if scenario == 'key-create' and flags & os.O_EXCL:
                            hit = True
                            raise OSError('injected exclusive create failure')
                        return original_open(name, flags, *args, **kwargs)
                    original_fchmod = os.fchmod
                    def key_chmod(fd, mode):
                        nonlocal hit
                        if scenario == 'key-chmod':
                            hit = True
                            raise OSError('injected chmod failure')
                        return original_fchmod(fd, mode)
                    def entropy(n):
                        nonlocal hit
                        if scenario == 'key-entropy':
                            hit = True
                            raise OSError('injected entropy failure')
                        return bytes(range(n))
                    with patch.object(os, 'fdopen', fdopen), patch.object(os, 'open', key_open), patch.object(os, 'fchmod', key_chmod), patch.object(os, 'urandom', entropy):
                        transactions._journal_key(file, True)
                elif mode == 'write-journal':
                    import hmac, hashlib
                    record['mac'] = hmac.new(transactions._journal_key(file, True), transactions._canonical(record), hashlib.sha256).hexdigest()
                    def replace(*args, **kwargs):
                        if scenario == 'rename': raise OSError('injected rename failure')
                        return real_replace(*args, **kwargs)
                    original_fdopen = os.fdopen
                    def stage_fdopen(out, *args, **kwargs):
                        if scenario == 'stage-write':
                            os.close(out); raise OSError('injected journal write failure')
                        return original_fdopen(out, *args, **kwargs)
                    with patch.object(os, 'replace', replace), patch.object(os, 'fdopen', stage_fdopen):
                        fd = bundles.open_directory(config)
                        try: shared.replace_document(journal, shared.encode(record), fd)
                        finally: os.close(fd)
                    del record['mac']
                else:
                    with transactions._document_lock(config, scenario != 'missing-read'):
                        if scenario == 'root-swap':
                            config.rename(case / 'detached')
                            config.mkdir()
                            (config / 'foreign').write_bytes(b'outside lock')
            expected = {'accepted': True}
        except (OSError, ValueError) as error:
            expected = {'accepted': False, 'error_class': 'FileExistsError' if isinstance(error, FileExistsError) else 'ValueError' if isinstance(error, ValueError) else 'OSError'}
        if 'mac' in record: del record['mac']
        py_snapshot = bundle_snapshot(case)
        # New random authority bytes are explicitly nondeterministic; preserve length/mode/kind.
        def normalize(snapshot):
            if mode == 'key' and scenario in ('new', 'key-sync', 'key-parent-sync') and key.exists():
                value = snapshot.get(key.name)
                if value is not None:
                    assert len(value[-1]) == 32, (mode, scenario, 'authority key must remain exactly 32 bytes', value)
                    value = list(value); value[-1] = '<random-32-byte-key>'; snapshot[key.name] = tuple(value)
            return snapshot
        py_snapshot = normalize(py_snapshot)
        # Restore input tree, preserving contention descriptor while its inode is live.
        if mode == 'key':
            if key.is_dir(): key.rmdir()
            elif key.exists() or key.is_symlink(): key.unlink()
            if (case / 'other').exists(): (case / 'other').unlink()
            setup()
        elif mode == 'write-journal': journal.write_bytes(b'{"old":true}\n')
        elif scenario == 'new': lock.unlink()
        elif scenario == 'missing-create': lock.unlink(); config.rmdir()
        elif scenario == 'root-swap':
            (config / 'foreign').unlink(); config.rmdir()
            (case / 'detached').rename(config)
            lock.unlink()
        (base / 'rows.json').write_text(json.dumps([row]))
        result = run(['cargo', 'test', '--quiet', '--manifest-path', 'rust/Cargo.toml', '--lib', 'transaction_parity_tests::differential_publication', '--', '--ignored', '--exact'], {'AW_TRANSACTION_BASE': str(base)})
        if held is not None: os.close(held)
        assert result.returncode == 0, result.stdout + result.stderr
        actual = json.loads((base / 'results.json').read_text())[0]
        assert actual == expected, (mode, scenario, expected, actual)
        rs_snapshot = normalize(bundle_snapshot(case))
        assert rs_snapshot == py_snapshot, (mode, scenario, py_snapshot, rs_snapshot)
    print(f'transaction durability parity: {len(scenarios)} key/journal/lock rows; exact journal bytes and typed filesystem effects match')


def main() -> int:
    build = run(["cargo", "build", "--quiet", "--manifest-path", "rust/Cargo.toml"])
    if build.returncode:
        sys.stderr.write(build.stderr)
        return build.returncode
    try:
        with tempfile.TemporaryDirectory(prefix="agent-workbench-lifecycle-") as raw:
            base = Path(raw).resolve()
            for script in ("check_cursor_activation_parity.py", "check_cursor_activation_edges.py",
                           "check_cursor_activation_recovery.py", "check_claude_activation_parity.py",
                           "check_claude_activation_edges.py", "check_claude_activation_recovery.py",
                           "check_claude_native_contract.py", "check_activation_inertness.py",
                           "check_artifact_inspection_parity.py", "check_artifact_inventory.py"):
                activation = run([sys.executable, str(REPOSITORY / "scripts" / script)])
                assert activation.returncode == 0, activation.stdout + activation.stderr
                print(activation.stdout.strip())
            inherited_activation = run([sys.executable, str(REPOSITORY / "rust/src/activation_contract.py")])
            assert inherited_activation.returncode == 0, inherited_activation.stdout + inherited_activation.stderr
            print(inherited_activation.stdout.strip())
            path_recovery = run([sys.executable, str(REPOSITORY / "rust/src/transaction/path_contract.py")])
            assert path_recovery.returncode == 0, path_recovery.stdout + path_recovery.stderr
            print(path_recovery.stdout.splitlines()[-1])
            apply = run([sys.executable, str(REPOSITORY / "rust/src/transaction/apply_contract.py")])
            assert apply.returncode == 0, apply.stdout + apply.stderr
            print(apply.stdout.splitlines()[-1])
            recovery_order = run([sys.executable, str(REPOSITORY / "rust/src/transaction/recovery_order_contract.py")])
            assert recovery_order.returncode == 0, recovery_order.stdout + recovery_order.stderr
            print(recovery_order.stdout.splitlines()[-1])
            exception = run([sys.executable, str(REPOSITORY / "rust/src/transaction/state_parent_contract.py")])
            assert exception.returncode == 0, exception.stdout + exception.stderr
            print(exception.stdout.splitlines()[-1])
            cleanup_exception = run([sys.executable, str(REPOSITORY / "rust/src/transaction/stage_cleanup_contract.py")])
            assert cleanup_exception.returncode == 0, cleanup_exception.stdout + cleanup_exception.stderr
            print(cleanup_exception.stdout.splitlines()[-1])
            wire = run([sys.executable, str(REPOSITORY / "rust/src/transaction/wire_contract.py")])
            assert wire.returncode == 0, wire.stdout + wire.stderr
            print(wire.stdout.splitlines()[-1])
            authority = run([sys.executable, str(REPOSITORY / "rust/src/transaction/authority_contract.py")])
            assert authority.returncode == 0, authority.stdout + authority.stderr
            print(authority.stdout.splitlines()[-1])
            capture = run([sys.executable, str(REPOSITORY / "rust/src/transaction/capture_contract.py")])
            assert capture.returncode == 0, capture.stdout + capture.stderr
            print(capture.stdout.splitlines()[-1])
            recovery_persistence = run([sys.executable, str(REPOSITORY / "rust/src/transaction/recovery_persistence_contract.py")])
            assert recovery_persistence.returncode == 0, recovery_persistence.stdout + recovery_persistence.stderr
            print(recovery_persistence.stdout.splitlines()[-1])
            transaction_durable_rows(base / "transaction-durable")
            transaction_publication_rows(base / "transaction-publication")
            transaction_journal_rows(base / "transaction-journals")
            transaction_recovery_rows(base / "transaction-recovery")
            shared_document_rows(base / "shared-documents")
            shared_ownership_rows(base / "shared-ownership")
            shared_snapshot_rows(base / "shared-snapshots")
            shared_reference_rows(base / "shared-references")
            bundle_rows(base / "bundles")
            bundle_name_rows(base / "bundle-names")
            bundle_publication_rows(base / "bundle-publication")
            trust_contract_rows(base / "trust")
            trust_proof_rows(base / "trust-proofs")
            trust_inspection_rows(base / "trust-inspection")
            trust_cli_contract(base)
            state_wire_rows(base / "state-wire")
            state_v4_public_lifecycle(base)
            for target in TARGETS:
                default_destination(base, target)
                explicit_destination_expansion(base, target)
                install_status_uninstall(base, target)
                adoption_and_conflicts(base, target)
                relocation(base, target)
                v2_compatibility(base, target)
                state_rejection(base, target)
            destination_root_authorization(base)
            copy_race_rows(base)
            copied_safety_rows(base)
            claude_native_paths(base)
            cursor_project_paths(base)
            copied_agent_lifecycle(base)
            component_override(base)
            destination_precedence(base)
            shared_state_isolation(base)
    except AssertionError:
        import traceback
        traceback.print_exc()
        return 1
    print(
        "lifecycle parity: all six skills targets destinations/overrides/clean/adopt/"
        "conflict/relocation/v2/rejection/destination-root scenarios match; "
        "Cursor/Claude native paths and copied-file bytes/modes/digests/races match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
