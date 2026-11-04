//! E1 private driver: caller supplies artifact paths, never oracle inventories.
use std::{env, fs, path::PathBuf};

#[test]
#[ignore]
fn differential_inspection() {
    let base = PathBuf::from(env::var_os("AW_ARTIFACT_BASE").unwrap());
    let input: serde_json::Value =
        serde_json::from_slice(&fs::read(base.join("input.json")).unwrap()).unwrap();
    let repository = PathBuf::from(input["repository"].as_str().unwrap());
    let mut rows = Vec::new();
    for artifact in input["artifacts"].as_array().unwrap() {
        let kind = artifact["kind"].as_str().unwrap();
        let path = PathBuf::from(artifact["path"].as_str().unwrap());
        rows.push(match crate::artifacts::inspect(&repository, kind, &path) {
            Ok(inventory) => serde_json::json!({"inventory": inventory}),
            Err(error) => serde_json::json!({"error": error}),
        });
    }
    fs::write(base.join("result.json"), serde_json::to_vec(&rows).unwrap()).unwrap();
    let inspection = crate::artifacts::inspect_catalog(&repository).unwrap();
    let requests = input["projections"].as_array().unwrap();
    let mut projections = Vec::new();
    for request in requests {
        projections.push(crate::capabilities::project_request(&inspection, request).unwrap());
    }
    fs::write(
        base.join("projection-result.json"),
        serde_json::to_vec(&projections).unwrap(),
    )
    .unwrap();
    fs::write(
        base.join("catalog-result.json"),
        serde_json::to_vec(&inspection).unwrap(),
    )
    .unwrap();
}
