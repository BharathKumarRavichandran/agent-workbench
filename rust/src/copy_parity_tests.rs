//! Test-only driver for deterministic old-versus-new filesystem race rows.
use super::*;
use std::cell::RefCell;
use std::os::unix::fs::symlink;

type Hook = Box<dyn FnMut(&str, &Path) -> Result<()>>;
thread_local! { static HOOK: RefCell<Option<Hook>> = RefCell::new(None); }

pub(super) fn event(event: &str, path: &Path) -> Result<()> {
    HOOK.with(|hook| match hook.borrow_mut().as_mut() {
        Some(hook) => hook(event, path),
        None => Ok(()),
    })
}

#[test]
#[ignore = "driven by scripts/check_rust_plan_parity.py with isolated fixtures"]
fn differential_copy_race() {
    let base = PathBuf::from(env::var_os("AW_COPY_BASE").unwrap());
    let scenario = env::var("AW_COPY_CASE").unwrap();
    let root = base.join("checkout");
    let source = root.join("plugins/pstack/agents/sample.md");
    let project = base.join("project");
    let agents = project.join(".cursor/agents");
    let mut cli = Cli {
        root,
        profile: "pstack".into(),
        target: SkillsTarget::Cursor,
        components: Some(vec![
            ComponentKind::Skills,
            ComponentKind::Agents,
            ComponentKind::Rules,
        ]),
        destination: Some(base.join("destination")),
        project_root: Some(project.clone()),
        state_dir: base.join("state"),
        adopt: false,
        trust: Vec::new(),
        json: true,
        action: Action::Install,
    };
    if scenario.ends_with("state-failure") {
        cli.components = Some(vec![ComponentKind::Agents]);
    }
    let removal = scenario.starts_with("remove") || scenario == "selection-interrupted";
    let replacement = scenario == "replace-state-failure";
    let keep = scenario == "keep-source-change";
    if removal || replacement || keep {
        run(&cli).unwrap();
    }
    if replacement {
        fs::write(
            &source,
            format!("{}updated\n", fs::read_to_string(&source).unwrap()),
        )
        .unwrap();
    }
    if removal {
        if scenario == "selection-interrupted" {
            cli.components = Some(vec![ComponentKind::Skills]);
        } else {
            cli.action = Action::Uninstall;
        }
    }
    if scenario == "root-swap" {
        fs::create_dir_all(&agents).unwrap();
    }
    if scenario == "parent-swap" {
        fs::create_dir_all(&project).unwrap();
    }
    let plan = reconcile(&cli).unwrap();
    match scenario.as_str() {
        "source-change" | "keep-source-change" => {
            fs::write(&source, "changed after planning").unwrap()
        }
        "source-symlink" | "source-parent-symlink" => {
            let path = if scenario == "source-symlink" {
                source.clone()
            } else {
                source.parent().unwrap().to_path_buf()
            };
            let moved = path.with_extension("saved");
            fs::rename(&path, &moved).unwrap();
            symlink(moved, path).unwrap();
        }
        "root-swap" | "parent-swap" => {
            let path = if scenario == "root-swap" {
                agents.clone()
            } else {
                project.clone()
            };
            fs::rename(&path, path.with_extension("detached")).unwrap();
            fs::create_dir(&path).unwrap();
        }
        "remove-modified" => fs::write(agents.join("sample.md"), "foreign after plan").unwrap(),
        _ => {}
    }
    let hook_case = scenario.clone();
    HOOK.with(|slot| {
        *slot.borrow_mut() = Some(Box::new(move |event, path| {
            if ((hook_case == "publish-root-swap" && event == "publish")
                || (hook_case == "published-root-swap" && event == "published")
                || (hook_case == "remove-root-swap" && event == "quarantined"))
                && path == agents
            {
                fs::rename(path, path.with_extension("detached")).unwrap();
                let outside = project.parent().unwrap().join("outside");
                fs::create_dir(&outside).unwrap();
                symlink(outside, path).unwrap();
            }
            if hook_case == "remove-foreign-state-failure" && event == "progress" {
                fs::write(agents.join("sample.md"), "foreign during rollback").unwrap();
            }
            if ((hook_case == "remove-interrupted" || hook_case == "selection-interrupted")
                && event == "quarantine"
                && path.ends_with("sample.mdc"))
                || (hook_case.ends_with("state-failure") && event == "progress")
            {
                return Err("injected failure".into());
            }
            Ok(())
        }))
    });
    let failed = apply(&plan, cli.action == Action::Uninstall).is_err();
    HOOK.with(|slot| *slot.borrow_mut() = None);
    fs::write(
        base.join("race-result.json"),
        if failed { "true" } else { "false" },
    )
    .unwrap();
}
