#!/usr/bin/env python3
"""F3: fail-closed, real filesystem prerequisites for the full parity suite."""
from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'scripts')]
from agent_workbench import bundles
from check_cursor_activation_parity import snapshot
from parity_oracle import require_python312


def probe(root):
    root.mkdir()
    for function in (os.open, os.stat, os.mkdir, os.symlink, os.readlink, os.link, os.unlink):
        assert function in os.supports_dir_fd, f'dir_fd unavailable: {function.__name__}'
    fd = bundles.open_directory(root)
    try:
        child = os.open('private', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        try:
            os.fchmod(child, 0o600)
            assert stat.S_IMODE(os.fstat(child).st_mode) == 0o600
            os.write(child, b'owned')
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(fd)
        os.symlink('.', 'alias', dir_fd=fd)
        try:
            unexpected = os.open('alias', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError as error:
            assert error.errno in (errno.ELOOP, errno.ENOTDIR), error
        else:
            os.close(unexpected)
            raise AssertionError('O_NOFOLLOW followed directory symlink')
        assert os.readlink('alias', dir_fd=fd) == '.'
        os.link('private', 'published', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        assert os.stat('private', dir_fd=fd).st_ino == os.stat('published', dir_fd=fd).st_ino
        try:
            os.link('private', 'published', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            pass
        else:
            raise AssertionError('hardlink publication replaced an occupied path')
        for name in ('stage', 'occupied'):
            os.mkdir(name, 0o700, dir_fd=fd)
            (root / name).chmod(0o700)
            assert stat.S_IMODE((root / name).stat().st_mode) == 0o700
        try:
            bundles.rename_new('stage', 'occupied', fd)
        except FileExistsError:
            pass
        else:
            raise AssertionError('exclusive directory rename replaced an occupied directory')
        bundles.rename_new('stage', 'bundle', fd)
        assert (root / 'occupied').is_dir() and (root / 'bundle').is_dir()
        lock = os.open('lock', os.O_RDWR | os.O_CREAT, 0o600, dir_fd=fd)
        other = os.open('lock', os.O_RDWR, dir_fd=fd)
        try:
            os.fchmod(lock, 0o600)
            assert stat.S_IMODE(os.fstat(lock).st_mode) == 0o600
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError('independent descriptor bypassed flock')
        finally:
            os.close(lock)
            os.close(other)
        # Keep the authorized inode open while the lexical parent is substituted.
        pinned = os.fstat(fd)
        detached = root.with_name('detached')
        root.rename(detached)
        root.mkdir()
        (root / 'foreign').write_bytes(b'outside')
        assert os.fstat(fd).st_ino == pinned.st_ino != root.stat().st_ino
        os.unlink('published', dir_fd=fd)
        assert (root / 'foreign').read_bytes() == b'outside'
        assert not (detached / 'published').exists()
        (root / 'foreign').unlink()
        root.rmdir()
        detached.rename(root)
        os.fsync(fd)
    finally:
        os.close(fd)
    return snapshot(root)


def main():
    require_python312()
    assert sys.platform in ('darwin', 'linux'), 'F3 supports macOS/Linux only'
    assert os.environ.get('TMPDIR'), 'explicit job-owned scratch TMPDIR required'
    with tempfile.TemporaryDirectory(prefix='platform-primitives-', dir=os.environ['TMPDIR']) as raw:
        base = Path(raw).resolve()
        root = base / 'fixture'
        expected = probe(root)
        shutil.rmtree(root)  # No ownership or authenticated journal exists in this primitive-only fixture.
        result = subprocess.run(['cargo', 'test', '--quiet', '--manifest-path', str(REPO / 'rust/Cargo.toml'),
            '--lib', 'platform_parity_tests::required_filesystem_primitives', '--', '--exact'],
            cwd=REPO, env=dict(os.environ, AW_PLATFORM_BASE=str(base)), capture_output=True, text=True)
        assert result.returncode == 0 and '1 passed' in result.stdout, result.stdout + result.stderr
        actual = snapshot(root)
        assert actual == expected, ('platform primitive effects differ', expected, actual)
    print(f'F3 filesystem primitives: {platform.system()}/{platform.machine()}; Python {platform.python_version()}; '
          'dir_fd/nofollow, file+directory fsync, modes, hardlink no-replace, native exclusive rename, flock, pinned-parent mutation passed')


if __name__ == '__main__':
    main()
