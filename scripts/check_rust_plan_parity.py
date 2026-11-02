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


def main() -> int:
    build = run(["cargo", "build", "--quiet", "--manifest-path", "rust/Cargo.toml"])
    if build.returncode:
        sys.stderr.write(build.stderr)
        return build.returncode
    try:
        with tempfile.TemporaryDirectory(prefix="agent-workbench-lifecycle-") as raw:
            base = Path(raw).resolve()
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
