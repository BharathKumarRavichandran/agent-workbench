//! Private E3 syscall-failure driver. Inputs are paths/fault names, never oracle plans.
use super::*;
use std::{cell::Cell, env};

thread_local! {
    static FAULT: Cell<&'static str> = const { Cell::new("") };
    static HIT: Cell<bool> = const { Cell::new(false) };
}

pub(super) fn inject(phase: &str) -> std::io::Result<()> {
    if FAULT.with(|fault| fault.get() == phase) {
        HIT.with(|hit| hit.set(true));
        Err(std::io::Error::from_raw_os_error(
            rustix::io::Errno::IO.raw_os_error(),
        ))
    } else {
        Ok(())
    }
}

#[test]
#[ignore]
fn differential_failure() {
    let base = PathBuf::from(env::var_os("AW_RENDER_BASE").unwrap());
    let fault = env::var("AW_RENDER_FAULT").unwrap();
    let fault: &'static str = Box::leak(fault.into_boxed_str());
    FAULT.with(|value| value.set(fault));
    let root = base.join("repository");
    let error = write_generated(
        &root,
        &root.join(".cursor-plugin/marketplace.json"),
        b"generated\n",
    )
    .err();
    fs::write(
        base.join("result.json"),
        serde_json::to_vec(&serde_json::json!({
            "error": error, "hit": HIT.with(Cell::get)
        }))
        .unwrap(),
    )
    .unwrap();
}
