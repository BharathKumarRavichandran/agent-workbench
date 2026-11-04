//! Inert POSIX shell token inspection and native managed-reference compilation.
use super::*;

pub(super) fn split(text: &str) -> Result<Vec<String>> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut started = false;
    let mut quote = None;
    let mut chars = text.chars();
    while let Some(ch) = chars.next() {
        match (quote, ch) {
            (Some('\''), '\'') | (Some('"'), '"') => quote = None,
            (Some('\''), _) => token.push(ch),
            (_, '\\') => {
                let next = chars.next().ok_or("No escaped character")?;
                if quote == Some('"') && !matches!(next, '"' | '\\') {
                    token.push('\\');
                }
                token.push(next);
                started = true;
            }
            (None, '\'' | '"') => {
                quote = Some(ch);
                started = true;
            }
            (None, ' ' | '\t' | '\r' | '\n') => {
                if started {
                    tokens.push(std::mem::take(&mut token));
                    started = false;
                }
            }
            _ => {
                token.push(ch);
                started = true;
            }
        }
    }
    if quote.is_some() {
        return Err("No closing quotation".into());
    }
    if started {
        tokens.push(token);
    }
    Ok(tokens)
}

fn quote_context(text: &str) -> Option<char> {
    let mut quote = None;
    let mut chars = text.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' && quote != Some('\'') {
            chars.next();
            continue;
        }
        if matches!(ch, '\'' | '"') {
            if quote == Some(ch) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(ch);
            }
        }
    }
    quote
}
fn escaped(path: &str, context: Option<char>) -> String {
    match context {
        Some('"') => path
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('$', "\\$")
            .replace('`', "\\`"),
        Some('\'') => path.replace('\'', "'\\''"),
        _ if !path.is_empty()
            && path
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c)) =>
        {
            path.into()
        }
        _ => format!("'{}'", path.replace('\'', "'\"'\"'")),
    }
}
fn delimiter(ch: char) -> bool {
    ch.is_whitespace() || "'\";|&<>".contains(ch)
}
fn word(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

pub(super) fn compile(
    text: &str,
    files: &[String],
    destination: &Path,
    policy: Policy,
) -> Result<String> {
    let variable = policy.variable();
    if text.is_empty() {
        return Err("hook command must be a non-empty string".into());
    }
    if text.contains(if policy.cursor() {
        "${CLAUDE_PLUGIN_ROOT}"
    } else {
        "${CURSOR_PLUGIN_ROOT}"
    }) {
        return Err("target-incompatible plugin-root variable".into());
    }
    let mut declared: Vec<_> = files
        .iter()
        .filter(|p| p.as_str() != "hooks/hooks.json")
        .map(|p| p.strip_prefix("hooks/").unwrap_or(p))
        .collect();
    declared.sort_by_key(|name| std::cmp::Reverse(name.chars().count()));
    for token in split(text)? {
        if !policy.cursor()
            && !token.contains('/')
            && !declared.contains(&token.as_str())
            && [".sh", ".py", ".js", ".mjs", ".cjs", ".rb", ".pl", ".ps1"]
                .iter()
                .any(|suffix| token.ends_with(suffix))
        {
            return Err("hook unresolved local file reference escapes copied bundle".into());
        }
        if token.contains('/')
            && !declared.contains(&token.as_str())
            && !token.contains("://")
            && !["./", "../", "hooks/", variable, "/"]
                .iter()
                .any(|p| token.starts_with(p))
        {
            return Err("hook unresolved local file reference escapes copied bundle".into());
        }
        if token.starts_with('/')
            && ![
                "/bin/sh",
                "/bin/bash",
                "/bin/zsh",
                "/usr/bin/env",
                "/usr/bin/python3",
            ]
            .contains(&token.as_str())
        {
            return Err("hook absolute local reference escapes copied bundle".into());
        }
    }
    let mut output = String::new();
    let mut index = 0;
    while index < text.len() {
        let tail = &text[index..];
        let boundary = text[..index]
            .chars()
            .next_back()
            .is_none_or(|c| !word(c) && c != '/');
        let length = if let Some(suffix) = tail.strip_prefix(variable) {
            variable.len()
                + if suffix.starts_with('/') {
                    suffix
                        .chars()
                        .take_while(|c| !delimiter(*c))
                        .map(char::len_utf8)
                        .sum::<usize>()
                } else {
                    0
                }
        } else if boundary && ["./", "../", "hooks/"].iter().any(|p| tail.starts_with(p)) {
            tail.chars()
                .take_while(|c| !delimiter(*c))
                .map(char::len_utf8)
                .sum()
        } else if boundary {
            declared
                .iter()
                .find(|name| {
                    tail.starts_with(**name)
                        && tail[name.len()..].chars().next().is_none_or(delimiter)
                })
                .map_or(0, |name| name.len())
        } else {
            0
        };
        if length == 0 {
            let ch = tail.chars().next().unwrap();
            output.push(ch);
            index += ch.len_utf8();
            continue;
        }
        let value = &tail[..length];
        let relative = if value.starts_with(variable) {
            value.get(variable.len() + 1..).unwrap_or("").to_owned()
        } else if value.starts_with("./hooks/") || value.starts_with("hooks/") {
            value.strip_prefix("./").unwrap_or(value).into()
        } else {
            format!("hooks/{value}")
        };
        if relative.split('/').any(|p| p == "..") {
            return Err("hook local file reference escapes copied bundle".into());
        }
        let normalized = relative
            .split('/')
            .filter(|p| !p.is_empty() && *p != ".")
            .collect::<Vec<_>>()
            .join("/");
        if !files.contains(&normalized) {
            return Err("hook local file reference escapes or is absent from copied bundle".into());
        }
        output.push_str(&escaped(
            &destination.join(normalized).to_string_lossy(),
            quote_context(&text[..index]),
        ));
        index += length;
    }
    if output.contains(variable) {
        return Err("unresolved local plugin-root reference".into());
    }
    Ok(output)
}
