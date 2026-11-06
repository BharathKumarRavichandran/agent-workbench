//! Frozen Python 3.12 argparse boundary. Values never re-enter token parsing.
use crate::*;

const COMMANDS: &[&str] = &[
    "validate",
    "render",
    "plan",
    "install",
    "status",
    "trust",
    "uninstall",
    "update",
];
const COMPONENTS: &[&str] = &["skills", "agents", "rules", "commands", "hooks", "mcp"];

#[derive(Debug, Default)]
pub(crate) struct Options {
    pub root: PathBuf,
    pub profile: String,
    pub target: String,
    pub destination: Option<PathBuf>,
    pub project_root: Option<PathBuf>,
    pub state_dir: Option<PathBuf>,
    pub components: Vec<ComponentKind>,
    pub trust: Vec<String>,
    pub adopt: bool,
    pub json: bool,
}

pub(crate) enum Command {
    Validate {
        options: Options,
        generated: bool,
    },
    Render {
        options: Options,
        check: bool,
    },
    Update {
        options: Options,
        source: String,
    },
    Lifecycle {
        options: Options,
        action: lifecycle::Action,
    },
}

pub(crate) enum Parsed {
    Command(Command),
    Output(CommandOutput),
}

fn output(command: &str, error: Option<String>) -> CommandOutput {
    let text = if command.is_empty() {
        usage_text()
    } else {
        cli_help::text(command)
    };
    match error {
        None => CommandOutput {
            stdout: text,
            stdout_single_write: true,
            stderr: Vec::new(),
            exit_code: 0,
        },
        Some(error) => {
            let usage = text.split("\n\n").next().unwrap();
            let name = if command.is_empty() {
                "agent-workbench".into()
            } else {
                format!("agent-workbench {command}")
            };
            CommandOutput {
                stdout: String::new(),
                stdout_single_write: true,
                stderr: format!("{usage}\n{name}: error: {error}\n").into_bytes(),
                exit_code: 2,
            }
        }
    }
}

fn declarations(command: &str) -> Vec<(&'static str, bool)> {
    let mut result = vec![("--help", false)];
    match command {
        "" => result.extend([("--root", true), ("--profile", true)]),
        "validate" => result.extend([("--json", false), ("--no-generated-check", false)]),
        "render" => result.push(("--check", false)),
        "update" => result.push(("--source", true)),
        _ => {
            result.extend([("--target", true), ("--destination", true)]);
            if command != "uninstall" {
                result.push(("--component", true));
            }
            result.extend([("--project-root", true), ("--state-dir", true)]);
            if command != "uninstall" {
                result.extend([("--trust", true), ("--adopt", false)]);
            }
            result.push(("--json", false));
        }
    }
    result
}

#[derive(Clone)]
enum Token {
    Value,
    End,
    Unknown,
    Invalid(String),
    Option(&'static str, bool, Option<String>),
}

fn path(value: String) -> PathBuf {
    if value.is_empty() {
        PathBuf::from(".")
    } else {
        PathBuf::from(value)
    }
}

fn negative_number(value: &str) -> bool {
    let Some(value) = value.strip_prefix('-') else {
        return false;
    };
    let decimal = |c| cli_help::decimal_digit(c).is_some();
    // Regex $ also matches immediately before one terminal newline.
    let value = value.strip_suffix('\n').unwrap_or(value);
    if !value.is_empty() && value.chars().all(decimal) {
        return true;
    }
    if let Some((left, right)) = value.split_once('.') {
        return left.chars().all(decimal) && !right.is_empty() && right.chars().all(decimal);
    }
    false
}

fn classify(value: &str, options: &[(&'static str, bool)]) -> Result<Token> {
    if value == "--" {
        return Ok(Token::End);
    }
    if value == "-" || !value.starts_with('-') {
        return Ok(Token::Value);
    }
    let (name, explicit) = value
        .split_once('=')
        .map_or((value, None), |(name, value)| {
            (name, Some(value.to_owned()))
        });
    if let Some(&(name, takes)) = options.iter().find(|(key, _)| *key == name) {
        return Ok(Token::Option(name, takes, explicit));
    }
    if name == "-h" {
        return Ok(Token::Option("--help", false, explicit));
    }
    if value.starts_with("--") {
        let matches: Vec<_> = options
            .iter()
            .filter(|(key, _)| key.starts_with(name))
            .collect();
        if matches.len() > 1 {
            return Err(format!(
                "ambiguous option: {value} could match {}",
                matches
                    .iter()
                    .map(|(name, _)| *name)
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        if let Some(&&(name, takes)) = matches.first() {
            return Ok(Token::Option(name, takes, explicit));
        }
    } else if value.starts_with("-h") {
        // Python 3.12 executes the first help action before examining its cluster tail.
        return Ok(Token::Option("--help", false, None));
    }
    if negative_number(value) || value.contains(' ') {
        Ok(Token::Value)
    } else {
        Ok(Token::Unknown)
    }
}

pub(crate) fn parse(args: &[String]) -> Parsed {
    match parse_inner(args) {
        Ok(command) => Parsed::Command(command),
        Err(output) => Parsed::Output(output),
    }
}

fn parse_inner(args: &[String]) -> std::result::Result<Command, CommandOutput> {
    let args = args.get(1..).unwrap_or_default();
    let mut options = Options {
        root: ".".into(),
        profile: "default".into(),
        ..Options::default()
    };
    let mut command = "";
    let mut source = None;
    let mut target_seen = false;
    let mut generated = true;
    let mut check = false;
    let mut extras = Vec::new();
    let mut index = 0;
    loop {
        let declarations = declarations(command);
        // argparse classifies every token at this parser level before actions.
        let mut ended = false;
        let mut tokens = Vec::new();
        for arg in &args[index..] {
            let token = if ended {
                Token::Value
            } else {
                classify(arg, &declarations).unwrap_or_else(Token::Invalid)
            };
            if matches!(token, Token::End) {
                ended = true;
            }
            tokens.push(token);
        }
        let start = index;
        let mut child = false;
        while index < args.len() {
            let arg = &args[index];
            match &tokens[index - start] {
                Token::Invalid(error) => return Err(output(command, Some(error.clone()))),
                Token::End if command.is_empty() => {}
                Token::End | Token::Unknown => extras.push(arg.clone()),
                Token::Value if command.is_empty() => {
                    if !COMMANDS.contains(&arg.as_str()) {
                        return Err(output(
                            command,
                            Some(format!(
                                "argument command: invalid choice: {} (choose from {})",
                                state::wire::repr(arg),
                                COMMANDS.join(", ")
                            )),
                        ));
                    }
                    command = arg;
                    index += 1;
                    child = true;
                    break;
                }
                Token::Value => extras.push(arg.clone()),
                Token::Option(key, takes, explicit) => {
                    let display = if *key == "--help" { "-h/--help" } else { key };
                    let value = if *takes {
                        if let Some(value) = explicit {
                            Some(value.clone())
                        } else if matches!(tokens.get(index - start + 1), Some(Token::Value)) {
                            index += 1;
                            Some(args[index].clone())
                        } else {
                            return Err(output(
                                command,
                                Some(format!("argument {display}: expected one argument")),
                            ));
                        }
                    } else {
                        if let Some(value) = explicit {
                            return Err(output(
                                command,
                                Some(format!(
                                    "argument {display}: ignored explicit argument {}",
                                    state::wire::repr(value)
                                )),
                            ));
                        }
                        None
                    };
                    match *key {
                        "--help" => return Err(output(command, None)),
                        "--root" => options.root = path(value.unwrap()),
                        "--profile" => options.profile = value.unwrap(),
                        "--target" => {
                            options.target = value.unwrap();
                            target_seen = true;
                        }
                        "--source" => source = value,
                        "--destination" => options.destination = value.map(path),
                        "--project-root" => options.project_root = value.map(path),
                        "--state-dir" => options.state_dir = value.map(path),
                        "--trust" => options.trust.push(value.unwrap()),
                        "--component" => {
                            let value = value.unwrap();
                            if !COMPONENTS.contains(&value.as_str()) {
                                return Err(output(
                                    command,
                                    Some(format!(
                                        "argument --component: invalid choice: {} (choose from {})",
                                        state::wire::repr(&value),
                                        COMPONENTS.join(", ")
                                    )),
                                ));
                            }
                            let kind = match value.as_str() {
                                "skills" => ComponentKind::Skills,
                                "agents" => ComponentKind::Agents,
                                "rules" => ComponentKind::Rules,
                                "commands" => ComponentKind::Commands,
                                "hooks" => ComponentKind::Hooks,
                                _ => ComponentKind::Mcp,
                            };
                            options.components.push(kind);
                        }
                        "--json" => options.json = true,
                        "--adopt" => options.adopt = true,
                        "--check" => check = true,
                        "--no-generated-check" => generated = false,
                        _ => unreachable!(),
                    }
                }
            }
            index += 1;
        }
        if child {
            continue;
        }
        break;
    }
    let required = match command {
        "" => Some("command"),
        "update" if source.is_none() => Some("--source"),
        "plan" | "install" | "status" | "trust" | "uninstall" if !target_seen => Some("--target"),
        _ => None,
    };
    if let Some(key) = required {
        return Err(output(
            command,
            Some(format!("the following arguments are required: {key}")),
        ));
    }
    if !extras.is_empty() {
        return Err(output(
            "",
            Some(format!("unrecognized arguments: {}", extras.join(" "))),
        ));
    }
    Ok(match command {
        "validate" => Command::Validate { options, generated },
        "render" => Command::Render { options, check },
        "update" => Command::Update {
            options,
            source: source.unwrap(),
        },
        _ => Command::Lifecycle {
            options,
            action: match command {
                "plan" => lifecycle::Action::Plan,
                "install" => lifecycle::Action::Install,
                "status" => lifecycle::Action::Status,
                "trust" => lifecycle::Action::Trust,
                "uninstall" => lifecycle::Action::Uninstall,
                _ => unreachable!(),
            },
        },
    })
}

impl Options {
    pub(crate) fn lifecycle(self, action: lifecycle::Action) -> Result<Cli> {
        // argparse appends; the domain request deduplicates in first-seen order.
        let mut components = Vec::new();
        for kind in self.components {
            if !components.contains(&kind) {
                components.push(kind);
            }
        }
        Ok(Cli {
            root: self.root,
            profile: self.profile,
            target: SkillsTarget::parse(&self.target)?,
            components: if components.is_empty() {
                None
            } else {
                Some(components)
            },
            destination: self.destination.map(expand_home_path).transpose()?,
            project_root: self.project_root.map(expand_home_path).transpose()?,
            state_dir: self
                .state_dir
                .map(expand_home_path)
                .unwrap_or_else(default_state_dir)?,
            adopt: self.adopt,
            trust: self.trust,
            json: self.json,
            action,
        })
    }
}
