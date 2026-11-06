//! Native Cursor/Claude activation: content-bound grants and D5 transactions.
use crate::{
    bundles,
    lifecycle::{self, Action},
    resource::{ContentDigest, PathResource},
    shared_document::{
        self as docs,
        snapshot::{self, Error, Snapshot},
    },
    state::{self, wire::Wire, Entry, Slot},
    transaction::{apply as tx, recovery},
    trust, *,
};
#[path = "activation_commands.rs"]
mod commands;
use serde::Serialize;
use sha2::{Digest, Sha256};

/// Closed native activation formats. No cross-target hook translation.
#[derive(Clone, Copy)]
pub(super) enum Policy {
    Cursor,
    Claude,
}
impl Policy {
    pub(super) fn for_target(target: SkillsTarget) -> Option<Self> {
        match target {
            SkillsTarget::Cursor => Some(Self::Cursor),
            SkillsTarget::Claude => Some(Self::Claude),
            _ => None,
        }
    }
    fn id(self) -> &'static str {
        match self {
            Self::Cursor => "cursor",
            Self::Claude => "claude",
        }
    }
    fn cursor(self) -> bool {
        matches!(self, Self::Cursor)
    }
    fn hooks_file(self) -> &'static str {
        match self {
            Self::Cursor => "hooks.json",
            Self::Claude => "settings.json",
        }
    }
    fn mcp_file(self) -> &'static str {
        match self {
            Self::Cursor => "mcp.json",
            Self::Claude => ".mcp.json",
        }
    }
    fn variable(self) -> &'static str {
        match self {
            Self::Cursor => "${CURSOR_PLUGIN_ROOT}",
            Self::Claude => "${CLAUDE_PLUGIN_ROOT}",
        }
    }
}

fn error(e: Error) -> String {
    match e {
        Error::Invalid(s) => s,
        Error::Io(e) => e.to_string(),
    }
}
fn roots_wire(roots: &BTreeMap<ComponentKind, PathBuf>) -> BTreeMap<String, PathBuf> {
    roots
        .iter()
        .map(|(c, p)| (c.as_str().into(), p.clone()))
        .collect()
}
pub(super) fn recover(
    file: &Path,
    roots: &BTreeMap<ComponentKind, PathBuf>,
    policy: Policy,
) -> Result<()> {
    let request =
        recovery::RecoveryRequest::new(file.to_path_buf(), policy.id(), roots_wire(roots))
            .map_err(error)?;
    recovery::recover(&request, |_| Ok(())).map_err(error)
}
fn selected<'a>(
    catalog: &'a Catalog,
    profile: &'a Profile,
) -> impl Iterator<Item = (&'a Plugin, &'a Source)> + 'a {
    catalog
        .plugins
        .iter()
        .filter(move |p| {
            (profile.plugins.include == ["*"] || profile.plugins.include.contains(&p.id))
                && !profile.plugins.exclude.contains(&p.id)
        })
        .map(|p| {
            (
                p,
                catalog.sources.iter().find(|s| s.id == p.source).unwrap(),
            )
        })
}
fn artifact(
    repository: &Path,
    plugin: &Plugin,
    source: &Source,
    name: &str,
) -> Result<Option<PathBuf>> {
    plugin
        .artifacts
        .get(name)
        .map(|a| {
            resolve_repo_path(repository, &source.path).and_then(|r| resolve_repo_path(&r, &a.path))
        })
        .transpose()
}
pub(crate) fn classify_hooks(data: &Wire, alias: &str) -> Result<bool> {
    let Wire::Object(events) = data.get("hooks") else {
        return Err("hooks.json requires a hooks object".into());
    };
    let mut cursor = false;
    let mut claude = false;
    for (event, entries) in events {
        let Wire::Array(entries) = entries else {
            return Err("hook events must map to arrays".into());
        };
        if event.is_empty() {
            return Err("hook events must map to arrays".into());
        }
        for entry in entries {
            if !matches!(entry, Wire::Object(_)) {
                return Err("hook entries must be objects".into());
            }
            if entry.has("hooks") && !entry.has("command") {
                let Wire::Array(handlers) = entry.get("hooks") else {
                    return Err(
                        "Claude matcher groups require a hooks array and optional string matcher"
                            .into(),
                    );
                };
                if entry.has("matcher") && entry.get("matcher").string().is_none() {
                    return Err(
                        "Claude matcher groups require a hooks array and optional string matcher"
                            .into(),
                    );
                }
                for handler in handlers {
                    let kind = handler
                        .get("type")
                        .string()
                        .ok_or("Claude handlers require type")?;
                    if kind == "command"
                        && handler.get("command").string().is_none_or(str::is_empty)
                    {
                        return Err("Claude command handler requires command".into());
                    }
                }
                claude = true;
            } else if entry.has("command") && !entry.has("hooks") {
                if entry.get("command").string().is_none_or(str::is_empty) {
                    return Err("Cursor hook requires command".into());
                }
                cursor = true;
            } else {
                return Err("ambiguous hook entry representation".into());
            }
        }
    }
    if !cursor && !claude {
        if alias == "hooks" && !data.has("version") {
            return Err(
                "empty hooks require an explicit representation alias or Cursor version".into(),
            );
        }
        if alias == "claude-hooks" {
            claude = true;
        } else {
            cursor = true;
        }
    }
    if (cursor && claude)
        || (alias == "cursor-hooks" && !cursor)
        || (alias == "claude-hooks" && !claude)
    {
        return Err("mixed or incompatible hook representations".into());
    }
    if claude && data.has("version") {
        return Err("Claude hooks do not have a Cursor version field".into());
    }
    if cursor
        && data.has("version")
        && !matches!(data.get("version"), Wire::Scalar(serde_json::Value::Number(n)) if n.as_i64() == Some(1))
    {
        return Err("Cursor hooks require version 1".into());
    }
    Ok(cursor)
}
fn hook_source(
    repository: &Path,
    plugin: &Plugin,
    source: &Source,
    policy: Policy,
) -> Result<Option<PathBuf>> {
    let mut exact = Vec::new();
    for alias in ["hooks", "cursor-hooks", "claude-hooks"] {
        let Some(path) = artifact(repository, plugin, source, alias)? else {
            continue;
        };
        let inventory = crate::artifacts::inspect(repository, alias, &path)?;
        if (inventory.representation == Some("hook-bundle")) == policy.cursor() {
            exact.push(path);
        }
    }
    if exact.len() > 1 {
        return Err("multiple hook artifacts declare the same representation".into());
    }
    Ok(exact.pop())
}
pub(super) fn hook_entries(
    policy: Policy,
    repository: &Path,
    catalog: &Catalog,
    profile: &Profile,
    requested: &[ComponentKind],
    roots: &BTreeMap<ComponentKind, PathBuf>,
    entries: &mut BTreeMap<String, Entry>,
) -> Result<Vec<Operation>> {
    let mut conflicts = Vec::new();
    if !requested.contains(&ComponentKind::Hooks) {
        return Ok(conflicts);
    }
    for (plugin, source) in selected(catalog, profile) {
        if let Some(path) = hook_source(repository, plugin, source, policy)? {
            let tree = bundles::source_tree(&path).map_err(|e| e.to_string())?;
            let digest = tree.digest().map_err(|e| e.to_string())?;
            let key = format!("hook:{}", plugin.id);
            entries.insert(
                key.clone(),
                Entry {
                    key,
                    plugin: plugin.id.clone(),
                    source: path
                        .strip_prefix(repository)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .into_owned(),
                    destination: roots[&ComponentKind::Hooks].join(&plugin.id),
                    source_repository: repository.to_path_buf(),
                    component: ComponentKind::Hooks,
                    resource: PathResource::DirectoryCopy(ContentDigest::parse(&digest)?),
                    trust: None,
                    trust_proof: None,
                },
            );
        } else if ["hooks", "cursor-hooks", "claude-hooks"]
            .iter()
            .any(|name| plugin.artifacts.contains_key(*name))
        {
            let mut count = 0;
            for alias in ["hooks", "cursor-hooks", "claude-hooks"] {
                if let Some(path) = artifact(repository, plugin, source, alias)? {
                    let data = docs::parse_bytes(&lifecycle::read_regular(
                        repository,
                        &path.join("hooks.json"),
                    )?)?;
                    let total: usize = data
                        .get("hooks")
                        .object("hooks")?
                        .iter()
                        .map(|(_, entries)| match entries {
                            Wire::Array(a) => a.len(),
                            _ => 0,
                        })
                        .sum();
                    count += total.max(1);
                }
            }
            for _ in 0..count {
                conflicts.push(Operation {
                    kind: "conflict",
                    key: "component:hooks".into(),
                    destination: roots[&ComponentKind::Hooks].to_string_lossy().into_owned(),
                    source: String::new(),
                    reason: if policy.cursor() {
                        "cursor does not support claude-hook-bundle"
                    } else {
                        "claude does not support hook-bundle"
                    }
                    .into(),
                    component: "hooks",
                    resource: PathResource::DirectorySymlink,
                    diagnostic: Some("REPRESENTATION_UNSUPPORTED".into()),
                    trust_token: None,
                    trust_required: false,
                    expected_link: None,
                    expected_digest: None,
                });
            }
        }
    }
    Ok(conflicts)
}
pub(super) fn document(
    roots: &BTreeMap<ComponentKind, PathBuf>,
    component: ComponentKind,
    policy: Policy,
) -> PathBuf {
    match component {
        ComponentKind::Hooks => roots[&component]
            .parent()
            .unwrap()
            .join(policy.hooks_file()),
        ComponentKind::Mcp => roots[&component].join(policy.mcp_file()),
        _ => unreachable!(),
    }
}
fn compile(tree: &bundles::Tree, destination: &Path, policy: Policy) -> Result<Wire> {
    let (_, files) = tree.to_journal();
    let content = &files
        .iter()
        .find(|(p, _, _)| p == Path::new("hooks/hooks.json"))
        .ok_or("hook bundle lacks hooks/hooks.json")?
        .2;
    let data = docs::parse_bytes(content)?;
    let mut hooks = data.get("hooks").clone();
    if let Wire::Object(events) = &mut hooks {
        for (_, entries) in events {
            if let Wire::Array(entries) = entries {
                for entry in entries {
                    let handlers = if policy.cursor() {
                        std::slice::from_mut(entry)
                    } else {
                        let Some(Wire::Array(handlers)) = entry.get_mut("hooks") else {
                            return Err("incompatible hook representation".into());
                        };
                        handlers.as_mut_slice()
                    };
                    for entry in handlers {
                        if !policy.cursor() && entry.get("type").string() != Some("command") {
                            continue;
                        }
                        let command = entry
                            .get("command")
                            .string()
                            .ok_or("incompatible hook representation")?;
                        let paths: Vec<_> = files
                            .iter()
                            .map(|(path, _, _)| path.to_string_lossy().into_owned())
                            .collect();
                        let rewritten = commands::compile(command, &paths, destination, policy)?;
                        entry.set("command", Wire::Scalar(serde_json::json!(rewritten)));
                    }
                }
            }
        }
    }
    Ok(hooks)
}
fn plugin_variable(text: &str) -> bool {
    [
        "$CURSOR_PLUGIN_ROOT",
        "${CURSOR_PLUGIN_ROOT",
        "$CLAUDE_PLUGIN_ROOT",
        "${CLAUDE_PLUGIN_ROOT",
    ]
    .iter()
    .any(|v| text.contains(v))
}
fn contains_plugin_variable(value: &Wire) -> bool {
    match value {
        Wire::Scalar(serde_json::Value::String(s)) => plugin_variable(s),
        Wire::Array(items) => items.iter().any(contains_plugin_variable),
        Wire::Object(items) => items.iter().any(|(_, v)| contains_plugin_variable(v)),
        _ => false,
    }
}
fn local_mcp_payload(value: &str, root: &Path) -> bool {
    if plugin_variable(value) || value.starts_with("./") || value.starts_with("../") {
        return true;
    }
    let path = Path::new(value);
    if path.is_absolute() {
        canonicalize_missing(path).is_ok_and(|p| p.starts_with(root))
    } else {
        match std::fs::metadata(root.join(path)) {
            Ok(_) => true,
            Err(e) => !matches!(
                e.kind(),
                std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
            ),
        }
    }
}
pub(crate) fn validate_mcp_inventory(data: &Wire, artifact: &Path) -> Result<()> {
    let Wire::Object(servers) = data.get("mcpServers") else {
        return Err(format!(
            "MCP document requires an mcpServers object: {}",
            artifact.display()
        ));
    };
    let mut ordered: Vec<_> = servers.iter().collect();
    ordered.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, server) in ordered {
        if !matches!(server, Wire::Object(_)) {
            continue;
        }
        if !safe_filename(name) {
            return Err(format!(
                "component name must be one safe filename component: {}: {}",
                artifact.display(),
                state::wire::repr(name)
            ));
        }
    }
    if servers.iter().any(|(_, s)| !matches!(s, Wire::Object(_))) {
        return Err(format!(
            "MCP servers must map names to objects: {}",
            artifact.display()
        ));
    }
    Ok(())
}
pub(super) fn inspect_requested(
    policy: Policy,
    repository: &Path,
    catalog: &Catalog,
    profile: &Profile,
    requested: &[ComponentKind],
) -> Result<()> {
    for (plugin, source) in selected(catalog, profile) {
        for component in requested {
            match component {
                ComponentKind::Hooks => {
                    hook_source(repository, plugin, source, policy)?;
                }
                ComponentKind::Mcp => {
                    if let Some(path) = artifact(repository, plugin, source, "mcp")? {
                        crate::artifacts::inspect(repository, "mcp", &path)?;
                    }
                }
                _ => {}
            }
        }
    }
    Ok(())
}

fn validate_mcp(data: &Wire, artifact: &Path) -> Result<()> {
    let Wire::Object(servers) = data.get("mcpServers") else {
        return Err("MCP document requires an mcpServers object".into());
    };
    let root = canonicalize_missing(artifact.parent().unwrap())?;
    for (_, server) in servers {
        if !matches!(server, Wire::Object(_)) {
            return Err("MCP servers must be objects".into());
        }
        if contains_plugin_variable(server) {
            return Err(
                "plugin-root variables are unavailable in standalone MCP configuration".into(),
            );
        }
        if let Some(command) = server.get("command").string() {
            let tokens = commands::split(command)
                .map_err(|_| "MCP command cannot be parsed safely".to_owned())?;
            if tokens.iter().any(|t| local_mcp_payload(t, &root)) {
                return Err(
                    "local MCP payload cannot be preserved in standalone configuration".into(),
                );
            }
        }
        if let Wire::Array(args) = server.get("args") {
            if args
                .iter()
                .any(|v| v.string().is_some_and(|s| local_mcp_payload(s, &root)))
            {
                return Err(
                    "local MCP payload cannot be preserved in standalone configuration".into(),
                );
            }
        }
    }
    Ok(())
}
fn snapshot(path: &Path) -> Result<Snapshot> {
    snapshot::snapshot(path).map_err(error)
}
fn image(s: Snapshot) -> tx::DocumentSnapshot {
    tx::DocumentSnapshot {
        path: s.path,
        content: s.content,
        revision: s.revision,
    }
}
#[derive(Serialize)]
#[serde(untagged)]
enum PublicOperation<'a> {
    Path(&'a Operation),
    Slot(&'a docs::SlotOperation),
}

pub(super) fn run(cli: &Cli) -> Result<CommandOutput> {
    run_inner(cli, |_| Ok(()))
}

#[cfg(test)]
pub(crate) fn run_with(
    cli: &Cli,
    boundary: impl FnMut(tx::ApplyBoundary) -> std::io::Result<()>,
) -> Result<CommandOutput> {
    run_inner(cli, boundary)
}

fn run_inner(
    cli: &Cli,
    boundary: impl FnMut(tx::ApplyBoundary) -> std::io::Result<()>,
) -> Result<CommandOutput> {
    let policy = Policy::for_target(cli.target).ok_or("unsupported activation target")?;
    let mut plan = lifecycle::reconcile(cli)?;
    let (catalog, _) = validation_codec::catalog(&plan.repository.join("catalog/plugins.json"))?;
    let profile = validation_codec::profile(
        &plan
            .repository
            .join("profiles")
            .join(format!("{}.json", cli.profile)),
    )?;
    let file = plan.state_file.to_string_lossy().into_owned();
    let ledger_path = plan.authorized[&ComponentKind::Hooks]
        .parent()
        .unwrap()
        .join(".agent-workbench-owners.json");
    let ledger = snapshot(&ledger_path)?;
    snapshot::validate_ledger(ledger.content.as_deref())?;
    let previous_slots = plan
        .previous
        .as_ref()
        .map(|s| s.slots.clone())
        .unwrap_or_default();
    let old: Vec<_> = previous_slots.values().cloned().collect();
    let authority_conflicts = docs::ownership::conflicts(ledger.content.as_deref(), &file, &old)?;
    let mut documents = Vec::new();
    let mut operations = Vec::new();
    let mut sources = Vec::new();
    let mut slots = BTreeMap::new();
    let mut ordered_slots = Vec::new();
    for component in [ComponentKind::Hooks, ComponentKind::Mcp] {
        if !plan.authorized.contains_key(&component) {
            continue;
        }
        let owned: BTreeMap<_, _> = previous_slots
            .iter()
            .filter(|(_, s)| s.component == component.as_str())
            .map(|(k, s)| (k.clone(), s.clone()))
            .collect();
        let wanted = cli.action != Action::Uninstall && plan.requested.contains(&component);
        let has_bundle = plan
            .previous
            .as_ref()
            .is_some_and(|s| s.entries.values().any(|e| e.component == component));
        if authority_conflicts.is_empty() && !wanted && owned.is_empty() && !has_bundle {
            continue;
        }
        let path = document(&plan.authorized, component, policy);
        let before = snapshot(&path)?;
        if !authority_conflicts.is_empty() {
            operations.extend(
                authority_conflicts
                    .iter()
                    .filter(|o| o.component == component.as_str())
                    .map(|o| {
                        serde_json::from_value::<docs::SlotOperation>(
                            serde_json::to_value(o).unwrap(),
                        )
                        .unwrap()
                    }),
            );
            documents.push(tx::PlannedDocument {
                removed: before.content.clone(),
                after: before.content.clone(),
                before: image(before),
            });
            continue;
        }
        let mut desired = Vec::new();
        if wanted {
            for (plugin, source) in selected(&catalog, &profile) {
                let artifact = if component == ComponentKind::Hooks {
                    hook_source(&plan.repository, plugin, source, policy)?
                } else {
                    artifact(&plan.repository, plugin, source, "mcp")?
                };
                let Some(artifact) = artifact else {
                    continue;
                };
                let (digest, values) = if component == ComponentKind::Hooks {
                    let tree = bundles::source_tree(&artifact).map_err(|e| e.to_string())?;
                    let hooks =
                        compile(&tree, &plan.authorized[&component].join(&plugin.id), policy)?;
                    let mut values = Vec::new();
                    for (event, entries) in hooks.object("hooks")? {
                        let Wire::Array(entries) = entries else {
                            return Err("hook events must map to arrays".into());
                        };
                        for entry in entries {
                            values
                                .push((format!("{event}:{}", docs::digest(entry)?), entry.clone()));
                        }
                    }
                    (tree.digest().map_err(|e| e.to_string())?, values)
                } else {
                    let content = lifecycle::read_regular(&plan.repository, &artifact)?;
                    let data = docs::parse_bytes(&content)?;
                    validate_mcp(&data, &artifact)?;
                    let values = data.get("mcpServers").object("mcpServers")?.to_vec();
                    if values.iter().any(|(_, v)| !matches!(v, Wire::Object(_))) {
                        return Err("MCP servers must be objects".into());
                    }
                    (format!("{:x}", Sha256::digest(&content)), values)
                };
                let token = trust::Grant {
                    plugin,
                    source,
                    component,
                    target: policy.id(),
                    document: &path,
                    repository: &plan.repository,
                    artifact: Some(&artifact),
                    fallback: None,
                    digest: &digest,
                }
                .token()?;
                let bundle_key = format!("hook:{}", plugin.id);
                let bundle = plan
                    .previous
                    .as_ref()
                    .and_then(|s| s.entries.get(&bundle_key));
                let granted = trust::slots_granted(
                    &token,
                    &plugin.id,
                    component,
                    &cli.trust,
                    owned.values(),
                    bundle,
                    &plan.state_file,
                );
                if component == ComponentKind::Hooks {
                    let entry = plan.next.entries.get_mut(&bundle_key).unwrap();
                    entry.trust = Some(token.clone());
                    *entry = trust::reuse_proof(entry.clone(), bundle, &plan.state_file);
                    let op = plan
                        .operations
                        .iter_mut()
                        .find(|o| o.key == bundle_key)
                        .unwrap();
                    op.trust_token = Some(token.clone());
                    op.trust_required =
                        !trust::entry_granted(&token, &cli.trust, bundle, &plan.state_file);
                    if op.trust_required {
                        op.kind = "conflict";
                        op.reason = format!("content-bound grant required; pass --trust {token}");
                        op.diagnostic = Some("TRUST_REQUIRED".into());
                    }
                }
                for (name, value) in values {
                    let key = format!("{}:{}:{name}", component.as_str(), plugin.id);
                    let state = Slot {
                        trust_proof: None,
                        key: key.clone(),
                        plugin: plugin.id.clone(),
                        component: component.as_str().into(),
                        document: path.to_string_lossy().into_owned(),
                        slot: name,
                        digest: docs::digest(&value)?,
                        trust: token.clone(),
                    };
                    desired.push(docs::Desired {
                        state: trust::reuse_proof(state, owned.get(&key), &plan.state_file),
                        value,
                        granted,
                    });
                    sources.push(tx::Source {
                        path: artifact.clone(),
                        digest: digest.clone(),
                        tree: component == ComponentKind::Hooks,
                    });
                }
            }
        }
        let result = docs::plan_document(
            before.content.as_deref(),
            &path.to_string_lossy(),
            component.as_str(),
            &desired,
            &owned,
            cli.adopt,
            policy.id(),
        )?;
        operations.extend(result.operations);
        ordered_slots.extend(
            desired
                .iter()
                .filter_map(|item| result.slots.get(&item.state.key))
                .cloned(),
        );
        slots.extend(result.slots);
        documents.push(tx::PlannedDocument {
            before: image(before),
            removed: result.removed,
            after: result.after,
        });
    }
    if !documents.is_empty() {
        let ownership = docs::ownership::plan(
            ledger.content.as_deref(),
            &file,
            &old,
            &ordered_slots,
            operations,
        )?;
        operations = ownership.operations;
        documents.push(tx::PlannedDocument {
            before: image(ledger),
            removed: ownership.removed,
            after: ownership.after,
        });
        plan.next.schema_version = 4;
        plan.next.slots = slots;
        for component in [ComponentKind::Hooks, ComponentKind::Mcp] {
            if let Some(root) = plan.authorized.get(&component) {
                plan.next.destination_roots.insert(component, root.clone());
            }
        }
    }
    for operation in &mut plan.operations {
        if operation.kind == "remove-owned"
            && matches!(operation.resource, PathResource::DirectoryCopy(_))
        {
            if let Some(doc) = documents
                .iter()
                .find(|d| d.before.path == document(&plan.authorized, ComponentKind::Hooks, policy))
            {
                if docs::references::references_bundle(
                    doc.after.as_deref(),
                    Path::new(&operation.destination),
                )? {
                    operation.kind = "conflict";
                    operation.reason = "an active hook still references the managed bundle".into();
                    operation.diagnostic = Some("HOOK_BUNDLE_REFERENCED".into());
                }
            }
        }
    }
    let conflicts =
        has_conflicts(&plan.operations) || operations.iter().any(|o| o.kind == "conflict");
    let public: Vec<_> = plan
        .operations
        .iter()
        .map(PublicOperation::Path)
        .chain(operations.iter().map(PublicOperation::Slot))
        .collect();
    let stdout = if cli.json {
        serde_json::to_string_pretty(&public).map_err(|e| e.to_string())? + "\n"
    } else {
        let mut text = operations_output(&plan.operations, false)?;
        for operation in &operations {
            let reason = operation_reason(
                &operation.reason,
                operation.diagnostic.as_deref(),
                operation.trust_token.as_deref(),
                operation.trust_required,
            );
            text.push_str(&format!(
                "{:14} {}/shared-json-slot {} [{}]  ({reason})\n",
                operation.kind, operation.component, operation.destination, operation.slot
            ));
        }
        text
    };
    if matches!(cli.action, Action::Install | Action::Uninstall) && !conflicts {
        if documents.is_empty() {
            return lifecycle::run(cli);
        }
        let request = tx::ApplyRequest {
            target: policy.id().into(),
            repository: plan.repository.clone(),
            roots: roots_wire(&plan.authorized),
            state_before: image(snapshot(&plan.state_file)?),
            state_after: state::encode(&plan.next)?.into_bytes(),
            conflicts,
            uninstall: cli.action == Action::Uninstall,
            root_snapshots: plan
                .roots
                .values()
                .map(|r| tx::RootSnapshot {
                    path: r.path.clone(),
                    identity: r.identity.map(|i| [i.0, i.1]),
                    anchor: r.anchor.clone(),
                    anchor_identity: [r.anchor_identity.0, r.anchor_identity.1],
                })
                .collect(),
            documents,
            sources,
            operations: plan
                .operations
                .iter()
                .map(|o| tx::Operation {
                    kind: o.kind.into(),
                    path: PathBuf::from(&o.destination),
                    component: o.component.into(),
                    resource: match o.resource {
                        PathResource::DirectorySymlink => {
                            crate::transaction::path_recovery::Resource::Symlink
                        }
                        PathResource::FileCopy(_) => {
                            crate::transaction::path_recovery::Resource::File
                        }
                        PathResource::DirectoryCopy(_) => {
                            crate::transaction::path_recovery::Resource::Tree
                        }
                    },
                    source: Some(PathBuf::from(&o.source)),
                    digest: o.resource.digest().map(|d| d.as_str().into()),
                    expected_digest: o.expected_digest.clone(),
                    expected_link: o
                        .expected_link
                        .as_ref()
                        .map(|p| p.to_string_lossy().into_owned()),
                })
                .collect(),
        };
        if let Err(failure) = tx::apply(&request, boundary) {
            let state_lock = plan.state_file.with_file_name(format!(
                ".{}.lock",
                plan.state_file.file_name().unwrap().to_string_lossy()
            ));
            let message = match &failure {
                Error::Io(e)
                    if e.get_ref()
                        .and_then(|cause| {
                            cause.downcast_ref::<crate::transaction::LockContention>()
                        })
                        .is_some_and(|busy| busy.path == state_lock) =>
                {
                    format!(
                        "installation state is locked by another operation: {}",
                        plan.state_file.display()
                    )
                }
                _ => error(failure),
            };
            return Ok(CommandOutput::apply_error(stdout, message, cli.json));
        }
    }
    Ok(CommandOutput {
        stdout,
        stdout_single_write: cli.json,
        stderr: Vec::new(),
        exit_code: u8::from(conflicts),
    })
}
