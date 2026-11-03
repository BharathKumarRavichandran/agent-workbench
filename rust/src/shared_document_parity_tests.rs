//! Private differential driver: no production CLI entry point.
use serde_json::{json, Value};
use std::{env, fs, path::PathBuf};

#[test]
#[ignore]
fn differential_shared_document() {
    fn byte_plans(mut value: Value) -> Value {
        for name in ["removed", "after"] {
            if let Some(text) = value[name].as_str() {
                value[name] = json!(text.as_bytes());
            }
        }
        value
    }
    let base = PathBuf::from(env::var_os("AW_SHARED_BASE").unwrap());
    let wire =
        crate::state::wire::parse(&fs::read_to_string(base.join("rows.json")).unwrap()).unwrap();
    let crate::state::wire::Wire::Array(rows) = wire else {
        panic!("rows")
    };
    let results: Vec<Value> = rows.iter().map(|row| {
        use crate::{shared_document as shared, state::wire::Wire};
        if row.get("mode").string() == Some("encode") {
            return json!({"encoded": shared::encode(row.get("value")).unwrap(), "digest": shared::digest(row.get("value")).unwrap()});
        }
        if row.get("mode").string() == Some("reference") {
            let content = if row.has("content_bytes") {
                Some(serde_json::from_value::<Vec<u8>>(row.get("content_bytes").value()).unwrap())
            } else { row.get("content").string().map(|text| text.as_bytes().to_vec()) };
            return match shared::references::references_bundle(content.as_deref(), std::path::Path::new(row.get("bundle").string().unwrap())) { Ok(value) => json!(value), Err(error) => json!({"error":error}) };
        }
        if row.get("mode").string() == Some("snapshot") {
            use shared::snapshot::{self, Error};
            let path = std::path::Path::new(row.get("path").string().unwrap());
            let result = if row.get("scenario").string() == Some("during-read") {
                let parent = crate::bundles::open_directory(path.parent().unwrap()).unwrap();
                snapshot::snapshot_at(path, &parent, || { fs::write(path, "{\"changed\":true}").unwrap(); })
            } else { snapshot::snapshot(path) };
            let result = result.and_then(|value| {
                if row.get("ledger").value() == json!(true) { snapshot::validate_ledger(value.content.as_deref()).map_err(Error::Invalid)?; }
                Ok(value)
            });
            return match result {
                Ok(value) => json!({"accepted":true,"content":value.content,"revision":value.revision.is_some()}),
                Err(Error::Io(error)) => { let _ = error.kind(); json!({"accepted":false,"error_class":"OSError"}) },
                Err(Error::Invalid(message)) => { if row.get("scenario").string() == Some("during-read") { assert_eq!(message, "shared document changed while reading"); } json!({"accepted":false,"error_class":"ValueError"}) }
            };
        }
        if row.get("mode").string() == Some("ownership") {
            let plan = shared::ownership::plan(row.get("content").string().map(str::as_bytes), row.get("file").string().unwrap(), &serde_json::from_value::<Vec<crate::state::Slot>>(row.get("previous").value()).unwrap(), &serde_json::from_value::<Vec<crate::state::Slot>>(row.get("desired").value()).unwrap(), serde_json::from_value(row.get("operations").value()).unwrap()).unwrap();
            let operations_json = serde_json::to_string(&plan.operations).unwrap();
            let conflicts_json = serde_json::to_string(&plan.conflicts).unwrap();
            let mut value = serde_json::to_value(plan).unwrap();
            value["operations_json"] = json!(operations_json);
            value["conflicts_json"] = json!(conflicts_json);
            return byte_plans(value);
        }
        let Wire::Array(items) = row.get("desired") else { panic!("desired") };
        let desired = items.iter().map(|item| shared::Desired {
            state: serde_json::from_value(item.get("state").value()).unwrap(),
            value: item.get("value").clone(), granted: item.get("granted").value() == json!(true),
        }).collect::<Vec<_>>();
        let owned = serde_json::from_value(row.get("owned").value()).unwrap();
        let content = if row.has("content_bytes") {
            Some(serde_json::from_value::<Vec<u8>>(row.get("content_bytes").value()).unwrap())
        } else { row.get("content").string().map(|text| text.as_bytes().to_vec()) };
        match shared::plan_document(content.as_deref(), row.get("path").string().unwrap(), row.get("component").string().unwrap(), &desired, &owned, row.get("adopt").value() == json!(true), row.get("target").string().unwrap()) {
            Ok(plan) => {
                let operations_json = serde_json::to_string(&plan.operations).unwrap();
                let mut value = serde_json::to_value(plan).unwrap();
                value["operations_json"] = json!(operations_json);
                byte_plans(value)
            }, Err(error) => json!({"error":error})
        }
    }).collect();
    fs::write(
        base.join("results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}
