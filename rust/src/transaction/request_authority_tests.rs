//! Caller-request authority is independent from authenticated journal authority.
//! These are private-plan defense tests, not independent public target planning.
use serde_json::{json, Value};
use std::{fs, os::unix::fs::MetadataExt};

#[test]
fn invalid_caller_requests_reject_before_creating_any_transaction_resource() {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path();
    let foreign = base.join("foreign.json");
    fs::write(&foreign, b"foreign caller-authority sentinel").unwrap();
    let metadata = fs::symlink_metadata(&foreign).unwrap();
    let identity = (metadata.dev(), metadata.ino(), metadata.mode());
    let hooks = base.join("project/.cursor/.agent-workbench-hooks");
    let config = hooks.parent().unwrap();
    let baseline = json!({
        "target":"cursor", "repository":base.join("repository"),
        "roots":{"hooks":hooks, "mcp":config, "skills":config.join("skills")},
        "state_before":{"path":base.join("state/profile.json"),"content":null,"revision":null},
        "state_after":[],"conflicts":false,"uninstall":false,
        "root_snapshots":[],"documents":[],"sources":[],"operations":[]
    });
    let operation = json!({"kind":"create","path":hooks.join("sample"),
        "component":"hooks","resource":"directory-copy","source":null,
        "digest":null,"expected_digest":null,"expected_link":null});
    let document = json!({"before":{"path":config.join("hooks.json"),"content":null,"revision":null},
        "removed":null,"after":null});
    let mut cases: Vec<(&str, Value, &str)> = Vec::new();
    let mut row = baseline.clone();
    row["target"] = json!("hermes");
    cases.push(("unsupported-target", row, "unsupported recovery target"));
    let mut row = baseline.clone();
    row["roots"]["foreign"] = json!(base.join("outside"));
    cases.push(("unknown-root-component", row, "invalid recovery component"));
    let mut row = baseline.clone();
    row["state_before"]["path"] = json!("relative-state.json");
    cases.push((
        "relative-state",
        row,
        "recovery request paths must be absolute",
    ));
    let mut row = baseline.clone();
    row["roots"]["hooks"] = json!("relative-hooks");
    cases.push((
        "relative-root",
        row,
        "recovery request paths must be absolute",
    ));
    let mut row = baseline.clone();
    row["roots"]["hooks"] = json!(base.join("project/../outside"));
    cases.push((
        "parent-traversal-root",
        row,
        "recovery request paths must be absolute",
    ));
    let mut row = baseline.clone();
    row["roots"].as_object_mut().unwrap().remove("hooks");
    cases.push(("missing-hooks-root", row, "missing recovery hooks root"));
    let mut bad_document = document.clone();
    bad_document["before"]["path"] = json!(foreign);
    let mut row = baseline.clone();
    row["documents"] = json!([bad_document]);
    cases.push((
        "outside-document",
        row,
        "unauthorized or duplicated apply document",
    ));
    let mut row = baseline.clone();
    row["documents"] = json!([document.clone(), document]);
    cases.push((
        "duplicate-document",
        row,
        "unauthorized or duplicated apply document",
    ));
    let mut bad_operation = operation.clone();
    bad_operation["path"] = json!(foreign);
    let mut row = baseline.clone();
    row["operations"] = json!([bad_operation]);
    cases.push(("outside-resource", row, "unauthorized apply destination"));
    let mut row = baseline.clone();
    row["operations"] = json!([operation.clone(), operation.clone()]);
    cases.push(("duplicate-resource", row, "unauthorized apply destination"));
    let mut bad_operation = operation.clone();
    bad_operation["component"] = json!("skills");
    let mut row = baseline.clone();
    row["operations"] = json!([bad_operation]);
    cases.push((
        "resource-kind-disagrees",
        row,
        "unsupported apply resource operation",
    ));
    let mut bad_operation = operation.clone();
    bad_operation["component"] = json!("mcp");
    let mut row = baseline.clone();
    row["operations"] = json!([bad_operation]);
    cases.push((
        "mcp-is-not-path-resource",
        row,
        "unsupported apply resource operation",
    ));
    let mut bad_operation = operation.clone();
    bad_operation["kind"] = json!("overwrite-foreign");
    let mut row = baseline.clone();
    row["operations"] = json!([bad_operation]);
    cases.push((
        "unsupported-operation",
        row,
        "unsupported apply resource operation",
    ));
    let mut bad_operation = operation;
    bad_operation["path"] = json!(hooks.join(".foreign"));
    let mut row = baseline;
    row["operations"] = json!([bad_operation]);
    cases.push(("hidden-destination", row, "unauthorized apply destination"));
    for (name, row, diagnostic) in &cases {
        let request = serde_json::from_value(row.clone()).unwrap();
        let error = super::apply::apply(&request, |_| panic!("{name}: mutation seam reached"))
            .expect_err(name);
        assert!(
            matches!(&error, crate::shared_document::snapshot::Error::Invalid(message) if message.contains(diagnostic)),
            "{name}: {error:?}"
        );
        let info = fs::symlink_metadata(&foreign).unwrap();
        assert_eq!(
            identity,
            (info.dev(), info.ino(), info.mode()),
            "{name}: changed foreign identity"
        );
        assert_eq!(
            fs::read(&foreign).unwrap(),
            b"foreign caller-authority sentinel"
        );
        assert_eq!(
            fs::read_dir(base).unwrap().count(),
            1,
            "{name}: created transaction resources"
        );
    }
    println!(
        "caller authority: {} private real-apply rejection rows passed without mutation",
        cases.len()
    );
}
