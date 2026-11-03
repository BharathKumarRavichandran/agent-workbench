//! Pure D4 logical document planning. Not an authorization or mutation boundary.
#![cfg_attr(not(test), allow(dead_code))]
use crate::{
    state::{
        wire::{self, Wire},
        Slot,
    },
    Result,
};
use serde::Serialize;
use serde_json::json;
mod json;
pub(crate) mod ownership;
pub(crate) mod references;
pub(crate) mod snapshot;
use std::collections::BTreeMap;

pub(crate) struct Desired {
    pub state: Slot,
    pub value: Wire,
    pub granted: bool,
}
#[derive(Serialize)]
pub(crate) struct Plan {
    pub removed: Option<Vec<u8>>,
    pub after: Option<Vec<u8>>,
    pub operations: Vec<SlotOperation>,
    pub slots: BTreeMap<String, Slot>,
}

pub(crate) fn parse(text: &str) -> Result<Wire> {
    let value = wire::parse(text)?;
    value.object("shared JSON document")?;
    Ok(value)
}
/// Decode JSON's byte encodings without changing the snapshot's original bytes.
pub(crate) fn parse_bytes(content: &[u8]) -> Result<Wire> {
    let (content, width, little) = if let Some(rest) = content.strip_prefix(b"\xff\xfe\x00\x00") {
        (rest, 4, true)
    } else if let Some(rest) = content.strip_prefix(b"\x00\x00\xfe\xff") {
        (rest, 4, false)
    } else if let Some(rest) = content.strip_prefix(b"\xff\xfe") {
        (rest, 2, true)
    } else if let Some(rest) = content.strip_prefix(b"\xfe\xff") {
        (rest, 2, false)
    } else if let Some(rest) = content.strip_prefix(b"\xef\xbb\xbf") {
        (rest, 1, false)
    } else if content.len() >= 4 && content[0] == 0 {
        (content, if content[1] == 0 { 4 } else { 2 }, false)
    } else if content.len() >= 4 && content[1] == 0 {
        (
            content,
            if content[2] == 0 && content[3] == 0 {
                4
            } else {
                2
            },
            true,
        )
    } else if content.len() == 2 && (content[0] == 0 || content[1] == 0) {
        (content, 2, content[1] == 0)
    } else {
        (content, 1, false)
    };
    let invalid = || "invalid JSON byte encoding".to_owned();
    if width == 1 {
        return parse(std::str::from_utf8(content).map_err(|_| invalid())?);
    }
    if content.len() % width != 0 {
        return Err(invalid());
    }
    let text = if width == 2 {
        let words = content
            .as_chunks::<2>()
            .0
            .iter()
            .map(|b| {
                if little {
                    u16::from_le_bytes([b[0], b[1]])
                } else {
                    u16::from_be_bytes([b[0], b[1]])
                }
            })
            .collect::<Vec<_>>();
        String::from_utf16(&words).map_err(|_| invalid())?
    } else {
        content
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| {
                let b = [b[0], b[1], b[2], b[3]];
                char::from_u32(if little {
                    u32::from_le_bytes(b)
                } else {
                    u32::from_be_bytes(b)
                })
                .ok_or_else(invalid)
            })
            .collect::<Result<String>>()?
    };
    parse(&text)
}
pub(crate) fn encode(value: &Wire) -> Result<String> {
    json::pretty(value)
}
#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SlotOperation {
    pub kind: String,
    pub key: String,
    pub component: String,
    pub resource_kind: String,
    pub document: String,
    pub destination: String,
    pub slot: String,
    pub reason: String,
    pub diagnostic: Option<String>,
    pub trust_token: Option<String>,
    pub trust_required: bool,
}
fn operation(
    kind: &str,
    item: &Slot,
    path: &str,
    reason: &str,
    diagnostic: Option<&str>,
    desired: bool,
    granted: bool,
) -> SlotOperation {
    SlotOperation {
        kind: kind.into(),
        key: item.key.clone(),
        component: item.component.clone(),
        resource_kind: "shared-json-slot".into(),
        document: path.into(),
        destination: path.into(),
        slot: item.slot.clone(),
        reason: reason.into(),
        diagnostic: diagnostic.map(str::to_owned),
        trust_token: desired.then(|| item.trust.clone()),
        trust_required: desired && !granted,
    }
}
pub(crate) fn digest(value: &Wire) -> Result<String> {
    use sha2::{Digest, Sha256};
    Ok(format!("{:x}", Sha256::digest(json::canonical(value)?)))
}
fn python_equal(left: &Wire, right: &Wire) -> bool {
    use serde_json::Value;
    enum Numeric {
        Integer(String),
        Float(f64),
    }
    fn numeric(value: &Value) -> Option<Numeric> {
        match value {
            Value::Bool(value) => Some(Numeric::Integer(if *value { "1" } else { "0" }.into())),
            Value::Number(value) if !value.is_f64() => Some(Numeric::Integer(value.to_string())),
            Value::Number(value) => value.as_f64().map(Numeric::Float),
            _ => None,
        }
    }
    fn equals_float(integer: &str, float: f64) -> bool {
        // Fixed zero precision formats the exact integral binary64 value, not
        // its shortest round-trip decimal (which can denote a different int).
        float.is_finite()
            && float.fract() == 0.0
            && integer
                == if float == 0.0 {
                    "0".into()
                } else {
                    format!("{float:.0}")
                }
    }
    match (left, right) {
        (Wire::Object(a), Wire::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(key, value)| {
                    b.iter()
                        .any(|(other_key, other)| key == other_key && python_equal(value, other))
                })
        }
        (Wire::Array(a), Wire::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| python_equal(a, b))
        }
        (Wire::Scalar(a), Wire::Scalar(b)) => match (numeric(a), numeric(b)) {
            (Some(Numeric::Integer(a)), Some(Numeric::Integer(b))) => a == b,
            (Some(Numeric::Integer(i)), Some(Numeric::Float(f)))
            | (Some(Numeric::Float(f)), Some(Numeric::Integer(i))) => equals_float(&i, f),
            (Some(Numeric::Float(a)), Some(Numeric::Float(b))) => a == b,
            _ => a == b,
        },
        _ => false,
    }
}
fn changed_bytes(data: &Wire, baseline: &Wire, content: Option<&[u8]>) -> Result<Option<Vec<u8>>> {
    if python_equal(data, baseline) {
        Ok(content.map(<[u8]>::to_vec))
    } else {
        encode(data).map(|text| Some(text.into_bytes()))
    }
}
fn validate_claude(slots: &Wire) -> Result<()> {
    let mut cursor = false;
    for (event, value) in slots.object("hooks")? {
        if event.is_empty() {
            return Err("hook events must map to arrays".into());
        }
        let Wire::Array(entries) = value else {
            return Err("hook events must map to arrays".into());
        };
        for entry in entries {
            if entry.has("hooks") && !entry.has("command") {
                let Wire::Array(handlers) = entry.get("hooks") else {
                    return Err(
                        "Claude matcher groups require a hooks array and optional string matcher"
                            .into(),
                    );
                };
                if entry.has("matcher") && entry.get("matcher").string().is_none() {
                    return Err(
                        "Claude matcher groups require a hooks array and optional string matcher"
                            .into(),
                    );
                }
                for handler in handlers {
                    let Some(kind) = handler.get("type").string() else {
                        return Err("Claude handlers require type".into());
                    };
                    if kind == "command"
                        && handler.get("command").string().is_none_or(str::is_empty)
                    {
                        return Err("Claude command handler requires command".into());
                    }
                }
            } else if entry.has("command") && !entry.has("hooks") {
                if entry.get("command").string().is_none_or(str::is_empty) {
                    return Err("Cursor hook requires command".into());
                }
                cursor = true;
            } else {
                return Err("ambiguous hook entry representation".into());
            }
        }
    }
    if cursor {
        return Err("mixed or incompatible hook representations".into());
    }
    Ok(())
}
fn event(item: &Slot) -> &str {
    item.slot.rsplit_once(':').map_or("", |(event, _)| event)
}
fn matches(slots: &Wire, item: &Slot, hooks: bool) -> Result<(bool, bool, bool)> {
    if !hooks {
        let exists = slots.has(&item.slot);
        return Ok((
            exists,
            exists && digest(slots.get(&item.slot))? == item.digest,
            false,
        ));
    }
    let mut count = 0;
    if let Wire::Array(entries) = slots.get(event(item)) {
        for entry in entries {
            if digest(entry)? == item.digest {
                count += 1;
            }
        }
    }
    Ok((count > 0, count == 1, count > 1))
}
fn remove_slot(slots: &mut Wire, item: &Slot, hooks: bool) -> Result<()> {
    if !hooks {
        slots.remove(&item.slot);
    } else if let Some(Wire::Array(entries)) = slots.get_mut(event(item)) {
        let mut retained = Vec::new();
        for entry in entries.drain(..) {
            if digest(&entry)? != item.digest {
                retained.push(entry);
            }
        }
        *entries = retained;
    }
    Ok(())
}
pub(crate) fn plan_document(
    content: Option<&[u8]>,
    path: &str,
    component: &str,
    desired: &[Desired],
    owned: &BTreeMap<String, Slot>,
    adopt: bool,
    target: &str,
) -> Result<Plan> {
    let original = content
        .map(parse_bytes)
        .transpose()?
        .unwrap_or(Wire::Object(vec![]));
    if desired.is_empty() && owned.is_empty() {
        return Ok(Plan {
            removed: content.map(<[u8]>::to_vec),
            after: content.map(<[u8]>::to_vec),
            operations: vec![],
            slots: BTreeMap::new(),
        });
    }
    let mut data = original.clone();
    let hooks = component == "hooks";
    let field = if hooks { "hooks" } else { "mcpServers" };
    if data.has(field) && !matches!(data.get(field), Wire::Object(_)) {
        return Err(format!("{field} must be an object: {path}"));
    }
    if hooks
        && target == "cursor"
        && data.has("version")
        && data.get("version").value().as_u64() != Some(1)
    {
        return Err("shared hooks document requires version 1".into());
    }
    if !data.has(field) {
        data.set(field, Wire::Object(vec![]));
    }
    for (_, value) in data.get(field).object(field)? {
        if hooks {
            let Wire::Array(entries) = value else {
                return Err("hook events must contain object arrays".into());
            };
            if entries.iter().any(|item| !matches!(item, Wire::Object(_))) {
                return Err("hook events must contain object arrays".into());
            }
        } else if !matches!(value, Wire::Object(_)) {
            return Err("MCP servers must be objects".into());
        }
    }
    if hooks && target == "claude" {
        validate_claude(data.get(field))?;
    }
    let baseline = data.clone();
    let mut operations = vec![];
    let mut next_slots = BTreeMap::new();
    let slots = data.get_mut(field).unwrap();
    let wanted: BTreeMap<_, _> = desired.iter().map(|d| (d.state.key.as_str(), d)).collect();
    for (key, old) in owned {
        if !matches(slots, old, hooks)?.1 {
            operations.push(operation(
                "conflict",
                old,
                path,
                "owned slot was externally modified or is ambiguous",
                Some("OWNED_SLOT_MODIFIED"),
                false,
                false,
            ));
            continue;
        }
        if wanted.get(key.as_str()).is_some_and(|d| d.state == *old) {
            continue;
        }
        remove_slot(slots, old, hooks)?;
        if !wanted.contains_key(key.as_str()) {
            operations.push(operation(
                "remove-owned",
                old,
                path,
                "remove unchanged owned slot",
                None,
                false,
                false,
            ));
        }
    }
    let removed = changed_bytes(&data, &baseline, content)?;
    let slots = data.get_mut(field).unwrap();
    let mut seen = std::collections::BTreeSet::new();
    for wanted in desired {
        let item = &wanted.state;
        let collision = !seen.insert(&item.slot);
        let (exists, matching, ambiguous) = matches(slots, item, hooks)?;
        let (mut kind, mut reason, mut diagnostic) =
            ("create", "add shared document slot".to_owned(), None);
        if collision || ambiguous {
            kind = "conflict";
            reason = "duplicate or ambiguous slot".into();
            diagnostic = Some("SLOT_AMBIGUOUS");
        } else if exists && !matching {
            kind = "conflict";
            reason = "slot contains foreign content".into();
            diagnostic = Some("SLOT_COLLISION");
        } else if owned.get(&item.key) == Some(item) {
            kind = "keep";
            reason = "unchanged owned slot".into();
        } else if matching {
            kind = if adopt { "adopt" } else { "conflict" };
            reason = if adopt {
                "adopt matching unmanaged slot"
            } else {
                "matching unmanaged slot requires --adopt"
            }
            .into();
        }
        if !wanted.granted {
            kind = "conflict";
            reason = format!("content-bound grant required; pass --trust {}", item.trust);
            diagnostic = Some("TRUST_REQUIRED");
        }
        operations.push(operation(
            kind,
            item,
            path,
            &reason,
            diagnostic,
            true,
            wanted.granted,
        ));
        next_slots.insert(item.key.clone(), item.clone());
        if !exists && kind != "conflict" {
            if !hooks {
                slots.set(&item.slot, wanted.value.clone());
            } else {
                if !slots.has(event(item)) {
                    slots.set(event(item), Wire::Array(vec![]));
                }
                if let Some(Wire::Array(entries)) = slots.get_mut(event(item)) {
                    entries.push(wanted.value.clone());
                }
            }
        }
    }
    if hooks && target == "cursor" && !desired.is_empty() && !data.has("version") {
        data.set("version", Wire::Scalar(json!(1)));
    }
    Ok(Plan {
        removed,
        after: changed_bytes(&data, &original, content)?,
        operations,
        slots: next_slots,
    })
}
