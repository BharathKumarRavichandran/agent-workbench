//! Test-only wire boundary; never exposed by the executable.
use super::*;

#[test]
#[ignore]
fn differential_state_wire() {
    let base = PathBuf::from(std::env::var_os("AW_STATE_BASE").unwrap());
    let mut results = BTreeMap::new();
    for item in fs::read_dir(base.join("inputs")).unwrap() {
        let path = item.unwrap().path();
        let result = match load(&path) {
            Ok(Some(state)) => {
                serde_json::json!({"accepted": true, "encoded": encode(&state).unwrap()})
            }
            Ok(None) => panic!("fixture unexpectedly absent: {}", path.display()),
            Err(error) => {
                serde_json::json!({"accepted": false, "error_class": "ValueError", "error": error})
            }
        };
        results.insert(
            path.file_name().unwrap().to_string_lossy().into_owned(),
            result,
        );
    }
    fs::write(
        base.join("results.json"),
        serde_json::to_string(&results).unwrap(),
    )
    .unwrap();
}
