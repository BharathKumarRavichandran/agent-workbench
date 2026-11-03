#[cfg(test)]
mod parity_tests;

use super::resource::PathResource;
use super::{ComponentKind, Result};
mod decode;
pub(crate) mod wire;
use serde::ser::SerializeMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Entry {
    pub key: String,
    pub plugin: String,
    pub source: String,
    pub destination: PathBuf,
    pub source_repository: PathBuf,
    pub component: ComponentKind,
    pub resource: PathResource,
    pub trust: Option<String>,
    pub trust_proof: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct State {
    pub schema_version: u64,
    pub repository: PathBuf,
    pub marketplace: String,
    pub profile: String,
    pub target: String,
    pub destination_root: PathBuf,
    pub entries: BTreeMap<String, Entry>,
    pub destination_roots: BTreeMap<ComponentKind, PathBuf>,
    pub slots: BTreeMap<String, Slot>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct Slot {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trust_proof: Option<String>,
    pub key: String,
    pub plugin: String,
    pub component: String,
    pub document: String,
    pub slot: String,
    pub digest: String,
    pub trust: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Encoded<'a> {
    schema_version: u64,
    repository: &'a str,
    marketplace: &'a str,
    profile: &'a str,
    target: &'a str,
    destination_root: &'a str,
    entries: BTreeMap<&'a str, EncodedEntry<'a>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    slots: Option<&'a BTreeMap<String, Slot>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    destination_roots: Option<BTreeMap<&'a str, &'a str>>,
}

struct EncodedEntry<'a> {
    legacy: bool,
    trust_proof: Option<&'a str>,
    key: &'a str,
    plugin: &'a str,
    source: &'a str,
    destination: &'a str,
    component: &'a str,
    resource_kind: &'a str,
    source_repository: &'a str,
    include_trust: bool,
    trust: Option<&'a str>,
    digest: Option<&'a str>,
}

impl Serialize for EncodedEntry<'_> {
    fn serialize<S: serde::Serializer>(
        &self,
        serializer: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(None)?;
        if let Some(proof) = self.trust_proof {
            map.serialize_entry("trustProof", proof)?;
        }
        map.serialize_entry("key", self.key)?;
        map.serialize_entry("plugin", self.plugin)?;
        map.serialize_entry("source", self.source)?;
        map.serialize_entry("destination", self.destination)?;
        if self.legacy {
            return map.end();
        }
        map.serialize_entry("component", self.component)?;
        map.serialize_entry("resourceKind", self.resource_kind)?;
        map.serialize_entry("sourceRepository", self.source_repository)?;
        if self.include_trust {
            map.serialize_entry("trust", &self.trust)?;
        }
        if let Some(digest) = self.digest {
            map.serialize_entry("digest", digest)?;
        }
        map.end()
    }
}

#[cfg_attr(not(test), allow(dead_code))]
pub(super) fn entry_json(entry: &Entry) -> Result<String> {
    serde_json::to_string_pretty(&encoded_entry(entry)?).map_err(|error| error.to_string())
}

fn encoded_entry(entry: &Entry) -> Result<EncodedEntry<'_>> {
    Ok(EncodedEntry {
        legacy: false,
        trust_proof: entry.trust_proof.as_deref(),
        key: &entry.key,
        plugin: &entry.plugin,
        source: &entry.source,
        destination: entry
            .destination
            .to_str()
            .ok_or("state destination is not UTF-8")?,
        component: entry.component.as_str(),
        resource_kind: entry.resource.kind(),
        source_repository: entry
            .source_repository
            .to_str()
            .ok_or("state source repository is not UTF-8")?,
        include_trust: matches!(entry.resource, PathResource::DirectoryCopy(_)),
        trust: entry.trust.as_deref(),
        digest: entry
            .resource
            .digest()
            .map(super::resource::ContentDigest::as_str),
    })
}

pub(super) fn load(path: &Path) -> Result<Option<State>> {
    let metadata = match fs::metadata(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    if !metadata.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let value = wire::parse(&text)?;
    decode::decode(&value, path).map(Some)
}

pub(super) fn encode(state: &State) -> Result<String> {
    let repository = state
        .repository
        .to_str()
        .ok_or("state repository is not UTF-8")?;
    let destination_root = state
        .destination_root
        .to_str()
        .ok_or("state destination root is not UTF-8")?;
    let entries = state
        .entries
        .iter()
        .map(|(key, entry)| {
            let mut encoded = encoded_entry(entry)?;
            encoded.legacy = state.schema_version == 2;
            Ok((key.as_str(), encoded))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let destination_roots = state
        .destination_roots
        .iter()
        .map(|(component, path)| {
            Ok((
                component.as_str(),
                path.to_str().ok_or("state destination root is not UTF-8")?,
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let encoded = Encoded {
        schema_version: state.schema_version,
        repository,
        marketplace: &state.marketplace,
        profile: &state.profile,
        target: &state.target,
        destination_root,
        entries,
        slots: (state.schema_version == 4).then_some(&state.slots),
        destination_roots: (state.schema_version >= 3).then_some(destination_roots),
    };
    serde_json::to_string_pretty(&encoded)
        .map(|value| value + "\n")
        .map_err(|error| error.to_string())
}

pub(super) fn write(path: &Path, state: &State) -> Result<()> {
    let content = encode(state)?;
    let parent = path.parent().ok_or("state path has no parent")?;
    fs::create_dir_all(parent).map_err(|error| format!("{}: {error}", parent.display()))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap().to_string_lossy(),
        std::process::id()
    ));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| format!("{}: {error}", temporary.display()))?;
    let result = (|| {
        file.write_all(content.as_bytes())
            .map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        fs::rename(&temporary, path).map_err(|error| error.to_string())?;
        fs::File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub(super) fn remove(path: &Path) -> Result<()> {
    if !path.exists() {
        return Ok(());
    }
    match fs::remove_file(path) {
        Ok(()) => {
            if let Some(parent) = path.parent() {
                fs::File::open(parent)
                    .and_then(|directory| directory.sync_all())
                    .map_err(|error| error.to_string())?;
            }
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}
