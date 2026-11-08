use serde::de::{DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::ser::{Serialize, SerializeMap};
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};

mod activation;
#[cfg(test)]
mod activation_parity_tests;
#[cfg(test)]
mod artifact_parity_tests;
pub mod artifacts;
#[cfg(test)]
mod bundle_parity_tests;
mod bundles;
pub mod capabilities;
mod cli;
mod cli_help;
mod lifecycle;
#[cfg(test)]
mod platform_parity_tests;
mod rendering;
mod resource;
mod shared_document;
#[cfg(test)]
mod shared_document_parity_tests;
mod sources;
mod state;
mod transaction;
#[cfg(test)]
mod transaction_parity_tests;
mod trust;
mod validation;
mod validation_codec;
mod validation_json;

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

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum ComponentKind {
    Skills,
    Agents,
    Rules,
    Commands,
    Hooks,
    Mcp,
}

impl ComponentKind {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Skills => "skills",
            Self::Agents => "agents",
            Self::Rules => "rules",
            Self::Commands => "commands",
            Self::Hooks => "hooks",
            Self::Mcp => "mcp",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SkillsTarget {
    Agents,
    Claude,
    Codex,
    Cursor,
    Grok,
    Hermes,
}

impl SkillsTarget {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "agents" => Ok(Self::Agents),
            "claude" => Ok(Self::Claude),
            "codex" => Ok(Self::Codex),
            "cursor" => Ok(Self::Cursor),
            "grok" => Ok(Self::Grok),
            "hermes" => Ok(Self::Hermes),
            _ => Err(format!("unsupported target for Rust parity unit: {value}")),
        }
    }

    fn id(self) -> &'static str {
        match self {
            Self::Agents => "agents",
            Self::Claude => "claude",
            Self::Codex => "codex",
            Self::Cursor => "cursor",
            Self::Grok => "grok",
            Self::Hermes => "hermes",
        }
    }

    fn default_destination(self) -> Result<PathBuf> {
        match self {
            Self::Agents => Ok(home_dir()?.join(".agents/skills")),
            Self::Claude => Ok(home_dir()?.join(".claude/skills")),
            Self::Codex => environment_skills_destination("CODEX_HOME", ".codex"),
            Self::Cursor => Ok(home_dir()?.join(".cursor/skills")),
            Self::Grok => Ok(home_dir()?.join(".grok/skills")),
            Self::Hermes => environment_skills_destination("HERMES_HOME", ".hermes"),
        }
    }
}

fn environment_skills_destination(variable: &str, fallback: &str) -> Result<PathBuf> {
    let base = match env::var_os(variable).filter(|value| !value.is_empty()) {
        Some(value) => expand_home_path(PathBuf::from(value))?,
        None => home_dir()?.join(fallback),
    };
    Ok(base.join("skills"))
}

#[derive(Debug, PartialEq, Eq)]
pub struct Cli {
    pub root: PathBuf,
    pub profile: String,
    pub target: SkillsTarget,
    pub components: Option<Vec<ComponentKind>>,
    pub destination: Option<PathBuf>,
    pub project_root: Option<PathBuf>,
    pub state_dir: PathBuf,
    pub adopt: bool,
    pub trust: Vec<String>,
    pub json: bool,
    action: lifecycle::Action,
}

#[derive(Clone, Debug)]
pub struct Operation {
    kind: &'static str,
    key: String,
    destination: String,
    source: String,
    reason: String,
    component: &'static str,
    resource: resource::PathResource,
    diagnostic: Option<String>,
    trust_token: Option<String>,
    trust_required: bool,
    expected_link: Option<PathBuf>,
    expected_digest: Option<String>,
}

impl Serialize for Operation {
    fn serialize<S: serde::Serializer>(
        &self,
        serializer: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(11))?;
        map.serialize_entry("kind", self.kind)?;
        map.serialize_entry("key", &self.key)?;
        map.serialize_entry("destination", &self.destination)?;
        let projected_conflict = self.key.starts_with("component:");
        map.serialize_entry("source", &(!projected_conflict).then_some(&self.source))?;
        map.serialize_entry("reason", &self.reason)?;
        map.serialize_entry("component", self.component)?;
        map.serialize_entry(
            "resourceKind",
            &(!projected_conflict).then_some(self.resource.kind()),
        )?;
        map.serialize_entry(
            "digest",
            &self.resource.digest().map(resource::ContentDigest::as_str),
        )?;
        map.serialize_entry("diagnostic", &self.diagnostic)?;
        map.serialize_entry("trustToken", &self.trust_token)?;
        map.serialize_entry("trustRequired", &self.trust_required)?;
        map.end()
    }
}

pub fn parse_cli(args: impl IntoIterator<Item = String>) -> Result<Cli> {
    match cli::parse(&args.into_iter().collect::<Vec<_>>()) {
        cli::Parsed::Command(cli::Command::Lifecycle { options, action }) => {
            options.lifecycle(action)
        }
        cli::Parsed::Command(_) => Err("expected lifecycle command".into()),
        cli::Parsed::Output(output) => Err(if output.exit_code == 0 {
            output.stdout
        } else {
            String::from_utf8_lossy(&output.stderr).into_owned()
        }),
    }
}

fn usage() -> String {
    cli_help::text("")
}

pub fn help_requested(args: &[String]) -> bool {
    matches!(cli::parse(args), cli::Parsed::Output(output) if output.exit_code == 0)
}

pub fn usage_text() -> String {
    usage()
}

fn default_state_dir() -> Result<PathBuf> {
    if let Some(value) = env::var_os("XDG_STATE_HOME").filter(|value| !value.is_empty()) {
        return expand_home_path(PathBuf::from(value));
    }
    Ok(home_dir()?.join(".local/state"))
}

fn home_dir() -> Result<PathBuf> {
    match env::var_os("HOME") {
        Some(value) if value.is_empty() => Ok(PathBuf::from("/")),
        Some(value) => Ok(PathBuf::from(value)),
        None => platform_home_dir(),
    }
}

fn platform_home_dir() -> Result<PathBuf> {
    home::home_dir().ok_or("home directory is unavailable".into())
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

fn resolve_destination(
    explicit: Option<&Path>,
    profile: Option<&str>,
    target: SkillsTarget,
) -> Result<PathBuf> {
    let path = if let Some(path) = explicit {
        path.to_path_buf()
    } else if let Some(path) = profile {
        expand_home(path)?
    } else {
        target.default_destination()?
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
    if root.is_symlink() {
        return Err(format!(
            "skills root may not be a symlink: {}",
            root.display()
        ));
    }
    if !root.is_dir() {
        return Err(format!("skills root does not exist: {}", root.display()));
    }
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
        if metadata.is_symlink() {
            return Err(format!(
                "skill directory may not be a symlink: {}",
                entry.path().display()
            ));
        }
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
        reject_symlink_tree(&entry.path())?;
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
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
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
    let frontmatter = &text[4..closing];
    let mut name = None;
    let mut description = false;
    let mut offset = 0;
    for line in frontmatter.split_inclusive('\n') {
        // Python's anchored regular expressions allow \s to cross a newline;
        // these are deliberately not YAML scalar semantics.
        if name.is_none() && line.starts_with("name:") {
            let remaining = frontmatter[offset + "name:".len()..].trim_start();
            let value = remaining.split('\n').next().unwrap_or("").trim_end();
            let value = value.strip_prefix(['\'', '"']).unwrap_or(value);
            let value = value.strip_suffix(['\'', '"']).unwrap_or(value);
            if !value.is_empty() && !value.contains(['\'', '"']) {
                name = Some(value.trim().to_owned());
            }
        }
        if line.starts_with("description:") {
            description |= frontmatter[offset + "description:".len()..]
                .chars()
                .any(|ch| ch != '\n');
        }
        offset += line.len();
    }
    let name = name.ok_or_else(|| {
        format!(
            "skill frontmatter needs name and description: {}",
            path.display()
        )
    })?;
    if !description {
        return Err(format!(
            "skill frontmatter needs name and description: {}",
            path.display()
        ));
    }
    if !safe_filename(&name) {
        return Err(format!(
            "skill name must be one safe filename component: {}: {}",
            path.display(),
            state::wire::repr(&name)
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
    expand_home_path(PathBuf::from(value))
}

fn expand_home_path(value: PathBuf) -> Result<PathBuf> {
    if value == Path::new("~") {
        return home_dir();
    }
    if let Ok(rest) = value.strip_prefix("~") {
        return Ok(home_dir()?.join(rest));
    }
    if value
        .components()
        .next()
        .and_then(|part| part.as_os_str().to_str())
        .is_some_and(|part| part.starts_with('~'))
    {
        return Err("unknown user in path".into());
    }
    Ok(value)
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

fn operations_output(operations: &[Operation], json: bool) -> Result<String> {
    if json {
        return Ok(operations_json(operations)? + "\n");
    }
    Ok(operations.iter().map(operation_text).collect())
}

fn operation_text(operation: &Operation) -> String {
    let unsupported = operation.key.starts_with("component:");
    let resource = if unsupported {
        "unsupported"
    } else {
        operation.resource.kind()
    };
    let source = if unsupported || operation.source.is_empty() {
        String::new()
    } else {
        format!(" -> {}", operation.source)
    };
    let reason = operation_reason(
        &operation.reason,
        operation.diagnostic.as_deref(),
        operation.trust_token.as_deref(),
        operation.trust_required,
    );
    format!(
        "{:14} {}/{} {}{}  ({reason})\n",
        operation.kind, operation.component, resource, operation.destination, source
    )
}

fn operation_reason(
    reason: &str,
    diagnostic: Option<&str>,
    token: Option<&str>,
    required: bool,
) -> String {
    let mut reason = match diagnostic.filter(|s| !s.is_empty()) {
        Some(diagnostic) => format!("{diagnostic}: {reason}"),
        None => reason.to_owned(),
    };
    if let Some(token) = token.filter(|s| !s.is_empty()) {
        reason.push_str(&format!(
            "; trust {}: {token}",
            if required { "required" } else { "granted" }
        ));
    }
    reason
}

pub struct CommandOutput {
    pub stdout: String,
    /// JSON reports are one Python print payload, not one print per line.
    pub stdout_single_write: bool,
    pub stderr: Vec<u8>,
    pub exit_code: u8,
}

impl CommandOutput {
    // The public CLI prints the plan before attempting apply. Retain those
    // bytes on ordinary apply failure, in both output modes.
    fn apply_error(stdout: String, error: String, json: bool) -> Self {
        Self {
            stdout,
            stdout_single_write: json,
            stderr: format!("error: {error}\n").into_bytes(),
            exit_code: 2,
        }
    }
}

pub fn execute(args: Vec<String>) -> Result<CommandOutput> {
    match cli::parse(&args) {
        cli::Parsed::Output(output) => Ok(output),
        cli::Parsed::Command(command) => match command {
            cli::Command::Validate { options, generated } => {
                validation::run_options(&options.root, &options.profile, options.json, generated)
            }
            cli::Command::Render { options, check } => {
                rendering::run_options(&options.root, &options.profile, check)
            }
            cli::Command::Update { options, source } => {
                sources::run_options(&options.root, &options.profile, &source)
            }
            cli::Command::Lifecycle { options, action } => {
                // Workspace loading precedes target lookup in the frozen public CLI.
                // Private parse_cli callers retain their filesystem-free parse boundary.
                let root = artifacts::resolve_catalog_path(&options.root)?;
                validation::workspace(&root, &options.profile)?;
                if SkillsTarget::parse(&options.target).is_err() {
                    return Err(format!("unknown install target: {}", options.target));
                }
                let cli = options.lifecycle(action)?;
                if activation::Policy::for_target(cli.target).is_some() {
                    activation::run(&cli)
                } else {
                    lifecycle::run(&cli)
                }
            }
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_duplicate_keys_at_any_depth() {
        let error = reject_duplicate_json_keys(r#"{"outer":{"id":"a","id":"b"}}"#).unwrap_err();
        assert!(error.contains("duplicate JSON key \"id\""), "{error}");
    }

    const OPERATION_FIELDS: [&str; 11] = [
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

    #[test]
    fn copied_resource_json_has_stable_shape() {
        let digest = resource::ContentDigest::parse(&"ab".repeat(32)).unwrap();
        let proof = format!("aw-proof-v1:{}", "cd".repeat(32));
        let trust = format!("aw-trust-v1:{}", "ef".repeat(32));
        let digest_json = serde_json::Value::String(digest.as_str().to_owned());

        let symlink_operation = serde_json::to_string_pretty(&operation_fixture(
            "skills",
            resource::PathResource::DirectorySymlink,
            "/src/skill",
        ))
        .unwrap();
        assert_operation_shape(
            &symlink_operation,
            "directory-symlink",
            serde_json::Value::Null,
        );

        let file_operation = serde_json::to_string_pretty(&operation_fixture(
            "agents",
            resource::PathResource::FileCopy(digest.clone()),
            "/src/agent.md",
        ))
        .unwrap();
        assert_operation_shape(&file_operation, "file-copy", digest_json.clone());

        let directory_operation = serde_json::to_string_pretty(&operation_fixture(
            "hooks",
            resource::PathResource::DirectoryCopy(digest.clone()),
            "/src/hooks",
        ))
        .unwrap();
        assert_operation_shape(&directory_operation, "directory-copy", digest_json);

        let symlink_entry = state::entry_json(&entry_fixture(
            "skill:sample",
            ComponentKind::Skills,
            resource::PathResource::DirectorySymlink,
            None,
            None,
        ))
        .unwrap();
        assert_entry_shape(
            &symlink_entry,
            "skills",
            "directory-symlink",
            None,
            None,
            None,
        );

        let file_entry = state::entry_json(&entry_fixture(
            "agent:sample.md",
            ComponentKind::Agents,
            resource::PathResource::FileCopy(digest.clone()),
            None,
            None,
        ))
        .unwrap();
        assert_entry_shape(
            &file_entry,
            "agents",
            "file-copy",
            Some(digest.as_str()),
            None,
            None,
        );

        let directory_entry = state::entry_json(&entry_fixture(
            "hook:sample",
            ComponentKind::Hooks,
            resource::PathResource::DirectoryCopy(digest.clone()),
            Some(trust.clone()),
            None,
        ))
        .unwrap();
        assert_entry_shape(
            &directory_entry,
            "hooks",
            "directory-copy",
            Some(digest.as_str()),
            Some(serde_json::Value::String(trust.clone())),
            None,
        );

        let directory_entry_proof = state::entry_json(&entry_fixture(
            "hook:sample",
            ComponentKind::Hooks,
            resource::PathResource::DirectoryCopy(digest.clone()),
            Some(trust.clone()),
            Some(proof.clone()),
        ))
        .unwrap();
        assert_entry_shape(
            &directory_entry_proof,
            "hooks",
            "directory-copy",
            Some(digest.as_str()),
            Some(serde_json::Value::String(trust)),
            Some(&proof),
        );

        let directory_entry_null_trust = state::entry_json(&entry_fixture(
            "hook:sample",
            ComponentKind::Hooks,
            resource::PathResource::DirectoryCopy(digest.clone()),
            None,
            None,
        ))
        .unwrap();
        assert_entry_shape(
            &directory_entry_null_trust,
            "hooks",
            "directory-copy",
            Some(digest.as_str()),
            Some(serde_json::Value::Null),
            None,
        );
    }

    fn assert_field_order(json: &str, fields: &[&str]) {
        let positions: Vec<_> = fields
            .iter()
            .map(|field| {
                json.find(&format!("\"{field}\"")).unwrap_or_else(|| {
                    panic!("missing {field} in {json}");
                })
            })
            .collect();
        assert!(
            positions.windows(2).all(|pair| pair[0] < pair[1]),
            "field order in {json}"
        );
    }

    fn assert_operation_shape(json: &str, resource_kind: &str, digest: serde_json::Value) {
        assert_field_order(json, &OPERATION_FIELDS);
        let value: serde_json::Value = serde_json::from_str(json).unwrap();
        assert_eq!(value["kind"], "create");
        assert_eq!(value["key"], "sample");
        assert_eq!(value["destination"], "/dest/sample");
        assert_eq!(value["resourceKind"], resource_kind);
        assert_eq!(value["digest"], digest);
        assert!(value["diagnostic"].is_null());
        assert!(value["trustToken"].is_null());
        assert_eq!(value["trustRequired"], false);
    }

    fn assert_entry_shape(
        json: &str,
        component: &str,
        resource_kind: &str,
        digest: Option<&str>,
        trust: Option<serde_json::Value>,
        proof: Option<&str>,
    ) {
        let mut fields = Vec::new();
        if proof.is_some() {
            fields.push("trustProof");
        }
        fields.extend([
            "key",
            "plugin",
            "source",
            "destination",
            "component",
            "resourceKind",
            "sourceRepository",
        ]);
        if trust.is_some() {
            fields.push("trust");
        }
        if digest.is_some() {
            fields.push("digest");
        }
        assert_field_order(json, &fields);
        let value: serde_json::Value = serde_json::from_str(json).unwrap();
        if let Some(proof) = proof {
            assert_eq!(value["trustProof"], proof);
        } else {
            assert!(value.get("trustProof").is_none());
        }
        assert_eq!(value["component"], component);
        assert_eq!(value["resourceKind"], resource_kind);
        match digest {
            Some(digest) => assert_eq!(value["digest"], digest),
            None => assert!(value.get("digest").is_none()),
        }
        match trust {
            Some(trust) => assert_eq!(value["trust"], trust),
            None => assert!(value.get("trust").is_none()),
        }
    }

    fn operation_fixture(
        component: &'static str,
        resource: resource::PathResource,
        source: &str,
    ) -> Operation {
        Operation {
            kind: "create",
            key: "sample".into(),
            destination: "/dest/sample".into(),
            source: source.into(),
            reason: "destination is absent".into(),
            component,
            resource,
            diagnostic: None,
            trust_token: None,
            trust_required: false,
            expected_link: None,
            expected_digest: None,
        }
    }

    fn entry_fixture(
        key: &str,
        component: ComponentKind,
        resource: resource::PathResource,
        trust: Option<String>,
        trust_proof: Option<String>,
    ) -> state::Entry {
        state::Entry {
            key: key.into(),
            plugin: "pstack".into(),
            source: "plugins/sample".into(),
            destination: PathBuf::from("/dest/sample"),
            source_repository: PathBuf::from("/repo"),
            component,
            resource,
            trust,
            trust_proof,
        }
    }

    #[test]
    fn output_field_order_is_stable() {
        let operation = Operation {
            kind: "create",
            key: "skill:x".into(),
            destination: "/d/x".into(),
            source: "/s/x".into(),
            reason: "destination is absent".into(),
            component: "skills",
            resource: resource::PathResource::DirectorySymlink,
            diagnostic: None,
            trust_token: None,
            trust_required: false,
            expected_link: None,
            expected_digest: None,
        };
        let value = operations_json(&[operation]).unwrap();
        assert_field_order(&value, &OPERATION_FIELDS);
    }
}
