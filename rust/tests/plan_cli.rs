#![cfg(unix)]

use serde_json::Value;
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use tempfile::TempDir;

struct Fixture {
    _temp: TempDir,
    root: PathBuf,
    destination: PathBuf,
    state: PathBuf,
    source: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let temp = TempDir::new().unwrap();
        let root = temp.path().join("repository");
        let source = root.join("plugins/pstack/skills/example");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(root.join("catalog")).unwrap();
        fs::create_dir_all(root.join("profiles")).unwrap();
        fs::write(
            source.join("SKILL.md"),
            "---\nname: example\ndescription: Example skill.\n---\n",
        )
        .unwrap();
        fs::write(
            root.join("catalog/plugins.json"),
            r#"{
              "schemaVersion": 2,
              "marketplace": {"id":"market","name":"Market","owner":{"name":"Owner"},"description":"Description"},
              "sources": [{"id":"local","kind":"local","path":"plugins"}],
              "plugins": [{"id":"pstack","source":"local","artifacts":{"agent-skills":{"path":"pstack/skills"}}}]
            }"#,
        )
        .unwrap();
        fs::write(
            root.join("profiles/pstack.json"),
            r#"{
              "schemaVersion": 1,
              "id": "pstack",
              "plugins": {"include":["pstack"],"exclude":[]},
              "targets": [{"id":"hermes"}]
            }"#,
        )
        .unwrap();
        let destination = temp.path().join("destination");
        let state = temp.path().join("state");
        Self {
            _temp: temp,
            root,
            destination,
            state,
            source,
        }
    }

    fn command(&self, adopt: bool) -> Output {
        let mut command = Command::new(env!("CARGO_BIN_EXE_agent-workbench"));
        command.args([
            "--root",
            self.root.to_str().unwrap(),
            "--profile",
            "pstack",
            "plan",
            "--target",
            "hermes",
            "--destination",
            self.destination.to_str().unwrap(),
            "--state-dir",
            self.state.to_str().unwrap(),
            "--json",
        ]);
        if adopt {
            command.arg("--adopt");
        }
        command.output().unwrap()
    }
}

fn operation(output: &Output) -> Value {
    let operations: Value = serde_json::from_slice(&output.stdout).unwrap();
    operations.as_array().unwrap()[0].clone()
}

fn code(output: &Output) -> i32 {
    output.status.code().unwrap()
}

#[test]
fn clean_matching_adopt_and_foreign_observations_have_exit_parity() {
    let fixture = Fixture::new();

    let clean = fixture.command(false);
    assert_eq!(
        code(&clean),
        0,
        "{}",
        String::from_utf8_lossy(&clean.stderr)
    );
    assert_eq!(operation(&clean)["kind"], "create");

    fs::create_dir_all(&fixture.destination).unwrap();
    symlink(&fixture.source, fixture.destination.join("example")).unwrap();
    let matching = fixture.command(false);
    assert_eq!(code(&matching), 1);
    assert_eq!(operation(&matching)["kind"], "conflict");
    assert_eq!(
        operation(&matching)["reason"],
        "matching resource is not recorded as owned; use --adopt"
    );

    let adopted = fixture.command(true);
    assert_eq!(code(&adopted), 0);
    assert_eq!(operation(&adopted)["kind"], "adopt");

    fs::remove_file(fixture.destination.join("example")).unwrap();
    fs::write(fixture.destination.join("example"), "foreign").unwrap();
    let foreign = fixture.command(false);
    assert_eq!(code(&foreign), 1);
    assert_eq!(operation(&foreign)["kind"], "conflict");
    assert_eq!(
        operation(&foreign)["reason"],
        "destination is a foreign file or link"
    );
}

#[test]
fn malformed_state_and_unknown_commands_fail_with_exit_two() {
    let fixture = Fixture::new();
    let state_file = fixture
        .state
        .join("agent-workbench/market/pstack/hermes.json");
    fs::create_dir_all(state_file.parent().unwrap()).unwrap();
    fs::write(&state_file, "{}").unwrap();
    let state = fixture.command(false);
    assert_eq!(code(&state), 2);
    assert!(String::from_utf8_lossy(&state.stderr).contains("unsupported installation state"));

    let unknown = Command::new(env!("CARGO_BIN_EXE_agent-workbench"))
        .args(["--root", fixture.root.to_str().unwrap(), "unknown"])
        .output()
        .unwrap();
    assert_eq!(code(&unknown), 2);
    assert!(unknown.stdout.is_empty());
    let usage = agent_workbench::usage_text();
    let usage = usage.split("\n\n").next().unwrap();
    assert_eq!(
        String::from_utf8_lossy(&unknown.stderr),
        format!("{usage}\nagent-workbench: error: argument command: invalid choice: 'unknown' (choose from validate, render, plan, install, status, trust, uninstall, update)\n")
    );
}

#[test]
fn symlink_anywhere_in_source_tree_fails_closed() {
    let fixture = Fixture::new();
    symlink(Path::new("SKILL.md"), fixture.source.join("linked-content")).unwrap();
    let output = fixture.command(false);
    assert_eq!(code(&output), 2);
    assert!(String::from_utf8_lossy(&output.stderr).contains("skill content may not be a symlink"));
}

#[test]
fn destination_symlink_and_indented_frontmatter_match_python_rejection() {
    let fixture = Fixture::new();
    let real = fixture._temp.path().join("real-destination");
    fs::create_dir_all(&real).unwrap();
    symlink(&real, &fixture.destination).unwrap();
    let redirected = fixture.command(false);
    assert_eq!(code(&redirected), 2);
    assert!(String::from_utf8_lossy(&redirected.stderr)
        .contains("destination root may not be a symlink"));

    fs::remove_file(&fixture.destination).unwrap();
    fs::write(
        fixture.source.join("SKILL.md"),
        "---\n  name: example\n  description: Example skill.\n---\n",
    )
    .unwrap();
    let indented = fixture.command(false);
    assert_eq!(code(&indented), 2);
    assert!(String::from_utf8_lossy(&indented.stderr).contains("frontmatter needs"));

    fs::write(
        fixture.source.join("SKILL.md"),
        "---\nname: first\nname: second\ndescription: Example skill.\n---\n",
    )
    .unwrap();
    let duplicate_name = fixture.command(false);
    assert_eq!(code(&duplicate_name), 0);
    assert_eq!(operation(&duplicate_name)["key"], "skill:first");
}

#[test]
fn explicit_skills_component_and_global_help_are_supported() {
    let fixture = Fixture::new();
    let component = Command::new(env!("CARGO_BIN_EXE_agent-workbench"))
        .args([
            "--root",
            fixture.root.to_str().unwrap(),
            "--profile",
            "pstack",
            "plan",
            "--target",
            "hermes",
            "--component",
            "skills",
            "--destination",
            fixture.destination.to_str().unwrap(),
            "--state-dir",
            fixture.state.to_str().unwrap(),
            "--json",
        ])
        .output()
        .unwrap();
    assert_eq!(
        code(&component),
        0,
        "{}",
        String::from_utf8_lossy(&component.stderr)
    );

    let help = Command::new(env!("CARGO_BIN_EXE_agent-workbench"))
        .arg("--help")
        .output()
        .unwrap();
    assert_eq!(code(&help), 0);
    assert!(String::from_utf8_lossy(&help.stdout).starts_with("usage:"));

    let help_after_global = Command::new(env!("CARGO_BIN_EXE_agent-workbench"))
        .args(["--root", fixture.root.to_str().unwrap(), "--help"])
        .output()
        .unwrap();
    assert_eq!(code(&help_after_global), 0);
    assert!(String::from_utf8_lossy(&help_after_global.stdout).starts_with("usage:"));
}
