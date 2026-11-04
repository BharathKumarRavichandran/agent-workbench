#!/usr/bin/env python3
"""D8: real executable tripwires, loopback endpoints and no implicit grants.

Only harmless, explicitly invoked canaries execute. Malicious-looking Markdown,
prompt and opaque artifact text remain data throughout validation and lifecycle.
The Rust public validate command belongs to Section E and stays fail-closed;
its actual native inventory validator is exercised through a private test driver.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile

from check_cursor_activation_edges import Replay
from check_cursor_activation_parity import REPO, RUST, prepare, snapshot, readonly_identity


class Observer:
    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(8)
        self.socket.setblocking(False)
        self.port = self.socket.getsockname()[1]

    def messages(self):
        messages = []
        while True:
            try:
                connection, _ = self.socket.accept()
            except BlockingIOError:
                break
            with connection:
                connection.settimeout(2)
                messages.append(connection.recv(4096))
        return messages

    def inert(self, root):
        for name in ('HOOK_EXECUTED', 'MCP_STARTED', 'PAYLOAD_EXECUTED', 'MALICIOUS_TEXT_EXECUTED'):
            assert not (root / name).exists(), f'executable tripwire fired: {name}'
        assert not self.messages(), 'declared loopback endpoint was contacted'

    def canary(self, root, command, marker, message):
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        # A real execution must make the observer RED, not merely increment a mock.
        try:
            self.inert(root)
        except AssertionError as error:
            assert 'executable tripwire fired' in str(error), error
        else:
            raise AssertionError('execution canary escaped its observer')
        (root / marker).unlink()
        assert self.messages() == [message], 'network canary did not reach real listener'
        self.inert(root)

    def close(self):
        self.socket.close()


def payload(path, marker, observer, message):
    path.write_text('from pathlib import Path\nimport socket\n'
                    + f'Path({str(marker)!r}).write_text("harmless tripwire")\n'
                    + f'with socket.create_connection(("127.0.0.1", {observer.port}), timeout=2) as channel:\n'
                    + f'    channel.sendall({message!r})\n')
    path.chmod(0o751)


def fixture(base, target, observer):
    root, repository = prepare(base, target=target)
    hook = repository / 'plugins/sample/hooks/stop.py'
    mcp = root / 'tripwire_mcp.py'
    payload(hook, root / 'HOOK_EXECUTED', observer, b'hook-canary')
    payload(mcp, root / 'MCP_STARTED', observer, b'mcp-canary')
    malicious_shell = '$(touch ' + shlex.quote(str(root / 'MALICIOUS_TEXT_EXECUTED')) + ')'
    malicious_python = '__import__("pathlib").Path(' + repr(str(root / 'MALICIOUS_TEXT_EXECUTED')) + ').write_text("inert text was evaluated")'
    data = {'hooks': {'Stop': [{'matcher': '*', 'hooks': [
        {'type': 'command', 'command': 'python3 ./hooks/stop.py'},
        {'type': 'prompt', 'prompt': malicious_shell + '; ' + malicious_python}]}]}} if target == 'claude' else {
        'version': 1, 'hooks': {'stop': [{'command': 'python3 ./hooks/stop.py'}]}}
    (hook.parent / 'hooks.json').write_text(json.dumps(data))
    (repository / 'plugins/sample/mcp.json').write_text(json.dumps({'mcpServers': {
        'executable-tripwire': {'command': sys.executable, 'args': [str(mcp)]},
        'network-tripwire': {'type': 'http', 'url': f'http://127.0.0.1:{observer.port}/must-not-connect'},
    }}))
    # These strings are deliberately executable-looking but are never invoked,
    # including by the positive controls (which use only the harmless payloads).
    text = '\n' + malicious_shell + '\n<script>fetch("http://127.0.0.1:' + str(observer.port) + '/inert-text")</script>\n' + malicious_python + '\n'
    skill = repository / 'plugins/sample/skills/sample-skill/SKILL.md'
    skill.write_text(skill.read_text() + text)
    (hook.parent / 'opaque-payload.txt').write_text(text)
    return root, repository, hook, mcp


def validate_inventory(base, runner):
    before = readonly_identity(runner.root)
    if runner.engine == 'python':
        code = ('from pathlib import Path; import sys; from agent_workbench.workspace import Workspace; '
                'from agent_workbench.validation import validate; '
                'result=validate(Workspace.load(Path(sys.argv[1]), "pstack"), check_generated=False); '
                'print(result); raise SystemExit(not result.valid)')
        result = subprocess.run([sys.executable, '-c', code, str(runner.repository)], cwd=REPO,
            env=dict(os.environ, HOME=str(runner.root / 'home'), PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        args = ['--root', str(runner.repository), '--profile', 'pstack', 'plan', '--target', runner.target,
                '--project-root', str(runner.root / 'project'), '--state-dir', str(runner.root / 'state'),
                '--component', 'hooks', '--component', 'mcp', '--json']
        (base / 'args.json').write_text(json.dumps(args))
        result = subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
            '--lib', 'activation_parity_tests::inert_native_validation', '--', '--ignored', '--exact'], cwd=REPO,
            env=dict(os.environ, AW_PUBLIC_ACTIVATION_BASE=str(base)), capture_output=True, text=True)
        assert result.returncode == 0 and '1 passed' in result.stdout, result.stdout + result.stderr
        assert json.loads((base / 'validation-result.json').read_text()) == {'valid': True}
        # Do not widen Section E just to make a public validation claim.
        public = subprocess.run([str(RUST), '--root', str(runner.repository), '--profile', 'pstack', 'validate', '--json'],
            cwd=REPO, capture_output=True, text=True,
            env=dict(os.environ, HOME=str(runner.root / 'home')))
        assert public.returncode == 2 and 'unsupported subcommand' in public.stderr
    assert before == readonly_identity(runner.root), 'validation mutated fixture identity/bytes'
    return snapshot(runner.root)


def scenario(base, runner, hook, mcp, observer):
    root = runner.root
    observer.canary(root, [sys.executable, str(hook)], 'HOOK_EXECUTED', b'hook-canary')
    observer.canary(root, [sys.executable, str(mcp)], 'MCP_STARTED', b'mcp-canary')
    key = runner.state_file.with_name('.' + runner.state_file.name + '.transaction-key')
    key.unlink()
    original = snapshot(root)
    validated = validate_inventory(base, runner)
    observer.inert(root)
    assert validated == original and not key.exists()
    components = ('skills', 'agents', 'commands' if runner.target == 'claude' else 'rules', 'hooks', 'mcp')
    for action in ('plan', 'trust', 'status', 'install'):
        result = runner.call(action, components)
        assert result.returncode == 1 and 'TRUST_REQUIRED' in result.stdout, result
        assert snapshot(root) == original, (runner.target, runner.engine, action, 'implicit mutation/grant')
        assert not key.exists() and not runner.state_file.exists()
        observer.inert(root)
    tokens = runner.grants(components)
    observer.inert(root)
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)
    result = runner.call('install', components, tokens)
    assert result.returncode == 0, result
    observer.inert(root)
    settings = json.loads(runner.hook_config.read_text())
    configured = settings['hooks']['Stop'][0]['hooks'][0]['command'] if runner.target == 'claude' else settings['hooks']['stop'][0]['command']
    assert str(runner.bundle / 'hooks/stop.py') in configured
    assert (runner.bundle / 'hooks/stop.py').read_bytes() == hook.read_bytes()
    saved_state = runner.state_file.read_bytes()
    for action in ('plan', 'trust', 'status', 'install'):
        result = runner.call(action, components)
        assert result.returncode == 0, result
        assert runner.state_file.read_bytes() == saved_state
        observer.inert(root)
    # A new content-bound token may be inspected, but no read/failed install grants it.
    hook.write_text(hook.read_text() + '# changed executable content\n')
    for action in ('plan', 'trust', 'status', 'install'):
        result = runner.call(action, components, tokens if action == 'install' else ())
        assert result.returncode == 1 and 'TRUST_REQUIRED' in result.stdout, result
        assert runner.state_file.read_bytes() == saved_state
        observer.inert(root)
    # Prove the exact installed command still triggers the execution observer.
    observer.canary(root, shlex.split(configured), 'HOOK_EXECUTED', b'hook-canary')
    result = runner.call('uninstall')
    assert result.returncode == 0, result
    observer.inert(root)
    return validated, runner.rows


def main():
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    subprocess.run(['cargo', 'build', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml')], cwd=REPO, check=True)
    rows = 0
    with tempfile.TemporaryDirectory(prefix='activation-inertness-', dir=os.environ['TMPDIR']) as raw:
        base = Path(raw)
        for target in ('cursor', 'claude'):
            observer = Observer()
            try:
                expected = None
                for engine in ('python', 'rust'):
                    root, repository, hook, mcp = fixture(base, target, observer)
                    runner = Replay(root, repository, engine, target=target)
                    runner.timeout = 15
                    actual = scenario(base, runner, hook, mcp, observer)
                    if expected is None:
                        expected = actual
                    else:
                        assert len(expected[1]) == len(actual[1])
                        assert expected == actual, (target, 'inertness output/typed fixture mismatch')
                    shutil.rmtree(root)
                rows += len(expected[1])
                print(f'{target} inertness: {len(expected[1])} paired public rows plus real native validation; execution/network canaries passed', flush=True)
            finally:
                observer.close()
    print(f'Activation inertness: {rows} paired public CLI rows; both native validators; no implicit grants; live executable and loopback canaries passed')


if __name__ == '__main__':
    main()
