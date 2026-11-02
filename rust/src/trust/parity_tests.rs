//! Private dependency-bound driver: no CLI flags or authority creation.
use super::*;
use serde::Deserialize;
use std::{fs, path::PathBuf};

#[derive(Deserialize)]
struct Case {
    plugin: Plugin,
    source: Source,
    component: ComponentKind,
    target: String,
    repository: PathBuf,
    document: PathBuf,
    digest: String,
    fallback: Option<PathBuf>,
    r#override: Option<PathBuf>,
}

#[test]
#[ignore]
fn differential_inspection() {
    let base = PathBuf::from(std::env::var_os("AW_TRUST_BASE").unwrap());
    let rows: Vec<String> =
        serde_json::from_slice(&fs::read(base.join("inspection.json")).unwrap()).unwrap();
    // These isolated fixtures are validated regular JSON documents. Production
    // snapshot validation is D4; do not expose this fixture reader in the CLI.
    let results: Vec<_> = rows
        .iter()
        .map(|path| {
            let snapshot = match fs::read(path) {
                Ok(bytes) => Some(bytes),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
                Err(error) => panic!("fixture read failed: {error}"),
            };
            check_inspection(snapshot.as_deref()).err()
        })
        .collect();
    fs::write(
        base.join("inspection-results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore]
fn differential_proofs() {
    let base = PathBuf::from(std::env::var_os("AW_TRUST_BASE").unwrap());
    let rows: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(base.join("proofs.json")).unwrap()).unwrap();
    let results: Vec<_> = rows.iter().map(|row| {
        let mut prior = crate::state::load(Path::new(row["prior"].as_str().unwrap())).unwrap().unwrap();
        // Mutate after strict decoding so individual field changes remain
        // independent even when they would also violate state-shape invariants.
        if let Some(field) = row["mutation"]["field"].as_str() {
            let value = row["mutation"]["value"].as_str().unwrap().to_owned();
            if let Some(entry) = prior.entries.values_mut().next() {
                use crate::resource::{ContentDigest, PathResource};
                match field {
                    "key" => entry.key = value,
                    "plugin" => entry.plugin = value,
                    "source" => entry.source = value,
                    "destination" => entry.destination = value.into(),
                    "sourceRepository" => entry.source_repository = value.into(),
                    "component" => entry.component = ComponentKind::Mcp,
                    "resourceKind" => entry.resource = PathResource::FileCopy(ContentDigest::parse(&"b".repeat(64)).unwrap()),
                    "digest" => entry.resource = PathResource::DirectoryCopy(ContentDigest::parse(&value).unwrap()),
                    "trust" => entry.trust = Some(value),
                    "trustProof" => entry.trust_proof = Some(value),
                    _ => panic!("unknown entry field"),
                }
            } else {
                let slot = prior.slots.values_mut().next().unwrap();
                match field {
                    "key" => slot.key = value,
                    "plugin" => slot.plugin = value,
                    "component" => slot.component = value,
                    "document" => slot.document = value,
                    "slot" => slot.slot = value,
                    "digest" => slot.digest = value,
                    "trust" => slot.trust = value,
                    "trustProof" => slot.trust_proof = Some(value),
                    _ => panic!("unknown slot field"),
                }
            }
        }
        let candidate = crate::state::load(Path::new(row["candidate"].as_str().unwrap())).unwrap().unwrap();
        let file = Path::new(row["file"].as_str().unwrap());
        let explicit: Vec<String> = serde_json::from_value(row["explicit"].clone()).unwrap();
        if let Some(record) = candidate.entries.values().next() {
            let old = prior.entries.values().next();
            json!({"proof": record_proof(record, file).ok(), "authenticated": authenticated(old, file),
                "reused": reuse_proof(record.clone(), old, file).value().unwrap(),
                "granted": entry_granted(record.trust.as_deref().unwrap(), &explicit, old, file)})
        } else {
            let record = candidate.slots.values().next().unwrap();
            let old = prior.slots.values().next();
            let component = match record.component.as_str() { "hooks" => ComponentKind::Hooks, "mcp" => ComponentKind::Mcp, _ => panic!() };
            json!({"proof": record_proof(record, file).ok(), "authenticated": authenticated(old, file),
                "reused": reuse_proof(record.clone(), old, file).value().unwrap(),
                "granted": slots_granted(&record.trust, &record.plugin, component, &explicit, prior.slots.values(), prior.entries.values().next(), file)})
        }
    }).collect();
    fs::write(
        base.join("proof-results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore]
fn differential_trust() {
    let base = PathBuf::from(std::env::var_os("AW_TRUST_BASE").unwrap());
    let cases: Vec<Case> =
        serde_json::from_slice(&fs::read(base.join("tokens.json")).unwrap()).unwrap();
    let tokens: Vec<String> = cases
        .iter()
        .map(|case| {
            Grant {
                plugin: &case.plugin,
                source: &case.source,
                component: case.component,
                target: &case.target,
                repository: &case.repository,
                document: &case.document,
                digest: &case.digest,
                artifact: case.r#override.as_deref(),
                fallback: case.fallback.as_deref(),
            }
            .token()
            .unwrap()
        })
        .collect();
    fs::write(
        base.join("results.json"),
        serde_json::to_vec(&tokens).unwrap(),
    )
    .unwrap();
}
