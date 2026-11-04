//! Private serialized real-plan driver. Not a public activation surface.
use std::{env, fs, path::PathBuf};
fn assert_locks(raw: &serde_json::Value) -> std::io::Result<()> {
    let state = PathBuf::from(raw["state_before"]["path"].as_str().unwrap());
    let hooks = PathBuf::from(raw["roots"]["hooks"].as_str().unwrap());
    for path in [
        state.parent().unwrap().join(format!(
            ".{}.lock",
            state.file_name().unwrap().to_str().unwrap()
        )),
        hooks.parent().unwrap().join(".agent-workbench.lock"),
    ] {
        let held_path = if path.exists() {
            path.clone()
        } else {
            PathBuf::from(raw["repository"].as_str().unwrap())
                .parent()
                .unwrap()
                .join("project/detached")
                .join(path.file_name().unwrap())
        };
        let lock = fs::File::open(&held_path)?;
        assert_eq!(
            rustix::fs::flock(&lock, rustix::fs::FlockOperation::NonBlockingLockExclusive),
            Err(rustix::io::Errno::WOULDBLOCK),
            "transaction released lock: {path:?}"
        );
    }
    Ok(())
}
#[test]
#[ignore]
fn differential_apply() {
    let base = PathBuf::from(env::var_os("AW_APPLY_BASE").unwrap());
    let raw: serde_json::Value =
        serde_json::from_slice(&fs::read(base.join("request.json")).unwrap()).unwrap();
    let request: super::apply::ApplyRequest = serde_json::from_value(raw.clone()).unwrap();
    let scenario = env::var("AW_APPLY_SCENARIO").unwrap_or_default();
    if scenario == "recover-only" {
        let recovery = super::recovery::RecoveryRequest::new(
            PathBuf::from(raw["state_before"]["path"].as_str().unwrap()),
            raw["target"].as_str().unwrap(),
            serde_json::from_value(raw["roots"].clone()).unwrap(),
        )
        .unwrap();
        let result = super::recovery::recover(&recovery, |_| Ok(()));
        fs::write(
            base.join("result.json"),
            serde_json::to_vec(
                &serde_json::json!({"accepted": result.is_ok(), "error": format!("{result:?}"),
                    "error_class": match &result {
                        Err(crate::shared_document::snapshot::Error::Invalid(_)) => "ValueError",
                        Err(crate::shared_document::snapshot::Error::Io(e)) => match e.kind() {
                            std::io::ErrorKind::AlreadyExists => "FileExistsError",
                            std::io::ErrorKind::NotFound => "FileNotFoundError",
                            std::io::ErrorKind::NotADirectory => "NotADirectoryError",
                            std::io::ErrorKind::IsADirectory => "IsADirectoryError",
                            std::io::ErrorKind::PermissionDenied => "PermissionError",
                            _ => "OSError",
                        },
                        _ => "",
                    },
                    "diagnostic": match &result { Err(crate::shared_document::snapshot::Error::Invalid(e)) => e.clone(), Err(crate::shared_document::snapshot::Error::Io(e)) => e.to_string(), _ => String::new() },
                    "errno": match &result { Err(crate::shared_document::snapshot::Error::Io(e)) => e.raw_os_error(), _ => None }}),
            )
            .unwrap(),
        )
        .unwrap();
        return;
    }
    let mut hit = false;
    let mut recovery_checked = false;
    let mut recovery_observed = false;
    let mut state_phase = false;
    let mut publications = 0;
    let mut captures = 0;
    let mut resource_index = 0;
    let result = super::apply::apply(&request, |edge| {
        use super::apply::ApplyBoundary as A;
        if edge == A::BeforeResource {
            resource_index += 1;
        }
        if scenario.starts_with("capture-tree-")
            && edge == A::ResourceCaptured
            && !hit
            && raw["operations"][resource_index - 1]["component"] == "hooks"
        {
            assert_locks(&raw)?;
            hit = true;
            let destination = PathBuf::from(
                raw["operations"][resource_index - 1]["path"]
                    .as_str()
                    .unwrap(),
            );
            let root = destination.parent().unwrap();
            let original = if scenario.ends_with("parent-swap") {
                root.parent().unwrap()
            } else {
                root
            };
            let project = base.join("fixture/project");
            fs::rename(original, project.join("detached"))?;
            if scenario.ends_with("outside-swap") {
                fs::create_dir(project.join("outside"))?;
                std::os::unix::fs::symlink(project.join("outside"), original)?;
            } else {
                fs::create_dir(original)?;
            }
        }
        if edge == A::BeforeRecovery {
            recovery_observed = true;
            assert_locks(&raw)?;
        }
        if scenario == "foreign-stage-cleanup-sync" && edge == A::ResourceParentSync {
            return Err(std::io::Error::other("injected after bundle rename"));
        }
        if scenario.starts_with("foreign-stage-cleanup") && edge == A::ResourceCleanup && !hit {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            assert_locks(&raw)?;
            hit = true;
            let operation = raw["operations"]
                .as_array()
                .unwrap()
                .iter()
                .find(|op| op["component"] == "hooks")
                .unwrap();
            let destination = PathBuf::from(operation["path"].as_str().unwrap());
            let foreign = destination
                .parent()
                .unwrap()
                .join(".agent-workbench-copy-0123456789abcdef");
            fs::create_dir(&foreign)?;
            fs::set_permissions(&foreign, fs::Permissions::from_mode(0o750))?;
            fs::write(
                foreign.join("foreign.txt"),
                b"foreign replacement must survive",
            )?;
            fs::set_permissions(
                foreign.join("foreign.txt"),
                fs::Permissions::from_mode(0o640),
            )?;
            let info = fs::symlink_metadata(&foreign)?;
            fs::write(
                base.join("foreign-identity.json"),
                serde_json::to_vec(&serde_json::json!([
                    info.dev(),
                    info.ino(),
                    info.mode() & 0o7777
                ]))?,
            )?;
        }
        if edge == A::ResourceParentSync
            && scenario.starts_with("tree-")
            && scenario.ends_with("swap")
            && !hit
        {
            assert_locks(&raw)?;
            hit = true;
            let destination = raw["operations"]
                .as_array()
                .unwrap()
                .iter()
                .find(|op| op["component"] == "hooks")
                .unwrap()["path"]
                .as_str()
                .unwrap();
            let root = std::path::Path::new(destination).parent().unwrap();
            let original = if scenario == "tree-parent-swap" {
                root.parent().unwrap()
            } else {
                root
            };
            let project = base.join("fixture/project");
            fs::rename(original, project.join("detached"))?;
            if scenario == "tree-outside-swap" {
                fs::create_dir(project.join("outside"))?;
                std::os::unix::fs::symlink(project.join("outside"), original)?;
            } else {
                fs::create_dir(original)?;
            }
        }
        if scenario.starts_with("state-parent-")
            && !hit
            && ((!scenario.contains("post")
                && ((scenario.ends_with("install")
                    && !scenario.ends_with("uninstall")
                    && edge == A::StateReplace)
                    || (scenario.ends_with("uninstall") && edge == A::StateUnlink)))
                || (scenario.contains("post")
                    && ((scenario.ends_with("install")
                        && !scenario.ends_with("uninstall")
                        && edge == A::StateParentSync)
                        || (scenario.ends_with("uninstall") && edge == A::StateRemovedSync))))
        {
            assert_locks(&raw)?;
            hit = true;
            let state = PathBuf::from(raw["state_before"]["path"].as_str().unwrap());
            let parent = state.parent().unwrap();
            let outside = base.join("fixture/outside-state");
            if scenario.ends_with("install")
                && !scenario.ends_with("uninstall")
                && !scenario.contains("post")
            {
                let temporary = fs::read_dir(parent)?
                    .map(|e| e.unwrap().path())
                    .find(|p| {
                        let prefix = format!(".{}.", state.file_name().unwrap().to_string_lossy());
                        let name = p.file_name().unwrap().to_string_lossy();
                        name.starts_with(&prefix) && name.len() == prefix.len() + 16
                    })
                    .expect("synced state temporary");
                let seeded = outside.join(temporary.file_name().unwrap());
                fs::copy(&temporary, &seeded)?;
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(seeded, fs::Permissions::from_mode(0o644))?;
            }
            fs::rename(parent, base.join("fixture/detached-state"))?;
            if scenario.contains("outside") {
                std::os::unix::fs::symlink(&outside, parent)?;
            } else {
                fs::rename(&outside, parent)?;
            }
        }
        if scenario.starts_with("late-") && edge == A::JournalDurable {
            assert_locks(&raw)?;
            hit = true;
            let repository = PathBuf::from(raw["repository"].as_str().unwrap());
            match scenario.as_str() {
                "late-agent-source" | "late-tree-source" => {
                    let (suffix, bytes): (&str, &[u8]) = if scenario == "late-agent-source" {
                        (
                            "plugins/sample/agents/sample-agent.md",
                            b"late agent source",
                        )
                    } else {
                        ("plugins/sample/hooks/stop.sh", b"late hook source")
                    };
                    let path = repository.join(suffix);
                    fs::remove_file(&path)?;
                    fs::write(path, bytes)?;
                }
                "late-document" => fs::write(
                    raw["documents"][0]["before"]["path"].as_str().unwrap(),
                    b"{\"foreign\":true}\n",
                )?,
                "late-link-alias" => {
                    let operation = raw["operations"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .find(|op| op["component"] == "skills")
                        .unwrap();
                    let path = PathBuf::from(operation["path"].as_str().unwrap());
                    let link = fs::read_link(&path)?;
                    fs::remove_file(&path)?;
                    std::os::unix::fs::symlink(format!("./{}", link.display()), path)?;
                }
                "late-destination" => {
                    let operation = raw["operations"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .find(|op| op["component"] == "agents")
                        .unwrap();
                    fs::write(
                        operation["path"].as_str().unwrap(),
                        b"late foreign destination",
                    )?;
                }
                _ => unreachable!(),
            }
        }
        if scenario.starts_with("document-")
            && scenario.ends_with("swap")
            && edge == A::Publication(super::Boundary::PublishParentSync)
            && !hit
        {
            assert_locks(&raw)?;
            hit = true;
            let document = PathBuf::from(raw["documents"][0]["before"]["path"].as_str().unwrap());
            let original = document.parent().unwrap();
            let project = base.join("fixture/project");
            fs::rename(original, project.join("detached"))?;
            if scenario == "document-outside-swap" {
                fs::create_dir(project.join("outside"))?;
                std::os::unix::fs::symlink(project.join("outside"), original)?;
            } else {
                fs::create_dir(original)?;
            }
        }
        if let A::TreeStage(point) = edge {
            let suffix = match point {
                crate::bundles::TreeWriteEdge::FileWrite => "file-write",
                crate::bundles::TreeWriteEdge::FileChmod => "file-chmod",
                crate::bundles::TreeWriteEdge::FileSync => "file-sync",
                crate::bundles::TreeWriteEdge::DirectoryChmod => "directory-chmod",
                crate::bundles::TreeWriteEdge::DirectorySync => "directory-sync",
                crate::bundles::TreeWriteEdge::RootSync => "root-sync",
            };
            if !hit && scenario == format!("tree-stage-{suffix}") {
                assert_locks(&raw)?;
                hit = true;
                return Err(std::io::Error::other(format!("injected {scenario}")));
            }
        }
        let named = format!("{edge:?}");
        let fault_edge = match scenario.as_str() {
            "final-root-sync" => "FinalRootSync",
            "post-state-config-sync" => "PostStateConfigSync",
            "evidence-delete" => "ResourceEvidenceDelete",
            "tree-evidence-delete" => "TreeEvidenceDelete",
            "tree-evidence-partial" | "tree-evidence-partial-interrupt" => {
                "TreeEvidenceDeletedFile"
            }
            "file-sync" => "FileSync",
            "file-link" => "FileLink",
            "file-temp-unlink" => "FileUnlink",
            "uninstall-state-unlink" => "StateUnlink",
            "uninstall-state-sync" => "StateRemovedSync",
            "uninstall-state-interrupt" => "StateRemovedSync",
            "file-unlinked-interrupt" => "FileUnlinked",
            _ => "",
        };
        if !hit && named == fault_edge {
            assert_locks(&raw)?;
            hit = true;
            return Err(if scenario.ends_with("interrupt") {
                std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    if scenario == "file-unlinked-interrupt" {
                        "after file temporary unlink"
                    } else if scenario == "tree-evidence-partial-interrupt" {
                        "injected tree-evidence-partial-interrupt"
                    } else {
                        "after state unlink"
                    },
                )
            } else {
                std::io::Error::other(format!("injected {scenario}"))
            });
        }
        if !hit
            && matches!(
                (scenario.as_str(), edge),
                ("state-sync", A::StateSync)
                    | ("state-parent-sync", A::StateParentSync)
                    | ("journal-unlink", A::BeforeCleanup)
                    | ("journal-removed-sync", A::JournalRemovedSync)
                    | ("resource-cleanup", A::ResourceCleanup)
                    | ("resource-parent-sync", A::ResourceParentSync)
            )
        {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::other(format!("injected {scenario}")));
        }
        if edge == super::apply::ApplyBoundary::ResourceCaptured {
            captures += 1;
            if matches!(scenario.as_str(), "capture-interrupt" | "narrow-interrupt")
                && captures == 2
            {
                assert_locks(&raw)?;
                hit = true;
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    "after two mixed captures",
                ));
            }
        }
        if edge == super::apply::ApplyBoundary::ResourcePublished {
            publications += 1;
            if matches!(
                scenario.as_str(),
                "root-swap" | "parent-swap" | "outside-swap"
            ) && !hit
            {
                assert_locks(&raw)?;
                hit = true;
                let destination = PathBuf::from(raw["operations"][0]["path"].as_str().unwrap());
                let root = destination.parent().unwrap();
                let original = if scenario == "parent-swap" {
                    root.parent().unwrap()
                } else {
                    root
                };
                let project = base.join("fixture/project");
                fs::rename(original, project.join("detached"))?;
                if scenario == "outside-swap" {
                    fs::create_dir(project.join("outside"))?;
                    std::os::unix::fs::symlink(project.join("outside"), original)?;
                } else {
                    fs::create_dir(original)?;
                }
            }
            if scenario == "partial-interrupt" && publications == 2 {
                assert_locks(&raw)?;
                hit = true;
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    "after two mixed publications",
                ));
            }
        }
        if matches!(
            scenario.as_str(),
            "journal-interrupt"
                | "link-alias-interrupt"
                | "state-alias-interrupt"
                | "state-link-interrupt"
        ) && edge == super::apply::ApplyBoundary::JournalDurable
        {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "after durable journal",
            ));
        }
        if scenario == "before-state-fail" && edge == super::apply::ApplyBoundary::BeforeState {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::other("before state persistence"));
        }
        if scenario == "recovery-locks" && edge == super::apply::ApplyBoundary::BeforeRecovery {
            recovery_checked = true;
            let state = PathBuf::from(raw["state_before"]["path"].as_str().unwrap());
            let hooks = PathBuf::from(raw["roots"]["hooks"].as_str().unwrap());
            for path in [
                state.parent().unwrap().join(format!(
                    ".{}.lock",
                    state.file_name().unwrap().to_str().unwrap()
                )),
                hooks.parent().unwrap().join(".agent-workbench.lock"),
            ] {
                let lock = fs::File::open(&path)?;
                assert_eq!(
                    rustix::fs::flock(&lock, rustix::fs::FlockOperation::NonBlockingLockExclusive),
                    Err(rustix::io::Errno::WOULDBLOCK),
                    "recovery released lock: {path:?}"
                );
            }
        }
        if edge == super::apply::ApplyBoundary::BeforeState {
            state_phase = true;
        }
        if scenario == "state-replace-interrupt"
            && state_phase
            && edge == super::apply::ApplyBoundary::StateReplace
        {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "before state atomic replace",
            ));
        }
        if scenario == "state-capture-fail"
            && state_phase
            && edge == super::apply::ApplyBoundary::StateWrite
            && !hit
        {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::other("injected state capture failure"));
        }
        if edge == super::apply::ApplyBoundary::ResourcePublished
            && !hit
            && matches!(scenario.as_str(), "foreign-document" | "interrupt")
        {
            assert_locks(&raw)?;
            hit = true;
            if scenario == "foreign-document" {
                fs::write(
                    raw["documents"][0]["before"]["path"].as_str().unwrap(),
                    b"{\"foreign\":true}\n",
                )?;
                return Err(std::io::Error::other("foreign document blocks recovery"));
            }
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "interrupted apply",
            ));
        }
        if ((matches!(scenario.as_str(), "resource-fail" | "recovery-locks")
            && edge == super::apply::ApplyBoundary::ResourcePublished)
            || (scenario == "state-fail" && edge == super::apply::ApplyBoundary::BeforeCleanup))
            && !hit
        {
            assert_locks(&raw)?;
            hit = true;
            return Err(std::io::Error::other(if scenario == "state-fail" {
                "injected state publication failure"
            } else {
                "injected resource publication failure"
            }));
        }
        Ok(())
    });
    fs::write(
        base.join("result.json"),
        serde_json::to_vec(
            &{
                use crate::shared_document::snapshot::Error;
                let (class, diagnostic) = match &result {
                    Ok(()) => ("", String::new()),
                    Err(Error::Invalid(message)) => ("ValueError", message.clone()),
                    Err(Error::Io(error)) => (
                        match error.kind() {
                            std::io::ErrorKind::Interrupted => "KeyboardInterrupt",
                            std::io::ErrorKind::AlreadyExists => "FileExistsError",
                            std::io::ErrorKind::NotFound => "FileNotFoundError",
                            std::io::ErrorKind::NotADirectory => "NotADirectoryError",
                            std::io::ErrorKind::IsADirectory => "IsADirectoryError",
                            std::io::ErrorKind::PermissionDenied => "PermissionError",
                            _ => "OSError",
                        },
                        error.to_string(),
                    ),
                };
                let errno = match &result {
                    Err(Error::Io(error)) => error.raw_os_error(),
                    _ => None,
                };
                serde_json::json!({"accepted":result.is_ok(),"error_class":class,"diagnostic":diagnostic,"errno":errno,"recovery_observed":recovery_observed,"hit":if scenario == "recovery-locks" { recovery_checked } else { hit }})
            },
        )
        .unwrap(),
    )
    .unwrap();
}
