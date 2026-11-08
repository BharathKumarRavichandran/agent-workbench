# Agent Workbench

A portable source, adapter, validation, and installation repository for AI-agent plugins and skills.

The workbench keeps upstream sources pinned, describes each plugin through agent-neutral artifacts, renders native marketplaces, resolves profiles, plans installations before mutation, and records which paths it owns.

## Included packages

| Plugin | Source | Update model |
|---|---|---|
| `pstack` | Cursor/Lauren Tan | pinned `cursor/plugins` submodule |
| `karpathy-guidelines` | Forrest Chang / multica-ai | curated copy with attribution |

## Architecture

```text
catalog JSON
    ↓ strict decoder
immutable domain model
    ├── marketplace renderers
    ├── component path projections
    ├── profile resolution
    ├── layered validation
    └── install planner
             ↓
      explicit operations
             ↓
       safe executor + state
```

The central catalog describes sources and artifacts such as `cursor-plugin`, `claude-plugin`, `agent-skills`, `agents`, `rules`, `hooks`, `cursor-hooks`, `claude-hooks`, `mcp`, and `commands`. Registered inspectors validate and inventory each artifact independently from rendering and installation. Adding another skill-directory agent does not require changing every plugin.

## Clone and verify

```bash
git clone --recurse-submodules <repository-url> agent-workbench
cd agent-workbench
make check
```

The default CLI is the Rust `agent-workbench` executable. Rust is the specification. There is no live Python oracle.

## Default CLI

The documented default CLI is the Rust `agent-workbench` executable. An install places one binary at `$PREFIX/bin/agent-workbench`. It does not publish Git tags or GitHub releases.

```bash
make rust-release PREFIX=$HOME/.local
export PATH="$HOME/.local/bin:$PATH"
```

The install path is `$PREFIX/bin/agent-workbench`. Upgrade consumes producer-written ownership in place (status and keep-reinstall). Uninstall removes only recorded owned resources:

```bash
agent-workbench --profile pstack status --target hermes
agent-workbench --profile pstack install --target hermes
agent-workbench --profile pstack uninstall --target hermes
```

## Transition invocations

Rust is the default CLI. Makefile targets (`make validate`, `make render`, `make update`, `make plan-hermes`) dispatch to that default through `scripts/agent-workbench`.

The Python runtime is removed. `python3 -m` is not a supported runtime. `scripts/agent-workbench` is a transition wrapper that execs the Rust `agent-workbench` executable. It does not replace `$PREFIX/bin/agent-workbench`:

```bash
scripts/agent-workbench --profile pstack status --target hermes
```

## Intentional differences

Rust is the specification. The F6 catalog IDs `E1`, `E2`, `VAL-GIT-1`, `RENDER-GIT-1`, `UPDATE-GIT-1`, `UPDATE-CONFIG-1`, `PYTHON-TO-RUST-UPGRADE`, `REVERSE-STATUS-NOT-SUPPORTED`, and `REJECT-V4-AS-V3` remain historical documentation only. They are not re-proven by paired Python/Rust processes.

Exact command, failure boundary, stdout/stderr class, exit status, and state/config effect for each historical row: [`rust/SECTION_F_CONTRACT.md`](rust/SECTION_F_CONTRACT.md) (F6) and [`rust/PARITY_EXCEPTIONS.md`](rust/PARITY_EXCEPTIONS.md).

## Commands

```bash
# Validate the catalog, manifests, component artifacts, lock report, and generated files.
agent-workbench validate

# Render native marketplaces and catalog/lock.json.
agent-workbench render
agent-workbench render --check

# Preview an installation without changing the machine.
agent-workbench --profile pstack plan --target hermes

# Apply it. Existing exact links require explicit adoption once.
agent-workbench --profile pstack install --target hermes --adopt

# Inspect current desired-versus-actual state.
agent-workbench --profile pstack status --target hermes

# Remove only paths recorded as owned by this workbench.
agent-workbench --profile pstack uninstall --target hermes

# Advance the reviewed upstream source, then render and validate.
agent-workbench update --source cursor-plugins
```

`--destination` overrides the skills directory. `--project-root` selects project directories for components. `--state-dir` overrides machine-local ownership storage.

To install pstack agents into a project:

```bash
agent-workbench --profile pstack plan --target cursor --component agents --project-root /path/to/project
agent-workbench --profile pstack install --target cursor --component agents --project-root /path/to/project
agent-workbench --profile pstack uninstall --target cursor --project-root /path/to/project
```

Repeat `--component` to select more than one kind, such as `--component skills --component agents`. The same flags apply to `plan`, `install`, and `status`. Text and JSON operations identify the component and resource kind. Unsupported requests produce `COMPONENT_UNSUPPORTED` conflicts before mutation, even when selected plugins export none of that component.

## Supported components

The table describes current artifact formats. The workbench copies supported documents without format conversion.

| Target | Skills | Markdown agents | Cursor `.mdc` rules | Legacy Markdown commands |
|---|---|---|---|---|
| `agents` | directory symlink | unsupported | unsupported | unsupported |
| `cursor` | directory symlink | managed copy | managed copy, project only | unsupported |
| `claude` | directory symlink | managed copy | unsupported | managed copy |
| `codex`, `hermes`, `grok` | directory symlink | unsupported | unsupported | unsupported |

For Cursor and Claude, `--project-root /path/to/project` selects `.cursor` or `.claude` under that project. Each component uses its own directory: `skills`, `agents`, `rules`, or `commands`. Without a project root, supported components use the target's home directory. Cursor rules require `--project-root` and otherwise produce `PROJECT_ROOT_REQUIRED`. An explicit `--destination` overrides only the skills directory, including when a project root is present.

Cursor and Claude support explicit activation of their exact hook formats and MCP definitions. There is no format conversion; requesting the other target’s hook representation returns `REPRESENTATION_UNSUPPORTED`. Other targets report `COMPONENT_UNSUPPORTED`. Inspection, planning, and installation never execute hooks or start servers.

To activate Cursor hooks and MCP for a project, first inspect the plan and its trust tokens:

```bash
agent-workbench trust --target cursor --project-root /path/to/project \
  --component hooks --component mcp --json
agent-workbench install --target cursor --project-root /path/to/project \
  --component hooks --component mcp \
  --trust aw-trust-v1:<hooks-token> --trust aw-trust-v1:<mcp-token>
```

`trust` is read-only and exits with code 1 while grants are required. It reports the same tokens as `plan` and `status`. Repeat `--trust` for each required plugin and component. Tokens bind content, artifact location, source provenance, target, and config path. Unchanged owned activations reuse their grants. Changed content requires new tokens. MCP definitions always require trust, including remote servers.

Project activation merges `.cursor/hooks.json` and `.cursor/mcp.json`. Without `--project-root` or a profile `projectRoot`, Cursor uses `~/.cursor/hooks.json` and `~/.cursor/mcp.json`. `--destination` still changes only the skills directory. Hook commands run from the project root for project scope and from `~/.cursor` for user scope.

Exact activation paths and formats:

| Target / component | Artifact format | Project document | User document |
| --- | --- | --- | --- |
| Cursor hooks | `hooks.Event[]` contains command entries; optional `version: 1` | `.cursor/hooks.json` | `~/.cursor/hooks.json` |
| Claude hooks | `hooks.Event[]` contains matcher groups with nested `hooks[]` handlers | `.claude/settings.json`, top-level `hooks` | `~/.claude/settings.json`, top-level `hooks` |
| Cursor MCP | Top-level `mcpServers` map | `.cursor/mcp.json` | `~/.cursor/mcp.json` |
| Claude MCP | Top-level `mcpServers` map | `.mcp.json` in the project root | Unsupported: `PROJECT_ROOT_REQUIRED` |

Use the same trust/install commands above with `--target claude` for Claude-native artifacts. Claude user MCP does not authorize or write `~/.claude.json`; pass `--project-root`. User hook paths come from the target adapter’s home resolution, independently of `--destination`.

A generic `hooks` artifact is classified by its document shape. Mixed or ambiguous documents fail before activation. Use explicit `cursor-hooks` and `claude-hooks` artifacts when one plugin provides both representations. Empty generic hooks require Cursor’s `version: 1`; empty Claude bundles require `claude-hooks`. Profiles still select `hooks`, never the artifact aliases.

Claude matcher groups, nested handler fields, and non-command handlers are preserved exactly. Only command handlers’ contained `${CLAUDE_PLUGIN_ROOT}/hooks/...`, quoted equivalents, and declared artifact-relative payload references are rewritten to `.claude/.agent-workbench-hooks/<plugin>/hooks/...`. Non-command handlers are inspected inertly and included in content-bound trust. Arbitrary absolute payloads, traversal, missing files, and unresolved local paths fail before activation. Standalone MCP server objects remain byte-semantically unchanged only when portable: Cursor/Claude plugin-root variables and plugin-local relative payload references are rejected because `.mcp.json` and `mcp.json` do not provide the original plugin root.

Each hook bundle is copied into `.cursor/.agent-workbench-hooks/<plugin>/hooks/`, with executable modes preserved. Static `./hooks/...`, `${CURSOR_PLUGIN_ROOT}/hooks/...`, and artifact-relative `./...` references become absolute managed paths. Escaping, missing, or unresolved local path references are rejected. Shell text and event entry fields otherwise stay unchanged. Commands never point into the checkout.

MCP ownership covers individual `mcpServers/<name>` objects. Hook ownership covers exact Cursor entries or whole Claude matcher-group objects in `hooks/<event>` arrays. Foreign entries and keys survive installation and uninstall. Matching unmanaged slots require `--adopt` as well as trust. Different MCP content, duplicate hook entries, and edited owned slots are conflicts. An ownership ledger prevents another profile from adopting an already-owned slot.

Cursor and Claude activation use one shared document lock and a private recovery journal under `.cursor` or `.claude`. The journal may contain config values and bundle backups, has mode `0600`, and is removed after commit. On interruption, run `status` with the same profile, state directory, and scope to restore the prior activation. Other profiles wait for that recovery. If an external edit prevents safe recovery, the command fails and retains the journal and bundle evidence for repair. A pending journal makes `trust` fail without modifying anything.

## Profiles

- `default`: all plugins;
- `pstack`: pstack only;
- `minimal`: Karpathy guidelines only.

Profiles select plugins and allowed targets. They cannot change source provenance or artifact paths.

Version 1 profiles normalize every target to `skills` only. The checked-in profiles remain version 1, including the new Cursor target. Version 2 adds optional `components` and `projectRoot` fields to each target:

```json
{
  "schemaVersion": 2,
  "id": "project",
  "plugins": {"include": ["pstack"], "exclude": []},
  "targets": [{
    "id": "cursor",
    "components": ["skills", "agents"],
    "projectRoot": "/path/to/project"
  }]
}
```

Without `--component`, commands use the profile's selection, which defaults to skills. Explicit component flags replace that selection for the request. Installation removes previously owned resources that are no longer selected. Uninstall removes all recorded resources regardless of the current component selection. Custom destinations and project roots must match the installation request during uninstall. State cannot authorize an arbitrary destination.

## Safety behavior

The installer builds a complete plan before writing anything.

- Foreign paths and modified owned paths are conflicts.
- Matching unmanaged skill links and byte-identical regular files require `--adopt`.
- Repeated installation converges to `keep` operations.
- State writes are atomic and guarded by an operation lock; stale plans are rejected against the full ownership snapshot.
- Destination roots are created and mutated through pinned directory descriptors, preventing parent/root symlink swaps from redirecting writes.
- Copied files carry a SHA-256 digest in the plan and state. Apply checks source bytes again, then publishes the complete file atomically.
- Replacement and removal quarantine each resource and check its ownership evidence before deletion.
- Uninstall removes owned links that still point to the expected source and owned files whose digest still matches.
- Moved checkouts produce `replace-owned` operations rather than silent deletion.

Path-only installations write strict state schema v3. Cursor and Claude activation write v4 with bundle digests, content-bound grants, and logical slot evidence. States v2 and v3 remain uninstallable and migrate during activation. State-file locations remain unchanged:

```text
${XDG_STATE_HOME:-~/.local/state}/agent-workbench/<marketplace>/<profile>/<target>.json
```

## Generated outputs

- `.cursor-plugin/marketplace.json`
- `.claude-plugin/marketplace.json`
- `catalog/lock.json`

The lock report records source revisions, plugin versions, artifact roots, and per-component counts. Hooks and MCP definitions are inspected as inert data: validation never executes hooks or starts servers. All generated files are deterministic and checked in CI.

## Repository layout

```text
agent-workbench/
├── rust/                      # default CLI crate
├── catalog/                   # canonical catalog, schema, lock report
├── profiles/                  # package and target selections
├── plugins/                   # locally owned or curated packages
├── vendor/                    # pinned upstream sources
└── scripts/                   # default CLI wrapper
```

## Adding a plugin

1. Add or reuse a source in `catalog/plugins.json`.
2. Add the plugin with one or more artifact roots.
3. Preserve provenance and a notice for curated copies.
4. Add it to the appropriate profiles.
5. Run `make render check`.

Component artifacts are validated and reported independently of installation. Skills and the supported document formats listed above can be installed into target directories. A `hooks` artifact points to the complete bundle directory containing `hooks.json` and its companion scripts; an `mcp` artifact points directly to its JSON file. Cursor and Claude activation require explicit component selection and content-bound trust. Other targets remain unsupported for hooks and MCP.

## Adding an agent

If the agent consumes standard skill directories, register another `SkillDirectoryTarget` with its destination. No plugin entries change.

If it needs a native marketplace format, add one narrow renderer implementing the marketplace adapter contract, register it once, and add golden behavior tests.

See [DESIGN.md](DESIGN.md) for contracts and design decisions. Development and commit-message rules are in [CONTRIBUTING.md](CONTRIBUTING.md).
