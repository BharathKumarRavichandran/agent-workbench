use super::resource::PathResource;
use super::{reject_duplicate_json_keys, ComponentKind, Result};
use serde::ser::SerializeMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Component, Path, PathBuf};

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
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct V2 {
    schema_version: u64,
    repository: String,
    marketplace: String,
    profile: String,
    target: String,
    destination_root: String,
    entries: BTreeMap<String, V2Entry>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct V2Entry {
    key: String,
    plugin: String,
    source: String,
    destination: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct V3 {
    schema_version: u64,
    repository: String,
    marketplace: String,
    profile: String,
    target: String,
    destination_root: String,
    entries: BTreeMap<String, V3Entry>,
    destination_roots: BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct V3Entry {
    key: String,
    plugin: String,
    source: String,
    destination: String,
    component: String,
    resource_kind: String,
    source_repository: String,
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
    destination_roots: BTreeMap<&'static str, &'a str>,
}

struct EncodedEntry<'a> {
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
    reject_duplicate_json_keys(&text)?;
    let value: Value =
        serde_json::from_str(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    let version = value
        .as_object()
        .and_then(|object| object.get("schemaVersion"))
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("unsupported installation state: {}", path.display()))?;
    match version {
        2 => decode_v2(serde_json::from_value(value).map_err(|error| error.to_string())?),
        3 => decode_v3(serde_json::from_value(value).map_err(|error| error.to_string())?),
        _ => Err(format!(
            "unsupported installation state: {}",
            path.display()
        )),
    }
    .map(Some)
}

fn decode_v2(raw: V2) -> Result<State> {
    if raw.schema_version != 2 {
        return Err("unsupported installation state".into());
    }
    let repository = absolute(&raw.repository, "installation state.repository")?;
    let root = absolute(&raw.destination_root, "installation state.destinationRoot")?;
    let mut entries = BTreeMap::new();
    for (key, value) in raw.entries {
        validate_entry_identity(&key, &value.key, &value.plugin, &value.source)?;
        let destination = absolute(&value.destination, "installation state entry destination")?;
        validate_destination(&key, &destination, &root)?;
        entries.insert(
            key.clone(),
            Entry {
                key,
                plugin: value.plugin,
                source: value.source,
                destination,
                source_repository: repository.clone(),
                component: ComponentKind::Skills,
                resource: PathResource::DirectorySymlink,
                trust: None,
                trust_proof: None,
            },
        );
    }
    Ok(State {
        schema_version: 2,
        repository,
        marketplace: identifier(raw.marketplace, "installation state.marketplace")?,
        profile: identifier(raw.profile, "installation state.profile")?,
        target: identifier(raw.target, "installation state.target")?,
        destination_root: root,
        entries,
    })
}

fn decode_v3(raw: V3) -> Result<State> {
    if raw.schema_version != 3 {
        return Err("unsupported installation state".into());
    }
    if raw.destination_roots.len() != 1 || !raw.destination_roots.contains_key("skills") {
        return Err("installation state destinationRoots contains unsupported components".into());
    }
    let repository = absolute(&raw.repository, "installation state.repository")?;
    let root = absolute(&raw.destination_root, "installation state.destinationRoot")?;
    let skills_root = absolute(
        raw.destination_roots.get("skills").unwrap(),
        "installation state.destinationRoots.skills",
    )?;
    if skills_root != root {
        return Err("installation state destinationRoots skills must match destinationRoot".into());
    }
    let mut entries = BTreeMap::new();
    for (key, value) in raw.entries {
        validate_entry_identity(&key, &value.key, &value.plugin, &value.source)?;
        if value.component != "skills" || value.resource_kind != "directory-symlink" {
            // Copied resources are a closed type, but v3 multi-root authorization is a later unit.
            return Err(
                "installation state entry key, component and resource kind disagree".into(),
            );
        }
        let destination = absolute(&value.destination, "installation state entry destination")?;
        validate_destination(&key, &destination, &root)?;
        entries.insert(
            key.clone(),
            Entry {
                key,
                plugin: value.plugin,
                source: value.source,
                destination,
                source_repository: absolute(
                    &value.source_repository,
                    "installation state entry sourceRepository",
                )?,
                component: ComponentKind::Skills,
                resource: PathResource::DirectorySymlink,
                trust: None,
                trust_proof: None,
            },
        );
    }
    Ok(State {
        schema_version: 3,
        repository,
        marketplace: identifier(raw.marketplace, "installation state.marketplace")?,
        profile: identifier(raw.profile, "installation state.profile")?,
        target: identifier(raw.target, "installation state.target")?,
        destination_root: root,
        entries,
    })
}

fn validate_entry_identity(key: &str, embedded: &str, plugin: &str, source: &str) -> Result<()> {
    let name = key
        .strip_prefix("skill:")
        .filter(|name| safe_name(name))
        .ok_or("installation state entry key is not a valid skill key")?;
    if embedded != key {
        return Err(format!(
            "installation state entry key does not match mapping key: {key:?}"
        ));
    }
    let _ = name;
    identifier(plugin.to_owned(), "installation state entry plugin")?;
    source_path(source)?;
    Ok(())
}

fn validate_destination(key: &str, destination: &Path, root: &Path) -> Result<()> {
    let name = key.strip_prefix("skill:").unwrap();
    if destination.parent() != Some(root)
        || destination.file_name().and_then(|v| v.to_str()) != Some(name)
    {
        return Err(format!(
            "state destination must be a direct child of installation root: {}",
            destination.display()
        ));
    }
    Ok(())
}

fn identifier(value: String, location: &str) -> Result<String> {
    if value.is_empty()
        || !value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || (index > 0 && byte == b'-')
        })
    {
        return Err(format!("{location} is invalid"));
    }
    Ok(value)
}

fn absolute(value: &str, location: &str) -> Result<PathBuf> {
    let path = PathBuf::from(value);
    if value.is_empty()
        || value.contains('\0')
        || !path.is_absolute()
        || path.components().any(|part| part == Component::ParentDir)
    {
        return Err(format!("{location} must be absolute"));
    }
    Ok(path)
}

fn source_path(value: &str) -> Result<()> {
    let path = Path::new(value);
    if value.is_empty()
        || value.contains(['\0', '\\'])
        || path.is_absolute()
        || path.components().any(|part| {
            matches!(
                part,
                Component::ParentDir
                    | Component::CurDir
                    | Component::RootDir
                    | Component::Prefix(_)
            )
        })
    {
        return Err(
            "installation state entry source must be a contained repository source path".into(),
        );
    }
    Ok(())
}

fn safe_name(value: &str) -> bool {
    !value.is_empty()
        && value != "."
        && value != ".."
        && !value.contains(['/', '\\', '\0'])
        && Path::new(value).file_name().and_then(|name| name.to_str()) == Some(value)
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
        .map(|(key, entry)| Ok((key.as_str(), encoded_entry(entry)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let encoded = Encoded {
        schema_version: 3,
        repository,
        marketplace: &state.marketplace,
        profile: &state.profile,
        target: &state.target,
        destination_root,
        entries,
        destination_roots: BTreeMap::from([("skills", destination_root)]),
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
