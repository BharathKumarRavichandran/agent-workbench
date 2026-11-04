//! Private D5 transaction gate. No CLI or MCP activation entry point.
use super::{compare_replace_document, document_lock, io_error, random, write_journal, Boundary};
use crate::{
    bundles,
    shared_document::{
        self,
        snapshot::{self, Error, Snapshot},
    },
    state::wire::Wire,
};
use base64::Engine;
use hmac::Mac;
use rustix::{fd::OwnedFd, fs as rfs};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{self, Write},
    path::{Path, PathBuf},
};
type Result<T> = std::result::Result<T, Error>;
// Apply validation is not recovery: no journal may exist yet. Preserve the
// oracle's diagnostic rather than appending a claim about retained evidence.
fn invalid(message: &str) -> Error {
    Error::Invalid(message.to_owned())
}

/// E2 is not an ordinary transaction failure: leave authenticated intent for
/// explicit recovery rather than silently rolling back and erasing the evidence.
#[derive(Debug)]
struct ForeignStageReplacement(PathBuf);
impl std::fmt::Display for ForeignStageReplacement {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "foreign bundle staging replacement: {}; journal retained",
            self.0.display()
        )
    }
}
impl std::error::Error for ForeignStageReplacement {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PlannedDocument {
    pub before: DocumentSnapshot,
    pub removed: Option<Vec<u8>>,
    pub after: Option<Vec<u8>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DocumentSnapshot {
    pub path: PathBuf,
    pub content: Option<Vec<u8>>,
    pub revision: Option<(u64, u64, u64, i128, i128)>,
}
impl DocumentSnapshot {
    fn snapshot(&self) -> Snapshot {
        Snapshot {
            path: self.path.clone(),
            content: self.content.clone(),
            revision: self.revision,
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RootSnapshot {
    pub path: PathBuf,
    pub identity: Option<[u64; 2]>,
    pub anchor: PathBuf,
    pub anchor_identity: [u64; 2],
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Operation {
    pub kind: String,
    pub path: PathBuf,
    pub component: String,
    pub resource: super::path_recovery::Resource,
    pub source: Option<PathBuf>,
    pub digest: Option<String>,
    pub expected_digest: Option<String>,
    pub expected_link: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Source {
    pub path: PathBuf,
    pub digest: String,
    pub tree: bool,
}
/// Entire request originates in the current caller's validated plan, NOT a journal.
/// Complete snapshots keep byte equality separate from inode/timestamp freshness.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ApplyRequest {
    pub target: String,
    pub repository: PathBuf,
    pub roots: BTreeMap<String, PathBuf>,
    pub state_before: DocumentSnapshot,
    pub state_after: Vec<u8>,
    pub conflicts: bool,
    pub uninstall: bool,
    pub root_snapshots: Vec<RootSnapshot>,
    pub documents: Vec<PlannedDocument>,
    pub sources: Vec<Source>,
    pub operations: Vec<Operation>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ApplyBoundary {
    LocksAcquired,
    BeforeJournal,
    JournalDurable,
    BeforeDeactivate,
    BeforeActivate,
    BeforeResource,
    ResourceCaptured,
    ResourceEvidenceDelete,
    TreeEvidenceDelete,
    TreeEvidenceDeletedFile,
    TreeStage(bundles::TreeWriteEdge),
    ResourceStage,
    ResourcePublish,
    ResourcePublished,
    ResourceParentSync,
    ResourceCleanup,
    BeforeState,
    FinalRootSync,
    PostStateConfigSync,
    StateUnlink,
    StateRemovedSync,
    FileSync,
    FileLink,
    FileUnlink,
    FileUnlinked,
    StateWrite,
    StateSync,
    StateReplace,
    StateParentSync,
    BeforeCleanup,
    JournalRemovedSync,
    BeforeRecovery,
    Publication(Boundary),
}
fn identity(fd: &OwnedFd) -> Result<[u64; 2]> {
    let s = rfs::fstat(fd).map_err(io_error)?;
    Ok([s.st_dev as u64, s.st_ino as u64])
}
struct Parents(BTreeMap<PathBuf, (OwnedFd, [u64; 2])>);
impl Parents {
    fn verify_root(&self, path: &Path) -> Result<()> {
        let expected = self
            .0
            .get(path)
            .ok_or_else(|| invalid("unbound apply parent"))?
            .1;
        let actual = bundles::open_directory(path).and_then(|fd| {
            let s = rfs::fstat(&fd).map_err(io::Error::from)?;
            Ok([s.st_dev as u64, s.st_ino as u64])
        });
        if !matches!(actual, Ok(id) if id == expected) {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                format!(
                    "destination root changed since planning: {}",
                    path.display()
                ),
            )
            .into());
        }
        Ok(())
    }
    fn verify(&self) -> Result<()> {
        for (p, (_, id)) in &self.0 {
            if identity(&bundles::open_directory(p)?)? != *id {
                return Err(invalid(
                    "recovery parent identity changed; journal retained",
                ));
            }
        }
        Ok(())
    }
    fn get(&self, p: &Path) -> Result<&OwnedFd> {
        self.verify()?;
        self.0
            .get(p)
            .map(|v| &v.0)
            .ok_or_else(|| invalid("unbound apply parent"))
    }
}
fn pack(v: Option<&[u8]>) -> serde_json::Value {
    v.map(|v| json!(base64::engine::general_purpose::STANDARD.encode(v)))
        .unwrap_or(serde_json::Value::Null)
}
fn nonce(prefix: &str) -> Result<String> {
    // Test-only entropy control preserves exact failed-cleanup residue names;
    // no normalizer is allowed to erase unauthenticated staging files.
    #[cfg(test)]
    if prefix == ".agent-workbench-copy-"
        && std::env::var("AW_APPLY_SCENARIO").is_ok_and(|s| {
            s == "file-temp-unlink"
                || s.starts_with("foreign-stage-cleanup")
                || s.starts_with("tree-stage-")
        })
    {
        return Ok(format!("{prefix}0123456789abcdef"));
    }
    Ok(format!(
        "{prefix}{}",
        random::<8>()?
            .iter()
            .map(|v| format!("{v:02x}"))
            .collect::<String>()
    ))
}
fn ordered<const N: usize>(fields: [(&str, Wire); N]) -> Wire {
    Wire::Object(fields.into_iter().map(|(k, v)| (k.to_owned(), v)).collect())
}
fn wire(v: serde_json::Value) -> Result<Wire> {
    shared_document::parse_bytes(
        &serde_json::to_vec(&json!({"value": v})).map_err(|e| Error::Invalid(e.to_string()))?,
    )
    .map(|wrapped| wrapped.get("value").clone())
    .map_err(Error::Invalid)
}
fn signed_state(
    request: &ApplyRequest,
    boundary: &mut impl FnMut(ApplyBoundary) -> io::Result<()>,
) -> Result<Vec<u8>> {
    let mut state = shared_document::parse_bytes(&request.state_after).map_err(Error::Invalid)?;
    for group in ["entries", "slots"] {
        let keys = state
            .get(group)
            .object(group)
            .map_err(Error::Invalid)?
            .iter()
            .map(|(k, _)| k.clone())
            .collect::<Vec<_>>();
        for key in keys {
            let item = state
                .get_mut(group)
                .and_then(|g| g.get_mut(&key))
                .ok_or_else(|| invalid("invalid state record"))?;
            if group == "entries" && item.get("resourceKind").string() != Some("directory-copy") {
                continue;
            }
            let secret = super::journal_key(&request.state_before.path, true, |b| {
                boundary(ApplyBoundary::Publication(b))
            })?;
            item.remove("trustProof");
            let value = json!([
                "aw-persisted-trust-v1",
                request.state_before.path,
                item.value()
            ]);
            let bytes = crate::trust::ascii_json(&value).map_err(Error::Invalid)?;
            let mut mac = hmac::Hmac::<Sha256>::new_from_slice(&secret)
                .map_err(|e| Error::Invalid(e.to_string()))?;
            mac.update(&bytes);
            let proof = format!(
                "aw-proof-v1:{}",
                mac.finalize()
                    .into_bytes()
                    .iter()
                    .map(|v| format!("{v:02x}"))
                    .collect::<String>()
            );
            // Persisted state puts proof before the original ordered fields.
            let mut fields = vec![("trustProof".to_owned(), Wire::Scalar(json!(proof)))];
            fields.extend(item.object("record").map_err(Error::Invalid)?.to_vec());
            *item = Wire::Object(fields);
        }
    }
    shared_document::json::pretty(&state)
        .map(|v| v.into_bytes())
        .map_err(Error::Invalid)
}
impl ApplyRequest {
    fn authority(&self) -> Result<(PathBuf, BTreeSet<PathBuf>)> {
        if self.conflicts {
            return Err(invalid("installation plan has conflicts"));
        }
        let _ = super::recovery::RecoveryRequest::new(
            self.state_before.path.clone(),
            &self.target,
            self.roots.clone(),
        )?;
        let mut destinations = BTreeSet::new();
        for op in &self.operations {
            if !matches!(
                op.kind.as_str(),
                "create" | "replace-owned" | "remove-owned" | "keep" | "adopt"
            ) || !matches!(
                (op.component.as_str(), op.resource),
                ("hooks", super::path_recovery::Resource::Tree)
                    | ("skills", super::path_recovery::Resource::Symlink)
                    | (
                        "agents" | "rules" | "commands",
                        super::path_recovery::Resource::File
                    )
            ) {
                return Err(invalid("unsupported apply resource operation"));
            }
            if self.roots.get(&op.component).map(PathBuf::as_path) != op.path.parent()
                || !destinations.insert(op.path.clone())
                || op
                    .path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .is_none_or(|n| n.starts_with('.'))
            {
                return Err(invalid("unauthorized apply destination"));
            }
        }
        let config = self
            .roots
            .get("hooks")
            .and_then(|p| p.parent())
            .ok_or_else(|| invalid("missing hooks root"))?
            .to_path_buf();
        let hooks = if self.target == "claude" {
            "settings.json"
        } else {
            "hooks.json"
        };
        let mcp = if self.target == "claude" {
            ".mcp.json"
        } else {
            "mcp.json"
        };
        let mut allowed = BTreeSet::from([
            config.join(hooks),
            config.join(".agent-workbench-owners.json"),
        ]);
        if let Some(p) = self.roots.get("mcp") {
            allowed.insert(p.join(mcp));
        }
        let mut seen = BTreeSet::new();
        for d in &self.documents {
            if !allowed.contains(&d.before.path) || !seen.insert(d.before.path.clone()) {
                return Err(invalid("unauthorized or duplicated apply document"));
            }
        }
        Ok((config, allowed))
    }
}
/// Both initial and exception recovery run under the same state/document locks
/// as mutation; an Interrupted test seam models Python BaseException bypass.
pub(crate) fn apply(
    request: &ApplyRequest,
    mut boundary: impl FnMut(ApplyBoundary) -> io::Result<()>,
) -> Result<()> {
    if request.conflicts {
        let state = &request.state_before.path;
        let lock = format!(
            ".{}.lock",
            state
                .file_name()
                .and_then(|v| v.to_str())
                .ok_or_else(|| invalid("invalid state name"))?
        );
        return document_lock(
            state
                .parent()
                .ok_or_else(|| invalid("state has no parent"))?,
            true,
            &lock,
            |_| Err(invalid("installation plan has conflicts")),
        );
    }
    let (config, _) = request.authority()?;
    let recovery = super::recovery::RecoveryRequest::new(
        request.state_before.path.clone(),
        &request.target,
        request.roots.clone(),
    )?;
    let state = &request.state_before.path;
    let lock = format!(
        ".{}.lock",
        state
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or_else(|| invalid("invalid state name"))?
    );
    let mut durable = false;
    document_lock(state.parent().unwrap(), true, &lock, |_| {
        document_lock(&config, true, ".agent-workbench.lock", |_| {
            boundary(ApplyBoundary::LocksAcquired)?;
            super::recovery::recover_locked(&recovery, &mut |_| Ok(()))?;
            let result = apply_locked(request, &config, &mut durable, &mut boundary);
            if result.is_err()
                && durable
                && !matches!(&result, Err(Error::Io(e)) if e.kind() == io::ErrorKind::Interrupted
                    || e.get_ref().is_some_and(|cause| cause.is::<ForeignStageReplacement>()))
            {
                boundary(ApplyBoundary::BeforeRecovery)?;
                super::recovery::recover_locked(&recovery, &mut |_| Ok(()))?;
            }
            result
        })
    })
}
fn apply_locked(
    request: &ApplyRequest,
    config: &Path,
    durable: &mut bool,
    boundary: &mut impl FnMut(ApplyBoundary) -> io::Result<()>,
) -> Result<()> {
    let journal = config.join(".agent-workbench-transaction.json");
    if snapshot::snapshot(&journal)?.content.is_some() {
        return Err(invalid("pending recovery"));
    }
    let current_state = crate::state::load(&request.state_before.path).map_err(Error::Invalid)?;
    let planned_state = request
        .state_before
        .content
        .as_deref()
        .map(|bytes| crate::state::decode_snapshot(bytes, &request.state_before.path))
        .transpose()
        .map_err(Error::Invalid)?;
    if current_state != planned_state {
        return Err(invalid("installation state changed since planning"));
    }
    let needed = request
        .documents
        .iter()
        .map(|d| d.before.path.parent().unwrap().to_path_buf())
        .chain(
            request
                .operations
                .iter()
                .map(|o| o.path.parent().unwrap().to_path_buf()),
        )
        .chain([
            config.to_path_buf(),
            request.state_before.path.parent().unwrap().to_path_buf(),
        ])
        .collect::<BTreeSet<_>>();
    let mut pinned = BTreeMap::new();
    for p in needed {
        if let Some(s) = request.root_snapshots.iter().find(|s| s.path == p) {
            let ancestor_changed = || {
                Error::Io(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!(
                        "destination ancestor changed since planning: {}",
                        s.anchor.display()
                    ),
                ))
            };
            let root_changed = || {
                Error::Io(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!("destination root changed since planning: {}", p.display()),
                ))
            };
            let mut fd = bundles::open_directory(&s.anchor).map_err(|_| ancestor_changed())?;
            if identity(&fd)? != s.anchor_identity {
                return Err(ancestor_changed());
            }
            {
                for part in p
                    .strip_prefix(&s.anchor)
                    .map_err(|_| invalid("root outside anchor"))?
                    .components()
                {
                    let std::path::Component::Normal(name) = part else {
                        return Err(invalid("invalid root component"));
                    };
                    match rfs::mkdirat(&fd, name, rfs::Mode::from_raw_mode(0o755)) {
                        Ok(()) | Err(rustix::io::Errno::EXIST) => (),
                        Err(e) => return Err(io_error(e)),
                    }
                    let info =
                        rfs::statat(&fd, name, rfs::AtFlags::SYMLINK_NOFOLLOW).map_err(io_error)?;
                    if rfs::FileType::from_raw_mode(info.st_mode) != rfs::FileType::Directory {
                        return Err(root_changed());
                    }
                    fd = rfs::openat(
                        &fd,
                        name,
                        rfs::OFlags::RDONLY | rfs::OFlags::DIRECTORY | rfs::OFlags::NOFOLLOW,
                        rfs::Mode::empty(),
                    )
                    .map_err(io_error)?;
                }
                if let Some(id) = s.identity {
                    if identity(&fd)? != id {
                        return Err(root_changed());
                    }
                }
            }
        } else if p != request.state_before.path.parent().unwrap() {
            return Err(invalid("missing planned root"));
        }
        let fd = bundles::open_directory(&p)?;
        let id = identity(&fd)?;
        pinned.insert(p, (fd, id));
    }
    let parents = Parents(pinned);
    let verify_docs = || -> Result<()> {
        for d in &request.documents {
            if snapshot::snapshot_at(
                &d.before.path,
                parents.get(d.before.path.parent().unwrap())?,
                || {},
            )? != d.before.snapshot()
            {
                return Err(invalid("shared document changed since planning"));
            }
        }
        Ok(())
    };
    verify_docs()?;
    for source in &request.sources {
        if !source.path.starts_with(&request.repository) {
            return Err(invalid("source outside repository"));
        }
        let actual = if source.tree {
            bundles::source_tree(&source.path)?.digest()?
        } else {
            let fd = bundles::open_directory(
                source
                    .path
                    .parent()
                    .ok_or_else(|| invalid("source has no parent"))?,
            )?;
            let captured = super::path_recovery::capture_at(
                &fd,
                source
                    .path
                    .file_name()
                    .and_then(|v| v.to_str())
                    .ok_or_else(|| invalid("invalid source name"))?,
                super::path_recovery::Resource::File,
            )?;
            let Some(super::path_recovery::Snapshot::File(bytes)) = captured else {
                return Err(invalid("source missing"));
            };
            format!("{:x}", Sha256::digest(bytes))
        };
        if actual != source.digest {
            return Err(invalid("executable source changed since planning"));
        }
    }
    let paths = prepare_paths(request, &parents)?;
    parents.verify()?;
    verify_docs()?;
    let after = if request.uninstall {
        None
    } else {
        Some(signed_state(request, boundary)?)
    };
    let quarantines = request
        .documents
        .iter()
        .map(|_| nonce(".agent-workbench-document-"))
        .collect::<Result<Vec<_>>>()?;
    let documents = request
        .documents
        .iter()
        .zip(&quarantines)
        .map(|(d, q)| {
            Ok(ordered([
                ("path", wire(json!(d.before.path))?),
                ("before", wire(pack(d.before.content.as_deref()))?),
                ("removed", wire(pack(d.removed.as_deref()))?),
                ("after", wire(pack(d.after.as_deref()))?),
                ("quarantine", wire(json!(q))?),
            ]))
        })
        .collect::<Result<Vec<_>>>()?;
    // Python inserts opened roots in plan order, then the state parent.
    let mut evidence = Vec::new();
    for p in request
        .root_snapshots
        .iter()
        .map(|s| &s.path)
        .chain(std::iter::once(
            &request.state_before.path.parent().unwrap().to_path_buf(),
        ))
    {
        if let Some((_, id)) = parents.0.get(p) {
            let key = p.to_string_lossy().into_owned();
            if !evidence.iter().any(|(k, _)| k == &key) {
                evidence.push((key, wire(json!(id))?));
            }
        }
    }
    let record = ordered([
        ("version", wire(json!(1))?),
        ("stateFile", wire(json!(request.state_before.path))?),
        ("parents", Wire::Object(evidence)),
        (
            "stateBefore",
            wire(pack(
                // Ownership state follows links when read; only shared documents
                // use nofollow snapshots. Publication still replaces the link.
                if request.state_before.path.exists() {
                    Some(std::fs::read(&request.state_before.path)?)
                } else {
                    None
                }
                .as_deref(),
            ))?,
        ),
        ("stateAfter", wire(pack(after.as_deref()))?),
        ("documents", Wire::Array(documents)),
        (
            "paths",
            Wire::Array(paths.iter().map(|p| p.record.clone()).collect()),
        ),
    ]);
    boundary(ApplyBoundary::BeforeJournal)?;
    parents.verify()?;
    write_journal(
        record,
        &request.state_before.path,
        &journal,
        parents.get(config)?,
        |b| boundary(ApplyBoundary::Publication(b)),
    )?;
    *durable = true;
    boundary(ApplyBoundary::JournalDurable)?;
    let mut current = request
        .documents
        .iter()
        .map(|d| d.before.snapshot())
        .collect::<Vec<_>>();
    for ((d, q), s) in request.documents.iter().zip(&quarantines).zip(&mut current) {
        boundary(ApplyBoundary::BeforeDeactivate)?;
        parents.verify()?;
        if d.removed != s.content {
            *s = compare_replace_document(
                s,
                d.removed.as_deref(),
                parents.get(s.path.parent().unwrap())?,
                &format!("{q}-remove"),
                |b| boundary(ApplyBoundary::Publication(b)),
            )?;
        }
    }
    for (op, path) in request.operations.iter().zip(&paths) {
        parents.verify()?;
        for s in &current {
            if snapshot::snapshot_at(&s.path, parents.get(s.path.parent().unwrap())?, || {})? != *s
            {
                return Err(invalid("shared document changed during transaction"));
            }
        }
        mutate_path(op, path, &parents, boundary)?;
    }
    for (op, path) in request.operations.iter().zip(&paths) {
        let fd = parents.get(op.path.parent().unwrap())?;
        let actual = super::path_recovery::capture_at(
            fd,
            op.path.file_name().unwrap().to_str().unwrap(),
            op.resource,
        )?;
        if !same_resource_image(op, &actual, &path.after) {
            return Err(invalid("managed path changed before activation"));
        }
    }
    for ((d, q), s) in request.documents.iter().zip(&quarantines).zip(&current) {
        boundary(ApplyBoundary::BeforeActivate)?;
        let fd = parents.get(s.path.parent().unwrap())?;
        if snapshot::snapshot_at(&s.path, fd, || {})? != *s {
            return Err(invalid("shared document changed during transaction"));
        }
        if d.after != s.content {
            compare_replace_document(s, d.after.as_deref(), fd, &format!("{q}-activate"), |b| {
                boundary(ApplyBoundary::Publication(b))
            })?;
        }
    }
    // The oracle syncs opened destination roots in plan order, not its
    // separately pinned ownership-state parent.
    for root in &request.root_snapshots {
        if let Some((fd, _)) = parents.0.get(&root.path) {
            boundary(ApplyBoundary::FinalRootSync)?;
            rfs::fsync(fd).map_err(io_error)?;
        }
    }
    boundary(ApplyBoundary::BeforeState)?;
    parents.verify()?;
    persist_state(
        &request.state_before.path,
        after.as_deref(),
        &parents,
        boundary,
    )?;
    boundary(ApplyBoundary::PostStateConfigSync)?;
    rfs::fsync(parents.get(config)?).map_err(io_error)?;
    boundary(ApplyBoundary::BeforeCleanup)?;
    parents.verify()?;
    rfs::unlinkat(
        parents.get(config)?,
        journal.file_name().unwrap(),
        rfs::AtFlags::empty(),
    )
    .map_err(io_error)?;
    boundary(ApplyBoundary::JournalRemovedSync)?;
    rfs::fsync(parents.get(config)?).map_err(io_error)?;
    Ok(())
}
/// Ownership state uses atomic replacement, not the document quarantine protocol.
/// Keep the temporary cleanup on every error (including simulated interruption).
fn persist_state(
    path: &Path,
    content: Option<&[u8]>,
    parents: &Parents,
    boundary: &mut impl FnMut(ApplyBoundary) -> io::Result<()>,
) -> Result<()> {
    let parent = parents.get(
        path.parent()
            .ok_or_else(|| invalid("state has no parent"))?,
    )?;
    let name = path
        .file_name()
        .ok_or_else(|| invalid("state has no name"))?;
    let Some(content) = content else {
        boundary(ApplyBoundary::StateUnlink)?;
        parents.verify()?;
        match rfs::unlinkat(parent, name, rfs::AtFlags::empty()) {
            Ok(()) => {
                parents.verify()?;
                boundary(ApplyBoundary::StateRemovedSync)?;
                parents.verify()?;
                rfs::fsync(parent).map_err(io_error)?;
            }
            Err(rustix::io::Errno::NOENT) => (),
            Err(e) => return Err(io_error(e)),
        }
        return Ok(());
    };
    let temporary = nonce(&format!(".{}.", name.to_string_lossy()))?;
    let fd = rfs::openat(
        parent,
        &temporary,
        rfs::OFlags::WRONLY | rfs::OFlags::CREATE | rfs::OFlags::EXCL | rfs::OFlags::NOFOLLOW,
        rfs::Mode::from_raw_mode(0o600),
    )
    .map_err(io_error)?;
    let result = (|| -> Result<()> {
        let mut file = std::fs::File::from(fd);
        boundary(ApplyBoundary::StateWrite)?;
        file.write_all(content)?;
        boundary(ApplyBoundary::StateSync)?;
        file.sync_all()?;
        drop(file);
        boundary(ApplyBoundary::StateReplace)?;
        // E1: reject a late lexical-parent replacement, never publish into
        // the detached directory or follow an attacker's replacement parent.
        parents.verify()?;
        rfs::renameat(parent, &temporary, parent, name).map_err(io_error)?;
        parents.verify()?;
        boundary(ApplyBoundary::StateParentSync)?;
        parents.verify()?;
        rfs::fsync(parent).map_err(io_error)?;
        Ok(())
    })();
    match rfs::unlinkat(parent, &temporary, rfs::AtFlags::empty()) {
        Ok(()) | Err(rustix::io::Errno::NOENT) => (),
        Err(e) => return Err(io_error(e)),
    }
    result
}
struct PreparedPath {
    before: Option<super::path_recovery::Snapshot>,
    after: Option<super::path_recovery::Snapshot>,
    revision: Option<super::path_recovery::Revision>,
    quarantine: String,
    record: Wire,
}
fn same_resource_image(
    op: &Operation,
    actual: &Option<super::path_recovery::Snapshot>,
    expected: &Option<super::path_recovery::Snapshot>,
) -> bool {
    use super::path_recovery::Snapshot;
    match (actual, expected) {
        (Some(Snapshot::Symlink(actual)), Some(Snapshot::Symlink(expected))) => {
            let parent = op.path.parent().unwrap();
            match (
                parent.join(actual).canonicalize(),
                parent.join(expected).canonicalize(),
            ) {
                (Ok(actual), Ok(expected)) => actual == expected,
                _ => false,
            }
        }
        _ => actual == expected,
    }
}
fn prepare_paths(request: &ApplyRequest, parents: &Parents) -> Result<Vec<PreparedPath>> {
    use super::path_recovery::{self as paths, Resource, Snapshot as Image};
    request
        .operations
        .iter()
        .map(|op| {
            let fd = parents.get(op.path.parent().unwrap())?;
            let name = op.path.file_name().unwrap().to_str().unwrap();
            // Validate the source before reading destination evidence. Python
            // reports stale sources first even when the destination changed kind.
            let after = if op.kind == "remove-owned" {
                None
            } else {
                let source = op
                    .source
                    .as_ref()
                    .filter(|p| p.starts_with(&request.repository))
                    .ok_or_else(|| invalid("source outside repository"))?;
                Some(match op.resource {
                    Resource::Tree => Image::Tree(bundles::Tree::validate_source(
                        source,
                        op.digest
                            .as_deref()
                            .ok_or_else(|| invalid("missing source digest"))?,
                    )?),
                    Resource::Symlink => {
                        crate::lifecycle::validate_source(&request.repository, source)
                            .map_err(Error::Invalid)?;
                        Image::Symlink(
                            crate::lifecycle::relative_link(source, op.path.parent().unwrap())
                                .map_err(Error::Invalid)?,
                        )
                    }
                    Resource::File => {
                        let fd = bundles::open_directory(source.parent().unwrap())?;
                        let Some(Image::File(bytes)) = paths::capture_at(
                            &fd,
                            source.file_name().unwrap().to_str().unwrap(),
                            Resource::File,
                        )?
                        else {
                            return Err(invalid("source missing"));
                        };
                        if op.digest.as_deref()
                            != Some(format!("{:x}", Sha256::digest(&bytes)).as_str())
                        {
                            return Err(invalid(&format!(
                                "copy source changed since planning: {}",
                                source.display()
                            )));
                        }
                        Image::File(bytes)
                    }
                })
            };
            let before = paths::capture_at(fd, name, op.resource)?;
            let revision = paths::revision(fd, name)?;
            let valid = match op.kind.as_str() {
                "create" => before.is_none(),
                "keep" | "adopt" => same_resource_image(op, &before, &after),
                "remove-owned" | "replace-owned" => match &before {
                    Some(Image::Tree(t)) => {
                        op.expected_digest.as_deref() == Some(t.digest()?.as_str())
                    }
                    Some(Image::File(bytes)) => {
                        op.expected_digest.as_deref()
                            == Some(format!("{:x}", Sha256::digest(bytes)).as_str())
                    }
                    Some(Image::Symlink(link)) => {
                        op.expected_link
                            .as_deref()
                            .map(std::path::Path::new)
                            .map(std::path::Path::as_os_str)
                            == Some(link.as_os_str())
                    }
                    _ => false,
                },
                _ => false,
            };
            if !valid {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!("destination changed since planning: {}", op.path.display()),
                )
                .into());
            }
            let quarantine = nonce(".agent-workbench-quarantine-")?;
            let record = ordered([
                ("path", wire(json!(op.path))?),
                ("component", wire(json!(op.component))?),
                ("resource", wire(json!(op.resource))?),
                (
                    "before",
                    wire(json!(before.as_ref().map(Image::to_journal).transpose()?))?,
                ),
                (
                    "after",
                    wire(json!(after.as_ref().map(Image::to_journal).transpose()?))?,
                ),
                ("quarantine", wire(json!(quarantine))?),
            ]);
            Ok(PreparedPath {
                before,
                after,
                revision,
                quarantine,
                record,
            })
        })
        .collect()
}
fn mutate_path(
    op: &Operation,
    path: &PreparedPath,
    parents: &Parents,
    boundary: &mut impl FnMut(ApplyBoundary) -> io::Result<()>,
) -> Result<()> {
    use super::path_recovery::{self as paths, Snapshot as Image};
    boundary(ApplyBoundary::BeforeResource)?;
    let parent = parents.get(op.path.parent().unwrap())?;
    let name = op.path.file_name().unwrap().to_str().unwrap();
    // Python verifies keep/adopt at preparation and again before activation,
    // but performs no capture or revision check at the mutation phase. A
    // semantically identical link may have been re-created in the meantime.
    if matches!(op.kind.as_str(), "keep" | "adopt") {
        return Ok(());
    }
    if paths::capture_at(parent, name, op.resource)? != path.before
        || paths::revision(parent, name)? != path.revision
    {
        return Err(invalid("managed path changed at mutation"));
    }
    if path.before.is_some() {
        rfs::mkdirat(parent, &path.quarantine, rfs::Mode::from_raw_mode(0o700))
            .map_err(io_error)?;
        let saved = rfs::openat(
            parent,
            &path.quarantine,
            rfs::OFlags::RDONLY | rfs::OFlags::DIRECTORY | rfs::OFlags::NOFOLLOW,
            rfs::Mode::empty(),
        )
        .map_err(io_error)?;
        bundles::rename_new(name, parent, "entry", &saved)?;
        // _DestinationRoot.quarantine does not sync either directory. The
        // authenticated intent is already durable; apply syncs resource roots
        // after activation. Extra syncs here add observable failure windows.
        boundary(ApplyBoundary::ResourceCaptured)?;
        if paths::capture_at(&saved, "entry", op.resource)? != path.before {
            return Err(invalid("managed path changed during capture"));
        }
        boundary(ApplyBoundary::ResourceEvidenceDelete)?;
        if op.resource == paths::Resource::Tree {
            boundary(ApplyBoundary::TreeEvidenceDelete)?;
        }
        // Apply has just checked the complete captured image. Retain the
        // evidence-directory name validation, but not recovery's final fsync:
        // _DestinationRoot.discard deletes evidence without syncing its parent.
        for entry in rfs::Dir::read_from(&saved).map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            if !matches!(entry.file_name().to_bytes(), b"." | b".." | b"entry") {
                return Err(invalid("foreign path evidence"));
            }
        }
        if paths::capture_at(&saved, "entry", op.resource)? != path.before {
            return Err(invalid("modified path evidence"));
        }
        drop(saved);
        bundles::delete_tree_observed(&path.quarantine, parent, &mut || {
            if op.resource == paths::Resource::Tree {
                boundary(ApplyBoundary::TreeEvidenceDeletedFile)?;
            }
            Ok(())
        })?;
    }
    if let Some(Image::Tree(tree)) = &path.after {
        let stage = nonce(".agent-workbench-copy-")?;
        boundary(ApplyBoundary::ResourceStage)?;
        let mut stage_consumed = false;
        let publication = (|| -> Result<()> {
            tree.write_journal_observed(&stage, parent, &mut |edge, _| {
                boundary(ApplyBoundary::TreeStage(edge))
            })?;
            boundary(ApplyBoundary::ResourcePublish)?;
            parents.verify()?;
            bundles::rename_new(&stage, parent, name, parent)?;
            stage_consumed = true;
            boundary(ApplyBoundary::ResourceParentSync)?;
            rfs::fsync(parent).map_err(io_error)?;
            boundary(ApplyBoundary::ResourcePublished)?;
            Ok(())
        })();
        boundary(ApplyBoundary::ResourceCleanup)?;
        if stage_consumed {
            // The successful no-replace rename consumed our staging inode.
            // Anything now occupying its old name is foreign, never cleanup.
            match rfs::statat(parent, &stage, rfs::AtFlags::SYMLINK_NOFOLLOW) {
                Err(rustix::io::Errno::NOENT) => (),
                Err(error) => return Err(io_error(error)),
                Ok(_) => {
                    return Err(io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        ForeignStageReplacement(op.path.parent().unwrap().join(&stage)),
                    )
                    .into())
                }
            }
        } else {
            match bundles::delete_tree(&stage, parent) {
                Ok(()) => (),
                Err(e) if e.kind() == io::ErrorKind::NotFound => (),
                Err(e) => return Err(e.into()),
            }
        }
        publication?;
    }
    if let Some(Image::Symlink(target)) = &path.after {
        boundary(ApplyBoundary::ResourcePublish)?;
        parents.verify()?;
        rfs::symlinkat(target, parent, name).map_err(io_error)?;
        boundary(ApplyBoundary::ResourcePublished)?;
    }
    if let Some(Image::File(bytes)) = &path.after {
        let stage = nonce(".agent-workbench-copy-")?;
        boundary(ApplyBoundary::ResourceStage)?;
        let fd = rfs::openat(
            parent,
            &stage,
            rfs::OFlags::WRONLY | rfs::OFlags::CREATE | rfs::OFlags::EXCL | rfs::OFlags::NOFOLLOW,
            rfs::Mode::from_raw_mode(0o644),
        )
        .map_err(io_error)?;
        let publication = (|| -> Result<()> {
            let mut file = std::fs::File::from(fd);
            file.write_all(bytes)?;
            boundary(ApplyBoundary::FileSync)?;
            file.sync_all()?;
            drop(file);
            boundary(ApplyBoundary::ResourcePublish)?;
            parents.verify()?;
            boundary(ApplyBoundary::FileLink)?;
            rfs::linkat(parent, &stage, parent, name, rfs::AtFlags::empty()).map_err(io_error)?;
            Ok(())
        })();
        boundary(ApplyBoundary::FileUnlink)?;
        rfs::unlinkat(parent, &stage, rfs::AtFlags::empty()).map_err(io_error)?;
        boundary(ApplyBoundary::FileUnlinked)?;
        publication?;
        boundary(ApplyBoundary::ResourcePublished)?;
    }
    parents.verify_root(op.path.parent().unwrap())?;
    Ok(())
}
