//! D5 foundation only. No public activation entry point consumes this yet.
#![cfg_attr(not(test), allow(dead_code))]
pub(crate) mod apply;
#[cfg(test)]
mod apply_parity_tests;
#[cfg(test)]
mod authority_parity_tests;
#[cfg(test)]
mod capture_parity_tests;
pub(crate) mod journal_wire;
#[cfg(test)]
mod path_parity_tests;
pub(crate) mod path_recovery;
pub(crate) mod recovery;
#[cfg(test)]
mod request_authority_tests;
use crate::{
    bundles,
    shared_document::snapshot::{self, Error, Snapshot},
};
use rustix::{
    fd::AsFd,
    fs::{self as rfs, Mode, OFlags},
};
use std::{
    fs::File,
    io::{self, Read, Write},
    path::Path,
};

/// Preserve the primitive's diagnostic while carrying the exact flock operand
/// to the public lifecycle printer (not an inferred generic EEXIST failure).
#[derive(Debug)]
pub(crate) struct LockContention {
    pub path: std::path::PathBuf,
}
impl std::fmt::Display for LockContention {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        std::io::Error::from_raw_os_error(rustix::io::Errno::EXIST.raw_os_error()).fmt(f)
    }
}
impl std::error::Error for LockContention {}

/// OS entropy, never timestamps, process IDs, or a deterministic fallback.
fn random<const N: usize>() -> io::Result<[u8; N]> {
    #[cfg(test)]
    if let Some(bytes) = authority_parity_tests::entropy() {
        return Ok(bytes);
    }
    let mut bytes = [0; N];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes)
}

/// Exclusive creation leaves failures visible, exactly like the oracle. A failed
/// or partial key is never silently repaired, deleted, or replaced.
pub(crate) fn journal_key(
    file: &Path,
    create: bool,
    mut boundary: impl FnMut(Boundary) -> io::Result<()>,
) -> Result<[u8; 32], Error> {
    let parent = bundles::open_directory(
        file.parent()
            .ok_or_else(|| invalid("state has no parent"))?,
    )?;
    let name = format!(
        ".{}.transaction-key",
        file.file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| invalid("invalid state filename"))?
    );
    if create {
        boundary(Boundary::KeyCreate)?;
        match rfs::openat(
            &parent,
            &name,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
            Mode::from_raw_mode(0o600),
        ) {
            Err(rustix::io::Errno::EXIST) => (),
            Err(error) => return Err(io_error(error)),
            Ok(fd) => {
                let mut handle = File::from(fd);
                boundary(Boundary::KeyWrite)?;
                boundary(Boundary::KeyChmod)?;
                rfs::fchmod(&handle, Mode::from_raw_mode(0o600)).map_err(io_error)?;
                boundary(Boundary::KeyEntropy)?;
                let content = random::<32>()?;
                #[cfg(test)]
                authority_parity_tests::io_fault(&mut handle, "key-partial-write", &content)?;
                handle.write_all(&content)?;
                #[cfg(test)]
                authority_parity_tests::io_fault(&mut handle, "key-flush", &[])?;
                boundary(Boundary::KeySync)?;
                handle.sync_all()?;
                drop(handle);
                #[cfg(test)]
                authority_parity_tests::phase_fault("key-close")?;
                boundary(Boundary::KeyParentSync)?;
                rfs::fsync(&parent).map_err(io_error)?;
            }
        }
    }
    crate::trust::read_key_checked(file)
}

/// Hold the target-wide nonblocking lock across a private operation. Missing
/// read-only roots create nothing; existing roots still get a lock file, as Python.
/// The operation receives only the pinned directory, and successful return is
/// conditional on revalidating that directory's live lexical identity.
pub(crate) fn document_lock(
    base: &Path,
    create: bool,
    name: &str,
    operation: impl FnOnce(Option<&rustix::fd::OwnedFd>) -> Result<(), Error>,
) -> Result<(), Error> {
    use std::os::unix::fs::MetadataExt;
    if name.is_empty() || name.contains('/') || matches!(name, "." | "..") {
        return Err(invalid("invalid document lock name"));
    }
    let mut anchor = base;
    let metadata = loop {
        match std::fs::symlink_metadata(anchor) {
            Ok(info) if info.is_dir() => break info,
            Ok(info) => {
                return Err(Error::Invalid(format!(
                    "destination root {}: {}",
                    if info.file_type().is_symlink() {
                        "may not be a symlink"
                    } else {
                        "must be a directory"
                    },
                    anchor.display()
                )))
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                anchor = anchor
                    .parent()
                    .ok_or_else(|| invalid("no config ancestor"))?;
            }
            Err(error) => return Err(error.into()),
        }
    };
    // Pin even a missing root's ancestor without following intermediate links.
    let mut fd = bundles::open_directory(anchor)?;
    let same = |fd: &rustix::fd::OwnedFd, device: u64, inode: u64| -> Result<bool, Error> {
        let info = rfs::fstat(fd).map_err(io_error)?;
        Ok(info.st_dev as u64 == device && info.st_ino as u64 == inode)
    };
    if !same(&fd, metadata.dev(), metadata.ino())? {
        return Err(invalid("config ancestor changed"));
    }
    if !create && anchor != base {
        return operation(None);
    }
    for component in base
        .strip_prefix(anchor)
        .map_err(|_| invalid("config outside ancestor"))?
        .components()
    {
        let name = component.as_os_str();
        match rfs::mkdirat(&fd, name, Mode::from_raw_mode(0o755)) {
            Ok(()) | Err(rustix::io::Errno::EXIST) => (),
            Err(error) => return Err(io_error(error)),
        }
        fd = rfs::openat(
            &fd,
            name,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
            Mode::empty(),
        )
        .map_err(io_error)?;
    }
    let identity = rfs::fstat(&fd).map_err(io_error)?;
    let verify = || -> Result<(), Error> {
        let live = bundles::open_directory(base).map_err(|_| io_error(rustix::io::Errno::EXIST))?;
        if !same(&live, identity.st_dev as u64, identity.st_ino as u64)? {
            return Err(io_error(rustix::io::Errno::EXIST));
        }
        Ok(())
    };
    let lock = rfs::openat(
        &fd,
        name,
        OFlags::RDWR | OFlags::CREATE | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::from_raw_mode(0o600),
    )
    .map_err(io_error)?;
    if !rfs::FileType::from_raw_mode(rfs::fstat(&lock).map_err(io_error)?.st_mode).is_file() {
        return Err(invalid("document lock must be a regular file"));
    }
    rfs::flock(&lock, rfs::FlockOperation::NonBlockingLockExclusive).map_err(|error| {
        if error == rustix::io::Errno::WOULDBLOCK {
            Error::Io(io::Error::new(
                io::ErrorKind::AlreadyExists,
                LockContention {
                    path: base.join(name),
                },
            ))
        } else {
            io_error(error)
        }
    })?;
    verify()?;
    operation(Some(&fd))?;
    verify()?;
    // The owned lock descriptor remains live through the final verification.
    drop(lock);
    Ok(())
}

/// Sign ordered intent, then replace through an already-authorized pinned config
/// descriptor. This is publication, not schema/path authorization or lifecycle.
pub(crate) fn write_journal(
    mut record: crate::state::wire::Wire,
    state: &Path,
    journal: &Path,
    parent: &impl AsFd,
    mut boundary: impl FnMut(Boundary) -> io::Result<()>,
) -> Result<(), Error> {
    use crate::{shared_document::json, state::wire::Wire};
    use hmac::Mac;
    if !matches!(record, Wire::Object(_)) || record.has("mac") {
        return Err(invalid("journal must be an unsigned object"));
    }
    let key = journal_key(state, true, &mut boundary)?;
    let mut mac = hmac::Hmac::<sha2::Sha256>::new_from_slice(&key)
        .map_err(|error| Error::Invalid(error.to_string()))?;
    mac.update(json::canonical(&record).map_err(Error::Invalid)?.as_bytes());
    let signature = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    record.set("mac", Wire::Scalar(serde_json::Value::String(signature)));
    let content = json::pretty(&record).map_err(Error::Invalid)?;
    let temporary = format!(
        ".agent-workbench-json-{}",
        random::<8>()?
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    let fd = rfs::openat(
        parent,
        &temporary,
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
        Mode::from_raw_mode(0o600),
    )
    .map_err(io_error)?;
    let result: Result<(), Error> = (|| {
        let mut handle = File::from(fd);
        boundary(Boundary::JournalWrite)?;
        #[cfg(test)]
        authority_parity_tests::io_fault(&mut handle, "journal-partial-write", content.as_bytes())?;
        handle.write_all(content.as_bytes())?;
        #[cfg(test)]
        authority_parity_tests::io_fault(&mut handle, "journal-flush", &[])?;
        boundary(Boundary::JournalStageSync)?;
        handle.sync_all()?;
        drop(handle);
        #[cfg(test)]
        authority_parity_tests::phase_fault("journal-close")?;
        boundary(Boundary::JournalRename)?;
        rfs::renameat(
            parent,
            &temporary,
            parent,
            journal
                .file_name()
                .ok_or_else(|| invalid("journal has no name"))?,
        )
        .map_err(io_error)?;
        Ok(())
    })();
    // Mirror the oracle's finally cleanup, including propagation of cleanup errors.
    #[cfg(test)]
    authority_parity_tests::phase_fault("journal-cleanup")?;
    match rfs::unlinkat(parent, &temporary, rfs::AtFlags::empty()) {
        Ok(()) | Err(rustix::io::Errno::NOENT) => (),
        Err(error) => return Err(io_error(error)),
    }
    result?;
    boundary(Boundary::JournalParentSync)?;
    rfs::fsync(parent).map_err(io_error)?;
    Ok(())
}

/// Authenticate first; this grants no authorization to any embedded path.
/// Recovery must separately validate the complete schema, adapter roots, pinned
/// parent identities, quarantine names, and current content before mutation.
pub(crate) fn journal_record(saved: &[u8], file: &Path) -> crate::Result<journal_wire::Wire> {
    use hmac::Mac;
    use journal_wire::Wire;
    let mut data = journal_wire::parse_bytes(saved)?;
    let version = data.get("version").value()?;
    if !(version == serde_json::json!(true) || version.as_f64() == Some(1.0))
        || data.get("stateFile").string().as_deref() != file.to_str()
    {
        return Err(
            "pending recovery belongs to another profile; run status for its owning profile".into(),
        );
    }
    let failure = "unauthenticated recovery journal; journal retained";
    let signature = data.get("mac").string().ok_or(failure)?.to_owned();
    if signature.len() != 64
        || !signature
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(failure.into());
    }
    let Wire::Object(_) = &mut data else {
        return Err(failure.into());
    };
    data.remove("mac");
    let mut decoded = [0u8; 32];
    for (index, byte) in decoded.iter_mut().enumerate() {
        *byte =
            u8::from_str_radix(&signature[index * 2..index * 2 + 2], 16).map_err(|_| failure)?;
    }
    let key = crate::trust::read_key(file).map_err(|_| failure)?;
    let mut mac = hmac::Hmac::<sha2::Sha256>::new_from_slice(&key).map_err(|_| failure)?;
    mac.update(data.canonical()?.as_bytes());
    mac.verify_slice(&decoded).map_err(|_| failure)?;
    Ok(data)
}

/// Exact mutation/persistence seams, also used by the private differential driver.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Boundary {
    KeyCreate,
    KeyChmod,
    KeyEntropy,
    KeyWrite,
    KeySync,
    KeyParentSync,
    JournalWrite,
    JournalStageSync,
    JournalRename,
    JournalParentSync,
    Capture,
    CaptureParentSync,
    CaptureEvidenceSync,
    StageSync,
    Publish,
    PublishParentSync,
    EvidenceRemove,
    QuarantineRemove,
    CleanupParentSync,
}
fn io_error(error: rustix::io::Errno) -> Error {
    Error::Io(io::Error::from(error))
}
fn invalid(message: &str) -> Error {
    Error::Invalid(format!("{message}; journal retained"))
}

/// Capture/validate/publish without replacement, preserving evidence on failure.
/// Caller must hold the document lock and authenticate durable intent first.
/// This function alone neither authorizes a path nor performs transaction recovery.
pub(crate) fn compare_replace_document(
    expected: &Snapshot,
    content: Option<&[u8]>,
    parent: &impl AsFd,
    quarantine: &str,
    mut boundary: impl FnMut(Boundary) -> io::Result<()>,
) -> Result<Snapshot, Error> {
    if quarantine.is_empty() || quarantine.contains('/') || matches!(quarantine, "." | "..") {
        return Err(invalid("invalid document quarantine"));
    }
    if snapshot::snapshot_at(&expected.path, parent, || {})? != *expected {
        return Err(invalid("shared document changed at mutation"));
    }
    let name = expected
        .path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| invalid("document name is not UTF-8"))?;
    rfs::mkdirat(parent, quarantine, Mode::from_raw_mode(0o700)).map_err(io_error)?;
    let child = rfs::openat(
        parent,
        quarantine,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .map_err(io_error)?;
    let result = (|| {
        if expected.content.is_some() {
            boundary(Boundary::Capture)?;
            bundles::rename_new(name, parent, "entry", &child)?;
            boundary(Boundary::CaptureParentSync)?;
            rfs::fsync(parent).map_err(io_error)?;
            boundary(Boundary::CaptureEvidenceSync)?;
            rfs::fsync(&child).map_err(io_error)?;
            let captured = snapshot::snapshot_at(Path::new("entry"), &child, || {})?;
            // A rename may change ctime, but no other revision field.
            let same_revision = match (captured.revision, expected.revision) {
                (Some(a), Some(b)) => (a.0, a.1, a.2, a.3) == (b.0, b.1, b.2, b.3),
                _ => false,
            };
            if captured.content != expected.content || !same_revision {
                bundles::rename_new("entry", &child, name, parent)?;
                rfs::fsync(parent).map_err(io_error)?;
                return Err(invalid("shared document changed during capture"));
            }
        }
        let published = if let Some(content) = content {
            let fd = rfs::openat(
                &child,
                "new",
                OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
                Mode::from_raw_mode(0o600),
            )
            .map_err(io_error)?;
            let mut file = File::from(fd);
            file.write_all(content)?;
            boundary(Boundary::StageSync)?;
            file.sync_all()?;
            boundary(Boundary::Publish)?;
            bundles::rename_new("new", &child, name, parent)?;
            // Read through the still-open published inode, not an unpinned path.
            snapshot::from_metadata(expected.path.clone(), content.to_vec(), &file.metadata()?)
        } else {
            if snapshot::snapshot_at(&expected.path, parent, || {})?
                .content
                .is_some()
            {
                return Err(invalid("shared document changed during removal"));
            }
            Snapshot {
                path: expected.path.clone(),
                content: None,
                revision: None,
            }
        };
        boundary(Boundary::PublishParentSync)?;
        rfs::fsync(parent).map_err(io_error)?;
        boundary(Boundary::EvidenceRemove)?;
        match rfs::unlinkat(&child, "entry", rfs::AtFlags::empty()) {
            Ok(()) | Err(rustix::io::Errno::NOENT) => (),
            Err(error) => return Err(io_error(error)),
        }
        Ok(published)
    })();
    let published = match result {
        Ok(value) => value,
        Err(error) => {
            // This is not transaction rollback: only return our captured inode,
            // without replacement. An occupied destination always wins. All other
            // errors (including return fsync failure) propagate with evidence kept.
            match bundles::rename_new("entry", &child, name, parent) {
                Ok(()) => rfs::fsync(parent).map_err(io_error)?,
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::NotFound | io::ErrorKind::AlreadyExists
                    ) => {}
                Err(e) => return Err(e.into()),
            }
            return Err(error);
        }
    };
    drop(child);
    boundary(Boundary::QuarantineRemove)?;
    rfs::unlinkat(parent, quarantine, rfs::AtFlags::REMOVEDIR).map_err(io_error)?;
    boundary(Boundary::CleanupParentSync)?;
    rfs::fsync(parent).map_err(io_error)?;
    Ok(published)
}
