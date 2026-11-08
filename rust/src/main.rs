use agent_workbench::execute;
use std::io::{IsTerminal, Write};
use std::process::ExitCode;

fn unbuffered() -> bool {
    let Some(value) = std::env::var_os("PYTHONUNBUFFERED") else {
        return false;
    };
    if value.is_empty() {
        return false;
    }
    // CPython accepts a complete decimal zero (with leading C whitespace and
    // an optional sign) as false; any other nonempty value enables -u.
    let text = value.to_string_lossy();
    let text = text.trim_start_matches(|ch: char| ch.is_ascii_whitespace());
    let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
    digits.is_empty() || !digits.bytes().all(|byte| byte == b'0')
}

struct BufferedStdout<W> {
    writer: W,
    text: Vec<u8>,
    binary: Vec<u8>,
    binary_size: usize,
}

impl<W: Write> BufferedStdout<W> {
    fn text(&mut self, value: &str) -> std::io::Result<()> {
        self.text.extend_from_slice(value.as_bytes());
        if self.text.len() >= 8192 {
            self.flush_text()?;
        }
        Ok(())
    }

    fn record(&mut self, value: &str) -> std::io::Result<()> {
        if let Some(body) = value.strip_suffix('\n') {
            self.text(body)?;
            self.text("\n")
        } else {
            self.text(value)
        }
    }

    fn flush_text(&mut self) -> std::io::Result<()> {
        if self.text.len() > self.binary_size - self.binary.len() {
            self.writer.write_all(&self.binary)?;
            self.writer.flush()?;
            self.binary.clear();
        }
        if self.text.len() > self.binary_size {
            self.writer.write_all(&self.text)?;
            self.writer.flush()?;
        } else {
            self.binary.extend_from_slice(&self.text);
        }
        self.text.clear();
        Ok(())
    }

    fn finish(mut self) -> std::io::Result<()> {
        self.flush_text()?;
        self.writer.write_all(&self.binary)?;
        self.writer.flush()
    }
}

fn main() -> ExitCode {
    match execute(std::env::args().collect()) {
        Ok(output) => {
            let stdout = std::io::stdout();
            if stdout.is_terminal() || unbuffered() {
                stdout
                    .lock()
                    .write_all(output.stdout.as_bytes())
                    .expect("write stdout");
                std::io::stderr()
                    .write_all(&output.stderr)
                    .expect("write stderr");
            } else {
                // Match the oracle's separate text and binary buffering layers,
                // including large single JSON prints and per-operation text.
                let mut buffered = BufferedStdout {
                    writer: stdout.lock(),
                    text: Vec::new(),
                    binary: Vec::new(),
                    binary_size: rustix::fs::fstat(&stdout)
                        .ok()
                        .filter(|info| info.st_blksize > 1)
                        .map_or(8192, |info| info.st_blksize as usize),
                };
                if output.stdout_single_write {
                    buffered.record(&output.stdout).expect("write stdout");
                } else {
                    for line in output.stdout.split_inclusive('\n') {
                        buffered.record(line).expect("write stdout");
                    }
                }
                std::io::stderr()
                    .write_all(&output.stderr)
                    .expect("write stderr");
                buffered.finish().expect("flush stdout");
            }
            ExitCode::from(output.exit_code)
        }
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
