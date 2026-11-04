//! Inert artifact inventories, independent of installation target policy.
use crate::{state::wire::Wire, *};

#[derive(Debug, serde::Serialize)]
pub struct Inventory {
    pub kind: String,
    pub root: PathBuf,
    pub components: Vec<(String, String)>,
    pub representation: Option<&'static str>,
}

pub(crate) fn validate_tree(root: &Path) -> Result<()> {
    reject_symlink_tree(root)
        .map_err(|error| error.replacen("skill content", "artifact content", 1))
}

pub(crate) fn load_json_object(repository: &Path, path: &Path) -> Result<Wire> {
    let content = lifecycle::read_regular(repository, path)?;
    // Source artifact readers use strict UTF-8 text, unlike installed shared
    // documents whose byte decoder deliberately accepts BOM/UTF-16/UTF-32.
    let text = std::str::from_utf8(&content).map_err(|error| {
        let start = error.valid_up_to();
        let length = error.error_len().unwrap_or(content.len() - start);
        let position = if length == 1 {
            format!("byte 0x{:02x} in position {start}", content[start])
        } else {
            format!("bytes in position {start}-{}", start + length - 1)
        };
        let reason = match error.error_len() {
            None => "unexpected end of data",
            Some(1) if !(0xc2..=0xf4).contains(&content[start]) => "invalid start byte",
            Some(_) => "invalid continuation byte",
        };
        format!("'utf-8' codec can't decode {position}: {reason}")
    })?;
    if text.starts_with('\u{feff}') {
        return Err(
            "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)".into(),
        );
    }
    shared_document::parse(text).map_err(|error| {
        if error == "shared JSON document must be an object" {
            format!("JSON document must be an object: {}", path.display())
        } else if error.starts_with("EOF while parsing a value") {
            let text = String::from_utf8_lossy(&content);
            let line = text.chars().filter(|ch| *ch == '\n').count() + 1;
            let column = text.rsplit('\n').next().unwrap_or("").chars().count() + 1;
            format!(
                "Expecting value: line {line} column {column} (char {})",
                text.chars().count()
            )
        } else {
            error
        }
    })
}

// Path.resolve(strict=False) also retains suffixes beneath regular files. Keep
// this inspection policy separate from installation path authorization.
fn resolve_catalog_path(path: &Path) -> Result<PathBuf> {
    match fs::canonicalize(path) {
        Ok(path) => Ok(path),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
            ) =>
        {
            let parent = path
                .parent()
                .ok_or_else(|| format!("path has no existing ancestor: {}", path.display()))?;
            let name = path
                .file_name()
                .ok_or_else(|| format!("path has no existing ancestor: {}", path.display()))?;
            Ok(resolve_catalog_path(parent)?.join(name))
        }
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

/// Catalog inspection collects independent artifact failures rather than stopping
/// at the first invalid artifact. It never compiles or executes payloads.
#[derive(Debug, serde::Serialize)]
pub struct CatalogInspection {
    pub plugins: Vec<String>,
    pub inventories: Vec<(String, Inventory)>,
    pub issues: Vec<(String, String, String, String)>,
    pub counts: BTreeMap<String, usize>,
}

pub fn inspect_catalog(repository: &Path) -> Result<CatalogInspection> {
    let catalog_path = repository.join("catalog/plugins.json");
    let catalog = decode_catalog(&catalog_path)?;
    let raw =
        crate::state::wire::parse(&fs::read_to_string(&catalog_path).map_err(|e| e.to_string())?)?;
    let Wire::Array(raw_plugins) = raw.get("plugins") else {
        unreachable!()
    };
    let mut result = CatalogInspection {
        plugins: Vec::new(),
        inventories: Vec::new(),
        issues: Vec::new(),
        counts: BTreeMap::new(),
    };
    for source in &catalog.sources {
        let root = resolve_catalog_path(&repository.join(&source.path))?;
        if !root.exists() {
            result.issues.push((
                "SOURCE_PATH_MISSING".into(),
                "error".into(),
                root.display().to_string(),
                "source path does not exist".into(),
            ));
        }
    }
    for (plugin, raw_plugin) in catalog.plugins.iter().zip(raw_plugins) {
        let Some(source) = catalog.sources.iter().find(|s| s.id == plugin.source) else {
            result.issues.push((
                "PLUGIN_SOURCE_UNKNOWN".into(),
                "error".into(),
                format!("plugin:{}", plugin.id),
                format!("unknown source: {}", plugin.source),
            ));
            continue;
        };
        result.plugins.push(plugin.id.clone());
        let source_root = resolve_catalog_path(&repository.join(&source.path))?;
        for (kind, _) in raw_plugin.get("artifacts").object("artifacts")? {
            let root = resolve_catalog_path(&source_root.join(&plugin.artifacts[kind].path))?;
            if !matches!(
                kind.as_str(),
                "agent-skills"
                    | "agents"
                    | "rules"
                    | "commands"
                    | "hooks"
                    | "cursor-hooks"
                    | "claude-hooks"
                    | "mcp"
                    | "cursor-plugin"
                    | "claude-plugin"
            ) {
                result.issues.push((
                    "ARTIFACT_KIND_UNKNOWN".into(),
                    "error".into(),
                    format!("plugin:{}", plugin.id),
                    format!("unknown artifact kind: {kind}"),
                ));
            } else if !root.exists() {
                result.issues.push((
                    "ARTIFACT_PATH_MISSING".into(),
                    "error".into(),
                    root.display().to_string(),
                    "artifact path does not exist".into(),
                ));
            } else {
                match inspect(repository, kind, &root) {
                    Ok(inventory) => {
                        if !inventory.components.is_empty() {
                            *result.counts.entry(kind.clone()).or_default() +=
                                inventory.components.len();
                        }
                        result.inventories.push((plugin.id.clone(), inventory));
                    }
                    Err(error) => result.issues.push((
                        "ARTIFACT_INVALID".into(),
                        "error".into(),
                        root.display().to_string(),
                        format!("{kind}: {error}"),
                    )),
                }
            }
        }
    }
    Ok(result)
}

pub fn inspect(repository: &Path, kind: &str, root: &Path) -> Result<Inventory> {
    let mut inventory = Inventory {
        kind: kind.into(),
        root: root.into(),
        components: Vec::new(),
        representation: None,
    };
    match kind {
        "agent-skills" => {
            for (name, path) in discover_skills(root)? {
                inventory.components.push((
                    name,
                    path.strip_prefix(root)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .into(),
                ));
            }
        }
        "agents" | "rules" | "commands" => {
            let (component, suffix) = match kind {
                "agents" => (ComponentKind::Agents, ".md"),
                "rules" => (ComponentKind::Rules, ".mdc"),
                _ => (ComponentKind::Commands, ".md"),
            };
            inventory.components = lifecycle::discover_files(root, suffix, component)?
                .into_iter()
                .map(|(name, _)| {
                    (
                        Path::new(&name)
                            .file_stem()
                            .unwrap()
                            .to_string_lossy()
                            .into_owned(),
                        name,
                    )
                })
                .collect();
        }
        "hooks" | "cursor-hooks" | "claude-hooks" => {
            validate_tree(root)?;
            let path = root.join("hooks.json");
            let data = load_json_object(repository, &path)?;
            if !matches!(data.get("hooks"), Wire::Object(_)) {
                return Err(format!(
                    "hooks.json requires a hooks object: {}",
                    path.display()
                ));
            }
            inventory.representation = Some(if activation::classify_hooks(&data, kind)? {
                "hook-bundle"
            } else {
                "claude-hook-bundle"
            });
            let mut events: Vec<_> = data.get("hooks").object("hooks")?.iter().collect();
            events.sort_by(|a, b| a.0.cmp(&b.0));
            for (event, entries) in events {
                if let Wire::Array(entries) = entries {
                    for index in 0..entries.len() {
                        inventory
                            .components
                            .push((format!("{event}:{index}"), "hooks.json".into()));
                    }
                }
            }
        }
        "mcp" => {
            let data = load_json_object(repository, root)?;
            activation::validate_mcp_inventory(&data, root)?;
            let mut servers: Vec<_> = data
                .get("mcpServers")
                .object("mcpServers")?
                .iter()
                .collect();
            servers.sort_by(|a, b| a.0.cmp(&b.0));
            for (name, _) in servers {
                inventory.components.push((
                    name.clone(),
                    root.file_name().unwrap().to_string_lossy().into(),
                ));
            }
        }
        "cursor-plugin" | "claude-plugin" => {
            if root.is_symlink() {
                return Err(format!(
                    "artifact root may not be a symlink: {}",
                    root.display()
                ));
            }
            if !root.exists() {
                return Err(format!("artifact path does not exist: {}", root.display()));
            }
        }
        _ => return Err(format!("unknown artifact kind: {kind}")),
    }
    Ok(inventory)
}
