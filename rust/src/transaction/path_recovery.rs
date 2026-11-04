//! Private managed-path recovery. Authentication and adapter-root authorization
//! precede this module; pinned descriptors are the only filesystem authority.
use crate::bundles;
use rustix::{
    fd::AsFd,
    fs::{self as fs, AtFlags, Mode, OFlags},
};
use serde_json::{json, Value};
use std::{
    fs::File,
    io::{self, Read, Write},
    path::PathBuf,
};

fn invalid(message: &str) -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        format!("recovery blocked by {message}; journal retained"),
    )
}
fn leaf(name: &std::ffi::OsStr) -> io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let bytes = name.as_bytes();
    if bytes.is_empty()
        || bytes.contains(&b'/')
        || bytes.contains(&0)
        || matches!(bytes, b"." | b"..")
    {
        return Err(invalid("unsafe path name"));
    }
    Ok(())
}
const B64: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
fn pack(bytes: &[u8]) -> String {
    let mut out = String::new();
    for c in bytes.chunks(3) {
        let n = (u32::from(c[0]) << 16)
            | (u32::from(*c.get(1).unwrap_or(&0)) << 8)
            | u32::from(*c.get(2).unwrap_or(&0));
        out.push(B64[((n >> 18) & 63) as usize] as char);
        out.push(B64[((n >> 12) & 63) as usize] as char);
        out.push(if c.len() > 1 {
            B64[((n >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if c.len() > 2 {
            B64[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}
fn unpack(value: &Value) -> io::Result<Vec<u8>> {
    let text = value.as_str().ok_or_else(|| invalid("invalid base64"))?;
    if text.len() % 4 != 0 {
        return Err(invalid("invalid base64"));
    }
    let mut out = Vec::new();
    for (i, c) in text.as_bytes().chunks(4).enumerate() {
        let mut n = 0u32;
        let mut padding = 0;
        for (j, &b) in c.iter().enumerate() {
            n <<= 6;
            if b == b'=' {
                padding += 1;
                if j < 2 || i + 1 != text.len() / 4 {
                    return Err(invalid("invalid base64"));
                }
            } else {
                if padding != 0 {
                    return Err(invalid("invalid base64"));
                }
                n |= B64
                    .iter()
                    .position(|&v| v == b)
                    .ok_or_else(|| invalid("invalid base64"))? as u32;
            }
        }
        if padding > 2 {
            return Err(invalid("invalid base64"));
        }
        out.push((n >> 16) as u8);
        if padding < 2 {
            out.push((n >> 8) as u8);
        }
        if padding == 0 {
            out.push(n as u8);
        }
    }
    Ok(out)
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
pub(crate) enum Resource {
    #[serde(rename = "directory-copy")]
    Tree,
    #[serde(rename = "file-copy")]
    File,
    #[serde(rename = "directory-symlink")]
    Symlink,
}
#[derive(Clone, Debug)]
pub(crate) enum Snapshot {
    Tree(bundles::Tree),
    File(Vec<u8>),
    Symlink(PathBuf),
    /// MAC-verified wire identity is separate from decoded publication content.
    /// Unknown safe fields, boolean modes and lexical/base64 aliases must not
    /// authenticate a canonical capture merely because their bytes match.
    Authenticated {
        original: super::journal_wire::Wire,
        decoded: Option<Box<Snapshot>>,
    },
}
impl PartialEq for Snapshot {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Authenticated { .. }, _) | (_, Self::Authenticated { .. }) => {
                matches!((self.identity(), other.identity()), (Ok(a), Ok(b)) if a == b)
            }
            (Self::Tree(a), Self::Tree(b)) => a == b,
            (Self::File(a), Self::File(b)) => a == b,
            (Self::Symlink(a), Self::Symlink(b)) => a.as_os_str() == b.as_os_str(),
            _ => false,
        }
    }
}
impl Eq for Snapshot {}
impl Snapshot {
    fn identity(&self) -> io::Result<super::journal_wire::Wire> {
        match self {
            Self::Authenticated { original, .. } => Ok(original.clone()),
            Self::Symlink(path) => Ok(super::journal_wire::Wire::Object(vec![(
                super::journal_wire::Text::ascii("link"),
                super::journal_wire::Wire::String(super::journal_wire::Text::from_path(path)),
            )])),
            Self::Tree(tree) => {
                use super::journal_wire::{Text, Wire};
                let (dirs, files) = tree.to_journal();
                let dirs = dirs
                    .iter()
                    .map(|(p, m)| {
                        Wire::Array(vec![
                            Wire::String(Text::from_path(p)),
                            Wire::from_value(&json!(m)),
                        ])
                    })
                    .collect();
                let files = files
                    .iter()
                    .map(|(p, m, bytes)| {
                        Wire::Array(vec![
                            Wire::String(Text::from_path(p)),
                            Wire::from_value(&json!(m)),
                            Wire::from_value(&json!(pack(bytes))),
                        ])
                    })
                    .collect();
                Ok(Wire::Object(vec![(
                    Text::ascii("tree"),
                    Wire::Object(vec![
                        (Text::ascii("directories"), Wire::Array(dirs)),
                        (Text::ascii("files"), Wire::Array(files)),
                    ]),
                )]))
            }
            _ => Ok(super::journal_wire::Wire::from_value(&self.to_journal()?)),
        }
    }
    pub(crate) fn from_lossless_journal(value: &super::journal_wire::Wire) -> io::Result<Self> {
        if !value.as_object().is_some_and(|o| o.len() == 1) {
            return Err(invalid("invalid path snapshot"));
        }
        if value.has("link") {
            let link = value.get("link");
            let super::journal_wire::Wire::String(text) = link else {
                return Err(invalid("invalid link"));
            };
            // Unencodable high surrogates remain valid inert images, as in Python.
            let decoded = link.path().ok().map(|p| Box::new(Self::Symlink(p)));
            if text.contains_nul() {
                return Err(invalid("invalid link"));
            }
            return Ok(Self::Authenticated {
                original: value.clone(),
                decoded,
            });
        }
        // Inert after-images are validated without forcing Python code points
        // through Rust String or filesystem encoding. Retain the entire identity.
        let projection = if value.has("tree") {
            let tree = value.get("tree");
            for (key, len) in [("directories", 2), ("files", 3)] {
                let records = tree
                    .get(key)
                    .as_array()
                    .ok_or_else(|| invalid("invalid tree"))?;
                for record in records {
                    let row = record
                        .as_array()
                        .filter(|a| a.len() == len)
                        .ok_or_else(|| invalid("invalid tree record"))?;
                    super::journal_wire::validate_tree_name(&row[0]).map_err(|e| invalid(&e))?;
                    let mode = row[1].value().map_err(|e| invalid(&e))?;
                    if mode
                        .as_u64()
                        .or_else(|| mode.as_bool().map(u64::from))
                        .filter(|n| *n <= 0o777)
                        .is_none()
                    {
                        return Err(invalid("invalid tree mode"));
                    }
                    if len == 3 {
                        unpack(&row[2].value().map_err(|e| invalid(&e))?)?;
                    }
                }
            }
            tree.project(&["directories", "files"])
                .map(|tree| json!({"tree": tree}))
        } else {
            value.value()
        };
        let decoded = match projection {
            Ok(v) => Some(Box::new(Self::decode_journal(&v)?)),
            Err(_) if value.has("tree") => {
                let tree = value.get("tree");
                let mode = |row: &super::journal_wire::Wire| {
                    let v = row[1].value().unwrap();
                    v.as_u64().or_else(|| v.as_bool().map(u64::from)).unwrap() as u32
                };
                let dirs = tree
                    .get("directories")
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| row[0].path().map(|p| (p, mode(row))))
                    .collect::<Result<Vec<_>, _>>();
                let files = tree
                    .get("files")
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| {
                        Ok((
                            row[0].path()?,
                            mode(row),
                            unpack(&row[2].value()?).map_err(|e| e.to_string())?,
                        ))
                    })
                    .collect::<Result<Vec<_>, String>>();
                match (dirs, files) {
                    (Ok(dirs), Ok(files)) => Some(Box::new(Self::Tree(
                        bundles::Tree::from_journal(dirs, files)?,
                    ))),
                    _ => None,
                }
            }
            Err(e) => return Err(invalid(&e)),
        };
        Ok(Self::Authenticated {
            original: value.clone(),
            decoded,
        })
    }
    /// Decode only after MAC verification. This supplies content, never path authority.
    pub(crate) fn from_journal(value: &Value) -> io::Result<Self> {
        Self::from_lossless_journal(&super::journal_wire::Wire::from_value(value))
    }
    fn decoded(&self) -> &Self {
        match self {
            Self::Authenticated {
                decoded: Some(decoded),
                ..
            } => decoded.decoded(),
            _ => self,
        }
    }
    fn decode_journal(value: &Value) -> io::Result<Self> {
        let object = value
            .as_object()
            .filter(|o| o.len() == 1)
            .ok_or_else(|| invalid("invalid path snapshot"))?;
        if let Some(v) = object.get("file") {
            return Ok(Self::File(unpack(v)?));
        }
        if let Some(v) = object.get("link") {
            let s = v
                .as_str()
                .filter(|s| !s.contains('\0'))
                .ok_or_else(|| invalid("invalid link"))?;
            return Ok(Self::Symlink(s.into()));
        }
        let v = object
            .get("tree")
            .ok_or_else(|| invalid("invalid path snapshot"))?;
        let dirs = v
            .get("directories")
            .and_then(Value::as_array)
            .ok_or_else(|| invalid("invalid tree"))?;
        let files = v
            .get("files")
            .and_then(Value::as_array)
            .ok_or_else(|| invalid("invalid tree"))?;
        fn record(v: &Value, len: usize) -> io::Result<(PathBuf, u32)> {
            let a = v
                .as_array()
                .filter(|a| a.len() == len)
                .ok_or_else(|| invalid("invalid tree record"))?;
            let path = a[0].as_str().ok_or_else(|| invalid("invalid tree path"))?;
            let mode = a[1]
                .as_u64()
                .or_else(|| a[1].as_bool().map(u64::from))
                .filter(|n| *n <= 0o777)
                .ok_or_else(|| invalid("invalid tree mode"))?;
            Ok((path.into(), mode as u32))
        }
        let dirs = dirs
            .iter()
            .map(|v| record(v, 2))
            .collect::<io::Result<_>>()?;
        let files = files
            .iter()
            .map(|v| {
                let (p, m) = record(v, 3)?;
                Ok((p, m, unpack(&v[2])?))
            })
            .collect::<io::Result<_>>()?;
        Ok(Self::Tree(bundles::Tree::from_journal(dirs, files)?))
    }
    pub(crate) fn to_journal(&self) -> io::Result<Value> {
        Ok(match self {
            Self::Authenticated { original, .. } => original.value().map_err(|e| invalid(&e))?,
            Self::File(bytes) => json!({"file":pack(bytes)}),
            Self::Symlink(_) => self.identity()?.value().map_err(|e| invalid(&e))?,
            Self::Tree(tree) => {
                let (dirs, files) = tree.to_journal();
                let dirs = dirs
                    .iter()
                    .map(|(p, m)| {
                        Ok(json!([
                            p.to_str().ok_or_else(|| invalid("non-UTF8 tree"))?,
                            m
                        ]))
                    })
                    .collect::<io::Result<Vec<_>>>()?;
                let files = files
                    .iter()
                    .map(|(p, m, b)| {
                        Ok(json!([
                            p.to_str().ok_or_else(|| invalid("non-UTF8 tree"))?,
                            m,
                            pack(b)
                        ]))
                    })
                    .collect::<io::Result<Vec<_>>>()?;
                json!({"tree":{"directories":dirs,"files":files}})
            }
        })
    }
    fn resource(&self) -> Resource {
        match self.decoded() {
            Self::Authenticated { original, .. } => {
                if original.has("tree") {
                    Resource::Tree
                } else if original.has("file") {
                    Resource::File
                } else {
                    Resource::Symlink
                }
            }
            Self::Tree(_) => Resource::Tree,
            Self::File(_) => Resource::File,
            Self::Symlink(_) => Resource::Symlink,
        }
    }
}
/// An authenticated tree can contain an unencodable Python codepoint. Do not
/// reject it eagerly: the oracle retains all staging performed before encoding
/// that particular name fails. This path can only fail, never publish a tree.
fn stage_unencodable_tree(
    parent: &impl AsFd,
    name: &str,
    original: &super::journal_wire::Wire,
    edge: &mut impl FnMut(Edge) -> io::Result<()>,
) -> io::Result<()> {
    let tree = original.get("tree");
    fs::mkdirat(parent, name, Mode::from_raw_mode(0o700))?;
    let fd = fs::openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    )?;
    let mut directories = tree
        .get("directories")
        .as_array()
        .unwrap()
        .iter()
        .collect::<Vec<_>>();
    // Path separators are ASCII and survive every supported source encoding.
    directories.sort_by_key(|row| {
        row[0]
            .canonical()
            .unwrap()
            .bytes()
            .filter(|&b| b == b'/')
            .count()
    });
    for row in directories {
        let path = row[0].path().map_err(|e| invalid(&e))?;
        if !path.as_os_str().is_empty() {
            fs::mkdirat(&fd, &path, Mode::from_raw_mode(0o700))?;
        }
    }
    for row in tree.get("files").as_array().unwrap() {
        let path = row[0].path().map_err(|e| invalid(&e))?;
        let bytes = unpack(&row[2].value().map_err(|e| invalid(&e))?)?;
        let mode = row[1].value().map_err(|e| invalid(&e))?;
        let mode = mode
            .as_u64()
            .or_else(|| mode.as_bool().map(u64::from))
            .unwrap() as u32;
        let out = fs::openat(
            &fd,
            &path,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
            Mode::from_raw_mode(0o600),
        )?;
        let mut file = File::from(out);
        edge(Edge::TreeWrite(bundles::TreeWriteEdge::FileWrite))?;
        file.write_all(&bytes)?;
        file.flush()?;
        edge(Edge::TreeWrite(bundles::TreeWriteEdge::FileChmod))?;
        fs::fchmod(&file, Mode::from_raw_mode(mode as _))?;
        edge(Edge::TreeWrite(bundles::TreeWriteEdge::FileSync))?;
        file.sync_all()?;
    }
    Err(invalid("path is not filesystem-encodable"))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Revision {
    device: u64,
    inode: u64,
    mode: u32,
    size: i64,
    mtime: (i64, i64),
    ctime: (i64, i64),
}
impl Revision {
    fn same_captured(&self, b: &Self) -> bool {
        self.device == b.device
            && self.inode == b.inode
            && self.mode == b.mode
            && self.size == b.size
            && self.mtime == b.mtime
    }
}
#[allow(clippy::unnecessary_cast)]
pub(crate) fn revision(
    parent: &impl AsFd,
    name: impl AsRef<std::ffi::OsStr>,
) -> io::Result<Option<Revision>> {
    let name = name.as_ref();
    leaf(name)?;
    let s = match fs::statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(s) => s,
        Err(rustix::io::Errno::NOENT) => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    Ok(Some(Revision {
        device: s.st_dev as u64,
        inode: s.st_ino as u64,
        mode: s.st_mode as u32,
        size: s.st_size,
        mtime: (s.st_mtime as i64, s.st_mtime_nsec as i64),
        ctime: (s.st_ctime as i64, s.st_ctime_nsec as i64),
    }))
}
pub(crate) fn capture_at(
    parent: &impl AsFd,
    name: impl AsRef<std::ffi::OsStr>,
    resource: Resource,
) -> io::Result<Option<Snapshot>> {
    let name = name.as_ref();
    let before = revision(parent, name)?;
    if before.is_none() {
        return Ok(None);
    }
    let value = match resource {
        Resource::Tree => Snapshot::Tree(bundles::read_tree(name, parent)?),
        Resource::Symlink => Snapshot::Symlink({
            use std::os::unix::ffi::OsStringExt;
            std::ffi::OsString::from_vec(fs::readlinkat(parent, name, Vec::new())?.into_bytes())
                .into()
        }),
        Resource::File => {
            let fd = fs::openat(
                parent,
                name,
                OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
                Mode::empty(),
            )?;
            if !fs::FileType::from_raw_mode(fs::fstat(&fd)?.st_mode).is_file() {
                return Err(invalid("nonregular file"));
            }
            let mut bytes = Vec::new();
            File::from(fd).read_to_end(&mut bytes)?;
            Snapshot::File(bytes)
        }
    };
    if revision(parent, name)? != before {
        return Err(invalid("managed path changed while reading"));
    }
    Ok(Some(value))
}
#[derive(Clone, Debug)]
pub(crate) struct PathRestore {
    pub(crate) before: Option<Snapshot>,
    pub(crate) expected: Option<Snapshot>,
    pub(crate) allowed: Vec<Option<Snapshot>>,
    pub(crate) revision: Option<Revision>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Edge {
    Capture,
    CaptureSync,
    Stage,
    FileWrite,
    FileSync,
    TreeWrite(bundles::TreeWriteEdge),
    Publish,
    PublishSync,
    EvidenceRemove,
    EvidenceDeletedFile,
    EvidenceSync,
}
fn discard(parent: &impl AsFd, name: &str) -> io::Result<()> {
    let Some(r) = revision(parent, name)? else {
        return Ok(());
    };
    if fs::FileType::from_raw_mode(r.mode as _).is_dir() {
        bundles::delete_tree(name, parent)
    } else {
        Ok(fs::unlinkat(parent, name, AtFlags::empty())?)
    }
}
pub(crate) fn restore_path(
    parent: &impl AsFd,
    name: impl AsRef<std::ffi::OsStr>,
    resource: Resource,
    restore: &PathRestore,
    quarantine: &str,
    mut edge: impl FnMut(Edge) -> io::Result<()>,
) -> io::Result<()> {
    let name = name.as_ref();
    leaf(name)?;
    leaf(quarantine.as_ref())?;
    if name == quarantine
        || name == std::ffi::OsStr::new(&format!("{quarantine}-tree"))
        || name == std::ffi::OsStr::new(&format!("{quarantine}-file"))
    {
        return Err(invalid("aliased quarantine"));
    }
    for s in restore
        .allowed
        .iter()
        .chain([&restore.before, &restore.expected])
        .flatten()
    {
        if s.resource() != resource {
            return Err(invalid("snapshot resource mismatch"));
        }
    }
    let current = capture_at(parent, name, resource)?;
    if current != restore.expected || revision(parent, name)? != restore.revision {
        return Err(invalid("modified managed path"));
    }
    let mut saved = capture_at(parent, quarantine, resource)?;
    if saved.is_some() && !restore.allowed.contains(&saved) {
        return Err(invalid("modified managed path"));
    }
    if current != restore.before {
        if current.is_some() {
            if saved.is_some() {
                return Err(invalid("occupied path quarantine"));
            }
            edge(Edge::Capture)?;
            bundles::rename_new(name, parent, quarantine, parent)?;
            edge(Edge::CaptureSync)?;
            fs::fsync(parent)?;
            saved = capture_at(parent, quarantine, resource)?;
            let captured = revision(parent, quarantine)?;
            if saved != restore.expected
                || !matches!((&captured,&restore.revision),(Some(a),Some(b)) if a.same_captured(b))
            {
                match bundles::rename_new(quarantine, parent, name, parent) {
                    Ok(()) => fs::fsync(parent)?,
                    Err(e) if e.kind() == io::ErrorKind::AlreadyExists => (),
                    Err(e) => return Err(e),
                }
                return Err(invalid("modified managed path during capture"));
            }
        }
        if let Some(value) = &restore.before {
            match value.decoded() {
                Snapshot::Authenticated { original, .. } => {
                    if !original.has("tree") {
                        return Err(invalid("path is not filesystem-encodable"));
                    }
                    let temporary = format!("{quarantine}-tree");
                    if capture_at(parent, &temporary, Resource::Tree)?.is_some() {
                        return Err(invalid("modified staged path"));
                    }
                    edge(Edge::Stage)?;
                    stage_unencodable_tree(parent, &temporary, original, &mut edge)?;
                    unreachable!("an unencodable tree cannot be published");
                }
                Snapshot::Symlink(target) => {
                    edge(Edge::Publish)?;
                    fs::symlinkat(target, parent, name)?;
                }
                Snapshot::Tree(_) | Snapshot::File(_) => {
                    let temporary = format!(
                        "{quarantine}-{}",
                        if resource == Resource::Tree {
                            "tree"
                        } else {
                            "file"
                        }
                    );
                    let staged = capture_at(parent, &temporary, resource)?;
                    if staged.is_some() && staged.as_ref() != Some(value) {
                        return Err(invalid("modified staged path"));
                    }
                    if staged.is_none() {
                        edge(Edge::Stage)?;
                        match value.decoded() {
                            Snapshot::Tree(tree) => {
                                tree.write_journal_observed(&temporary, parent, &mut |point, _| {
                                    edge(Edge::TreeWrite(point))
                                })?
                            }
                            Snapshot::File(bytes) => {
                                let fd = fs::openat(
                                    parent,
                                    &temporary,
                                    OFlags::WRONLY
                                        | OFlags::CREATE
                                        | OFlags::EXCL
                                        | OFlags::NOFOLLOW,
                                    Mode::from_raw_mode(0o644),
                                )?;
                                let mut file = File::from(fd);
                                edge(Edge::FileWrite)?;
                                file.write_all(bytes)?;
                                edge(Edge::FileSync)?;
                                file.sync_all()?;
                            }
                            _ => unreachable!(),
                        }
                    }
                    edge(Edge::Publish)?;
                    bundles::rename_new(&temporary, parent, name, parent)?;
                }
            }
        } else if capture_at(parent, name, resource)?.is_some() {
            return Err(invalid("new managed path"));
        }
    }
    edge(Edge::PublishSync)?;
    fs::fsync(parent)?;
    if capture_at(parent, quarantine, resource)? != saved {
        return Err(invalid("modified path quarantine"));
    }
    edge(Edge::EvidenceRemove)?;
    discard(parent, quarantine)?;
    edge(Edge::EvidenceSync)?;
    fs::fsync(parent)?;
    Ok(())
}
/// Validate the entire evidence directory before deletion, including partial old
/// bundles left by interrupted deletion. No foreign names or altered records pass.
pub(crate) fn discard_path_evidence(
    parent: &impl AsFd,
    resource: Resource,
    quarantine: &str,
    before: Option<&Snapshot>,
) -> io::Result<()> {
    discard_path_evidence_observed(parent, resource, quarantine, before, &mut |_| Ok(()))
}

pub(crate) fn discard_path_evidence_observed(
    parent: &impl AsFd,
    resource: Resource,
    quarantine: &str,
    before: Option<&Snapshot>,
    edge: &mut impl FnMut(Edge) -> io::Result<()>,
) -> io::Result<()> {
    leaf(quarantine.as_ref())?;
    let fd = match fs::openat(
        parent,
        quarantine,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    ) {
        Ok(fd) => fd,
        Err(rustix::io::Errno::NOENT) => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    for entry in fs::Dir::read_from(&fd)? {
        let entry = entry?;
        if !matches!(entry.file_name().to_bytes(), b"." | b".." | b"entry") {
            return Err(invalid("foreign path evidence"));
        }
    }
    let captured = capture_at(&fd, "entry", resource)?;
    if captured.is_some() && captured.as_ref() != before {
        if let (Some(Snapshot::Tree(partial)), Some(Snapshot::Tree(complete))) = (
            captured.as_ref().map(Snapshot::decoded),
            before.map(Snapshot::decoded),
        ) {
            let (pd, pf) = partial.to_journal();
            let (cd, cf) = complete.to_journal();
            // Python's partial-tree subset compares literal record names, not
            // pathlib/PathBuf equality (which collapses internal dot segments).
            if !pd.iter().all(|(p, m)| {
                cd.iter()
                    .any(|(q, n)| p.as_os_str() == q.as_os_str() && m == n)
            }) || !pf.iter().all(|(p, m, b)| {
                cf.iter()
                    .any(|(q, n, c)| p.as_os_str() == q.as_os_str() && m == n && b == c)
            }) {
                return Err(invalid("modified path evidence"));
            }
        } else {
            return Err(invalid("modified path evidence"));
        }
    }
    drop(fd);
    bundles::delete_tree_observed(quarantine, parent, &mut || edge(Edge::EvidenceDeletedFile))?;
    edge(Edge::EvidenceSync)?;
    fs::fsync(parent)?;
    Ok(())
}
