#!/usr/bin/env python3
"""Integrated recovery persistence faults against real interrupted Rust apply intent.

Runs only private ignored Rust drivers; no production CLI race/fault switches.
Replay restores files/evidence, never authenticated parent directory inodes.

Staging races include both injected interruption and uninterrupted parent swaps;
cleanup races continue through the real fsync and ordinary late rejection. Swap
rows retry while detached, then reattach the exact authorized inode for real
recovery and operation retries. Foreign directory/symlink occupants retain their
bytes, modes and identities.

Cursor and Claude use native layouts, real interrupted apply intent, and
original authenticated parent directory inodes throughout replay.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply_contract import (REPO, ActivationTests, C, installation, shared_config,
                            transactions, serialize, observe)


def cargo(base, driver, **environment):
    result = subprocess.run(
        ['cargo', 'test', '--quiet', '--manifest-path', str(REPO/'rust/Cargo.toml'),
         '--lib', driver, '--', '--ignored', '--exact'], cwd=REPO,
        env=dict(os.environ, **environment), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def saved_tree(base):
    directories = {p: stat.S_IMODE(p.stat().st_mode) for p in base.rglob('*')
                   if p.is_dir() and not p.is_symlink()}
    files = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
             for p in base.rglob('*') if p.is_file() and not p.is_symlink()}
    links = {p: os.readlink(p) for p in base.rglob('*') if p.is_symlink()}
    return directories, files, links


def replay(base, saved):
    directories, files, links = saved
    for p in sorted(base.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if p.is_symlink() or p.is_file(): p.unlink()
        elif p not in directories: p.rmdir()
    for p, mode in sorted(directories.items(), key=lambda pair: len(pair[0].parts)):
        p.mkdir(exist_ok=True); p.chmod(mode)
    for p, (content, mode) in files.items(): p.write_bytes(content); p.chmod(mode)
    for p, target in links.items(): p.symlink_to(target)


def python_recover(plan, roots):
    try:
        transactions.recover(plan.state_file, roots, plan.target)
        return {'accepted': True}
    except (OSError, ValueError) as error:
        return {'accepted': False, 'error_class': 'ValueError' if isinstance(error, ValueError)
                else 'FileExistsError' if isinstance(error, FileExistsError) else 'OSError'}


def swap_parent(parent, directory=False):
    parent.rename(parent.with_name(parent.name + '-detached'))
    outside = parent.with_name(parent.name + '-outside')
    if directory: outside.rename(parent)
    else: parent.symlink_to(outside, target_is_directory=True)


def reattach(parent):
    outside = parent.with_name(parent.name + '-outside')
    occupant = outside if parent.is_symlink() else parent
    before = observe(occupant)
    identity = (occupant.stat().st_dev, occupant.stat().st_ino)
    if parent.is_symlink(): parent.unlink()
    else: parent.rename(outside)
    parent.with_name(parent.name + '-detached').rename(parent)
    assert observe(outside) == before
    assert (outside.stat().st_dev, outside.stat().st_ino) == identity


class Fault:
    """Scope descriptor-role/phase-specific faults to one real resource restore."""
    def __init__(self, path, edge, ordinal=1):
        self.path, self.edge, self.hit = path, edge, False
        self.ordinal, self.matches = ordinal, 0
        self.active = self.tree = self.discard = False
        self.root_syncs = self.directory_syncs = 0
        self.tree_directories = 0

    def trip(self, edge):
        selected = ('tree-root-sync' if self.edge.startswith('swap-stage') else
                    'discard-sync' if self.edge.startswith('swap-cleanup') else self.edge)
        if self.active and not self.hit and edge == selected:
            self.matches += 1
            if self.matches != self.ordinal: return
            self.hit = True
            if self.edge.startswith('swap-'):
                swap_parent(self.path.parent, self.edge.endswith('-directory'))
                if self.edge != 'swap-stage': return
            raise OSError('injected recovery persistence failure: ' + edge)

    def patches(self):
        stack = ExitStack()
        restore, discard = transactions._restore_path, transactions._discard_path_evidence
        write_tree, sync, chmod, fdopen = transactions.bundles.write_tree, os.fsync, os.fchmod, os.fdopen
        fault = self

        def restoring(root, operation, *args):
            self.active = operation.destination == self.path
            try: return restore(root, operation, *args)
            finally: self.active = False

        def discarding(root, operation, *args):
            self.active = operation.destination == self.path
            self.discard = True
            try: return discard(root, operation, *args)
            finally: self.active = self.discard = False

        def tree(name, parent, value):
            self.tree = True
            self.tree_directories = len(value.directories)
            try: return write_tree(name, parent, value)
            finally: self.tree = False

        def fsync(fd):
            if self.active:
                directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                if self.discard: self.trip('discard-sync')
                elif self.tree:
                    if directory:
                        self.directory_syncs += 1
                        self.trip('tree-root-sync' if self.directory_syncs > self.tree_directories else 'tree-directory-sync')
                    else: self.trip('tree-file-sync')
                elif directory:
                    self.root_syncs += 1
                    self.trip({1: 'capture-sync', 2: 'publish-sync', 3: 'evidence-sync'}[self.root_syncs])
                else: self.trip('file-sync')
            return sync(fd)

        def fchmod(fd, mode):
            if self.active and self.tree:
                self.trip('tree-directory-chmod' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'tree-file-chmod')
            return chmod(fd, mode)

        class Writer:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): self.handle.__enter__(); return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def __getattr__(self, name): return getattr(self.handle, name)
            def write(self, data):
                fault.trip('tree-file-write' if fault.tree else 'file-write')
                return self.handle.write(data)

        def opening(*args, **kwargs):
            handle = fdopen(*args, **kwargs)
            return Writer(handle) if self.active else handle

        for owner, name, replacement in (
            (transactions, '_restore_path', restoring),
            (transactions, '_discard_path_evidence', discarding),
            (transactions.bundles, 'write_tree', tree),
            (os, 'fsync', fsync), (os, 'fchmod', fchmod), (os, 'fdopen', opening)):
            stack.enter_context(patch.object(owner, name, replacement))
        return stack


BOUNDARIES = {
    'capture-sync': 'Path(CaptureSync)',
    'publish-sync': 'Path(PublishSync)',
    'evidence-sync': 'Path(EvidenceSync)',
    'file-write': 'Path(FileWrite)',
    'file-sync': 'Path(FileSync)',
    'tree-file-write': 'Path(TreeWrite(FileWrite))',
    'tree-file-chmod': 'Path(TreeWrite(FileChmod))',
    'tree-file-sync': 'Path(TreeWrite(FileSync))',
    'tree-directory-chmod': 'Path(TreeWrite(DirectoryChmod))',
    'tree-directory-sync': 'Path(TreeWrite(DirectorySync))',
    'tree-root-sync': 'Path(TreeWrite(RootSync))',
    'discard-sync': 'PathEvidence(EvidenceSync)',
    'swap-stage': 'Path(TreeWrite(RootSync))',
    'swap-cleanup': 'PathEvidence(EvidenceSync)',
}


def run():
    targets = os.environ.get('AW_RECOVERY_PERSISTENCE_TARGETS', 'cursor,claude').split(',')
    counts = [run_target(target) for target in targets]
    print(f'recovery persistence contract: {sum(counts)} rows passed ({dict(zip(targets, counts))})')


def run_target(target):
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    previous = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory(prefix='aw-recovery-persistence-', dir=os.environ['TMPDIR']) as tmp:
            base = Path(tmp)
            fixture = ActivationTests(); fixture.setUp()
            try:
                # Keep fixture helpers, but isolate every filesystem object beneath scratch.
                shutil.rmtree(fixture.base)
                fixture.base = base/'fixture'; fixture.base.mkdir()
                from test_workbench import Fixture
                from test_components import configure
                from agent_workbench.workspace import Workspace
                fixture.fixture = Fixture(fixture.base/'repo'); configure(fixture.fixture)
                fixture.workspace = Workspace.load(fixture.fixture.root)
                fixture.project, fixture.state = fixture.base/'project', fixture.base/'state'
                components = (C.SKILLS, C.AGENTS, C.HOOKS, C.MCP)
                if target == 'claude':
                    (fixture.fixture.root/'plugins/sample/hooks/hooks.json').write_bytes(shared_config.encode({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"'}]}]}}))
                    fixture.workspace = Workspace.load(fixture.fixture.root)
                def trusted():
                    first = installation.plan_install(fixture.workspace, target, fixture.state, components=components, project_root=fixture.project)
                    tokens = tuple({o.trust_token for o in first.all_operations if o.trust_token})
                    result = installation.plan_install(fixture.workspace, target, fixture.state, components=components, project_root=fixture.project, trust=tokens)
                    assert not result.has_conflicts, [o.as_dict() for o in result.all_operations]
                    return result
                plan = trusted()
                plan.state_file.parent.mkdir(parents=True, exist_ok=True)
                key = plan.state_file.with_name('.'+plan.state_file.name+'.transaction-key')
                key.write_bytes(bytes(range(32))); key.chmod(0o600)
                installation.apply_plan(plan)
                hooks_root = Path(plan.next_state.destination_roots[C.HOOKS])
                outside = hooks_root.with_name(hooks_root.name + '-outside')
                outside.mkdir(mode=0o751)
                (outside/'sentinel').write_bytes(b'outside recovery bytes')
                (outside/'sentinel').chmod(0o440)
                outside_before = observe(outside)
                source = fixture.fixture.root/'plugins/sample'
                agent = source/'agents/sample-agent.md'
                agent.write_text(agent.read_text() + '\nReplacement agent\n')
                (source/'hooks/stop.sh').write_text('#!/bin/sh\nexit 17\n')
                fixture.fixture.root.rename(fixture.base/'checkout')
                fixture.workspace = Workspace.load(fixture.base/'checkout')
                plan = trusted()
                (base/'request.json').write_text(json.dumps(serialize(plan)))
                cargo(base, 'transaction::apply_parity_tests::differential_apply',
                      AW_APPLY_BASE=str(base), AW_APPLY_SCENARIO='state-replace-interrupt')
                result = json.loads((base/'result.json').read_text())
                assert not result['accepted'] and result['hit'], result
                roots = {c: Path(p) for c, p in plan.next_state.destination_roots.items()}
                journal = transactions._journal_path(roots)
                record = json.loads(journal.read_bytes())
                assert {item['resource'] for item in record['paths']} == {'directory-symlink', 'file-copy', 'directory-copy'}
                link_item = next(item for item in record['paths'] if item['resource'] == 'directory-symlink')
                assert link_item['before'] != link_item['after'], (link_item, serialize(plan)['operations'])
                assert {'link': os.readlink(link_item['path'])} == link_item['after'], (link_item, os.readlink(link_item['path']))
                initial = saved_tree(fixture.base)
                identities = {p: (p.stat().st_dev, p.stat().st_ino) for p in map(Path, record['parents'])}
                assert python_recover(plan, roots) == {'accepted': True}
                (fixture.base/'checkout').rename(fixture.fixture.root)
                fixture.workspace = Workspace.load(fixture.fixture.root)
                capture_plan = trusted()
                (base/'request.json').write_text(json.dumps(serialize(capture_plan)))
                cargo(base, 'transaction::apply_parity_tests::differential_apply',
                      AW_APPLY_BASE=str(base), AW_APPLY_SCENARIO='capture-interrupt')
                captured_result = json.loads((base/'result.json').read_text())
                assert not captured_result['accepted'] and captured_result['hit'], captured_result
                capture_record = json.loads(journal.read_bytes())
                capture_initial = saved_tree(fixture.base)
                cases = [(resource, edge)
                         for resource in ('directory-symlink', 'file-copy', 'directory-copy')
                         for edge in ('capture-sync', 'publish-sync', 'evidence-sync')]
                cases += [('directory-copy', 'discard-sync')]
                cases += [('file-copy', edge) for edge in ('file-write', 'file-sync')]
                cases += [('directory-copy', 'tree-' + edge) for edge in
                          ('file-write', 'file-chmod', 'file-sync',
                           'directory-chmod', 'directory-sync', 'root-sync')]
                cases += [('directory-copy', edge) for edge in ('swap-stage', 'swap-cleanup')]
                cases += [('directory-copy', edge) for edge in
                          ('tree-directory-chmod@2', 'tree-directory-sync@2',
                           'swap-stage-continue', 'swap-stage-directory', 'swap-cleanup-directory')]
                if os.environ.get('AW_RECOVERY_PERSISTENCE_CASES'):
                    cases = [tuple(case.split(':')) for case in os.environ['AW_RECOVERY_PERSISTENCE_CASES'].split(',')]
                for resource, edge in cases:
                    edge, _, ordinal_text = edge.partition('@')
                    ordinal = int(ordinal_text or '1')
                    cleanup = edge == 'discard-sync' or edge.startswith('swap-cleanup')
                    current_record = capture_record if cleanup else record
                    current_initial = capture_initial if cleanup else initial
                    item = next(item for item in current_record['paths'] if item['resource'] == resource)
                    path = Path(item['path'])
                    boundary_edge = ('swap-stage' if edge.startswith('swap-stage') else 'swap-cleanup' if edge.startswith('swap-cleanup') else edge)
                    row = dict(mode='recover', file=str(plan.state_file), target=target,
                               roots={c.value: str(p) for c,p in roots.items()}, scenario='persistence',
                               fault_boundary=BOUNDARIES[boundary_edge], fault_ordinal=ordinal, observe_hit=True,
                               swap_parent=str(path.parent) if edge.startswith('swap-') else None,
                               swap_continue=edge != 'swap-stage', swap_directory=edge.endswith('-directory'),
                               path_index=list(reversed(current_record['paths'])).index(item), journal=str(journal))

                    def rust_recover(fault=True):
                        (base/'rows.json').write_text(json.dumps([row if fault else dict(row, fault_boundary='')]))
                        cargo(base, 'transaction_parity_tests::differential_publication', AW_TRANSACTION_BASE=str(base))
                        actual = json.loads((base/'results.json').read_text())[0]
                        hit = actual.pop('hit', False)
                        return actual, hit

                    def outside_identity():
                        occupant = path.parent if edge.endswith('-directory') and not outside.exists() else outside
                        return [(p.stat().st_dev, p.stat().st_ino) for p in (occupant, occupant/'sentinel')]

                    def assert_outside(identity):
                        occupant = path.parent if edge.endswith('-directory') and not outside.exists() else outside
                        assert observe(occupant) == outside_before and outside_identity() == identity

                    replay(fixture.base, current_initial)
                    outside_before_fault = outside_identity()
                    swap_retry = {'accepted': False, 'error_class': 'ValueError' if edge.endswith('-directory') else 'OSError'}
                    fault = Fault(path, edge, ordinal)
                    with fault.patches(): expected = python_recover(plan, roots)
                    assert fault.hit and expected == swap_retry, (resource, edge, fault.hit, expected)
                    assert_outside(outside_before_fault)
                    first = observe(fixture.base)
                    if edge.startswith('swap-'):
                        assert python_recover(plan, roots) == swap_retry
                        assert observe(fixture.base) == first, 'blocked swapped-parent retry changed evidence'
                        reattach(path.parent)
                    expected_retry = python_recover(plan, roots)
                    blocked = edge in {'file-write', 'tree-file-write', 'tree-file-chmod',
                                       'tree-file-sync', 'tree-directory-chmod', 'tree-directory-sync'}
                    if edge == 'tree-directory-sync' and ordinal == 2: blocked = False
                    assert expected_retry == ({'accepted': False, 'error_class': 'ValueError'} if blocked else {'accepted': True}), (resource, edge, expected_retry)
                    if blocked: assert journal.exists(), 'blocked retry discarded authenticated intent'
                    after_retry = observe(fixture.base)
                    replay(fixture.base, current_initial)
                    outside_before_fault = outside_identity()
                    actual, hit = rust_recover()
                    assert hit, (resource, edge, 'Rust fault not reached', actual)
                    assert actual == expected, (resource, edge, expected, actual)
                    assert_outside(outside_before_fault)
                    actual_snapshot = observe(fixture.base)
                    assert actual_snapshot == first, (resource, edge, 'first-failure typed snapshot',
                        {'python_only': [r for r in first if r not in actual_snapshot],
                         'rust_only': [r for r in actual_snapshot if r not in first]})
                    if edge.startswith('swap-'):
                        assert rust_recover(False) == (swap_retry, False)
                        assert observe(fixture.base) == first, 'blocked Rust swapped-parent retry changed evidence'
                        reattach(path.parent)
                    interrupted = saved_tree(fixture.base)
                    assert python_recover(plan, roots) == expected_retry
                    assert observe(fixture.base) == after_retry, (resource, edge, 'Rust -> Python retry snapshot')
                    replay(fixture.base, interrupted)
                    assert rust_recover(False) == (expected_retry, False)
                    assert observe(fixture.base) == after_retry, (resource, edge, 'Rust -> Rust retry snapshot')
                    if not blocked:
                        # Rebuild plans from the recovered live state, not stale
                        # pre-interruption revisions, then retry the operation.
                        fixture.workspace = Workspace.load(fixture.fixture.root if cleanup else fixture.base/'checkout')
                        recovered = saved_tree(fixture.base)
                        retry_outside_identity = outside_identity()
                        installation.apply_plan(trusted())
                        assert_outside(retry_outside_identity)
                        assert not journal.exists()
                        published = observe(fixture.base)
                        replay(fixture.base, recovered)
                        retry_outside_identity = outside_identity()
                        (base/'request.json').write_text(json.dumps(serialize(trusted())))
                        cargo(base, 'transaction::apply_parity_tests::differential_apply',
                              AW_APPLY_BASE=str(base), AW_APPLY_SCENARIO='replace')
                        retried = json.loads((base/'result.json').read_text())
                        assert retried['accepted'] and not retried['hit'], (resource, edge, retried)
                        assert observe(fixture.base) == published, (resource, edge, 'real apply retry typed snapshot')
                        assert_outside(retry_outside_identity)
                    assert all((p.stat().st_dev, p.stat().st_ino) == identity for p, identity in identities.items())
                    print(f'{target}:{resource}:{edge}@{ordinal}: first failure and real cross-engine retries agree ({expected_retry})')
                return len(cases)
            finally: fixture.doCleanups()
    finally: os.umask(previous)


if __name__ == '__main__': run()
