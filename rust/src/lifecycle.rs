use super::resource::PathResource;
use super::*;
use crate::state::{self, Entry, State};
use rustix::fd::OwnedFd;
use rustix::fs::{self as rfs, AtFlags, FlockOperation, Mode, OFlags};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Action {
    Plan,
    Status,
    Install,
    Uninstall,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Identity(u64, u64);

#[derive(Clone, Debug)]
struct RootSnapshot {
    path: PathBuf,
    identity: Option<Identity>,
    anchor: PathBuf,
    anchor_identity: Identity,
}

#[derive(Debug)]
pub(super) struct Plan {
    operations: Vec<Operation>,
    previous: Option<State>,
    next: State,
    state_file: PathBuf,
    repository: PathBuf,
    root: RootSnapshot,
}

pub(super) fn run(cli: &Cli) -> Result<(Vec<Operation>, bool)> {
    let plan = reconcile(cli)?;
    let conflicts = has_conflicts(&plan.operations);
    if matches!(cli.action, Action::Install | Action::Uninstall) && !conflicts {
        apply(&plan, cli.action == Action::Uninstall)?;
    }
    Ok((plan.operations, conflicts))
}

fn reconcile(cli: &Cli) -> Result<Plan> {
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
    if requested != [ComponentKind::Skills] {
        return Err("Rust parity unit supports only the skills component".into());
    }
    let destination = resolve_destination(
        cli.destination.as_deref(),
        target.destination.as_deref(),
        cli.target,
    )?;
    reject_destination_root_symlink(&destination)?;
    let state_file = canonicalize_missing(&cli.state_dir)?.join(format!(
        "agent-workbench/{}/{}/{target_id}.json",
        catalog.marketplace.id, profile.id
    ));
    let previous = state::load(&state_file)?;
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
        authorize_recorded_roots(previous, &authorized_roots(cli.target, &destination)?)?;
    }

    let desired = if cli.action == Action::Uninstall {
        BTreeMap::new()
    } else {
        desired_entries(&repository, &catalog, &profile, &destination)?
    };
    let owned = previous
        .as_ref()
        .map(|value| &value.entries)
        .cloned()
        .unwrap_or_default();
    let mut operations = Vec::new();
    let mut next_entries = BTreeMap::new();
    for (key, entry) in &desired {
        let source = entry.source_repository.join(&entry.source);
        let prior = owned.get(key);
        let same_owner = prior.is_some_and(|prior| prior.destination == entry.destination);
        let (kind, reason, expected_link) = match fs::symlink_metadata(&entry.destination) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                ("create", "destination is absent", None)
            }
            Err(error) => return Err(format!("{}: {error}", entry.destination.display())),
            Ok(_) if same_owner && !matches_owned(prior.unwrap())? => {
                ("conflict", "managed path was modified", None)
            }
            Ok(metadata)
                if metadata.file_type().is_symlink() && same_link(&entry.destination, &source)? =>
            {
                if same_owner {
                    ("keep", "managed resource already matches", None)
                } else if cli.adopt {
                    (
                        "adopt",
                        "matching unmanaged resource explicitly adopted",
                        None,
                    )
                } else {
                    (
                        "conflict",
                        "matching resource is not recorded as owned; use --adopt",
                        None,
                    )
                }
            }
            Ok(_) if same_owner && matches_owned(prior.unwrap())? => (
                "replace-owned",
                "managed resource has a new source",
                Some(
                    fs::read_link(&entry.destination)
                        .map_err(|error| format!("{}: {error}", entry.destination.display()))?,
                ),
            ),
            Ok(_) => ("conflict", "destination is a foreign file or link", None),
        };
        operations.push(operation(
            kind,
            key,
            &entry.destination,
            &source,
            reason,
            expected_link,
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
    let next = State {
        schema_version: 3,
        repository: repository.clone(),
        marketplace: catalog.marketplace.id.clone(),
        profile: profile.id.clone(),
        target: target_id.into(),
        destination_root: destination.clone(),
        entries: next_entries,
        destination_roots: BTreeMap::from([(ComponentKind::Skills, destination.clone())]),
    };
    let root = snapshot_root(&destination)?;
    Ok(Plan {
        operations,
        previous,
        next,
        state_file,
        repository,
        root,
    })
}

fn desired_entries(
    repository: &Path,
    catalog: &Catalog,
    profile: &Profile,
    destination: &Path,
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
        let artifact = plugin
            .artifacts
            .get("agent-skills")
            .ok_or_else(|| format!("plugin {} has no agent-skills artifact", plugin.id))?;
        let source_root = resolve_repo_path(repository, &source.path)?;
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
    Ok(entries)
}

fn operation(
    kind: &'static str,
    key: &str,
    destination: &Path,
    source: &Path,
    reason: &'static str,
    expected_link: Option<PathBuf>,
) -> Operation {
    Operation {
        kind,
        key: key.into(),
        destination: destination.to_string_lossy().into_owned(),
        source: source.to_string_lossy().into_owned(),
        reason,
        component: "skills",
        resource: PathResource::DirectorySymlink,
        diagnostic: None,
        trust_token: None,
        trust_required: false,
        expected_link,
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
    let home_base = home_dir()?.join(base_name);
    let capability_base = home_base.join("skills");
    let capability_parent = capability_base
        .parent()
        .ok_or("target home has no parent")?
        .to_path_buf();
    roots.insert(ComponentKind::Agents, capability_parent.join("agents"));
    if target == SkillsTarget::Claude {
        roots.insert(ComponentKind::Commands, capability_parent.join("commands"));
    }
    roots.insert(
        ComponentKind::Hooks,
        home_base.join(".agent-workbench-hooks"),
    );
    if target == SkillsTarget::Cursor {
        roots.insert(ComponentKind::Mcp, home_base);
    }
    Ok(roots)
}

fn removal(key: &str, entry: &Entry, reason: &'static str) -> Result<Option<Operation>> {
    match fs::symlink_metadata(&entry.destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("{}: {error}", entry.destination.display())),
        Ok(_) if matches_owned(entry)? => Ok(Some(operation(
            "remove-owned",
            key,
            &entry.destination,
            &entry.source_repository.join(&entry.source),
            reason,
            Some(fs::read_link(&entry.destination).map_err(|error| error.to_string())?),
        ))),
        Ok(_) => Ok(Some(operation(
            "conflict",
            key,
            &entry.destination,
            &entry.source_repository.join(&entry.source),
            "managed path was modified",
            None,
        ))),
    }
}

fn matches_owned(entry: &Entry) -> Result<bool> {
    let metadata = match fs::symlink_metadata(&entry.destination) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.to_string()),
    };
    Ok(metadata.file_type().is_symlink()
        && same_link(
            &entry.destination,
            &entry.source_repository.join(&entry.source),
        )?)
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
        let file = OpenOptions::new()
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

    fn restore(&self, name: &str, moved: Quarantine) -> Result<()> {
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

fn apply(plan: &Plan, uninstall: bool) -> Result<()> {
    let _lock = LockedState::acquire(&plan.state_file)?;
    if state::load(&plan.state_file)? != plan.previous {
        return Err("installation state changed since planning".into());
    }
    for operation in &plan.operations {
        if operation.kind != "remove-owned" {
            let source = Path::new(&operation.source);
            validate_source(&plan.repository, source)?;
        }
    }
    if plan.operations.is_empty() {
        return if uninstall {
            state::remove(&plan.state_file)
        } else {
            state::write(&plan.state_file, &plan.next)
        };
    }
    let root = DestinationRoot::open(&plan.root, !uninstall)?;
    let mut entries = plan
        .previous
        .as_ref()
        .map(|value| value.entries.clone())
        .unwrap_or_default();
    for operation in &plan.operations {
        root.verify()?;
        let destination = Path::new(&operation.destination);
        let name = destination
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or("destination name is not UTF-8")?;
        match operation.kind {
            "keep" | "adopt" => {
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
                entries.insert(
                    operation.key.clone(),
                    plan.next.entries[&operation.key].clone(),
                );
                write_progress(plan, &entries)?;
            }
            "create" => {
                validate_source(&plan.repository, Path::new(&operation.source))?;
                root.create_verified(name, Path::new(&operation.source))?;
                entries.insert(
                    operation.key.clone(),
                    plan.next.entries[&operation.key].clone(),
                );
                if let Err(error) = write_progress(plan, &entries) {
                    let expected = relative_link(Path::new(&operation.source), &root.path)?;
                    let moved = root.quarantine(name, &expected)?;
                    root.discard(moved)?;
                    return Err(error);
                }
            }
            "remove-owned" => {
                let expected = operation
                    .expected_link
                    .as_deref()
                    .ok_or("owned removal lacks source evidence")?;
                let moved = root.quarantine(name, expected)?;
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
                validate_source(&plan.repository, Path::new(&operation.source))?;
                let expected = operation
                    .expected_link
                    .as_deref()
                    .ok_or("owned replacement lacks source evidence")?;
                let moved = root.quarantine(name, expected)?;
                if let Err(error) = root
                    .create(name, Path::new(&operation.source))
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
    let progress = State {
        schema_version: 3,
        repository: plan.next.repository.clone(),
        marketplace: plan.next.marketplace.clone(),
        profile: plan.next.profile.clone(),
        target: plan.next.target.clone(),
        destination_root: plan.next.destination_root.clone(),
        entries: entries.clone(),
        destination_roots: plan.next.destination_roots.clone(),
    };
    state::write(&plan.state_file, &progress)
}

fn validate_source(repository: &Path, source: &Path) -> Result<()> {
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

fn relative_link(source: &Path, parent: &Path) -> Result<PathBuf> {
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
                state_dir: self.state_root.clone(),
                adopt: false,
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
