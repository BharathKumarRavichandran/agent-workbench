//! Ordered public validation decoding. Typed lifecycle decoders are kept separate
//! until their command-specific diagnostic contracts are migrated.
use crate::{state::wire, validation_json::Wire, *};

fn keys(data: &Wire, required: &[&str], optional: &[&str], location: &str) -> Result<()> {
    let fields = data.object(location)?;
    let missing: BTreeSet<_> = required
        .iter()
        .filter(|key| !data.has(key))
        .copied()
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "{location} is missing fields: {}",
            missing.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    let unknown: BTreeSet<_> = fields
        .iter()
        .map(|(key, _)| key)
        .filter(|key| {
            !key.scalar()
                .is_some_and(|s| required.contains(&s.as_str()) || optional.contains(&s.as_str()))
        })
        .collect();
    if !unknown.is_empty() {
        return Err(format!(
            "{location} has unknown fields: {}",
            unknown
                .into_iter()
                .map(|key| key.diagnostic())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    Ok(())
}
fn array<'a>(data: &'a Wire, location: &str) -> Result<&'a [Wire]> {
    if let Wire::Array(values) = data {
        Ok(values)
    } else {
        Err(format!("{location} must be an array"))
    }
}
fn string(data: &Wire, location: &str) -> Result<String> {
    let value = data
        .string()
        .ok_or_else(|| format!("{location} must be a non-empty string"))?;
    require_nonempty(&value, location)?;
    Ok(value)
}
fn identifier(data: &Wire, location: &str) -> Result<String> {
    if data
        .text()
        .is_some_and(|t| !t.0.is_empty() && t.scalar().is_none())
    {
        return Err(format!("{location} must match ^[a-z0-9][a-z0-9-]*$"));
    }
    let value = string(data, location)?;
    validate_id(&value, location)?;
    Ok(value)
}
fn repo(data: &Wire, location: &str) -> Result<String> {
    let value = string(data, location)?;
    if Path::new(&value).is_absolute()
        || Path::new(&value)
            .components()
            .any(|c| c == Component::ParentDir)
    {
        return Err(format!(
            "repository path must be relative and contained: {}",
            data.repr()
        ));
    }
    Ok(value)
}
fn upstream(data: &Wire, location: &str) -> Result<String> {
    let value = string(data, location)?;
    if value.starts_with('-') {
        return Err(format!("{location} is not a safe upstream URL"));
    }
    let scheme = upstream_scheme(&value)?;
    // re.fullmatch(r"[^@\s]+@[^:\s]+:.+", value): the host may contain
    // another @, and dot excludes LF only (not CR or Unicode separators).
    let whitespace = |c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c);
    let scp = value.split_once('@').is_some_and(|(user, rest)| {
        !user.is_empty()
            && !user.chars().any(whitespace)
            && rest.split_once(':').is_some_and(|(host, path)| {
                !host.is_empty()
                    && !host.chars().any(whitespace)
                    && !path.is_empty()
                    && !path.contains('\n')
            })
    });
    if !["https", "ssh", "git"].contains(&scheme.as_str()) && !scp {
        return Err(format!(
            "{location} must be an https, ssh, git, or scp-style URL"
        ));
    }
    Ok(value)
}
// Only urlparse's scheme and eager authority validation are observable here.
// Normalize a temporary parse view, never the retained catalog string. Match
// CPython 3.12 urlsplit/_check_bracketed_netloc/_checknetloc, not a stricter URL
// library (ports, missing hosts and arbitrary scheme paths remain permitted).
fn upstream_scheme(value: &str) -> Result<String> {
    let clean = value
        .trim_start_matches(|c: char| c <= '\u{20}')
        .replace(['\t', '\r', '\n'], "");
    let mut rest = clean.as_str();
    let mut scheme = String::new();
    if let Some((prefix, suffix)) = clean.split_once(':') {
        if prefix.starts_with(|c: char| c.is_ascii_alphabetic())
            && prefix
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
        {
            scheme = prefix.to_ascii_lowercase();
            rest = suffix;
        }
    }
    if let Some(authority) = rest.strip_prefix("//") {
        let netloc = authority.split(['/', '?', '#']).next().unwrap_or("");
        let open = netloc.contains('[');
        let close = netloc.contains(']');
        if open != close {
            return Err("Invalid IPv6 URL".into());
        }
        if open {
            let host_port = netloc.rsplit('@').next().unwrap_or("");
            let host = if let Some((before, bracketed)) = host_port.split_once('[') {
                let (host, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
                if !before.is_empty() || (!port.is_empty() && !port.starts_with(':')) {
                    return Err("Invalid IPv6 URL".into());
                }
                host
            } else {
                host_port.split(':').next().unwrap_or("")
            };
            if let Some(future) = host.strip_prefix('v') {
                if !future.split_once('.').is_some_and(|(version, address)| {
                    !version.is_empty()
                        && version.chars().all(|c| c.is_ascii_hexdigit())
                        && !address.is_empty()
                        && !address.contains('\n')
                }) {
                    return Err("IPvFuture address is invalid".into());
                }
            } else if host.parse::<std::net::Ipv4Addr>().is_ok() {
                return Err("An IPv4 address cannot be in brackets".into());
            } else {
                // Python IPv6Address permits one nonempty arbitrary scope ID.
                let (address, scope_valid) = host.split_once('%').map_or((host, true), |(a, s)| {
                    (a, !s.is_empty() && !s.contains('%'))
                });
                if !scope_valid || address.parse::<std::net::Ipv6Addr>().is_err() {
                    return Err(format!(
                        "{} does not appear to be an IPv4 or IPv6 address",
                        wire::repr(host)
                    ));
                }
            }
        }
        // Complete Python 3.12 Unicode NFKC delimiter-confusable set. urlsplit
        // removes literal @ : # ? before normalization; only introduced URL
        // delimiters cause failure. No Unicode normalization affects storage.
        if netloc.chars().any(|c| {
            matches!(
                c,
                '\u{2047}'
                    ..='\u{2049}'
                        | '\u{2100}'
                        | '\u{2101}'
                        | '\u{2105}'
                        | '\u{2106}'
                        | '\u{2a74}'
                        | '\u{fe13}'
                        | '\u{fe16}'
                        | '\u{fe55}'
                        | '\u{fe56}'
                        | '\u{fe5f}'
                        | '\u{fe6b}'
                        | '\u{ff03}'
                        | '\u{ff0f}'
                        | '\u{ff1a}'
                        | '\u{ff1f}'
                        | '\u{ff20}'
            )
        }) {
            return Err(format!(
                "netloc '{netloc}' contains invalid characters under NFKC normalization"
            ));
        }
    }
    Ok(scheme)
}

fn branch(data: &Wire, location: &str) -> Result<String> {
    let value = string(data, location)?;
    if validate_branch(&value).is_err() {
        return Err(format!("{location} is not a safe Git branch"));
    }
    Ok(value)
}

pub(crate) fn source_text(content: &[u8]) -> Result<&str> {
    std::str::from_utf8(content).map_err(|error| {
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
    })
}

pub(crate) fn read_value(path: &Path) -> Result<Wire> {
    let content = fs::read(path).map_err(|error| io_error(path, error))?;
    let text = source_text(&content)?;
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    crate::validation_json::parse(&text)
}

pub(crate) fn read(path: &Path) -> Result<Wire> {
    let data = read_value(path).map_err(|error| {
        if let Some(key) = error.strip_prefix("duplicate JSON key: ") {
            format!("duplicate JSON key {key}: {}", path.display())
        } else {
            error
        }
    })?;
    data.object(&path.display().to_string())?;
    Ok(data)
}
pub(crate) fn io_error(path: &Path, error: std::io::Error) -> String {
    let message = match error.kind() {
        std::io::ErrorKind::NotFound => "No such file or directory",
        std::io::ErrorKind::NotADirectory => "Not a directory",
        std::io::ErrorKind::IsADirectory => "Is a directory",
        std::io::ErrorKind::PermissionDenied => "Permission denied",
        _ => return format!("{}: {error}", path.display()),
    };
    format!(
        "[Errno {}] {message}: {}",
        error.raw_os_error().unwrap_or(0),
        wire::repr(&path.display().to_string())
    )
}

// The legacy inspection model does not consume marketplace descriptions. Keep
// their exact code points in the returned AST, and project only scalar text to
// that model. The E2 renderer always reads these fields from the AST, never the
// projection; in particular unpaired surrogates fail only at UTF-8 publication.
fn descriptive(data: &Wire, location: &str) -> Result<String> {
    let text = data
        .text()
        .filter(|t| !t.0.is_empty() && !t.0.contains(&0))
        .ok_or_else(|| format!("{location} must be a non-empty string"))?;
    if let Some(s) = text.scalar() {
        require_nonempty(&s, location)?;
        Ok(s)
    } else {
        Ok(String::new())
    }
}

pub(crate) fn catalog(path: &Path) -> Result<(Catalog, Wire)> {
    let data = read(path)?;
    keys(
        &data,
        &["schemaVersion", "marketplace", "sources", "plugins"],
        &["$schema"],
        "catalog",
    )?;
    if data.get("schemaVersion").value().as_u64() != Some(2) {
        return Err("catalog.schemaVersion must be integer 2".into());
    }
    let market = data.get("marketplace");
    keys(
        market,
        &["id", "name", "owner", "description"],
        &[],
        "catalog.marketplace",
    )?;
    let owner = market.get("owner");
    keys(owner, &["name"], &[], "catalog.marketplace.owner")?;
    let marketplace = Marketplace {
        id: identifier(market.get("id"), "catalog.marketplace.id")?,
        name: descriptive(market.get("name"), "catalog.marketplace.name")?,
        owner: Owner {
            name: descriptive(owner.get("name"), "catalog.marketplace.owner.name")?,
        },
        description: descriptive(market.get("description"), "catalog.marketplace.description")?,
    };
    let mut sources = Vec::new();
    for (index, source) in array(data.get("sources"), "catalog.sources")?
        .iter()
        .enumerate()
    {
        let loc = format!("catalog.sources[{index}]");
        keys(
            source,
            &["id", "kind", "path"],
            &["upstream", "branch"],
            &loc,
        )?;
        let kind = match source.get("kind").string().as_deref() {
            Some("local") => SourceKind::Local,
            Some("curated-copy") => SourceKind::CuratedCopy,
            Some("git-submodule") => SourceKind::GitSubmodule,
            _ => {
                return Err(format!(
                    "{loc}.kind is unsupported: {}",
                    source.get("kind").repr()
                ))
            }
        };
        sources.push(Source {
            id: identifier(source.get("id"), &format!("{loc}.id"))?,
            kind,
            path: repo(source.get("path"), &format!("{loc}.path"))?,
            upstream: if source.has("upstream") {
                Some(upstream(
                    source.get("upstream"),
                    &format!("{loc}.upstream"),
                )?)
            } else {
                None
            },
            branch: if source.has("branch") {
                Some(branch(source.get("branch"), &format!("{loc}.branch"))?)
            } else {
                None
            },
        });
    }
    let mut plugins = Vec::new();
    for (index, plugin) in array(data.get("plugins"), "catalog.plugins")?
        .iter()
        .enumerate()
    {
        let loc = format!("catalog.plugins[{index}]");
        keys(
            plugin,
            &["id", "source", "artifacts"],
            &["provenance"],
            &loc,
        )?;
        let fields = plugin
            .get("artifacts")
            .object(&format!("{loc}.artifacts"))?;
        if fields.is_empty() {
            return Err(format!(
                "{loc}.artifacts must contain at least one artifact"
            ));
        }
        let mut artifacts = BTreeMap::new();
        for (kind, artifact) in fields {
            let kind = kind.diagnostic();
            validate_id(&kind, &format!("{loc}.artifacts key"))?;
            let subloc = format!("{loc}.artifacts.{kind}");
            keys(artifact, &["path"], &[], &subloc)?;
            artifacts.insert(
                kind.clone(),
                Artifact {
                    path: repo(artifact.get("path"), &format!("{subloc}.path"))?,
                },
            );
        }
        let provenance = if plugin.has("provenance") {
            let provenance = plugin.get("provenance");
            let loc = format!("{loc}.provenance");
            keys(provenance, &["upstream"], &["notice"], &loc)?;
            Some(Provenance {
                upstream: upstream(provenance.get("upstream"), &format!("{loc}.upstream"))?,
                notice: if provenance.has("notice") {
                    Some(repo(provenance.get("notice"), &format!("{loc}.notice"))?)
                } else {
                    None
                },
            })
        } else {
            None
        };
        plugins.push(Plugin {
            id: identifier(plugin.get("id"), &format!("{loc}.id"))?,
            source: identifier(plugin.get("source"), &format!("{loc}.source"))?,
            artifacts,
            provenance,
        });
    }
    for source in &sources {
        if source.kind == SourceKind::CuratedCopy && source.upstream.is_none() {
            return Err(format!(
                "curated-copy source {} requires upstream provenance",
                source.id
            ));
        }
        if source.kind == SourceKind::GitSubmodule
            && (source.upstream.is_none() || source.branch.is_none())
        {
            return Err(format!(
                "git-submodule source {} requires upstream and branch",
                source.id
            ));
        }
    }
    for plugin in &plugins {
        if sources
            .iter()
            .rev()
            .find(|s| s.id == plugin.source)
            .is_some_and(|s| s.kind == SourceKind::CuratedCopy)
            && plugin
                .provenance
                .as_ref()
                .and_then(|p| p.notice.as_ref())
                .is_none()
        {
            return Err(format!(
                "curated-copy plugin {} requires provenance with a notice",
                plugin.id
            ));
        }
    }
    Ok((
        Catalog {
            schema: None,
            schema_version: 2,
            marketplace,
            sources,
            plugins,
        },
        data,
    ))
}

pub(crate) fn profile(path: &Path) -> Result<Profile> {
    let data = read(path)?;
    keys(
        &data,
        &["schemaVersion", "id", "plugins", "targets"],
        &["$schema"],
        "profile",
    )?;
    let version = data
        .get("schemaVersion")
        .value()
        .as_u64()
        .filter(|v| [1, 2].contains(v))
        .ok_or("profile.schemaVersion must be integer 1 or 2")?;
    let selector = data.get("plugins");
    keys(selector, &["include", "exclude"], &[], "profile.plugins")?;
    let include = array(selector.get("include"), "profile.plugins.include")?
        .iter()
        .map(|v| string(v, "profile.plugins.include[]"))
        .collect::<Result<Vec<_>>>()?;
    let exclude = array(selector.get("exclude"), "profile.plugins.exclude")?
        .iter()
        .map(|v| identifier(v, "profile.plugins.exclude[]"))
        .collect::<Result<Vec<_>>>()?;
    let mut targets = Vec::new();
    for (index, target) in array(data.get("targets"), "profile.targets")?
        .iter()
        .enumerate()
    {
        let loc = format!("profile.targets[{index}]");
        keys(
            target,
            &["id"],
            if version == 1 {
                &["destination"]
            } else {
                &["destination", "components", "projectRoot"]
            },
            &loc,
        )?;
        let components = if target.has("components") {
            let mut components = Vec::new();
            for value in array(target.get("components"), &format!("{loc}.components"))? {
                let text = string(value, &format!("{loc}.components[]"))?;
                let component = match text.as_str() {
                    "skills" => ComponentKind::Skills,
                    "agents" => ComponentKind::Agents,
                    "rules" => ComponentKind::Rules,
                    "commands" => ComponentKind::Commands,
                    "hooks" => ComponentKind::Hooks,
                    "mcp" => ComponentKind::Mcp,
                    _ => {
                        return Err(format!(
                            "{} is not a valid ComponentKind",
                            wire::repr(&text)
                        ))
                    }
                };
                components.push(component);
            }
            if components.iter().collect::<BTreeSet<_>>().len() != components.len() {
                return Err(format!("{loc}.components must be unique"));
            }
            Some(components)
        } else {
            None
        };
        let project_root = if target.has("projectRoot") {
            let value = string(target.get("projectRoot"), &format!("{loc}.projectRoot"))?;
            let expanded = expand_home(&value)
                .map_err(|_| format!("{loc}.projectRoot has an unknown user"))?;
            if !expanded.is_absolute() || has_parent_component(Path::new(&value)) {
                return Err(format!(
                    "{loc}.projectRoot must be an absolute contained path"
                ));
            }
            Some(value.to_owned())
        } else {
            None
        };
        targets.push(ProfileTarget {
            id: identifier(target.get("id"), &format!("{loc}.id"))?,
            destination: if target.has("destination") {
                Some(string(
                    target.get("destination"),
                    &format!("{loc}.destination"),
                )?)
            } else {
                None
            },
            components,
            project_root,
        });
    }
    if version == 2 && targets.iter().map(|t| &t.id).collect::<BTreeSet<_>>().len() != targets.len()
    {
        return Err("profile target ids must be unique".into());
    }
    Ok(Profile {
        schema: None,
        schema_version: version,
        id: identifier(data.get("id"), "profile.id")?,
        plugins: PluginSelector { include, exclude },
        targets,
    })
}
