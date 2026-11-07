#!/usr/bin/env python3
"""F2: authentic interrupted producer journals consumed at their original parents."""
from __future__ import annotations

import base64
from contextlib import closing, redirect_stdout, redirect_stderr
import fcntl
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import shutil
from unittest.mock import patch

from check_python_baseline_upgrade import (require_pinned_baseline, python_install_skills,
    skills_common, prefix, run, TARGETS, env_for)
from check_cursor_activation_edges import Replay, configure_case
from check_cursor_activation_parity import REPO, prepare, snapshot, readonly_identity
from check_cursor_activation_recovery import interrupt as interrupt_apply
from check_activation_inertness import Observer, fixture as inert_fixture
from agent_workbench import cli, installation
from check_rust_plan_parity import fixture


def interrupt(base, runner, phase, uninstall, components=('skills', 'agents', 'hooks', 'mcp')):
    if runner.engine == 'rust':
        return interrupt_apply(base, runner, phase, uninstall, components)
    with patch.dict(os.environ, env_for(runner.root)):
        return interrupt_apply(base, runner, phase, uninstall, components)


def files(root):
    """Replay file/evidence bytes only; never replace an authorized parent inode."""
    return {p.relative_to(root): (stat.S_IMODE(p.lstat().st_mode),
            os.readlink(p) if p.is_symlink() else p.read_bytes(), p.is_symlink())
            for p in root.rglob('*') if not p.is_dir() or p.is_symlink()}


def replay_files(root, saved, directories):
    for p in root.rglob('*'):
        if (not p.is_dir() or p.is_symlink()) and p.relative_to(root) not in saved:
            p.unlink()
    for p in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if p.is_dir() and not p.is_symlink() and p.relative_to(root) not in directories:
            p.rmdir()
    for rel, mode in sorted(directories.items(), key=lambda item: len(item[0].parts)):
        p = root / rel
        p.mkdir(exist_ok=True)
        p.chmod(mode)
    for rel, (mode, data, link) in saved.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() or p.is_symlink():
            p.unlink()
        if link:
            p.symlink_to(data)
        else:
            p.write_bytes(data)
            p.chmod(mode)


def parents(journal):
    record = json.loads(journal.read_bytes())
    result = {Path(p): tuple(identity) for p, identity in record['parents'].items()}
    assert_parents(result)
    return result


def assert_parents(saved):
    for p, identity in saved.items():
        assert (p.stat().st_dev, p.stat().st_ino) == identity, ('parent replaced', p)


def capture(root):
    return files(root), {p.relative_to(root): stat.S_IMODE(p.stat().st_mode)
                        for p in root.rglob('*') if p.is_dir() and not p.is_symlink()}


def paired(root, python, rust, identities=None):
    saved, directories = capture(root)
    expected = python()
    tree = snapshot(root)
    if identities:
        assert_parents(identities)
    replay_files(root, saved, directories)
    if identities:
        assert_parents(identities)
    actual = rust()
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr), (expected, actual)
    assert snapshot(root) == tree, ('handoff filesystem mismatch',
        [r for r in tree if r not in snapshot(root)], [r for r in snapshot(root) if r not in tree])
    return actual


def relocation_skills(base, target, source_only=False):
    root = base / ('skills-' + target + ('-source' if source_only else ''))
    root.mkdir()
    repository = python_install_skills(root, target)
    moved = repository.with_name('moved-checkout')
    if source_only:
        (repository / 'plugins').rename(repository / 'moved-plugins')
        moved = repository
    else:
        repository.rename(moved)
    for action in ('status', 'install', 'uninstall'):
        before = snapshot(root)
        result = paired(root,
            lambda: run(prefix('python') + skills_common(root, moved, target, action), root),
            lambda: run(prefix('rust') + skills_common(root, moved, target, action), root))
        assert result.returncode in ((0, 1, 2) if source_only else (0, 1)), result
        if result.returncode == 2:
            assert snapshot(root) == before, 'unavailable source rejection mutated ownership'
    print(f'F2 relocation: Python-owned skills {target} source-only={source_only} -> Rust status/install/uninstall passed', flush=True)


def relocation_activation(base, target, source_only=False):
    root, repository = prepare(base, target=target)
    producer = Replay(root, repository, 'python', target=target)
    components = ('skills', 'agents', 'commands' if target == 'claude' else 'rules', 'hooks', 'mcp')
    producer.install(components)
    moved = repository.with_name('moved-checkout')
    if source_only:
        (repository / 'plugins').rename(repository / 'moved-plugins')
        moved = repository
    else:
        repository.rename(moved)
    producer.repository = moved
    consumer = Replay(root, moved, 'rust', target=target)
    for action in ('status', 'install', 'uninstall'):
        before = snapshot(root)
        result = paired(root, lambda: producer.call(action, components), lambda: consumer.call(action, components))
        assert result.returncode in ((0, 1, 2) if source_only else (0, 1)), result
        if result.returncode == 2:
            assert snapshot(root) == before
    print(f'F2 relocation: Python-owned {target} native/copies source-only={source_only} -> Rust status/install/uninstall passed', flush=True)


def tracer(base, target='cursor', phase='journal', engine='python', uninstall=False, relocate=False):
    with closing(Observer()) as observer:
        root, repository, hook, mcp = inert_fixture(base, target, observer)
        observer.canary(root, [sys.executable, str(hook)], 'HOOK_EXECUTED', b'hook-canary')
        observer.canary(root, [sys.executable, str(mcp)], 'MCP_STARTED', b'mcp-canary')
        recover_tracer(base, root, repository, target, phase, engine, uninstall, observer, relocate)


def recover_tracer(base, root, repository, target, phase, engine, uninstall, observer, relocate):
    components = ('skills', 'agents', 'commands' if target == 'claude' else 'rules', 'hooks', 'mcp')
    producer = Replay(root, repository, engine, target=target)
    configure_case(producer, 'foreign-fields')
    if uninstall:
        Replay(root, repository, 'python', target=target).install(components)
    interrupt(base, producer, phase, uninstall, components)
    observer.inert(root)
    journal = producer.hook_config.parent / '.agent-workbench-transaction.json'
    identities = parents(journal)
    if relocate:
        moved = repository.with_name('moved-checkout')
        repository.rename(moved)
        repository = moved
    before = readonly_identity(root)
    refused = Replay(root, repository, 'rust', target=target).call('trust', components)
    assert refused.returncode == 2 and 'pending recovery' in refused.stderr, refused
    assert readonly_identity(root) == before
    observer.inert(root)
    saved = files(root)
    directories = {p.relative_to(root): stat.S_IMODE(p.stat().st_mode)
                   for p in root.rglob('*') if p.is_dir() and not p.is_symlink()}
    # This oracle replay is characterization, NOT support for Rust -> Python upgrades.
    expected = Replay(root, repository, 'python', target=target).call('status', components)
    assert expected.returncode == (0 if uninstall else 1), expected
    expected_tree = snapshot(root)
    assert_parents(identities)
    replay_files(root, saved, directories)
    assert_parents(identities)
    consumer = Replay(root, repository, 'rust', target=target)
    actual = consumer.call('status', components)
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr), (expected, actual)
    assert snapshot(root) == expected_tree, 'Python journal -> Rust recovery filesystem mismatch'
    assert not journal.exists()
    observer.inert(root)
    consumer.install(components)
    observer.inert(root)
    tokens = consumer.grants(components)
    saved_state = consumer.state_file.read_bytes()
    hook = repository / 'plugins/sample/hooks/stop.py'
    hook.write_text(hook.read_text() + '# changed content after handoff\n')
    refused = consumer.call('install', components, tokens)
    assert refused.returncode == 1 and 'TRUST_REQUIRED' in refused.stdout, refused
    assert consumer.state_file.read_bytes() == saved_state
    consumer.call('trust', components)
    observer.inert(root)
    consumer.install(components)
    observer.inert(root)
    assert consumer.call('uninstall').returncode == 0
    observer.inert(root)
    hooks = json.loads(consumer.hook_config.read_text())
    assert (hooks['permissions'] == {'allow': ['Read']} if target == 'claude' else hooks['other'] is True)
    assert json.loads(consumer.mcp_config.read_text())['other'] == 42
    assert not consumer.bundle.exists()
    print(f'F2 tracer: {target} {engine} {phase} uninstall={uninstall} relocate={relocate} -> Rust recovery/retry/uninstall passed', flush=True)


def rejected_journal(base, target, defect):
    root, repository = prepare(base, target=target)
    producer = Replay(root, repository, 'python', target=target)
    interrupt(base, producer, 'before-state', False)
    journal = producer.hook_config.parent / '.agent-workbench-transaction.json'
    original = journal.read_bytes()
    detached = None
    parent = producer.state_file.parent
    copied = root.with_name('copied')
    if defect == 'mac':
        record = json.loads(original)
        record['mac'] = '0' * 64
        from agent_workbench import shared_config
        journal.write_bytes(shared_config.encode(record))
    elif defect == 'copy':
        shutil.copytree(root, copied, symlinks=True)
        root, repository = copied, copied / 'repo'
    elif defect == 'parent':
        detached = parent.with_name('detached')
        parent.rename(detached)
        parent.mkdir()
        for p in detached.iterdir():
            if p.is_file():
                shutil.copy2(p, parent / p.name)
    before = snapshot(base)
    consumer = Replay(root, repository, 'rust', target=target)
    for action in ('status', 'install', 'uninstall'):
        py = Replay(root, repository, 'python', target=target).call(action)
        rs = consumer.call(action)
        assert py.returncode == rs.returncode == 2, (target, defect, action, py, rs)
        assert py.stderr == rs.stderr and py.stdout == rs.stdout, (py, rs)
        assert snapshot(base) == before, (target, defect, action, 'rejection mutated evidence')
    if defect == 'mac':
        journal.write_bytes(original)
    elif defect == 'copy':
        # Only the untouched original is eligible for successful recovery.
        root, repository = producer.root, producer.repository
    elif detached:
        shutil.rmtree(parent)
        detached.rename(parent)
    consumer = Replay(root, repository, 'rust', target=target)
    assert consumer.call('status', ('skills', 'agents', 'hooks', 'mcp')).returncode == 1
    consumer.install(('skills', 'agents', 'hooks', 'mcp'))
    assert consumer.call('uninstall').returncode == 0
    if defect == 'copy':
        shutil.rmtree(copied)
    print(f'F2 {target}: {defect} journal fails closed without mutation; original parents recover', flush=True)


def locked_recovery(base, target, lock_kind):
    root, repository = prepare(base, target=target)
    producer = Replay(root, repository, 'python', target=target)
    interrupt(base, producer, 'before-state', False)
    lock = (producer.state_file.with_name('.' + producer.state_file.name + '.lock')
            if lock_kind == 'resource' else producer.hook_config.parent / '.agent-workbench.lock')
    with lock.open('r+b') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = readonly_identity(root)
        for action in ('status', 'install', 'uninstall'):
            expected = None
            for engine in ('python', 'rust'):
                result = Replay(root, repository, engine, target=target).call(action)
                assert result.returncode == 2 and 'locked by another operation' in result.stderr, result
                row = (result.returncode, result.stdout, result.stderr)
                if expected is None:
                    expected = row
                else:
                    assert row == expected, (expected, row)
                assert readonly_identity(root) == before
    consumer = Replay(root, repository, 'rust', target=target)
    assert consumer.call('status', ('skills', 'agents', 'hooks', 'mcp')).returncode == 1
    consumer.install(('skills', 'agents', 'hooks', 'mcp'))
    assert consumer.call('uninstall').returncode == 0
    print(f'F2 {target}: Python journal recovery honors held {lock_kind} lock', flush=True)


def recovery_interrupted(base, target, engine):
    root, repository = prepare(base, target=target)
    producer = Replay(root, repository, engine, target=target)
    interrupt(base, producer, 'before-state', False)
    journal = producer.hook_config.parent / '.agent-workbench-transaction.json'
    identities = parents(journal)
    # Request roots are authentic ownership data, not a Python-produced plan.
    record = json.loads(journal.read_bytes())
    state = json.loads(base64.b64decode(record['stateAfter']))
    row = dict(mode='recover', file=str(producer.state_file), target=target,
               roots=state['destinationRoots'], scenario='f2-interrupt',
               path_index=0, fault_boundary='AfterPath', observe_hit=True)
    (base / 'rows.json').write_text(json.dumps([row]))
    command = ['cargo', 'test', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
               '--lib', 'transaction_parity_tests::differential_publication', '--', '--ignored', '--exact']
    result = subprocess.run(command, cwd=REPO, env=dict(os.environ, AW_TRANSACTION_BASE=str(base)), capture_output=True, text=True)
    assert result.returncode == 0 and '1 passed' in result.stdout, result.stdout + result.stderr
    result = json.loads((base / 'results.json').read_text())[0]
    assert result == {'accepted': False, 'error_class': 'OSError', 'hit': True}, result
    assert journal.exists()
    assert_parents(identities)
    consumer = Replay(root, repository, 'rust', target=target)
    assert consumer.call('status', ('skills', 'agents', 'hooks', 'mcp')).returncode == 1
    assert not journal.exists()
    consumer.install(('skills', 'agents', 'hooks', 'mcp'))
    assert consumer.call('uninstall').returncode == 0
    print(f'F2 {target}: {engine} apply -> interrupted Rust recovery -> Rust retry passed', flush=True)


def skills_interrupted(base, target, uninstall):
    root = base / f'skills-interrupted-{target}-{uninstall}'
    root.mkdir()
    repository = fixture(root)
    second = repository / 'plugins/pstack/skills/second'
    second.mkdir()
    (second / 'SKILL.md').write_text('---\nname: second\ndescription: Second skill.\n---\n')
    if uninstall:
        result = run(prefix('python') + skills_common(root, repository, target, 'install'), root)
        assert result.returncode == 0, result
    args = skills_common(root, repository, target, 'uninstall' if uninstall else 'install')
    original = installation._write_progress
    hit = False

    def progress(*args):
        nonlocal hit
        original(*args)
        hit = True
        raise KeyboardInterrupt('F2 durable Python progress interruption')

    try:
        with patch.dict(os.environ, env_for(root)), patch.object(installation, '_write_progress', progress), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli.main(args)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('Python progress interruption not reached')
    assert hit
    for action in ('status', 'install', 'uninstall'):
        result = paired(root,
            lambda: run(prefix('python') + skills_common(root, repository, target, action), root),
            lambda: run(prefix('rust') + skills_common(root, repository, target, action), root))
        assert result.returncode in (0, 1), result
    print(f'F2 skills {target}: interrupted Python uninstall={uninstall} -> Rust status/retry/uninstall passed', flush=True)


def main():
    require_pinned_baseline()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    with tempfile.TemporaryDirectory(prefix='f2-handoff-', dir=os.environ['TMPDIR']) as raw:
        if sys.argv[1:] == ['tracer']:
            locked_recovery(Path(raw), 'cursor', 'resource')
            return
        assert not sys.argv[1:], sys.argv[1:]
        for target in ('cursor', 'claude'):
            for phase in ('journal', 'before-state'):
                for engine in ('python', 'rust'):
                    for uninstall in (False, True):
                        tracer(Path(raw), target, phase, engine, uninstall)
        for target in TARGETS:
            for source_only in (False, True):
                relocation_skills(Path(raw), target, source_only)
            for uninstall in (False, True):
                skills_interrupted(Path(raw), target, uninstall)
        for target in ('cursor', 'claude'):
            tracer(Path(raw), target, 'before-state', 'python', False, relocate=True)
            for source_only in (False, True):
                relocation_activation(Path(raw), target, source_only)
            for defect in ('mac', 'copy', 'parent'):
                rejected_journal(Path(raw), target, defect)
            for lock in ('resource', 'document'):
                locked_recovery(Path(raw), target, lock)
            for engine in ('python', 'rust'):
                recovery_interrupted(Path(raw), target, engine)


if __name__ == '__main__':
    main()
