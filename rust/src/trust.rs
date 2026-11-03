//! D2 trust primitives. Activation planning is intentionally gated on D6.
// Remove this allowance when the activation planner consumes these primitives.
#![cfg_attr(not(test), allow(dead_code))]

use crate::{ComponentKind, Plugin, Result, Source, SourceKind};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::path::Path;

#[cfg(test)]
mod parity_tests;

/// Resolved inputs, not catalog-relative artifact declarations. Explicit artifact
/// overrides win; an absent fallback is the Python string "None", not JSON null.
pub(super) struct Grant<'a> {
    pub plugin: &'a Plugin,
    pub source: &'a Source,
    pub component: ComponentKind,
    pub target: &'a str,
    pub document: &'a Path,
    pub repository: &'a Path,
    pub artifact: Option<&'a Path>,
    pub fallback: Option<&'a Path>,
    pub digest: &'a str,
}

impl Grant<'_> {
    pub fn token(&self) -> Result<String> {
        let kind = match self.source.kind {
            SourceKind::Local => "local",
            SourceKind::CuratedCopy => "curated-copy",
            SourceKind::GitSubmodule => "git-submodule",
        };
        let artifact = self
            .artifact
            .or(self.fallback)
            .map(path_text)
            .transpose()?
            .unwrap_or_else(|| "None".into());
        let provenance = self.plugin.provenance.as_ref();
        let value = json!([
            self.plugin.id,
            self.component.as_str(),
            self.target,
            path_text(self.document)?,
            [
                path_text(self.repository)?,
                self.source.id,
                kind,
                self.source.path,
                self.source.upstream,
                self.source.branch,
                artifact,
                provenance.map(|p| p.upstream.as_str()),
                provenance.and_then(|p| p.notice.as_deref())
            ],
            self.digest
        ]);
        Ok(format!(
            "aw-trust-v1:{:x}",
            Sha256::digest(ascii_json(&value)?)
        ))
    }
}

/// Read-only inspection never invokes recovery or key creation. The D4/D6 caller
/// must supply an already validated descriptor-relative journal snapshot; this
/// boundary deliberately does not interpret or authenticate a recovery journal.
pub(super) fn check_inspection(pending_journal: Option<&[u8]>) -> Result<()> {
    if pending_journal.is_some() {
        return Err(
            "pending recovery; run status for the owning profile before requesting trust".into(),
        );
    }
    Ok(())
}

/// Both persisted record variants use their exact state wire representation.
pub(super) trait TrustedRecord: Clone + Eq {
    fn proof(&self) -> Option<&str>;
    fn set_proof(&mut self, proof: Option<String>);
    fn value(&self) -> Result<serde_json::Value>;
}

impl TrustedRecord for crate::state::Entry {
    fn proof(&self) -> Option<&str> {
        self.trust_proof.as_deref()
    }
    fn set_proof(&mut self, proof: Option<String>) {
        self.trust_proof = proof;
    }
    fn value(&self) -> Result<serde_json::Value> {
        serde_json::from_str(&crate::state::entry_json(self)?).map_err(|e| e.to_string())
    }
}

impl TrustedRecord for crate::state::Slot {
    fn proof(&self) -> Option<&str> {
        self.trust_proof.as_deref()
    }
    fn set_proof(&mut self, proof: Option<String>) {
        self.trust_proof = proof;
    }
    fn value(&self) -> Result<serde_json::Value> {
        serde_json::to_value(self).map_err(|e| e.to_string())
    }
}

fn record_mac(record: &impl TrustedRecord, file: &Path) -> Result<hmac::Hmac<Sha256>> {
    use hmac::Mac;
    let mut value = record.value()?;
    value
        .as_object_mut()
        .ok_or("trust record must be an object")?
        .remove("trustProof");
    let message = ascii_json(&json!(["aw-persisted-trust-v1", path_text(file)?, value]))?;
    let mut mac =
        hmac::Hmac::<Sha256>::new_from_slice(&read_key(file)?).map_err(|e| e.to_string())?;
    mac.update(&message);
    Ok(mac)
}

/// Read-only: never creates authority. D5 transactions will own key creation.
pub(super) fn record_proof(record: &impl TrustedRecord, file: &Path) -> Result<String> {
    use hmac::Mac;
    use std::fmt::Write;
    let mut proof = String::from("aw-proof-v1:");
    for byte in record_mac(record, file)?.finalize().into_bytes() {
        write!(proof, "{byte:02x}").unwrap();
    }
    Ok(proof)
}

pub(super) fn authenticated<R: TrustedRecord>(record: Option<&R>, file: &Path) -> bool {
    use hmac::Mac;
    let Some(record) = record else {
        return false;
    };
    let Some(hex) = record.proof().and_then(|p| p.strip_prefix("aw-proof-v1:")) else {
        return false;
    };
    // The wire format is lowercase hex. Decoding then verify_slice gives a
    // constant-time MAC comparison without accepting alternate spellings.
    if hex.len() != 64
        || !hex
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return false;
    }
    let mut signature = [0u8; 32];
    for (index, byte) in signature.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).unwrap();
    }
    record_mac(record, file).is_ok_and(|mac| mac.verify_slice(&signature).is_ok())
}

pub(super) fn reuse_proof<R: TrustedRecord>(record: R, prior: Option<&R>, file: &Path) -> R {
    if let Some(prior) = prior.filter(|prior| authenticated(Some(*prior), file)) {
        let mut candidate = record.clone();
        candidate.set_proof(prior.proof().map(str::to_owned));
        if candidate == *prior {
            return candidate;
        }
    }
    record
}

pub(super) fn entry_granted(
    token: &str,
    explicit: &[String],
    prior: Option<&crate::state::Entry>,
    file: &Path,
) -> bool {
    explicit.iter().any(|grant| grant == token)
        || prior.is_some_and(|prior| {
            prior.trust.as_deref() == Some(token) && authenticated(Some(prior), file)
        })
}

/// A matching authenticated slot grants the plugin/component token to all its
/// slots, not just the old slot name. Hooks may also reuse their bundle evidence.
pub(super) fn slots_granted<'a>(
    token: &str,
    plugin: &str,
    component: ComponentKind,
    explicit: &[String],
    slots: impl IntoIterator<Item = &'a crate::state::Slot>,
    bundle: Option<&crate::state::Entry>,
    file: &Path,
) -> bool {
    explicit.iter().any(|grant| grant == token)
        || slots.into_iter().any(|slot| {
            slot.plugin == plugin
                && slot.component == component.as_str()
                && slot.trust == token
                && authenticated(Some(slot), file)
        })
        || (component == ComponentKind::Hooks
            && bundle.is_some_and(|entry| {
                entry.key == format!("hook:{plugin}")
                    && entry.trust.as_deref() == Some(token)
                    && authenticated(Some(entry), file)
            }))
}

fn read_key(file: &Path) -> Result<[u8; 32]> {
    use rustix::fs::{self as rfs, Mode, OFlags};
    use std::io::Read;
    use std::path::Component;
    let parent = file.parent().ok_or("state file has no parent")?;
    if !parent.is_absolute()
        || parent
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err("directory must be absolute and contained".into());
    }
    let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW;
    let mut directory = rfs::open("/", flags, Mode::empty()).map_err(|e| e.to_string())?;
    for part in parent.components() {
        if let Component::Normal(name) = part {
            directory =
                rfs::openat(&directory, name, flags, Mode::empty()).map_err(|e| e.to_string())?;
        }
    }
    let name = format!(
        ".{}.transaction-key",
        file.file_name()
            .and_then(|s| s.to_str())
            .ok_or("state filename is not UTF-8")?
    );
    let fd = rfs::openat(
        &directory,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    )
    .map_err(|e| e.to_string())?;
    let info = rfs::fstat(&fd).map_err(|e| e.to_string())?;
    if !rfs::FileType::from_raw_mode(info.st_mode).is_file()
        || info.st_mode & 0o7777 != 0o600
        || info.st_uid != rustix::process::getuid().as_raw()
        || info.st_nlink != 1
        || info.st_size != 32
    {
        return Err("unsafe private transaction key".into());
    }
    let mut key = Vec::new();
    std::fs::File::from(fd)
        .take(33)
        .read_to_end(&mut key)
        .map_err(|e| e.to_string())?;
    key.try_into()
        .map_err(|_| "unsafe private transaction key".into())
}

fn path_text(path: &Path) -> Result<String> {
    let text = path.to_str().ok_or("trust path is not UTF-8")?;
    // pathlib.PurePosixPath is lexical: collapse separators/dots but retain
    // parent components and the special exactly-two-leading-slashes anchor.
    let anchor = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<_> = text
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect();
    let normalized = format!("{anchor}{}", parts.join("/"));
    Ok(if normalized.is_empty() {
        ".".into()
    } else {
        normalized
    })
}

/// Python's compact ensure_ascii=True JSON, including surrogate pairs. Value
/// object maps are sorted (serde_json without preserve_order), while arrays keep
/// order. Tokens use arrays; persisted proofs use sorted-key objects.
pub(crate) fn ascii_json(value: &serde_json::Value) -> Result<Vec<u8>> {
    use std::fmt::Write;
    let compact = serde_json::to_string(value).map_err(|e| e.to_string())?;
    let mut output = String::new();
    for ch in compact.chars() {
        if ch >= '\u{7f}' {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                write!(output, "\\u{unit:04x}").unwrap();
            }
        } else {
            output.push(ch);
        }
    }
    Ok(output.into_bytes())
}
