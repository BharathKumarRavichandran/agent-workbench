//! Ordered JSON boundary: Python validates mappings in source insertion order.
use super::*;
use serde::de::{MapAccess, SeqAccess, Visitor};
use std::fmt;

#[derive(Clone, Debug)]
pub(crate) enum Wire {
    Object(Vec<(String, Wire)>),
    Array(Vec<Wire>),
    Scalar(Value),
}

impl Serialize for Wire {
    fn serialize<S: serde::Serializer>(
        &self,
        serializer: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        match self {
            Self::Scalar(value) => value.serialize(serializer),
            Self::Array(values) => values.serialize(serializer),
            Self::Object(fields) => {
                let mut map = serializer.serialize_map(Some(fields.len()))?;
                for (key, value) in fields {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
        }
    }
}

impl Wire {
    pub(crate) fn value(&self) -> Value {
        serde_json::to_value(self).expect("JSON wire")
    }
    pub(crate) fn set(&mut self, key: &str, value: Wire) {
        if let Self::Object(fields) = self {
            if let Some((_, prior)) = fields.iter_mut().find(|(name, _)| name == key) {
                *prior = value;
            } else {
                fields.push((key.to_owned(), value));
            }
        }
    }
    pub(crate) fn get_mut(&mut self, key: &str) -> Option<&mut Wire> {
        if let Self::Object(fields) = self {
            fields
                .iter_mut()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value)
        } else {
            None
        }
    }
    pub(crate) fn remove(&mut self, key: &str) {
        if let Self::Object(fields) = self {
            fields.retain(|(name, _)| name != key);
        }
    }
    pub(crate) fn get(&self, key: &str) -> &Self {
        if let Self::Object(fields) = self {
            if let Some((_, value)) = fields.iter().find(|(name, _)| name == key) {
                return value;
            }
        }
        &Self::Scalar(Value::Null)
    }
    pub(crate) fn has(&self, key: &str) -> bool {
        matches!(self, Self::Object(fields) if fields.iter().any(|(name, _)| name == key))
    }
    pub(crate) fn string(&self) -> Option<&str> {
        match self {
            Self::Scalar(Value::String(value)) => Some(value),
            _ => None,
        }
    }
    pub(crate) fn object(&self, location: &str) -> Result<&[(String, Wire)]> {
        match self {
            Self::Object(fields) => Ok(fields),
            _ => Err(format!("{location} must be an object")),
        }
    }
    pub(crate) fn repr(&self) -> String {
        match self {
            Self::Scalar(Value::Null) => "None".into(),
            Self::Scalar(Value::Bool(value)) => if *value { "True" } else { "False" }.into(),
            Self::Scalar(Value::String(value)) => repr(value),
            Self::Scalar(value) => value.to_string(),
            Self::Array(values) => format!(
                "[{}]",
                values.iter().map(Self::repr).collect::<Vec<_>>().join(", ")
            ),
            Self::Object(fields) => format!(
                "{{{}}}",
                fields
                    .iter()
                    .map(|(key, value)| format!("{}: {}", repr(key), value.repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
}

pub(crate) fn repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut result = String::from(quote);
    for ch in value.chars() {
        match ch {
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            ch if ch == quote => {
                result.push('\\');
                result.push(ch);
            }
            ch if ch.is_control() && (ch as u32) <= 255 => {
                result.push_str(&format!("\\x{:02x}", ch as u32))
            }
            ch => result.push(ch),
        }
    }
    result.push(quote);
    result
}

impl<'de> Deserialize<'de> for Wire {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        use serde::de::Error;
        use serde::Deserializer;
        // Decode real containers explicitly: arbitrary_precision's synthetic
        // number map must never be mistaken for a source JSON object.
        let raw = Box::<serde_json::value::RawValue>::deserialize(deserializer)?;
        let text = raw.get();
        let mut decoder = serde_json::Deserializer::from_str(text);
        match text.as_bytes()[0] {
            b'{' => decoder
                .deserialize_map(WireVisitor)
                .map_err(D::Error::custom),
            b'[' => decoder
                .deserialize_seq(WireVisitor)
                .map_err(D::Error::custom),
            _ => {
                let mut value: Value = serde_json::from_str(text).map_err(D::Error::custom)?;
                if let Value::Number(number) = &value {
                    if text.contains(['.', 'e', 'E']) {
                        let float = number
                            .as_f64()
                            .ok_or_else(|| D::Error::custom("non-finite JSON number"))?;
                        value = Value::Number(
                            serde_json::Number::from_f64(float)
                                .ok_or_else(|| D::Error::custom("non-finite JSON number"))?,
                        );
                    } else if text == "-0" {
                        value = Value::Number(0.into());
                    }
                }
                Ok(Wire::Scalar(value))
            }
        }
    }
}
struct WireVisitor;
impl<'de> Visitor<'de> for WireVisitor {
    type Value = Wire;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("JSON")
    }
    fn visit_unit<E>(self) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(Value::Null))
    }
    fn visit_bool<E>(self, value: bool) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(value.into()))
    }
    fn visit_i64<E>(self, value: i64) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(value.into()))
    }
    fn visit_u64<E>(self, value: u64) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(value.into()))
    }
    fn visit_f64<E>(self, value: f64) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(value.into()))
    }
    fn visit_str<E>(self, value: &str) -> std::result::Result<Wire, E> {
        Ok(Wire::Scalar(value.into()))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> std::result::Result<Wire, A::Error> {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element()? {
            values.push(value);
        }
        Ok(Wire::Array(values))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> std::result::Result<Wire, A::Error> {
        let mut fields = Vec::new();
        let mut seen = BTreeSet::new();
        let mut duplicate = None;
        while let Some((key, value)) = map.next_entry::<String, Wire>()? {
            // object_pairs_hook runs after all child objects have decoded.
            if !seen.insert(key.clone()) && duplicate.is_none() {
                duplicate = Some(key.clone());
            }
            fields.push((key, value));
        }
        if let Some(key) = duplicate {
            return Err(serde::de::Error::custom(format!(
                "duplicate JSON key: {}",
                repr(&key)
            )));
        }
        Ok(Wire::Object(fields))
    }
}

pub(crate) fn parse(text: &str) -> Result<Wire> {
    serde_json::from_str(text).map_err(|error| {
        let message = error.to_string();
        if message.starts_with("duplicate JSON key: ") {
            // serde adds source coordinates to custom errors; Python's hook does not.
            message
                .rsplit_once(" at line ")
                .map_or(message.clone(), |(message, _)| message.to_owned())
        } else {
            message
        }
    })
}
