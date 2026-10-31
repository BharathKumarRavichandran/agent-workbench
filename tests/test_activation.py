from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_workbench import Fixture
from test_components import configure
from agent_workbench import cli, installation, shared_config, transactions
from agent_workbench.installation import plan_install, apply_plan, plan_uninstall, apply_uninstall, OperationKind as Op
from agent_workbench.model import ComponentKind as C
from agent_workbench.state import load_state
from agent_workbench.workspace import Workspace


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.fixture = Fixture(self.base / 'repo')
        configure(self.fixture)
        self.workspace = Workspace.load(self.fixture.root)
        self.project = self.base / 'project'
        self.state = self.base / 'state'
        self.hooks = self.fixture.root / 'plugins/sample/hooks'
        (self.hooks / 'stop.sh').chmod(0o755)
        self.bundle = self.project / '.cursor/.agent-workbench-hooks/sample'
        self.hook_config = self.project / '.cursor/hooks.json'
        self.mcp_config = self.project / '.cursor/mcp.json'

    def plan(self, components=(C.HOOKS, C.MCP), trust=(), adopt=False):
        return plan_install(self.workspace, 'cursor', self.state, components=components,
                            project_root=self.project, trust=trust, adopt=adopt)

    def trusted(self, components=(C.HOOKS, C.MCP), adopt=False):
        tokens = tuple({item.trust_token for item in self.plan(components, adopt=adopt).all_operations if item.trust_token})
        plan = self.plan(components, tokens, adopt)
        self.assertFalse(plan.has_conflicts, [item.as_dict() for item in plan.all_operations])
        return plan

    def uninstall(self):
        return plan_uninstall(self.workspace, 'cursor', self.state, project_root=self.project)

    def test_trust_copy_rewrite_modes_noop_and_inert_lifecycle(self):
        (self.hooks / 'stop.sh').write_text('#!/bin/sh\ntouch ' + str(self.base / 'EXECUTED') + '\n')
        self.assertTrue(self.plan().has_conflicts)
        self.assertFalse(self.project.exists())
        with mock.patch('subprocess.Popen', side_effect=AssertionError('payload executed')):
            plan = self.trusted()
            apply_plan(plan)
            self.assertEqual(4, load_state(plan.state_file).schema_version)
            self.assertFalse(self.bundle.is_symlink())
            self.assertEqual((self.hooks / 'stop.sh').read_bytes(), (self.bundle / 'hooks/stop.sh').read_bytes())
            self.assertEqual(0o755, (self.bundle / 'hooks/stop.sh').stat().st_mode & 0o777)
            entry = json.loads(self.hook_config.read_text())['hooks']['stop'][0]
            self.assertEqual(str(self.bundle / 'hooks/stop.sh'), entry['command'])
            before = self.hook_config.stat().st_mtime_ns
            self.assertEqual({Op.KEEP}, {item.kind for item in self.plan().all_operations})
            apply_plan(self.plan())
            self.assertEqual(before, self.hook_config.stat().st_mtime_ns)
            apply_uninstall(self.uninstall())
        self.assertFalse((self.base / 'EXECUTED').exists())
        self.assertFalse(self.bundle.exists())
        self.assertEqual([], json.loads(self.hook_config.read_text())['hooks']['stop'])
        self.assertEqual({}, json.loads(self.mcp_config.read_text())['mcpServers'])

    def test_changed_content_requires_new_grant(self):
        old = self.trusted()
        apply_plan(old)
        (self.hooks / 'stop.sh').write_text('#!/bin/sh\nexit 1\n')
        old_tokens = tuple(item.trust_token for item in old.all_operations if item.trust_token)
        self.assertTrue(self.plan(trust=old_tokens).has_conflicts)
        apply_plan(self.trusted())
        self.assertEqual({Op.KEEP}, {item.kind for item in self.plan().all_operations})
        source = self.fixture.root / 'plugins/sample/mcp.json'
        source.write_text(source.read_text() + '\n')
        self.assertTrue(self.plan().has_conflicts)

    def test_merge_foreign_and_uninstall_only_owned(self):
        self.hook_config.parent.mkdir(parents=True)
        foreign_hook = {'command': 'foreign-command', 'matcher': 'keep'}
        self.hook_config.write_text(json.dumps({'version': 1, 'other': True, 'hooks': {'stop': [foreign_hook], 'otherEvent': []}}))
        foreign_mcp = {'type': 'http', 'url': 'https://foreign.invalid', 'headers': {'key': 'SECRET'}}
        self.mcp_config.write_text(json.dumps({'other': 42, 'mcpServers': {'foreign': foreign_mcp}}))
        plan = self.trusted()
        self.assertNotIn('SECRET', json.dumps([item.as_dict() for item in plan.all_operations]))
        apply_plan(plan)
        apply_uninstall(self.uninstall())
        self.assertEqual({'version': 1, 'other': True, 'hooks': {'stop': [foreign_hook], 'otherEvent': []}}, json.loads(self.hook_config.read_text()))
        self.assertEqual({'other': 42, 'mcpServers': {'foreign': foreign_mcp}}, json.loads(self.mcp_config.read_text()))

    def test_uninstall_blocks_foreign_hook_referencing_managed_bundle(self):
        apply_plan(self.trusted((C.HOOKS,)))
        data = json.loads(self.hook_config.read_text())
        foreign = {'command': str(self.bundle / 'hooks/stop.sh'), 'matcher': 'foreign'}
        data['hooks']['stop'].append(foreign)
        self.hook_config.write_text(json.dumps(data))

        plan = self.uninstall()

        self.assertTrue(plan.has_conflicts)
        conflict = next(item for item in plan.operations if item.key == 'hook:sample')
        self.assertEqual('HOOK_BUNDLE_REFERENCED', conflict.diagnostic)
        self.assertTrue(self.bundle.exists())
        self.assertEqual(foreign, json.loads(self.hook_config.read_text())['hooks']['stop'][-1])

    def test_collision_and_exact_adoption(self):
        self.mcp_config.parent.mkdir(parents=True)
        value = json.loads((self.fixture.root / 'plugins/sample/mcp.json').read_text())
        self.mcp_config.write_text(json.dumps(value))
        self.assertTrue(self.plan((C.MCP,)).has_conflicts)
        apply_plan(self.trusted((C.MCP,), adopt=True))
        self.assertEqual(Op.KEEP, self.plan((C.MCP,)).documents[0].operations[0].kind)
        value['mcpServers']['sample-server']['url'] = 'changed'
        self.mcp_config.write_text(json.dumps(value))
        self.assertTrue(self.plan((C.MCP,)).has_conflicts)
        self.assertTrue(self.uninstall().has_conflicts)

    def test_hook_adoption_duplicates_and_changed_owned_slot(self):
        plan = self.trusted((C.HOOKS,))
        self.hook_config.parent.mkdir(parents=True)
        self.hook_config.write_bytes(plan.documents[0].after)
        self.assertTrue(self.plan((C.HOOKS,)).has_conflicts)
        # Config matching isn't sufficient for adopting a missing bundle: install
        # creates the bundle before recording the exact config entry.
        apply_plan(self.trusted((C.HOOKS,), adopt=True))
        data = json.loads(self.hook_config.read_text())
        data['hooks']['stop'].append(data['hooks']['stop'][0])
        self.hook_config.write_text(json.dumps(data))
        self.assertTrue(self.plan((C.HOOKS,), adopt=True).has_conflicts)
        self.assertTrue(self.uninstall().has_conflicts)
        data['hooks']['stop'] = [{'command': 'modified'}]
        self.hook_config.write_text(json.dumps(data))
        self.assertTrue(self.uninstall().has_conflicts)

    def test_modified_bundle_conflicts_and_source_swaps(self):
        apply_plan(self.trusted())
        copied = self.bundle / 'hooks/stop.sh'
        copied.write_text('local change')
        self.assertTrue(self.plan().has_conflicts)
        self.assertTrue(self.uninstall().has_conflicts)
        copied.write_bytes((self.hooks / 'stop.sh').read_bytes())
        copied.chmod(0o755)
        stale = self.plan()
        saved = self.hooks.with_name('hooks-saved')
        self.hooks.rename(saved)
        self.hooks.symlink_to(saved, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            apply_plan(stale)
        self.assertTrue(self.bundle.exists())

    def test_changed_source_after_plan(self):
        plan = self.trusted()
        (self.hooks / 'stop.sh').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            apply_plan(plan)
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.hook_config.exists())

    def test_stale_document_plan(self):
        plan = self.trusted()
        self.hook_config.parent.mkdir(parents=True)
        self.hook_config.write_text('{"foreign": true}')
        with self.assertRaisesRegex(ValueError, 'document changed since planning'):
            apply_plan(plan)
        self.assertFalse(self.bundle.exists())
        self.assertEqual({'foreign': True}, json.loads(self.hook_config.read_text()))

    def test_duplicate_keys_symlinks_special_documents(self):
        self.hook_config.parent.mkdir(parents=True)
        self.hook_config.write_text('{"hooks":{}, "hooks":{}}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            self.plan()
        self.hook_config.unlink()
        self.hook_config.symlink_to(self.hooks / 'hooks.json')
        with self.assertRaises(OSError):
            self.plan()
        self.hook_config.unlink()
        os.mkfifo(self.hook_config)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.plan()

    def test_local_escape_rejected_and_plugin_root_rewriting(self):
        for command in ('./hooks/../../escape.sh', '${CURSOR_PLUGIN_ROOT}/../outside.sh', '../escape.sh', '/tmp/evil.sh'):
            with self.subTest(command=command):
                (self.hooks / 'hooks.json').write_text(json.dumps({'version': 1, 'hooks': {'stop': [{'command': command}]}}))
                with self.assertRaisesRegex(ValueError, 'escapes'):
                    self.plan()
        for command in ('sh ./hooks/stop.sh --hello', 'sh "${CURSOR_PLUGIN_ROOT}/hooks/stop.sh" --hello'):
            with self.subTest(command=command):
                (self.hooks / 'hooks.json').write_text(json.dumps({'version': 1, 'hooks': {'stop': [{'command': command, 'loop_limit': None}]}}))
                plan = self.trusted((C.HOOKS,))
                entry = json.loads(plan.documents[0].after)['hooks']['stop'][0]
                self.assertIn(str(self.bundle / 'hooks/stop.sh'), entry['command'])
                self.assertTrue(entry['command'].endswith(' --hello'))
                self.assertIsNone(entry['loop_limit'])

    def test_user_and_project_paths_and_cli_tokens(self):
        with mock.patch('pathlib.Path.home', return_value=self.base / 'home'), mock.patch.dict(os.environ, {'HOME': str(self.base / 'home')}):
            # expanduser uses HOME; no real ~/.cursor paths are touched.
            plan = plan_install(Workspace.load(self.fixture.root), 'cursor', self.state, components=(C.HOOKS, C.MCP))
            self.assertEqual({self.base / 'home/.cursor/hooks.json', self.base / 'home/.cursor/mcp.json'}, {item.before.path for item in plan.documents if not item.before.path.name.startswith('.')})
        output = io.StringIO()
        args = ['--root', str(self.fixture.root), 'trust', '--target', 'cursor', '--component', 'hooks', '--project-root', str(self.project), '--state-dir', str(self.state), '--json']
        with contextlib.redirect_stdout(output):
            self.assertEqual(1, cli.main(args))
        operations = json.loads(output.getvalue())
        token = next(item['trustToken'] for item in operations if item.get('trustToken'))
        self.assertFalse(self.bundle.exists())
        args[2] = 'install'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(args + ['--trust', token]))

    def test_document_lock_contention(self):
        import fcntl
        plan = self.trusted()
        self.hook_config.parent.mkdir(parents=True)
        with (self.hook_config.parent / '.agent-workbench.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FileExistsError, 'locked'):
                apply_plan(plan)
        self.assertFalse(self.bundle.exists())

    def test_state_write_failure_rolls_back_every_activation(self):
        plan = self.trusted()
        with mock.patch.object(installation, 'write_state', side_effect=OSError('state unavailable')):
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                apply_plan(plan)
        self.assertFalse(self.hook_config.exists())
        self.assertFalse(self.mcp_config.exists())
        self.assertFalse(self.bundle.exists())
        self.assertFalse(plan.state_file.exists())
        apply_plan(self.trusted())
        before = self.hook_config.read_bytes()
        with mock.patch.object(installation, 'remove_state', side_effect=OSError('state unavailable')):
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                apply_uninstall(self.uninstall())
        self.assertEqual(before, self.hook_config.read_bytes())
        self.assertTrue(self.bundle.exists())

    def test_path_write_failure_and_crash_journal_recovery(self):
        plan = self.trusted()
        with mock.patch.object(installation._DestinationRoot, 'create', side_effect=OSError('path unavailable')):
            with self.assertRaisesRegex(OSError, 'path unavailable'):
                apply_plan(plan)
        self.assertFalse(self.hook_config.exists())
        real = shared_config.compare_replace_document
        def crash(expected, content, fd, quarantine):
            path = expected.path
            result = real(expected, content, fd, quarantine)
            if path.name == 'hooks.json' and content and b'command' in content:
                raise KeyboardInterrupt('crash after activation')
            return result
        with mock.patch.object(shared_config, 'compare_replace_document', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(self.trusted())
        self.assertTrue(self.hook_config.exists())
        self.assertTrue(self.bundle.exists())
        self.assertTrue((self.hook_config.parent / '.agent-workbench-transaction.json').exists())
        self.plan()  # repairs the pending transaction before planning
        self.assertFalse(self.hook_config.exists())
        self.assertFalse(self.bundle.exists())
        self.assertFalse((self.hook_config.parent / '.agent-workbench-transaction.json').exists())

    def test_config_write_failure_preserves_recoverable_journal(self):
        real = shared_config.compare_replace_document
        def failure(expected, content, fd, quarantine):
            path = expected.path
            if path.name == 'mcp.json':
                raise OSError('config unavailable')
            return real(expected, content, fd, quarantine)
        with mock.patch.object(shared_config, 'compare_replace_document', failure):
            with self.assertRaisesRegex(OSError, 'config unavailable'):
                apply_plan(self.trusted())
        self.plan()
        self.assertFalse(self.hook_config.exists())
        self.assertFalse(self.bundle.exists())

    def test_empty_hook_bundle_is_still_trusted_and_uninstallable(self):
        (self.hooks / 'hooks.json').write_text('{"version":1,"hooks":{}}')
        apply_plan(self.trusted((C.HOOKS,)))
        self.assertTrue(self.bundle.exists())
        apply_uninstall(self.uninstall())
        self.assertFalse(self.bundle.exists())

    def test_two_profiles_share_document_without_sharing_owned_slots(self):
        import shutil
        second = self.fixture.root / 'plugins/second'
        shutil.copytree(self.hooks.parent, second)
        (second / 'mcp.json').write_text('{"mcpServers":{"second-server":{"command":"never-run-this"}}}')
        catalog_path = self.fixture.root / 'catalog/plugins.json'
        catalog = json.loads(catalog_path.read_text())
        plugin = json.loads(json.dumps(catalog['plugins'][0]))
        plugin['id'] = 'second'
        for artifact in plugin['artifacts'].values():
            artifact['path'] = artifact['path'].replace('sample', 'second')
        catalog['plugins'].append(plugin)
        catalog_path.write_text(json.dumps(catalog))
        profile_path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(profile_path.read_text())
        profile['plugins']['include'] = ['sample']
        profile_path.write_text(json.dumps(profile))
        profile['id'] = 'second'
        profile['plugins']['include'] = ['second']
        (profile_path.parent / 'second.json').write_text(json.dumps(profile))
        self.workspace = Workspace.load(self.fixture.root)
        other = Workspace.load(self.fixture.root, 'second')
        apply_plan(self.trusted())
        plan = plan_install(other, 'cursor', self.state, components=(C.HOOKS, C.MCP), project_root=self.project)
        tokens = tuple(item.trust_token for item in plan.all_operations if item.trust_token)
        plan = plan_install(other, 'cursor', self.state, components=(C.HOOKS, C.MCP), project_root=self.project, trust=tokens)
        self.assertFalse(plan.has_conflicts)
        apply_plan(plan)
        self.assertEqual(2, len(json.loads(self.hook_config.read_text())['hooks']['stop']))
        self.assertEqual({'sample-server', 'second-server'}, set(json.loads(self.mcp_config.read_text())['mcpServers']))
        apply_uninstall(self.uninstall())
        self.assertEqual(1, len(json.loads(self.hook_config.read_text())['hooks']['stop']))
        self.assertEqual({'second-server'}, set(json.loads(self.mcp_config.read_text())['mcpServers']))
        apply_uninstall(plan_uninstall(other, 'cursor', self.state, project_root=self.project))
        self.assertFalse((self.bundle.parent / 'second').exists())

    def test_other_profile_cannot_adopt_an_owned_slot(self):
        apply_plan(self.trusted((C.MCP,)))
        path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(path.read_text())
        profile['id'] = 'other'
        (path.parent / 'other.json').write_text(json.dumps(profile))
        other = Workspace.load(self.fixture.root, 'other')
        plan = plan_install(other, 'cursor', self.state, components=(C.MCP,), project_root=self.project, adopt=True)
        tokens = tuple(item.trust_token for item in plan.all_operations if item.trust_token)
        plan = plan_install(other, 'cursor', self.state, components=(C.MCP,), project_root=self.project, adopt=True, trust=tokens)
        self.assertTrue(plan.has_conflicts)
        self.assertTrue(any(item.diagnostic == 'SLOT_OWNED' for item in plan.all_operations))

    def test_ledger_revision_is_part_of_stale_plan(self):
        plan = self.trusted()
        path = self.hook_config.parent / '.agent-workbench-owners.json'
        path.parent.mkdir(parents=True)
        path.write_text('{"version":1,"slots":{}}')
        with self.assertRaisesRegex(ValueError, 'document changed since planning'):
            apply_plan(plan)
        self.assertFalse(self.bundle.exists())

    def test_tree_symlinks_special_files_and_modes_are_checked(self):
        extra = self.hooks / 'extra'
        extra.symlink_to(self.hooks / 'stop.sh')
        with self.assertRaises((ValueError, OSError)):
            self.plan()
        extra.unlink()
        os.mkfifo(extra)
        with self.assertRaisesRegex(ValueError, 'special file'):
            self.plan()
        extra.unlink()
        apply_plan(self.trusted())
        (self.bundle / 'hooks/stop.sh').chmod(0o644)
        self.assertTrue(self.plan().has_conflicts)

    def test_mcp_source_swap_and_modified_source_after_plan(self):
        source = self.fixture.root / 'plugins/sample/mcp.json'
        plan = self.trusted((C.MCP,))
        saved = source.with_suffix('.saved')
        source.rename(saved)
        source.symlink_to(saved)
        with self.assertRaises(OSError):
            apply_plan(plan)
        source.unlink()
        saved.rename(source)
        plan = self.trusted((C.MCP,))
        source.write_text(source.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            apply_plan(plan)
        self.assertFalse(self.mcp_config.exists())

    def test_v3_migrates_to_v4_and_legacy_resources_remain_uninstallable(self):
        apply_plan(self.plan((C.SKILLS,)))
        self.assertEqual(3, load_state(self.plan((C.SKILLS,)).state_file).schema_version)
        apply_plan(self.trusted((C.SKILLS, C.HOOKS, C.MCP)))
        self.assertEqual(4, load_state(self.plan().state_file).schema_version)
        apply_uninstall(self.uninstall())
        self.assertFalse((self.project / '.cursor/skills/sample-skill').is_symlink())
        self.assertFalse(self.bundle.exists())

    def test_crafted_slot_document_is_not_mutation_authority(self):
        plan = self.trusted()
        apply_plan(plan)
        data = json.loads(plan.state_file.read_text())
        victim = self.base / 'victim.json'
        victim.write_text('{"preserve":true}')
        next(iter(data['slots'].values()))['document'] = str(victim)
        plan.state_file.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'adapter-authorized'):
            self.uninstall()
        self.assertEqual('{"preserve":true}', victim.read_text())

    def test_document_symlink_after_planning_rejects_without_writes(self):
        plan = self.trusted()
        self.mcp_config.parent.mkdir(parents=True)
        outside = self.base / 'outside.json'
        outside.write_text('{"foreign":true}')
        self.mcp_config.symlink_to(outside)
        with self.assertRaises(OSError):
            apply_plan(plan)
        self.assertEqual('{"foreign":true}', outside.read_text())
        self.assertFalse(self.bundle.exists())

    def test_uninstall_crash_recovers_bundle_before_reactivating_hooks(self):
        apply_plan(self.trusted())
        old = self.hook_config.read_bytes()
        real_discard = installation._DestinationRoot.discard
        def crash(root, moved):
            real_discard(root, moved)
            raise KeyboardInterrupt('crash after bundle removal')
        with mock.patch.object(installation._DestinationRoot, 'discard', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_uninstall(self.uninstall())
        self.assertEqual([], json.loads(self.hook_config.read_text())['hooks']['stop'])
        self.assertFalse(self.bundle.exists())
        self.plan()
        self.assertEqual(old, self.hook_config.read_bytes())
        self.assertTrue(self.bundle.exists())
        self.assertEqual({Op.KEEP}, {item.kind for item in self.plan().all_operations})

    def test_recovery_refuses_external_document_edits_and_keeps_evidence(self):
        real = shared_config.compare_replace_document
        def crash(expected, content, fd, quarantine):
            path = expected.path
            result = real(expected, content, fd, quarantine)
            if path.name == 'hooks.json' and content and b'command' in content:
                raise KeyboardInterrupt('crash')
            return result
        with mock.patch.object(shared_config, 'compare_replace_document', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(self.trusted())
        data = json.loads(self.hook_config.read_text())
        data['foreign'] = 'added after crash'
        self.hook_config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'recovery blocked'):
            self.plan()
        self.assertTrue(self.bundle.exists())
        self.assertTrue((self.hook_config.parent / '.agent-workbench-transaction.json').exists())
        self.assertEqual('added after crash', json.loads(self.hook_config.read_text())['foreign'])

    def test_rollback_does_not_remove_unchanged_active_hook_bundle(self):
        apply_plan(self.trusted())
        real_delete = transactions._restore_path
        def verify_no_active_bundle_removal(root, operation, value, quarantine):
            if operation.component is C.HOOKS:
                self.assertEqual(Op.KEEP, self.plan_without_recovery.operations[0].kind)
                self.assertTrue(self.bundle.exists())
            return real_delete(root, operation, value, quarantine)
        self.plan_without_recovery = self.plan()
        before_inode = self.bundle.stat().st_ino
        with mock.patch.object(installation, 'write_state', side_effect=OSError('state unavailable')), mock.patch.object(transactions, '_restore_path', verify_no_active_bundle_removal):
            with self.assertRaises(OSError):
                apply_plan(self.plan_without_recovery)
        self.assertEqual(before_inode, self.bundle.stat().st_ino)

    def test_trust_is_read_only_with_pending_journal(self):
        real = shared_config.compare_replace_document
        def crash(expected, content, fd, quarantine):
            path = expected.path
            result = real(expected, content, fd, quarantine)
            if path.name == 'hooks.json' and content and b'command' in content:
                raise KeyboardInterrupt('crash')
            return result
        with mock.patch.object(shared_config, 'compare_replace_document', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(self.trusted())
        before = self.hook_config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'pending recovery'):
            plan_install(self.workspace, 'cursor', self.state, components=(C.HOOKS,), project_root=self.project, repair=False)
        self.assertEqual(before, self.hook_config.read_bytes())
        self.assertTrue(self.bundle.exists())

    def test_recovery_precedes_inspection_of_changed_source(self):
        real = shared_config.compare_replace_document
        def crash(expected, content, fd, quarantine):
            path = expected.path
            result = real(expected, content, fd, quarantine)
            if path.name == 'hooks.json' and content and b'command' in content:
                raise KeyboardInterrupt('crash')
            return result
        with mock.patch.object(shared_config, 'compare_replace_document', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(self.trusted())
        (self.hooks / 'hooks.json').write_text('invalid JSON')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertFalse(self.hook_config.exists())
        self.assertFalse(self.bundle.exists())
        self.assertFalse((self.hook_config.parent / '.agent-workbench-transaction.json').exists())

    def test_bundle_foreign_collision_and_exact_copy_adoption(self):
        import shutil
        self.bundle.mkdir(parents=True)
        shutil.copytree(self.hooks, self.bundle / 'hooks')
        self.assertTrue(self.plan((C.HOOKS,)).has_conflicts)
        plan = self.trusted((C.HOOKS,), adopt=True)
        self.assertEqual(Op.ADOPT, plan.operations[0].kind)
        apply_plan(plan)
        apply_uninstall(self.uninstall())
        self.bundle.mkdir()
        (self.bundle / 'foreign').write_text('preserve')
        tokens = tuple(item.trust_token for item in self.plan((C.HOOKS,), adopt=True).all_operations if item.trust_token)
        self.assertTrue(self.plan((C.HOOKS,), tokens, adopt=True).has_conflicts)
        self.assertEqual('preserve', (self.bundle / 'foreign').read_text())

    def test_bundle_publish_cannot_overwrite_racing_foreign_directory(self):
        plan = self.trusted((C.HOOKS,))
        real = installation.bundles.rename_new
        def race(source, destination, parent, destination_fd=None):
            if destination == 'sample':
                os.mkdir(destination, dir_fd=parent)
            return real(source, destination, parent, destination_fd)
        with mock.patch.object(installation.bundles, 'rename_new', race):
            with self.assertRaises((FileExistsError, ValueError)):
                apply_plan(plan)
        # Recovery fails closed on that foreign directory and keeps its journal.
        self.assertTrue(self.bundle.is_dir())
        self.assertFalse(self.hook_config.exists())
        self.assertTrue((self.hook_config.parent / '.agent-workbench-transaction.json').exists())

    def test_v4_rejects_unknown_fields_and_invalid_slot_evidence(self):
        plan = self.trusted()
        apply_plan(plan)
        original = json.loads(plan.state_file.read_text())
        mutations = [
            lambda data: data.update(extra=True),
            lambda data: data['entries']['hook:sample'].update(trust='invalid'),
            lambda data: data['entries']['hook:sample'].update(resourceKind='file-copy'),
            lambda data: next(iter(data['slots'].values())).update(extra=True),
            lambda data: next(iter(data['slots'].values())).update(digest='invalid'),
            lambda data: next(iter(data['slots'].values())).update(component='skills'),
            lambda data: next(iter(data['slots'].values())).update(trust='invalid'),
        ]
        for mutation in mutations:
            data = json.loads(json.dumps(original))
            mutation(data)
            plan.state_file.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_state(plan.state_file)
        plan.state_file.write_text(json.dumps(original))
        self.assertFalse(self.uninstall().has_conflicts)

    def test_status_recovery_does_not_discard_modified_state(self):
        apply_plan(self.trusted())
        plan = self.uninstall()
        real_discard = installation._DestinationRoot.discard
        def crash(root, moved):
            real_discard(root, moved)
            raise KeyboardInterrupt('crash')
        with mock.patch.object(installation._DestinationRoot, 'discard', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_uninstall(plan)
        state = json.loads(plan.state_file.read_text())
        state['repository'] = str(self.base / 'external-edit')
        plan.state_file.write_text(json.dumps(state))
        before = plan.state_file.read_bytes()
        with self.assertRaisesRegex(ValueError, 'modified ownership state'):
            self.plan()
        self.assertEqual(before, plan.state_file.read_bytes())

    def test_user_scope_activation_and_uninstall_are_isolated(self):
        home = self.base / 'home'
        with mock.patch('pathlib.Path.home', return_value=home), mock.patch.dict(os.environ, {'HOME': str(home)}), mock.patch('subprocess.Popen', side_effect=AssertionError('payload executed')):
            workspace = Workspace.load(self.fixture.root)
            initial = plan_install(workspace, 'cursor', self.state, components=(C.HOOKS, C.MCP))
            tokens = tuple(item.trust_token for item in initial.all_operations if item.trust_token)
            plan = plan_install(workspace, 'cursor', self.state, components=(C.HOOKS, C.MCP), trust=tokens)
            apply_plan(plan)
            config = home / '.cursor'
            self.assertTrue((config / 'hooks.json').is_file())
            self.assertTrue((config / 'mcp.json').is_file())
            hook = json.loads((config / 'hooks.json').read_text())['hooks']['stop'][0]
            self.assertEqual(str(config / '.agent-workbench-hooks/sample/hooks/stop.sh'), hook['command'])
            apply_uninstall(plan_uninstall(workspace, 'cursor', self.state))
            self.assertFalse((config / '.agent-workbench-hooks/sample').exists())
            self.assertEqual([], json.loads((config / 'hooks.json').read_text())['hooks']['stop'])
        self.assertFalse(self.project.exists())

    def test_validate_plan_and_install_cannot_start_payloads(self):
        from agent_workbench.validation import validate
        source = self.fixture.root / 'plugins/sample/mcp.json'
        marker = self.base / 'must-not-exist'
        source.write_text(json.dumps({'mcpServers': {'danger': {'command': 'touch', 'args': [str(marker)]}}}))
        (self.hooks / 'hooks.json').write_text(json.dumps({'version': 1, 'hooks': {'stop': [{'command': 'touch ' + str(marker)}]}}))
        # Absolute payload references fail closed; inspection still only reads.
        with mock.patch('subprocess.Popen', side_effect=AssertionError('payload executed')):
            self.assertTrue(validate(self.workspace, check_generated=False).valid)
            with self.assertRaisesRegex(ValueError, 'escapes'):
                self.plan()
            apply_plan(self.trusted((C.MCP,)))
        self.assertFalse(marker.exists())

    def test_identical_bytes_with_new_document_identity_is_stale(self):
        apply_plan(self.trusted())
        plan = self.plan()
        replacement = self.hook_config.with_suffix('.replacement')
        replacement.write_bytes(self.hook_config.read_bytes())
        replacement.replace(self.hook_config)
        with self.assertRaisesRegex(ValueError, 'document changed since planning'):
            apply_plan(plan)

    def test_forged_project_journal_cannot_activate_untrusted_hooks(self):
        plan = self.plan()
        self.hook_config.parent.mkdir(parents=True)
        journal = self.hook_config.parent / '.agent-workbench-transaction.json'
        payload = shared_config.encode({'version': 1, 'hooks': {'stop': [{'command': 'attacker-command'}]}})
        journal.write_bytes(shared_config.encode({
            'version': 1, 'stateFile': str(plan.state_file), 'stateBefore': None,
            'stateAfter': None, 'paths': [], 'documents': [{
                'path': str(self.hook_config), 'before': transactions._pack(payload),
                'removed': None, 'after': None}]}))
        before = {p.name: p.read_bytes() for p in self.hook_config.parent.iterdir()}
        with self.assertRaisesRegex(ValueError, 'unauthenticated'):
            self.plan()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.hook_config.parent.iterdir()})
        self.assertFalse(self.bundle.exists())
        self.assertFalse(plan.state_file.exists())

    def test_stale_state_cannot_remove_another_profiles_server(self):
        plan = self.trusted((C.MCP,))
        apply_plan(plan)
        stale = plan.state_file.read_bytes()
        apply_uninstall(self.uninstall())
        profile_path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(profile_path.read_text())
        profile['id'] = 'other'
        (profile_path.parent / 'other.json').write_text(json.dumps(profile))
        other = Workspace.load(self.fixture.root, 'other')
        pending = plan_install(other, 'cursor', self.state, components=(C.MCP,), project_root=self.project)
        tokens = tuple(op.trust_token for op in pending.all_operations if op.trust_token)
        apply_plan(plan_install(other, 'cursor', self.state, components=(C.MCP,), project_root=self.project, trust=tokens))
        plan.state_file.write_bytes(stale)
        self.assert_removal_authority_conflict()

    def test_forged_state_cannot_remove_unmanaged_foreign_server(self):
        plan = self.trusted((C.MCP,))
        apply_plan(plan)
        data = json.loads(plan.state_file.read_text())
        slot = next(iter(data['slots'].values()))
        foreign = {'command': 'foreign-command'}
        slot['slot'] = 'foreign'
        slot['digest'] = shared_config.digest(foreign)
        slot['key'] = 'mcp:sample:foreign'
        data['slots'] = {slot['key']: slot}
        plan.state_file.write_text(json.dumps(data))
        self.mcp_config.write_bytes(shared_config.encode({'mcpServers': {'foreign': foreign}}))
        self.assert_removal_authority_conflict()

    def assert_removal_authority_conflict(self):
        ledger = self.mcp_config.parent / '.agent-workbench-owners.json'
        before = (self.mcp_config.read_bytes(), ledger.read_bytes())
        plan = self.uninstall()
        self.assertTrue(plan.has_conflicts)
        self.assertTrue(any(op.diagnostic == 'SLOT_OWNERSHIP_MISMATCH' for op in plan.all_operations))
        self.assertTrue(all(doc.before.content == doc.after == doc.removed for doc in plan.documents))
        with self.assertRaises(ValueError):
            apply_uninstall(plan)
        self.assertEqual(before, (self.mcp_config.read_bytes(), ledger.read_bytes()))

    def test_foreign_edit_after_journal_write_survives_deactivation(self):
        apply_plan(self.trusted((C.MCP,)))
        plan = self.uninstall()
        real = shared_config.replace_document
        def inject(path, content, fd):
            real(path, content, fd)
            if path.name == '.agent-workbench-transaction.json':
                data = json.loads(self.mcp_config.read_text())
                data['mcpServers']['foreign'] = {'command': 'preserve-me'}
                self.mcp_config.write_bytes(shared_config.encode(data))
        with mock.patch.object(shared_config, 'replace_document', inject):
            with self.assertRaisesRegex(ValueError, 'changed|recovery blocked'):
                apply_uninstall(plan)
        self.assertEqual({'command': 'preserve-me'}, json.loads(self.mcp_config.read_text())['mcpServers']['foreign'])
        self.assertTrue((self.mcp_config.parent / '.agent-workbench-transaction.json').exists())

    def persist_pending_journal(self, plan):
        real = shared_config.replace_document
        def crash(path, content, fd):
            real(path, content, fd)
            if path.name == '.agent-workbench-transaction.json':
                raise KeyboardInterrupt('durable journal')
        with mock.patch.object(shared_config, 'replace_document', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(plan)
        return self.hook_config.parent / '.agent-workbench-transaction.json'

    def test_tampered_authenticated_journal_is_never_restored(self):
        plan = self.trusted()
        journal = self.persist_pending_journal(plan)
        data = json.loads(journal.read_text())
        data['documents'][0]['before'] = transactions._pack(shared_config.encode(
            {'hooks': {'stop': [{'command': 'attacker-command'}]}}))
        journal.write_bytes(shared_config.encode(data))
        with self.assertRaisesRegex(ValueError, 'unauthenticated'):
            self.plan()
        self.assertTrue(journal.exists())
        self.assertFalse(self.hook_config.exists())
        self.assertFalse(self.bundle.exists())

    def test_private_journal_key_rejects_symlinks_and_public_permissions(self):
        plan = self.trusted((C.MCP,))
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        key.parent.mkdir(parents=True)
        outside = self.base / 'outside-key'
        outside.write_bytes(b'x' * 32)
        outside.chmod(0o600)
        key.symlink_to(outside)
        with self.assertRaises(OSError):
            apply_plan(plan)
        self.assertEqual(b'x' * 32, outside.read_bytes())
        key.unlink()
        key.write_bytes(b'x' * 32)
        key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'unsafe private transaction key'):
            apply_plan(plan)
        self.assertFalse(self.mcp_config.exists())

    def test_pending_journal_only_owning_profile_can_recover(self):
        plan = self.trusted()
        journal = self.persist_pending_journal(plan)
        key = plan.state_file.with_name('.' + plan.state_file.name + '.transaction-key')
        self.assertEqual(0o600, key.stat().st_mode & 0o777)
        profile_path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(profile_path.read_text())
        profile['id'] = 'other'
        (profile_path.parent / 'other.json').write_text(json.dumps(profile))
        other = Workspace.load(self.fixture.root, 'other')
        before = journal.read_bytes()
        with self.assertRaisesRegex(ValueError, 'another profile'):
            plan_install(other, 'cursor', self.state, components=(C.MCP,), project_root=self.project)
        self.assertEqual(before, journal.read_bytes())
        # Whitespace/key ordering are not part of the authenticated canonical bytes.
        journal.write_text(json.dumps(json.loads(before), sort_keys=True))
        self.plan()
        self.assertFalse(journal.exists())
        self.assertFalse(self.hook_config.exists())

    def test_document_capture_preserves_racing_foreign_content(self):
        apply_plan(self.trusted((C.MCP,)))
        plan = self.uninstall()
        real = transactions.bundles.rename_new
        injected = []
        def race(source, destination, parent, destination_fd=None):
            if source == 'mcp.json' and destination == 'entry' and not injected:
                data = json.loads(self.mcp_config.read_text())
                data['mcpServers']['foreign'] = {'command': 'preserve-me'}
                self.mcp_config.write_bytes(shared_config.encode(data))
                injected.append(True)
            return real(source, destination, parent, destination_fd)
        with mock.patch.object(transactions.bundles, 'rename_new', race):
            with self.assertRaisesRegex(ValueError, 'changed|recovery blocked'):
                apply_uninstall(plan)
        self.assertTrue(injected)
        self.assertEqual({'command': 'preserve-me'}, json.loads(self.mcp_config.read_text())['mcpServers']['foreign'])
        self.assertTrue((self.mcp_config.parent / '.agent-workbench-transaction.json').exists())

    def test_document_publish_cannot_overwrite_racing_foreign_file(self):
        apply_plan(self.trusted((C.MCP,)))
        source = self.fixture.root / 'plugins/sample/mcp.json'
        source.write_text('{"mcpServers":{"sample-server":{"command":"updated-command"}}}')
        plan = self.trusted((C.MCP,))
        real = transactions.bundles.rename_new
        injected = []
        foreign = shared_config.encode({'mcpServers': {'foreign': {'command': 'preserve-me'}}})
        def race(source, destination, parent, destination_fd=None):
            if source == 'new' and destination == 'mcp.json' and not injected:
                self.mcp_config.write_bytes(foreign)
                injected.append(True)
            return real(source, destination, parent, destination_fd)
        with mock.patch.object(transactions.bundles, 'rename_new', race):
            with self.assertRaises((ValueError, FileExistsError)):
                apply_plan(plan)
        self.assertTrue(injected)
        self.assertEqual(foreign, self.mcp_config.read_bytes())
        journal = self.mcp_config.parent / '.agent-workbench-transaction.json'
        self.assertTrue(journal.exists())
        self.assertTrue(any(self.mcp_config.parent.glob('.agent-workbench-document-*/entry')))

    def test_crash_with_document_quarantine_and_staged_file_recovers(self):
        apply_plan(self.trusted((C.MCP,)))
        before = self.mcp_config.read_bytes()
        plan = self.uninstall()
        real = transactions.bundles.rename_new
        def crash(source, destination, parent, destination_fd=None):
            if source == 'new' and destination == 'mcp.json':
                # Leave a durable staged file, as on an interrupted publish.
                raise KeyboardInterrupt('staged document')
            return real(source, destination, parent, destination_fd)
        with mock.patch.object(transactions.bundles, 'rename_new', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_uninstall(plan)
        self.assertTrue(list(self.mcp_config.parent.glob('.agent-workbench-document-*/new')))
        self.plan()
        self.assertEqual(before, self.mcp_config.read_bytes())
        self.assertFalse(list(self.mcp_config.parent.glob('.agent-workbench-document-*')))

    def test_changed_ledger_digest_blocks_prior_slot_removal(self):
        apply_plan(self.trusted((C.MCP,)))
        ledger = self.mcp_config.parent / '.agent-workbench-owners.json'
        data = json.loads(ledger.read_text())
        next(iter(data['slots'].values()))['digest'] = '0' * 64
        ledger.write_bytes(shared_config.encode(data))
        self.assert_removal_authority_conflict()

    def test_crash_after_capture_recovers_absent_document_from_quarantine(self):
        apply_plan(self.trusted((C.MCP,)))
        before = self.mcp_config.read_bytes()
        plan = self.plan((C.MCP,))
        journal = self.persist_pending_journal(plan)
        record = next(item for item in json.loads(journal.read_text())['documents']
                      if item['path'] == str(self.mcp_config))
        # Reconstruct the exact durable layout of process death after capture,
        # before staging/publishing. No Python cleanup runs in a killed process.
        quarantine = self.mcp_config.parent / (record['quarantine'] + '-remove')
        quarantine.mkdir(mode=0o700)
        self.mcp_config.rename(quarantine / 'entry')
        self.assertFalse(self.mcp_config.exists())
        self.plan()
        self.assertEqual(before, self.mcp_config.read_bytes())
        self.assertFalse(quarantine.exists())
        self.assertFalse(journal.exists())

    def test_shared_document_lock_blocks_recovery_across_profiles(self):
        journal = self.persist_pending_journal(self.trusted())
        before = journal.read_bytes()
        with transactions._document_lock(self.hook_config.parent, False):
            with self.assertRaisesRegex(FileExistsError, 'locked by another operation'):
                self.plan()
        self.assertEqual(before, journal.read_bytes())
        self.plan()
        self.assertFalse(journal.exists())

    def test_partial_staged_document_can_be_recovered(self):
        apply_plan(self.trusted((C.MCP,)))
        before = self.mcp_config.read_bytes()
        journal = self.persist_pending_journal(self.plan((C.MCP,)))
        record = next(item for item in json.loads(journal.read_text())['documents']
                      if item['path'] == str(self.mcp_config))
        quarantine = self.mcp_config.parent / (record['quarantine'] + '-recover-before')
        quarantine.mkdir(mode=0o700)
        self.mcp_config.rename(quarantine / 'entry')
        # A killed writer can leave an incomplete JSON prefix in its known temp.
        (quarantine / 'new').write_bytes(before[:5])
        self.plan()
        self.assertEqual(before, self.mcp_config.read_bytes())
        self.assertFalse(quarantine.exists())
        self.assertFalse(journal.exists())
