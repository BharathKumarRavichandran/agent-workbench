use agent_workbench_rs::execute;
use std::process::ExitCode;

fn main() -> ExitCode {
    match execute(std::env::args().collect()) {
        Ok(output) => {
            print!("{}", output.stdout);
            ExitCode::from(output.exit_code)
        }
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
