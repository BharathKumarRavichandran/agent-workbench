//! Inert, closed bundle snapshots. Production activation remains disabled.
#![cfg_attr(not(test), allow(dead_code))]
use rustix::fd::{AsFd, OwnedFd};
use rustix::fs::{self as rfs, Mode, OFlags};
use sha2::{Digest, Sha256};
use std::ffi::{OsStr, OsString};
use std::fmt::Write as _;
use std::fs::File;
use std::io::{self, Read, Write};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::path::{Component, Path, PathBuf};

#[derive(Clone, Debug, PartialEq, Eq)]
struct TreeFile {
    path: PathBuf,
    mode: u32,
    content: Vec<u8>,
}

/// Only no-follow traversal can construct a tree: callers cannot inject paths,
/// permission bits, duplicate records, or bytes that were not snapshotted.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Tree {
    directories: Vec<(PathBuf, u32)>,
    files: Vec<TreeFile>,
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

pub(super) fn open_directory(path: &Path) -> io::Result<OwnedFd> {
    if !path.is_absolute() || path.components().any(|p| p == Component::ParentDir) {
        return Err(invalid("directory must be absolute and contained"));
    }
    let mut fd = rfs::open("/", OFlags::RDONLY | OFlags::DIRECTORY, Mode::empty())?;
    for part in path.components().skip(1) {
        fd = directory_at(&fd, part.as_os_str())?;
    }
    Ok(fd)
}

fn directory_at(parent: &impl AsFd, name: impl rustix::path::Arg) -> io::Result<OwnedFd> {
    Ok(rfs::openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    )?)
}

/// Python's os.fsdecode uses UTF-8 plus surrogateescape on POSIX. Keep raw
/// path bytes for syscalls, and scalar values (including DC80..DCFF) for sorting.
fn python_name(name: &OsStr) -> Vec<u32> {
    let mut remaining = name.as_bytes();
    let mut points = Vec::new();
    while !remaining.is_empty() {
        match std::str::from_utf8(remaining) {
            Ok(text) => {
                points.extend(text.chars().map(u32::from));
                break;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                points.extend(
                    std::str::from_utf8(&remaining[..valid])
                        .unwrap()
                        .chars()
                        .map(u32::from),
                );
                remaining = &remaining[valid..];
                let invalid = error.error_len().unwrap_or(remaining.len());
                points.extend(
                    remaining[..invalid]
                        .iter()
                        .map(|byte| 0xdc00 + u32::from(*byte)),
                );
                remaining = &remaining[invalid..];
            }
        }
    }
    points
}

/// A JSON string that also handles unpaired surrogateescape scalars, which
/// serde_json::Value cannot represent. Match json.dumps(ensure_ascii=True).
fn quoted_name(points: &[u32]) -> String {
    let mut text = String::from("\"");
    for &point in points {
        match point {
            8 => text.push_str("\\b"),
            9 => text.push_str("\\t"),
            10 => text.push_str("\\n"),
            12 => text.push_str("\\f"),
            13 => text.push_str("\\r"),
            34 => text.push_str("\\\""),
            92 => text.push_str("\\\\"),
            0..=31 | 127..=65535 => {
                write!(text, "\\u{point:04x}").unwrap();
            }
            32..=126 => text.push(char::from_u32(point).unwrap()),
            _ => {
                let offset = point - 0x10000;
                write!(
                    text,
                    "\\u{:04x}\\u{:04x}",
                    0xd800 + (offset >> 10),
                    0xdc00 + (offset & 0x3ff)
                )
                .unwrap();
            }
        }
    }
    text.push('"');
    text
}

fn names(fd: &impl AsFd) -> io::Result<Vec<OsString>> {
    let mut names = Vec::new();
    for entry in rfs::Dir::read_from(fd)? {
        let entry = entry?;
        let bytes = entry.file_name().to_bytes();
        if bytes != b"." && bytes != b".." {
            names.push(OsString::from_vec(bytes.to_vec()));
        }
    }
    names.sort_by_cached_key(|name| python_name(name));
    Ok(names)
}

#[allow(clippy::unnecessary_cast)] // st_mode is u16 on Darwin, u32 on Linux.
fn mode(info: &rfs::Stat) -> io::Result<u32> {
    let mode = info.st_mode as u32 & 0o7777;
    if mode & !0o777 != 0 {
        return Err(invalid("bundle special permission bits are unsupported"));
    }
    Ok(mode)
}

pub(super) fn read_tree(name: impl AsRef<OsStr>, parent: &impl AsFd) -> io::Result<Tree> {
    fn visit(name: &OsStr, parent: &impl AsFd, prefix: &Path, tree: &mut Tree) -> io::Result<()> {
        let fd = directory_at(parent, name)?;
        tree.directories
            .push((prefix.to_owned(), mode(&rfs::fstat(&fd)?)?));
        for child in names(&fd)? {
            let relative = prefix.join(&child);
            let info = rfs::statat(&fd, child.as_os_str(), rfs::AtFlags::SYMLINK_NOFOLLOW)?;
            let kind = rfs::FileType::from_raw_mode(info.st_mode);
            if kind.is_dir() {
                visit(&child, &fd, &relative, tree)?;
            } else if kind.is_file() {
                let item = rfs::openat(
                    &fd,
                    child.as_os_str(),
                    OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
                    Mode::empty(),
                )?;
                let before = rfs::fstat(&item)?;
                if !rfs::FileType::from_raw_mode(before.st_mode).is_file() {
                    return Err(invalid("bundle file is not regular"));
                }
                let mut reader = File::from(item);
                let mut content = Vec::new();
                reader.read_to_end(&mut content)?;
                #[cfg(test)]
                super::bundle_parity_tests::after_read(&relative)?;
                let after = rfs::fstat(&reader)?;
                if (
                    before.st_ino,
                    before.st_size,
                    before.st_mtime,
                    before.st_mtime_nsec,
                    before.st_ctime,
                    before.st_ctime_nsec,
                ) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime,
                    after.st_mtime_nsec,
                    after.st_ctime,
                    after.st_ctime_nsec,
                ) {
                    return Err(invalid("bundle changed while reading"));
                }
                tree.files.push(TreeFile {
                    path: relative,
                    mode: mode(&before)?,
                    content,
                });
            } else {
                return Err(invalid("bundle contains a symlink or special file"));
            }
        }
        Ok(())
    }
    let mut tree = Tree {
        directories: Vec::new(),
        files: Vec::new(),
    };
    visit(name.as_ref(), parent, Path::new(""), &mut tree)?;
    Ok(tree)
}

pub(super) fn source_tree(source: &Path) -> io::Result<Tree> {
    let parent = open_directory(
        source
            .parent()
            .ok_or_else(|| invalid("source has no parent"))?,
    )?;
    let tree = read_tree(
        source
            .file_name()
            .ok_or_else(|| invalid("source has no name"))?,
        &parent,
    )?;
    let mut directories = vec![(PathBuf::new(), 0o755)];
    directories.extend(tree.directories.into_iter().map(|(name, mode)| {
        (
            if name.as_os_str().is_empty() {
                PathBuf::from("hooks")
            } else {
                Path::new("hooks").join(name)
            },
            mode,
        )
    }));
    Ok(Tree {
        directories,
        files: tree
            .files
            .into_iter()
            .map(|item| TreeFile {
                path: Path::new("hooks").join(&item.path),
                ..item
            })
            .collect(),
    })
}

impl Tree {
    pub(super) fn validate_source(source: &Path, expected: &str) -> io::Result<Self> {
        let tree = source_tree(source)?;
        if tree.digest()? != expected {
            return Err(invalid("copy source changed since planning"));
        }
        Ok(tree)
    }

    pub(super) fn digest(&self) -> io::Result<String> {
        let mut records: Vec<(Vec<u32>, u32, Option<String>)> = self
            .directories
            .iter()
            .map(|(name, mode)| (python_name(name.as_os_str()), *mode, None))
            .collect();
        records.extend(self.files.iter().map(|item| {
            (
                python_name(item.path.as_os_str()),
                item.mode,
                Some(format!("{:x}", Sha256::digest(&item.content))),
            )
        }));
        records.sort();
        let records: Vec<_> = records
            .into_iter()
            .map(|(name, mode, digest)| {
                format!(
                    "[{},{mode},{}]",
                    quoted_name(&name),
                    digest.map_or_else(|| "null".into(), |digest| format!("\"{digest}\""))
                )
            })
            .collect();
        Ok(format!(
            "{:x}",
            Sha256::digest(format!("[{}]", records.join(",")))
        ))
    }
}

pub(super) fn tree_digest(name: &str, parent: &impl AsFd) -> Option<String> {
    read_tree(name, parent).and_then(|tree| tree.digest()).ok()
}

/// Root and children remain private until exact modes and bytes have been fsynced.
/// The immutable snapshot, not a live checkout, is the only input to publication.
pub(super) fn write_tree(name: &str, parent: &impl AsFd, tree: &Tree) -> io::Result<()> {
    rfs::mkdirat(parent, name, Mode::from_raw_mode(0o700))?;
    let result = (|| {
        let fd = directory_at(parent, name)?;
        let mut directories: Vec<_> = tree.directories.iter().collect();
        directories.sort_by_key(|(path, _)| {
            path.as_os_str()
                .as_bytes()
                .iter()
                .filter(|&&byte| byte == b'/')
                .count()
        });
        for (path, _) in directories {
            if !path.as_os_str().is_empty() {
                rfs::mkdirat(&fd, path, Mode::from_raw_mode(0o700))?;
            }
        }
        for item in &tree.files {
            let out = rfs::openat(
                &fd,
                &item.path,
                OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
                Mode::from_raw_mode(0o600),
            )?;
            let mut handle = File::from(out);
            handle.write_all(&item.content)?;
            handle.flush()?;
            rfs::fchmod(&handle, Mode::from_raw_mode(item.mode as _))?;
            handle.sync_all()?;
        }
        for (path, mode) in tree.directories.iter().rev() {
            let child = directory_at(
                &fd,
                if path.as_os_str().is_empty() {
                    Path::new(".")
                } else {
                    path
                },
            )?;
            rfs::fchmod(&child, Mode::from_raw_mode(*mode as _))?;
            rfs::fsync(&child)?;
        }
        rfs::fsync(&fd)?;
        Ok(())
    })();
    if result.is_err() {
        delete_tree(name, parent)?;
    }
    result
}

pub(super) fn delete_tree(name: impl AsRef<OsStr>, parent: &impl AsFd) -> io::Result<()> {
    let name = name.as_ref();
    let fd = directory_at(parent, name)?;
    rfs::fchmod(
        &fd,
        Mode::from_raw_mode((rfs::fstat(&fd)?.st_mode & 0o7777) | 0o700),
    )?;
    for child in names(&fd)? {
        let info = rfs::statat(&fd, child.as_os_str(), rfs::AtFlags::SYMLINK_NOFOLLOW)?;
        if rfs::FileType::from_raw_mode(info.st_mode).is_dir() {
            delete_tree(&child, &fd)?;
        } else {
            rfs::unlinkat(&fd, child.as_os_str(), rfs::AtFlags::empty())?;
        }
    }
    drop(fd);
    rfs::unlinkat(parent, name, rfs::AtFlags::REMOVEDIR)?;
    Ok(())
}

pub(super) fn rename_new(
    source: &str,
    parent: &impl AsFd,
    destination: &str,
    destination_parent: &impl AsFd,
) -> io::Result<()> {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        rfs::renameat_with(
            parent,
            source,
            destination_parent,
            destination,
            rfs::RenameFlags::NOREPLACE,
        )?;
        Ok(())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        let _ = (source, parent, destination, destination_parent);
        Err(io::Error::other(
            "atomic no-replace directory rename is unavailable",
        ))
    }
}

pub(super) fn publish(name: &str, parent: &impl AsFd, tree: &Tree) -> io::Result<()> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SERIAL: AtomicU64 = AtomicU64::new(0);
    let temporary = format!(
        ".agent-workbench-copy-{}-{}",
        std::process::id(),
        SERIAL.fetch_add(1, Ordering::Relaxed)
    );
    write_tree(&temporary, parent, tree)?;
    let result = rename_new(&temporary, parent, name, parent);
    match result {
        Ok(()) => {
            rfs::fsync(parent)?;
            Ok(())
        }
        Err(error) => {
            delete_tree(&temporary, parent)?;
            Err(error)
        }
    }
}

#[cfg(test)]
pub(super) fn fixture_digest(names: &[Vec<u8>]) -> io::Result<String> {
    Tree {
        directories: vec![(PathBuf::new(), 0o755)],
        files: names
            .iter()
            .map(|name| TreeFile {
                path: OsString::from_vec(name.clone()).into(),
                mode: 0o751,
                content: b"raw\x00\xff".to_vec(),
            })
            .collect(),
    }
    .digest()
}
