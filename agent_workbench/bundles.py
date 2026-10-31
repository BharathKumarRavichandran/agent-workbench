"""Descriptor-relative, inert snapshots of complete executable bundles."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TreeFile:
    path: str
    mode: int
    content: bytes


@dataclass(frozen=True)
class Tree:
    directories: tuple[tuple[str, int], ...]
    files: tuple[TreeFile, ...]

    @property
    def digest(self) -> str:
        records = [(name, mode, None) for name, mode in self.directories]
        records += [(item.path, item.mode, hashlib.sha256(item.content).hexdigest()) for item in self.files]
        return hashlib.sha256(json.dumps(sorted(records), separators=(',', ':')).encode()).hexdigest()


def open_directory(path: Path) -> int:
    """Walk from / without following any symlink, including ancestors."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('directory must be absolute and contained')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_tree(name: str, parent_fd: int) -> Tree:
    directories = []
    files = []

    def visit(name: str, parent: int, prefix: str) -> None:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            if mode & ~0o777:
                raise ValueError('bundle special permission bits are unsupported')
            directories.append((prefix, mode))
            for child in sorted(os.listdir(fd)):
                relative = prefix + '/' + child if prefix else child
                info = os.stat(child, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(child, fd, relative)
                elif stat.S_ISREG(info.st_mode):
                    item = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(item, 'rb') as handle:
                        before = os.fstat(handle.fileno())
                        if not stat.S_ISREG(before.st_mode):
                            raise ValueError('bundle file is not regular')
                        content = handle.read()
                        after = os.fstat(handle.fileno())
                        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                            raise ValueError('bundle changed while reading')
                    if stat.S_IMODE(before.st_mode) & ~0o777:
                        raise ValueError('bundle special permission bits are unsupported')
                    files.append(TreeFile(relative, stat.S_IMODE(before.st_mode), content))
                else:
                    raise ValueError('bundle contains a symlink or special file')
        finally:
            os.close(fd)
    visit(name, parent_fd, '')
    return Tree(tuple(directories), tuple(files))


def source_tree(source: Path) -> Tree:
    fd = open_directory(source.parent)
    try:
        tree = read_tree(source.name, fd)
    finally:
        os.close(fd)
    # Synthetic plugin root: the artifact is always its hooks directory.
    return Tree((('', 0o755),) + tuple(('hooks' + ('/' + name if name else ''), mode)
                for name, mode in tree.directories),
                tuple(TreeFile('hooks/' + item.path, item.mode, item.content) for item in tree.files))


def tree_digest(path: Path | str, parent_fd: int | None = None) -> str | None:
    owned_fd = None
    try:
        if parent_fd is None:
            path = Path(path)
            owned_fd = open_directory(path.parent)
            parent_fd = owned_fd
            path = path.name
        return read_tree(str(path), parent_fd).digest
    except (OSError, ValueError):
        return None
    finally:
        if owned_fd is not None:
            os.close(owned_fd)


def write_tree(name: str, parent_fd: int, tree: Tree) -> None:
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        for path, mode in sorted(tree.directories, key=lambda item: item[0].count('/')):
            if path:
                os.mkdir(path, 0o700, dir_fd=fd)
        for item in tree.files:
            out = os.open(item.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(out, 'wb') as handle:
                handle.write(item.content)
                handle.flush()
                os.fchmod(handle.fileno(), item.mode)
                os.fsync(handle.fileno())
        for path, mode in reversed(tree.directories):
            child_fd = os.open(path or '.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                os.fchmod(child_fd, mode)
                os.fsync(child_fd)
            finally:
                os.close(child_fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def delete_tree(name: str, parent_fd: int) -> None:
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | 0o700)
        for child in os.listdir(fd):
            mode = os.stat(child, dir_fd=fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                delete_tree(child, fd)
            else:
                os.unlink(child, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)


def rename_new(source: str, destination: str, parent_fd: int, destination_fd: int | None = None) -> None:
    """Atomic directory publish with no replacement, on macOS and Linux."""
    import ctypes
    import errno
    library = ctypes.CDLL(None, use_errno=True)
    if hasattr(library, 'renameatx_np'):
        function = library.renameatx_np
        flag = 0x00000004  # RENAME_EXCL (Darwin)
    elif hasattr(library, 'renameat2'):
        function = library.renameat2
        flag = 1  # RENAME_NOREPLACE (Linux)
    else:
        raise OSError('atomic no-replace directory rename is unavailable')
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd if destination_fd is None else destination_fd, os.fsencode(destination), flag):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, 'destination changed since planning', destination)
        raise OSError(error, os.strerror(error), destination)
