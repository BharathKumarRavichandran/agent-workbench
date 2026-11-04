//! Private real-apply driver; syscall faults live in disposable external observers.
//! macOS uses dyld interposition; Linux follows libtest's cloned test thread with
//! ptrace, observing the configured rustix backend without production flags.
use std::{env, fs, path::PathBuf};

#[test]
#[ignore]
fn differential_capture() {
    let base = PathBuf::from(env::var_os("AW_CAPTURE_BASE").unwrap());
    let raw: serde_json::Value =
        serde_json::from_slice(&fs::read(base.join("request.json")).unwrap()).unwrap();
    let marker = base.join("phase");
    fs::write(&marker, "none").unwrap();
    let mode = env::var("AW_CAPTURE_MODE").unwrap_or_default();
    let mut interrupted = false;
    let mut captures = 0;
    let mut discards = 0;
    let mut recovery_observed = false;
    let result = if mode == "probe" {
        // A GREEN zero-call audit is valid only if this exact rustix backend
        // really passes through the disposable syscall interceptor.
        fs::write(&marker, "capture").unwrap();
        let root = PathBuf::from(env::var_os("AW_CAPTURE_ROOT").unwrap());
        let fd = crate::bundles::open_directory(&root).unwrap();
        rustix::fs::fsync(&fd).map_err(|e| crate::shared_document::snapshot::Error::Io(e.into()))
    } else if mode == "recover" {
        let request = super::recovery::RecoveryRequest::new(
            PathBuf::from(raw["state_before"]["path"].as_str().unwrap()),
            raw["target"].as_str().unwrap(),
            serde_json::from_value(raw["roots"].clone()).unwrap(),
        )
        .unwrap();
        super::recovery::recover(&request, |_| Ok(()))
    } else {
        let operation_paths: Vec<PathBuf> = raw["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|op| PathBuf::from(op["path"].as_str().unwrap()))
            .collect();
        let root = PathBuf::from(env::var_os("AW_CAPTURE_ROOT").unwrap());
        let mut resource_index = 0;
        let request = serde_json::from_value(raw).unwrap();
        super::apply::apply(&request, |edge| {
            use super::apply::ApplyBoundary as A;
            match edge {
                A::BeforeResource => {
                    resource_index += 1;
                    fs::write(&marker, "capture")?;
                }
                A::ResourceCaptured => {
                    captures += 1;
                    fs::write(&marker, "none")?;
                    if mode == "interrupt"
                        && !interrupted
                        && operation_paths[resource_index - 1].parent() == Some(root.as_path())
                    {
                        interrupted = true;
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::Interrupted,
                            "capture interruption",
                        ));
                    }
                }
                A::ResourceEvidenceDelete => {
                    discards += 1;
                    fs::write(&marker, "discard")?;
                }
                A::ResourceStage | A::ResourcePublish | A::BeforeActivate => {
                    fs::write(&marker, "none")?
                }
                A::BeforeRecovery => {
                    recovery_observed = true;
                    fs::write(&marker, "none")?;
                }
                _ => (),
            }
            Ok(())
        })
    };
    fs::write(&marker, "none").unwrap();
    let (class, errno) = match &result {
        Ok(()) => ("", None),
        Err(crate::shared_document::snapshot::Error::Io(e)) => (
            if e.kind() == std::io::ErrorKind::Interrupted {
                "KeyboardInterrupt"
            } else {
                "OSError"
            },
            e.raw_os_error(),
        ),
        Err(_) => ("ValueError", None),
    };
    fs::write(
        base.join("result.json"),
        serde_json::to_vec(&serde_json::json!({
            "accepted": result.is_ok(), "class": class, "errno": errno,
            "interrupted": interrupted, "recovery_observed": recovery_observed,
            "captures": captures, "discards": discards,
            "detail": format!("{result:?}")
        }))
        .unwrap(),
    )
    .unwrap();
}
