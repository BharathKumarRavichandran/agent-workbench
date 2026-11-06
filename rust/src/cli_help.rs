//! Python 3.12's default argparse help layout for the frozen public surface.
//! Presentation only; the canonical argv boundary lives in cli.rs.

pub(crate) fn text(command: &str) -> String {
    let mut options = vec![(
        "-h, --help",
        "[-h]".to_owned(),
        "show this help message and exit",
    )];
    match command {
        "" => options.extend([
            ("--root ROOT", "[--root ROOT]".into(), "repository checkout"),
            ("--profile PROFILE", "[--profile PROFILE]".into(), ""),
        ]),
        "validate" => options.extend([
            ("--json", "[--json]".into(), ""),
            ("--no-generated-check", "[--no-generated-check]".into(), ""),
        ]),
        "render" => options.push(("--check", "[--check]".into(), "")),
        "update" => options.push(("--source SOURCE", "--source SOURCE".into(), "")),
        _ => {
            options.push(("--target TARGET", "--target TARGET".into(), ""));
            options.push((
                "--destination DESTINATION",
                "[--destination DESTINATION]".into(),
                if command == "uninstall" {
                    ""
                } else {
                    "skills directory override"
                },
            ));
            if command != "uninstall" {
                options.push((
                    "--component {skills,agents,rules,commands,hooks,mcp}",
                    "[--component {skills,agents,rules,commands,hooks,mcp}]".into(),
                    "",
                ));
            }
            options.extend([
                (
                    "--project-root PROJECT_ROOT",
                    "[--project-root PROJECT_ROOT]".into(),
                    "",
                ),
                (
                    "--state-dir STATE_DIR",
                    "[--state-dir STATE_DIR]".into(),
                    "",
                ),
            ]);
            if command != "uninstall" {
                options.extend([
                    (
                        "--trust TOKEN",
                        "[--trust TOKEN]".into(),
                        "repeatable content-bound activation grant",
                    ),
                    ("--adopt", "[--adopt]".into(), ""),
                ]);
            }
            options.push(("--json", "[--json]".into(), ""));
        }
    }
    const CHOICES: &str = "{validate,render,plan,install,status,trust,uninstall,update}";
    let width = terminal_width() - 2;
    let prog = if command.is_empty() {
        "agent-workbench".to_owned()
    } else {
        format!("agent-workbench {command}")
    };
    // Required option operands are separate usage parts, unlike bracketed ones.
    let optional: Vec<&str> = options
        .iter()
        .flat_map(|(_, item, _)| {
            if item.starts_with('[') {
                vec![item.as_str()]
            } else {
                item.split(' ').collect()
            }
        })
        .collect();
    let positional = if command.is_empty() {
        vec![CHOICES, "..."]
    } else {
        vec![]
    };
    let joined = std::iter::once(prog.as_str())
        .chain(optional.iter().copied())
        .chain(positional.iter().copied())
        .collect::<Vec<_>>()
        .join(" ");
    let usage = if 7 + joined.len() as i64 <= width {
        joined
    } else if (7 + prog.len()) as f64 <= 0.75 * width as f64 {
        let indent = 8 + prog.len();
        let mut parts = vec![prog.as_str()];
        parts.extend(&optional);
        let mut lines = usage_lines(&parts, indent, 7, width);
        lines[0] = lines[0][indent..].to_owned();
        lines.extend(usage_lines(&positional, indent, indent, width));
        lines.join("\n")
    } else {
        let mut parts = optional.clone();
        parts.extend(&positional);
        let mut lines = usage_lines(&parts, 7, 7, width);
        if lines.len() > 1 {
            lines = usage_lines(&optional, 7, 7, width);
            lines.extend(usage_lines(&positional, 7, 7, width));
        }
        lines.insert(0, prog);
        lines.join("\n")
    };
    let mut output = format!("usage: {usage}\n\n");
    if command.is_empty() {
        output.push_str(&format!("positional arguments:\n  {CHOICES}\n\n"));
    }
    output.push_str("options:\n");
    let longest = options
        .iter()
        .map(|(name, _, _)| name.len())
        .max()
        .unwrap()
        .max(if command.is_empty() { CHOICES.len() } else { 0 });
    let help_column = (longest as i64 + 4).min(24.min((width - 20).max(4))) as usize;
    let help_width = (width - help_column as i64).max(11) as usize;
    for (name, _, description) in &options {
        output.push_str("  ");
        output.push_str(name);
        if !description.is_empty() {
            if name.len() + 4 > help_column {
                output.push('\n');
                output.push_str(&" ".repeat(help_column));
            } else {
                output.push_str(&" ".repeat(help_column - name.len() - 2));
            }
            output.push_str(
                &wrap_description(description, help_width)
                    .join(&format!("\n{}", " ".repeat(help_column))),
            );
        }
        output.push('\n');
    }
    output
}

fn usage_lines(parts: &[&str], indent: usize, first: usize, width: i64) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line: Vec<&str> = Vec::new();
    let mut length = first as i64 - 1;
    for part in parts {
        if length + 1 + part.len() as i64 > width && !line.is_empty() {
            lines.push(format!("{}{}", " ".repeat(indent), line.join(" ")));
            line.clear();
            length = indent as i64 - 1;
        }
        line.push(part);
        length += 1 + part.len() as i64;
    }
    if !line.is_empty() {
        lines.push(format!("{}{}", " ".repeat(indent), line.join(" ")));
    }
    lines
}

fn terminal_width() -> i64 {
    let Some(value) = std::env::var("COLUMNS").ok() else {
        return 80;
    };
    let value = value.trim();
    let (negative, value) = if let Some(v) = value.strip_prefix('-') {
        (true, v)
    } else {
        (false, value.strip_prefix('+').unwrap_or(value))
    };
    let mut number = 0i64;
    let mut previous_digit = false;
    let mut digits = 0;
    for c in value.chars() {
        if c == '_' && previous_digit {
            previous_digit = false;
            continue;
        }
        // Python int() accepts Unicode decimal digits, not other numerics.
        let digit = decimal_digit(c);
        let Some(digit) = digit else { return 80 };
        digits += 1;
        number = number.saturating_mul(10).saturating_add(digit as i64);
        previous_digit = true;
    }
    if negative || !previous_digit || digits > 4300 || number == 0 {
        80
    } else {
        number
    }
}

// Unicode 15.0 General_Category=Nd: Python 3.12 regex/int decimal repertoire.
pub(crate) fn decimal_digit(c: char) -> Option<u32> {
    const STARTS: &[u32] = &[
        0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66,
        0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
        0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
        0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
        0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x11f50, 0x16a60,
        0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0,
        0x1e950, 0x1fbf0,
    ];
    STARTS
        .iter()
        .map(|start| (c as u32).wrapping_sub(*start))
        .find(|digit| *digit < 10)
}

fn wrap_description(text: &str, width: usize) -> Vec<String> {
    // The fixed descriptions have ASCII words and one hyphenated compound.
    // Preserve textwrap's break-on-hyphens opportunities before packing lines.
    let mut chunks = std::collections::VecDeque::new();
    for (i, word) in text.split_ascii_whitespace().enumerate() {
        if i > 0 {
            chunks.push_back(" ".to_owned());
        }
        for part in word.split_inclusive('-') {
            chunks.push_back(part.to_owned());
        }
    }
    let mut lines = Vec::new();
    while !chunks.is_empty() {
        if chunks.front().is_some_and(|s| s == " ") {
            chunks.pop_front();
        }
        let mut line = String::new();
        while chunks
            .front()
            .is_some_and(|s| line.len() + s.len() <= width)
        {
            line.push_str(&chunks.pop_front().unwrap());
        }
        if let Some(mut chunk) = chunks.pop_front_if(|s| s.len() > width) {
            let available = width - line.len();
            line.push_str(&chunk[..available]);
            chunk.drain(..available);
            chunks.push_front(chunk);
        }
        lines.push(line.trim_end().to_owned());
    }
    lines
}
