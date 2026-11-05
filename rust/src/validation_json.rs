//! Validation-only Python 3.12 JSON boundary. Retain code points and nonfinite
//! scalars without widening the lifecycle/shared-document decoder contract.
use crate::{state::wire, Result};
use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub(crate) struct Text(pub Vec<u32>);
impl Text {
    pub fn from_str(s: &str) -> Self {
        Self(s.chars().map(u32::from).collect())
    }
    pub fn scalar(&self) -> Option<String> {
        self.0.iter().map(|&n| char::from_u32(n)).collect()
    }
    pub fn diagnostic(&self) -> String {
        self.0
            .iter()
            .map(|&n| char::from_u32(n).map_or_else(|| format!("\\u{n:04x}"), |c| c.to_string()))
            .collect()
    }
    pub fn repr(&self) -> String {
        let quote = if self.0.contains(&39) && !self.0.contains(&34) {
            '"'
        } else {
            '\''
        };
        let mut out = quote.to_string();
        for &n in &self.0 {
            match char::from_u32(n) {
                None => out.push_str(&format!("\\u{n:04x}")),
                Some(c) => {
                    let s = wire::repr(&c.to_string());
                    if c == quote || c == '\\' {
                        out.push('\\');
                        out.push(c);
                    } else if c == '\'' || c == '"' {
                        out.push(c);
                    } else {
                        out.push_str(&s[1..s.len() - 1]);
                    }
                }
            }
        }
        out.push(quote);
        out
    }
    fn quoted(&self) -> Vec<u32> {
        let mut out = vec![34];
        for &n in &self.0 {
            let escape = match n {
                34 => Some("\\\""),
                92 => Some("\\\\"),
                8 => Some("\\b"),
                9 => Some("\\t"),
                10 => Some("\\n"),
                12 => Some("\\f"),
                13 => Some("\\r"),
                _ => None,
            };
            if let Some(s) = escape {
                out.extend(s.chars().map(u32::from));
            } else if n < 32 {
                out.extend(format!("\\u{n:04x}").chars().map(u32::from));
            } else {
                out.push(n);
            }
        }
        out.push(34);
        out
    }
}
#[derive(Clone, Debug)]
pub(crate) enum Wire {
    Object(Vec<(Text, Wire)>),
    Array(Vec<Wire>),
    String(Text),
    Scalar(Value),
    Nonfinite(&'static str),
}
impl Wire {
    pub fn get(&self, key: &str) -> &Self {
        if let Self::Object(fields) = self {
            if let Some((_, v)) = fields.iter().find(|(k, _)| *k == Text::from_str(key)) {
                return v;
            }
        }
        &Self::Scalar(Value::Null)
    }
    pub fn has(&self, key: &str) -> bool {
        self.object("")
            .is_ok_and(|o| o.iter().any(|(k, _)| *k == Text::from_str(key)))
    }
    pub fn string(&self) -> Option<String> {
        if let Self::String(t) = self {
            t.scalar()
        } else {
            None
        }
    }
    pub fn text(&self) -> Option<&Text> {
        if let Self::String(t) = self {
            Some(t)
        } else {
            None
        }
    }
    pub fn object(&self, loc: &str) -> Result<&[(Text, Wire)]> {
        if let Self::Object(o) = self {
            Ok(o)
        } else {
            Err(format!("{loc} must be an object"))
        }
    }
    pub fn value(&self) -> Value {
        if let Self::Scalar(v) = self {
            v.clone()
        } else {
            Value::Null
        }
    }
    pub fn repr(&self) -> String {
        match self {
            Self::String(t) => t.repr(),
            Self::Nonfinite(n) => n.to_ascii_lowercase().replace("infinity", "inf"),
            Self::Scalar(v) => wire::Wire::Scalar(v.clone()).repr(),
            Self::Array(a) => format!(
                "[{}]",
                a.iter().map(Self::repr).collect::<Vec<_>>().join(", ")
            ),
            Self::Object(o) => format!(
                "{{{}}}",
                o.iter()
                    .map(|(k, v)| format!("{}: {}", k.repr(), v.repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
    /// Only structural artifact ordering crosses into the existing inspector.
    /// Accepted descriptive/ignored foreign strings remain in the lossless AST.
    pub fn inspection_wire(&self) -> wire::Wire {
        match self {
            Self::Object(o) => wire::Wire::Object(
                o.iter()
                    .map(|(k, v)| (k.diagnostic(), v.inspection_wire()))
                    .collect(),
            ),
            Self::Array(a) => wire::Wire::Array(a.iter().map(Self::inspection_wire).collect()),
            Self::String(t) => wire::Wire::Scalar(Value::String(t.diagnostic())),
            Self::Scalar(v) => wire::Wire::Scalar(v.clone()),
            Self::Nonfinite(_) => wire::Wire::Scalar(Value::Null),
        }
    }
    pub fn set(&mut self, key: &str, value: Self) {
        if let Self::Object(o) = self {
            if let Some((_, v)) = o.iter_mut().find(|(k, _)| *k == Text::from_str(key)) {
                *v = value;
            } else {
                o.push((Text::from_str(key), value));
            }
        }
    }
    fn render(&self, depth: usize) -> Result<Vec<u32>> {
        let literal = |s: &str| s.chars().map(u32::from).collect::<Vec<_>>();
        let (open, close, parts) = match self {
            Self::String(t) => return Ok(t.quoted()),
            Self::Nonfinite(n) => return Ok(literal(n)),
            Self::Scalar(v) => {
                return Ok(literal(
                    crate::shared_document::json::pretty(&wire::Wire::Scalar(v.clone()))?
                        .trim_end_matches('\n'),
                ))
            }
            Self::Array(a) => (
                91,
                93,
                a.iter()
                    .map(|v| v.render(depth + 1))
                    .collect::<Result<Vec<_>>>()?,
            ),
            Self::Object(o) => (
                123,
                125,
                o.iter()
                    .map(|(k, v)| {
                        let mut s = k.quoted();
                        s.extend([58, 32]);
                        s.extend(v.render(depth + 1)?);
                        Ok(s)
                    })
                    .collect::<Result<Vec<_>>>()?,
            ),
        };
        let mut out = vec![open];
        for (i, part) in parts.iter().enumerate() {
            if i > 0 {
                out.push(44);
            }
            out.push(10);
            out.extend(literal(&"  ".repeat(depth + 1)));
            out.extend(part);
        }
        if !parts.is_empty() {
            out.push(10);
            out.extend(literal(&"  ".repeat(depth)));
        }
        out.push(close);
        Ok(out)
    }
    pub fn pretty(&self) -> Result<String> {
        let mut points = self.render(0)?;
        points.push(10);
        if let Some(start) = points.iter().position(|n| (0xd800..=0xdfff).contains(n)) {
            let end = start
                + points[start..]
                    .iter()
                    .take_while(|n| (0xd800..=0xdfff).contains(*n))
                    .count();
            let position = if end == start + 1 {
                format!(
                    "character {} in position {start}",
                    Text(vec![points[start]]).repr()
                )
            } else {
                format!("characters in position {start}-{}", end - 1)
            };
            return Err(format!(
                "'utf-8' codec can't encode {position}: surrogates not allowed"
            ));
        }
        Ok(points
            .into_iter()
            .map(|n| char::from_u32(n).unwrap())
            .collect())
    }
}
struct Parser {
    chars: Vec<char>,
    pos: usize,
    depth: usize,
}
impl Parser {
    fn err(&self, msg: &str, pos: usize) -> String {
        let prefix = &self.chars[..pos.min(self.chars.len())];
        let line = prefix.iter().filter(|c| **c == '\n').count() + 1;
        let column = prefix.iter().rev().take_while(|c| **c != '\n').count() + 1;
        format!("{msg}: line {line} column {column} (char {pos})")
    }
    fn ws(&mut self) {
        while self
            .chars
            .get(self.pos)
            .is_some_and(|c| matches!(c, ' ' | '\t' | '\r' | '\n'))
        {
            self.pos += 1;
        }
    }
    fn take(&mut self, c: char) -> bool {
        self.ws();
        if self.chars.get(self.pos) == Some(&c) {
            self.pos += 1;
            true
        } else {
            false
        }
    }
    fn hex(&mut self) -> Result<u32> {
        let start = self.pos;
        let mut n = 0;
        for i in 0..4 {
            let c = self.chars.get(start + i).and_then(|c| c.to_digit(16));
            if let Some(d) = c {
                n = n * 16 + d;
            } else {
                return Err(self.err("Invalid \\uXXXX escape", start.saturating_sub(1)));
            }
        }
        self.pos += 4;
        Ok(n)
    }
    fn string(&mut self) -> Result<Text> {
        self.ws();
        let start = self.pos;
        if !self.take('"') {
            return Err(self.err(
                "Expecting property name enclosed in double quotes",
                self.pos,
            ));
        }
        let mut points = Vec::new();
        loop {
            let Some(&c) = self.chars.get(self.pos) else {
                return Err(self.err("Unterminated string starting at", start));
            };
            self.pos += 1;
            match c {
                '"' => break,
                '\\' => {
                    let slash = self.pos - 1;
                    let Some(&c) = self.chars.get(self.pos) else {
                        return Err(self.err("Unterminated string starting at", start));
                    };
                    self.pos += 1;
                    let n = match c {
                        '"' => 34,
                        '\\' => 92,
                        '/' => 47,
                        'b' => 8,
                        'f' => 12,
                        'n' => 10,
                        'r' => 13,
                        't' => 9,
                        'u' => {
                            let high = self.hex()?;
                            if (0xd800..=0xdbff).contains(&high)
                                && self.chars.get(self.pos..self.pos + 2) == Some(&['\\', 'u'])
                            {
                                let save = self.pos;
                                self.pos += 2;
                                match self.hex() {
                                    Ok(low) if (0xdc00..=0xdfff).contains(&low) => {
                                        0x10000 + ((high - 0xd800) << 10) + low - 0xdc00
                                    }
                                    _ => {
                                        self.pos = save;
                                        high
                                    }
                                }
                            } else {
                                high
                            }
                        }
                        _ => return Err(self.err("Invalid \\escape", slash)),
                    };
                    points.push(n);
                }
                c if (c as u32) < 32 => {
                    return Err(self.err("Invalid control character at", self.pos - 1))
                }
                c => points.push(c as u32),
            }
        }
        Ok(Text(points))
    }
    fn value(&mut self) -> Result<Wire> {
        self.ws();
        self.depth += 1;
        if self.depth > 512 {
            return Err("maximum recursion depth exceeded while decoding a JSON object from a unicode string".into());
        }
        let result = match self.chars.get(self.pos) {
            Some('"') => Wire::String(self.string()?),
            Some('{') => {
                self.pos += 1;
                let mut fields = Vec::new();
                let mut duplicate = None;
                if !self.take('}') {
                    loop {
                        let key = self.string()?;
                        if !self.take(':') {
                            return Err(self.err("Expecting ':' delimiter", self.pos));
                        }
                        let v = self.value()?;
                        if duplicate.is_none() && fields.iter().any(|(k, _)| k == &key) {
                            duplicate = Some(key.clone());
                        }
                        fields.push((key, v));
                        if self.take('}') {
                            break;
                        }
                        if !self.take(',') {
                            return Err(self.err("Expecting ',' delimiter", self.pos));
                        }
                    }
                }
                if let Some(k) = duplicate {
                    return Err(format!("duplicate JSON key: {}", k.repr()));
                }
                Wire::Object(fields)
            }
            Some('[') => {
                self.pos += 1;
                let mut values = Vec::new();
                if !self.take(']') {
                    loop {
                        values.push(self.value()?);
                        if self.take(']') {
                            break;
                        }
                        if !self.take(',') {
                            return Err(self.err("Expecting ',' delimiter", self.pos));
                        }
                    }
                }
                Wire::Array(values)
            }
            _ => self.scalar()?,
        };
        self.depth -= 1;
        Ok(result)
    }
    fn scalar(&mut self) -> Result<Wire> {
        for (token, value) in [
            ("null", Wire::Scalar(Value::Null)),
            ("true", Wire::Scalar(Value::Bool(true))),
            ("false", Wire::Scalar(Value::Bool(false))),
            ("NaN", Wire::Nonfinite("NaN")),
            ("Infinity", Wire::Nonfinite("Infinity")),
            ("-Infinity", Wire::Nonfinite("-Infinity")),
        ] {
            let chars = token.chars().collect::<Vec<_>>();
            if self.chars.get(self.pos..self.pos + chars.len()) == Some(chars.as_slice()) {
                self.pos += chars.len();
                return Ok(value);
            }
        }
        let start = self.pos;
        let mut end = start;
        if self.chars.get(end) == Some(&'-') {
            end += 1;
        }
        match self.chars.get(end) {
            Some('0') => end += 1,
            Some('1'..='9') => {
                end += 1;
                while self.chars.get(end).is_some_and(char::is_ascii_digit) {
                    end += 1;
                }
            }
            _ => return Err(self.err("Expecting value", start)),
        }
        if self.chars.get(end) == Some(&'.')
            && self.chars.get(end + 1).is_some_and(char::is_ascii_digit)
        {
            end += 2;
            while self.chars.get(end).is_some_and(char::is_ascii_digit) {
                end += 1;
            }
        }
        if self.chars.get(end).is_some_and(|c| matches!(c, 'e' | 'E')) {
            let mut exp = end + 1;
            if self.chars.get(exp).is_some_and(|c| matches!(c, '+' | '-')) {
                exp += 1;
            }
            if self.chars.get(exp).is_some_and(char::is_ascii_digit) {
                end = exp + 1;
                while self.chars.get(end).is_some_and(char::is_ascii_digit) {
                    end += 1;
                }
            }
        }
        self.pos = end;
        let text = self.chars[start..end].iter().collect::<String>();
        if text.contains(['.', 'e', 'E']) && text.parse::<f64>().is_ok_and(|n| !n.is_finite()) {
            return Ok(Wire::Nonfinite(if text.starts_with('-') {
                "-Infinity"
            } else {
                "Infinity"
            }));
        }
        let wire::Wire::Scalar(v) = wire::parse(&text)? else {
            unreachable!()
        };
        Ok(Wire::Scalar(v))
    }
}
pub(crate) fn parse(text: &str) -> Result<Wire> {
    if text.starts_with('\u{feff}') {
        return Err(
            "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)".into(),
        );
    }
    let mut p = Parser {
        chars: text.chars().collect(),
        pos: 0,
        depth: 0,
    };
    let value = p.value()?;
    p.ws();
    if p.pos != p.chars.len() {
        return Err(p.err("Extra data", p.pos));
    }
    Ok(value)
}
