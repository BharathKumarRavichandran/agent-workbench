use super::resource::PathResource;
use super::*;
use crate::state::{self, Entry, State};
use rustix::fd::OwnedFd;
use rustix::fs::{self as rfs, AtFlags, FlockOperation, Mode, OFlags};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

#[cfg(test)]
#[path = "copy_parity_tests.rs"]
mod copy_parity_tests;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Action {
    Plan,
    Status,
    Trust,
    Install,
    Uninstall,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Identity(pub u64, pub u64);

#[derive(Clone, Debug)]
pub(super) struct RootSnapshot {
    pub path: PathBuf,
    pub identity: Option<Identity>,
    pub anchor: PathBuf,
    pub anchor_identity: Identity,
}

#[derive(Debug)]
pub(super) struct Plan {
    pub operations: Vec<Operation>,
    pub previous: Option<State>,
    pub next: State,
    pub state_file: PathBuf,
    pub repository: PathBuf,
    pub roots: BTreeMap<PathBuf, RootSnapshot>,
    pub authorized: BTreeMap<ComponentKind, PathBuf>,
    pub requested: Vec<ComponentKind>,
}

pub(super) fn run(cli: &Cli) -> Result<(Vec<Operation>, bool)> {
    let plan = reconcile(cli)?;
    let conflicts = has_conflicts(&plan.operations);
    if matches!(cli.action, Action::Install | Action::Uninstall) && !conflicts {
        apply(&plan, cli.action == Action::Uninstall)?;
    }
    Ok((plan.operations, conflicts))
}

pub(super) fn reconcile(cli: &Cli) -> Result<Plan> {
    let repository = canonicalize_existing(&cli.root, "repository root")?;
    let catalog = decode_catalog(&repository.join("catalog/plugins.json"))?;
    let profile_path = repository
        .join("profiles")
        .join(format!("{}.json", cli.profile));
    let profile = decode_profile(&profile_path)?;
    if profile.id != cli.profile {
        return Err("profile id must match profile filename".into());
    }
    let target_id = cli.target.id();
    let target = profile
        .targets
        .iter()
        .find(|target| target.id == target_id)
        .ok_or_else(|| {
            format!(
                "target {target_id} is not enabled by profile {}",
                profile.id
            )
        })?;
    let requested = cli
        .components
        .clone()
        .or_else(|| target.components.clone())
        .unwrap_or_else(|| vec![ComponentKind::Skills]);
    if requested.iter().any(|component| {
        !matches!(
            component,
            ComponentKind::Skills
                | ComponentKind::Agents
                | ComponentKind::Rules
                | ComponentKind::Commands
                | ComponentKind::Hooks
                | ComponentKind::Mcp
        )
    }) {
        return Err("Rust parity unit supports only path components".into());
    }
    let project_root = cli
        .project_root
        .clone()
        .or_else(|| target.project_root.as_deref().map(PathBuf::from));
    let project_root = project_root
        .map(|path| {
            let path = expand_home_path(path)?;
            if has_parent_component(&path) {
                return Err("project root must be contained".into());
            }
            let absolute = if path.is_absolute() {
                path
            } else {
                env::current_dir()
                    .map_err(|error| error.to_string())?
                    .join(path)
            };
            canonicalize_missing(&absolute)
        })
        .transpose()?;
    let destination = match (&project_root, &cli.destination, &target.destination) {
        (Some(project), None, None) => project.join(format!(".{target_id}/skills")),
        _ => resolve_destination(
            cli.destination.as_deref(),
            target.destination.as_deref(),
            cli.target,
        )?,
    };
    reject_destination_root_symlink(&destination)?;
    let state_file = canonicalize_missing(&cli.state_dir)?.join(format!(
        "agent-workbench/{}/{}/{target_id}.json",
        catalog.marketplace.id, profile.id
    ));
    if cli.action == Action::Trust {
        let roots = authorized_roots(cli.target, &destination, project_root.as_deref())?;
        if let Some(hooks) = roots.get(&ComponentKind::Hooks) {
            let journal = hooks
                .parent()
                .unwrap()
                .join(".agent-workbench-transaction.json");
            reject_destination_root_symlink(&journal)?;
            match fs::symlink_metadata(&journal) {
                Ok(_) => crate::trust::check_inspection(Some(&[]))?,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.to_string()),
            }
        }
    }

    let authorized = authorized_roots(cli.target, &destination, project_root.as_deref())?;
    let policy = crate::activation::Policy::for_target(cli.target);
    if let Some(policy) = policy.filter(|_| cli.action != Action::Trust) {
        crate::activation::recover(&state_file, &authorized, policy)?;
    }
    if let Some(policy) = policy.filter(|_| cli.action != Action::Uninstall) {
        crate::activation::inspect_requested(policy, &repository, &catalog, &profile, &requested)?;
    }
    let previous = state::load(&state_file)?;
    // Executable lifecycle remains closed for targets without a native policy.
    // Do not classify, remove, downgrade, or silently drop recorded slots/evidence.
    if previous
        .as_ref()
        .is_some_and(|state| state.schema_version == 4 && policy.is_none())
    {
        return Err("v4 executable lifecycle is not supported by Rust yet".into());
    }
    if let Some(previous) = &previous {
        if previous.marketplace != catalog.marketplace.id
            || previous.profile != profile.id
            || previous.target != target_id
        {
            return Err(
                "installation state metadata does not match marketplace, profile, and target"
                    .into(),
            );
        }
        if previous.destination_root != destination {
            return Err("recorded destination root differs from requested destination; pass the same explicit --destination used for installation".into());
        }
        authorize_recorded_roots(previous, &authorized)?;
        if let Some(policy) = policy {
            for slot in previous.slots.values() {
                let expected = match slot.component.as_str() {
                    "hooks" => Some(crate::activation::document(
                        &authorized,
                        ComponentKind::Hooks,
                        policy,
                    )),
                    "mcp" if authorized.contains_key(&ComponentKind::Mcp) => Some(
                        crate::activation::document(&authorized, ComponentKind::Mcp, policy),
                    ),
                    _ => None,
                };
                if expected.as_deref() != Some(Path::new(&slot.document)) {
                    return Err("recorded document differs from adapter-authorized path".into());
                }
            }
        }
    }

    let mut projection_conflicts = Vec::new();
    if cli.action != Action::Uninstall {
        for component in &requested {
            let supported = match cli.target {
                SkillsTarget::Cursor => matches!(
                    component,
                    ComponentKind::Skills
                        | ComponentKind::Agents
                        | ComponentKind::Rules
                        | ComponentKind::Hooks
                        | ComponentKind::Mcp
                ),
                SkillsTarget::Claude => matches!(
                    component,
                    ComponentKind::Skills
                        | ComponentKind::Agents
                        | ComponentKind::Commands
                        | ComponentKind::Hooks
                        | ComponentKind::Mcp
                ),
                _ => *component == ComponentKind::Skills,
            };
            let rejection = if !supported {
                Some((
                    "COMPONENT_UNSUPPORTED",
                    format!(
                        "{target_id} does not support current {} format",
                        component.as_str()
                    ),
                ))
            } else if !authorized.contains_key(component) {
                Some((
                    "PROJECT_ROOT_REQUIRED",
                    format!("{target_id} {} requires --project-root", component.as_str()),
                ))
            } else {
                None
            };
            if let Some((code, reason)) = rejection {
                projection_conflicts.push(Operation {
                    kind: "conflict",
                    key: format!("component:{}", component.as_str()),
                    destination: authorized
                        .get(component)
                        .unwrap_or(&destination)
                        .to_string_lossy()
                        .into_owned(),
                    source: String::new(),
                    reason,
                    component: component.as_str(),
                    resource: PathResource::DirectorySymlink,
                    diagnostic: Some(code.into()),
                    trust_token: None,
                    trust_required: false,
                    expected_link: None,
                    expected_digest: None,
                });
            }
        }
    }
    let accepted: Vec<_> = requested
        .iter()
        .copied()
        .filter(|component| {
            !projection_conflicts
                .iter()
                .any(|op| op.component == component.as_str())
        })
        .collect();
    let mut desired = if cli.action == Action::Uninstall {
        BTreeMap::new()
    } else {
        desired_entries(
            &repository,
            &catalog,
            &profile,
            &destination,
            &accepted,
            &authorized,
        )?
    };
    if let Some(policy) = policy.filter(|_| cli.action != Action::Uninstall) {
        projection_conflicts.extend(crate::activation::hook_entries(
            policy,
            &repository,
            &catalog,
            &profile,
            &accepted,
            &authorized,
            &mut desired,
        )?);
    }
    let owned = previous
        .as_ref()
        .map(|value| &value.entries)
        .cloned()
        .unwrap_or_default();
    let mut operations = projection_conflicts;
    let mut next_entries = BTreeMap::new();
    let mut ordered_desired: Vec<_> = desired.iter().collect();
    ordered_desired.sort_by_key(|(key, entry)| {
        (
            entry.component == ComponentKind::Hooks,
            catalog
                .plugins
                .iter()
                .position(|plugin| plugin.id == entry.plugin),
            accepted
                .iter()
                .position(|component| *component == entry.component),
            *key,
        )
    });
    for (key, entry) in ordered_desired {
        let source = entry.source_repository.join(&entry.source);
        let prior = owned.get(key);
        let same_owner = prior.is_some_and(|prior| {
            prior.destination == entry.destination
                && prior.component == entry.component
                && std::mem::discriminant(&prior.resource)
                    == std::mem::discriminant(&entry.resource)
        });
        let (kind, reason, expected_link, expected_digest) =
            classify(entry, &source, prior, same_owner, cli.adopt)?;
        operations.push(operation(
            kind,
            entry,
            &source,
            reason,
            expected_link,
            expected_digest,
        ));
        next_entries.insert(key.clone(), entry.clone());
    }
    if cli.action == Action::Uninstall {
        operations.clear();
        next_entries.clear();
        for (key, prior) in &owned {
            if let Some(operation) = removal(key, prior, "remove managed entry")? {
                operations.push(operation);
            }
        }
    } else {
        for (key, prior) in &owned {
            if !desired.contains_key(key) {
                if let Some(operation) = removal(key, prior, "managed entry is no longer selected")?
                {
                    operations.push(operation);
                }
            }
        }
    }
    let mut destination_roots = BTreeMap::from([(ComponentKind::Skills, destination.clone())]);
    for entry in next_entries.values() {
        if let Some(root) = authorized.get(&entry.component) {
            destination_roots.insert(entry.component, root.clone());
        }
    }
    let next = State {
        schema_version: 3,
        slots: BTreeMap::new(),
        repository: repository.clone(),
        marketplace: catalog.marketplace.id.clone(),
        profile: profile.id.clone(),
        target: target_id.into(),
        destination_root: destination.clone(),
        entries: next_entries,
        destination_roots,
    };
    let document_parents: Vec<_> = if cli.target == SkillsTarget::Claude {
        authorized
            .get(&ComponentKind::Hooks)
            .and_then(|p| p.parent())
            .map(Path::to_path_buf)
            .into_iter()
            .chain(project_root.clone())
            .collect()
    } else {
        Vec::new()
    };
    let roots = authorized
        .values()
        .chain(document_parents.iter())
        .map(|path| Ok((path.clone(), snapshot_root(path)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    Ok(Plan {
        operations,
        previous,
        next,
        state_file,
        repository,
        roots,
        authorized,
        requested: accepted,
    })
}

fn desired_entries(
    repository: &Path,
    catalog: &Catalog,
    profile: &Profile,
    destination: &Path,
    components: &[ComponentKind],
    roots: &BTreeMap<ComponentKind, PathBuf>,
) -> Result<BTreeMap<String, Entry>> {
    let source_by_id: BTreeMap<_, _> = catalog
        .sources
        .iter()
        .map(|source| (source.id.as_str(), source))
        .collect();
    if source_by_id.len() != catalog.sources.len() {
        return Err("source ids must be unique".into());
    }
    let known_plugins: BTreeSet<_> = catalog
        .plugins
        .iter()
        .map(|plugin| plugin.id.as_str())
        .collect();
    let excluded: BTreeSet<_> = profile.plugins.exclude.iter().map(String::as_str).collect();
    if !excluded.is_subset(&known_plugins) {
        return Err("profile excludes unknown plugins".into());
    }
    let selected: BTreeSet<&str> = if profile.plugins.include == ["*"] {
        known_plugins.clone()
    } else {
        let selected: BTreeSet<_> = profile.plugins.include.iter().map(String::as_str).collect();
        if !selected.is_subset(&known_plugins) {
            return Err("profile includes unknown plugins".into());
        }
        selected
    };
    let mut entries = BTreeMap::new();
    for plugin in &catalog.plugins {
        if !selected.contains(plugin.id.as_str()) || excluded.contains(plugin.id.as_str()) {
            continue;
        }
        let source = source_by_id.get(plugin.source.as_str()).ok_or_else(|| {
            format!(
                "plugin {} references unknown source {}",
                plugin.id, plugin.source
            )
        })?;
        let source_root = resolve_repo_path(repository, &source.path)?;
        if components.contains(&ComponentKind::Skills) {
            let artifact = plugin
                .artifacts
                .get("agent-skills")
                .ok_or_else(|| format!("plugin {} has no agent-skills artifact", plugin.id))?;
            let artifact_root = resolve_repo_path(&source_root, &artifact.path)?;
            for (name, skill_root) in discover_skills(&artifact_root)? {
                let key = format!("skill:{name}");
                if entries.contains_key(&key) {
                    return Err(format!("duplicate exported skill name: {name}"));
                }
                entries.insert(
                    key.clone(),
                    Entry {
                        key,
                        plugin: plugin.id.clone(),
                        source: skill_root
                            .strip_prefix(repository)
                            .map_err(|_| "skill source escapes checkout")?
                            .to_string_lossy()
                            .replace('\\', "/"),
                        destination: destination.join(name),
                        source_repository: repository.to_path_buf(),
                        component: ComponentKind::Skills,
                        resource: PathResource::DirectorySymlink,
                        trust: None,
                        trust_proof: None,
                    },
                );
            }
        }
        for (component, artifact_name, suffix) in [
            (ComponentKind::Agents, "agents", ".md"),
            (ComponentKind::Rules, "rules", ".mdc"),
            (ComponentKind::Commands, "commands", ".md"),
        ] {
            if !components.contains(&component) {
                continue;
            }
            let Some(artifact) = plugin.artifacts.get(artifact_name) else {
                continue;
            };
            let root = roots.get(&component).ok_or_else(|| {
                format!(
                    "{} {} requires --project-root",
                    "cursor",
                    component.as_str()
                )
            })?;
            let artifact_root = canonicalize_missing(&source_root.join(&artifact.path))?;
            if !artifact_root.starts_with(&source_root) {
                return Err("repository path escapes checkout".into());
            }
            for (name, source) in discover_files(&artifact_root, suffix, component)? {
                let key = format!("{}:{name}", component_key_prefix(component));
                if entries.contains_key(&key) {
                    return Err(format!("duplicate exported component name: {name}"));
                }
                let digest = file_digest(&source)?
                    .ok_or_else(|| format!("copy requires a regular file: {}", source.display()))?;
                entries.insert(
                    key.clone(),
                    Entry {
                        key,
                        plugin: plugin.id.clone(),
                        source: source
                            .strip_prefix(repository)
                            .map_err(|_| "component source escapes checkout")?
                            .to_string_lossy()
                            .replace('\\', "/"),
                        destination: root.join(&name),
                        source_repository: repository.to_path_buf(),
                        component,
                        resource: PathResource::FileCopy(crate::resource::ContentDigest::parse(
                            &digest,
                        )?),
                        trust: None,
                        trust_proof: None,
                    },
                );
            }
        }
    }
    Ok(entries)
}

fn component_key_prefix(component: ComponentKind) -> &'static str {
    match component {
        ComponentKind::Skills => "skill",
        ComponentKind::Agents => "agent",
        ComponentKind::Rules => "rule",
        ComponentKind::Commands => "command",
        ComponentKind::Hooks => "hook",
        ComponentKind::Mcp => "mcp",
    }
}

fn discover_files(
    root: &Path,
    suffix: &str,
    component: ComponentKind,
) -> Result<Vec<(String, PathBuf)>> {
    reject_symlink_tree(root)?;
    let mut found = Vec::new();
    for entry in fs::read_dir(root).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let path = entry.path();
        let file_type = entry.file_type().map_err(|error| error.to_string())?;
        if file_type.is_symlink() || !file_type.is_file() {
            continue;
        }
        let name = entry.file_name();
        let name = name.to_str().ok_or("component filename is not UTF-8")?;
        if path.extension().and_then(|value| value.to_str()) != suffix.strip_prefix('.') {
            continue;
        }
        validate_document(&path, component)?;
        found.push((name.to_owned(), path));
    }
    found.sort();
    Ok(found)
}

fn validate_document(path: &Path, component: ComponentKind) -> Result<()> {
    // DocumentDirectoryInspector uses universal newlines and the last nonempty
    // value for each stripped frontmatter key; it does not parse YAML.
    let text = fs::read_to_string(path).map_err(|error| error.to_string())?;
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    if !text.starts_with("---\n") {
        return Err(format!(
            "frontmatter must start at byte zero: {}",
            path.display()
        ));
    }
    let closing = text[4..]
        .find("\n---\n")
        .map(|index| index + 4)
        .ok_or_else(|| format!("frontmatter is not closed: {}", path.display()))?;
    let mut values = BTreeMap::new();
    for line in text[4..closing].lines() {
        if let Some((key, value)) = line.split_once(':') {
            let value = value.trim().trim_matches(['\'', '"']);
            if !key.trim().is_empty() && !value.is_empty() {
                values.insert(key.trim(), value);
            }
        }
    }
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or("invalid component filename")?;
    let name = if component == ComponentKind::Agents {
        values.get("name").copied().unwrap_or("")
    } else {
        stem
    };
    if !safe_filename(name) {
        return Err("component name must be one safe filename component".into());
    }
    if component == ComponentKind::Agents && name != stem {
        return Err(format!(
            "component name must match filename stem: {}",
            path.display()
        ));
    }
    if matches!(component, ComponentKind::Agents | ComponentKind::Rules)
        && !values.contains_key("description")
    {
        return Err(format!(
            "component frontmatter requires description: {}",
            path.display()
        ));
    }
    Ok(())
}

fn operation(
    kind: &'static str,
    entry: &Entry,
    source: &Path,
    reason: &'static str,
    expected_link: Option<PathBuf>,
    expected_digest: Option<String>,
) -> Operation {
    Operation {
        kind,
        key: entry.key.clone(),
        destination: entry.destination.to_string_lossy().into_owned(),
        source: source.to_string_lossy().into_owned(),
        reason: reason.into(),
        component: entry.component.as_str(),
        resource: entry.resource.clone(),
        diagnostic: None,
        trust_token: None,
        trust_required: false,
        expected_link,
        expected_digest,
    }
}

fn authorize_recorded_roots(
    previous: &State,
    authorized: &BTreeMap<ComponentKind, PathBuf>,
) -> Result<()> {
    for (component, recorded) in &previous.destination_roots {
        if authorized.get(component) != Some(recorded) {
            return Err("recorded destination root differs from adapter-authorized root; pass the same --project-root".into());
        }
    }
    Ok(())
}

fn authorized_roots(
    target: SkillsTarget,
    destination: &Path,
    project_root: Option<&Path>,
) -> Result<BTreeMap<ComponentKind, PathBuf>> {
    let mut roots = BTreeMap::from([(ComponentKind::Skills, destination.to_path_buf())]);
    if !matches!(target, SkillsTarget::Cursor | SkillsTarget::Claude) {
        return Ok(roots);
    }
    let base_name = match target {
        SkillsTarget::Cursor => ".cursor",
        SkillsTarget::Claude => ".claude",
        _ => unreachable!(),
    };
    let home_base = match project_root {
        Some(project) => project.join(base_name),
        None => home_dir()?.join(base_name),
    };
    let capability_base = home_base.join("skills");
    let capability_parent = capability_base
        .parent()
        .ok_or("target home has no parent")?
        .to_path_buf();
    roots.insert(ComponentKind::Agents, capability_parent.join("agents"));
    if target == SkillsTarget::Cursor && project_root.is_some() {
        roots.insert(ComponentKind::Rules, capability_parent.join("rules"));
    }
    if target == SkillsTarget::Claude {
        roots.insert(ComponentKind::Commands, capability_parent.join("commands"));
    }
    roots.insert(
        ComponentKind::Hooks,
        home_base.join(".agent-workbench-hooks"),
    );
    if target == SkillsTarget::Cursor {
        roots.insert(ComponentKind::Mcp, home_base);
    } else if let Some(project) = project_root {
        roots.insert(ComponentKind::Mcp, project.to_path_buf());
    }
    Ok(roots)
}

fn removal(_key: &str, entry: &Entry, reason: &'static str) -> Result<Option<Operation>> {
    match fs::symlink_metadata(&entry.destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("{}: {error}", entry.destination.display())),
        Ok(_) if matches_owned(entry)? => {
            let (expected_link, expected_digest) = ownership_evidence(entry)?;
            Ok(Some(operation(
                "remove-owned",
                entry,
                &entry.source_repository.join(&entry.source),
                reason,
                expected_link,
                expected_digest,
            )))
        }
        Ok(_) => Ok(Some(operation(
            "conflict",
            entry,
            &entry.source_repository.join(&entry.source),
            "managed path was modified",
            None,
            None,
        ))),
    }
}

fn classify(
    entry: &Entry,
    source: &Path,
    prior: Option<&Entry>,
    same_owner: bool,
    adopt: bool,
) -> Result<(&'static str, &'static str, Option<PathBuf>, Option<String>)> {
    match fs::symlink_metadata(&entry.destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Ok(("create", "destination is absent", None, None))
        }
        Err(error) => Err(format!("{}: {error}", entry.destination.display())),
        Ok(_) if same_owner && !matches_owned(prior.unwrap())? => {
            Ok(("conflict", "managed path was modified", None, None))
        }
        Ok(_) if matches_resource(&entry.destination, source, &entry.resource)? => {
            if same_owner {
                Ok(("keep", "managed resource already matches", None, None))
            } else if adopt {
                Ok((
                    "adopt",
                    "matching unmanaged resource explicitly adopted",
                    None,
                    None,
                ))
            } else {
                Ok((
                    "conflict",
                    "matching resource is not recorded as owned; use --adopt",
                    None,
                    None,
                ))
            }
        }
        Ok(_) if same_owner && matches_owned(prior.unwrap())? => {
            let (expected_link, expected_digest) = ownership_evidence(prior.unwrap())?;
            Ok((
                "replace-owned",
                "managed resource has a new source",
                expected_link,
                expected_digest,
            ))
        }
        Ok(_) => Ok((
            "conflict",
            "destination is a foreign file or link",
            None,
            None,
        )),
    }
}

fn ownership_evidence(entry: &Entry) -> Result<(Option<PathBuf>, Option<String>)> {
    match &entry.resource {
        PathResource::DirectorySymlink => Ok((
            Some(
                fs::read_link(&entry.destination)
                    .map_err(|error| format!("{}: {error}", entry.destination.display()))?,
            ),
            None,
        )),
        PathResource::FileCopy(digest) | PathResource::DirectoryCopy(digest) => {
            Ok((None, Some(digest.as_str().to_owned())))
        }
    }
}

fn matches_resource(destination: &Path, source: &Path, resource: &PathResource) -> Result<bool> {
    match resource {
        PathResource::DirectorySymlink => Ok(fs::symlink_metadata(destination)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
            && same_link(destination, source)?),
        PathResource::FileCopy(digest) => {
            Ok(file_digest(destination)?.as_deref() == Some(digest.as_str()))
        }
        PathResource::DirectoryCopy(digest) => Ok(destination
            .parent()
            .and_then(|parent| crate::bundles::open_directory(parent).ok())
            .and_then(|parent| crate::bundles::read_tree(destination.file_name()?, &parent).ok())
            .and_then(|tree| tree.digest().ok())
            .as_deref()
            == Some(digest.as_str())),
    }
}

fn matches_owned(entry: &Entry) -> Result<bool> {
    matches_resource(
        &entry.destination,
        &entry.source_repository.join(&entry.source),
        &entry.resource,
    )
}

pub(super) fn read_regular(repository: &Path, path: &Path) -> Result<Vec<u8>> {
    let relative = path
        .strip_prefix(repository)
        .map_err(|_| "copy source escapes checkout")?;
    let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW;
    let mut directory =
        rfs::open(repository, flags, Mode::empty()).map_err(|error| error.to_string())?;
    for component in relative
        .parent()
        .ok_or("copy source has no parent")?
        .components()
    {
        if !matches!(component, Component::Normal(_)) {
            return Err("copy source escapes checkout".into());
        }
        directory = rfs::openat(&directory, component.as_os_str(), flags, Mode::empty())
            .map_err(|error| error.to_string())?;
    }
    let file = rfs::openat(
        &directory,
        relative.file_name().ok_or("copy source has no filename")?,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    )
    .map_err(|error| format!("{}: {error}", path.display()))?;
    let metadata = rfs::fstat(&file).map_err(|error| error.to_string())?;
    if !rustix::fs::FileType::from_raw_mode(metadata.st_mode).is_file() {
        return Err(format!("copy requires a regular file: {}", path.display()));
    }
    let mut reader = std::fs::File::from(file);
    let mut content = Vec::new();
    std::io::Read::read_to_end(&mut reader, &mut content).map_err(|error| error.to_string())?;
    Ok(content)
}

fn file_digest(path: &Path) -> Result<Option<String>> {
    let file = match rfs::open(
        path,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    ) {
        Ok(value) => value,
        Err(rustix::io::Errno::NOENT | rustix::io::Errno::LOOP) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    digest_descriptor(file)
}

fn digest_descriptor(file: OwnedFd) -> Result<Option<String>> {
    use sha2::{Digest, Sha256};
    let metadata = rfs::fstat(&file).map_err(|error| error.to_string())?;
    if !rustix::fs::FileType::from_raw_mode(metadata.st_mode).is_file() {
        return Ok(None);
    }
    let mut reader = std::fs::File::from(file);
    let mut hasher = Sha256::new();
    std::io::copy(&mut reader, &mut hasher).map_err(|error| error.to_string())?;
    Ok(Some(format!("{:x}", hasher.finalize())))
}

fn identity(path: &Path) -> Result<Option<Identity>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(format!(
            "destination root may not be a symlink: {}",
            path.display()
        )),
        Ok(metadata) if metadata.is_dir() => Ok(Some(Identity(metadata.dev(), metadata.ino()))),
        Ok(_) => Err(format!(
            "destination root must be a directory: {}",
            path.display()
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

fn snapshot_root(path: &Path) -> Result<RootSnapshot> {
    let expected = identity(path)?;
    let mut anchor = path.to_path_buf();
    let anchor_identity = loop {
        if let Some(value) = identity(&anchor)? {
            break value;
        }
        anchor = anchor
            .parent()
            .ok_or_else(|| {
                format!(
                    "destination has no existing directory ancestor: {}",
                    path.display()
                )
            })?
            .to_path_buf();
    };
    Ok(RootSnapshot {
        path: path.to_path_buf(),
        identity: expected,
        anchor,
        anchor_identity,
    })
}

struct LockedState {
    _file: std::fs::File,
}

impl LockedState {
    fn acquire(path: &Path) -> Result<Self> {
        let parent = path.parent().ok_or("state path has no parent")?;
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        let lock = path.with_file_name(format!(
            ".{}.lock",
            path.file_name().unwrap().to_string_lossy()
        ));
        use std::os::unix::fs::OpenOptionsExt;
        let file = OpenOptions::new()
            .mode(0o600)
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(lock)
            .map_err(|error| error.to_string())?;
        rfs::flock(&file, FlockOperation::NonBlockingLockExclusive).map_err(|_| {
            format!(
                "installation state is locked by another operation: {}",
                path.display()
            )
        })?;
        Ok(Self { _file: file })
    }
}

struct DestinationRoot {
    fd: OwnedFd,
    path: PathBuf,
    identity: Identity,
}

impl DestinationRoot {
    fn open(snapshot: &RootSnapshot, create: bool) -> Result<Self> {
        let mut fd = rfs::open(
            &snapshot.anchor,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
            Mode::empty(),
        )
        .map_err(|error| {
            format!(
                "destination ancestor changed since planning: {}: {error}",
                snapshot.anchor.display()
            )
        })?;
        let stat = rfs::fstat(&fd).map_err(|error| error.to_string())?;
        if Identity(stat.st_dev as u64, stat.st_ino as u64) != snapshot.anchor_identity {
            return Err(format!(
                "destination ancestor changed since planning: {}",
                snapshot.anchor.display()
            ));
        }
        let relative = snapshot
            .path
            .strip_prefix(&snapshot.anchor)
            .map_err(|_| "destination is outside pinned ancestor")?;
        for component in relative.components() {
            let name = component.as_os_str();
            let next = match rfs::openat(
                &fd,
                name,
                OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
                Mode::empty(),
            ) {
                Ok(value) => value,
                Err(error) if error == rustix::io::Errno::NOENT && create => {
                    rfs::mkdirat(&fd, name, Mode::from_raw_mode(0o755))
                        .map_err(|error| error.to_string())?;
                    rfs::openat(
                        &fd,
                        name,
                        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
                        Mode::empty(),
                    )
                    .map_err(|error| error.to_string())?
                }
                Err(error) => {
                    return Err(format!(
                        "destination root changed since planning: {}: {error}",
                        snapshot.path.display()
                    ))
                }
            };
            fd = next;
        }
        let stat = rfs::fstat(&fd).map_err(|error| error.to_string())?;
        let actual = Identity(stat.st_dev as u64, stat.st_ino as u64);
        if snapshot.identity.is_some_and(|expected| expected != actual) {
            return Err(format!(
                "destination root changed since planning: {}",
                snapshot.path.display()
            ));
        }
        Ok(Self {
            fd,
            path: snapshot.path.clone(),
            identity: actual,
        })
    }

    fn verify(&self) -> Result<()> {
        let descriptor = rfs::fstat(&self.fd).map_err(|error| error.to_string())?;
        if Identity(descriptor.st_dev as u64, descriptor.st_ino as u64) != self.identity {
            return Err(format!(
                "destination root changed since planning: {}",
                self.path.display()
            ));
        }
        let live = identity(&self.path)?;
        if live != Some(self.identity) {
            return Err(format!(
                "destination root changed since planning: {}",
                self.path.display()
            ));
        }
        Ok(())
    }

    fn target(&self, name: &str) -> Result<PathBuf> {
        let value =
            rfs::readlinkat(&self.fd, name, Vec::new()).map_err(|error| error.to_string())?;
        Ok(PathBuf::from(value.to_string_lossy().into_owned()))
    }

    fn create(&self, name: &str, source: &Path) -> Result<()> {
        let target = relative_link(source, &self.path)?;
        rfs::symlinkat(&target, &self.fd, name).map_err(|_| {
            format!(
                "destination changed since planning: {}",
                self.path.join(name).display()
            )
        })
    }

    fn verify_created(&self, name: &str, source: &Path) -> Result<()> {
        if let Err(error) = self.verify() {
            let expected = relative_link(source, &self.path)?;
            let rollback = self
                .quarantine(name, &expected)
                .and_then(|moved| self.discard(moved));
            return match rollback {
                Ok(()) => Err(error),
                Err(rollback_error) => Err(format!(
                    "{error}; failed to roll back created resource {}: {rollback_error}",
                    self.path.join(name).display()
                )),
            };
        }
        Ok(())
    }

    fn create_verified(&self, name: &str, source: &Path) -> Result<()> {
        self.create(name, source)?;
        self.verify_created(name, source)
    }

    fn quarantine(&self, name: &str, expected: &Path) -> Result<Quarantine> {
        let directory = format!(".agent-workbench-quarantine-{}-{name}", std::process::id());
        rfs::mkdirat(&self.fd, &directory, Mode::from_raw_mode(0o700))
            .map_err(|error| error.to_string())?;
        let qfd = rfs::openat(
            &self.fd,
            &directory,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
            Mode::empty(),
        )
        .map_err(|error| error.to_string())?;
        if let Err(error) = rfs::renameat(&self.fd, name, &qfd, "entry") {
            let _ = rfs::unlinkat(&self.fd, &directory, AtFlags::REMOVEDIR);
            return Err(error.to_string());
        }
        let moved = Quarantine { directory, qfd };
        let actual = rfs::readlinkat(&moved.qfd, "entry", Vec::new())
            .ok()
            .map(|value| PathBuf::from(value.to_string_lossy().into_owned()));
        if actual.as_deref() != Some(expected) {
            self.restore(name, moved)?;
            return Err(format!(
                "destination changed since planning: {}",
                self.path.join(name).display()
            ));
        }
        Ok(moved)
    }

    fn quarantine_file(&self, name: &str, expected_digest: &str) -> Result<Quarantine> {
        #[cfg(test)]
        copy_parity_tests::event("quarantine", &self.path.join(name))?;
        let directory = format!(".agent-workbench-quarantine-{}-{name}", std::process::id());
        rfs::mkdirat(&self.fd, &directory, Mode::from_raw_mode(0o700))
            .map_err(|error| error.to_string())?;
        let qfd = rfs::openat(
            &self.fd,
            &directory,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
            Mode::empty(),
        )
        .map_err(|error| error.to_string())?;
        if let Err(error) = rfs::renameat(&self.fd, name, &qfd, "entry") {
            let _ = rfs::unlinkat(&self.fd, &directory, AtFlags::REMOVEDIR);
            return Err(error.to_string());
        }
        let moved = Quarantine { directory, qfd };
        #[cfg(test)]
        copy_parity_tests::event("quarantined", &self.path)?;
        let actual = rfs::openat(
            &moved.qfd,
            "entry",
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
            Mode::empty(),
        )
        .map_err(|error| error.to_string())
        .and_then(digest_descriptor);
        let actual = match actual {
            Ok(actual) => actual,
            Err(error) => {
                self.restore(name, moved)?;
                return Err(error);
            }
        };
        if actual.as_deref() != Some(expected_digest) {
            self.restore(name, moved)?;
            return Err(format!(
                "destination changed since planning: {}",
                self.path.join(name).display()
            ));
        }
        Ok(moved)
    }

    fn publish_file(&self, name: &str, content: &[u8]) -> Result<()> {
        #[cfg(test)]
        copy_parity_tests::event("publish", &self.path)?;
        let temporary = format!(".agent-workbench-copy-{}-{}", std::process::id(), name);
        let file = rfs::openat(
            &self.fd,
            &temporary,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL,
            Mode::from_raw_mode(0o644),
        )
        .map_err(|error| error.to_string())?;
        let mut writer = std::fs::File::from(file);
        let write = (|| {
            std::io::Write::write_all(&mut writer, content).map_err(|error| error.to_string())?;
            writer.sync_all().map_err(|error| error.to_string())
        })();
        drop(writer);
        if let Err(error) = write {
            let _ = rfs::unlinkat(&self.fd, &temporary, AtFlags::empty());
            return Err(error);
        }
        if let Err(error) = rfs::linkat(&self.fd, &temporary, &self.fd, name, AtFlags::empty()) {
            let _ = rfs::unlinkat(&self.fd, &temporary, AtFlags::empty());
            return Err(error.to_string());
        }
        rfs::unlinkat(&self.fd, &temporary, AtFlags::empty()).map_err(|error| error.to_string())?;
        #[cfg(test)]
        copy_parity_tests::event("published", &self.path)?;
        rfs::fsync(&self.fd).map_err(|error| error.to_string())
    }

    fn restore(&self, name: &str, moved: Quarantine) -> Result<()> {
        match rfs::statat(&self.fd, name, AtFlags::SYMLINK_NOFOLLOW) {
            Err(rustix::io::Errno::NOENT) => {}
            _ => {
                return Err(format!(
                "destination changed while restoring quarantined entry; recover it from {}/entry",
                self.path.join(&moved.directory).display()
            ))
            }
        }
        rfs::renameat(&moved.qfd, "entry", &self.fd, name).map_err(|error| {
            format!(
                "destination changed while restoring quarantined entry; recover it from {}/entry: {error}",
                self.path.join(&moved.directory).display()
            )
        })?;
        drop(moved.qfd);
        rfs::unlinkat(&self.fd, &moved.directory, AtFlags::REMOVEDIR)
            .map_err(|error| error.to_string())
    }

    fn discard(&self, moved: Quarantine) -> Result<()> {
        rfs::unlinkat(&moved.qfd, "entry", AtFlags::empty()).map_err(|error| error.to_string())?;
        drop(moved.qfd);
        rfs::unlinkat(&self.fd, &moved.directory, AtFlags::REMOVEDIR)
            .map_err(|error| error.to_string())
    }
}

struct Quarantine {
    directory: String,
    qfd: OwnedFd,
}

fn validate_operation_source(plan: &Plan, operation: &Operation) -> Result<Option<Vec<u8>>> {
    let source = Path::new(&operation.source);
    if let PathResource::FileCopy(digest) = &operation.resource {
        let content = read_regular(&plan.repository, source)?;
        use sha2::{Digest, Sha256};
        if format!("{:x}", Sha256::digest(&content)) != digest.as_str() {
            return Err(format!(
                "copy source changed since planning: {}",
                source.display()
            ));
        }
        Ok(Some(content))
    } else {
        validate_source(&plan.repository, source)?;
        Ok(None)
    }
}

fn apply(plan: &Plan, uninstall: bool) -> Result<()> {
    let _lock = LockedState::acquire(&plan.state_file)?;
    if state::load(&plan.state_file)? != plan.previous {
        return Err("installation state changed since planning".into());
    }
    for operation in &plan.operations {
        if operation.kind != "remove-owned" {
            validate_operation_source(plan, operation)?;
        }
    }
    if plan.operations.is_empty() {
        return if uninstall {
            state::remove(&plan.state_file)
        } else {
            state::write(&plan.state_file, &plan.next)
        };
    }
    let needed: BTreeSet<_> = plan
        .operations
        .iter()
        .map(|operation| {
            Path::new(&operation.destination)
                .parent()
                .ok_or_else(|| "destination has no parent".to_owned())
        })
        .collect::<Result<_>>()?;
    let roots = needed
        .into_iter()
        .map(|path| {
            let snapshot = plan
                .roots
                .get(path)
                .ok_or("destination root lacks planning snapshot")?;
            Ok((path, DestinationRoot::open(snapshot, !uninstall)?))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut entries = plan
        .previous
        .as_ref()
        .map(|value| value.entries.clone())
        .unwrap_or_default();
    for operation in &plan.operations {
        let destination = Path::new(&operation.destination);
        let parent = destination.parent().ok_or("destination has no parent")?;
        let root = roots.get(parent).ok_or("destination root was not opened")?;
        root.verify()?;
        let name = destination
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or("destination name is not UTF-8")?;
        match operation.kind {
            "keep" | "adopt" => {
                validate_operation_source(plan, operation)?;
                if let PathResource::FileCopy(digest) = &operation.resource {
                    if file_digest(Path::new(&operation.destination))?.as_deref()
                        != Some(digest.as_str())
                    {
                        return Err(format!(
                            "destination changed since planning: {}",
                            destination.display()
                        ));
                    }
                } else {
                    validate_source(&plan.repository, Path::new(&operation.source))?;
                    if root
                        .target(name)
                        .ok()
                        .as_deref()
                        .map(|target| canonicalize_missing(&root.path.join(target)))
                        .transpose()?
                        != Some(canonicalize_missing(Path::new(&operation.source))?)
                    {
                        return Err(format!(
                            "destination changed since planning: {}",
                            destination.display()
                        ));
                    }
                }
                entries.insert(
                    operation.key.clone(),
                    plan.next.entries[&operation.key].clone(),
                );
                write_progress(plan, &entries)?;
            }
            "create" => {
                let source = Path::new(&operation.source);
                if matches!(operation.resource, PathResource::FileCopy(_)) {
                    let content =
                        validate_operation_source(plan, operation)?.ok_or("copy lacks content")?;
                    root.publish_file(name, &content)?;
                    if let Err(error) = root.verify() {
                        let digest = operation.resource.digest().ok_or("copy lacks digest")?;
                        let moved = root.quarantine_file(name, digest.as_str())?;
                        root.discard(moved)?;
                        return Err(error);
                    }
                } else {
                    validate_source(&plan.repository, source)?;
                    root.create_verified(name, source)?;
                }
                entries.insert(
                    operation.key.clone(),
                    plan.next.entries[&operation.key].clone(),
                );
                if let Err(error) = write_progress(plan, &entries) {
                    if let PathResource::FileCopy(digest) = &operation.resource {
                        let moved = root.quarantine_file(name, digest.as_str())?;
                        root.discard(moved)?;
                    } else {
                        let expected = relative_link(Path::new(&operation.source), &root.path)?;
                        let moved = root.quarantine(name, &expected)?;
                        root.discard(moved)?;
                    }
                    return Err(error);
                }
            }
            "remove-owned" => {
                let moved = if let Some(expected) = operation.expected_link.as_deref() {
                    root.quarantine(name, expected)?
                } else if let Some(digest) = operation.expected_digest.as_deref() {
                    root.quarantine_file(name, digest)?
                } else {
                    return Err("owned removal lacks source evidence".into());
                };
                if let Err(error) = root.verify() {
                    root.restore(name, moved)?;
                    return Err(error);
                }
                entries.remove(&operation.key);
                if let Err(error) = write_progress(plan, &entries) {
                    root.restore(name, moved)?;
                    return Err(error);
                }
                root.discard(moved)?;
            }
            "replace-owned" => {
                let source = Path::new(&operation.source);
                if matches!(operation.resource, PathResource::FileCopy(_)) {
                    let content =
                        validate_operation_source(plan, operation)?.ok_or("copy lacks content")?;
                    let moved = operation
                        .expected_digest
                        .as_deref()
                        .ok_or("owned replacement lacks source evidence")?;
                    let moved = root.quarantine_file(name, moved)?;
                    if let Err(error) = root
                        .publish_file(name, &content)
                        .and_then(|_| root.verify())
                    {
                        let _ = rfs::unlinkat(&root.fd, name, AtFlags::empty());
                        root.restore(name, moved)?;
                        return Err(error);
                    }
                    entries.insert(
                        operation.key.clone(),
                        plan.next.entries[&operation.key].clone(),
                    );
                    if let Err(error) = write_progress(plan, &entries) {
                        rfs::unlinkat(&root.fd, name, AtFlags::empty())
                            .map_err(|value| value.to_string())?;
                        root.restore(name, moved)?;
                        return Err(error);
                    }
                    root.discard(moved)?;
                } else {
                    validate_source(&plan.repository, source)?;
                    let expected = operation
                        .expected_link
                        .as_deref()
                        .ok_or("owned replacement lacks source evidence")?;
                    let moved = root.quarantine(name, expected)?;
                    if let Err(error) = root.create(name, source).and_then(|_| root.verify()) {
                        let _ = rfs::unlinkat(&root.fd, name, AtFlags::empty());
                        root.restore(name, moved)?;
                        return Err(error);
                    }
                    entries.insert(
                        operation.key.clone(),
                        plan.next.entries[&operation.key].clone(),
                    );
                    if let Err(error) = write_progress(plan, &entries) {
                        rfs::unlinkat(&root.fd, name, AtFlags::empty())
                            .map_err(|value| value.to_string())?;
                        root.restore(name, moved)?;
                        return Err(error);
                    }
                    root.discard(moved)?;
                }
            }
            _ => {}
        }
    }
    if uninstall {
        state::remove(&plan.state_file)
    } else {
        state::write(&plan.state_file, &plan.next)
    }
}

fn write_progress(plan: &Plan, entries: &BTreeMap<String, Entry>) -> Result<()> {
    #[cfg(test)]
    copy_parity_tests::event("progress", &plan.state_file)?;
    let mut destination_roots = plan
        .previous
        .as_ref()
        .map(|state| state.destination_roots.clone())
        .unwrap_or_default();
    destination_roots.extend(plan.next.destination_roots.clone());
    destination_roots.retain(|component, _| {
        *component == ComponentKind::Skills
            || entries.values().any(|entry| entry.component == *component)
    });
    let progress = State {
        schema_version: 3,
        slots: BTreeMap::new(),
        repository: plan.next.repository.clone(),
        marketplace: plan.next.marketplace.clone(),
        profile: plan.next.profile.clone(),
        target: plan.next.target.clone(),
        destination_root: plan.next.destination_root.clone(),
        entries: entries.clone(),
        destination_roots,
    };
    state::write(&plan.state_file, &progress)
}

pub(crate) fn validate_source(repository: &Path, source: &Path) -> Result<()> {
    let relative = source
        .strip_prefix(repository)
        .map_err(|_| format!("skill source escapes checkout: {}", source.display()))?;
    let resolved = resolve_repo_path(repository, &relative.to_string_lossy())?;
    reject_symlink_tree(&resolved)?;
    let skill = resolved.join("SKILL.md");
    let metadata =
        fs::symlink_metadata(&skill).map_err(|error| format!("{}: {error}", skill.display()))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(format!(
            "SKILL.md must be a regular file: {}",
            skill.display()
        ));
    }
    parse_frontmatter(&skill)?;
    Ok(())
}

pub(crate) fn relative_link(source: &Path, parent: &Path) -> Result<PathBuf> {
    let source_parts: Vec<_> = source.components().collect();
    let parent_parts: Vec<_> = parent.components().collect();
    let common = source_parts
        .iter()
        .zip(&parent_parts)
        .take_while(|(left, right)| left == right)
        .count();
    if common == 0 {
        return Err("source and destination have no common root".into());
    }
    let mut result = PathBuf::new();
    for _ in common..parent_parts.len() {
        result.push("..");
    }
    for part in &source_parts[common..] {
        result.push(part.as_os_str());
    }
    Ok(result)
}

#[cfg(test)]
mod race_tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::TempDir;

    struct Fixture {
        _temp: TempDir,
        root: PathBuf,
        destination: PathBuf,
        state_root: PathBuf,
    }

    impl Fixture {
        fn new(names: &[&str]) -> Self {
            let temp = TempDir::new().unwrap();
            let base = temp.path().canonicalize().unwrap();
            let root = base.join("repository");
            fs::create_dir_all(root.join("plugins/pstack/skills")).unwrap();
            fs::create_dir_all(root.join("catalog")).unwrap();
            fs::create_dir_all(root.join("profiles")).unwrap();
            for name in names {
                let source = root.join("plugins/pstack/skills").join(name);
                fs::create_dir_all(&source).unwrap();
                fs::write(
                    source.join("SKILL.md"),
                    format!("---\nname: {name}\ndescription: Example.\n---\n"),
                )
                .unwrap();
            }
            fs::write(
                root.join("catalog/plugins.json"),
                r#"{"schemaVersion":2,"marketplace":{"id":"market","name":"Market","owner":{"name":"Owner"},"description":"Description"},"sources":[{"id":"local","kind":"local","path":"plugins"}],"plugins":[{"id":"pstack","source":"local","artifacts":{"agent-skills":{"path":"pstack/skills"}}}]}"#,
            )
            .unwrap();
            fs::write(
                root.join("profiles/pstack.json"),
                r#"{"schemaVersion":1,"id":"pstack","plugins":{"include":["pstack"],"exclude":[]},"targets":[{"id":"hermes"}]}"#,
            )
            .unwrap();
            Self {
                _temp: temp,
                root,
                destination: base.join("destination"),
                state_root: base.join("state"),
            }
        }

        fn cli(&self, action: Action) -> Cli {
            Cli {
                root: self.root.clone(),
                profile: "pstack".into(),
                target: SkillsTarget::Hermes,
                components: None,
                destination: Some(self.destination.clone()),
                project_root: None,
                state_dir: self.state_root.clone(),
                adopt: false,
                trust: Vec::new(),
                json: true,
                action,
            }
        }
    }

    #[test]
    fn apply_rejects_destination_root_swap() {
        let fixture = Fixture::new(&["a"]);
        fs::create_dir_all(&fixture.destination).unwrap();
        let plan = reconcile(&fixture.cli(Action::Install)).unwrap();
        let old = fixture.destination.with_extension("old");
        fs::rename(&fixture.destination, &old).unwrap();
        fs::create_dir(&fixture.destination).unwrap();
        let error = apply(&plan, false).unwrap_err();
        assert!(error.contains("changed since planning"), "{error}");
        assert!(!fixture.destination.join("a").exists());
    }

    #[test]
    fn post_create_root_swap_removes_unowned_link_from_detached_root() {
        let fixture = Fixture::new(&["a"]);
        fs::create_dir_all(&fixture.destination).unwrap();
        let snapshot = snapshot_root(&fixture.destination).unwrap();
        let root = DestinationRoot::open(&snapshot, true).unwrap();
        let source = fixture.root.join("plugins/pstack/skills/a");
        root.create("a", &source).unwrap();

        let detached = fixture.destination.with_extension("detached");
        fs::rename(&fixture.destination, &detached).unwrap();
        fs::create_dir(&fixture.destination).unwrap();

        let error = root.verify_created("a", &source).unwrap_err();
        assert!(error.contains("changed since planning"), "{error}");
        assert!(!detached.join("a").exists());
        assert!(!fixture.destination.join("a").exists());
    }

    #[test]
    fn apply_rejects_source_swap_to_symlink() {
        let fixture = Fixture::new(&["a"]);
        let plan = reconcile(&fixture.cli(Action::Install)).unwrap();
        let source = fixture.root.join("plugins/pstack/skills/a");
        let moved = source.with_extension("moved");
        fs::rename(&source, &moved).unwrap();
        symlink(&moved, &source).unwrap();
        let error = apply(&plan, false).unwrap_err();
        assert!(
            error.contains("repository path may not contain symlinks"),
            "{error}"
        );
        assert!(!fixture.destination.join("a").exists());
    }

    #[test]
    fn apply_rejects_stale_complete_state() {
        let fixture = Fixture::new(&["a"]);
        let plan = reconcile(&fixture.cli(Action::Install)).unwrap();
        state::write(&plan.state_file, &plan.next).unwrap();
        let error = apply(&plan, false).unwrap_err();
        assert!(
            error.contains("installation state changed since planning"),
            "{error}"
        );
        assert!(!fixture.destination.join("a").exists());
    }

    #[test]
    fn failed_quarantine_ownership_check_restores_entry() {
        let fixture = Fixture::new(&["a"]);
        let install = reconcile(&fixture.cli(Action::Install)).unwrap();
        apply(&install, false).unwrap();
        let uninstall = reconcile(&fixture.cli(Action::Uninstall)).unwrap();
        let path = fixture.destination.join("a");
        fs::remove_file(&path).unwrap();
        symlink("changed-after-plan", &path).unwrap();
        let error = apply(&uninstall, true).unwrap_err();
        assert!(
            error.contains("destination changed since planning"),
            "{error}"
        );
        assert_eq!(
            fs::read_link(&path).unwrap(),
            PathBuf::from("changed-after-plan")
        );
        assert!(uninstall.state_file.exists());
    }

    #[test]
    fn late_failure_preserves_progress_state() {
        let fixture = Fixture::new(&["a", "b"]);
        let plan = reconcile(&fixture.cli(Action::Install)).unwrap();
        fs::create_dir_all(&fixture.destination).unwrap();
        fs::write(fixture.destination.join("b"), "foreign").unwrap();
        let error = apply(&plan, false).unwrap_err();
        assert!(
            error.contains("destination changed since planning"),
            "{error}"
        );
        assert!(fixture.destination.join("a").is_symlink());
        let state = state::load(&plan.state_file).unwrap().unwrap();
        assert_eq!(
            state.entries.keys().cloned().collect::<Vec<_>>(),
            ["skill:a"]
        );
    }
}
