//! Authenticated rollback under adapter-authorized, identity-pinned roots.
use super::path_recovery::{self as paths, PathRestore, Resource};
use super::{compare_replace_document, document_lock, invalid, io_error, journal_record, Boundary};
use crate::{
    bundles,
    shared_document::{
        self,
        snapshot::{self, Error, Snapshot},
    },
};
use base64::Engine;
use rustix::{
    fd::{AsFd, OwnedFd},
    fs::{self as rfs, Mode, OFlags},
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{self, Read, Write},
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, Error>;

/// Created from the current caller's target and roots, never from journal paths.
pub(crate) struct RecoveryRequest {
    state: PathBuf,
    journal: PathBuf,
    allowed: BTreeSet<PathBuf>,
    parents: BTreeSet<PathBuf>,
    roots: BTreeMap<String, PathBuf>,
    legacy_cursor: bool,
}
impl RecoveryRequest {
    pub(crate) fn new(
        state: PathBuf,
        target: &str,
        roots: BTreeMap<String, PathBuf>,
    ) -> Result<Self> {
        let (hooks_name, mcp_name) = match target {
            "cursor" => ("hooks.json", "mcp.json"),
            "claude" => ("settings.json", ".mcp.json"),
            _ => return Err(invalid("unsupported recovery target")),
        };
        for path in roots.values().chain(std::iter::once(&state)) {
            if !path.is_absolute()
                || path
                    .components()
                    .any(|part| part == std::path::Component::ParentDir)
            {
                return Err(invalid("recovery request paths must be absolute"));
            }
        }
        if roots.keys().any(|key| {
            !matches!(
                key.as_str(),
                "hooks" | "mcp" | "skills" | "agents" | "rules" | "commands"
            )
        }) {
            return Err(invalid("invalid recovery component"));
        }
        let config = roots
            .get("hooks")
            .and_then(|root| root.parent())
            .ok_or_else(|| invalid("missing recovery hooks root"))?;
        let journal = config.join(".agent-workbench-transaction.json");
        let mut allowed = BTreeSet::from([
            config.join(hooks_name),
            config.join(".agent-workbench-owners.json"),
        ]);
        if let Some(root) = roots.get("mcp") {
            allowed.insert(root.join(mcp_name));
        }
        let mut parents: BTreeSet<_> = allowed
            .iter()
            .map(|path| path.parent().unwrap().to_path_buf())
            .collect();
        parents.extend(roots.values().cloned());
        parents.insert(
            state
                .parent()
                .ok_or_else(|| invalid("state has no parent"))?
                .to_path_buf(),
        );
        Ok(Self {
            state,
            journal,
            allowed,
            parents,
            roots,
            legacy_cursor: target == "cursor",
        })
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Journal {
    version: serde_json::Value,
    state_file: String,
    #[serde(skip)]
    parents: Option<Vec<(PathBuf, [u64; 2])>>,
    state_before: Option<String>,
    state_after: Option<String>,
    documents: Vec<DocumentWire>,
    #[serde(skip)]
    paths: Vec<super::journal_wire::Wire>,
}
#[derive(Deserialize)]
struct DocumentWire {
    path: PathBuf,
    before: Option<String>,
    removed: Option<String>,
    after: Option<String>,
    quarantine: String,
}
struct Document {
    path: PathBuf,
    images: [Option<Vec<u8>>; 3],
    quarantine: String,
}
impl Document {
    fn from_wire(item: DocumentWire) -> Result<Self> {
        if !item
            .quarantine
            .strip_prefix(".agent-workbench-document-")
            .is_some_and(|suffix| {
                suffix.len() == 16
                    && suffix
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
        {
            return Err(invalid("invalid journal document quarantine"));
        }
        Ok(Self {
            path: item.path,
            images: [
                unpack(item.before)?,
                unpack(item.removed)?,
                unpack(item.after)?,
            ],
            quarantine: item.quarantine,
        })
    }
    fn names(&self) -> Vec<String> {
        ["-remove", "-activate", "-recover-remove", "-recover-before"]
            .iter()
            .map(|suffix| format!("{}{suffix}", self.quarantine))
            .collect()
    }
}
fn unpack(value: Option<String>) -> Result<Option<Vec<u8>>> {
    value
        .map(|text| {
            let bytes = base64::engine::GeneralPurpose::new(
                &base64::alphabet::STANDARD,
                base64::engine::GeneralPurposeConfig::new().with_decode_allow_trailing_bits(true),
            )
            .decode(text)
            .map_err(|_| invalid("invalid journal base64"))?;
            shared_document::parse_bytes(&bytes).map_err(Error::Invalid)?;
            Ok(bytes)
        })
        .transpose()
}
fn path_error(error: io::Error) -> Error {
    if error.kind() == io::ErrorKind::InvalidData {
        invalid(&error.to_string())
    } else {
        Error::Io(error)
    }
}
struct ManagedPath {
    path: PathBuf,
    resource: Resource,
    quarantine: String,
    before: Option<paths::Snapshot>,
    after: Option<paths::Snapshot>,
}
impl ManagedPath {
    fn name(&self) -> &std::ffi::OsStr {
        self.path.file_name().unwrap()
    }
    fn decode(value: &super::journal_wire::Wire, request: &RecoveryRequest) -> Result<Self> {
        let text = |key| {
            value
                .get(key)
                .string()
                .ok_or_else(|| invalid("invalid journal path field"))
        };
        let component = text("component")?;
        let path = value.get("path").path().map_err(|e| invalid(&e))?;
        let root = request
            .roots
            .get(&component)
            .ok_or_else(|| invalid("unauthorized recovery component"))?;
        if path.parent() != Some(root.as_path()) || path.file_name().is_none() {
            return Err(invalid("journal path is outside adapter-authorized roots"));
        }
        let resource = match text("resource")?.as_str() {
            "directory-copy" => Resource::Tree,
            "directory-symlink" => Resource::Symlink,
            "file-copy" => Resource::File,
            _ => return Err(invalid("invalid journal resource")),
        };
        if component == "mcp" || (component == "hooks" && resource != Resource::Tree) {
            return Err(invalid("journal resource disagrees with component"));
        }
        let quarantine = text("quarantine")?.to_owned();
        if !quarantine
            .strip_prefix(".agent-workbench-quarantine-")
            .is_some_and(|s| {
                s.len() == 16
                    && s.bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
        {
            return Err(invalid("invalid journal quarantine"));
        }
        let decode = |key| {
            if !value.has(key) {
                return Err(invalid("missing path image"));
            }
            let v = value.get(key);
            if v.is_null() {
                return Ok(None);
            }
            let expected = match resource {
                Resource::Tree => "tree",
                Resource::File => "file",
                Resource::Symlink => "link",
            };
            if !v
                .as_object()
                .is_some_and(|o| o.len() == 1 && v.has(expected))
            {
                return Err(invalid("invalid resource evidence"));
            }
            paths::Snapshot::from_lossless_journal(v)
                .map(Some)
                .map_err(path_error)
        };
        Ok(Self {
            path,
            resource,
            quarantine,
            before: decode("before")?,
            after: decode("after")?,
        })
    }
}
struct Parents(BTreeMap<PathBuf, (OwnedFd, [u64; 2])>);
fn identity(fd: &impl AsFd) -> Result<[u64; 2]> {
    let info = rfs::fstat(fd).map_err(io_error)?;
    Ok([info.st_dev as u64, info.st_ino as u64])
}
impl Parents {
    fn open(
        request: &RecoveryRequest,
        evidence: Option<Vec<(PathBuf, [u64; 2])>>,
        required: BTreeSet<PathBuf>,
    ) -> Result<Self> {
        let mut pinned = BTreeMap::new();
        let legacy = evidence.is_none() && request.legacy_cursor;
        if evidence.is_none() && !legacy {
            return Err(invalid("missing authenticated parent identities"));
        }
        for (path, expected) in evidence.unwrap_or_default() {
            if !request
                .parents
                .iter()
                .any(|allowed| allowed.as_os_str() == path.as_os_str())
            {
                return Err(invalid(
                    "journal parent is outside adapter-authorized roots",
                ));
            }
            let fd = bundles::open_directory(&path)?;
            if identity(&fd)? != expected {
                return Err(invalid("recovery parent identity changed"));
            }
            pinned.insert(path, (fd, expected));
        }
        if legacy {
            for path in &required {
                if !request.parents.contains(path) {
                    return Err(invalid(
                        "journal parent is outside adapter-authorized roots",
                    ));
                }
                let fd = bundles::open_directory(path)?;
                let expected = identity(&fd)?;
                pinned.insert(path.clone(), (fd, expected));
            }
        }
        if !required.iter().all(|path| pinned.contains_key(path)) {
            return Err(invalid("missing authenticated parent identities"));
        }
        Ok(Self(pinned))
    }
    fn verify(&self) -> Result<()> {
        for (path, (_, expected)) in &self.0 {
            if identity(&bundles::open_directory(path)?)? != *expected {
                return Err(invalid("recovery parent identity changed"));
            }
        }
        Ok(())
    }
    fn get(&self, path: &Path) -> Result<&OwnedFd> {
        self.verify()?;
        self.0
            .get(path)
            .map(|(fd, _)| fd)
            .ok_or_else(|| invalid("unbound recovery parent"))
    }
}

/// Named private test seams, not CLI switches or success substitutes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RecoveryBoundary {
    LocksAcquired,
    BeforeDocument,
    AfterDeactivate,
    AfterRestore,
    BeforeState,
    BeforeCleanup,
    BeforePath,
    BeforePaths,
    Path(paths::Edge),
    PathEvidence(paths::Edge),
    AfterPath,
    PrepareStageSync,
    PreparePublish,
    Publication(Boundary),
}

pub(crate) fn recover(
    request: &RecoveryRequest,
    mut boundary: impl FnMut(RecoveryBoundary) -> io::Result<()>,
) -> Result<()> {
    let saved = super::journal_wire::snapshot(&request.journal)?;
    let Some(content) = saved.content else {
        return Ok(());
    };
    // No lock, directory or authority creation before authentication.
    journal_record(&content, &request.state).map_err(Error::Invalid)?;
    let state_parent = request.state.parent().unwrap();
    let state_lock = format!(
        ".{}.lock",
        request
            .state
            .file_name()
            .unwrap()
            .to_str()
            .ok_or_else(|| invalid("invalid state filename"))?
    );
    document_lock(state_parent, false, &state_lock, |_| {
        document_lock(
            request.journal.parent().unwrap(),
            false,
            ".agent-workbench.lock",
            |_| {
                boundary(RecoveryBoundary::LocksAcquired)?;
                recover_locked(request, &mut boundary)
            },
        )
    })
}

pub(super) fn recover_locked(
    request: &RecoveryRequest,
    boundary: &mut impl FnMut(RecoveryBoundary) -> io::Result<()>,
) -> Result<()> {
    let config = request.journal.parent().unwrap();
    let journal_parent = bundles::open_directory(config)?;
    let saved = super::journal_wire::snapshot_at(&request.journal, &journal_parent)?;
    let Some(content) = saved.content.as_ref() else {
        return Ok(());
    };
    let data = journal_record(content, &request.state).map_err(Error::Invalid)?;
    // Explicit required-field checking keeps null separate from omitted images.
    for field in [
        "version",
        "stateFile",
        "stateBefore",
        "stateAfter",
        "documents",
        "paths",
    ] {
        if !data.has(field) {
            return Err(invalid("missing journal field"));
        }
    }
    if let super::journal_wire::Wire::Array(items) = data.get("documents") {
        for item in items {
            for field in ["path", "before", "removed", "after", "quarantine"] {
                if !item.has(field) {
                    return Err(invalid("missing journal document field"));
                }
            }
        }
    }
    let mut schema = data
        .project(&["version", "stateFile", "stateBefore", "stateAfter"])
        .map_err(|_| invalid("invalid journal schema"))?;
    let documents = data
        .get("documents")
        .as_array()
        .ok_or_else(|| invalid("invalid journal schema"))?
        .iter()
        .map(|item| item.project(&["path", "before", "removed", "after", "quarantine"]))
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|_| invalid("invalid journal schema"))?;
    schema["documents"] = serde_json::Value::Array(documents);
    let mut record: Journal =
        serde_json::from_value(schema).map_err(|_| invalid("invalid journal schema"))?;
    if data.has("parents") && !data.get("parents").is_null() {
        let fields = data
            .get("parents")
            .as_object()
            .ok_or_else(|| invalid("invalid journal schema"))?;
        record.parents = Some(
            fields
                .iter()
                .map(|(key, value)| {
                    let path = key
                        .path()
                        .map_err(|_| invalid("invalid journal parent path"))?;
                    let identity = serde_json::from_value(
                        value
                            .value()
                            .map_err(|_| invalid("invalid journal schema"))?,
                    )
                    .map_err(|_| invalid("invalid journal schema"))?;
                    Ok((path, identity))
                })
                .collect::<Result<_>>()?,
        );
    }
    record.paths = data
        .get("paths")
        .as_array()
        .ok_or_else(|| invalid("invalid journal schema"))?
        .clone();
    if !(record.version == serde_json::json!(true) || record.version.as_f64() == Some(1.0))
        || record.state_file != request.state.to_string_lossy()
    {
        return Err(invalid("journal profile changed"));
    }

    // The oracle inspects ownership state before authorizing/opening journal
    // parents or decoding document/resource evidence. Preserve first-failure
    // diagnostics when independent defects coexist, without granting the
    // journal any authority over the caller-supplied state path.
    let before = unpack(record.state_before)?;
    let after = unpack(record.state_after)?;
    let state = Document {
        path: request.state.clone(),
        images: [before.clone(), before, after],
        quarantine: format!(
            ".agent-workbench-document-{:x}",
            Sha256::digest(request.state.to_string_lossy().as_bytes())
        )[..42]
            .to_owned(),
    };
    let state_parent = bundles::open_directory(state.path.parent().unwrap())?;
    let state_snapshot = inspect(&state, &state_parent, "ownership state")?;
    drop(state_parent);

    // Parent evidence is checked in authenticated insertion order before
    // document/resource authorization, matching the oracle's first failure.
    // Deriving required parents does not grant authority to those paths.
    let mut required: BTreeSet<_> = record
        .documents
        .iter()
        .map(|item| item.path.parent().unwrap().to_path_buf())
        .collect();
    required.extend([
        config.to_path_buf(),
        request.state.parent().unwrap().to_path_buf(),
    ]);
    for item in &record.paths {
        let path = item.get("path").path().map_err(Error::Invalid)?;
        let parent = path
            .parent()
            .ok_or_else(|| invalid("invalid journal path"))?;
        required.insert(parent.to_path_buf());
    }
    let parents = Parents::open(request, record.parents, required)?;
    if identity(&journal_parent)? != identity(parents.get(config)?)? {
        return Err(invalid("recovery journal parent changed"));
    }
    let mut seen = BTreeSet::new();
    let mut quarantines = BTreeSet::new();
    let documents: Vec<_> = record
        .documents
        .into_iter()
        .map(|item| {
            if !request
                .allowed
                .iter()
                .any(|allowed| allowed.as_os_str() == item.path.as_os_str())
                || !seen.insert(item.path.clone())
                || !quarantines.insert((
                    item.path.parent().map(Path::to_path_buf),
                    item.quarantine.clone(),
                ))
            {
                return Err(invalid(
                    "journal document is outside adapter-authorized paths or duplicated",
                ));
            }
            Document::from_wire(item)
        })
        .collect::<Result<_>>()?;
    let managed: Vec<ManagedPath> = record
        .paths
        .iter()
        .map(|v| ManagedPath::decode(v, request))
        .collect::<Result<_>>()?;
    let mut snapshots: Vec<_> = documents
        .iter()
        .map(|item| inspect(item, parents.get(item.path.parent().unwrap())?, "document"))
        .collect::<Result<_>>()?;
    boundary(RecoveryBoundary::BeforePaths)?;
    let restores = managed
        .iter()
        .map(|item| {
            let root = item.path.parent().unwrap();
            let expected = parents
                .0
                .get(root)
                .ok_or_else(|| invalid("unbound recovery parent"))?
                .1;
            if bundles::open_directory(root)
                .ok()
                .and_then(|fd| identity(&fd).ok())
                != Some(expected)
            {
                return Err(Error::Io(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "destination root changed since planning",
                )));
            }
            let fd = parents.get(item.path.parent().unwrap())?;
            let current = paths::capture_at(fd, item.name(), item.resource).map_err(path_error)?;
            let allowed = vec![item.before.clone(), item.after.clone(), None];
            if !allowed.contains(&current) {
                return Err(invalid("recovery blocked by modified managed path"));
            }
            Ok(PathRestore {
                before: item.before.clone(),
                expected: current,
                allowed,
                revision: paths::revision(fd, item.name()).map_err(path_error)?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    // Intent, live paths/documents/state, and document evidence are validated
    // before mutation. Resource rollback/staging evidence follows the oracle's
    // phase-local validation in restore_path/discard_path_evidence.
    for (item, current) in documents.iter().zip(&mut snapshots) {
        boundary(RecoveryBoundary::BeforeDocument)?;
        let fd = parents.get(item.path.parent().unwrap())?;
        *current = prepare(item, current, fd, &parents, boundary)?;
        if current.content != item.images[1] {
            *current = publish(
                current,
                item.images[1].as_deref(),
                fd,
                &format!("{}-recover-remove", item.quarantine),
                &parents,
                boundary,
            )?;
        } else if snapshot::snapshot_at(&item.path, fd, || {})? != *current {
            return Err(invalid("modified document"));
        }
        boundary(RecoveryBoundary::AfterDeactivate)?;
        parents.verify()?;
    }
    for (item, restore) in managed.iter().zip(&restores).rev() {
        boundary(RecoveryBoundary::BeforePath)?;
        let fd = parents.get(item.path.parent().unwrap())?;
        paths::restore_path(
            fd,
            item.name(),
            item.resource,
            restore,
            &format!("{}-rollback", item.quarantine),
            // Restore and discard operate through the pinned descriptor. Match
            // the oracle's phase boundary: reject a substituted parent after
            // both finish, not in the middle of staged publication/cleanup.
            |edge| boundary(RecoveryBoundary::Path(edge)),
        )
        .map_err(path_error)?;
        paths::discard_path_evidence_observed(
            fd,
            item.resource,
            &item.quarantine,
            item.before.as_ref(),
            &mut |edge| boundary(RecoveryBoundary::PathEvidence(edge)),
        )
        .map_err(path_error)?;
        boundary(RecoveryBoundary::AfterPath)?;
        parents.verify()?;
    }
    for (item, current) in documents.iter().zip(&mut snapshots) {
        let fd = parents.get(item.path.parent().unwrap())?;
        if current.content != item.images[0] {
            *current = publish(
                current,
                item.images[0].as_deref(),
                fd,
                &format!("{}-recover-before", item.quarantine),
                &parents,
                boundary,
            )?;
        }
        boundary(RecoveryBoundary::AfterRestore)?;
        parents.verify()?;
    }
    boundary(RecoveryBoundary::BeforeState)?;
    let fd = parents.get(state.path.parent().unwrap())?;
    if snapshot::snapshot_at(&state.path, fd, || {})? != state_snapshot {
        return Err(invalid("externally modified ownership state"));
    }
    let current = prepare(&state, &state_snapshot, fd, &parents, boundary)?;
    if current.content != state.images[0] {
        publish(
            &current,
            state.images[0].as_deref(),
            fd,
            &format!("{}-recover-before", state.quarantine),
            &parents,
            boundary,
        )?;
    }
    boundary(RecoveryBoundary::BeforeCleanup)?;
    parents.verify()?;
    if super::journal_wire::snapshot_at(&request.journal, &journal_parent)? != saved {
        return Err(invalid("journal changed during recovery"));
    }
    rfs::unlinkat(
        &journal_parent,
        request.journal.file_name().unwrap(),
        rfs::AtFlags::empty(),
    )
    .map_err(io_error)?;
    rfs::fsync(&journal_parent).map_err(io_error)?;
    Ok(())
}
fn publish(
    current: &Snapshot,
    content: Option<&[u8]>,
    fd: &OwnedFd,
    quarantine: &str,
    parents: &Parents,
    boundary: &mut impl FnMut(RecoveryBoundary) -> io::Result<()>,
) -> Result<Snapshot> {
    parents.verify()?;
    compare_replace_document(current, content, fd, quarantine, |point| {
        boundary(RecoveryBoundary::Publication(point))?;
        parents
            .verify()
            .map_err(|_| io::Error::other("recovery parent identity changed"))
    })
}

struct Evidence {
    name: String,
    fd: OwnedFd,
    identity: [u64; 2],
    entries: Vec<(String, Vec<u8>)>,
}
fn raw(name: &str, parent: &impl AsFd) -> Result<Vec<u8>> {
    let fd = rfs::openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    )
    .map_err(io_error)?;
    let mut file = File::from(fd);
    if !file.metadata()?.is_file() {
        return Err(invalid("document evidence is not regular"));
    }
    let mut content = Vec::new();
    file.read_to_end(&mut content)?;
    Ok(content)
}
fn evidence(item: &Document, parent: &OwnedFd) -> Result<Vec<Evidence>> {
    let mut found = Vec::new();
    for name in item.names() {
        let fd = match rfs::openat(
            parent,
            &name,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
            Mode::empty(),
        ) {
            Ok(fd) => fd,
            Err(rustix::io::Errno::NOENT) => continue,
            Err(error) => return Err(io_error(error)),
        };
        let mut entries = Vec::new();
        for entry in rfs::Dir::read_from(&fd).map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let name = entry
                .file_name()
                .to_str()
                .map_err(|_| invalid("foreign document quarantine"))?;
            if matches!(name, "." | "..") {
                continue;
            }
            if !matches!(name, "entry" | "new" | "recovery") {
                return Err(invalid("foreign document quarantine"));
            }
            let bytes = raw(name, &fd)?;
            let accepted = if name == "entry" {
                item.images
                    .iter()
                    .any(|image| image.as_deref() == Some(&bytes))
            } else {
                item.images
                    .iter()
                    .flatten()
                    .any(|image| image.starts_with(&bytes))
            };
            if !accepted {
                return Err(invalid("modified document quarantine"));
            }
            entries.push((name.to_owned(), bytes));
        }
        found.push(Evidence {
            identity: identity(&fd)?,
            name,
            fd,
            entries,
        });
    }
    Ok(found)
}
fn inspect(item: &Document, fd: &OwnedFd, kind: &str) -> Result<Snapshot> {
    let found = evidence(item, fd)?;
    let current = snapshot::snapshot_at(&item.path, fd, || {})?;
    if !item.images.contains(&current.content)
        && !(current.content.is_none()
            && found
                .iter()
                .any(|e| e.entries.iter().any(|(name, _)| name == "entry")))
    {
        return Err(invalid(&format!(
            "recovery blocked by externally modified {kind}"
        )));
    }
    Ok(current)
}
fn verify_evidence(parent: &OwnedFd, item: &Evidence) -> Result<()> {
    let fd = rfs::openat(
        parent,
        &item.name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .map_err(io_error)?;
    if identity(&fd)? != item.identity {
        return Err(invalid("document evidence directory changed"));
    }
    Ok(())
}
fn prepare(
    item: &Document,
    expected: &Snapshot,
    parent: &OwnedFd,
    parents: &Parents,
    boundary: &mut impl FnMut(RecoveryBoundary) -> io::Result<()>,
) -> Result<Snapshot> {
    if snapshot::snapshot_at(&item.path, parent, || {})? != *expected {
        return Err(invalid("modified document"));
    }
    let found = evidence(item, parent)?;
    let mut current = expected.clone();
    if current.content.is_none()
        && found
            .iter()
            .any(|e| e.entries.iter().any(|(name, _)| name == "entry"))
    {
        if let Some(content) = item.images[1].as_ref().or(item.images[0].as_ref()) {
            let saved = found.first().ok_or_else(|| invalid("missing evidence"))?;
            parents.verify()?;
            verify_evidence(parent, saved)?;
            match rfs::unlinkat(&saved.fd, "recovery", rfs::AtFlags::empty()) {
                Ok(()) | Err(rustix::io::Errno::NOENT) => (),
                Err(error) => return Err(io_error(error)),
            }
            let fd = rfs::openat(
                &saved.fd,
                "recovery",
                OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
                Mode::from_raw_mode(0o600),
            )
            .map_err(io_error)?;
            let mut file = File::from(fd);
            file.write_all(content)?;
            boundary(RecoveryBoundary::PrepareStageSync)?;
            parents.verify()?;
            file.sync_all()?;
            boundary(RecoveryBoundary::PreparePublish)?;
            parents.verify()?;
            verify_evidence(parent, saved)?;
            bundles::rename_new(
                "recovery",
                &saved.fd,
                item.path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .ok_or_else(|| invalid("invalid document filename"))?,
                parent,
            )?;
            rfs::fsync(parent).map_err(io_error)?;
            current = snapshot::snapshot_at(&item.path, parent, || {})?;
            if current.content.as_ref() != Some(content) {
                return Err(invalid("modified document publication"));
            }
        }
    }
    rfs::fsync(parent).map_err(io_error)?;
    // Revalidate after durable publication; remove only known authenticated files
    // through their pinned nofollow directories, never a recursive foreign tree.
    for saved in evidence(item, parent)? {
        parents.verify()?;
        verify_evidence(parent, &saved)?;
        for (name, bytes) in &saved.entries {
            if raw(name, &saved.fd)? != *bytes {
                return Err(invalid("document evidence changed before cleanup"));
            }
            rfs::unlinkat(&saved.fd, name, rfs::AtFlags::empty()).map_err(io_error)?;
        }
        verify_evidence(parent, &saved)?;
        rfs::unlinkat(parent, &saved.name, rfs::AtFlags::REMOVEDIR).map_err(io_error)?;
    }
    rfs::fsync(parent).map_err(io_error)?;
    Ok(current)
}
