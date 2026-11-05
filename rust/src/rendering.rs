//! Deterministic public generated output publication.
use crate::*;
use rustix::fs::{self as rfs, AtFlags, FileType, Mode, OFlags};
use std::io::{Read, Write};

#[cfg(test)]
mod parity_tests;

pub(crate) fn requested(args: &[String]) -> bool {
    let mut args = args.iter().skip(1);
    while let Some(arg) = args.next() {
        if matches!(arg.as_str(), "--root" | "--profile") {
            args.next();
        } else if !arg.starts_with("--root=") && !arg.starts_with("--profile=") {
            return arg == "render";
        }
    }
    false
}

pub(crate) fn run(args: &[String]) -> Result<CommandOutput> {
    let mut root = PathBuf::from(".");
    let mut profile = "default".to_owned();
    let mut check = false;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--root" | "--profile" => {
                let key = &args[index];
                index += 1;
                let value = args.get(index).ok_or("missing option value")?;
                if key == "--root" {
                    root = value.into();
                } else {
                    profile = value.clone();
                }
            }
            "render" => {}
            "--check" => check = true,
            other => return Err(format!("unsupported render argument: {other}")),
        }
        index += 1;
    }
    let root = canonicalize_existing(&root, "repository")?;
    validation::workspace(&root, &profile)?;
    if profile != "default" {
        return Err(
            "canonical generated files may only be rendered with the default profile".into(),
        );
    }
    let generated = match validation::generated_files(&root) {
        Ok(files) => files,
        Err(validation::RenderError::Report(error)) => return Err(error),
        Err(validation::RenderError::Git(stderr)) => {
            return Ok(CommandOutput {
                stdout: String::new(),
                stderr,
                exit_code: 1,
            })
        }
    };
    let mut stale = Vec::new();
    for (path, content) in generated {
        let current = if path.is_file() {
            Some(fs::read(&path).map_err(|e| validation_codec::io_error(&path, e))?)
        } else {
            None
        };
        if current.as_deref() == Some(content.as_bytes()) {
            continue;
        }
        if !check {
            write_generated(&root, &path, content.as_bytes())?;
        }
        stale.push(path);
    }
    let stdout = stale
        .iter()
        .map(|path| {
            format!(
                "{} {}\n",
                if check { "stale" } else { "wrote" },
                path.strip_prefix(&root).unwrap().display()
            )
        })
        .collect();
    Ok(CommandOutput {
        stdout,
        stderr: Vec::new(),
        exit_code: u8::from(check && !stale.is_empty()),
    })
}

fn fault(phase: &str) -> std::io::Result<()> {
    #[cfg(test)]
    parity_tests::inject(phase)?;
    #[cfg(not(test))]
    let _ = phase;
    Ok(())
}

fn publication_error(error: std::io::Error, paths: &[&Path]) -> String {
    let number = error.raw_os_error().unwrap_or(0);
    let native = error.to_string();
    let suffix = format!(" (os error {number})");
    let message = native.strip_suffix(&suffix).unwrap_or(&native);
    let mut result = format!("[Errno {number}] {message}");
    for (index, path) in paths.iter().enumerate() {
        result.push_str(if index == 0 { ": " } else { " -> " });
        result.push_str(&state::wire::repr(&path.to_string_lossy()));
    }
    result
}

fn write_generated(root: &Path, output: &Path, content: &[u8]) -> Result<()> {
    let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW;
    let mut parent = fault("root-open")
        .and_then(|_| rfs::open(root, flags, Mode::empty()).map_err(Into::into))
        .map_err(|e| publication_error(e, &[root]))?;
    let relative = output
        .strip_prefix(root)
        .map_err(|_| "generated output escapes repository")?;
    for part in relative
        .parent()
        .ok_or("output has no parent")?
        .components()
    {
        let name = part.as_os_str();
        let info = match rfs::statat(&parent, name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(info) => info,
            Err(rustix::io::Errno::NOENT) => {
                fault("mkdir")
                    .and_then(|_| {
                        rfs::mkdirat(&parent, name, Mode::from_raw_mode(0o755)).map_err(Into::into)
                    })
                    .map_err(|e| publication_error(e, &[Path::new(name)]))?;
                rfs::statat(&parent, name, AtFlags::SYMLINK_NOFOLLOW)
                    .map_err(|e| publication_error(e.into(), &[Path::new(name)]))?
            }
            Err(e) => return Err(publication_error(e.into(), &[Path::new(name)])),
        };
        if !FileType::from_raw_mode(info.st_mode).is_dir() {
            return Err(format!(
                "generated output has a non-directory parent: {}",
                output.display()
            ));
        }
        parent = fault("parent-open")
            .and_then(|_| rfs::openat(&parent, name, flags, Mode::empty()).map_err(Into::into))
            .map_err(|e| publication_error(e, &[Path::new(name)]))?;
    }
    let name = relative.file_name().ok_or("output has no filename")?;
    let mut random = [0u8; 8];
    fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut random))
        .map_err(|e| e.to_string())?;
    let nonce: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
    let temporary = format!(".{}.{nonce}", name.to_string_lossy());
    let fd = fault("temp-open")
        .and_then(|_| {
            rfs::openat(
                &parent,
                &temporary,
                OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL,
                Mode::from_raw_mode(0o600),
            )
            .map_err(Into::into)
        })
        .map_err(|e| publication_error(e, &[Path::new(&temporary)]))?;
    let result = (|| {
        let mut file = fs::File::from(fd);
        fault("write")
            .and_then(|_| file.write_all(content))
            .and_then(|_| file.flush())
            .and_then(|_| fault("fsync"))
            .and_then(|_| file.sync_all())
            .map_err(|e| publication_error(e, &[]))?;
        drop(file);
        match rfs::statat(&parent, name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(info) if !FileType::from_raw_mode(info.st_mode).is_file() => {
                return Err(format!(
                    "generated output may only replace a regular file: {}",
                    output.display()
                ))
            }
            Err(e) if e != rustix::io::Errno::NOENT => {
                return Err(publication_error(e.into(), &[Path::new(name)]))
            }
            _ => {}
        }
        fault("rename")
            .and_then(|_| rfs::renameat(&parent, &temporary, &parent, name).map_err(Into::into))
            .map_err(|e| publication_error(e, &[Path::new(&temporary), Path::new(name)]))
    })();
    if result.is_err() {
        let _ = rfs::unlinkat(&parent, &temporary, AtFlags::empty());
    }
    result
}
