//! Python json.dumps formatting for ordered shared documents and sorted digests.
use super::*;

// serde's shortest finite representation uses the same round-trip digits, but
// Python chooses scientific notation outside [-4, 16) and pads its exponent.
fn float_text(value: &serde_json::Number) -> String {
    let raw = value.to_string();
    let (sign, raw) = raw
        .strip_prefix('-')
        .map_or(("", raw.as_str()), |v| ("-", v));
    let (mantissa, exponent) = raw
        .split_once('e')
        .map_or((raw, 0), |(m, e)| (m, e.parse::<i32>().unwrap()));
    let point = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
    let digits = mantissa.replace('.', "");
    let first = digits.find(|ch| ch != '0');
    let Some(first) = first else {
        return format!("{sign}0.0");
    };
    let exponent = exponent + point - first as i32 - 1;
    let digits = digits[first..].trim_end_matches('0');
    if !(-4..16).contains(&exponent) {
        let fraction = if digits.len() > 1 {
            format!(".{}", &digits[1..])
        } else {
            String::new()
        };
        let exponent = if exponent < 0 {
            format!("-{:02}", -exponent)
        } else {
            format!("+{exponent:02}")
        };
        format!("{sign}{}{fraction}e{exponent}", &digits[..1])
    } else {
        let point = exponent + 1;
        let body = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        format!("{sign}{body}")
    }
}
fn string(value: &str, ascii: bool) -> Result<String> {
    if ascii {
        String::from_utf8(crate::trust::ascii_json(&serde_json::Value::String(
            value.into(),
        ))?)
        .map_err(|e| e.to_string())
    } else {
        serde_json::to_string(value).map_err(|e| e.to_string())
    }
}
fn render(value: &Wire, pretty: bool, depth: usize) -> Result<String> {
    let pad = |depth| "  ".repeat(depth);
    let join = |parts: Vec<String>, open: &str, close: &str| {
        if parts.is_empty() {
            format!("{open}{close}")
        } else if pretty {
            format!(
                "{open}\n{}{}\n{}{close}",
                pad(depth + 1),
                parts.join(&format!(",\n{}", pad(depth + 1))),
                pad(depth)
            )
        } else {
            format!("{open}{}{close}", parts.join(","))
        }
    };
    Ok(match value {
        Wire::Scalar(serde_json::Value::Number(number)) if number.is_f64() => float_text(number),
        Wire::Scalar(serde_json::Value::String(value)) => string(value, !pretty)?,
        Wire::Scalar(value) => value.to_string(),
        Wire::Array(items) => join(
            items
                .iter()
                .map(|v| render(v, pretty, depth + 1))
                .collect::<Result<_>>()?,
            "[",
            "]",
        ),
        Wire::Object(fields) => {
            let mut fields = fields.iter().collect::<Vec<_>>();
            if !pretty {
                fields.sort_by(|a, b| a.0.cmp(&b.0));
            }
            join(
                fields
                    .into_iter()
                    .map(|(key, value)| {
                        Ok(format!(
                            "{}:{}{}",
                            string(key, !pretty)?,
                            if pretty { " " } else { "" },
                            render(value, pretty, depth + 1)?
                        ))
                    })
                    .collect::<Result<_>>()?,
                "{",
                "}",
            )
        }
    })
}
pub(crate) fn pretty(value: &Wire) -> Result<String> {
    Ok(render(value, true, 0)? + "\n")
}
pub(crate) fn canonical(value: &Wire) -> Result<String> {
    render(value, false, 0)
}
