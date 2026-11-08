use super::path_recovery::*;
use serde_json::json;

#[test]
fn authenticated_snapshot_roundtrip_rejects_escaping_tree() {
    let file = json!({"file":"YWJj"});
    assert_eq!(
        Snapshot::from_journal(&file).unwrap().to_journal().unwrap(),
        file
    );
    assert!(Snapshot::from_journal(
        &json!({"tree":{"directories":[["",493]],"files":[["../escape",420,""]]}})
    )
    .is_err());
}

#[test]
fn accepted_snapshot_aliases_retain_original_json_identity() {
    for raw in [
        json!({"file":"YR=="}),
        json!({"tree":{"directories":[["",493]],"files":[],"foreign":true}}),
        json!({"tree":{"directories":[["",493],["sub",488],["sub",488]],"files":[]}}),
        json!({"tree":{"directories":[["",493],["./sub",488]],"files":[["sub//data",true,"YR=="]]}}),
    ] {
        let snapshot = Snapshot::from_journal(&raw).unwrap();
        assert_eq!(snapshot.to_journal().unwrap(), raw);
    }
    assert_ne!(
        Snapshot::from_journal(&json!({"file":"YR=="})).unwrap(),
        Snapshot::File(b"a".to_vec())
    );
    assert_ne!(
        Snapshot::from_journal(
            &json!({"tree":{"directories":[["",493]],"files":[["data",true,"YQ=="]]}})
        )
        .unwrap(),
        Snapshot::from_journal(
            &json!({"tree":{"directories":[["",493]],"files":[["data",1,"YQ=="]]}})
        )
        .unwrap(),
    );
}

#[test]
fn staged_name_cannot_alias_live_destination() {
    let dir = tempfile::tempdir().unwrap();
    let parent = crate::bundles::open_directory(dir.path()).unwrap();
    let restore = PathRestore {
        before: Some(Snapshot::File(b"before".to_vec())),
        expected: None,
        allowed: vec![None],
        revision: None,
    };
    assert!(restore_path(&parent, "q-file", Resource::File, &restore, "q", |_| Ok(())).is_err());
    assert!(!dir.path().join("q-file").exists());
}

#[test]
fn roots_and_file_snapshots_never_follow_links() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("target"), b"secret").unwrap();
    std::os::unix::fs::symlink("target", dir.path().join("item")).unwrap();
    let parent = crate::bundles::open_directory(dir.path()).unwrap();
    assert!(capture_at(&parent, "item", Resource::File).is_err());
    assert!(capture_at(&parent, "item", Resource::Tree).is_err());
    assert_eq!(
        capture_at(&parent, "item", Resource::Symlink).unwrap(),
        Some(Snapshot::Symlink("target".into()))
    );
    std::os::unix::fs::symlink(dir.path(), dir.path().join("root-link")).unwrap();
    assert!(crate::bundles::open_directory(&dir.path().join("root-link")).is_err());
}

fn typed_state(root: &std::path::Path) -> serde_json::Value {
    use std::os::unix::fs::PermissionsExt;
    fn visit(
        root: &std::path::Path,
        directory: &std::path::Path,
        rows: &mut Vec<serde_json::Value>,
    ) {
        let mut entries = std::fs::read_dir(directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect::<Vec<_>>();
        entries.sort();
        for path in entries {
            let metadata = std::fs::symlink_metadata(&path).unwrap();
            let mode = metadata.permissions().mode() & 0o7777;
            let name = path.strip_prefix(root).unwrap().to_str().unwrap();
            if metadata.file_type().is_symlink() {
                rows.push(json!([
                    name,
                    "link",
                    mode,
                    null,
                    std::fs::read_link(&path).unwrap().to_str().unwrap()
                ]));
            } else if metadata.is_dir() {
                rows.push(json!([name, "dir", mode, null, null]));
                visit(root, &path, rows);
            } else {
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode | 0o400))
                    .unwrap();
                let content = std::fs::read(&path).unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
                let hex = content
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>();
                rows.push(json!([name, "file", mode, content.len(), hex]));
            }
        }
    }
    let mut rows = Vec::new();
    if root.exists() {
        visit(root, root, &mut rows);
    }
    json!(rows)
}

fn run_contract(input: &serde_json::Value) -> serde_json::Value {
    use std::{io, path::Path};
    let parent =
        crate::bundles::open_directory(Path::new(input["root"].as_str().unwrap())).unwrap();
    let resource = match input["kind"].as_str().unwrap() {
        "tree" => Resource::Tree,
        "link" => Resource::Symlink,
        _ => Resource::File,
    };
    let decode = |key: &str| {
        if input[key].is_null() {
            None
        } else {
            Some(Snapshot::from_journal(&input[key]).unwrap())
        }
    };
    let expected = capture_at(&parent, "item", resource).unwrap();
    let captured_json = expected.as_ref().map(|s| s.to_journal().unwrap());
    for raw in [&input["before"], &input["expected"]]
        .into_iter()
        .chain(input["allowed"].as_array().into_iter().flatten())
    {
        if !raw.is_null() {
            if let Err(e) = Snapshot::from_journal(raw) {
                return json!({"error":e.to_string(),"retry_error":null,"captured":captured_json});
            }
        }
    }
    let before = decode("before");
    let mut rev = revision(&parent, "item").unwrap();
    if input["stale"] == true {
        std::fs::write(
            Path::new(input["root"].as_str().unwrap()).join("item"),
            b"after",
        )
        .unwrap();
    }
    if input["detached"] == true {
        let root = Path::new(input["root"].as_str().unwrap());
        std::fs::rename(root, root.with_extension("detached")).unwrap();
        std::fs::create_dir(root).unwrap();
    }
    if input["retry"] == true {
        rev = revision(&parent, "item").unwrap();
    }
    let restore = PathRestore {
        before: before.clone(),
        expected: if input.get("expected").is_some() {
            decode("expected")
        } else {
            expected.clone()
        },
        allowed: if let Some(values) = input["allowed"].as_array() {
            values
                .iter()
                .map(|v| {
                    if v.is_null() {
                        None
                    } else {
                        Some(Snapshot::from_journal(v).unwrap())
                    }
                })
                .collect()
        } else {
            vec![before.clone(), expected]
        },
        revision: rev,
    };
    let mut fired = false;
    let mut edge_count = 0;
    let mut hook = |edge| {
        if !fired
            && input["edge"].as_str()
                == Some(match edge {
                    Edge::Capture => "capture",
                    Edge::CaptureSync => "capture-sync",
                    Edge::Publish => "publish",
                    Edge::PublishSync if input["edge"] == "sync" => "sync",
                    Edge::PublishSync => "publish-sync",
                    Edge::EvidenceSync => "evidence-sync",
                    Edge::FileWrite => "file-write",
                    Edge::FileSync => "file-sync",
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::FileWrite) => "tree-file-write",
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::FileChmod) => "tree-file-chmod",
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::FileSync) => "tree-file-sync",
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::DirectoryChmod) => {
                        "tree-directory-chmod"
                    }
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::DirectorySync) => {
                        "tree-directory-sync"
                    }
                    Edge::TreeWrite(crate::bundles::TreeWriteEdge::RootSync) => "tree-root-sync",
                    _ => "",
                })
        {
            edge_count += 1;
            if edge_count != input["occurrence"].as_u64().unwrap_or(1) {
                return Ok(());
            }
            fired = true;
            if input["action"] == "fail" {
                return Err(io::Error::other("injected"));
            }
            let root = Path::new(input["root"].as_str().unwrap());
            if input["action"] == "foreign" {
                std::fs::remove_file(root.join("item")).ok();
                std::fs::write(root.join("item"), b"foreign")?;
            }
        }
        Ok(())
    };
    let result = if input["discard"] == true {
        discard_path_evidence_observed(&parent, resource, "q", before.as_ref(), &mut hook)
    } else {
        restore_path(&parent, "item", resource, &restore, "q", &mut hook)
    };
    let first_state = typed_state(Path::new(input["root"].as_str().unwrap()));
    let first_error = result.as_ref().err().map(ToString::to_string);
    let retry_error = if input["retry"] == true {
        (|| -> io::Result<()> {
            if input["discard"] == true {
                return discard_path_evidence(&parent, resource, "q", before.as_ref());
            }
            let expected = capture_at(&parent, "item", resource)?;
            let r = PathRestore {
                before: before.clone(),
                expected: expected.clone(),
                allowed: vec![before, expected, restore.expected.clone()],
                revision: revision(&parent, "item").unwrap(),
            };
            restore_path(&parent, "item", resource, &r, "q", |_| Ok(()))
        })()
        .err()
        .map(|e| e.to_string())
    } else {
        None
    };
    serde_json::json!({"error":first_error,"retry_error":retry_error,"captured":captured_json,
        "fired":fired,"first_state":first_state})
}

#[test]
#[ignore]
fn path_contract_driver() {
    let input = std::fs::read(std::env::var("AW_PATH_INPUT").unwrap()).unwrap();
    let input: serde_json::Value = serde_json::from_slice(&input).unwrap();
    let output = run_contract(&input);
    std::fs::write(
        std::env::var("AW_PATH_OUTPUT").unwrap(),
        serde_json::to_vec(&output).unwrap(),
    )
    .unwrap();
}
