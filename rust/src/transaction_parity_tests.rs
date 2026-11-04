//! Isolated mutation driver; deliberately unavailable to the CLI.
use serde_json::{json, Value};
use std::{env, fs, path::PathBuf};

fn outcome(result: Result<(), crate::shared_document::snapshot::Error>) -> Value {
    use crate::shared_document::snapshot::Error;
    match result {
        Ok(()) => json!({"accepted":true}),
        Err(Error::Invalid(_)) => json!({"accepted":false,"error_class":"ValueError"}),
        Err(Error::Io(error)) => {
            json!({"accepted":false,"error_class": if error.kind() == std::io::ErrorKind::AlreadyExists {"FileExistsError"} else {"OSError"}})
        }
    }
}
fn fault(scenario: &str, point: crate::transaction::Boundary) -> std::io::Result<()> {
    use crate::transaction::Boundary;
    if matches!(
        (scenario, point),
        ("key-write", Boundary::KeyWrite)
            | ("key-sync", Boundary::KeySync)
            | ("key-parent-sync", Boundary::KeyParentSync)
            | ("stage-write", Boundary::JournalWrite)
            | ("stage-sync", Boundary::JournalStageSync)
            | ("rename", Boundary::JournalRename)
            | ("parent-sync", Boundary::JournalParentSync)
    ) {
        Err(std::io::Error::other("injected persistence failure"))
    } else {
        Ok(())
    }
}

#[test]
#[ignore]
fn differential_publication() {
    let base = PathBuf::from(env::var_os("AW_TRANSACTION_BASE").unwrap());
    let rows: Vec<Value> =
        serde_json::from_slice(&fs::read(base.join("rows.json")).unwrap()).unwrap();
    let results: Vec<Value> = rows
        .iter()
        .map(|row| {
            use crate::{
                shared_document::snapshot::{self, Error},
                transaction::{self, Boundary},
            };
            if row["mode"] == "key" {
                let scenario = row["scenario"].as_str().unwrap();
                return outcome(
                    transaction::journal_key(
                        std::path::Path::new(row["file"].as_str().unwrap()),
                        true,
                        |point| {
                            if (scenario == "key-create" && format!("{point:?}") == "KeyCreate")
                                || (scenario == "key-chmod" && format!("{point:?}") == "KeyChmod")
                                || (scenario == "key-entropy"
                                    && format!("{point:?}") == "KeyEntropy")
                            {
                                return Err(std::io::Error::other(
                                    "injected exclusive create failure",
                                ));
                            }
                            fault(scenario, point)
                        },
                    )
                    .map(|_| ()),
                );
            }
            if row["mode"] == "write-journal" {
                let bytes: Vec<u8> = serde_json::from_value(row["record_json"].clone()).unwrap();
                let record = crate::shared_document::parse_bytes(&bytes).unwrap();
                let config = PathBuf::from(row["config"].as_str().unwrap());
                let parent = crate::bundles::open_directory(&config).unwrap();
                return outcome(transaction::write_journal(
                    record,
                    std::path::Path::new(row["file"].as_str().unwrap()),
                    &config.join(".agent-workbench-transaction.json"),
                    &parent,
                    |point| fault(row["scenario"].as_str().unwrap(), point),
                ));
            }
            if row["mode"] == "lock" {
                return outcome(transaction::document_lock(
                    std::path::Path::new(row["config"].as_str().unwrap()),
                    row["scenario"] != "missing-read",
                    row["lock"].as_str().unwrap(),
                    |_| {
                        if row["scenario"] == "root-swap" {
                            let config = PathBuf::from(row["config"].as_str().unwrap());
                            fs::rename(&config, config.with_file_name("detached"))?;
                            fs::create_dir(&config)?;
                            fs::write(config.join("foreign"), b"outside lock")?;
                        }
                        Ok(())
                    },
                ));
            }
            if row["mode"] == "recover" {
                let mut persistence_hit = false;
                let mut persistence_matches = 0_u64;
                let mut path_index = 0_u64;
                let mut selected_path = false;
                let roots = serde_json::from_value(row["roots"].clone()).unwrap();
                let result = transaction::recovery::RecoveryRequest::new(
                    PathBuf::from(row["file"].as_str().unwrap()),
                    row["target"].as_str().unwrap(),
                    roots,
                )
                .and_then(|request| {
                    use transaction::recovery::RecoveryBoundary as R;
                    let scenario = row["scenario"].as_str().unwrap();
                    let mut hit = false;
                    transaction::recovery::recover(&request, |point| {
                        if point == R::BeforePath {
                            selected_path = row["path_index"].as_u64() == Some(path_index);
                            path_index += 1;
                        }
                        if selected_path
                            && !persistence_hit
                            && row["fault_boundary"].as_str() == Some(format!("{point:?}").as_str())
                        {
                            persistence_matches += 1;
                            if persistence_matches != row["fault_ordinal"].as_u64().unwrap_or(1) {
                                return Ok(());
                            }
                            persistence_hit = true;
                            if let Some(path) = row["swap_parent"].as_str() {
                                let parent = PathBuf::from(path);
                                let name = parent.file_name().unwrap().to_str().unwrap();
                                fs::rename(
                                    &parent,
                                    parent.with_file_name(format!("{name}-detached")),
                                )?;
                                let outside = parent.with_file_name(format!("{name}-outside"));
                                if row["swap_directory"] == true {
                                    fs::rename(outside, &parent)?;
                                } else {
                                    std::os::unix::fs::symlink(outside, &parent)?;
                                }
                                if row["swap_continue"] == true {
                                    return Ok(());
                                }
                            }
                            return Err(std::io::Error::other(
                                "injected recovery persistence failure",
                            ));
                        }
                        if hit {
                            return Ok(());
                        }
                        if scenario == "reauthenticate" && point == R::LocksAcquired {
                            hit = true;
                            let journal = PathBuf::from(row["journal"].as_str().unwrap());
                            let mut record =
                                crate::shared_document::parse_bytes(&fs::read(&journal)?).unwrap();
                            record.set(
                                "mac",
                                crate::state::wire::Wire::Scalar(json!("0".repeat(64))),
                            );
                            fs::write(
                                journal,
                                crate::shared_document::json::pretty(&record).unwrap(),
                            )?;
                        }
                        if scenario == "late-parent-swap"
                            && point
                                == if row["resource"].is_null() {
                                    R::BeforeDocument
                                } else {
                                    R::BeforePaths
                                }
                        {
                            hit = true;
                            let journal = PathBuf::from(row["journal"].as_str().unwrap());
                            let config = journal.parent().unwrap();
                            fs::rename(config, config.with_file_name("detached"))?;
                            fs::create_dir(config)?;
                            fs::write(config.join("foreign"), b"outside lock")?;
                        }
                        if scenario == "late-state" && point == R::AfterDeactivate {
                            hit = true;
                            fs::write(row["file"].as_str().unwrap(), b"{\"foreign\":true}\n")?;
                        }
                        if scenario == "late-capture"
                            && point == R::Path(transaction::path_recovery::Edge::Capture)
                        {
                            hit = true;
                            fs::write(
                                PathBuf::from(row["resource"].as_str().unwrap()).join("stop.sh"),
                                b"foreign-resource",
                            )?;
                        }
                        if scenario == "late-document" && point == R::BeforeDocument {
                            hit = true;
                            fs::write(row["document"].as_str().unwrap(), b"{\"foreign\":true}\n")?;
                        }
                        if matches!(
                            (scenario, point),
                            ("interrupt-deactivate", R::AfterDeactivate)
                                | ("interrupt-path", R::AfterPath)
                                | ("interrupt-restore", R::AfterRestore)
                                | ("interrupt-state", R::BeforeState)
                                | ("interrupt-cleanup", R::BeforeCleanup)
                                | ("interrupt-prepare-sync", R::PrepareStageSync)
                                | ("interrupt-prepare-publish", R::PreparePublish)
                        ) {
                            hit = true;
                            return Err(std::io::Error::other("interrupted recovery"));
                        }
                        Ok(())
                    })
                });
                let mut value = outcome(result);
                if row["observe_hit"] == true {
                    value["hit"] = json!(persistence_hit);
                }
                return value;
            }
            if row["mode"] == "journal" {
                let bytes: Vec<u8> = serde_json::from_value(row["content"].clone()).unwrap();
                return match transaction::journal_record(
                    &bytes,
                    std::path::Path::new(row["file"].as_str().unwrap()),
                ) {
                    Ok(_) => json!({"accepted":true}),
                    Err(_) => json!({"accepted":false,"error_class":"ValueError"}),
                };
            }
            let case = PathBuf::from(row["case"].as_str().unwrap());
            let path = case.join("document.json");
            let scenario = row["scenario"].as_str().unwrap();
            let expected = snapshot::snapshot(&path).unwrap();
            if scenario == "stale" {
                fs::write(&path, b"{\"foreign\":true}\n").unwrap();
            }
            let parent = crate::bundles::open_directory(&case).unwrap();
            let mut failed = false;
            let result = transaction::compare_replace_document(
                &expected,
                if scenario == "remove" {
                    None
                } else {
                    Some(b"{\"after\":true}\n")
                },
                &parent,
                "q",
                |point| {
                    if (scenario == "before-capture" && point == Boundary::Capture)
                        || (scenario == "before-publish" && point == Boundary::Publish)
                    {
                        fs::write(&path, b"{\"foreign\":true}\n")?;
                    }
                    let hit = matches!(
                        (scenario, point),
                        ("fail-file-sync", Boundary::StageSync)
                            | ("fail-parent-sync", Boundary::PublishParentSync)
                            | ("fail-capture-sync", Boundary::CaptureParentSync)
                            | ("fail-evidence-sync", Boundary::CaptureEvidenceSync)
                            | ("fail-cleanup-sync", Boundary::CleanupParentSync)
                            | ("fail-entry-delete", Boundary::EvidenceRemove)
                            | ("fail-quarantine-remove", Boundary::QuarantineRemove)
                    );
                    if hit && !failed {
                        failed = true;
                        return Err(std::io::Error::other("injected sync failure"));
                    }
                    Ok(())
                },
            );
            match result {
                Ok(value) => json!({"accepted":true,"content":value.content}),
                Err(Error::Io(_)) => json!({"accepted":false,"error_class":"OSError"}),
                Err(Error::Invalid(_)) => json!({"accepted":false,"error_class":"ValueError"}),
            }
        })
        .collect();
    fs::write(
        base.join("results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}
