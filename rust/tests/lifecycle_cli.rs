#![cfg(unix)]

use serde_json::{json, Value};
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::PathBuf;
use std::process::{Command, Output};
use tempfile::TempDir;

struct Fixture {
    _temp: TempDir,
    root: PathBuf,
    destination: PathBuf,
    state_root: PathBuf,
    source: PathBuf,
    state_file: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let temp = TempDir::new().unwrap();
        let base = temp.path().canonicalize().unwrap();
        let root = base.join("repository");
        let source = root.join("plugins/pstack/skills/example");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(root.join("catalog")).unwrap();
        fs::create_dir_all(root.join("profiles")).unwrap();
        fs::write(
            source.join("SKILL.md"),
            "---\nname: example\ndescription: Example.\n---\n",
        )
        .unwrap();
        fs::write(
            root.join("catalog/plugins.json"),
            r#"{"schemaVersion":2,"marketplace":{"id":"market","name":"Market","owner":{"name":"Owner"},"description":"Description"},"sources":[{"id":"local","kind":"local","path":"plugins"}],"plugins":[{"id":"pstack","source":"local","artifacts":{"agent-skills":{"path":"pstack/skills"}}}]}"#,
        )
        .unwrap();
        fs::write(
            root.join("profiles/pstack.json"),
            r#"{"schemaVersion":1,"id":"pstack","plugins":{"include":["pstack"],"exclude":[]},"targets":[{"id":"hermes"}]}"#,
        )
        .unwrap();
        let destination = base.join("destination");
        let state_root = base.join("state");
        let state_file = state_root.join("agent-workbench/market/pstack/hermes.json");
        Self {
            _temp: temp,
            root,
            destination,
            state_root,
            source,
            state_file,
        }
    }

    fn run(&self, action: &str, extra: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_agent-workbench-rs"))
            .args([
                "--root",
                self.root.to_str().unwrap(),
                "--profile",
                "pstack",
                action,
                "--target",
                "hermes",
                "--destination",
                self.destination.to_str().unwrap(),
                "--state-dir",
                self.state_root.to_str().unwrap(),
                "--json",
            ])
            .args(extra)
            .output()
            .unwrap()
    }
}

fn code(output: &Output) -> i32 {
    output.status.code().unwrap()
}

fn operations(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn clean_install_status_and_uninstall_write_v3_and_relative_links() {
    let fixture = Fixture::new();
    let installed = fixture.run("install", &[]);
    assert_eq!(
        code(&installed),
        0,
        "{}",
        String::from_utf8_lossy(&installed.stderr)
    );
    assert_eq!(operations(&installed)[0]["kind"], "create");
    let target = fs::read_link(fixture.destination.join("example")).unwrap();
    assert!(!target.is_absolute());
    assert_eq!(
        fixture.destination.join(target).canonicalize().unwrap(),
        fixture.source
    );

    let state: Value =
        serde_json::from_str(&fs::read_to_string(&fixture.state_file).unwrap()).unwrap();
    assert_eq!(
        fs::metadata(&fixture.state_file)
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    assert_eq!(state["schemaVersion"], 3);
    assert_eq!(
        state["destinationRoots"],
        json!({"skills": fixture.destination})
    );
    assert_eq!(
        state["entries"]["skill:example"]["resourceKind"],
        "directory-symlink"
    );

    let status = fixture.run("status", &[]);
    assert_eq!(code(&status), 0);
    assert_eq!(operations(&status)[0]["kind"], "keep");

    let uninstalled = fixture.run("uninstall", &[]);
    assert_eq!(
        code(&uninstalled),
        0,
        "{}",
        String::from_utf8_lossy(&uninstalled.stderr)
    );
    assert_eq!(operations(&uninstalled)[0]["kind"], "remove-owned");
    assert!(!fixture.destination.join("example").exists());
    assert!(!fixture.state_file.exists());
}

#[test]
fn modified_owned_link_conflicts_for_status_install_and_uninstall() {
    let fixture = Fixture::new();
    assert_eq!(code(&fixture.run("install", &[])), 0);
    fs::remove_file(fixture.destination.join("example")).unwrap();
    symlink("foreign", fixture.destination.join("example")).unwrap();
    for action in ["status", "install", "uninstall"] {
        let output = fixture.run(action, &[]);
        assert_eq!(
            code(&output),
            1,
            "{action}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(operations(&output)[0]["kind"], "conflict");
        assert_eq!(
            operations(&output)[0]["reason"],
            "managed path was modified"
        );
    }
    assert!(fixture.state_file.exists());
}

fn v2(fixture: &Fixture) -> Value {
    json!({
        "schemaVersion": 2,
        "repository": fixture.root,
        "marketplace": "market",
        "profile": "pstack",
        "target": "hermes",
        "destinationRoot": fixture.destination,
        "entries": {
            "skill:example": {
                "key": "skill:example",
                "plugin": "pstack",
                "source": "plugins/pstack/skills/example",
                "destination": fixture.destination.join("example")
            }
        }
    })
}

#[test]
fn v2_is_uninstallable_and_install_migrates_to_v3() {
    for action in ["uninstall", "install"] {
        let fixture = Fixture::new();
        fs::create_dir_all(&fixture.destination).unwrap();
        symlink(&fixture.source, fixture.destination.join("example")).unwrap();
        fs::create_dir_all(fixture.state_file.parent().unwrap()).unwrap();
        fs::write(
            &fixture.state_file,
            serde_json::to_string_pretty(&v2(&fixture)).unwrap(),
        )
        .unwrap();
        let output = fixture.run(action, &[]);
        assert_eq!(
            code(&output),
            0,
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        if action == "uninstall" {
            assert!(!fixture.state_file.exists());
        } else {
            let state: Value =
                serde_json::from_str(&fs::read_to_string(&fixture.state_file).unwrap()).unwrap();
            assert_eq!(state["schemaVersion"], 3);
            assert_eq!(operations(&output)[0]["kind"], "keep");
        }
    }
}

#[test]
fn state_decoder_fails_closed_on_v4_unknown_null_and_mismatch() {
    let fixture = Fixture::new();
    fs::create_dir_all(fixture.state_file.parent().unwrap()).unwrap();
    for value in [
        json!({"schemaVersion": 4}),
        json!({
            "schemaVersion": 3, "repository": fixture.root, "marketplace": "market",
            "profile": "pstack", "target": "hermes", "destinationRoot": fixture.destination,
            "entries": {}, "destinationRoots": {"skills": fixture.destination, "agents": fixture.destination}
        }),
        json!({
            "schemaVersion": 3, "repository": fixture.root, "marketplace": "market",
            "profile": "pstack", "target": "hermes", "destinationRoot": null,
            "entries": {}, "destinationRoots": {"skills": fixture.destination}
        }),
    ] {
        fs::write(&fixture.state_file, serde_json::to_vec(&value).unwrap()).unwrap();
        assert_eq!(code(&fixture.run("status", &[])), 2);
    }
}

#[test]
fn state_symlink_retains_python_ownership_semantics() {
    let fixture = Fixture::new();
    assert_eq!(code(&fixture.run("install", &[])), 0);
    let saved = fixture.state_file.with_file_name("saved.json");
    fs::rename(&fixture.state_file, &saved).unwrap();
    symlink(&saved, &fixture.state_file).unwrap();

    let status = fixture.run("status", &[]);
    assert_eq!(
        code(&status),
        0,
        "{}",
        String::from_utf8_lossy(&status.stderr)
    );
    assert_eq!(operations(&status)[0]["kind"], "keep");
}
