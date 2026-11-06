//! Registered capability values, not a second global whitelist at projection.
use crate::{artifacts::Inventory, *};

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, Deserialize)]
pub enum Representation {
    #[serde(rename = "skill-directory")]
    SkillDirectory,
    #[serde(rename = "markdown-agent")]
    MarkdownAgent,
    #[serde(rename = "cursor-mdc")]
    CursorRule,
    #[serde(rename = "legacy-markdown-command")]
    LegacyCommand,
    #[serde(rename = "hook-bundle")]
    CursorHooks,
    #[serde(rename = "claude-hook-bundle")]
    ClaudeHooks,
    #[serde(rename = "mcp-document")]
    McpDocument,
}
impl Representation {
    fn name(self) -> &'static str {
        match self {
            Self::SkillDirectory => "skill-directory",
            Self::MarkdownAgent => "markdown-agent",
            Self::CursorRule => "cursor-mdc",
            Self::LegacyCommand => "legacy-markdown-command",
            Self::CursorHooks => "hook-bundle",
            Self::ClaudeHooks => "claude-hook-bundle",
            Self::McpDocument => "mcp-document",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Export {
    pub plugin: String,
    pub component: ComponentKind,
    pub name: String,
    pub source: PathBuf,
    pub representation: Representation,
}
#[derive(Debug)]
pub struct ProjectedPath {
    pub key: String,
    pub export: Export,
    pub destination: PathBuf,
}
#[derive(Debug)]
pub struct Conflict {
    pub component: ComponentKind,
    pub code: &'static str,
    pub reason: String,
}
#[derive(Debug)]
pub struct RegisteredTarget {
    pub id: String,
    pub capabilities: BTreeMap<ComponentKind, Representation>,
}
impl RegisteredTarget {
    /// Custom skill-directory targets default to portable skills only.
    pub fn skills_only(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            capabilities: BTreeMap::from([(ComponentKind::Skills, Representation::SkillDirectory)]),
        }
    }
    pub fn builtin(target: SkillsTarget) -> Self {
        let mut registered = Self::skills_only(target.id());
        let extra = match target {
            SkillsTarget::Cursor => vec![
                (ComponentKind::Agents, Representation::MarkdownAgent),
                (ComponentKind::Rules, Representation::CursorRule),
                (ComponentKind::Hooks, Representation::CursorHooks),
                (ComponentKind::Mcp, Representation::McpDocument),
            ],
            SkillsTarget::Claude => vec![
                (ComponentKind::Agents, Representation::MarkdownAgent),
                (ComponentKind::Commands, Representation::LegacyCommand),
                (ComponentKind::Hooks, Representation::ClaudeHooks),
                (ComponentKind::Mcp, Representation::McpDocument),
            ],
            _ => Vec::new(),
        };
        registered.capabilities.extend(extra);
        registered
    }
    pub fn project(
        &self,
        requested: &[ComponentKind],
        exports: &[Export],
        roots: &BTreeMap<ComponentKind, PathBuf>,
    ) -> (Vec<ProjectedPath>, Vec<Conflict>) {
        let mut conflicts = Vec::new();
        for component in requested
            .iter()
            .filter(|c| !self.capabilities.contains_key(c))
        {
            conflicts.push(Conflict {
                component: *component,
                code: "COMPONENT_UNSUPPORTED",
                reason: format!(
                    "{} does not support current {} format",
                    self.id,
                    component.as_str()
                ),
            });
        }
        for component in requested
            .iter()
            .filter(|c| self.capabilities.contains_key(c) && !roots.contains_key(c))
        {
            conflicts.push(Conflict {
                component: *component,
                code: "PROJECT_ROOT_REQUIRED",
                reason: format!("{} {} requires --project-root", self.id, component.as_str()),
            });
        }
        let mut paths = Vec::new();
        for export in exports {
            let component = export.component;
            if !requested.contains(&component) {
                continue;
            }
            let (Some(representation), Some(root)) =
                (self.capabilities.get(&component), roots.get(&component))
            else {
                continue;
            };
            if export.representation != *representation {
                conflicts.push(Conflict {
                    component,
                    code: "REPRESENTATION_UNSUPPORTED",
                    reason: format!(
                        "{} does not support {}",
                        self.id,
                        export.representation.name()
                    ),
                });
                continue;
            }
            let prefix = match component {
                ComponentKind::Skills => "skill",
                ComponentKind::Agents => "agent",
                ComponentKind::Rules => "rule",
                ComponentKind::Commands => "command",
                ComponentKind::Hooks | ComponentKind::Mcp => continue,
            };
            let name = if component == ComponentKind::Skills {
                export.name.clone()
            } else {
                export
                    .source
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            };
            paths.push(ProjectedPath {
                key: format!("{prefix}:{name}"),
                export: export.clone(),
                destination: root.join(name),
            });
        }
        (paths, conflicts)
    }
}

pub fn semantic_exports(plugin: &str, inventory: &Inventory) -> Vec<Export> {
    let (component, representation) = match inventory.kind.as_str() {
        "agent-skills" => (ComponentKind::Skills, Representation::SkillDirectory),
        "agents" => (ComponentKind::Agents, Representation::MarkdownAgent),
        "rules" => (ComponentKind::Rules, Representation::CursorRule),
        "commands" => (ComponentKind::Commands, Representation::LegacyCommand),
        "hooks" | "cursor-hooks" | "claude-hooks" => (
            ComponentKind::Hooks,
            if inventory.representation == Some("claude-hook-bundle") {
                Representation::ClaudeHooks
            } else {
                Representation::CursorHooks
            },
        ),
        "mcp" => (ComponentKind::Mcp, Representation::McpDocument),
        _ => return Vec::new(),
    };
    inventory
        .components
        .iter()
        .map(|(name, path)| Export {
            plugin: plugin.into(),
            component,
            name: name.clone(),
            representation,
            source: if component == ComponentKind::Mcp {
                inventory.root.clone()
            } else {
                inventory.root.join(path)
            },
        })
        .collect()
}

#[cfg(test)]
pub(crate) fn project_request(
    inspection: &crate::artifacts::CatalogInspection,
    request: &serde_json::Value,
) -> Result<serde_json::Value> {
    let id = request["target"].as_str().unwrap();
    let mut target = match SkillsTarget::parse(id) {
        Ok(builtin) => RegisteredTarget::builtin(builtin),
        Err(_) => RegisteredTarget::skills_only(id),
    };
    if !request["capabilities"].is_null() {
        target.capabilities =
            serde_json::from_value(request["capabilities"].clone()).map_err(|e| e.to_string())?;
    }
    let requested: Vec<ComponentKind> =
        serde_json::from_value(request["requested"].clone()).map_err(|e| e.to_string())?;
    let roots = serde_json::from_value(request["roots"].clone()).map_err(|e| e.to_string())?;
    let exports: Vec<_> = inspection
        .inventories
        .iter()
        .flat_map(|(plugin, inventory)| semantic_exports(plugin, inventory))
        .collect();
    let (paths, conflicts) = target.project(&requested, &exports, &roots);
    Ok(serde_json::json!({
        "paths": paths.iter().map(|p| serde_json::json!([p.key, p.export.plugin, p.export.component.as_str(), p.export.name, p.export.source, p.export.representation, p.destination])).collect::<Vec<_>>(),
        "conflicts": conflicts.iter().map(|c| serde_json::json!([c.component.as_str(), c.code, c.reason])).collect::<Vec<_>>()
    }))
}
