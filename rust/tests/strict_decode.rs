use agent_workbench::{decode_catalog, decode_profile};
use std::fs;
use tempfile::TempDir;

fn write(temp: &TempDir, name: &str, text: &str) -> std::path::PathBuf {
    let path = temp.path().join(name);
    fs::write(&path, text).unwrap();
    path
}

fn catalog(extra: &str) -> String {
    format!(
        r#"{{
          "schemaVersion": 2,
          "marketplace": {{"id":"market","name":"Market","owner":{{"name":"Owner"}},"description":"Description"}},
          "sources": [{{"id":"local","kind":"local","path":"plugins"}}],
          "plugins": [{{"id":"plugin","source":"local","artifacts":{{"agent-skills":{{"path":"skills"}}}}}}]
          {extra}
        }}"#
    )
}

fn profile(version: u64, target_extra: &str) -> String {
    format!(
        r#"{{
          "schemaVersion": {version},
          "id": "profile",
          "plugins": {{"include":["plugin"],"exclude":[]}},
          "targets": [{{"id":"hermes"{target_extra}}}]
        }}"#
    )
}

#[test]
fn catalog_accepts_v2_and_rejects_unknown_fields() {
    let temp = TempDir::new().unwrap();
    decode_catalog(&write(&temp, "valid.json", &catalog(""))).unwrap();
    let error = decode_catalog(&write(
        &temp,
        "unknown.json",
        &catalog(",\"surprise\":true"),
    ))
    .unwrap_err();
    assert!(error.contains("unknown field"), "{error}");
}

#[test]
fn catalog_rejects_duplicate_dynamic_artifact_keys() {
    let temp = TempDir::new().unwrap();
    let duplicate = catalog("").replace(
        r#""agent-skills":{"path":"skills"}"#,
        r#""agent-skills":{"path":"skills"},"agent-skills":{"path":"other"}"#,
    );
    let error = decode_catalog(&write(&temp, "duplicate.json", &duplicate)).unwrap_err();
    assert!(
        error.contains("duplicate JSON key \"agent-skills\""),
        "{error}"
    );
}

#[test]
fn catalog_rejects_wrong_version_unsafe_paths_and_explicit_nulls() {
    let temp = TempDir::new().unwrap();
    let version = catalog("").replace("\"schemaVersion\": 2", "\"schemaVersion\": 3");
    assert!(decode_catalog(&write(&temp, "version.json", &version)).is_err());
    let path = catalog("").replace("\"path\":\"plugins\"", "\"path\":\"../plugins\"");
    assert!(decode_catalog(&write(&temp, "path.json", &path)).is_err());
    let explicit_null = catalog("").replace(
        "\"kind\":\"local\",\"path\":\"plugins\"",
        "\"kind\":\"local\",\"path\":\"plugins\",\"upstream\":null",
    );
    assert!(decode_catalog(&write(&temp, "null.json", &explicit_null)).is_err());
    let null_provenance = catalog("").replace(
        "\"artifacts\":{\"agent-skills\":{\"path\":\"skills\"}}",
        "\"artifacts\":{\"agent-skills\":{\"path\":\"skills\"}},\"provenance\":null",
    );
    assert!(decode_catalog(&write(&temp, "null-provenance.json", &null_provenance)).is_err());
}

#[test]
fn profile_accepts_v1_and_v2_and_enforces_versioned_shape() {
    let temp = TempDir::new().unwrap();
    decode_profile(&write(&temp, "v1.json", &profile(1, ""))).unwrap();
    decode_profile(&write(
        &temp,
        "v2.json",
        &profile(2, r#", "components":["skills"]"#),
    ))
    .unwrap();
    let error = decode_profile(&write(
        &temp,
        "v1-extra.json",
        &profile(1, r#", "components":["skills"]"#),
    ))
    .unwrap_err();
    assert!(error.contains("v1 target"), "{error}");
}

#[test]
fn profile_rejects_duplicates_unknown_fields_and_versions() {
    let temp = TempDir::new().unwrap();
    let duplicate = profile(2, r#", "components":["skills","skills"]"#);
    assert!(decode_profile(&write(&temp, "duplicate.json", &duplicate)).is_err());
    let unknown = profile(2, r#", "unknown":true"#);
    assert!(decode_profile(&write(&temp, "unknown.json", &unknown)).is_err());
    let explicit_null = profile(2, r#", "destination":null"#);
    assert!(decode_profile(&write(&temp, "null.json", &explicit_null)).is_err());
    assert!(decode_profile(&write(&temp, "version.json", &profile(3, ""))).is_err());
}
