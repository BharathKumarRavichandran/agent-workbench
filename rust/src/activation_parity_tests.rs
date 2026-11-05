//! Private failure seam around independent public activation planning.
//! Inputs are public CLI arguments, never serialized plans.
use std::{env, fs, io, path::PathBuf};

#[test]
#[ignore]
fn differential_public_activation() {
    let base = PathBuf::from(env::var_os("AW_PUBLIC_ACTIVATION_BASE").unwrap());
    let args: Vec<String> =
        serde_json::from_slice(&fs::read(base.join("args.json")).unwrap()).unwrap();
    let cli =
        crate::parse_cli(std::iter::once("agent-workbench-rs".to_owned()).chain(args)).unwrap();
    let edge = env::var("AW_PUBLIC_ACTIVATION_EDGE").unwrap();
    let mut hit = false;
    let result = crate::activation::run_with(&cli, |boundary| {
        if format!("{boundary:?}") == edge && !hit {
            hit = true;
            return Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "public activation interruption",
            ));
        }
        Ok(())
    });
    assert!(hit, "independent public plan never reached {edge}");
    let output = result.unwrap_or_else(|error| panic!("missing public error output: {error}"));
    assert_eq!(output.exit_code, 2, "interruption silently succeeded");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("public activation interruption"),
        "injected interruption diagnostic was lost"
    );
    assert!(
        !output.stdout.is_empty(),
        "pre-apply operation output was lost"
    );
    fs::write(
        base.join("result.json"),
        serde_json::to_vec(&serde_json::json!({
            "hit": hit, "exit": output.exit_code,
            "stdout": output.stdout, "stderr": String::from_utf8(output.stderr).unwrap()
        }))
        .unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore]
fn inert_native_validation() {
    let base = PathBuf::from(env::var_os("AW_PUBLIC_ACTIVATION_BASE").unwrap());
    let args: Vec<String> =
        serde_json::from_slice(&fs::read(base.join("args.json")).unwrap()).unwrap();
    let cli =
        crate::parse_cli(std::iter::once("agent-workbench-rs".to_owned()).chain(args)).unwrap();
    let policy = crate::activation::Policy::for_target(cli.target).unwrap();
    let root = fs::canonicalize(&cli.root).unwrap();
    let catalog = crate::decode_catalog(&root.join("catalog/plugins.json")).unwrap();
    let profile =
        crate::decode_profile(&root.join("profiles").join(format!("{}.json", cli.profile)))
            .unwrap();
    let components = cli.components.unwrap();
    let result =
        crate::activation::inspect_requested(policy, &root, &catalog, &profile, &components);
    assert!(
        result.is_ok(),
        "native validation unexpectedly failed: {result:?}"
    );
    fs::write(base.join("validation-result.json"), b"{\"valid\":true}").unwrap();
}
