use serde::de::{DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};

mod lifecycle;
mod state;

pub type Result<T> = std::result::Result<T, String>;

fn deserialize_optional_non_null<'de, D, T>(
    deserializer: D,
) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Catalog {
    #[serde(rename = "$schema")]
    pub schema: Option<String>,
    pub schema_version: u64,
    pub marketplace: Marketplace,
    pub sources: Vec<Source>,
    pub plugins: Vec<Plugin>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Marketplace {
    pub id: String,
    pub name: String,
    pub owner: Owner,
    pub description: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Owner {
    pub name: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub id: String,
    pub kind: SourceKind,
    pub path: String,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub upstream: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub branch: Option<String>,
}

#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum SourceKind {
    Local,
    CuratedCopy,
    GitSubmodule,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plugin {
    pub id: String,
    pub source: String,
    pub artifacts: BTreeMap<String, Artifact>,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub provenance: Option<Provenance>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub path: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    pub upstream: String,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub notice: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Profile {
    #[serde(rename = "$schema")]
    pub schema: Option<String>,
    pub schema_version: u64,
    pub id: String,
    pub plugins: PluginSelector,
    pub targets: Vec<ProfileTarget>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginSelector {
    pub include: Vec<String>,
    pub exclude: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct ProfileTarget {
    pub id: String,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub destination: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub components: Option<Vec<ComponentKind>>,
    #[serde(default, deserialize_with = "deserialize_optional_non_null")]
    pub project_root: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum ComponentKind {
    Skills,
    Agents,
    Rules,
    Commands,
    Hooks,
    Mcp,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Cli {
    pub root: PathBuf,
    pub profile: String,
    pub destination: Option<PathBuf>,
    pub state_dir: PathBuf,
    pub adopt: bool,
    pub json: bool,
    action: lifecycle::Action,
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Operation {
    kind: &'static str,
    key: String,
    destination: String,
    source: String,
    reason: &'static str,
    component: &'static str,
    resource_kind: &'static str,
    digest: Option<String>,
    diagnostic: Option<String>,
    trust_token: Option<String>,
    trust_required: bool,
    #[serde(skip)]
    expected_link: Option<PathBuf>,
}

pub fn parse_cli(args: impl IntoIterator<Item = String>) -> Result<Cli> {
    let mut args = args.into_iter();
    let _program = args.next();
    let mut root = PathBuf::from(".");
    let mut profile = String::from("default");
    let mut token = args.next().ok_or_else(usage)?;
    while token.starts_with("--") {
        match token.as_str() {
            "--root" => root = PathBuf::from(args.next().ok_or("--root requires a value")?),
            "--profile" => profile = args.next().ok_or("--profile requires a value")?,
            "--help" | "-h" => return Err(usage()),
            _ => return Err(format!("unsupported global option: {token}")),
        }
        token = args.next().ok_or_else(usage)?;
    }
    let action = match token.as_str() {
        "plan" => lifecycle::Action::Plan,
        "status" => lifecycle::Action::Status,
        "install" => lifecycle::Action::Install,
        "uninstall" => lifecycle::Action::Uninstall,
        _ => {
            return Err(format!(
                "unsupported subcommand for Rust parity unit: {token}"
            ))
        }
    };

    let mut target = None;
    let mut destination = None;
    let mut state_dir = default_state_dir()?;
    let mut adopt = false;
    let mut json = false;
    let mut components = Vec::new();
    while let Some(option) = args.next() {
        match option.as_str() {
            "--target" => target = Some(args.next().ok_or("--target requires a value")?),
            "--destination" => {
                destination = Some(PathBuf::from(
                    args.next().ok_or("--destination requires a value")?,
                ))
            }
            "--state-dir" => {
                state_dir = PathBuf::from(args.next().ok_or("--state-dir requires a value")?)
            }
            "--component" if action != lifecycle::Action::Uninstall => {
                components.push(args.next().ok_or("--component requires a value")?)
            }
            "--adopt" if action != lifecycle::Action::Uninstall => adopt = true,
            "--json" => json = true,
            _ => return Err(format!("unsupported {} option: {option}", token)),
        }
    }
    let target = target.ok_or("--target is required")?;
    if target != "hermes" {
        return Err(format!("unsupported target for Rust parity unit: {target}"));
    }
    if profile != "pstack" {
        return Err(format!(
            "unsupported profile for Rust parity unit: {profile}"
        ));
    }
    if components.iter().any(|component| component != "skills") {
        return Err("Rust parity unit supports only the skills component".into());
    }
    if !json {
        return Err("the Rust parity unit currently requires --json".into());
    }
    Ok(Cli {
        root,
        profile,
        destination,
        state_dir,
        adopt,
        json,
        action,
    })
}

fn usage() -> String {
    "usage: agent-workbench-rs [--root ROOT] [--profile PROFILE] {plan,status,install,uninstall} --target hermes [--destination PATH] [--state-dir PATH] [--component skills] [--adopt] --json".into()
}

pub fn help_requested(args: &[String]) -> bool {
    args.iter()
        .skip(1)
        .any(|argument| matches!(argument.as_str(), "--help" | "-h"))
}

pub fn usage_text() -> String {
    usage()
}

fn default_state_dir() -> Result<PathBuf> {
    if let Some(value) = env::var_os("XDG_STATE_HOME") {
        return Ok(PathBuf::from(value));
    }
    Ok(home_dir()?.join(".local/state"))
}

fn home_dir() -> Result<PathBuf> {
    env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or("HOME is not set".into())
}

pub fn decode_catalog(path: &Path) -> Result<Catalog> {
    let catalog: Catalog = decode_strict(path)?;
    if catalog.schema_version != 2 {
        return Err("catalog.schemaVersion must be integer 2".into());
    }
    validate_id(&catalog.marketplace.id, "catalog.marketplace.id")?;
    require_nonempty(&catalog.marketplace.name, "catalog.marketplace.name")?;
    require_nonempty(
        &catalog.marketplace.owner.name,
        "catalog.marketplace.owner.name",
    )?;
    require_nonempty(
        &catalog.marketplace.description,
        "catalog.marketplace.description",
    )?;

    let mut sources = BTreeSet::new();
    for source in &catalog.sources {
        validate_id(&source.id, "catalog source id")?;
        if !sources.insert(source.id.clone()) {
            return Err("source ids must be unique".into());
        }
        validate_repo_path(&source.path, "catalog source path")?;
        match source.kind {
            SourceKind::CuratedCopy if source.upstream.is_none() => {
                return Err(format!(
                    "curated-copy source {} requires upstream provenance",
                    source.id
                ))
            }
            SourceKind::GitSubmodule
                if source.upstream.is_none() || source.branch.as_deref().is_none() =>
            {
                return Err(format!(
                    "git-submodule source {} requires upstream and branch",
                    source.id
                ))
            }
            _ => {}
        }
        if let Some(upstream) = &source.upstream {
            validate_upstream(upstream)?;
        }
        if let Some(branch) = &source.branch {
            validate_branch(branch)?;
        }
    }

    let mut plugins = BTreeSet::new();
    for plugin in &catalog.plugins {
        validate_id(&plugin.id, "catalog plugin id")?;
        validate_id(&plugin.source, "catalog plugin source")?;
        if !plugins.insert(plugin.id.clone()) {
            return Err("plugin ids must be unique".into());
        }
        if plugin.artifacts.is_empty() {
            return Err("plugin artifacts must contain at least one artifact".into());
        }
        for (kind, artifact) in &plugin.artifacts {
            validate_id(kind, "catalog artifact kind")?;
            validate_repo_path(&artifact.path, "catalog artifact path")?;
        }
        if let Some(provenance) = &plugin.provenance {
            validate_upstream(&provenance.upstream)?;
            if let Some(notice) = &provenance.notice {
                validate_repo_path(notice, "catalog provenance notice")?;
            }
        }
        if catalog
            .sources
            .iter()
            .find(|source| source.id == plugin.source)
            .is_some_and(|source| source.kind == SourceKind::CuratedCopy)
            && plugin
                .provenance
                .as_ref()
                .and_then(|provenance| provenance.notice.as_ref())
                .is_none()
        {
            return Err(format!(
                "curated-copy plugin {} requires provenance with a notice",
                plugin.id
            ));
        }
    }
    Ok(catalog)
}

pub fn decode_profile(path: &Path) -> Result<Profile> {
    let profile: Profile = decode_strict(path)?;
    if profile.schema_version != 1 && profile.schema_version != 2 {
        return Err("profile.schemaVersion must be integer 1 or 2".into());
    }
    validate_id(&profile.id, "profile.id")?;
    for include in &profile.plugins.include {
        if include != "*" {
            validate_id(include, "profile.plugins.include[]")?;
        }
    }
    for exclude in &profile.plugins.exclude {
        validate_id(exclude, "profile.plugins.exclude[]")?;
    }
    let mut targets = BTreeSet::new();
    for target in &profile.targets {
        validate_id(&target.id, "profile target id")?;
        if profile.schema_version == 1
            && (target.components.is_some() || target.project_root.is_some())
        {
            return Err("profile v1 target has profile v2 fields".into());
        }
        if profile.schema_version == 2 && !targets.insert(target.id.clone()) {
            return Err("profile target ids must be unique".into());
        }
        if let Some(destination) = &target.destination {
            require_nonempty(destination, "profile target destination")?;
        }
        if let Some(components) = &target.components {
            let unique: BTreeSet<_> = components.iter().collect();
            if unique.len() != components.len() {
                return Err("profile target components must be unique".into());
            }
        }
        if let Some(project_root) = &target.project_root {
            let path = expand_home(project_root)?;
            if project_root.is_empty()
                || project_root.contains('\0')
                || !path.is_absolute()
                || has_parent_component(&path)
            {
                return Err("profile target projectRoot must be an absolute contained path".into());
            }
        }
    }
    Ok(profile)
}

fn decode_strict<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let text = fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    reject_duplicate_json_keys(&text)?;
    serde_json::from_str(&text).map_err(|error| format!("{}: {error}", path.display()))
}

pub fn reject_duplicate_json_keys(text: &str) -> Result<()> {
    let mut deserializer = serde_json::Deserializer::from_str(text);
    DuplicateSeed
        .deserialize(&mut deserializer)
        .map_err(|error| error.to_string())?;
    deserializer.end().map_err(|error| error.to_string())
}

struct DuplicateSeed;

impl<'de> DeserializeSeed<'de> for DuplicateSeed {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<(), D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_any(DuplicateVisitor)
    }
}

struct DuplicateVisitor;

impl<'de> Visitor<'de> for DuplicateVisitor {
    type Value = ();

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("valid JSON without duplicate object keys")
    }

    fn visit_bool<E>(self, _: bool) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_i64<E>(self, _: i64) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_u64<E>(self, _: u64) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_f64<E>(self, _: f64) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_str<E>(self, _: &str) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_string<E>(self, _: String) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_none<E>(self) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_unit<E>(self) -> std::result::Result<(), E> {
        Ok(())
    }
    fn visit_some<D>(self, deserializer: D) -> std::result::Result<(), D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        DuplicateSeed.deserialize(deserializer)
    }
    fn visit_seq<A>(self, mut sequence: A) -> std::result::Result<(), A::Error>
    where
        A: SeqAccess<'de>,
    {
        while sequence.next_element_seed(DuplicateSeed)?.is_some() {}
        Ok(())
    }
    fn visit_map<A>(self, mut map: A) -> std::result::Result<(), A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = BTreeSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !keys.insert(key.clone()) {
                return Err(serde::de::Error::custom(format!(
                    "duplicate JSON key {key:?}"
                )));
            }
            map.next_value_seed(DuplicateSeed)?;
        }
        Ok(())
    }
}

fn resolve_destination(explicit: Option<&Path>, profile: Option<&str>) -> Result<PathBuf> {
    let path = if let Some(path) = explicit {
        path.to_path_buf()
    } else if let Some(path) = profile {
        expand_home(path)?
    } else if let Some(home) = env::var_os("HERMES_HOME") {
        PathBuf::from(home).join("skills")
    } else {
        home_dir()?.join(".hermes/skills")
    };
    let absolute = if path.is_absolute() {
        path
    } else {
        env::current_dir()
            .map_err(|error| error.to_string())?
            .join(path)
    };
    if absolute.is_symlink() {
        return Err(format!(
            "destination root may not be a symlink: {}",
            absolute.display()
        ));
    }
    canonicalize_missing(&absolute)
}

fn reject_destination_root_symlink(path: &Path) -> Result<()> {
    let mut current = path;
    loop {
        if let Ok(metadata) = fs::symlink_metadata(current) {
            if metadata.file_type().is_symlink() {
                return Err(format!(
                    "destination root may not contain a symlink: {}",
                    current.display()
                ));
            }
        }
        match current.parent() {
            Some(parent) if parent != current => current = parent,
            _ => return Ok(()),
        }
    }
}

fn discover_skills(root: &Path) -> Result<Vec<(String, PathBuf)>> {
    reject_symlink_tree(root)?;
    let mut directories = fs::read_dir(root)
        .map_err(|error| format!("skills root does not exist: {}: {error}", root.display()))?
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    directories.sort_by_key(|entry| entry.file_name());
    let mut skills = Vec::new();
    for entry in directories {
        let metadata = entry
            .file_type()
            .map_err(|error| format!("{}: {error}", entry.path().display()))?;
        if !metadata.is_dir() {
            continue;
        }
        let skill_file = entry.path().join("SKILL.md");
        match fs::symlink_metadata(&skill_file) {
            Ok(metadata) if metadata.file_type().is_file() => {}
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(format!(
                    "SKILL.md may not be a symlink: {}",
                    skill_file.display()
                ))
            }
            _ => continue,
        }
        let name = parse_frontmatter(&skill_file)?;
        skills.push((name, canonicalize_existing(&entry.path(), "skill source")?));
    }
    Ok(skills)
}

fn reject_symlink_tree(root: &Path) -> Result<()> {
    let metadata =
        fs::symlink_metadata(root).map_err(|error| format!("{}: {error}", root.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!(
            "artifact root may not be a symlink: {}",
            root.display()
        ));
    }
    if !metadata.is_dir() {
        return Err(format!(
            "artifact root is not a directory: {}",
            root.display()
        ));
    }
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            let kind = entry.file_type().map_err(|error| error.to_string())?;
            if kind.is_symlink() {
                return Err(format!(
                    "skill content may not be a symlink: {}",
                    entry.path().display()
                ));
            }
            if kind.is_dir() {
                pending.push(entry.path());
            }
        }
    }
    Ok(())
}

fn parse_frontmatter(path: &Path) -> Result<String> {
    let text = fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    if !text.starts_with("---\n") {
        return Err(format!(
            "skill frontmatter must start at byte zero: {}",
            path.display()
        ));
    }
    let closing = text[4..]
        .find("\n---\n")
        .map(|index| index + 4)
        .ok_or_else(|| format!("skill frontmatter is not closed: {}", path.display()))?;
    let mut name = None;
    let mut description = false;
    for line in text[4..closing].lines() {
        let Some((key, raw_value)) = line.split_once(':') else {
            continue;
        };
        match key {
            "name" if name.is_none() => {
                let value = raw_value.trim().trim_matches(['\'', '"']);
                if !value.is_empty() {
                    name = Some(value.to_owned());
                }
            }
            "description" if !raw_value.is_empty() => description = true,
            _ => {}
        }
    }
    let name = name.filter(|value| safe_filename(value)).ok_or_else(|| {
        format!(
            "skill frontmatter needs safe name and description: {}",
            path.display()
        )
    })?;
    if !description {
        return Err(format!(
            "skill frontmatter needs name and description: {}",
            path.display()
        ));
    }
    Ok(name)
}

fn safe_filename(value: &str) -> bool {
    !value.is_empty()
        && value != "."
        && value != ".."
        && !value.contains(['/', '\\', '\0'])
        && Path::new(value).file_name().and_then(|name| name.to_str()) == Some(value)
}

fn same_link(path: &Path, source: &Path) -> Result<bool> {
    let target = fs::read_link(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let absolute = if target.is_absolute() {
        target
    } else {
        path.parent()
            .ok_or("destination has no parent")?
            .join(target)
    };
    Ok(canonicalize_missing(&absolute)? == canonicalize_missing(source)?)
}

fn resolve_repo_path(root: &Path, value: &str) -> Result<PathBuf> {
    validate_repo_path(value, "repository path")?;
    let mut lexical = root.to_path_buf();
    for component in Path::new(value).components() {
        lexical.push(component);
        let metadata = fs::symlink_metadata(&lexical)
            .map_err(|error| format!("repository path {}: {error}", lexical.display()))?;
        if metadata.file_type().is_symlink() {
            return Err(format!(
                "repository path may not contain symlinks: {}",
                lexical.display()
            ));
        }
    }
    let path = canonicalize_existing(&lexical, "repository path")?;
    if path != root && !path.starts_with(root) {
        return Err(format!("repository path escapes checkout: {value:?}"));
    }
    Ok(path)
}

fn validate_repo_path(value: &str, location: &str) -> Result<()> {
    let path = Path::new(value);
    if value.is_empty()
        || value.contains(['\0', '\\'])
        || path.is_absolute()
        || path.components().any(|part| {
            matches!(
                part,
                Component::ParentDir
                    | Component::CurDir
                    | Component::RootDir
                    | Component::Prefix(_)
            )
        })
    {
        return Err(format!(
            "{location} must be relative and contained: {value:?}"
        ));
    }
    Ok(())
}

fn validate_id(value: &str, location: &str) -> Result<()> {
    let valid = !value.is_empty()
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || (index > 0 && byte == b'-')
        });
    if !valid {
        return Err(format!("{location} must match ^[a-z0-9][a-z0-9-]*$"));
    }
    Ok(())
}

fn require_nonempty(value: &str, location: &str) -> Result<()> {
    if value.is_empty() || value.contains('\0') {
        Err(format!("{location} must be a non-empty string"))
    } else {
        Ok(())
    }
}

fn validate_upstream(value: &str) -> Result<()> {
    let valid_scheme = ["https://", "ssh://", "git://"]
        .iter()
        .any(|prefix| value.starts_with(prefix));
    let scp = value
        .split_once('@')
        .and_then(|(_, rest)| rest.split_once(':'))
        .is_some();
    if value.starts_with('-') || value.contains(['\0', ' ']) || (!valid_scheme && !scp) {
        return Err("upstream must be an https, ssh, git, or scp-style URL".into());
    }
    Ok(())
}

fn validate_branch(value: &str) -> Result<()> {
    let forbidden = ["..", "@{", " ", "~", "^", ":", "?", "*", "[", "\\"];
    if value.is_empty()
        || value.starts_with(['-', '/'])
        || value.ends_with(['/', '.'])
        || forbidden.iter().any(|part| value.contains(part))
    {
        return Err("source branch is not a safe Git branch".into());
    }
    Ok(())
}

fn expand_home(value: &str) -> Result<PathBuf> {
    if value == "~" {
        return home_dir();
    }
    if let Some(rest) = value.strip_prefix("~/") {
        return Ok(home_dir()?.join(rest));
    }
    if value.starts_with('~') {
        return Err("unknown user in path".into());
    }
    Ok(PathBuf::from(value))
}

fn has_parent_component(path: &Path) -> bool {
    path.components().any(|part| part == Component::ParentDir)
}

fn canonicalize_existing(path: &Path, location: &str) -> Result<PathBuf> {
    fs::canonicalize(path).map_err(|error| format!("{location} {}: {error}", path.display()))
}

fn canonicalize_missing(path: &Path) -> Result<PathBuf> {
    let mut suffix = Vec::new();
    let mut current = path;
    loop {
        match fs::canonicalize(current) {
            Ok(mut canonical) => {
                for part in suffix.iter().rev() {
                    canonical.push(part);
                }
                return Ok(canonical);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let name = current
                    .file_name()
                    .ok_or_else(|| format!("path has no existing ancestor: {}", path.display()))?;
                suffix.push(name.to_os_string());
                current = current
                    .parent()
                    .ok_or_else(|| format!("path has no existing ancestor: {}", path.display()))?;
            }
            Err(error) => return Err(format!("{}: {error}", current.display())),
        }
    }
}

pub fn has_conflicts(operations: &[Operation]) -> bool {
    operations
        .iter()
        .any(|operation| operation.kind == "conflict")
}

pub fn operations_json(operations: &[Operation]) -> Result<String> {
    serde_json::to_string_pretty(operations).map_err(|error| error.to_string())
}

pub struct CommandOutput {
    pub stdout: String,
    pub exit_code: u8,
}

pub fn execute(args: Vec<String>) -> Result<CommandOutput> {
    if help_requested(&args) {
        return Ok(CommandOutput {
            stdout: usage_text() + "\n",
            exit_code: 0,
        });
    }
    let cli = parse_cli(args)?;
    let (operations, conflicts) = lifecycle::run(&cli)?;
    Ok(CommandOutput {
        stdout: operations_json(&operations)? + "\n",
        exit_code: if conflicts { 1 } else { 0 },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_duplicate_keys_at_any_depth() {
        let error = reject_duplicate_json_keys(r#"{"outer":{"id":"a","id":"b"}}"#).unwrap_err();
        assert!(error.contains("duplicate JSON key \"id\""), "{error}");
    }

    #[test]
    fn output_field_order_is_the_python_contract() {
        let operation = Operation {
            kind: "create",
            key: "skill:x".into(),
            destination: "/d/x".into(),
            source: "/s/x".into(),
            reason: "destination is absent",
            component: "skills",
            resource_kind: "directory-symlink",
            digest: None,
            diagnostic: None,
            trust_token: None,
            trust_required: false,
            expected_link: None,
        };
        let value = operations_json(&[operation]).unwrap();
        let expected = [
            "kind",
            "key",
            "destination",
            "source",
            "reason",
            "component",
            "resourceKind",
            "digest",
            "diagnostic",
            "trustToken",
            "trustRequired",
        ];
        let positions: Vec<_> = expected
            .iter()
            .map(|field| value.find(&format!("\"{field}\"")).unwrap())
            .collect();
        assert!(positions.windows(2).all(|pair| pair[0] < pair[1]));
    }
}
