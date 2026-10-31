from __future__ import annotations

import json
import os
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from agent_workbench import cli
from agent_workbench.codec import decode_catalog, decode_profile
from agent_workbench.diagnostics import ValidationReport
from agent_workbench.installation import (
    OperationKind,
    apply_plan,
    apply_uninstall,
    plan_install,
    plan_uninstall,
)
from agent_workbench.rendering import render
from agent_workbench.sources import update_source
from agent_workbench.state import load_state, state_path
from agent_workbench.validation import validate
from agent_workbench.workspace import Workspace


SKILL = """---
name: sample-skill
description: A sample skill.
---

# Sample
"""


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        package = root / "plugins" / "sample"
        (package / ".cursor-plugin").mkdir(parents=True)
        (package / ".claude-plugin").mkdir(parents=True)
        (package / "skills" / "sample-skill").mkdir(parents=True)
        (package / "agents").mkdir()
        (package / "rules").mkdir()
        (package / "hooks").mkdir()
        (package / "commands").mkdir()
        manifest = {
            "name": "sample",
            "version": "1.0.0",
            "description": "Sample plugin.",
        }
        for agent in ("cursor", "claude"):
            (package / f".{agent}-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (package / "skills" / "sample-skill" / "SKILL.md").write_text(SKILL, encoding="utf-8")
        (package / "agents" / "sample-agent.md").write_text(
            "---\nname: sample-agent\ndescription: A sample agent.\n---\n\n# Sample agent\n",
            encoding="utf-8",
        )
        (package / "rules" / "sample-rule.mdc").write_text(
            "---\ndescription: A sample rule.\nalwaysApply: true\n---\n\n# Sample rule\n",
            encoding="utf-8",
        )
        (package / "hooks" / "hooks.json").write_text(
            json.dumps({"version": 1, "hooks": {"stop": [{"command": "./stop.sh"}]}}),
            encoding="utf-8",
        )
        (package / "hooks" / "stop.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (package / "mcp.json").write_text(
            json.dumps({"mcpServers": {"sample-server": {"type": "http", "url": "https://example.invalid"}}}),
            encoding="utf-8",
        )
        (package / "commands" / "sample-command.md").write_text(
            "---\ndescription: A sample command.\n---\n\n# Sample command\n",
            encoding="utf-8",
        )
        (root / "catalog").mkdir()
        (root / "profiles").mkdir()
        catalog = {
            "schemaVersion": 2,
            "marketplace": {
                "id": "test-marketplace",
                "name": "test-marketplace",
                "owner": {"name": "Test"},
                "description": "Test marketplace.",
            },
            "sources": [{"id": "local", "kind": "local", "path": "plugins"}],
            "plugins": [
                {
                    "id": "sample",
                    "source": "local",
                    "artifacts": {
                        "cursor-plugin": {"path": "sample"},
                        "claude-plugin": {"path": "sample"},
                        "agent-skills": {"path": "sample/skills"},
                        "agents": {"path": "sample/agents"},
                        "rules": {"path": "sample/rules"},
                        "hooks": {"path": "sample/hooks"},
                        "mcp": {"path": "sample/mcp.json"},
                        "commands": {"path": "sample/commands"},
                    },
                }
            ],
        }
        profile = {
            "schemaVersion": 1,
            "id": "default",
            "plugins": {"include": ["*"], "exclude": []},
            "targets": [{"id": "agents"}],
        }
        (root / "catalog" / "plugins.json").write_text(json.dumps(catalog), encoding="utf-8")
        (root / "profiles" / "default.json").write_text(json.dumps(profile), encoding="utf-8")

    def add_profile(self, profile_id: str, include: list[str]) -> None:
        profile = {
            "schemaVersion": 1,
            "id": profile_id,
            "plugins": {"include": include, "exclude": []},
            "targets": [{"id": "agents"}],
        }
        (self.root / "profiles" / f"{profile_id}.json").write_text(json.dumps(profile), encoding="utf-8")


class CodecTests(unittest.TestCase):
    def test_rejects_duplicate_keys_and_boolean_schema_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            original = catalog_path.read_text(encoding="utf-8")
            catalog_path.write_text(original.replace('"schemaVersion": 2', '"schemaVersion": 1, "schemaVersion": 2', 1))
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                decode_catalog(catalog_path)

            profile_path = fixture.root / "profiles" / "default.json"
            profile = json.loads(profile_path.read_text())
            profile["schemaVersion"] = True
            profile_path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError, "schemaVersion"):
                decode_profile(profile_path)

    def test_rejects_unknown_fields_and_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["unknown"] = True
            catalog_path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                decode_catalog(catalog_path)

            data.pop("unknown")
            data["sources"][0]["path"] = "../outside"
            catalog_path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "contained"):
                decode_catalog(catalog_path)

    def test_rejects_empty_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["plugins"][0]["artifacts"] = {}
            catalog_path.write_text(json.dumps(data))

            with self.assertRaisesRegex(ValueError, "artifact"):
                decode_catalog(catalog_path)

    def test_curated_copy_requires_upstream_and_plugin_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["sources"][0]["kind"] = "curated-copy"
            catalog_path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "upstream"):
                decode_catalog(catalog_path)

            data["sources"][0]["upstream"] = "https://example.invalid/sample"
            catalog_path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "notice"):
                decode_catalog(catalog_path)


class RenderingTests(unittest.TestCase):
    def test_component_artifacts_are_validated_and_reported_in_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            workspace = Workspace.load(fixture.root)

            self.assertTrue(validate(workspace, check_generated=False).valid)
            render(workspace)
            lock = json.loads((fixture.root / "catalog" / "lock.json").read_text(encoding="utf-8"))

            self.assertEqual(2, lock["schemaVersion"])
            self.assertEqual(
                {
                    "agent-skills": 1,
                    "agents": 1,
                    "commands": 1,
                    "hooks": 1,
                    "mcp": 1,
                    "rules": 1,
                },
                lock["plugins"][0]["components"],
            )

    def test_component_artifacts_are_inert_for_skill_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")

            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", base / "installed")

            self.assertEqual(["skill:sample-skill"], [operation.key for operation in plan.operations])

    def test_invalid_component_tree_is_rejected_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            hooks = fixture.root / "plugins" / "sample" / "hooks" / "hooks.json"
            hooks.write_text('{"hooks":{"stop":[{"command":"touch should-not-exist"}],"stop":[]}}', encoding="utf-8")

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertIn("ARTIFACT_INVALID", {issue.code for issue in report.issues})
            self.assertFalse((fixture.root / "should-not-exist").exists())

    def test_component_artifacts_reject_symlinked_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            external = base / "external-agent.md"
            external.write_text(
                "---\nname: linked-agent\ndescription: External.\n---\n",
                encoding="utf-8",
            )
            (fixture.root / "plugins" / "sample" / "agents" / "linked-agent.md").symlink_to(external)

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertIn("ARTIFACT_INVALID", {issue.code for issue in report.issues})

    def test_component_artifacts_reject_malformed_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            agent = fixture.root / "plugins" / "sample" / "agents" / "sample-agent.md"
            agent.write_text("# Missing frontmatter\n", encoding="utf-8")

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertIn("ARTIFACT_INVALID", {issue.code for issue in report.issues})

    def test_native_manifest_must_not_be_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            manifest = fixture.root / "plugins" / "sample" / ".cursor-plugin" / "plugin.json"
            external = base / "external.json"
            external.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
            manifest.unlink()
            manifest.symlink_to(external)

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertFalse(report.valid)
            self.assertIn("CURSOR_MANIFEST_INVALID", {issue.code for issue in report.issues})

    def test_native_manifest_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            manifest = fixture.root / "plugins" / "sample" / ".cursor-plugin" / "plugin.json"
            content = manifest.read_text(encoding="utf-8")
            manifest.write_text(content.replace('"name": "sample"', '"name": "sample", "name": "sample"', 1))

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertFalse(report.valid)
            self.assertIn("CURSOR_MANIFEST_INVALID", {issue.code for issue in report.issues})

    def test_render_rejects_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            outside = base / "outside"
            outside.mkdir()
            (fixture.root / ".cursor-plugin").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlinked output"):
                render(Workspace.load(fixture.root))
            self.assertFalse((outside / "marketplace.json").exists())

    def test_render_parent_swap_does_not_write_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            parent = fixture.root / ".cursor-plugin"
            parent.mkdir()
            moved = fixture.root / ".cursor-plugin-original"
            outside = base / "outside"
            outside.mkdir()

            from agent_workbench import rendering

            original = rendering._write_generated
            swapped = False

            def swap_then_write(repository, output, content):
                nonlocal swapped
                if not swapped and output.parent.name == ".cursor-plugin":
                    parent.rename(moved)
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original(repository, output, content)

            with mock.patch.object(rendering, "_write_generated", swap_then_write):
                with self.assertRaisesRegex(ValueError, "non-directory parent"):
                    render(Workspace.load(fixture.root))

            self.assertFalse((outside / "marketplace.json").exists())
            self.assertFalse((outside / "marketplace.json").is_symlink())

    def test_validation_requires_manifest_description_without_render_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            manifest_path = fixture.root / "plugins" / "sample" / ".cursor-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.pop("description")
            manifest_path.write_text(json.dumps(manifest))

            report = validate(Workspace.load(fixture.root), check_generated=False)

            self.assertIn("CURSOR_MANIFEST_INVALID", {issue.code for issue in report.issues})

    def test_render_is_deterministic_and_validation_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            workspace = Workspace.load(fixture.root)
            written = render(workspace)
            self.assertEqual(3, len(written))
            self.assertEqual((), render(workspace, check=True))
            self.assertTrue(validate(workspace).valid)

            cursor = fixture.root / ".cursor-plugin" / "marketplace.json"
            cursor.write_text("{}\n")
            report = validate(workspace)
            self.assertFalse(report.valid)
            self.assertIn("GENERATED_FILE_STALE", {issue.code for issue in report.issues})

    def test_validation_checks_excluded_plugins_and_unused_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["sources"].append({"id": "unused", "kind": "local", "path": "missing-source"})
            data["plugins"].append(
                {
                    "id": "excluded",
                    "source": "local",
                    "artifacts": {"agent-skills": {"path": "missing-skills"}},
                }
            )
            catalog_path.write_text(json.dumps(data))
            profile_path = fixture.root / "profiles" / "default.json"
            profile = json.loads(profile_path.read_text())
            profile["plugins"]["include"] = ["sample"]
            profile_path.write_text(json.dumps(profile))

            report = validate(Workspace.load(fixture.root), check_generated=False)

            codes = {issue.code for issue in report.issues}
            self.assertIn("SOURCE_PATH_MISSING", codes)
            self.assertIn("ARTIFACT_PATH_MISSING", codes)

    def test_non_default_profiles_cannot_rewrite_canonical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.add_profile("pstack", [])
            fixture.add_profile("minimal", [])
            render(Workspace.load(fixture.root))
            outputs = [
                fixture.root / ".cursor-plugin" / "marketplace.json",
                fixture.root / ".claude-plugin" / "marketplace.json",
                fixture.root / "catalog" / "lock.json",
            ]
            canonical = {path: path.read_bytes() for path in outputs}

            for profile_id in ("pstack", "minimal"):
                with self.subTest(profile=profile_id):
                    with self.assertRaisesRegex(ValueError, "default profile"):
                        render(Workspace.load(fixture.root, profile_id))
                    self.assertEqual(canonical, {path: path.read_bytes() for path in outputs})

    def test_non_default_validation_uses_canonical_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.add_profile("minimal", [])
            render(Workspace.load(fixture.root))
            (fixture.root / ".cursor-plugin" / "marketplace.json").write_text("{}\n", encoding="utf-8")

            report = validate(Workspace.load(fixture.root, "minimal"))

            self.assertIn("GENERATED_FILE_STALE", {issue.code for issue in report.issues})


class InstallationTests(unittest.TestCase):
    def test_install_revalidates_skill_source_after_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "skills"
            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", destination)
            source = fixture.root / "plugins" / "sample" / "skills" / "sample-skill"
            original = base / "original-skill"
            source.rename(original)
            external = base / "external-skill"
            external.mkdir()
            (external / "SKILL.md").write_text(SKILL, encoding="utf-8")
            source.symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "skill source"):
                apply_plan(plan)
            self.assertFalse((destination / "sample-skill").exists())
            self.assertFalse((destination / "sample-skill").is_symlink())
    def test_profile_destination_is_used_for_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "profile-skills"
            profile_path = fixture.root / "profiles" / "default.json"
            profile = json.loads(profile_path.read_text())
            profile["targets"][0]["destination"] = str(destination)
            profile_path.write_text(json.dumps(profile))
            workspace = Workspace.load(fixture.root)
            state = base / "state"
            apply_plan(plan_install(workspace, "agents", state))

            plan = plan_uninstall(workspace, "agents", state)

            self.assertEqual(destination.resolve(), plan.destination)
            apply_uninstall(plan)
            self.assertFalse((destination / "sample-skill").exists())

    def test_skill_discovery_rejects_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            external = base / "external-skill"
            external.mkdir()
            (external / "SKILL.md").write_text(SKILL, encoding="utf-8")
            linked = fixture.root / "plugins" / "sample" / "skills" / "external"
            linked.symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink"):
                plan_install(Workspace.load(fixture.root), "agents", base / "state", base / "skills")

    def test_skill_discovery_rejects_nested_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            external = base / "external.txt"
            external.write_text("private", encoding="utf-8")
            nested = fixture.root / "plugins" / "sample" / "skills" / "sample-skill" / "external.txt"
            nested.symlink_to(external)

            with self.assertRaisesRegex(ValueError, "symlink"):
                plan_install(Workspace.load(fixture.root), "agents", base / "state", base / "skills")

    def test_destination_root_swap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            destination.mkdir(parents=True)
            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", destination)
            moved = base / "original-skills"
            destination.rename(moved)
            outside = base / "outside"
            outside.mkdir()
            destination.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(FileExistsError, "destination .*changed"):
                apply_plan(plan)
            self.assertFalse((outside / "sample-skill").exists())

    def test_missing_destination_parent_swap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            parent = base / "consumer"
            parent.mkdir()
            destination = parent / "skills"
            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", destination)
            moved = base / "original-consumer"
            parent.rename(moved)
            outside = base / "outside"
            outside.mkdir()
            parent.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(FileExistsError, "destination ancestor changed"):
                apply_plan(plan)
            self.assertFalse((outside / "skills" / "sample-skill").exists())
            self.assertFalse((outside / "skills" / "sample-skill").is_symlink())

    def test_destination_root_swap_after_open_does_not_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            destination.mkdir(parents=True)
            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", destination)
            moved = base / "original-skills"
            outside = base / "outside"
            outside.mkdir()

            from agent_workbench import installation

            original = installation._DestinationRoot.create_link
            swapped = False

            def swap_then_create(root, operation):
                nonlocal swapped
                if not swapped:
                    destination.rename(moved)
                    destination.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original(root, operation)

            with mock.patch.object(installation._DestinationRoot, "create_link", swap_then_create):
                with self.assertRaisesRegex(FileExistsError, "destination root changed"):
                    apply_plan(plan)

            self.assertFalse((outside / "sample-skill").exists())
            self.assertFalse((outside / "sample-skill").is_symlink())
            self.assertFalse((moved / "sample-skill").exists())
            self.assertFalse((moved / "sample-skill").is_symlink())

    def test_uninstall_root_swap_after_open_does_not_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            workspace = Workspace.load(fixture.root)
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(workspace, "agents", state, destination))
            plan = plan_uninstall(workspace, "agents", state, destination)
            moved = base / "original-skills"
            outside = base / "outside"
            outside.mkdir()
            source = fixture.root / "plugins" / "sample" / "skills" / "sample-skill"
            foreign = outside / "sample-skill"
            foreign.symlink_to(source, target_is_directory=True)

            from agent_workbench import installation

            original = installation._DestinationRoot.quarantine
            swapped = False

            def swap_then_quarantine(root, operation):
                nonlocal swapped
                if not swapped:
                    destination.rename(moved)
                    destination.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original(root, operation)

            with mock.patch.object(installation._DestinationRoot, "quarantine", swap_then_quarantine):
                with self.assertRaisesRegex(FileExistsError, "destination root changed"):
                    apply_uninstall(plan)

            self.assertTrue(foreign.is_symlink())
            self.assertTrue((moved / "sample-skill").is_symlink())

    def test_stale_plan_cannot_overwrite_newer_ownership_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            stale = plan_install(Workspace.load(fixture.root), "agents", state, destination)
            second = fixture.root / "plugins" / "sample" / "skills" / "second-skill"
            second.mkdir()
            (second / "SKILL.md").write_text(SKILL.replace("sample-skill", "second-skill"), encoding="utf-8")
            current = plan_install(Workspace.load(fixture.root), "agents", state, destination)
            apply_plan(current)

            with self.assertRaisesRegex(ValueError, "installation state changed"):
                apply_plan(stale)

            saved = load_state(state_path(state, "test-marketplace", "default", "agents"))
            assert saved is not None
            self.assertEqual({"skill:sample-skill", "skill:second-skill"}, set(saved.entries))
            self.assertTrue((destination / "second-skill").is_symlink())

    def test_empty_uninstall_with_missing_destination_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "missing" / "skills"
            plan = plan_uninstall(Workspace.load(fixture.root), "agents", base / "state", destination)

            apply_uninstall(plan)

            self.assertFalse(destination.exists())

    def test_installation_state_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, base / "skills"))
            path = state_path(state, "test-marketplace", "default", "agents")
            content = path.read_text(encoding="utf-8")
            path.write_text(content.replace('"schemaVersion": 3', '"schemaVersion": 3, "schemaVersion": 3', 1), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_state(path)

    def test_rejects_traversal_and_absolute_skill_names(self) -> None:
        for unsafe_name in (".", "..", "../escape", "/tmp/escape", "nested/name", "nested\\name"):
            with self.subTest(unsafe_name=unsafe_name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                fixture = Fixture(base / "repo")
                skill_file = fixture.root / "plugins" / "sample" / "skills" / "sample-skill" / "SKILL.md"
                skill_file.write_text(SKILL.replace("sample-skill", unsafe_name), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "safe filename"):
                    plan_install(Workspace.load(fixture.root), "agents", base / "state", base / "skills")

    def test_install_reinstall_conflict_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            workspace = Workspace.load(fixture.root)
            destination = base / "consumer" / "skills"
            state = base / "state"

            first = plan_install(workspace, "agents", state, destination)
            self.assertEqual([OperationKind.CREATE], [item.kind for item in first.operations])
            apply_plan(first)
            link = destination / "sample-skill"
            self.assertTrue(link.is_symlink())
            self.assertTrue((link / "SKILL.md").is_file())

            second = plan_install(workspace, "agents", state, destination)
            self.assertEqual([OperationKind.KEEP], [item.kind for item in second.operations])
            apply_plan(second)

            link.unlink()
            link.write_text("foreign")
            conflict = plan_install(workspace, "agents", state, destination)
            self.assertTrue(conflict.has_conflicts)
            self.assertEqual(OperationKind.CONFLICT, conflict.operations[0].kind)
            with self.assertRaisesRegex(ValueError, "conflicts"):
                apply_plan(conflict)

            link.unlink()
            source = fixture.root / "plugins" / "sample" / "skills" / "sample-skill"
            link.symlink_to(Path(os.path.relpath(source, destination)), target_is_directory=True)
            recovered = plan_install(workspace, "agents", state, destination)
            self.assertEqual(OperationKind.KEEP, recovered.operations[0].kind)

            uninstall = plan_uninstall(workspace, "agents", state, destination)
            self.assertEqual(OperationKind.REMOVE_OWNED, uninstall.operations[0].kind)
            apply_uninstall(uninstall)
            self.assertFalse(link.exists())
            self.assertFalse(link.is_symlink())

    def test_unmanaged_matching_link_requires_explicit_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            workspace = Workspace.load(fixture.root)
            destination = base / "consumer" / "skills"
            destination.mkdir(parents=True)
            source = fixture.root / "plugins" / "sample" / "skills" / "sample-skill"
            link = destination / "sample-skill"
            link.symlink_to(Path(os.path.relpath(source, destination)), target_is_directory=True)

            refused = plan_install(workspace, "agents", base / "state", destination)
            self.assertEqual(OperationKind.CONFLICT, refused.operations[0].kind)
            adopted = plan_install(workspace, "agents", base / "state", destination, adopt=True)
            self.assertEqual(OperationKind.ADOPT, adopted.operations[0].kind)
            apply_plan(adopted)
            self.assertEqual(OperationKind.KEEP, plan_install(workspace, "agents", base / "state", destination).operations[0].kind)

    def test_moved_checkout_repairs_only_owned_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "old-repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))

            new_root = base / "new-repo"
            fixture.root.rename(new_root)
            moved = Workspace.load(new_root)
            repair = plan_install(moved, "agents", state, destination)
            self.assertEqual(OperationKind.REPLACE_OWNED, repair.operations[0].kind)
            apply_plan(repair)
            self.assertTrue((destination / "sample-skill" / "SKILL.md").is_file())
            self.assertEqual(OperationKind.KEEP, plan_install(moved, "agents", state, destination).operations[0].kind)

    def test_install_revalidates_stale_create_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            plan = plan_install(Workspace.load(fixture.root), "agents", base / "state", destination)
            foreign = destination / "sample-skill"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("foreign", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                apply_plan(plan)

            self.assertEqual("foreign", foreign.read_text(encoding="utf-8"))

    def test_install_revalidates_stale_replace_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "old-repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            new_root = base / "new-repo"
            fixture.root.rename(new_root)
            plan = plan_install(Workspace.load(new_root), "agents", state, destination)
            self.assertEqual(OperationKind.REPLACE_OWNED, plan.operations[0].kind)
            link = destination / "sample-skill"
            link.unlink()
            foreign = base / "foreign"
            foreign.mkdir()
            link.symlink_to(foreign, target_is_directory=True)

            with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                apply_plan(plan)

            self.assertEqual(foreign.resolve(), link.resolve())

    def test_replace_quarantines_before_inspection_and_restores_swapped_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "old-repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            new_root = base / "new-repo"
            fixture.root.rename(new_root)
            plan = plan_install(Workspace.load(new_root), "agents", state, destination)
            link = destination / "sample-skill"
            real_rename = os.rename

            def swap_then_rename(source: str, target: str) -> None:
                Path(source).unlink()
                Path(source).write_text("foreign", encoding="utf-8")
                real_rename(source, target)

            with mock.patch("agent_workbench.installation.os.rename", side_effect=swap_then_rename):
                with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                    apply_plan(plan)

            self.assertFalse(link.is_symlink())
            self.assertEqual("foreign", link.read_text(encoding="utf-8"))

    def test_uninstall_revalidates_stale_remove_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            plan = plan_uninstall(Workspace.load(fixture.root), "agents", state, destination)
            link = destination / "sample-skill"
            link.unlink()
            foreign = base / "foreign"
            foreign.mkdir()
            link.symlink_to(foreign, target_is_directory=True)

            with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                apply_uninstall(plan)

            self.assertTrue(link.is_symlink())
            self.assertEqual(foreign.resolve(), link.resolve())

    def test_uninstall_quarantines_before_inspection_and_restores_swapped_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            plan = plan_uninstall(Workspace.load(fixture.root), "agents", state, destination)
            link = destination / "sample-skill"
            real_rename = os.rename
            swapped = False

            def swap_then_rename(source: str, target: str) -> None:
                nonlocal swapped
                if not swapped:
                    Path(source).unlink()
                    Path(source).write_text("foreign", encoding="utf-8")
                    swapped = True
                real_rename(source, target)

            with mock.patch("agent_workbench.installation.os.rename", side_effect=swap_then_rename):
                with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                    apply_uninstall(plan)

            self.assertFalse(link.is_symlink())
            self.assertEqual("foreign", link.read_text(encoding="utf-8"))

    def test_uninstall_restores_swapped_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            plan = plan_uninstall(Workspace.load(fixture.root), "agents", state, destination)
            link = destination / "sample-skill"
            real_rename = os.rename
            swapped = False

            def swap_directory_then_rename(source: str, target: str) -> None:
                nonlocal swapped
                if not swapped:
                    Path(source).unlink()
                    Path(source).mkdir()
                    (Path(source) / "foreign.txt").write_text("foreign", encoding="utf-8")
                    swapped = True
                real_rename(source, target)

            with mock.patch("agent_workbench.installation.os.rename", side_effect=swap_directory_then_rename):
                with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                    apply_uninstall(plan)

            self.assertTrue(link.is_dir())
            self.assertEqual("foreign", (link / "foreign.txt").read_text(encoding="utf-8"))

    def test_late_install_failure_keeps_completed_entries_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            second = fixture.root / "plugins" / "sample" / "skills" / "z-second"
            second.mkdir()
            (second / "SKILL.md").write_text(SKILL.replace("sample-skill", "z-second"), encoding="utf-8")
            workspace = Workspace.load(fixture.root)
            destination = base / "consumer" / "skills"
            state = base / "state"
            plan = plan_install(workspace, "agents", state, destination)
            destination.mkdir(parents=True)
            (destination / "z-second").write_text("foreign", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "changed since planning"):
                apply_plan(plan)

            file = state_path(state, "test-marketplace", "default", "agents")
            installed = load_state(file)
            self.assertIsNotNone(installed)
            assert installed is not None
            self.assertIn("skill:sample-skill", installed.entries)
            retry = plan_install(workspace, "agents", state, destination)
            kinds = {operation.key: operation.kind for operation in retry.operations}
            self.assertEqual(OperationKind.KEEP, kinds["skill:sample-skill"])
            self.assertEqual(OperationKind.CONFLICT, kinds["skill:z-second"])

    def test_custom_destination_requires_same_explicit_uninstall_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            workspace = Workspace.load(fixture.root)
            destination = base / "custom" / "skills"
            state = base / "state"
            apply_plan(plan_install(workspace, "agents", state, destination))

            with self.assertRaisesRegex(ValueError, "recorded destination root"):
                plan_uninstall(workspace, "agents", state)

            plan = plan_uninstall(workspace, "agents", state, destination)
            self.assertEqual(destination.resolve(), plan.destination)

    def test_uninstall_rejects_crafted_arbitrary_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            workspace = Workspace.load(fixture.root)
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(workspace, "agents", state, destination))
            victim = base / "outside" / "sample-skill"
            victim.parent.mkdir()
            victim.write_text("do not delete", encoding="utf-8")
            file = state_path(state, "test-marketplace", "default", "agents")
            data = json.loads(file.read_text(encoding="utf-8"))
            data["entries"]["skill:sample-skill"]["destination"] = str(victim)
            file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "installation root"):
                plan_uninstall(workspace, "agents", state, destination)

            self.assertEqual("do not delete", victim.read_text(encoding="utf-8"))

    def test_uninstall_rejects_crafted_state_metadata_key_and_source(self) -> None:
        mutations = (
            ("marketplace", "other", "metadata"),
            ("key", "skill:other", "entry key"),
            ("source", "../../victim", "source"),
        )
        for name, value, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                fixture = Fixture(base / "repo")
                workspace = Workspace.load(fixture.root)
                destination = base / "consumer" / "skills"
                state = base / "state"
                apply_plan(plan_install(workspace, "agents", state, destination))
                file = state_path(state, "test-marketplace", "default", "agents")
                data = json.loads(file.read_text(encoding="utf-8"))
                if name == "marketplace":
                    data["marketplace"] = value
                else:
                    data["entries"]["skill:sample-skill"][name] = value
                file.write_text(json.dumps(data), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, message):
                    plan_uninstall(workspace, "agents", state, destination)

    def test_plan_destination_stays_installation_root_when_removing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "consumer" / "skills"
            state = base / "state"
            apply_plan(plan_install(Workspace.load(fixture.root), "agents", state, destination))
            profile_path = fixture.root / "profiles" / "default.json"
            profile = json.loads(profile_path.read_text())
            profile["plugins"]["include"] = []
            profile_path.write_text(json.dumps(profile))

            plan = plan_install(Workspace.load(fixture.root), "agents", state, destination)

            self.assertEqual(destination.resolve(), plan.destination)
            self.assertEqual([OperationKind.REMOVE_OWNED], [item.kind for item in plan.operations])


class CliTests(unittest.TestCase):
    def test_workspace_accepts_injected_adapter_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            from agent_workbench.adapters.registry import AdapterRegistry

            custom = AdapterRegistry(renderers=(), install_targets={})
            workspace = Workspace.load(fixture.root, registry=custom)

            self.assertIs(custom, workspace.registry)

    def test_custom_registry_requires_explicit_artifact_inspectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            from agent_workbench.adapters.registry import AdapterRegistry

            custom = AdapterRegistry(renderers=(), install_targets={})
            report = validate(Workspace.load(fixture.root, registry=custom), check_generated=False)

            self.assertIn("ARTIFACT_KIND_UNKNOWN", {issue.code for issue in report.issues})

    def test_workspace_rejects_profile_path_escape_and_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            outside = base / "outside.json"
            outside.write_text((fixture.root / "profiles" / "default.json").read_text(), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "profile id"):
                Workspace.load(fixture.root, str(outside))

            fixture.add_profile("alias", ["sample"])
            alias = fixture.root / "profiles" / "alias.json"
            data = json.loads(alias.read_text())
            data["id"] = "default"
            alias.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "does not match"):
                Workspace.load(fixture.root, "alias")

    def test_compatibility_wrappers_are_import_safe(self) -> None:
        for name in ("scripts.render_marketplaces", "scripts.update", "scripts.validate"):
            with self.subTest(module=name):
                import_module(name)

    def test_install_conflict_returns_one_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            destination = base / "skills"
            destination.mkdir()
            (destination / "sample-skill").write_text("foreign", encoding="utf-8")

            with mock.patch("agent_workbench.cli.apply_plan") as apply:
                status = cli.main(
                    [
                        "--root",
                        str(fixture.root),
                        "install",
                        "--target",
                        "agents",
                        "--destination",
                        str(destination),
                        "--state-dir",
                        str(base / "state"),
                    ]
                )

            self.assertEqual(1, status)
            apply.assert_not_called()

    def test_uninstall_accepts_destination(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["uninstall", "--target", "agents", "--destination", "/tmp/skills"])
        self.assertEqual(Path("/tmp/skills"), args.destination)

    def test_compatibility_installer_requires_explicit_adopt(self) -> None:
        module = import_module("scripts.install_skills")
        fake_plan = mock.Mock(operations=(), has_conflicts=False)
        workspace = mock.Mock()
        with (
            mock.patch.object(module.Workspace, "load", return_value=workspace),
            mock.patch.object(module, "plan_install", return_value=fake_plan) as plan,
            mock.patch.object(module, "apply_plan"),
        ):
            with mock.patch.object(module.sys, "argv", ["install_skills.py", "--target", "agents", "--dry-run"]):
                self.assertEqual(0, module.main())
            self.assertFalse(plan.call_args.kwargs["adopt"])
            with mock.patch.object(module.sys, "argv", ["install_skills.py", "--target", "agents", "--dry-run", "--adopt"]):
                self.assertEqual(0, module.main())
            self.assertTrue(plan.call_args.kwargs["adopt"])


class SourceTests(unittest.TestCase):
    def test_generated_rollback_rejects_symlinked_parent_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            parent = fixture.root / ".cursor-plugin"
            parent.mkdir()
            output = parent / "marketplace.json"
            original_parent = fixture.root / ".cursor-plugin-original"
            parent.rename(original_parent)
            outside = base / "outside"
            outside.mkdir()
            parent.symlink_to(outside, target_is_directory=True)
            from agent_workbench.sources import _restore_generated

            with self.assertRaises((ValueError, OSError)):
                _restore_generated(fixture.root, {output: b"original"})

            self.assertFalse((outside / "marketplace.json").exists())

    def test_generated_rollback_rejects_symlinked_parent_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            parent = fixture.root / ".cursor-plugin"
            parent.mkdir()
            output = parent / "marketplace.json"
            original_parent = fixture.root / ".cursor-plugin-original"
            parent.rename(original_parent)
            outside = base / "outside"
            outside.mkdir()
            victim = outside / "marketplace.json"
            victim.write_text("foreign", encoding="utf-8")
            parent.symlink_to(outside, target_is_directory=True)
            from agent_workbench.sources import _restore_generated

            with self.assertRaises((ValueError, OSError)):
                _restore_generated(fixture.root, {output: None})

            self.assertEqual("foreign", victim.read_text(encoding="utf-8"))

    def test_update_rejects_option_like_branch_before_git_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["sources"][0].update(
                {
                    "kind": "git-submodule",
                    "upstream": "https://example.invalid/plugins.git",
                    "branch": "--upload-pack=touch /tmp/pwned",
                }
            )
            catalog_path.write_text(json.dumps(data))

            with mock.patch("agent_workbench.sources.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "branch"):
                    update_source(Workspace.load(fixture.root), "local")
            run.assert_not_called()

    def test_update_rejects_source_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = Fixture(base / "repo")
            outside = base / "outside"
            outside.mkdir()
            source = fixture.root / "plugins"
            source.rename(fixture.root / "real-plugins")
            source.symlink_to(outside, target_is_directory=True)
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["sources"][0].update(
                {
                    "kind": "git-submodule",
                    "upstream": "https://example.invalid/plugins.git",
                    "branch": "main",
                }
            )
            catalog_path.write_text(json.dumps(data))

            with mock.patch("agent_workbench.sources.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "escapes"):
                    update_source(Workspace.load(fixture.root), "local")
            run.assert_not_called()

    def test_update_uses_catalog_branch_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            catalog_path = fixture.root / "catalog" / "plugins.json"
            data = json.loads(catalog_path.read_text())
            data["sources"][0].update(
                {
                    "kind": "git-submodule",
                    "upstream": "https://example.invalid/plugins.git",
                    "branch": "reviewed-branch",
                }
            )
            catalog_path.write_text(json.dumps(data))
            workspace = Workspace.load(fixture.root)

            with (
                mock.patch("agent_workbench.sources._source_path", return_value=fixture.root / "plugins"),
                mock.patch("agent_workbench.sources._verify_submodule_registration"),
                mock.patch("agent_workbench.sources._generated_snapshot", return_value={}),
                mock.patch("agent_workbench.sources.subprocess.check_output", side_effect=["old\n", "new\n"]),
                mock.patch("agent_workbench.sources.subprocess.run") as run,
                mock.patch("agent_workbench.sources.Workspace.load", return_value=workspace),
                mock.patch("agent_workbench.sources.render"),
                mock.patch("agent_workbench.sources.validate", return_value=ValidationReport(())),
            ):
                result = update_source(workspace, "local")

            self.assertEqual("new", result.after)
            commands = [call.args[0] for call in run.call_args_list]
            source_path = str(fixture.root / "plugins")
            self.assertIn(
                [
                    "git",
                    "-C",
                    source_path,
                    "fetch",
                    "--no-tags",
                    "https://example.invalid/plugins.git",
                    "refs/heads/reviewed-branch",
                ],
                commands,
            )
            self.assertIn(["git", "-C", source_path, "checkout", "--detach", "FETCH_HEAD"], commands)

    def test_update_rejects_non_default_profile_before_running_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.add_profile("minimal", [])

            with mock.patch("agent_workbench.sources.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "default profile"):
                    update_source(Workspace.load(fixture.root, "minimal"), "local")

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
