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
    assert!(result.is_err(), "interruption silently succeeded");
    fs::write(
        base.join("result.json"),
        serde_json::to_vec(&serde_json::json!({
            "hit": hit, "error": result.err().unwrap()
        }))
        .unwrap(),
    )
    .unwrap();
}
