//! Ordered ledger planning, separate from persisted authority and D5 publication.
use super::*;
fn identity(item: &Slot) -> Result<String> {
    digest(&wire::parse(
        &json!([item.document, item.component, item.slot]).to_string(),
    )?)
}
fn record(item: &Slot, file: &str) -> Wire {
    Wire::Object(vec![
        ("owner".into(), Wire::Scalar(json!(file))),
        ("digest".into(), Wire::Scalar(json!(item.digest))),
    ])
}
pub(crate) fn conflicts(
    content: Option<&[u8]>,
    file: &str,
    previous: &[Slot],
) -> Result<Vec<SlotOperation>> {
    let data = content
        .map(parse_bytes)
        .transpose()?
        .unwrap_or(Wire::Object(vec![]));
    let mut result = vec![];
    for item in previous {
        if data.get("slots").get(&identity(item)?).value() != record(item, file).value() {
            result.push(operation(
                "conflict",
                item,
                &item.document,
                "prior slot lacks matching resource ownership authority",
                Some("SLOT_OWNERSHIP_MISMATCH"),
                false,
                false,
            ));
        }
    }
    Ok(result)
}
#[derive(Serialize)]
pub(crate) struct OwnershipPlan {
    pub removed: Option<Vec<u8>>,
    pub after: Option<Vec<u8>>,
    pub operations: Vec<SlotOperation>,
    pub conflicts: Vec<SlotOperation>,
}
pub(crate) fn plan(
    content: Option<&[u8]>,
    file: &str,
    previous: &[Slot],
    desired: &[Slot],
    mut operations: Vec<SlotOperation>,
) -> Result<OwnershipPlan> {
    let original = content
        .map(parse_bytes)
        .transpose()?
        .unwrap_or(Wire::Object(vec![
            ("version".into(), Wire::Scalar(json!(1))),
            ("slots".into(), Wire::Object(vec![])),
        ]));
    let conflicts = conflicts(content, file, previous)?;
    if !conflicts.is_empty() {
        return Ok(OwnershipPlan {
            removed: content.map(<[u8]>::to_vec),
            after: content.map(<[u8]>::to_vec),
            operations,
            conflicts,
        });
    }
    let mut data = original.clone();
    let slots = data
        .get_mut("slots")
        .ok_or("invalid target ownership ledger")?;
    for item in previous {
        let key = identity(item)?;
        if slots.get(&key).get("owner").string() == Some(file) {
            slots.remove(&key);
        }
    }
    let mut rejected = std::collections::BTreeSet::new();
    for item in desired {
        let key = identity(item)?;
        if slots.has(&key) && slots.get(&key).get("owner").string() != Some(file) {
            rejected.insert(&item.key);
        } else {
            slots.set(&key, record(item, file));
        }
    }
    for operation in &mut operations {
        if rejected.iter().any(|item| item.as_str() == operation.key) {
            operation.kind = "conflict".into();
            operation.reason = "slot is owned by another profile".into();
            operation.diagnostic = Some("SLOT_OWNED".into());
        }
    }
    Ok(OwnershipPlan {
        removed: content.map(<[u8]>::to_vec),
        after: changed_bytes(&data, &original, content)?,
        operations,
        conflicts,
    })
}
