#!/usr/bin/env python3
"""D5 apply capture/discard syscall audit, not fictitious paired fsync seams.

Python _DestinationRoot.quarantine/discard perform NO fsync. The disposable
interposer faults only actual Rust fsync calls at these no-counterpart phases.
A callback marks phase, but only the intercepted syscall can record a hit.
No journal/state/recovery fsync is suppressed. macOS uses its verified dyld
interposer unchanged. Linux x86_64 uses a disposable ptrace syscall observer
following libtest's cloned threads, including rustix's native raw SYS_fsync;
no backend flags or LD_PRELOAD substitute. Its canary requires an actual
thread syscall faulted with EIO before zero-call rows can pass. Unsupported
architectures, blocked ptrace/procfs, or old kernels fail closed, never skip.
Run with explicit TMPDIR. Linux is UNEXERCISED locally: only exact-SHA Ubuntu
CI execution establishes that platform's result. The Ubuntu parity gate calls
this file via scripts/check_rust_plan_parity.py with TMPDIR=$RUNNER_TEMP.
Standalone: TMPDIR=<scratch> python3 rust/src/transaction/capture_contract.py
Disposable C executables/libraries and fixture files live under TMPDIR.
"""
from __future__ import annotations
import errno
import json
import os
from pathlib import Path
from typing import Any
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from apply_contract import REPO, serialize, observe
from test_activation import ActivationTests
from test_workbench import Fixture
from test_components import configure
from agent_workbench import installation, transactions, shared_config
from agent_workbench.model import ComponentKind as C
from agent_workbench.workspace import Workspace

INTERPOSER = r'''
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include <limits.h>
static int audited_fsync(int fd) {
    const char *base = getenv("AW_CAPTURE_BASE");
    const char *root = getenv("AW_CAPTURE_ROOT");
    const char *fault = getenv("AW_CAPTURE_FAULT");
    char file[PATH_MAX], phase[32] = {0}, path[PATH_MAX] = {0};
    const char *role = NULL;
    if (base && root) {
        snprintf(file, sizeof(file), "%s/phase", base);
        int f = open(file, O_RDONLY);
        if (f >= 0) { (void)read(f, phase, sizeof(phase)-1); close(f); }
#ifdef __APPLE__
        (void)fcntl(fd, F_GETPATH, path);
#else
        snprintf(file, sizeof(file), "/proc/self/fd/%d", fd);
        (void)readlink(file, path, sizeof(path)-1);
#endif
        if (!strcmp(phase, "capture")) {
            if (!strcmp(path, root)) role = "capture-parent";
            else if (!strncmp(path, root, strlen(root)) &&
                     !strncmp(path + strlen(root), "/.agent-workbench-quarantine-", 29))
                role = "capture-evidence";
        } else if (!strcmp(phase, "discard") && !strcmp(path, root)) role = "discard-parent";
        if (role) {
            snprintf(file, sizeof(file), "%s/syscalls", base);
            f = open(file, O_WRONLY|O_CREAT|O_APPEND, 0600);
            if (f >= 0) { (void)write(f, role, strlen(role)); (void)write(f, "\n", 1); close(f); }
            if (fault && !strcmp(fault, role)) {
                snprintf(file, sizeof(file), "%s/hit", base);
                f = open(file, O_WRONLY|O_CREAT|O_EXCL, 0600);
                if (f >= 0) {
                    close(f);
                    errno = getenv("AW_CAPTURE_EINTR") ? EINTR : EIO;
                    return -1;
                }
            }
        }
    }
#ifdef __APPLE__
    return fsync(fd);
#else
    int (*real_fsync)(int) = dlsym(RTLD_NEXT, "fsync");
    return real_fsync(fd);
#endif
}
#ifdef __APPLE__
__attribute__((used)) static struct { const void *replacement; const void *original; }
interpose_fsync __attribute__((section("__DATA,__interpose"))) = { (const void *)audited_fsync, (const void *)fsync };
#else
int fsync(int fd) { return audited_fsync(fd); }
#endif
'''


def build(base):
    if sys.platform == 'darwin':
        source = base/'interpose.c'; source.write_text(INTERPOSER)
        library = base/'interpose.dylib'
        subprocess.run(['cc', '-Wall', '-Werror', '-dynamiclib',
                        '-fPIC', str(source), '-o', str(library)], check=True)
    elif sys.platform == 'linux' and os.uname().machine == 'x86_64':
        source = Path(__file__).with_name('capture_ptrace.c')
        library = base/'capture-ptrace'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(source), '-o', str(library)], check=True)
    else:
        raise RuntimeError(f'capture syscall audit unsupported on {sys.platform}/{os.uname().machine}; refusing zero-call success')
    p = subprocess.run(['cargo', 'test', '--lib', '--no-run', '--message-format=json',
                        '--manifest-path', str(REPO/'rust/Cargo.toml')], cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout+p.stderr
    artifacts = [json.loads(line) for line in p.stdout.splitlines() if line.startswith('{')]
    binaries = [Path(a['executable']) for a in artifacts if a.get('executable') and a.get('profile', {}).get('test')]
    assert len(binaries) == 1, binaries
    return library, binaries[0]


def rust(base, library, binary, root, role='', mode='', interrupted=False):
    for name in ('syscalls', 'hit', 'trace', 'result.json'):
        (base/name).unlink(missing_ok=True)
    env = dict(os.environ, AW_CAPTURE_BASE=str(base), AW_CAPTURE_ROOT=str(root),
               AW_CAPTURE_FAULT=role, AW_CAPTURE_MODE=mode)
    if sys.platform == 'darwin': env['DYLD_INSERT_LIBRARIES'] = str(library)
    if interrupted: env['AW_CAPTURE_EINTR'] = '1'
    else: env.pop('AW_CAPTURE_EINTR', None)
    command = [str(binary), 'transaction::capture_parity_tests::differential_capture',
               '--ignored', '--exact']
    if sys.platform == 'linux': command.insert(0, str(library))
    p = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True,
                       timeout=180 if sys.platform == 'linux' else None)
    assert p.returncode == 0, p.stdout+p.stderr
    result = json.loads((base/'result.json').read_bytes())
    result['syscalls'] = (base/'syscalls').read_text().splitlines() if (base/'syscalls').exists() else []
    result['hit'] = (base/'hit').exists()
    return result


def run_case(base, library, binary, target, lifecycle, component, role, interrupted):
    outcomes = []
    observations = []
    for engine in ('python', 'rust'):
        f = ActivationTests(); f.setUp()
        try:
            shutil.rmtree(f.base)
            f.base = base/'fixture'; f.base.mkdir()
            f.fixture = Fixture(f.base/'repo'); configure(f.fixture)
            if target == 'claude':
                (f.fixture.root/'plugins/sample/hooks/hooks.json').write_bytes(shared_config.encode({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"'}]}]}}))
            f.workspace = Workspace.load(f.fixture.root)
            f.project = f.base/'project'; f.state = f.base/'state'
            def plan():
                if lifecycle == 'uninstall':
                    return installation.plan_uninstall(f.workspace, target, f.state, project_root=f.project)
                return trusted()
            def trusted():
                kw: dict[str, Any] = dict(components=(C.SKILLS, C.AGENTS, C.HOOKS, C.MCP), project_root=f.project)
                p = installation.plan_install(f.workspace, target, f.state, **kw)
                tokens = tuple({o.trust_token for o in p.all_operations if o.trust_token})
                return installation.plan_install(f.workspace, target, f.state, trust=tokens, **kw)
            initial = trusted(); initial.state_file.parent.mkdir(parents=True)
            key = initial.state_file.parent/('.'+initial.state_file.name+'.transaction-key')
            key.write_bytes(bytes(range(32))); key.chmod(0o600)
            if lifecycle != 'install':
                installation.apply_plan(initial)
                if lifecycle == 'replace':
                    (f.fixture.root/'plugins/sample/hooks/stop.sh').write_text('#!/bin/sh\nexit 23\n')
                    with (f.fixture.root/'plugins/sample/agents/sample-agent.md').open('a') as h: h.write('\nReplacement\n')
            p = plan(); assert not p.has_conflicts
            request = serialize(p); request['uninstall'] = lifecycle == 'uninstall'
            (base/'request.json').write_text(json.dumps(request))
            root = next(o.destination.parent for o in p.operations if o.component.value == component)
            phase = ''; calls = []; capture_hit = False
            captures = 0; discards = 0
            original_quarantine = installation._DestinationRoot.quarantine
            original_discard = installation._DestinationRoot.discard
            original_sync = os.fsync
            def quarantine(self, *args, **kwargs):
                nonlocal phase, capture_hit, captures
                captures += 1
                phase = 'capture'
                try: moved = original_quarantine(self, *args, **kwargs)
                finally: phase = ''
                if role == 'interrupt' and self.path == root and not capture_hit:
                    capture_hit = True
                    self._close_quarantine(moved)
                    raise KeyboardInterrupt('capture interruption')
                return moved
            def discard(self, *args, **kwargs):
                nonlocal phase, discards
                discards += 1
                phase = 'discard'
                try: return original_discard(self, *args, **kwargs)
                finally: phase = ''
            def sync(fd):
                if phase: calls.append((phase, os.fstat(fd).st_mode))
                return original_sync(fd)
            apply = installation.apply_uninstall if lifecycle == 'uninstall' else installation.apply_plan
            result: dict[str, Any]
            if engine == 'python':
                try:
                    with patch.object(installation._DestinationRoot, 'quarantine', quarantine), patch.object(installation._DestinationRoot, 'discard', discard), patch.object(os, 'fsync', sync):
                        apply(p)
                    accepted, cls = True, ''
                except (OSError, ValueError, KeyboardInterrupt) as e:
                    accepted, cls = False, type(e).__name__
                assert calls == [], ('oracle gained real capture/discard fsync calls', calls)
                result = dict(accepted=accepted, **{'class': cls}, hit=False, syscalls=[], interrupted=capture_hit,
                              captures=captures, discards=discards)
            else:
                result = rust(base, library, binary, root, '' if role == 'interrupt' else role,
                              'interrupt' if role == 'interrupt' else '', interrupted)
            observations.append(result)
            if lifecycle != 'install' and not result['hit']:
                assert result['captures'] > 0, ('real capture not reached', result)
                if role != 'interrupt': assert result['discards'] > 0, ('real discard not reached', result)
            snapshots: list[Any] = [(result['accepted'], result['class'], observe(f.project), observe(f.state), observe(f.fixture.root))]
            journal = f.project/('.claude' if target == 'claude' else '.cursor')/'.agent-workbench-transaction.json'
            result['retained_journal'] = journal.exists()
            result['retained_evidence'] = len(list(f.project.rglob('.agent-workbench-quarantine-*')))
            if not result['accepted']:
                if engine == 'rust' and result['hit']:
                    assert result['retained_journal'] == interrupted, result
                    assert result['recovery_observed'] != interrupted, result
                if role == 'interrupt':
                    assert result['interrupted'] and journal.exists()
                    assert any(f.project.rglob('.agent-workbench-quarantine-*'))
                roots = {c: Path(r) for c, r in p.next_state.destination_roots.items()}
                if p.previous_state: roots.update({c: Path(r) for c, r in p.previous_state.destination_roots.items()})
                if journal.exists():
                    transactions._journal_record(journal.read_bytes(), p.state_file)
                    if engine == 'rust': assert rust(base, library, binary, root, mode='recover')['accepted']
                    else: transactions.recover(p.state_file, roots, target)
                snapshots.append(('recovered', observe(f.project), observe(f.state), observe(f.fixture.root)))
                retry = plan()
                if engine == 'rust':
                    r = serialize(retry); r['uninstall'] = lifecycle == 'uninstall'
                    (base/'request.json').write_text(json.dumps(r))
                    assert rust(base, library, binary, root)['accepted']
                else: apply(retry)
                assert not journal.exists()
                snapshots.append(('retried', observe(f.project), observe(f.state), observe(f.fixture.root)))
            outcomes.append(snapshots)
        finally:
            f.doCleanups(); shutil.rmtree(base/'fixture', ignore_errors=True)
    assert outcomes[0] == outcomes[1], f'{target}/{lifecycle}/{component}/{role}/{interrupted}: Python={observations[0]} Rust={observations[1]}; snapshot/evidence/retry mismatch'
    if role != 'interrupt':
        assert not observations[1]['hit'] and observations[1]['syscalls'] == [], observations
    print(f'PASS {target}/{lifecycle}/{component}/{role}/{interrupted}: actual Python fsync=0 Rust={len(observations[1]["syscalls"])}', flush=True)


def run():
    old_umask = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory(prefix='aw-capture-', dir=os.environ['TMPDIR']) as tmp:
            base = Path(tmp); library, binary = build(base)
            probe = base/'probe'; probe.mkdir()
            (base/'request.json').write_text('{}')
            canary = rust(base, library, binary, probe, 'capture-parent', 'probe')
            assert canary['hit'] and not canary['accepted'] and canary['errno'] == errno.EIO, (
                'fsync interception unavailable for this rustix backend; refusing a false zero-call GREEN', canary)
            assert canary['syscalls'] == ['capture-parent'], canary
            if sys.platform == 'linux':
                # libtest runs the probe on a clone-created thread, not the leader.
                trace = (base/'trace').read_text().splitlines()
                assert len(trace) == 1, trace
                tid, leader, role = trace[0].split()
                assert tid != leader and role == 'capture-parent', trace
            print('syscall canary: real rustix fsync intercepted with EIO', flush=True)
            rows = []
            for target in ('cursor', 'claude'):
                rows.append((target, 'install', 'hooks', 'capture-parent', False))
                for lifecycle in ('replace', 'uninstall'):
                    for component in (('agents', 'hooks') if lifecycle == 'replace' else ('skills', 'agents', 'hooks')):
                        for role in ('capture-parent', 'capture-evidence', 'discard-parent'):
                            for interrupted in (False, True): rows.append((target, lifecycle, component, role, interrupted))
                    rows.append((target, lifecycle, 'hooks', 'interrupt', False))
            if os.environ.get('AW_CAPTURE_CASES'):
                rows = [r for r in rows if '/'.join(map(str, r)) in os.environ['AW_CAPTURE_CASES'].split(',')]
                assert rows
            failures = []
            for row in rows:
                try: run_case(base, library, binary, *row)
                except AssertionError as e: failures.append(str(e)); print('FAIL', str(e), flush=True)
            assert not failures, '\n'.join(failures)
            print(f'capture contract: {len(rows)} real mixed apply rows passed; capture/discard boundaries have no Python fsync counterpart')
    finally: os.umask(old_umask)


if __name__ == '__main__': run()
