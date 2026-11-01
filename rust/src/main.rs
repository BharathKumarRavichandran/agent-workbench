use agent_workbench_rs::{
    has_conflicts, help_requested, operations_json, parse_cli, plan, usage_text,
};
use std::process::ExitCode;

fn run(args: Vec<String>) -> Result<u8, String> {
    if help_requested(&args) {
        println!("{}", usage_text());
        return Ok(0);
    }
    let cli = parse_cli(args)?;
    let operations = plan(&cli)?;
    let conflicts = has_conflicts(&operations);
    println!("{}", operations_json(&operations)?);
    Ok(if conflicts { 1 } else { 0 })
}

fn main() -> ExitCode {
    match run(std::env::args().collect()) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
