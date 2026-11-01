from __future__ import annotations

import json
import os
import shlex
import unittest
from unittest import mock

import test_activation
from agent_workbench import bundles, installation, shared_config, transactions
from agent_workbench.adapters.components import classify_hooks
from agent_workbench.installation import plan_install, apply_plan, plan_uninstall, apply_uninstall, OperationKind as Op
from agent_workbench.model import ComponentKind as C, Representation as R
from agent_workbench.state import load_state
from agent_workbench.workspace import Workspace


class ClaudeActivationTests(unittest.TestCase):
    def setUp(self):
        test_activation.ActivationTests.setUp(self)
        self.bundle = self.project / '.claude/.agent-workbench-hooks/sample'
        self.hook_config = self.project / '.claude/settings.json'
        self.mcp_config = self.project / '.mcp.json'
        self.native = {'hooks': {'Stop': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh" --hello', 'timeout': 20},
            {'type': 'prompt', 'prompt': 'An inert prompt', 'model': 'preserve'}]}]}}
        self.write_hooks(self.native)
        self.workspace = Workspace.load(self.fixture.root)

    def write_hooks(self, data):
        (self.hooks / 'hooks.json').write_bytes(shared_config.encode(data))

    def plan(self, components=(C.HOOKS, C.MCP), trust=(), adopt=False):
        return plan_install(self.workspace, 'claude', self.state, components=components,
                            project_root=self.project, trust=trust, adopt=adopt)

    trusted = test_activation.ActivationTests.trusted

    def uninstall(self):
        return plan_uninstall(self.workspace, 'claude', self.state, project_root=self.project)

    test_changed_content_requires_new_grant = test_activation.ActivationTests.test_changed_content_requires_new_grant
    test_changed_source_after_plan = test_activation.ActivationTests.test_changed_source_after_plan
    test_stale_document_plan = test_activation.ActivationTests.test_stale_document_plan
    test_duplicate_keys_symlinks_special_documents = test_activation.ActivationTests.test_duplicate_keys_symlinks_special_documents
    test_document_lock_contention = test_activation.ActivationTests.test_document_lock_contention
    test_state_write_failure_rolls_back_every_activation = test_activation.ActivationTests.test_state_write_failure_rolls_back_every_activation
    test_modified_bundle_conflicts_and_source_swaps = test_activation.ActivationTests.test_modified_bundle_conflicts_and_source_swaps
    test_collision_and_exact_adoption = test_activation.ActivationTests.test_collision_and_exact_adoption
    test_state_token_edit_cannot_forge_persisted_trust = test_activation.ActivationTests.test_state_token_edit_cannot_forge_persisted_trust
    test_hook_state_token_edit_cannot_forge_persisted_trust = test_activation.ActivationTests.test_hook_state_token_edit_cannot_forge_persisted_trust
    test_legacy_trust_requires_grant_but_remains_uninstallable = test_activation.ActivationTests.test_legacy_trust_requires_grant_but_remains_uninstallable
    test_proofs_strict_decode_and_planning_does_not_create_authority = test_activation.ActivationTests.test_proofs_strict_decode_and_planning_does_not_create_authority

    def test_shape_classification_and_incompatible_targets_are_inert(self):
        self.assertIs(R.CLAUDE_HOOK_BUNDLE, classify_hooks(self.native))
        cursor = {'version': 1, 'hooks': {'stop': [{'command': 'hooks/stop.sh'}]}}
        self.assertIs(R.HOOK_BUNDLE, classify_hooks(cursor))
        for data in ({'hooks': {}}, {'hooks': {'Stop': [{'command': 'a', 'hooks': []}]}},
                     {'hooks': {'Stop': self.native['hooks']['Stop'] + cursor['hooks']['stop']}},
                     {'hooks': {'Stop': [{'hooks': [{'type': 'command'}]}]}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                classify_hooks(data)
        for target, data in (('cursor', self.native), ('claude', cursor)):
            self.write_hooks(data)
            plan = plan_install(Workspace.load(self.fixture.root), target, self.state,
                                components=(C.HOOKS,), project_root=self.project)
            self.assertTrue(plan.has_conflicts)
            self.assertIn('REPRESENTATION_UNSUPPORTED', {op.diagnostic for op in plan.all_operations})
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                apply_plan(plan)
            self.assertFalse(self.project.exists())
        self.write_hooks({'hooks': {'Stop': self.native['hooks']['Stop'] + cursor['hooks']['stop']}})
        with self.assertRaisesRegex(ValueError, 'mixed'):
            self.plan()
        self.assertFalse(self.project.exists())

    def test_native_copy_nested_rewrite_modes_reinstall_and_uninstall(self):
        sentinel = self.base / 'EXECUTED'
        (self.hooks / 'stop.sh').write_text('#!/bin/sh\ntouch ' + str(sentinel) + '\n')
        self.assertTrue(self.plan().has_conflicts)
        self.assertFalse(self.project.exists())
        with mock.patch('subprocess.Popen', side_effect=AssertionError('executed')):
            plan = self.trusted()
            apply_plan(plan)
            data = json.loads(self.hook_config.read_text())
            self.assertEqual({'hooks'}, set(data))
            group = data['hooks']['Stop'][0]
            expected = json.loads(json.dumps(self.native['hooks']['Stop'][0]))
            expected['hooks'][0]['command'] = 'sh "' + str(self.bundle / 'hooks/stop.sh') + '" --hello'
            self.assertEqual(expected, group)
            self.assertEqual(bundles.source_tree(self.hooks).digest, bundles.tree_digest(self.bundle))
            self.assertEqual(0o755, (self.bundle / 'hooks/stop.sh').stat().st_mode & 0o777)
            self.assertEqual(4, load_state(plan.state_file).schema_version)
            self.assertEqual({Op.KEEP}, {op.kind for op in self.plan().all_operations})
            apply_plan(self.plan())
            apply_uninstall(self.uninstall())
        self.assertFalse(sentinel.exists())
        self.assertFalse(self.bundle.exists())
        self.assertEqual([], json.loads(self.hook_config.read_text())['hooks']['Stop'])
        self.assertFalse((self.hook_config.parent / '.agent-workbench-transaction.json').exists())

    def test_project_and_isolated_user_paths(self):
        plan = self.plan()
        self.assertEqual({self.hook_config, self.mcp_config},
                         {doc.before.path for doc in plan.documents if doc.operations})
        home = self.base / 'home'
        with mock.patch('pathlib.Path.home', return_value=home), mock.patch.dict(os.environ, {'HOME': str(home)}):
            workspace = Workspace.load(self.fixture.root)
            pending = plan_install(workspace, 'claude', self.state, components=(C.HOOKS,))
            tokens = tuple(op.trust_token for op in pending.all_operations if op.trust_token)
            user = plan_install(workspace, 'claude', self.state, components=(C.HOOKS,), trust=tokens)
            apply_plan(user)
            self.assertTrue((home / '.claude/settings.json').is_file())
            apply_uninstall(plan_uninstall(workspace, 'claude', self.state))
            unsupported = plan_install(workspace, 'claude', self.state, components=(C.MCP,))
            self.assertEqual(['PROJECT_ROOT_REQUIRED'], [op.diagnostic for op in unsupported.all_operations])
            self.assertFalse((home / '.claude.json').exists())

    def test_foreign_groups_settings_servers_and_order_preserved(self):
        self.hook_config.parent.mkdir(parents=True)
        foreign = [{'matcher': 'foreign', 'hooks': [{'type': 'command', 'command': 'keep'}]},
                   {'hooks': [{'type': 'prompt', 'prompt': 'keep too'}]}]
        original = {'permissions': {'allow': ['Read']}, 'version': 'foreign-setting',
                    'hooks': {'Stop': foreign, 'SessionStart': []}}
        self.hook_config.write_bytes(shared_config.encode(original))
        mcp = {'other': 42, 'mcpServers': {'foreign': {'url': 'https://foreign.invalid'}}}
        self.mcp_config.write_bytes(shared_config.encode(mcp))
        apply_plan(self.trusted())
        self.assertEqual(foreign, json.loads(self.hook_config.read_text())['hooks']['Stop'][:2])
        apply_uninstall(self.uninstall())
        self.assertEqual(original, json.loads(self.hook_config.read_text()))
        self.assertEqual(mcp, json.loads(self.mcp_config.read_text()))

    def test_hook_adoption_modified_owned_and_foreign_bundle_reference(self):
        plan = self.trusted((C.HOOKS,))
        self.hook_config.parent.mkdir(parents=True)
        self.hook_config.write_bytes(plan.documents[0].after)
        self.assertTrue(self.plan((C.HOOKS,)).has_conflicts)
        apply_plan(self.trusted((C.HOOKS,), adopt=True))
        data = json.loads(self.hook_config.read_text())
        data['hooks']['Stop'].append({'matcher': 'foreign', 'hooks': [
            {'type': 'command', 'command': 'sh "' + str(self.bundle / 'hooks/stop.sh') + '"'}]})
        self.hook_config.write_bytes(shared_config.encode(data))
        self.assertIn('HOOK_BUNDLE_REFERENCED', {op.diagnostic for op in self.uninstall().all_operations})
        data['hooks']['Stop'].pop()
        data['hooks']['Stop'][0]['matcher'] = 'changed'
        self.hook_config.write_bytes(shared_config.encode(data))
        self.assertIn('OWNED_SLOT_MODIFIED', {op.diagnostic for op in self.uninstall().all_operations})

    def test_local_references_and_incompatible_mcp_variables(self):
        for command in ('./hooks/../../escape.sh', '${CLAUDE_PLUGIN_ROOT}/../outside.sh',
                        '../escape.sh', '/tmp/evil.sh', '${CURSOR_PLUGIN_ROOT}/hooks/stop.sh',
                        '${CLAUDE_PLUGIN_ROOT}/hooks/missing.sh', 'other/missing.sh'):
            self.native['hooks']['Stop'][0]['hooks'][0]['command'] = command
            self.write_hooks(self.native)
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.plan((C.HOOKS,))
            self.assertFalse(self.project.exists())
        for command in ('sh ./stop.sh', 'sh hooks/stop.sh', 'sh stop.sh',
                        "sh '${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh'", 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"'):
            self.native['hooks']['Stop'][0]['hooks'][0]['command'] = command
            self.write_hooks(self.native)
            plan = self.trusted((C.HOOKS,))
            actual = json.loads(plan.documents[0].after)['hooks']['Stop'][0]['hooks'][0]['command']
            self.assertEqual(str(self.bundle / 'hooks/stop.sh'), shlex.split(actual)[1])
        source = self.fixture.root / 'plugins/sample/mcp.json'
        (source.parent / 'server.js').write_text('payload')
        for target, value in (
            ('claude', '${CURSOR_PLUGIN_ROOT}/server'),
            ('claude', '${CLAUDE_PLUGIN_ROOT}/server'),
            ('cursor', '${CLAUDE_PLUGIN_ROOT}/server'),
            ('cursor', '${CURSOR_PLUGIN_ROOT}/server'),
            ('claude', './server.js'),
            ('cursor', 'server.js'),
        ):
            source.write_bytes(shared_config.encode({'mcpServers': {
                'sample': {'command': 'node', 'args': [value]}}}))
            with self.subTest(target=target, value=value), self.assertRaisesRegex(
                    ValueError, 'standalone MCP|local MCP'):
                plan_install(self.workspace, target, self.state, components=(C.MCP,),
                             project_root=self.project)
            self.assertFalse(self.project.exists())
        server = {'command': 'node', 'args': ['package-name'], 'env': {'V': '${OTHER}'}, 'custom': True}
        source.write_bytes(shared_config.encode({'mcpServers': {'sample': server}}))
        plan = self.trusted((C.MCP,))
        self.assertEqual(server, json.loads(plan.documents[0].after)['mcpServers']['sample'])

    def test_stale_project_mcp_and_parent_swap(self):
        plan = self.trusted()
        self.project.mkdir()
        self.mcp_config.write_text('{"foreign":true}')
        with self.assertRaisesRegex(ValueError, 'document changed since planning'):
            apply_plan(plan)
        self.assertFalse(self.bundle.exists())
        self.mcp_config.unlink()
        plan = self.trusted()
        saved = self.project.with_name('saved')
        self.project.rename(saved)
        self.project.mkdir()
        with self.assertRaisesRegex((ValueError, FileExistsError), 'changed since planning'):
            apply_plan(plan)
        self.assertFalse(self.bundle.exists())

    def pending_journal(self):
        plan = self.trusted()
        with mock.patch.object(installation, 'write_state', side_effect=KeyboardInterrupt('crash')):
            with self.assertRaises(KeyboardInterrupt):
                apply_plan(plan)
        return self.hook_config.parent / '.agent-workbench-transaction.json'

    def test_multi_parent_authenticated_recovery_and_parent_swap(self):
        journal = self.pending_journal()
        data = json.loads(journal.read_text())
        self.assertIn(str(self.project), data['parents'])
        self.assertIn(str(self.hook_config.parent), data['parents'])
        saved = self.project.with_name('saved')
        self.project.rename(saved)
        self.project.mkdir()
        # Move the valid journal/config back into a new project inode.
        (saved / '.claude').rename(self.project / '.claude')
        (saved / '.mcp.json').rename(self.mcp_config)
        with self.assertRaisesRegex(ValueError, 'parent identity changed'):
            self.plan()
        self.assertTrue(journal.exists())
        (self.project / '.claude').rename(saved / '.claude')
        self.mcp_config.rename(saved / '.mcp.json')
        self.project.rmdir()
        saved.rename(self.project)
        self.plan()
        self.assertFalse(journal.exists())
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.mcp_config.exists())

    def test_mcp_publish_failure_and_quarantine_recovery(self):
        apply_plan(self.trusted())
        before = self.mcp_config.read_bytes()
        real = transactions.bundles.rename_new
        def crash(source, destination, parent, destination_fd=None):
            if source == 'new' and destination == '.mcp.json':
                raise KeyboardInterrupt('staged project MCP')
            return real(source, destination, parent, destination_fd)
        with mock.patch.object(transactions.bundles, 'rename_new', crash):
            with self.assertRaises(KeyboardInterrupt):
                apply_uninstall(self.uninstall())
        self.assertEqual(before, self.mcp_config.read_bytes())
        self.assertTrue(list(self.project.glob('.agent-workbench-document-*/new')))
        self.plan()
        self.assertEqual(before, self.mcp_config.read_bytes())
        self.assertTrue(self.bundle.exists())
        self.assertFalse(list(self.project.glob('.agent-workbench-document-*')))

    def test_activation_and_deactivation_order_across_parents(self):
        real = shared_config.compare_replace_document
        def verify(expected, content, fd, quarantine):
            if expected.path == self.hook_config and content and b'command' in content:
                self.assertTrue((self.bundle / 'hooks/stop.sh').is_file())
            return real(expected, content, fd, quarantine)
        with mock.patch.object(shared_config, 'compare_replace_document', verify):
            apply_plan(self.trusted())
        discard = installation._DestinationRoot.discard
        def deactivated(root, moved):
            self.assertEqual([], json.loads(self.hook_config.read_text())['hooks']['Stop'])
            return discard(root, moved)
        with mock.patch.object(installation._DestinationRoot, 'discard', deactivated):
            apply_uninstall(self.uninstall())

    def test_both_representation_aliases_choose_exact_artifact(self):
        import shutil
        cursor = self.hooks.with_name('cursor-hooks')
        shutil.copytree(self.hooks, cursor)
        (cursor / 'hooks.json').write_text('{"version":1,"hooks":{"stop":[{"command":"hooks/stop.sh"}]}}')
        catalog_path = self.fixture.root / 'catalog/plugins.json'
        catalog = json.loads(catalog_path.read_text())
        artifacts = catalog['plugins'][0]['artifacts']
        artifacts['claude-hooks'] = artifacts.pop('hooks')
        artifacts['cursor-hooks'] = {'path': 'sample/cursor-hooks'}
        catalog_path.write_bytes(shared_config.encode(catalog))
        self.workspace = Workspace.load(self.fixture.root)
        for target in ('cursor', 'claude'):
            pending = plan_install(self.workspace, target, self.state, components=(C.HOOKS,), project_root=self.project)
            grants = tuple(op.trust_token for op in pending.all_operations if op.trust_token)
            plan = plan_install(self.workspace, target, self.state, components=(C.HOOKS,), project_root=self.project, trust=grants)
            self.assertFalse(plan.has_conflicts)
            apply_plan(plan)
            self.assertEqual(cursor if target == 'cursor' else self.hooks, plan.operations[0].source)
            apply_uninstall(plan_uninstall(self.workspace, target, self.state, project_root=self.project))

    def test_two_profiles_keep_distinct_groups_and_server_slots(self):
        import shutil
        second = self.hooks.parent.with_name('second')
        shutil.copytree(self.hooks.parent, second)
        (second / 'mcp.json').write_text('{"mcpServers":{"second-server":{"command":"never-run"}}}')
        catalog_path = self.fixture.root / 'catalog/plugins.json'
        catalog = json.loads(catalog_path.read_text())
        plugin = json.loads(json.dumps(catalog['plugins'][0]))
        plugin['id'] = 'second'
        for artifact in plugin['artifacts'].values():
            artifact['path'] = artifact['path'].replace('sample', 'second')
        catalog['plugins'].append(plugin)
        catalog_path.write_bytes(shared_config.encode(catalog))
        profile_path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(profile_path.read_text())
        profile['plugins']['include'] = ['sample']
        profile_path.write_bytes(shared_config.encode(profile))
        profile['id'] = 'second'
        profile['plugins']['include'] = ['second']
        (profile_path.parent / 'second.json').write_bytes(shared_config.encode(profile))
        self.workspace = Workspace.load(self.fixture.root)
        other = Workspace.load(self.fixture.root, 'second')
        apply_plan(self.trusted())
        pending = plan_install(other, 'claude', self.state, components=(C.HOOKS, C.MCP), project_root=self.project)
        grants = tuple(op.trust_token for op in pending.all_operations if op.trust_token)
        plan = plan_install(other, 'claude', self.state, components=(C.HOOKS, C.MCP), project_root=self.project, trust=grants)
        apply_plan(plan)
        self.assertEqual(2, len(json.loads(self.hook_config.read_text())['hooks']['Stop']))
        apply_uninstall(self.uninstall())
        self.assertEqual(1, len(json.loads(self.hook_config.read_text())['hooks']['Stop']))
        self.assertEqual({'second-server'}, set(json.loads(self.mcp_config.read_text())['mcpServers']))
        apply_uninstall(plan_uninstall(other, 'claude', self.state, project_root=self.project))

    def test_late_document_change_during_source_validation_is_pre_mutation(self):
        plan = self.trusted()
        self.project.mkdir()
        real = installation._validate_install_source
        def changed(*args):
            self.mcp_config.write_text('{"foreign":true}')
            return real(*args)
        with mock.patch.object(installation, '_validate_install_source', changed):
            with self.assertRaisesRegex(ValueError, 'document changed since planning'):
                apply_plan(plan)
        self.assertFalse(self.bundle.exists())
        self.assertFalse((self.hook_config.parent / '.agent-workbench-transaction.json').exists())
        self.assertEqual({'foreign': True}, json.loads(self.mcp_config.read_text()))

    def test_another_profile_cannot_adopt_owned_server(self):
        apply_plan(self.trusted((C.MCP,)))
        path = self.fixture.root / 'profiles/default.json'
        profile = json.loads(path.read_text())
        profile['id'] = 'other'
        (path.parent / 'other.json').write_bytes(shared_config.encode(profile))
        other = Workspace.load(self.fixture.root, 'other')
        pending = plan_install(other, 'claude', self.state, components=(C.MCP,), project_root=self.project)
        grants = tuple(op.trust_token for op in pending.all_operations if op.trust_token)
        plan = plan_install(other, 'claude', self.state, components=(C.MCP,), project_root=self.project, trust=grants, adopt=True)
        self.assertIn('SLOT_OWNED', {op.diagnostic for op in plan.all_operations})
        self.assertTrue(plan.has_conflicts)

    def test_tampered_parent_evidence_and_recovery_lock_fail_closed(self):
        journal = self.pending_journal()
        before = journal.read_bytes()
        with transactions._document_lock(self.hook_config.parent, False, 'claude'):
            with self.assertRaisesRegex(FileExistsError, 'locked'):
                self.plan()
        data = json.loads(before)
        data['parents'][str(self.project)][1] += 1
        journal.write_bytes(shared_config.encode(data))
        with self.assertRaisesRegex(ValueError, 'unauthenticated'):
            self.plan()
        self.assertTrue(self.bundle.exists())
        self.assertTrue(self.mcp_config.exists())
        journal.write_bytes(before)
        self.plan()
        self.assertFalse(journal.exists())

    def test_empty_explicit_claude_bundle_and_inert_handler_trust(self):
        path = self.fixture.root / 'catalog/plugins.json'
        data = json.loads(path.read_text())
        artifacts = data['plugins'][0]['artifacts']
        artifacts['claude-hooks'] = artifacts.pop('hooks')
        path.write_bytes(shared_config.encode(data))
        self.write_hooks({'hooks': {}})
        self.workspace = Workspace.load(self.fixture.root)
        apply_plan(self.trusted((C.HOOKS,)))
        self.assertFalse(self.hook_config.exists())
        self.assertTrue(self.bundle.is_dir())
        apply_uninstall(self.uninstall())
        self.write_hooks(self.native)
        plan = self.trusted((C.HOOKS,))
        apply_plan(plan)
        self.native['hooks']['Stop'][0]['hooks'][1]['prompt'] = 'changed inert data'
        self.write_hooks(self.native)
        self.assertTrue(self.plan((C.HOOKS,)).has_conflicts)

    def test_deselection_blocks_remaining_foreign_bundle_reference(self):
        apply_plan(self.trusted())
        data = json.loads(self.hook_config.read_text())
        data['hooks']['Stop'].append({'hooks': [
            {'type': 'command', 'command': str(self.bundle / 'hooks/stop.sh')}]})
        self.hook_config.write_bytes(shared_config.encode(data))
        self.assertIn('HOOK_BUNDLE_REFERENCED', {op.diagnostic for op in self.plan((C.MCP,)).all_operations})

    def test_alias_artifact_location_is_bound_into_trust(self):
        import shutil
        path = self.fixture.root / 'catalog/plugins.json'
        data = json.loads(path.read_text())
        artifacts = data['plugins'][0]['artifacts']
        artifacts['claude-hooks'] = artifacts.pop('hooks')
        path.write_bytes(shared_config.encode(data))
        self.workspace = Workspace.load(self.fixture.root)
        old = {op.trust_token for op in self.plan((C.HOOKS,)).all_operations if op.trust_token}
        shutil.copytree(self.hooks, self.hooks.with_name('new-location'))
        artifacts['claude-hooks']['path'] = 'sample/new-location'
        path.write_bytes(shared_config.encode(data))
        self.workspace = Workspace.load(self.fixture.root)
        new = {op.trust_token for op in self.plan((C.HOOKS,)).all_operations if op.trust_token}
        self.assertTrue(old and new and old.isdisjoint(new))
        self.assertFalse(self.project.exists())

    def test_incompatible_existing_settings_shape_fails_before_mutation(self):
        self.hook_config.parent.mkdir(parents=True)
        original = b'{"permissions":{},"hooks":{"Stop":[{"command":"cursor-flat"}]}}'
        self.hook_config.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.plan((C.HOOKS,))
        self.assertEqual(original, self.hook_config.read_bytes())
        self.assertFalse(self.bundle.exists())

    def test_hook_only_project_root_swap_with_same_config_inode_is_stale(self):
        apply_plan(self.trusted((C.HOOKS,)))
        plan = self.plan((C.HOOKS,))
        saved = self.project.with_name('saved')
        self.project.rename(saved)
        self.project.mkdir()
        (saved / '.claude').rename(self.hook_config.parent)
        with self.assertRaisesRegex(FileExistsError, 'changed since planning'):
            apply_plan(plan)
        self.assertTrue(self.bundle.exists())
        self.assertFalse((self.hook_config.parent / '.agent-workbench-transaction.json').exists())

    def test_foreign_reference_to_shell_quoted_bundle_blocks_removal(self):
        self.project = self.base / "project '$literal"
        self.bundle = self.project / '.claude/.agent-workbench-hooks/sample'
        self.hook_config = self.project / '.claude/settings.json'
        self.mcp_config = self.project / '.mcp.json'
        apply_plan(self.trusted((C.HOOKS,)))
        data = json.loads(self.hook_config.read_text())
        group = json.loads(json.dumps(data['hooks']['Stop'][0]))
        group['matcher'] = 'foreign'
        data['hooks']['Stop'].append(group)
        self.hook_config.write_bytes(shared_config.encode(data))
        self.assertIn('HOOK_BUNDLE_REFERENCED', {op.diagnostic for op in self.uninstall().all_operations})
        self.assertTrue(self.bundle.exists())

    def test_foreign_project_relative_bundle_reference_blocks_removal(self):
        apply_plan(self.trusted((C.HOOKS,)))
        data = json.loads(self.hook_config.read_text())
        data['hooks']['Stop'].append({'matcher': 'foreign', 'hooks': [{
            'type': 'command',
            'command': 'sh .claude/.agent-workbench-hooks/sample/hooks/stop.sh',
        }]})
        self.hook_config.write_bytes(shared_config.encode(data))

        plan = self.uninstall()

        self.assertIn('HOOK_BUNDLE_REFERENCED', {
            operation.diagnostic for operation in plan.all_operations
        })
        self.assertTrue(self.bundle.exists())
