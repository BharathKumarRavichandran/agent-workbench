//! Private dependency driver: never reachable from the production CLI.
use super::*;
use std::cell::RefCell;

#[test]
#[ignore = "driven by scripts/check_rust_plan_parity.py"]
fn differential_names() {
    let base = PathBuf::from(env::var_os("AW_BUNDLE_BASE").unwrap());
    let rows: Vec<Vec<Vec<u8>>> =
        serde_json::from_slice(&fs::read(base.join("names.json")).unwrap()).unwrap();
    let results: Vec<serde_json::Value> = rows
        .iter()
        .map(|names| serde_json::json!(bundles::fixture_digest(names).unwrap()))
        .collect();
    fs::write(
        base.join("name-results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}

type ReadHook = Box<dyn FnMut() -> std::io::Result<()>>;
thread_local! { static READ_HOOK: RefCell<Option<ReadHook>> = RefCell::new(None); }

pub(super) fn after_read(path: &Path) -> std::io::Result<()> {
    if path != Path::new("nested/run") {
        return Ok(());
    }
    READ_HOOK.with(|slot| match slot.borrow_mut().as_mut() {
        Some(hook) => hook(),
        None => Ok(()),
    })
}

#[test]
#[ignore = "driven by scripts/check_rust_plan_parity.py"]
fn differential_publication() {
    let base = PathBuf::from(env::var_os("AW_BUNDLE_BASE").unwrap());
    let rows: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(base.join("publication.json")).unwrap()).unwrap();
    let results: Vec<serde_json::Value> = rows
        .iter()
        .map(|row| {
            use std::os::unix::fs::{symlink, PermissionsExt};
            let base = PathBuf::from(row["base"].as_str().unwrap());
            let scenario = row["scenario"].as_str().unwrap();
            let expected = row["digest"].as_str().unwrap();
            let old_mask = rustix::process::umask(rustix::fs::Mode::from_raw_mode(
                row["umask"].as_u64().unwrap() as _,
            ));
            let mut accepted = false;
            let mut same = None;
            let result = (|| -> std::io::Result<()> {
                let destination = base.join("destination");
                let parent = bundles::open_directory(&destination)?;
                let source = base.join(if scenario == "ancestor-symlink" {
                    "alias/nested"
                } else {
                    "source"
                });
                if scenario == "during-read" {
                    let file = source.join("nested/run");
                    READ_HOOK.with(|slot| {
                        *slot.borrow_mut() =
                            Some(Box::new(move || fs::write(&file, b"changed while reading")))
                    });
                }
                let tree = bundles::Tree::validate_source(&source, expected)?;
                if scenario == "snapshot" {
                    fs::write(source.join("nested/run"), b"changed after snapshot")?;
                }
                if scenario == "detached" {
                    fs::rename(&destination, base.join("detached"))?;
                    symlink("outside", &destination)?;
                }
                bundles::publish("bundle", &parent, &tree)?;
                accepted = true;
                if scenario == "mode-change" {
                    fs::set_permissions(
                        destination.join("bundle/hooks/nested/run"),
                        fs::Permissions::from_mode(0o750),
                    )?;
                } else if scenario == "byte-change" {
                    fs::write(destination.join("bundle/hooks/nested/run"), b"foreign edit")?;
                }
                same = Some(bundles::tree_digest("bundle", &parent).as_deref() == Some(expected));
                if scenario == "keep" {
                    assert_eq!(bundles::Tree::validate_source(&source, expected)?, tree);
                }
                if scenario == "cleanup" || scenario == "detached" {
                    if scenario == "detached" {
                        assert!(bundles::open_directory(&destination).is_err());
                        accepted = false;
                    }
                    // Exercise the same pinned quarantine/delete boundary as Python;
                    // ownership state and transaction machinery remain D5 dependencies.
                    rustix::fs::mkdirat(
                        &parent,
                        "quarantine",
                        rustix::fs::Mode::from_raw_mode(0o700),
                    )?;
                    let quarantine = rustix::fs::openat(
                        &parent,
                        "quarantine",
                        rustix::fs::OFlags::RDONLY
                            | rustix::fs::OFlags::DIRECTORY
                            | rustix::fs::OFlags::NOFOLLOW,
                        rustix::fs::Mode::empty(),
                    )?;
                    bundles::rename_new("bundle", &parent, "entry", &quarantine)?;
                    assert_eq!(
                        bundles::tree_digest("entry", &quarantine).as_deref(),
                        Some(expected)
                    );
                    bundles::delete_tree("entry", &quarantine)?;
                    rustix::fs::unlinkat(&parent, "quarantine", rustix::fs::AtFlags::REMOVEDIR)?;
                }
                Ok(())
            })();
            READ_HOOK.with(|slot| *slot.borrow_mut() = None);
            if scenario == "during-read" {
                assert_eq!(
                    result.as_ref().unwrap_err().to_string(),
                    "bundle changed while reading"
                );
            }
            let _ = result; // Rejection is compared with the Python oracle, not suppressed by the CLI.
            rustix::process::umask(old_mask);
            serde_json::json!({"accepted": accepted, "same": same})
        })
        .collect();
    fs::write(
        base.join("publication-results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore = "driven by scripts/check_rust_plan_parity.py"]
fn differential_bundles() {
    let base = PathBuf::from(env::var_os("AW_BUNDLE_BASE").unwrap());
    let rows: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(base.join("rows.json")).unwrap()).unwrap();
    let results: Vec<serde_json::Value> = rows
        .iter()
        .map(|row| {
            serde_json::json!(
                bundles::source_tree(Path::new(row["source"].as_str().unwrap()))
                    .and_then(|tree| tree.digest())
                    .ok()
            )
        })
        .collect();
    fs::write(
        base.join("results.json"),
        serde_json::to_vec(&results).unwrap(),
    )
    .unwrap();
}
