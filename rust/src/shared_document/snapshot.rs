//! Descriptor-relative, nofollow, nonblocking shared document reads.
use super::*;
use rustix::{
    fd::AsFd,
    fs::{openat, Mode, OFlags},
};
use std::{
    fs::File,
    io::{self, Read},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
};
#[derive(Debug)]
pub(crate) enum Error {
    Io(io::Error),
    Invalid(String),
}
impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Snapshot {
    pub path: PathBuf,
    pub content: Option<Vec<u8>>,
    pub revision: Option<(u64, u64, u64, i128, i128)>,
}
pub(crate) fn from_metadata(path: PathBuf, content: Vec<u8>, info: &std::fs::Metadata) -> Snapshot {
    Snapshot {
        path,
        content: Some(content),
        revision: Some(revision(info)),
    }
}
fn revision(info: &std::fs::Metadata) -> (u64, u64, u64, i128, i128) {
    (
        info.dev(),
        info.ino(),
        info.size(),
        i128::from(info.mtime()) * 1_000_000_000 + i128::from(info.mtime_nsec()),
        i128::from(info.ctime()) * 1_000_000_000 + i128::from(info.ctime_nsec()),
    )
}
pub(crate) fn snapshot(path: &Path) -> std::result::Result<Snapshot, Error> {
    let parent = match crate::bundles::open_directory(
        path.parent()
            .ok_or_else(|| Error::Invalid("document has no parent".into()))?,
    ) {
        Ok(fd) => fd,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Ok(Snapshot {
                path: path.into(),
                content: None,
                revision: None,
            })
        }
        Err(error) => return Err(error.into()),
    };
    snapshot_at(path, &parent, || {})
}
pub(crate) fn snapshot_at(
    path: &Path,
    parent: &impl AsFd,
    after_read: impl FnOnce(),
) -> std::result::Result<Snapshot, Error> {
    let fd = match openat(
        parent,
        path.file_name()
            .ok_or_else(|| Error::Invalid("document has no name".into()))?,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    ) {
        Ok(fd) => fd,
        Err(rustix::io::Errno::NOENT) => {
            return Ok(Snapshot {
                path: path.into(),
                content: None,
                revision: None,
            })
        }
        Err(error) => return Err(io::Error::from(error).into()),
    };
    let mut handle = File::from(fd);
    let info = handle.metadata()?;
    if info.is_dir() {
        return Err(Error::Io(io::Error::from_raw_os_error(
            rustix::io::Errno::ISDIR.raw_os_error(),
        )));
    }
    if !info.is_file() {
        return Err(Error::Invalid(format!(
            "shared document must be a regular file: {}",
            path.display()
        )));
    }
    let mut content = Vec::new();
    handle.read_to_end(&mut content)?;
    after_read();
    if revision(&info) != revision(&handle.metadata()?) {
        return Err(Error::Invalid(
            "shared document changed while reading".into(),
        ));
    }
    parse_bytes(&content).map_err(Error::Invalid)?;
    Ok(Snapshot {
        path: path.into(),
        content: Some(content),
        revision: Some(revision(&info)),
    })
}
pub(crate) fn validate_ledger(content: Option<&[u8]>) -> Result<()> {
    let Some(content) = content else {
        return Ok(());
    };
    let data = parse_bytes(content)?;
    let fields = data.object("ledger")?;
    let version = data.get("version").value();
    // Python's ledger (unlike Cursor hook version) accepts True and 1.0.
    let one = version == json!(true) || version.as_f64() == Some(1.0);
    if fields.len() != 2
        || !data.has("slots")
        || !one
        || !matches!(data.get("slots"), Wire::Object(_))
    {
        return Err("invalid target ownership ledger".into());
    }
    fn digest_text(value: &str) -> bool {
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }
    for (key, value) in data.get("slots").object("slots")? {
        if !digest_text(key)
            || value.object("record").is_err()
            || value.object("record")?.len() != 2
            || value.get("owner").string().is_none()
            || !value.get("digest").string().is_some_and(digest_text)
        {
            return Err("invalid target ownership record".into());
        }
    }
    Ok(())
}
