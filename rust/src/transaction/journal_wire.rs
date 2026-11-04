//! Journal-only JSON: Python strings include unpaired surrogate code points.
//! Keep those code points (including object keys) through authentication and
//! snapshot identity. Conversion to Unicode scalar strings is explicit/fallible.
use crate::{shared_document::json, state::wire};
use serde_json::Value;
use std::ops::Index;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Text(Vec<u32>);
impl Text {
    pub(crate) fn ascii(s: &str) -> Self {
        Self(s.chars().map(u32::from).collect())
    }
    /// Python os.fsdecode on POSIX: invalid UTF-8 bytes use surrogateescape.
    pub(crate) fn from_path(path: &std::path::Path) -> Self {
        use std::os::unix::ffi::OsStrExt;
        let mut bytes = path.as_os_str().as_bytes();
        let mut points = Vec::new();
        while !bytes.is_empty() {
            match std::str::from_utf8(bytes) {
                Ok(s) => {
                    points.extend(s.chars().map(u32::from));
                    break;
                }
                Err(e) => {
                    let valid = e.valid_up_to();
                    points.extend(
                        std::str::from_utf8(&bytes[..valid])
                            .unwrap()
                            .chars()
                            .map(u32::from),
                    );
                    let count = e.error_len().unwrap_or(bytes.len() - valid);
                    points.extend(
                        bytes[valid..valid + count]
                            .iter()
                            .map(|b| 0xdc00 + u32::from(*b)),
                    );
                    bytes = &bytes[valid + count..];
                }
            }
        }
        Self(points)
    }
    pub(crate) fn contains_nul(&self) -> bool {
        self.0.contains(&0)
    }
    pub(crate) fn string(&self) -> Option<String> {
        self.0.iter().map(|&n| char::from_u32(n)).collect()
    }
    pub(crate) fn path(&self) -> Result<std::path::PathBuf, String> {
        use std::os::unix::ffi::OsStringExt;
        let mut bytes = Vec::new();
        for &n in &self.0 {
            if (0xdc80..=0xdcff).contains(&n) {
                bytes.push((n - 0xdc00) as u8);
            } else {
                let c = char::from_u32(n).ok_or("path is not filesystem-encodable")?;
                bytes.extend_from_slice(c.encode_utf8(&mut [0; 4]).as_bytes());
            }
        }
        Ok(std::ffi::OsString::from_vec(bytes).into())
    }
    fn quoted(&self) -> String {
        let mut s = String::from("\"");
        for &n in &self.0 {
            match n {
                34 => s.push_str("\\\""),
                92 => s.push_str("\\\\"),
                8 => s.push_str("\\b"),
                9 => s.push_str("\\t"),
                10 => s.push_str("\\n"),
                12 => s.push_str("\\f"),
                13 => s.push_str("\\r"),
                32..=126 => s.push(char::from_u32(n).unwrap()),
                0..=65535 => s.push_str(&format!("\\u{n:04x}")),
                _ => {
                    let n = n - 0x10000;
                    s.push_str(&format!(
                        "\\u{:04x}\\u{:04x}",
                        0xd800 + (n >> 10),
                        0xdc00 + (n & 1023)
                    ));
                }
            }
        }
        s.push('"');
        s
    }
}
#[derive(Clone, Debug)]
pub(crate) enum Wire {
    Object(Vec<(Text, Wire)>),
    Array(Vec<Wire>),
    String(Text),
    Scalar(Value),
}
impl Wire {
    fn from_shared(value: &wire::Wire) -> Self {
        match value {
            wire::Wire::Object(o) => Self::Object(
                o.iter()
                    .map(|(k, v)| (Text::ascii(k), Self::from_shared(v)))
                    .collect(),
            ),
            wire::Wire::Array(a) => Self::Array(a.iter().map(Self::from_shared).collect()),
            wire::Wire::Scalar(v) => Self::from_value(v),
        }
    }
    pub(crate) fn from_value(v: &Value) -> Self {
        match v {
            Value::Object(o) => Self::Object(
                o.iter()
                    .map(|(k, v)| (Text::ascii(k), Self::from_value(v)))
                    .collect(),
            ),
            Value::Array(a) => Self::Array(a.iter().map(Self::from_value).collect()),
            Value::String(s) => Self::String(Text::ascii(s)),
            _ => Self::Scalar(v.clone()),
        }
    }
    pub(crate) fn get(&self, key: &str) -> &Self {
        if let Self::Object(o) = self {
            if let Some((_, v)) = o.iter().find(|(k, _)| *k == Text::ascii(key)) {
                return v;
            }
        }
        &Self::Scalar(Value::Null)
    }
    pub(crate) fn has(&self, key: &str) -> bool {
        self.as_object()
            .is_some_and(|o| o.iter().any(|(k, _)| *k == Text::ascii(key)))
    }
    pub(crate) fn remove(&mut self, key: &str) {
        if let Self::Object(o) = self {
            o.retain(|(k, _)| *k != Text::ascii(key));
        }
    }
    pub(crate) fn string(&self) -> Option<String> {
        if let Self::String(t) = self {
            t.string()
        } else {
            None
        }
    }
    pub(crate) fn path(&self) -> Result<std::path::PathBuf, String> {
        if let Self::String(t) = self {
            t.path()
        } else {
            Err("invalid path string".into())
        }
    }
    pub(crate) fn as_array(&self) -> Option<&Vec<Self>> {
        if let Self::Array(a) = self {
            Some(a)
        } else {
            None
        }
    }
    pub(crate) fn as_object(&self) -> Option<&Vec<(Text, Self)>> {
        if let Self::Object(o) = self {
            Some(o)
        } else {
            None
        }
    }
    pub(crate) fn is_null(&self) -> bool {
        matches!(self, Self::Scalar(Value::Null))
    }
    pub(crate) fn value(&self) -> Result<Value, String> {
        Ok(match self {
            Self::String(t) => Value::String(t.string().ok_or("JSON string contains a surrogate")?),
            Self::Scalar(v) => v.clone(),
            Self::Array(a) => Value::Array(a.iter().map(Self::value).collect::<Result<_, _>>()?),
            Self::Object(o) => Value::Object(
                o.iter()
                    .map(|(k, v)| {
                        Ok((
                            k.string().ok_or("JSON key contains a surrogate")?,
                            v.value()?,
                        ))
                    })
                    .collect::<Result<_, String>>()?,
            ),
        })
    }
    pub(crate) fn canonical(&self) -> Result<String, String> {
        Ok(match self {
            Self::String(t) => t.quoted(),
            Self::Scalar(v) => json::canonical(&wire::Wire::Scalar(v.clone()))?,
            Self::Array(a) => format!(
                "[{}]",
                a.iter()
                    .map(Self::canonical)
                    .collect::<Result<Vec<_>, _>>()?
                    .join(",")
            ),
            Self::Object(o) => {
                let mut fields = o.iter().collect::<Vec<_>>();
                fields.sort_by(|a, b| a.0.cmp(&b.0));
                format!(
                    "{{{}}}",
                    fields
                        .into_iter()
                        .map(|(k, v)| Ok(format!("{}:{}", k.quoted(), v.canonical()?)))
                        .collect::<Result<Vec<_>, String>>()?
                        .join(",")
                )
            }
        })
    }
    /// Project known schema fields only AFTER authenticating the full AST.
    pub(crate) fn project(&self, fields: &[&str]) -> Result<Value, String> {
        let mut o = serde_json::Map::new();
        for &k in fields {
            if self.has(k) {
                o.insert(k.into(), self.get(k).value()?);
            }
        }
        Ok(Value::Object(o))
    }
}
impl Index<usize> for Wire {
    type Output = Wire;
    fn index(&self, i: usize) -> &Wire {
        &self.as_array().unwrap()[i]
    }
}
impl PartialEq for Wire {
    fn eq(&self, other: &Self) -> bool {
        // Preserve the existing serde value equality for scalar-safe images.
        match (self.value(), other.value()) {
            (Ok(a), Ok(b)) => a == b,
            _ => self.canonical().ok() == other.canonical().ok(),
        }
    }
}

pub(crate) fn validate_tree_name(value: &Wire) -> Result<(), String> {
    let Wire::String(Text(points)) = value else {
        return Err("invalid tree path".into());
    };
    if points.first() == Some(&47)
        || points.iter().any(|n| matches!(n, 0 | 92))
        || points.split(|n| *n == 47).any(|p| p == [46, 46])
    {
        return Err("unsafe journal bundle record".into());
    }
    Ok(())
}

struct Parser<'a> {
    text: &'a [u32],
    pos: usize,
    depth: usize,
}
impl Parser<'_> {
    fn ws(&mut self) {
        while self
            .text
            .get(self.pos)
            .is_some_and(|n| matches!(n, 32 | 10 | 13 | 9))
        {
            self.pos += 1;
        }
    }
    fn take(&mut self, b: u8) -> bool {
        self.ws();
        if self.text.get(self.pos) == Some(&u32::from(b)) {
            self.pos += 1;
            true
        } else {
            false
        }
    }
    fn hex(&mut self) -> Result<u32, String> {
        let digits = self
            .text
            .get(self.pos..self.pos + 4)
            .ok_or("invalid JSON escape")?;
        let mut n = 0;
        for &digit in digits {
            n = n * 16
                + char::from_u32(digit)
                    .and_then(|c| c.to_digit(16))
                    .ok_or("invalid JSON escape")?;
        }
        self.pos += 4;
        Ok(n)
    }
    fn string(&mut self) -> Result<Text, String> {
        if !self.take(b'"') {
            return Err("expected JSON string".into());
        }
        let mut points = Vec::new();
        loop {
            let c = *self.text.get(self.pos).ok_or("unterminated JSON string")?;
            self.pos += 1;
            match c {
                34 => break,
                92 => {
                    let c = *self.text.get(self.pos).ok_or("invalid JSON escape")?;
                    self.pos += 1;
                    let n = match c {
                        34 => 34,
                        92 => 92,
                        47 => 47,
                        98 => 8,
                        102 => 12,
                        110 => 10,
                        114 => 13,
                        116 => 9,
                        117 => {
                            let high = self.hex()?;
                            // Only adjacent JSON escapes combine, never raw
                            // surrogate codepoints adjoining an escape.
                            if (0xd800..=0xdbff).contains(&high)
                                && self.text.get(self.pos..self.pos + 2) == Some(&[92, 117])
                            {
                                let saved = self.pos;
                                self.pos += 2;
                                let low = self.hex()?;
                                if (0xdc00..=0xdfff).contains(&low) {
                                    0x10000 + ((high - 0xd800) << 10) + low - 0xdc00
                                } else {
                                    self.pos = saved;
                                    high
                                }
                            } else {
                                high
                            }
                        }
                        _ => return Err("invalid JSON escape".into()),
                    };
                    points.push(n);
                }
                0..=31 => return Err("invalid JSON control character".into()),
                n => points.push(n),
            }
        }
        Ok(Text(points))
    }
    fn value(&mut self) -> Result<Wire, String> {
        self.ws();
        self.depth += 1;
        if self.depth > 128 {
            return Err("JSON recursion limit exceeded".into());
        }
        let result = match self.text.get(self.pos) {
            Some(34) => Wire::String(self.string()?),
            Some(123) => {
                self.pos += 1;
                let mut o = Vec::new();
                let mut duplicate = None;
                if !self.take(b'}') {
                    loop {
                        let key = self.string()?;
                        if !self.take(b':') {
                            return Err("expected JSON colon".into());
                        }
                        let value = self.value()?;
                        if duplicate.is_none() && o.iter().any(|(k, _)| k == &key) {
                            duplicate = Some(key.clone());
                        }
                        o.push((key, value));
                        if self.take(b'}') {
                            break;
                        }
                        if !self.take(b',') {
                            return Err("expected JSON comma".into());
                        }
                    }
                }
                if let Some(k) = duplicate {
                    return Err(format!("duplicate JSON key: {}", k.quoted()));
                }
                Wire::Object(o)
            }
            Some(91) => {
                self.pos += 1;
                let mut a = Vec::new();
                if !self.take(b']') {
                    loop {
                        a.push(self.value()?);
                        if self.take(b']') {
                            break;
                        }
                        if !self.take(b',') {
                            return Err("expected JSON comma".into());
                        }
                    }
                }
                Wire::Array(a)
            }
            _ => {
                let start = self.pos;
                while self
                    .text
                    .get(self.pos)
                    .is_some_and(|n| !matches!(n, 44 | 93 | 125 | 32 | 10 | 13 | 9))
                {
                    self.pos += 1;
                }
                let scalar = self.text[start..self.pos]
                    .iter()
                    .map(|&n| char::from_u32(n))
                    .collect::<Option<String>>()
                    .ok_or("invalid JSON scalar")?;
                let wire::Wire::Scalar(v) = wire::parse(&scalar)? else {
                    return Err("invalid JSON scalar".into());
                };
                Wire::Scalar(v)
            }
        };
        self.depth -= 1;
        Ok(result)
    }
}
/// Journal bytes are not shared-document JSON: read with the same pinned,
/// nofollow regular-file checks, but let the journal decoder validate the wire.
pub(crate) fn snapshot_at(
    path: &std::path::Path,
    parent: &impl rustix::fd::AsFd,
) -> Result<crate::shared_document::snapshot::Snapshot, crate::shared_document::snapshot::Error> {
    use crate::shared_document::snapshot::{self, Error};
    use rustix::fs::{openat, Mode, OFlags};
    use std::{fs::File, io::Read};
    let fd = match openat(
        parent,
        path.file_name()
            .ok_or_else(|| Error::Invalid("journal has no name".into()))?,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    ) {
        Ok(fd) => fd,
        Err(rustix::io::Errno::NOENT) => {
            return Ok(snapshot::Snapshot {
                path: path.into(),
                content: None,
                revision: None,
            })
        }
        Err(e) => return Err(std::io::Error::from(e).into()),
    };
    let mut file = File::from(fd);
    let info = file.metadata()?;
    if info.is_dir() {
        return Err(
            std::io::Error::from_raw_os_error(rustix::io::Errno::ISDIR.raw_os_error()).into(),
        );
    }
    if !info.is_file() {
        return Err(Error::Invalid(format!(
            "shared document must be a regular file: {}",
            path.display()
        )));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    let result = snapshot::from_metadata(path.into(), bytes, &info);
    if result.revision
        != snapshot::from_metadata(path.into(), Vec::new(), &file.metadata()?).revision
    {
        return Err(Error::Invalid("journal changed while reading".into()));
    }
    Ok(result)
}
pub(crate) fn snapshot(
    path: &std::path::Path,
) -> Result<crate::shared_document::snapshot::Snapshot, crate::shared_document::snapshot::Error> {
    use crate::shared_document::snapshot::{Error, Snapshot};
    match crate::bundles::open_directory(
        path.parent()
            .ok_or_else(|| Error::Invalid("journal has no parent".into()))?,
    ) {
        Ok(parent) => snapshot_at(path, &parent),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Snapshot {
            path: path.into(),
            content: None,
            revision: None,
        }),
        Err(e) => Err(e.into()),
    }
}

fn decode_source(saved: &[u8]) -> Result<Vec<u32>, String> {
    let (bytes, width, little) = if let Some(b) = saved.strip_prefix(b"\xff\xfe\0\0") {
        (b, 4, true)
    } else if let Some(b) = saved.strip_prefix(b"\0\0\xfe\xff") {
        (b, 4, false)
    } else if let Some(b) = saved.strip_prefix(b"\xff\xfe") {
        (b, 2, true)
    } else if let Some(b) = saved.strip_prefix(b"\xfe\xff") {
        (b, 2, false)
    } else if let Some(b) = saved.strip_prefix(b"\xef\xbb\xbf") {
        (b, 1, false)
    } else if saved.len() >= 4 && saved[0] == 0 {
        (saved, if saved[1] == 0 { 4 } else { 2 }, false)
    } else if saved.len() >= 4 && saved[1] == 0 {
        (
            saved,
            if saved[2] == 0 && saved[3] == 0 { 4 } else { 2 },
            true,
        )
    } else {
        (saved, 1, false)
    };
    if width == 1 {
        // UTF-8 surrogatepass: retain surrogates, but reject malformed UTF-8
        // (overlong, truncated, bad continuation, and values above U+10FFFF).
        let mut points = Vec::new();
        let mut pos = 0;
        while pos < bytes.len() {
            let first = bytes[pos];
            let (width, mut n, minimum) = match first {
                0..=0x7f => (1, u32::from(first), 0),
                0xc2..=0xdf => (2, u32::from(first & 31), 0x80),
                0xe0..=0xef => (3, u32::from(first & 15), 0x800),
                0xf0..=0xf4 => (4, u32::from(first & 7), 0x10000),
                _ => return Err("invalid JSON byte encoding".into()),
            };
            let tail = bytes
                .get(pos + 1..pos + width)
                .ok_or("invalid JSON byte encoding")?;
            for &b in tail {
                if b & 0xc0 != 0x80 {
                    return Err("invalid JSON byte encoding".into());
                }
                n = (n << 6) | u32::from(b & 63);
            }
            if n < minimum || n > 0x10ffff {
                return Err("invalid JSON byte encoding".into());
            }
            points.push(n);
            pos += width;
        }
        return Ok(points);
    }
    if bytes.len() % width != 0 {
        return Err("invalid JSON byte encoding".into());
    }
    let points = if width == 2 {
        let words = bytes.as_chunks::<2>().0.iter().map(|b| {
            if little {
                u16::from_le_bytes(*b)
            } else {
                u16::from_be_bytes(*b)
            }
        });
        char::decode_utf16(words)
            .map(|c| {
                c.map(u32::from)
                    .unwrap_or_else(|e| u32::from(e.unpaired_surrogate()))
            })
            .collect::<Vec<_>>()
    } else {
        bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| {
                if little {
                    u32::from_le_bytes(*b)
                } else {
                    u32::from_be_bytes(*b)
                }
            })
            .collect()
    };
    if points.iter().any(|&n| n > 0x10ffff) {
        return Err("invalid JSON byte encoding".into());
    }
    Ok(points)
}

pub(crate) fn parse_bytes(saved: &[u8]) -> Result<Wire, String> {
    // Keep the established byte-encoding boundary for ordinary journals.
    let shared_error = match crate::shared_document::parse_bytes(saved) {
        Ok(v) => return Ok(Wire::from_shared(&v)),
        Err(error) => error,
    };
    let text = decode_source(saved)?;
    let surrogate_source = text.iter().any(|n| (0xd800..=0xdfff).contains(n))
        || text.windows(6).any(|w| {
            w[0] == 92
                && w[1] == 117
                && w[2..]
                    .iter()
                    .try_fold(0u32, |n, &c| {
                        char::from_u32(c)
                            .and_then(|c| c.to_digit(16))
                            .map(|c| n * 16 + c)
                    })
                    .is_some_and(|n| (0xd800..=0xdfff).contains(&n))
        });
    if !surrogate_source {
        return Err(shared_error);
    }
    let mut p = Parser {
        text: &text,
        pos: 0,
        depth: 0,
    };
    let value = p.value()?;
    p.ws();
    if p.pos != text.len() {
        return Err("trailing JSON data".into());
    }
    if !matches!(value, Wire::Object(_)) {
        return Err("shared JSON document must be an object".into());
    }
    Ok(value)
}
