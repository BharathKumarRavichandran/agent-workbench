//! Test-only fault controls for the authority construction contract.
use super::*;
use serde_json::{json, Value};
use std::{cell::RefCell, path::PathBuf};

thread_local! {
    static CONTROL: RefCell<Option<(String, Vec<String>)>> = const { RefCell::new(None) };
}

pub(super) fn entropy<const N: usize>() -> Option<[u8; N]> {
    CONTROL.with(|control| {
        control
            .borrow()
            .as_ref()
            .map(|_| std::array::from_fn(|i| i as u8))
    })
}

pub(super) fn io_fault(handle: &mut File, point: &str, content: &[u8]) -> io::Result<()> {
    CONTROL.with(|control| {
        let mut control = control.borrow_mut();
        if let Some((scenario, hits)) = control.as_mut() {
            if scenario == point {
                hits.push(point.to_owned());
                if point.ends_with("partial-write") {
                    handle.write_all(&content[..7])?;
                }
                return Err(io::Error::other("authority injected failure"));
            }
        }
        Ok(())
    })
}

pub(super) fn phase_fault(point: &str) -> io::Result<()> {
    CONTROL.with(|control| {
        let mut control = control.borrow_mut();
        if let Some((scenario, hits)) = control.as_mut() {
            if scenario == point {
                hits.push(point.to_owned());
                return Err(io::Error::other("authority injected phase failure"));
            }
        }
        Ok(())
    })
}

fn outcome(result: Result<(), Error>) -> Value {
    match result {
        Ok(()) => json!({"accepted": true}),
        Err(Error::Io(_)) => json!({"accepted": false, "error_class": "OSError"}),
        Err(Error::Invalid(_)) => json!({"accepted": false, "error_class": "ValueError"}),
    }
}

#[test]
#[ignore]
fn differential_authority() {
    let base = PathBuf::from(std::env::var_os("AW_AUTHORITY_BASE").unwrap());
    let row: Value =
        serde_json::from_slice(&std::fs::read(base.join("row.json")).unwrap()).unwrap();
    let scenario = row["scenario"].as_str().unwrap();
    let file = Path::new(row["file"].as_str().unwrap());
    CONTROL.with(|control| *control.borrow_mut() = Some((scenario.to_owned(), Vec::new())));
    let mut boundary = |point| {
        let target = match scenario {
            "key-create" => Some(Boundary::KeyCreate),
            "key-chmod" => Some(Boundary::KeyChmod),
            "key-entropy" => Some(Boundary::KeyEntropy),
            // Keep a failed stage for the finally-cleanup failure row.
            "journal-cleanup" => Some(Boundary::JournalRename),
            _ => None,
        };
        if target == Some(point) {
            CONTROL.with(|control| {
                control
                    .borrow_mut()
                    .as_mut()
                    .unwrap()
                    .1
                    .push(format!("{point:?}"))
            });
            return Err(io::Error::other("authority injected boundary failure"));
        }
        Ok(())
    };
    let result = if scenario.starts_with("key-") {
        journal_key(file, true, &mut boundary).map(|_| ())
    } else {
        let config = Path::new(row["config"].as_str().unwrap());
        let parent = bundles::open_directory(config).unwrap();
        let bytes: Vec<u8> = serde_json::from_value(row["record_json"].clone()).unwrap();
        write_journal(
            crate::shared_document::parse_bytes(&bytes).unwrap(),
            file,
            &config.join(".agent-workbench-transaction.json"),
            &parent,
            &mut boundary,
        )
    };
    let hits = CONTROL.with(|control| control.borrow_mut().take().unwrap().1);
    let key_path = file.with_file_name(format!(
        ".{}.transaction-key",
        file.file_name().unwrap().to_str().unwrap()
    ));
    let retained = std::fs::symlink_metadata(&key_path).ok().map(|info| {
        use std::os::unix::fs::MetadataExt;
        (
            info.dev(),
            info.ino(),
            info.mode(),
            std::fs::read(&key_path).ok(),
        )
    });
    CONTROL.with(|control| *control.borrow_mut() = Some(("retry".to_owned(), Vec::new())));
    let retry = if scenario.starts_with("key-") {
        outcome(journal_key(file, true, |_| Ok(())).map(|_| ()))
    } else {
        Value::Null
    };
    CONTROL.with(|control| *control.borrow_mut() = None);
    if let Some(expected) = retained {
        use std::os::unix::fs::MetadataExt;
        let info = std::fs::symlink_metadata(&key_path).unwrap();
        assert_eq!(
            expected,
            (
                info.dev(),
                info.ino(),
                info.mode(),
                std::fs::read(&key_path).ok()
            ),
            "retry replaced or repaired authority"
        );
    }
    std::fs::write(
        base.join("result.json"),
        serde_json::to_vec(&json!({
            "outcome": outcome(result), "hits": hits, "retry": retry,
        }))
        .unwrap(),
    )
    .unwrap();
}
