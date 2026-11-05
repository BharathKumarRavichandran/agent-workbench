use agent_workbench_rs::execute;
use std::process::ExitCode;

fn main() -> ExitCode {
    match execute(std::env::args().collect()) {
        Ok(output) => {
            print!("{}", output.stdout);
            use std::io::Write;
            std::io::stderr()
                .write_all(&output.stderr)
                .expect("write stderr");
            ExitCode::from(output.exit_code)
        }
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
