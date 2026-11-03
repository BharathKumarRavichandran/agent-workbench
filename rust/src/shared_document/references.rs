//! Reference guard only: never compiles, translates, or executes hook commands.
use super::*;
use std::path::Path;
// POSIX shlex.split(comments=False): punctuation stays in words, quotes are
// removed, and only quote/backslash are escapable within double quotes.
fn shell_words(text: &str) -> Option<Vec<String>> {
    let mut chars = text.chars().peekable();
    let mut quote = None;
    let mut word = String::new();
    let mut words = vec![];
    while let Some(ch) = chars.next() {
        match (quote, ch) {
            (Some('\''), '\'') | (Some('"'), '"') => quote = None,
            (None, '\'' | '"') => quote = Some(ch),
            (None, '\\') => word.push(chars.next()?),
            (Some('"'), '\\') => {
                let next = chars.next()?;
                if !matches!(next, '"' | '\\') {
                    word.push('\\');
                }
                word.push(next);
            }
            (None, ' ' | '\t' | '\r' | '\n') => {
                if !word.is_empty() {
                    words.push(std::mem::take(&mut word));
                }
            }
            _ => word.push(ch),
        }
    }
    if quote.is_some() {
        return None;
    }
    if !word.is_empty() {
        words.push(word);
    }
    Some(words)
}
fn boundary(text: &str, path: &str) -> bool {
    text.match_indices(path).any(|(index, _)| {
        let before = text[..index].chars().next_back();
        let after = text[index + path.len()..].chars().next();
        before.is_none_or(|ch| ch != '/' && ch != '_' && !ch.is_alphanumeric())
            && after.is_none_or(|ch| ch == '/' || ch.is_whitespace() || "'\";|&<>".contains(ch))
    })
}
pub(crate) fn references_bundle(content: Option<&[u8]>, bundle: &Path) -> Result<bool> {
    let Some(content) = content else {
        return Ok(false);
    };
    let data = parse_bytes(content)?;
    let empty = Wire::Object(vec![]);
    let hooks = if data.has("hooks") {
        data.get("hooks")
    } else {
        &empty
    };
    let mut references = vec![bundle.to_string_lossy().into_owned()];
    if let Some(config_root) = bundle.parent().and_then(Path::parent) {
        for root in [Some(config_root), config_root.parent()]
            .into_iter()
            .flatten()
        {
            if let Ok(relative) = bundle.strip_prefix(root) {
                let path = relative.to_string_lossy().into_owned();
                references.push(format!("./{path}"));
                references.push(path);
            }
        }
    }
    for (_, entries) in hooks.object("hooks")? {
        let Wire::Array(entries) = entries else {
            return Err("hook events must contain arrays".into());
        };
        for entry in entries {
            if !matches!(entry, Wire::Object(_)) {
                return Err("hook entries must be objects".into());
            }
            let mut handlers = vec![entry];
            if let Wire::Array(nested) = entry.get("hooks") {
                handlers.extend(nested);
            }
            for handler in handlers {
                if let Some(command) = handler.get("command").string() {
                    let mut candidates = vec![command.to_owned()];
                    if let Some(words) = shell_words(command) {
                        candidates.extend(
                            words
                                .into_iter()
                                .map(|word| word.replace("\\$", "$").replace("\\`", "`")),
                        );
                    }
                    if references
                        .iter()
                        .any(|path| candidates.iter().any(|text| boundary(text, path)))
                    {
                        return Ok(true);
                    }
                }
            }
        }
    }
    Ok(false)
}
