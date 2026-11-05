//! Reviewed source update orchestration. Git is real; Python is never invoked.
use crate::*;
use std::io::Write;
use std::process::{Command, Stdio};
use validation::RenderError;

type UpdateResult<T> = std::result::Result<T, RenderError>;

pub(crate) fn requested(args: &[String]) -> bool {
    let mut args = args.iter().skip(1);
    while let Some(arg) = args.next() {
        if matches!(arg.as_str(), "--root" | "--profile") {
            args.next();
        } else if !arg.starts_with("--root=") && !arg.starts_with("--profile=") {
            return arg == "update";
        }
    }
    false
}

pub(crate) fn run(args: &[String]) -> Result<CommandOutput> {
    let mut root = PathBuf::from(".");
    let mut profile = "default".to_owned();
    let mut source = None;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--root" | "--profile" | "--source" => {
                let key = &args[index];
                index += 1;
                let value = args.get(index).ok_or("missing option value")?;
                match key.as_str() {
                    "--root" => root = value.into(),
                    "--profile" => profile = value.clone(),
                    _ => source = Some(value.clone()),
                }
            }
            "update" => {}
            other => return Err(format!("unsupported update argument: {other}")),
        }
        index += 1;
    }
    let source = source.ok_or("update requires --source")?;
    let root = canonicalize_existing(&root, "repository")?;
    match update(&root, &profile, &source) {
        Ok((before, after)) => Ok(CommandOutput {
            stdout: format!("{source}: {before} -> {after}\n"),
            stderr: Vec::new(),
            exit_code: 0,
        }),
        Err(RenderError::Report(error)) => Err(error),
        Err(RenderError::Git(stderr)) => Ok(CommandOutput {
            stdout: String::new(),
            stderr,
            exit_code: 1,
        }),
    }
}

fn command(root: &Path, args: &[&str], capture: bool) -> UpdateResult<String> {
    let mut command = Command::new("git");
    command
        .current_dir(root)
        .args(args)
        .stderr(Stdio::inherit());
    if capture {
        let output = command.output().map_err(|e| e.to_string())?;
        if !output.status.success() {
            return Err(RenderError::Git(Vec::new()));
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    } else {
        let status = command.status().map_err(|e| e.to_string())?;
        if !status.success() {
            return Err(RenderError::Git(Vec::new()));
        }
        Ok(String::new())
    }
}

fn revision(root: &Path, path: &str) -> UpdateResult<String> {
    command(root, &["-C", path, "rev-parse", "HEAD"], true)
}

fn config_failure(class: &str, message: String) -> RenderError {
    RenderError::Git(format!("configparser.{class}: {message}\n").into_bytes())
}

fn interpolate(
    value: &str,
    options: &BTreeMap<String, String>,
    section: &str,
    option: &str,
    depth: usize,
) -> UpdateResult<String> {
    let raw = options.get(option).map(String::as_str).unwrap_or(value);
    if depth > 10 {
        return Err(config_failure("InterpolationDepthError", format!(
            "Recursion limit exceeded in value substitution: option {} in section {} contains an interpolation key which cannot be substituted in 10 steps. Raw value: {}",
            state::wire::repr(option), state::wire::repr(section), state::wire::repr(raw)
        )));
    }
    let mut result = String::new();
    let mut rest = value;
    while let Some(position) = rest.find('%') {
        result.push_str(&rest[..position]);
        rest = &rest[position..];
        if let Some(suffix) = rest.strip_prefix("%%") {
            result.push('%');
            rest = suffix;
        } else if let Some(suffix) = rest.strip_prefix("%(") {
            let end = suffix
                .find(')')
                .filter(|end| *end > 0 && suffix[*end..].starts_with(")s"));
            let Some(end) = end else {
                return Err(config_failure(
                    "InterpolationSyntaxError",
                    format!(
                        "bad interpolation variable reference {}",
                        state::wire::repr(rest)
                    ),
                ));
            };
            let key = suffix[..end].to_lowercase();
            let Some(replacement) = options.get(&key) else {
                return Err(config_failure("InterpolationMissingOptionError", format!(
                    "Bad value substitution: option {} in section {} contains an interpolation key {} which is not a valid option name. Raw value: {}",
                    state::wire::repr(option), state::wire::repr(section), state::wire::repr(&key), state::wire::repr(raw)
                )));
            };
            if replacement.contains('%') {
                result.push_str(&interpolate(
                    replacement,
                    options,
                    section,
                    option,
                    depth + 1,
                )?);
            } else {
                result.push_str(replacement);
            }
            rest = &suffix[end + 2..];
        } else {
            return Err(config_failure(
                "InterpolationSyntaxError",
                format!(
                    "'%' must be followed by '%' or '(', found: {}",
                    state::wire::repr(rest)
                ),
            ));
        }
    }
    result.push_str(rest);
    Ok(result)
}

fn config_whitespace(ch: char) -> bool {
    ch.is_whitespace() || matches!(ch, '\u{1c}'..='\u{1f}')
}

fn verify_registration(root: &Path, source: &Source, path: &Path) -> UpdateResult<()> {
    let relative = path.strip_prefix(root).unwrap().to_string_lossy();
    let staged = command(root, &["ls-files", "--stage", "--", &relative], true)?;
    if !staged.starts_with("160000 ") {
        return Err(format!("source is not a registered Git submodule: {relative}").into());
    }
    let modules_path = root.join(".gitmodules");
    // ConfigParser.read ignores file I/O errors, but decoding errors escape to the CLI.
    let bytes = fs::read(&modules_path).unwrap_or_default();
    let content = validation_codec::source_text(&bytes)?
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let mut entries = Vec::<BTreeMap<String, String>>::new();
    let mut section_names = Vec::new();
    let mut defaults = BTreeMap::new();
    let mut is_default = false;
    let mut option = None::<String>;
    let mut indent = 0;
    let mut section = String::new();
    let mut seen_options = BTreeSet::new();
    let mut seen_sections = BTreeSet::new();
    let mut parsing_errors = Vec::new();
    for (offset, raw_line) in content.split_inclusive('\n').enumerate() {
        let lineno = offset + 1;
        let line = raw_line.trim_matches(config_whitespace);
        if line.starts_with(['#', ';']) {
            continue;
        }
        if line.is_empty() {
            if let Some(name) = option.as_ref().filter(|name| !name.is_empty()) {
                let entry = if is_default {
                    Some(&mut defaults)
                } else {
                    entries.last_mut()
                };
                if let Some(value) = entry.and_then(|entry| entry.get_mut(name)) {
                    value.push('\n');
                }
            }
            continue;
        }
        let current_indent = raw_line
            .chars()
            .take_while(|ch| config_whitespace(*ch))
            .count();
        if option.as_ref().is_some_and(|name| !name.is_empty()) && current_indent > indent {
            let entry = if is_default {
                &mut defaults
            } else {
                entries.last_mut().unwrap()
            };
            entry.entry(option.clone().unwrap()).and_modify(|value| {
                value.push('\n');
                value.push_str(line);
            });
            continue;
        }
        indent = current_indent;
        if line.starts_with('[') && line.rfind(']').is_some_and(|end| end > 1) {
            section = line[1..line.rfind(']').unwrap()].to_owned();
            is_default = section == "DEFAULT";
            if !is_default {
                if !seen_sections.insert(section.clone()) {
                    return Err(RenderError::Git(format!(
                        "configparser.DuplicateSectionError: While reading from PosixPath({}) [line {lineno:2}]: section {} already exists\n",
                        state::wire::repr(&modules_path.to_string_lossy()), state::wire::repr(&section)
                    ).into_bytes()));
                }
                entries.push(BTreeMap::new());
                section_names.push(section.clone());
            }
            option = None;
        } else if section.is_empty() && entries.is_empty() && !is_default {
            return Err(RenderError::Git(format!(
                "configparser.MissingSectionHeaderError: File contains no section headers.\nfile: PosixPath({}), line: {lineno}\n{}\n",
                state::wire::repr(&modules_path.to_string_lossy()),
                state::wire::repr(raw_line)
            ).into_bytes()));
        } else if let Some(position) = line.find(['=', ':']) {
            let key = line[..position]
                .trim_end_matches(config_whitespace)
                .to_lowercase();
            if key.is_empty() {
                parsing_errors.push(format!(
                    "\n\t[line {lineno:2}]: {}",
                    state::wire::repr(raw_line)
                ));
            }
            let value = line[position + 1..]
                .trim_matches(config_whitespace)
                .to_owned();
            if !seen_options.insert((section.clone(), key.clone())) {
                return Err(RenderError::Git(format!(
                    "configparser.DuplicateOptionError: While reading from PosixPath({}) [line {lineno:2}]: option {} in section {} already exists\n",
                    state::wire::repr(&modules_path.to_string_lossy()),
                    state::wire::repr(&key), state::wire::repr(&section)
                ).into_bytes()));
            }
            let entry = if is_default {
                Some(&mut defaults)
            } else {
                entries.last_mut()
            };
            if let Some(entry) = entry {
                entry.insert(key.clone(), value);
            }
            option = Some(key);
        } else {
            parsing_errors.push(format!(
                "\n\t[line {lineno:2}]: {}",
                state::wire::repr(raw_line)
            ));
        }
    }
    if !parsing_errors.is_empty() {
        return Err(RenderError::Git(
            format!(
                "configparser.ParsingError: Source contains parsing errors: PosixPath({}){}\n",
                state::wire::repr(&modules_path.to_string_lossy()),
                parsing_errors.concat()
            )
            .into_bytes(),
        ));
    }
    for value in defaults
        .values_mut()
        .chain(entries.iter_mut().flat_map(|entry| entry.values_mut()))
    {
        value.truncate(value.trim_end_matches(config_whitespace).len());
    }
    for entry in &mut entries {
        for (key, value) in &defaults {
            entry.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    let mut matches = Vec::new();
    for (entry, section) in entries.iter().zip(&section_names) {
        let value = entry.get("path").map(String::as_str).unwrap_or("");
        if interpolate(value, entry, section, "path", 1)? == relative {
            matches.push((entry, section));
        }
    }
    if matches.len() != 1 {
        return Err(format!("source has no unique .gitmodules entry: {relative}").into());
    }
    let configured = interpolate(
        matches[0].0.get("url").map(String::as_str).unwrap_or(""),
        matches[0].0,
        matches[0].1,
        "url",
        1,
    )?;
    let upstream = source.upstream.as_deref().unwrap();
    if configured != upstream {
        return Err(format!(
            "submodule upstream differs from catalog: configured {}, catalog {}",
            state::wire::repr(&configured),
            state::wire::repr(upstream)
        )
        .into());
    }
    Ok(())
}

fn validate(root: &Path, generated: bool, phase: &str) -> UpdateResult<()> {
    let (catalog, raw, profile) = validation::workspace(root, "default")?;
    let issues = validation::collect_issues(root, &catalog, &raw, &profile, generated)?;
    if !issues.is_empty() {
        let messages = issues
            .iter()
            .map(|i| format!("{}: {}", i.code, i.message))
            .collect::<Vec<_>>()
            .join("; ");
        return Err(format!("updated source failed {phase}: {messages}").into());
    }
    Ok(())
}

fn update(root: &Path, profile: &str, id: &str) -> UpdateResult<(String, String)> {
    let (catalog, _, _) = validation::workspace(root, profile)?;
    if profile != "default" {
        return Err("sources may only be updated with the default profile".into());
    }
    let source = catalog
        .sources
        .iter()
        .find(|s| s.id == id)
        .ok_or_else(|| format!("unknown source: {id}"))?;
    if source.kind != SourceKind::GitSubmodule {
        let kind = if source.kind == SourceKind::Local {
            "local"
        } else {
            "curated-copy"
        };
        return Err(format!("source {id} is not updateable: {kind}").into());
    }
    let mut path = root.to_path_buf();
    for part in Path::new(&source.path).components() {
        path.push(part);
        if path.is_symlink() {
            return Err(format!("source path contains a symlink: {}", source.path).into());
        }
    }
    let path = artifacts::resolve_catalog_path(&path)?;
    verify_registration(root, source, &path)?;
    let path_string = path.to_string_lossy();
    let before = revision(root, &path_string)?;
    let mut snapshot = Vec::new();
    for (path, _) in validation::generated_files(root)? {
        let content = if path.is_file() {
            Some(fs::read(&path).map_err(|e| validation_codec::io_error(&path, e))?)
        } else {
            None
        };
        snapshot.push((path, content));
    }
    let result = (|| {
        let reference = format!("refs/heads/{}", source.branch.as_deref().unwrap());
        command(
            root,
            &[
                "-C",
                &path_string,
                "fetch",
                "--no-tags",
                source.upstream.as_deref().unwrap(),
                &reference,
            ],
            false,
        )?;
        command(
            root,
            &["-C", &path_string, "checkout", "--detach", "FETCH_HEAD"],
            false,
        )?;
        validate(root, false, "validation")?;
        command(
            root,
            &[
                "-C",
                &path_string,
                "submodule",
                "update",
                "--init",
                "--recursive",
            ],
            false,
        )?;
        validate(root, false, "validation after nested submodule update")?;
        for (path, content) in validation::generated_files(root)? {
            let current = if path.is_file() {
                Some(fs::read(&path).map_err(|e| validation_codec::io_error(&path, e))?)
            } else {
                None
            };
            if current.as_deref() != Some(content.as_bytes()) {
                rendering::write_generated(root, &path, content.as_bytes())?;
            }
        }
        validate(root, true, "final validation")
    })();
    if let Err(error) = result {
        // A renderer captures Git stderr; emit it before rollback, just as the oracle does.
        let error = match error {
            RenderError::Git(stderr) => {
                std::io::stderr()
                    .write_all(&stderr)
                    .map_err(|e| e.to_string())?;
                RenderError::Git(Vec::new())
            }
            error => error,
        };
        let _ = command(
            root,
            &["-C", &path_string, "checkout", "--detach", &before],
            false,
        );
        let _ = command(
            root,
            &[
                "-C",
                &path_string,
                "submodule",
                "update",
                "--init",
                "--recursive",
            ],
            false,
        );
        for (path, content) in snapshot {
            if let Some(content) = content {
                rendering::write_generated(root, &path, &content)?;
            } else {
                rendering::delete_generated(root, &path)?;
            }
        }
        return Err(error);
    }
    let after = revision(root, &path_string)?;
    Ok((before, after))
}
