from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_workbench import Fixture
from agent_workbench import cli, installation
from agent_workbench.adapters.projections import CAPABILITIES, project_paths
from agent_workbench.artifacts import semantic_exports
from agent_workbench.codec import decode_profile
from agent_workbench.installation import OperationKind as Op, apply_plan, apply_uninstall, plan_install, plan_uninstall
from agent_workbench.model import ComponentKind as C, DirectorySymlink, FileCopy, TargetId
from agent_workbench.state import load_state, write_state
from agent_workbench.workspace import Workspace


def configure(fixture: Fixture, components: list[str] | None = None, version: int = 2,
              project: Path | None = None) -> Path:
    path = fixture.root / 'profiles/default.json'
    data = json.loads(path.read_text())
    data['schemaVersion'] = version
    data['targets'] = [{'id': target} for target in CAPABILITIES]
    for target in data['targets']:
        if components is not None:
            target['components'] = components
        if project is not None:
            target['projectRoot'] = str(project)
    path.write_text(json.dumps(data))
    return path


class ProfileComponentTests(unittest.TestCase):
    def test_v1_normalizes_to_skills_only(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            profile = decode_profile(configure(fixture, version=1))
            self.assertTrue(all(target.components == (C.SKILLS,) and target.project_root is None
                                for target in profile.targets))

    def test_v2_explicit_selection_and_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            project = fixture.root / 'consumer'
            profile = decode_profile(configure(fixture, ['agents', 'rules'], project=project))
            self.assertEqual(2, profile.schema_version)
            self.assertEqual((C.AGENTS, C.RULES), profile.targets[0].components)
            self.assertEqual(str(project), profile.targets[0].project_root)
            self.assertEqual((C.SKILLS,), decode_profile(configure(fixture)).targets[0].components)

    def test_strict_v2_and_unchanged_v1_fields(self):
        mutations = (
            (2, 'components', 'agents'), (2, 'components', ['unknown']),
            (2, 'components', ['agents', 'agents']), (2, 'components', [True]),
            (2, 'components', [None]), (2, 'components', None),
            (2, 'projectRoot', '../outside'), (2, 'projectRoot', '/tmp/../outside'),
            (2, 'projectRoot', ''), (2, 'projectRoot', '/tmp/\x00bad'),
            (2, 'projectRoot', '~definitely-no-such-user-xyz/project'),
            (2, 'trust', True), (1, 'components', ['skills']),
            (1, 'projectRoot', '/tmp/project'),
        )
        for version, field, value in mutations:
            with self.subTest(version=version, field=field, value=value), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                path = configure(fixture, version=version)
                data = json.loads(path.read_text())
                data['targets'][0][field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    decode_profile(path)

    def test_strict_versions_and_duplicate_json(self):
        for version in (True, 0, 3, '2', 2.0):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                path = configure(fixture, version=version)
                with self.assertRaises(ValueError):
                    decode_profile(path)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            path = configure(fixture)
            path.write_text(path.read_text().replace('"schemaVersion": 2', '"schemaVersion": 2, "schemaVersion": 2'))
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                decode_profile(path)


class ProjectionTests(unittest.TestCase):
    def test_exact_capability_matrix_and_inert_rejection(self):
        expected = {'agents': {C.SKILLS}, 'cursor': {C.SKILLS, C.AGENTS, C.RULES},
                    'claude': {C.SKILLS, C.AGENTS, C.COMMANDS},
                    'codex': {C.SKILLS}, 'hermes': {C.SKILLS}, 'grok': {C.SKILLS}}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture)
            workspace = Workspace.load(fixture.root)
            for target, supported in expected.items():
                self.assertEqual(supported, set(CAPABILITIES[TargetId(target)]))
                for component in C:
                    with self.subTest(target=target, component=component):
                        plan = plan_install(workspace, target, base / 'state', components=(component,),
                                            project_root=base / 'project')
                        self.assertEqual(component not in supported, plan.has_conflicts)
                        if plan.has_conflicts:
                            self.assertEqual('COMPONENT_UNSUPPORTED', plan.operations[0].diagnostic)
                            with self.assertRaisesRegex(ValueError, 'conflicts'):
                                apply_plan(plan)
                        else:
                            self.assertEqual(1, len(plan.operations))
                            self.assertEqual(component, plan.operations[0].component)
            self.assertFalse((base / 'project').exists())
            self.assertFalse((base / 'state').exists() and list((base / 'state').rglob('*.json')))

    def test_unsupported_empty_artifact_selection_is_not_silently_skipped(self):
        paths, conflicts = project_paths(
            TargetId('codex'), CAPABILITIES[TargetId('codex')],
            (C.AGENTS,), (), {C.SKILLS: Path('/skills')},
        )
        self.assertEqual((), paths)
        self.assertEqual('COMPONENT_UNSUPPORTED', conflicts[0].code)

    def test_registered_skill_directory_target_defaults_to_skills_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            profile_path = fixture.root / 'profiles/default.json'
            profile = json.loads(profile_path.read_text())
            profile['targets'] = [{'id': 'new-agent'}]
            profile_path.write_text(json.dumps(profile))
            from agent_workbench.adapters.registry import AdapterRegistry, build_registry
            from agent_workbench.adapters.skills import DestinationSpec, SkillDirectoryTarget

            built_in = build_registry()
            target = SkillDirectoryTarget(
                TargetId('new-agent'),
                DestinationSpec(None, str(base / 'new-agent'), ('skills',)),
            )
            registry = AdapterRegistry(
                renderers=built_in.renderers,
                install_targets={target.id: target},
                artifact_inspectors=built_in.artifact_inspectors,
            )
            workspace = Workspace.load(fixture.root, registry=registry)

            plan = plan_install(workspace, 'new-agent', base / 'state')

            self.assertFalse(plan.has_conflicts)
            self.assertEqual(['skill:sample-skill'], [operation.key for operation in plan.operations])

    def test_unsupported_malformed_artifact_still_returns_stable_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture)
            (fixture.root / 'plugins/sample/hooks/hooks.json').write_text('invalid JSON')
            plan = plan_install(Workspace.load(fixture.root), 'cursor', base / 'state',
                                components=(C.HOOKS,), project_root=base / 'project')
            self.assertEqual('COMPONENT_UNSUPPORTED', plan.operations[0].diagnostic)

    def test_cursor_rules_require_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture)
            plan = plan_install(Workspace.load(fixture.root), 'cursor', base / 'state',
                                base / 'skills', components=(C.RULES,))
            self.assertTrue(plan.has_conflicts)
            self.assertEqual('PROJECT_ROOT_REQUIRED', plan.operations[0].diagnostic)
            self.assertFalse((base / 'skills').exists())

    def test_claude_agents_and_legacy_commands_are_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture)
            workspace = Workspace.load(fixture.root)
            plan = plan_install(workspace, 'claude', base / 'state',
                                components=(C.AGENTS, C.COMMANDS), project_root=base / 'project')
            apply_plan(plan)
            for operation in plan.operations:
                self.assertEqual(operation.source.read_bytes(), operation.destination.read_bytes())
                self.assertFalse(operation.destination.is_symlink())
            apply_uninstall(plan_uninstall(workspace, 'claude', base / 'state', project_root=base / 'project'))
            self.assertTrue(all(not operation.destination.exists() for operation in plan.operations))

    def test_semantic_exports_cover_all_inventories(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            workspace = Workspace.load(fixture.root)
            plugin = workspace.resolved.plugins[0]
            exports = [export for kind, inspector in workspace.registry.artifact_inspectors.items()
                       if kind in plugin.artifact_paths
                       for export in semantic_exports(inspector.inspect(plugin))]
            self.assertEqual(set(C), {export.component for export in exports})
            self.assertTrue(all(export.source.exists() for export in exports))

    def test_real_pstack_project_agents_cursor_and_claude(self):
        repository = Path(__file__).resolve().parents[1]
        workspace = Workspace.load(repository, 'pstack')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            for target in ('cursor', 'claude'):
                with self.subTest(target=target):
                    plan = plan_install(workspace, target, base / 'state', components=(C.AGENTS,), project_root=base)
                    self.assertFalse(plan.has_conflicts)
                    self.assertGreater(len(plan.operations), 0)
                    self.assertTrue(all(operation.destination.parent == base / f'.{target}' / 'agents'
                                        and isinstance(operation.resource, FileCopy)
                                        and operation.source.suffix == '.md'
                                        for operation in plan.operations))
            self.assertFalse((base / '.cursor').exists())
            self.assertFalse((base / '.claude').exists())

    def test_skills_only_defaults_for_all_builtin_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture, version=1)
            workspace = Workspace.load(fixture.root)
            for target in CAPABILITIES:
                plan = plan_install(workspace, target, base / 'state', base / 'legacy-skills')
                self.assertEqual(['skill:sample-skill'], [item.key for item in plan.operations])
                self.assertEqual(base / 'legacy-skills' / 'sample-skill', plan.operations[0].destination)
                self.assertIsInstance(plan.operations[0].resource, DirectorySymlink)


class CopiedResourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.fixture = Fixture(self.base / 'repo')
        configure(self.fixture)
        self.workspace = Workspace.load(self.fixture.root)
        self.project = self.base / 'consumer'
        self.state = self.base / 'state'
        self.source = self.fixture.root / 'plugins/sample/agents/sample-agent.md'
        self.destination = self.project / '.cursor/agents/sample-agent.md'

    def plan(self, components=(C.AGENTS,), adopt=False):
        return plan_install(self.workspace, 'cursor', self.state, adopt=adopt,
                            components=components, project_root=self.project)

    def uninstall(self):
        return plan_uninstall(self.workspace, 'cursor', self.state, project_root=self.project)

    def test_create_keep_replace_remove_and_mixed_roots(self):
        plan = self.plan((C.SKILLS, C.AGENTS, C.RULES))
        self.assertFalse(plan.has_conflicts)
        self.assertEqual(3, len(plan.operations))
        apply_plan(plan)
        self.assertEqual(self.source.read_bytes(), self.destination.read_bytes())
        self.assertFalse(self.destination.is_symlink())
        self.assertTrue((self.project / '.cursor/skills/sample-skill').is_symlink())
        self.assertEqual({Op.KEEP}, {item.kind for item in self.plan((C.SKILLS, C.AGENTS, C.RULES)).operations})
        apply_plan(self.plan((C.SKILLS, C.AGENTS, C.RULES)))
        self.source.write_text(self.source.read_text() + '\nUpdated.\n')
        plan = self.plan((C.SKILLS, C.AGENTS, C.RULES))
        agent = next(item for item in plan.operations if item.component is C.AGENTS)
        self.assertEqual(Op.REPLACE_OWNED, agent.kind)
        apply_plan(plan)
        self.assertEqual(self.source.read_bytes(), self.destination.read_bytes())
        plan = self.plan((C.SKILLS,))
        self.assertEqual(2, sum(item.kind is Op.REMOVE_OWNED for item in plan.operations))
        apply_plan(plan)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.project / '.cursor/rules/sample-rule.mdc').exists())

    def test_exact_foreign_copy_requires_adoption(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(self.source.read_bytes())
        self.assertTrue(self.plan().has_conflicts)
        adopted = self.plan(adopt=True)
        self.assertEqual(Op.ADOPT, adopted.operations[0].kind)
        apply_plan(adopted)
        self.assertEqual(Op.KEEP, self.plan().operations[0].kind)

    def test_foreign_file_directory_and_symlink_are_conflicts_even_with_adopt(self):
        for kind in ('file', 'directory', 'symlink'):
            with self.subTest(kind=kind):
                self.destination.parent.mkdir(parents=True, exist_ok=True)
                if kind == 'file':
                    self.destination.write_text('foreign')
                elif kind == 'directory':
                    self.destination.mkdir()
                else:
                    self.destination.symlink_to(self.source)
                self.assertTrue(self.plan(adopt=True).has_conflicts)
                if kind == 'directory':
                    self.destination.rmdir()
                else:
                    self.destination.unlink()

    def test_modified_owned_install_and_uninstall_conflict(self):
        apply_plan(self.plan())
        self.destination.write_text(self.source.read_text() + '\nLocal edits.\n')
        local_edits = self.destination.read_text()
        self.assertTrue(self.plan(adopt=True).has_conflicts)
        self.assertTrue(self.uninstall().has_conflicts)
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            apply_uninstall(self.uninstall())
        self.assertEqual(local_edits, self.destination.read_text())
        # Even a later source that happens to match local edits cannot claim them.
        self.source.write_text(local_edits)
        self.assertTrue(self.plan(adopt=True).has_conflicts)

    def test_uninstall_removes_all_recorded_components_without_component_flags(self):
        apply_plan(self.plan((C.SKILLS, C.AGENTS, C.RULES)))
        uninstall = self.uninstall()
        self.assertEqual(3, len(uninstall.operations))
        apply_uninstall(uninstall)
        self.assertFalse(self.destination.exists())
        self.assertFalse(uninstall.state_file.exists())

    def test_changed_source_is_rejected_before_destination_creation(self):
        plan = self.plan()
        self.source.write_text(self.source.read_text() + '\nChanged after plan.\n')
        with self.assertRaisesRegex(ValueError, 'source changed since planning'):
            apply_plan(plan)
        self.assertFalse(self.project.exists())
        self.assertFalse(plan.state_file.exists())

    def test_symlinked_source_and_parent_after_planning_are_rejected(self):
        for parent_swap in (False, True):
            with self.subTest(parent_swap=parent_swap):
                plan = self.plan()
                original = self.source.parent if parent_swap else self.source
                saved = original.with_name(original.name + '-saved')
                original.rename(saved)
                original.symlink_to(saved, target_is_directory=parent_swap)
                with self.assertRaises((ValueError, OSError)):
                    apply_plan(plan)
                self.assertFalse(self.destination.exists())
                original.unlink()
                saved.rename(original)

    def test_stale_create_keep_adopt_replace_and_remove_reject_destination_changes(self):
        plan = self.plan()
        self.destination.parent.mkdir(parents=True)
        self.destination.write_text('foreign')
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_plan(plan)
        self.assertEqual('foreign', self.destination.read_text())
        self.destination.write_bytes(self.source.read_bytes())
        plan = self.plan(adopt=True)
        self.destination.write_text('foreign')
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_plan(plan)
        self.destination.unlink()
        apply_plan(self.plan())
        plan = self.plan()
        self.destination.write_text('foreign')
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_plan(plan)
        self.destination.write_bytes(self.source.read_bytes())
        self.source.write_text(self.source.read_text() + '\nUpdated.\n')
        plan = self.plan()
        self.assertEqual(Op.REPLACE_OWNED, plan.operations[0].kind)
        self.destination.write_text('foreign')
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_plan(plan)
        self.assertEqual('foreign', self.destination.read_text())
        old = load_state(plan.state_file).entries['agent:sample-agent.md'].resource.digest
        self.assertNotEqual(old, plan.operations[0].resource.digest)
        # Restore the owned bytes using the quarantined plan's old source content.
        self.destination.write_text(self.source.read_text().removesuffix('\nUpdated.\n'))
        removal = self.uninstall()
        self.destination.write_text('foreign')
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_uninstall(removal)
        self.assertEqual('foreign', self.destination.read_text())

    def test_state_write_failure_rolls_back_create_replace_and_remove(self):
        with mock.patch.object(installation, 'write_state', side_effect=OSError('state unavailable')):
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                apply_plan(self.plan())
        self.assertFalse(self.destination.exists())
        apply_plan(self.plan())
        old_bytes = self.destination.read_bytes()
        state_bytes = self.plan().state_file.read_bytes()
        self.source.write_text(self.source.read_text() + '\nUpdated.\n')
        with mock.patch.object(installation, 'write_state', side_effect=OSError('state unavailable')):
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                apply_plan(self.plan())
            self.assertEqual(old_bytes, self.destination.read_bytes())
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                apply_uninstall(self.uninstall())
        self.assertEqual(old_bytes, self.destination.read_bytes())
        self.assertEqual(state_bytes, self.plan().state_file.read_bytes())
        self.assertFalse(list(self.project.rglob('.agent-workbench-*')))

    def test_interrupted_component_removal_keeps_remaining_roots_readable(self):
        for uninstall in (False, True):
            with self.subTest(uninstall=uninstall):
                apply_plan(self.plan((C.SKILLS, C.AGENTS, C.RULES)))
                plan = self.uninstall() if uninstall else self.plan((C.SKILLS,))
                real_quarantine = installation._DestinationRoot.quarantine

                def fail_last(root, operation):
                    if operation.component is C.RULES:
                        raise OSError('interrupted')
                    return real_quarantine(root, operation)

                with mock.patch.object(installation._DestinationRoot, 'quarantine', fail_last):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        (apply_uninstall if uninstall else apply_plan)(plan)
                saved = load_state(plan.state_file)
                self.assertEqual({C.SKILLS, C.RULES}, {entry.component for entry in saved.entries.values()})
                self.assertIn(C.RULES, saved.destination_roots)
                apply_uninstall(self.uninstall())
                self.assertFalse(plan.state_file.exists())

    def test_adapter_directory_symlink_is_rejected_during_planning(self):
        self.project.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.project / '.cursor').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan()
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan((C.SKILLS,))
        self.assertFalse(list(outside.iterdir()))

    def test_multiple_roots_cannot_authorize_crafted_state_destination(self):
        apply_plan(self.plan((C.SKILLS, C.AGENTS, C.RULES)))
        file = self.plan().state_file
        saved = file.read_bytes()
        for redirect_root in (False, True):
            with self.subTest(redirect_root=redirect_root):
                data = json.loads(saved)
                victim = self.base / 'outside/sample-agent.md'
                victim.parent.mkdir(exist_ok=True)
                victim.write_text('preserve')
                data['entries']['agent:sample-agent.md']['destination'] = str(victim)
                if redirect_root:
                    data['destinationRoots']['agents'] = str(victim.parent)
                file.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'root'):
                    self.uninstall()
                self.assertEqual('preserve', victim.read_text())
        file.write_bytes(saved)

    def test_missing_secondary_parent_and_existing_root_swaps_do_not_escape(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.project.mkdir(exist_ok=True)
                parent = self.project / '.cursor'
                if existing:
                    (parent / 'agents').mkdir(parents=True)
                plan = self.plan()
                original = parent if existing else self.project
                saved = original.with_name(original.name + '-saved')
                original.rename(saved)
                outside = self.base / ('outside-' + str(existing))
                outside.mkdir()
                original.symlink_to(outside, target_is_directory=True)
                with self.assertRaises((FileExistsError, OSError)):
                    apply_plan(plan)
                self.assertFalse(list(outside.iterdir()))
                original.unlink()
                saved.rename(original)

    def test_secondary_root_swap_after_open_rolls_back_inside_pinned_directory(self):
        self.destination.parent.mkdir(parents=True)
        plan = self.plan()
        moved = self.project / '.cursor/agents-original'
        outside = self.base / 'outside'
        outside.mkdir()
        real_create = installation._DestinationRoot.create

        def swap_then_create(root, operation, content):
            root.path.rename(moved)
            root.path.symlink_to(outside, target_is_directory=True)
            real_create(root, operation, content)

        with mock.patch.object(installation._DestinationRoot, 'create', swap_then_create):
            with self.assertRaisesRegex(FileExistsError, 'destination root changed'):
                apply_plan(plan)
        self.assertFalse(list(outside.iterdir()))
        self.assertFalse(list(moved.iterdir()))

    def test_stale_state_plan_is_rejected_for_copies(self):
        stale = self.plan()
        apply_plan(self.plan())
        with self.assertRaisesRegex(ValueError, 'state changed since planning'):
            apply_plan(stale)

    def test_profile_v2_selection_is_used_by_cli_and_json_identifies_resources(self):
        configure(self.fixture, ['agents'], project=self.project)
        output = io.StringIO()
        args = ['--root', str(self.fixture.root), 'install', '--target', 'cursor',
                '--state-dir', str(self.state), '--json']
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, cli.main(args))
        operation = json.loads(output.getvalue())[0]
        self.assertEqual('agents', operation['component'])
        self.assertEqual('file-copy', operation['resourceKind'])
        self.assertEqual(64, len(operation['digest']))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['--root', str(self.fixture.root), 'uninstall',
                '--target', 'cursor', '--state-dir', str(self.state)]))
        self.assertFalse(self.destination.exists())

    def test_status_json_reports_kept_copies(self):
        apply_plan(self.plan())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main(['--root', str(self.fixture.root), 'status', '--target', 'cursor',
                '--state-dir', str(self.state), '--project-root', str(self.project),
                '--component', 'agents', '--json'])
        self.assertEqual(0, result)
        operation = json.loads(output.getvalue())[0]
        self.assertEqual(('keep', 'agents', 'file-copy'),
                         (operation['kind'], operation['component'], operation['resourceKind']))

    def test_repeatable_component_overrides_profile_and_destination_remains_skills(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main(['--root', str(self.fixture.root), 'plan', '--target', 'cursor',
                '--state-dir', str(self.state), '--project-root', str(self.project),
                '--destination', str(self.base / 'custom-skills'), '--component', 'skills',
                '--component', 'agents', '--json'])
        self.assertEqual(0, result)
        operations = json.loads(output.getvalue())
        self.assertEqual({'skills', 'agents'}, {item['component'] for item in operations})
        skill = next(item for item in operations if item['component'] == 'skills')
        self.assertEqual(str(self.base / 'custom-skills/sample-skill'), skill['destination'])
        agent = next(item for item in operations if item['component'] == 'agents')
        self.assertEqual(str(self.destination), agent['destination'])


class StateCompatibilityTests(unittest.TestCase):
    def test_v2_remains_uninstallable_and_successful_install_migrates_v3(self):
        for migrate in (False, True):
            with self.subTest(migrate=migrate), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                fixture = Fixture(base / 'repo')
                workspace = Workspace.load(fixture.root)
                plan = plan_install(workspace, 'agents', base / 'state', base / 'skills')
                apply_plan(plan)
                legacy = replace(plan.next_state, schema_version=2, destination_roots={})
                write_state(plan.state_file, legacy)
                self.assertEqual(2, load_state(plan.state_file).schema_version)
                if migrate:
                    apply_plan(plan_install(workspace, 'agents', base / 'state', base / 'skills'))
                    migrated = load_state(plan.state_file)
                    self.assertEqual(3, migrated.schema_version)
                    self.assertIsInstance(migrated.entries['skill:sample-skill'].resource, DirectorySymlink)
                apply_uninstall(plan_uninstall(workspace, 'agents', base / 'state', base / 'skills'))
                self.assertFalse((base / 'skills/sample-skill').is_symlink())
                self.assertFalse(plan.state_file.exists())

    def test_interrupted_checkout_migration_preserves_each_link_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'old-repo')
            second = fixture.root / 'plugins/sample/skills/z-second'
            second.mkdir()
            from test_workbench import SKILL
            (second / 'SKILL.md').write_text(SKILL.replace('sample-skill', 'z-second'))
            workspace = Workspace.load(fixture.root)
            plan = plan_install(workspace, 'agents', base / 'state', base / 'skills')
            apply_plan(plan)
            write_state(plan.state_file, replace(plan.next_state, schema_version=2, destination_roots={}))
            moved = base / 'new-repo'
            fixture.root.rename(moved)
            workspace = Workspace.load(moved)
            repair = plan_install(workspace, 'agents', base / 'state', base / 'skills')
            real_create = installation._DestinationRoot.create

            def fail_last(root, operation, content):
                if operation.key == 'skill:z-second':
                    raise OSError('interrupted')
                real_create(root, operation, content)

            with mock.patch.object(installation._DestinationRoot, 'create', fail_last):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    apply_plan(repair)
            retry = plan_install(workspace, 'agents', base / 'state', base / 'skills')
            self.assertEqual([Op.KEEP, Op.REPLACE_OWNED], [operation.kind for operation in retry.operations])
            apply_uninstall(plan_uninstall(workspace, 'agents', base / 'state', base / 'skills'))
            self.assertFalse(any((base / 'skills').iterdir()))

    def test_strict_v3_resource_tags_digests_and_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            fixture = Fixture(base / 'repo')
            configure(fixture)
            plan = plan_install(Workspace.load(fixture.root), 'cursor', base / 'state',
                                components=(C.AGENTS,), project_root=base / 'project')
            apply_plan(plan)
            original = json.loads(plan.state_file.read_text())
            mutations = [
                lambda data: data.update(schemaVersion=True),
                lambda data: data.update(extra=True),
                lambda data: data['entries']['agent:sample-agent.md'].update(resourceKind='shared-document'),
                lambda data: data['entries']['agent:sample-agent.md'].update(digest='bad'),
                lambda data: data['entries']['agent:sample-agent.md'].pop('digest'),
                lambda data: data['entries']['agent:sample-agent.md'].update(component='hooks'),
                lambda data: data['entries']['agent:sample-agent.md'].update(component='skills'),
                lambda data: data['entries']['agent:sample-agent.md'].update(extra=True),
                lambda data: data['entries']['agent:sample-agent.md'].update(sourceRepository='../outside'),
                lambda data: data['destinationRoots'].pop('agents'),
                lambda data: data['destinationRoots'].update(unknown='/tmp/outside'),
                lambda data: data['destinationRoots'].update(skills='/tmp/outside'),
            ]
            for mutation in mutations:
                data = json.loads(json.dumps(original))
                mutation(data)
                plan.state_file.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    load_state(plan.state_file)
