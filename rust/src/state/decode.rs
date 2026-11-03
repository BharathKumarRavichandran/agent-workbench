//! Explicit validators follow state.py's first-error order, not serde field order.
use super::wire::{repr, Wire};
use super::*;
use crate::resource::ContentDigest;

fn string<'a>(value: &'a Wire, location: &str) -> Result<&'a str> {
    value
        .string()
        .filter(|s| !s.is_empty() && !s.contains('\0'))
        .ok_or_else(|| format!("{location} must be a non-empty string"))
}
fn keys(value: &Wire, fields: &[&str], location: &str) -> Result<()> {
    let object = value.object(location)?;
    let mut missing: Vec<_> = fields
        .iter()
        .copied()
        .filter(|key| !value.has(key))
        .collect();
    missing.sort_unstable();
    if !missing.is_empty() {
        return Err(format!(
            "{location} is missing fields: {}",
            missing.join(", ")
        ));
    }
    let mut unknown: Vec<_> = object
        .iter()
        .map(|(key, _)| key.as_str())
        .filter(|key| !fields.contains(key))
        .collect();
    unknown.sort_unstable();
    if !unknown.is_empty() {
        return Err(format!(
            "{location} has unknown fields: {}",
            unknown.join(", ")
        ));
    }
    Ok(())
}
fn identifier(value: &Wire, location: &str) -> Result<String> {
    let value = string(value, location)?;
    if !value.bytes().enumerate().all(|(index, byte)| {
        byte.is_ascii_lowercase() || byte.is_ascii_digit() || (index > 0 && byte == b'-')
    }) {
        return Err(format!("{location} is invalid"));
    }
    Ok(value.to_owned())
}
fn absolute(value: &Wire, location: &str) -> Result<PathBuf> {
    let value = string(value, location)?;
    if !value.starts_with('/') || value.split('/').any(|part| part == "..") {
        return Err(format!("{location} must be absolute"));
    }
    Ok(value.into())
}
fn source(value: &Wire, location: &str) -> Result<String> {
    let value = string(value, location)?;
    let parts: Vec<_> = value
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    if value.starts_with('/') || parts.is_empty() || parts.contains(&"..") || value.contains('\\') {
        return Err(format!(
            "{location} must be a contained repository source path"
        ));
    }
    Ok(value.to_owned())
}
fn entry_key<'a>(value: &'a str, location: &str) -> Result<&'a str> {
    if value.is_empty() || value.contains('\0') {
        return Err(format!("{location} must be a non-empty string"));
    }
    if !matches!(value.split_once(':'), Some(("skill" | "agent" | "rule" | "command" | "hook", name)) if !name.is_empty() && name != "." && name != ".." && !name.contains(['/', '\\']))
    {
        return Err(format!("{location} is not a valid entry key"));
    }
    Ok(value)
}
fn component(value: &Wire) -> Result<ComponentKind> {
    match value.string() {
        Some("skills") => Ok(ComponentKind::Skills),
        Some("agents") => Ok(ComponentKind::Agents),
        Some("rules") => Ok(ComponentKind::Rules),
        Some("commands") => Ok(ComponentKind::Commands),
        Some("hooks") => Ok(ComponentKind::Hooks),
        Some("mcp") => Ok(ComponentKind::Mcp),
        _ => Err(format!("{} is not a valid ComponentKind", value.repr())),
    }
}
fn digest(value: &Wire, location: &str, invalid: &str) -> Result<ContentDigest> {
    ContentDigest::parse(string(value, location)?).map_err(|_| invalid.to_owned())
}
// Syntactic evidence only; parsing never verifies or grants execution authority.
fn trust(value: &Wire) -> Result<String> {
    let value = string(value, "trust grant")?;
    if !value.strip_prefix("aw-trust-v1:").is_some_and(sha256) {
        return Err("invalid trust grant".into());
    }
    Ok(value.to_owned())
}
fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn proof(value: &Wire) -> Result<String> {
    let value = value
        .string()
        .filter(|value| value.strip_prefix("aw-proof-v1:").is_some_and(sha256))
        .ok_or("invalid trust proof")?;
    Ok(value.to_owned())
}
// pathlib normalizes separators/dots for comparisons, but retains exactly two
// leading slashes. Keep raw PathBuf strings in the state for byte serialization.
fn normalized(path: &Path) -> String {
    let text = path.to_str().unwrap();
    let prefix = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else {
        "/"
    };
    format!(
        "{prefix}{}",
        text.split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .collect::<Vec<_>>()
            .join("/")
    )
}

pub(super) fn decode(data: &Wire, path: &Path) -> Result<State> {
    data.object(&path.display().to_string())?;
    let version = match data.get("schemaVersion") {
        Wire::Scalar(Value::Number(number)) if number.is_u64() => number.as_u64().unwrap(),
        _ => 0,
    };
    if !matches!(version, 2..=4) {
        return Err(format!(
            "unsupported installation state: {}",
            path.display()
        ));
    }
    let mut fields = vec![
        "schemaVersion",
        "repository",
        "marketplace",
        "profile",
        "target",
        "destinationRoot",
        "entries",
    ];
    if version >= 3 {
        fields.push("destinationRoots");
    }
    if version == 4 {
        fields.push("slots");
    }
    keys(data, &fields, "installation state")?;
    let mut entries = BTreeMap::new();
    let mut entry_order = Vec::new();
    for (key, value) in data.get("entries").object("installation state.entries")? {
        entry_key(key, "installation state entry key")?;
        let location = format!("installation state.entries[{}]", repr(key));
        value.object(&location)?;
        let mut resource = PathResource::DirectorySymlink;
        let mut entry_component = ComponentKind::Skills;
        let mut fields = vec!["key", "plugin", "source", "destination"];
        if version >= 3 {
            entry_component = component(value.get("component"))?;
            let kind = value.get("resourceKind").string();
            fields.extend(["component", "resourceKind", "sourceRepository"]);
            if kind == Some("file-copy") || (kind == Some("directory-copy") && version == 4) {
                fields.push("digest");
                let digest = digest(
                    value.get("digest"),
                    "installation state entry digest",
                    "installation state entry digest must be SHA-256",
                )?;
                resource = if kind == Some("directory-copy") {
                    fields.push("trust");
                    trust(value.get("trust"))?;
                    PathResource::DirectoryCopy(digest)
                } else {
                    PathResource::FileCopy(digest)
                };
            } else if kind != Some("directory-symlink") {
                return Err("installation state entry resourceKind is unsupported".into());
            }
        }
        if value.has("trustProof") && matches!(resource, PathResource::DirectoryCopy(_)) {
            fields.push("trustProof");
            proof(value.get("trustProof"))?;
        }
        keys(value, &fields, &location)?;
        let prefix = match entry_component {
            ComponentKind::Skills => "skill",
            ComponentKind::Agents => "agent",
            ComponentKind::Rules => "rule",
            ComponentKind::Commands => "command",
            ComponentKind::Hooks => "hook",
            ComponentKind::Mcp => "",
        };
        if key.split_once(':').unwrap().0 != prefix
            || matches!(resource, PathResource::DirectorySymlink)
                != (entry_component == ComponentKind::Skills)
        {
            return Err(
                "installation state entry key, component and resource kind disagree".into(),
            );
        }
        if (entry_component == ComponentKind::Hooks)
            != matches!(resource, PathResource::DirectoryCopy(_))
        {
            return Err("hook bundles require directory-copy".into());
        }
        let embedded = string(value.get("key"), &format!("{location}.key"))?;
        entry_key(embedded, &format!("{location}.key"))?;
        if embedded != key {
            return Err(format!(
                "installation state entry key does not match mapping key: {}",
                repr(key)
            ));
        }
        let plugin = identifier(value.get("plugin"), &format!("{location}.plugin"))?;
        let source = source(value.get("source"), &format!("{location}.source"))?;
        let destination = absolute(value.get("destination"), &format!("{location}.destination"))?;
        let source_repository = absolute(
            if version >= 3 {
                value.get("sourceRepository")
            } else {
                data.get("repository")
            },
            &format!("{location}.sourceRepository"),
        )?;
        entries.insert(
            key.clone(),
            Entry {
                key: key.clone(),
                plugin,
                source,
                destination,
                source_repository,
                component: entry_component,
                resource,
                trust: value.get("trust").string().map(str::to_owned),
                trust_proof: value.get("trustProof").string().map(str::to_owned),
            },
        );
        entry_order.push(key);
    }
    let mut roots = BTreeMap::new();
    if version >= 3 {
        for (key, value) in data
            .get("destinationRoots")
            .object("installation state.destinationRoots")?
        {
            let key = component(&Wire::Scalar(Value::String(key.clone())))?;
            roots.insert(key, absolute(value, "installation state.destinationRoots")?);
        }
        if roots
            .get(&ComponentKind::Skills)
            .and_then(|path| path.to_str())
            != data.get("destinationRoot").string()
            || !roots.contains_key(&ComponentKind::Skills)
        {
            return Err(
                "installation state destinationRoots skills must match destinationRoot".into(),
            );
        }
    } else {
        // v2 does not validate this root until after entries/destinations. For
        // non-string values pathlib would raise TypeError at that use site.
        roots.insert(
            ComponentKind::Skills,
            data.get("destinationRoot").string().unwrap_or("").into(),
        );
    }
    if entries
        .values()
        .any(|entry| !roots.contains_key(&entry.component))
    {
        return Err("installation state entry lacks a destination root".into());
    }
    let mut destinations = BTreeSet::new();
    for key in entry_order {
        let entry = &entries[key];
        let destination = normalized(&entry.destination);
        let (parent, name) = destination.rsplit_once('/').unwrap();
        if name != key.split_once(':').unwrap().1 {
            return Err(format!(
                "installation state entry key does not match destination: {}",
                repr(key)
            ));
        }
        let parent = if parent.is_empty() {
            "/"
        } else if parent == "/" && destination.starts_with("//") {
            "//"
        } else {
            parent
        };
        if parent != normalized(&roots[&entry.component]) {
            return Err(format!(
                "state destination must be a direct child of installation root: {destination}"
            ));
        }
        if !destinations.insert(destination) {
            return Err("installation state destinations must be unique".into());
        }
    }
    let mut slots = BTreeMap::new();
    if version == 4 {
        for (key, value) in data.get("slots").object("installation state.slots")? {
            value.object("slot")?;
            let trust_proof = if value.has("trustProof") {
                Some(proof(value.get("trustProof"))?)
            } else {
                None
            };
            let mut fields = vec![
                "key",
                "plugin",
                "component",
                "document",
                "slot",
                "digest",
                "trust",
            ];
            if trust_proof.is_some() {
                fields.push("trustProof");
            }
            keys(value, &fields, "slot")?;
            let component = component(value.get("component"))?;
            if !matches!(component, ComponentKind::Hooks | ComponentKind::Mcp)
                || value.get("key").string() != Some(key.as_str())
            {
                return Err("invalid shared slot identity".into());
            }
            let plugin = identifier(value.get("plugin"), "slot plugin")?;
            let slot = string(value.get("slot"), "slot name")?;
            if key != &format!("{}:{plugin}:{slot}", component.as_str()) {
                return Err("invalid shared slot key".into());
            }
            let digest = digest(
                value.get("digest"),
                "slot digest",
                "invalid shared slot digest",
            )?;
            let document = absolute(value.get("document"), "slot document")?;
            let trust = trust(value.get("trust"))?;
            slots.insert(
                key.clone(),
                Slot {
                    key: key.clone(),
                    plugin,
                    component: component.as_str().into(),
                    document: document.to_str().unwrap().into(),
                    slot: slot.into(),
                    digest: digest.as_str().into(),
                    trust,
                    trust_proof,
                },
            );
        }
    }
    Ok(State {
        schema_version: version,
        repository: absolute(data.get("repository"), "installation state.repository")?,
        marketplace: identifier(data.get("marketplace"), "installation state.marketplace")?,
        profile: identifier(data.get("profile"), "installation state.profile")?,
        target: identifier(data.get("target"), "installation state.target")?,
        destination_root: absolute(
            data.get("destinationRoot"),
            "installation state.destinationRoot",
        )?,
        entries,
        destination_roots: roots,
        slots,
    })
}
