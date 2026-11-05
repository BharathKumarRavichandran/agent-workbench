//! Public inert workspace validation. No lifecycle mutation or payload execution.
use crate::*;

#[cfg(test)]
mod tests {
    #[test]
    fn validate_as_an_option_value_is_not_a_command() {
        for key in ["--root", "--profile"] {
            let args = ["aw", key, "validate", "plan", "--target", "hermes"].map(str::to_owned);
            assert!(!super::requested(&args));
        }
    }
}

pub(crate) fn requested(args: &[String]) -> bool {
    let mut args = args.iter().skip(1);
    while let Some(arg) = args.next() {
        if matches!(arg.as_str(), "--root" | "--profile") {
            args.next();
            continue;
        }
        if arg.starts_with("--root=") || arg.starts_with("--profile=") {
            continue;
        }
        return arg == "validate";
    }
    false
}

pub(crate) fn run(args: &[String]) -> Result<CommandOutput> {
    let mut root = PathBuf::from(".");
    let mut profile = "default".to_owned();
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
            "validate" | "--json" | "--no-generated-check" => {}
            other => return Err(format!("unsupported validate argument: {other}")),
        }
        index += 1;
    }
    let root = canonicalize_existing(&root, "repository")?;
    let (catalog, raw, profile) = workspace(&root, &profile)?;
    let mut issues = Vec::new();
    let mut targets = BTreeSet::new();
    if profile
        .targets
        .iter()
        .any(|target| !targets.insert(&target.id))
    {
        issues.push(Issue::new(
            "PROFILE_TARGET_DUPLICATE",
            format!("profile:{}", profile.id),
            "profile target ids must be unique",
        ));
    }
    for target in &profile.targets {
        if SkillsTarget::parse(&target.id).is_err() {
            issues.push(Issue::new(
                "PROFILE_TARGET_UNKNOWN",
                format!("profile:{}", profile.id),
                format!("unknown install target: {}", target.id),
            ));
        }
    }
    let inspection =
        artifacts::inspect_decoded_with(&root, &catalog, &raw.inspection_wire(), inspect_artifact)?;
    for (code, _, location, message) in &inspection.issues {
        issues.push(Issue::new(code, location, message));
    }
    for plugin in &catalog.plugins {
        if !inspection.plugins.contains(&plugin.id) {
            continue;
        }
        if let Some(notice) = plugin.provenance.as_ref().and_then(|p| p.notice.as_ref()) {
            let path = artifacts::resolve_catalog_path(&root.join(notice))?;
            if !path.is_file() {
                issues.push(Issue::new(
                    "PROVENANCE_NOTICE_MISSING",
                    path.display().to_string(),
                    "provenance notice does not exist",
                ));
            }
        }
    }
    for agent in ["cursor", "claude"] {
        for plugin in &catalog.plugins {
            if let Some(package) = package(&root, &catalog, plugin, agent)? {
                let path = package.join(format!(".{agent}-plugin/plugin.json"));
                let prefix = agent.to_uppercase();
                if !path.is_file() {
                    issues.push(Issue::new(
                        format!("{prefix}_MANIFEST_MISSING"),
                        path.display().to_string(),
                        format!(
                            "{} plugin manifest is missing",
                            if agent == "cursor" {
                                "Cursor"
                            } else {
                                "Claude"
                            }
                        ),
                    ));
                } else {
                    match manifest(&package, agent) {
                        Err(error) => issues.push(Issue::new(
                            format!("{prefix}_MANIFEST_INVALID"),
                            path.display().to_string(),
                            error,
                        )),
                        Ok(data)
                            if data.get("name").string().as_deref() != Some(plugin.id.as_str()) =>
                        {
                            issues.push(Issue::new(
                                format!("{prefix}_NAME_MISMATCH"),
                                path.display().to_string(),
                                format!("manifest name must be {}", plugin.id),
                            ))
                        }
                        _ => {}
                    }
                }
            }
        }
    }
    let mut names = BTreeMap::new();
    for (_, inventory) in &inspection.inventories {
        if inventory.kind != "agent-skills" {
            continue;
        }
        for (name, relative) in &inventory.components {
            let path = inventory.root.join(relative);
            if let Some(previous) = names.get(name) {
                issues.push(Issue::new(
                    "SKILL_NAME_DUPLICATE",
                    path.display().to_string(),
                    format!(
                        "skill {} also exists at {}",
                        state::wire::repr(name),
                        previous
                    ),
                ));
            } else {
                names.insert(name, path.display().to_string());
            }
        }
    }
    if !args.iter().any(|arg| arg == "--no-generated-check") {
        match generated_check(&root) {
            Ok(stale) => {
                for path in stale {
                    issues.push(Issue::new(
                        "GENERATED_FILE_STALE",
                        path.display().to_string(),
                        "generated file differs from catalog; run render",
                    ));
                }
            }
            Err(RenderError::Git(stderr)) => {
                return Ok(CommandOutput {
                    stdout: String::new(),
                    stderr,
                    exit_code: 1,
                })
            }
            Err(RenderError::Report(error)) => issues.push(Issue::new(
                "RENDER_FAILED",
                root.display().to_string(),
                error,
            )),
        }
    }
    let exit_code = if issues.is_empty() { 0 } else { 1 };
    let stdout = if args.iter().any(|arg| arg == "--json") {
        ascii_json(&serde_json::to_string_pretty(&issues).map_err(|e| e.to_string())?) + "\n"
    } else if issues.is_empty() {
        let selected: BTreeSet<_> = catalog
            .plugins
            .iter()
            .filter(|p| selected(&profile, &p.id))
            .map(|p| &p.id)
            .collect();
        let mut counts = BTreeMap::<&str, usize>::new();
        for (id, inventory) in &inspection.inventories {
            if selected.contains(id) && !inventory.components.is_empty() {
                *counts.entry(&inventory.kind).or_default() += inventory.components.len();
            }
        }
        let summary = counts
            .iter()
            .map(|(kind, count)| format!("{kind}={count}"))
            .collect::<Vec<_>>()
            .join(", ");
        format!(
            "validated {} plugins: {}\n",
            selected.len(),
            if summary.is_empty() {
                "no components"
            } else {
                &summary
            }
        )
    } else {
        issues
            .iter()
            .map(|issue| {
                format!(
                    "{}: {}: {}: {}\n",
                    issue.severity, issue.code, issue.location, issue.message
                )
            })
            .collect()
    };
    Ok(CommandOutput {
        stdout,
        stderr: Vec::new(),
        exit_code,
    })
}

fn ascii_json(text: &str) -> String {
    let mut result = String::new();
    for ch in text.chars() {
        if ch.is_ascii() {
            result.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                result.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    result
}

fn selected(profile: &Profile, id: &str) -> bool {
    !profile.plugins.exclude.iter().any(|p| p == id)
        && (profile.plugins.include == ["*"] || profile.plugins.include.iter().any(|p| p == id))
}

fn contained(root: &Path, value: &str) -> Result<PathBuf> {
    let path = artifacts::resolve_catalog_path(&root.join(value))?;
    if !path.starts_with(root) {
        return Err(format!(
            "repository path escapes checkout: {}",
            state::wire::repr(value)
        ));
    }
    Ok(path)
}

pub(crate) fn workspace(
    root: &Path,
    id: &str,
) -> Result<(Catalog, validation_json::Wire, Profile)> {
    if validate_id(id, "profile id").is_err() {
        return Err(format!("profile id is invalid: {}", state::wire::repr(id)));
    }
    let profiles = root.join("profiles");
    let profile_path = artifacts::resolve_catalog_path(&profiles.join(format!("{id}.json")))?;
    if profile_path.parent() != Some(artifacts::resolve_catalog_path(&profiles)?.as_path()) {
        return Err(format!(
            "profile id escapes profiles directory: {}",
            state::wire::repr(id)
        ));
    }
    let (catalog, raw) = validation_codec::catalog(&root.join("catalog/plugins.json"))?;
    let profile = validation_codec::profile(&profile_path)?;
    if profile.id != id {
        return Err(format!(
            "profile id {} does not match requested profile {}",
            state::wire::repr(&profile.id),
            state::wire::repr(id)
        ));
    }
    if catalog
        .sources
        .iter()
        .map(|s| &s.id)
        .collect::<BTreeSet<_>>()
        .len()
        != catalog.sources.len()
    {
        return Err("source ids must be unique".into());
    }
    if catalog
        .plugins
        .iter()
        .map(|p| &p.id)
        .collect::<BTreeSet<_>>()
        .len()
        != catalog.plugins.len()
    {
        return Err("plugin ids must be unique".into());
    }
    let known: BTreeSet<_> = catalog.plugins.iter().map(|p| p.id.as_str()).collect();
    for (values, verb) in [
        (&profile.plugins.exclude, "excludes"),
        (&profile.plugins.include, "includes"),
    ] {
        if verb == "includes" && values == &["*"] {
            continue;
        }
        let unknown: BTreeSet<_> = values
            .iter()
            .filter(|v| !known.contains(v.as_str()))
            .map(String::as_str)
            .collect();
        if !unknown.is_empty() {
            return Err(format!(
                "profile {verb} unknown plugins: {}",
                unknown.into_iter().collect::<Vec<_>>().join(", ")
            ));
        }
    }
    for plugin in &catalog.plugins {
        if !selected(&profile, &plugin.id) {
            continue;
        }
        let source = catalog
            .sources
            .iter()
            .find(|s| s.id == plugin.source)
            .ok_or_else(|| {
                format!(
                    "plugin {} references unknown source {}",
                    plugin.id, plugin.source
                )
            })?;
        let source_root = contained(root, &source.path)?;
        if !source_root.exists() {
            return Err(format!(
                "source path does not exist for {}: {}",
                source.id, source.path
            ));
        }
        for artifact in plugin.artifacts.values() {
            contained(&source_root, &artifact.path)?;
        }
    }
    Ok((catalog, raw, profile))
}

fn package(
    root: &Path,
    catalog: &Catalog,
    plugin: &Plugin,
    agent: &str,
) -> Result<Option<PathBuf>> {
    let Some(artifact) = plugin.artifacts.get(&format!("{agent}-plugin")) else {
        return Ok(None);
    };
    let Some(source) = catalog.sources.iter().find(|s| s.id == plugin.source) else {
        return Ok(None);
    };
    Ok(Some(artifacts::resolve_catalog_path(
        &root.join(&source.path).join(&artifact.path),
    )?))
}

fn manifest(package: &Path, agent: &str) -> Result<validation_json::Wire> {
    let native = package.join(format!(".{agent}-plugin/plugin.json"));
    let path = if native.exists() || native.is_symlink() {
        native
    } else {
        package.join("plugin.json")
    };
    if !path.exists() && !path.is_symlink() {
        return Err(format!(
            "[Errno 2] No such file or directory: {}",
            state::wire::repr(&path.display().to_string())
        ));
    }
    let mut current = package.to_path_buf();
    for part in path
        .strip_prefix(package)
        .map_err(|_| "manifest escapes artifact root")?
        .components()
    {
        current.push(part);
        let metadata = fs::symlink_metadata(&current)
            .map_err(|error| validation_codec::io_error(&current, error))?;
        if metadata.file_type().is_symlink() {
            return Err(format!(
                "manifest path may not contain symlinks: {}",
                current.display()
            ));
        }
    }
    if !path.is_file() {
        return Err(format!(
            "manifest must be a regular file: {}",
            path.display()
        ));
    }
    let data = validation_codec::read_value(&path)?;
    data.object("")
        .map_err(|_| format!("manifest must be an object: {}", path.display()))?;
    for field in ["name", "description"] {
        if data.get(field).text().is_none_or(|text| text.0.is_empty()) {
            return Err(format!(
                "manifest {field} must be a non-empty string: {}",
                path.display()
            ));
        }
    }
    Ok(data)
}

// Validation-only JSON inventory decoding. Keep lifecycle compilation on its
// existing strict decoder; these foreign values are inert data, never payloads.
fn inspect_artifact(repository: &Path, kind: &str, root: &Path) -> Result<artifacts::Inventory> {
    use validation_json::Wire;
    if !matches!(kind, "hooks" | "cursor-hooks" | "claude-hooks" | "mcp") {
        return artifacts::inspect(repository, kind, root);
    }
    let path = if kind == "mcp" {
        root.to_path_buf()
    } else {
        artifacts::validate_tree(root)?;
        root.join("hooks.json")
    };
    let bytes = lifecycle::read_regular(repository, &path)?;
    let text = validation_codec::source_text(&bytes)?
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let data = validation_json::parse(&text)?;
    data.object("")
        .map_err(|_| format!("JSON document must be an object: {}", path.display()))?;
    let mut inventory = artifacts::Inventory {
        kind: kind.into(),
        root: root.into(),
        components: Vec::new(),
        representation: None,
    };
    if kind == "mcp" {
        let servers = data.get("mcpServers").object("").map_err(|_| {
            format!(
                "MCP document requires an mcpServers object: {}",
                path.display()
            )
        })?;
        let mut ordered = servers.iter().collect::<Vec<_>>();
        ordered.sort_by(|a, b| a.0.cmp(&b.0));
        for (name, server) in &ordered {
            if !matches!(server, Wire::Object(_)) {
                continue;
            }
            if name.0.is_empty()
                || name.0 == [46]
                || name.0 == [46, 46]
                || name.0.iter().any(|n| matches!(n, 0 | 47 | 92))
            {
                return Err(format!(
                    "component name must be one safe filename component: {}: {}",
                    path.display(),
                    name.repr()
                ));
            }
        }
        if servers.iter().any(|(_, v)| !matches!(v, Wire::Object(_))) {
            return Err(format!(
                "MCP servers must map names to objects: {}",
                path.display()
            ));
        }
        // Only counts/kinds are consumed from non-skill inventories by validate.
        // The actual lossless names above govern ordering and diagnostics.
        for (name, _) in ordered {
            inventory.components.push((
                name.diagnostic(),
                root.file_name().unwrap().to_string_lossy().into(),
            ));
        }
    } else {
        let events = data
            .get("hooks")
            .object("")
            .map_err(|_| format!("hooks.json requires a hooks object: {}", path.display()))?;
        // Classification reads only object/array/string types, emptiness, the
        // ASCII 'command' discriminator, and integer version 1. Its projection
        // cannot execute or publish foreign values and keeps these properties.
        inventory.representation = Some(
            if activation::classify_hooks(&data.inspection_wire(), kind)? {
                "hook-bundle"
            } else {
                "claude-hook-bundle"
            },
        );
        let mut events = events.iter().collect::<Vec<_>>();
        events.sort_by(|a, b| a.0.cmp(&b.0));
        for (event, entries) in events {
            if let Wire::Array(entries) = entries {
                for index in 0..entries.len() {
                    inventory.components.push((
                        format!("{}:{index}", event.diagnostic()),
                        "hooks.json".into(),
                    ));
                }
            }
        }
    }
    Ok(inventory)
}

// Internal read-only renderer dependency for validate; public render remains E3.
pub(crate) enum RenderError {
    Report(String),
    Git(Vec<u8>),
}
impl From<String> for RenderError {
    fn from(value: String) -> Self {
        Self::Report(value)
    }
}
impl From<&str> for RenderError {
    fn from(value: &str) -> Self {
        Self::Report(value.into())
    }
}
fn generated_check(root: &Path) -> std::result::Result<Vec<PathBuf>, RenderError> {
    let mut stale = Vec::new();
    for (path, content) in generated_files(root)? {
        if !path.is_file() || fs::read(&path).map_err(|e| e.to_string())? != content.as_bytes() {
            stale.push(path);
        }
    }
    Ok(stale)
}

pub(crate) fn generated_files(
    root: &Path,
) -> std::result::Result<Vec<(PathBuf, String)>, RenderError> {
    let (catalog, raw, profile) = workspace(root, "default")?;
    let mut generated = Vec::new();
    for agent in ["cursor", "claude"] {
        let mut plugins = Vec::new();
        let mut descriptions = Vec::new();
        for plugin in &catalog.plugins {
            if profile.plugins.exclude.contains(&plugin.id)
                || !(profile.plugins.include == ["*"]
                    || profile.plugins.include.contains(&plugin.id))
            {
                continue;
            }
            if let Some(package) = package(root, &catalog, plugin, agent)? {
                let data = manifest(&package, agent)?;
                let source = package
                    .strip_prefix(root)
                    .map_err(|_| "generated package outside repository")?
                    .to_string_lossy()
                    .into_owned();
                descriptions.push(data.get("description").clone());
                plugins.push(RenderedPlugin {
                    name: &plugin.id,
                    source: if agent == "claude" {
                        format!("./{source}")
                    } else {
                        source
                    },
                    description: String::new(),
                });
            }
        }
        let document = RenderedMarketplace {
            name: &catalog.marketplace.name,
            owner: RenderedOwner {
                name: &catalog.marketplace.owner.name,
            },
            metadata: (agent == "cursor").then_some(RenderedMetadata {
                description: &catalog.marketplace.description,
            }),
            plugins,
        };
        let mut document =
            validation_json::parse(&serde_json::to_string(&document).map_err(|e| e.to_string())?)?;
        document.set("name", raw.get("marketplace").get("name").clone());
        document.set("owner", raw.get("marketplace").get("owner").clone());
        if agent == "cursor" {
            let mut metadata = validation_json::Wire::Object(Vec::new());
            metadata.set(
                "description",
                raw.get("marketplace").get("description").clone(),
            );
            document.set("metadata", metadata);
        }
        if let validation_json::Wire::Array(mut entries) = document.get("plugins").clone() {
            for (entry, description) in entries.iter_mut().zip(descriptions) {
                entry.set("description", description);
            }
            document.set("plugins", validation_json::Wire::Array(entries));
        }
        let path = root.join(format!(".{agent}-plugin/marketplace.json"));
        let mut current = root.to_path_buf();
        for part in path.strip_prefix(root).unwrap().components() {
            current.push(part);
            if current.is_symlink() {
                return Err(format!(
                    "generated output has a symlinked output path: {}",
                    current.display()
                )
                .into());
            }
        }
        generated.push((path, document.pretty()?));
    }
    let inspection =
        artifacts::inspect_decoded_with(root, &catalog, &raw.inspection_wire(), inspect_artifact)?;
    if !inspection.issues.is_empty() {
        return Err("catalog artifact inspection failed".into());
    }
    let mut sources = Vec::new();
    for source in &catalog.sources {
        let revision = if source.kind == SourceKind::GitSubmodule {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(contained(root, &source.path)?)
                .args(["rev-parse", "HEAD"])
                .output()
                .map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(RenderError::Git(output.stderr));
            }
            Some(String::from_utf8_lossy(&output.stdout).trim().to_owned())
        } else {
            None
        };
        sources.push(LockSource {
            id: &source.id,
            kind: match source.kind {
                SourceKind::Local => "local",
                SourceKind::CuratedCopy => "curated-copy",
                SourceKind::GitSubmodule => "git-submodule",
            },
            path: &source.path,
            upstream: &source.upstream,
            branch: &source.branch,
            revision,
        });
    }
    let mut plugins = Vec::new();
    let mut versions = Vec::new();
    for plugin in &catalog.plugins {
        if !selected(&profile, &plugin.id) {
            continue;
        }
        let mut version = validation_json::Wire::Scalar(serde_json::Value::Null);
        for agent in ["cursor", "claude"] {
            if let Some(package) = package(root, &catalog, plugin, agent)? {
                version = manifest(&package, agent)?.get("version").clone();
                if truthy(&version) {
                    break;
                }
            }
        }
        let mut components = BTreeMap::new();
        for (id, inventory) in &inspection.inventories {
            if id == &plugin.id && !inventory.components.is_empty() {
                components.insert(inventory.kind.clone(), inventory.components.len());
            }
        }
        let source = catalog
            .sources
            .iter()
            .find(|s| s.id == plugin.source)
            .unwrap();
        let source_root = contained(root, &source.path)?;
        let mut artifacts = BTreeMap::new();
        for (kind, artifact) in &plugin.artifacts {
            artifacts.insert(
                kind,
                contained(&source_root, &artifact.path)?
                    .strip_prefix(root)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .into_owned(),
            );
        }
        versions.push(version);
        plugins.push(LockPlugin {
            id: &plugin.id,
            source: &source.id,
            version: serde_json::Value::Null,
            skills: components.get("agent-skills").copied().unwrap_or(0),
            components,
            artifacts,
        });
    }
    let lock = LockDocument {
        schema_version: 2,
        marketplace: &catalog.marketplace.id,
        sources,
        plugins,
    };
    let path = root.join("catalog/lock.json");
    let mut current = root.to_path_buf();
    for part in path.strip_prefix(root).unwrap().components() {
        current.push(part);
        if current.is_symlink() {
            return Err(format!(
                "generated output has a symlinked output path: {}",
                current.display()
            )
            .into());
        }
    }
    let mut lock =
        validation_json::parse(&serde_json::to_string(&lock).map_err(|e| e.to_string())?)?;
    if let validation_json::Wire::Array(mut entries) = lock.get("plugins").clone() {
        for (entry, version) in entries.iter_mut().zip(versions) {
            entry.set("version", version);
        }
        lock.set("plugins", validation_json::Wire::Array(entries));
    }
    generated.push((path, lock.pretty()?));
    Ok(generated)
}
fn truthy(value: &validation_json::Wire) -> bool {
    use validation_json::Wire;
    match value {
        Wire::Array(values) => !values.is_empty(),
        Wire::Object(fields) => !fields.is_empty(),
        Wire::Scalar(serde_json::Value::Null) => false,
        Wire::Scalar(serde_json::Value::Bool(v)) => *v,
        Wire::String(v) => !v.0.is_empty(),
        Wire::Scalar(serde_json::Value::Number(v)) => v.as_f64() != Some(0.0),
        _ => true,
    }
}
#[derive(serde::Serialize)]
struct LockSource<'a> {
    id: &'a str,
    kind: &'a str,
    path: &'a str,
    upstream: &'a Option<String>,
    branch: &'a Option<String>,
    revision: Option<String>,
}
#[derive(serde::Serialize)]
struct LockPlugin<'a> {
    id: &'a str,
    source: &'a str,
    version: serde_json::Value,
    skills: usize,
    components: BTreeMap<String, usize>,
    artifacts: BTreeMap<&'a String, String>,
}
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct LockDocument<'a> {
    schema_version: u8,
    marketplace: &'a str,
    sources: Vec<LockSource<'a>>,
    plugins: Vec<LockPlugin<'a>>,
}

#[derive(serde::Serialize)]
struct RenderedPlugin<'a> {
    name: &'a str,
    source: String,
    description: String,
}
#[derive(serde::Serialize)]
struct RenderedOwner<'a> {
    name: &'a str,
}
#[derive(serde::Serialize)]
struct RenderedMetadata<'a> {
    description: &'a str,
}
#[derive(serde::Serialize)]
struct RenderedMarketplace<'a> {
    name: &'a str,
    owner: RenderedOwner<'a>,
    #[serde(skip_serializing_if = "Option::is_none")]
    metadata: Option<RenderedMetadata<'a>>,
    plugins: Vec<RenderedPlugin<'a>>,
}

#[derive(serde::Serialize)]
struct Issue {
    code: String,
    severity: &'static str,
    location: String,
    message: String,
}
impl Issue {
    fn new(
        code: impl Into<String>,
        location: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            code: code.into(),
            severity: "error",
            location: location.into(),
            message: message.into(),
        }
    }
}
