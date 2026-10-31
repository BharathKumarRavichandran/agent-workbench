# Agent Workbench design

## Purpose

The repository owns the full lifecycle of portable agent extensions: sourcing, describing, adapting, validating, installing, updating, and removing them. Native marketplaces are outputs of the workbench rather than its internal model.

## Design decision

The implementation uses a typed compiled catalog with narrow adapters. This takes the strong boundaries from the typed-adapter candidate and the pure plan-before-apply behavior from the capability-graph candidate. It does not introduce a general graph resolver or configuration language because two marketplaces and one shared skill capability do not justify that complexity yet.

## Domain model

The catalog separates four concepts:

1. A source owns bytes and update behavior.
2. A plugin is a logical extension.
3. An artifact says what the plugin exports.
4. A profile selects plugins and allowed installation targets.

Installation ownership is machine-local state, not catalog data.

```text
Source
  └── Plugin
       ├── cursor-plugin artifact
       ├── claude-plugin artifact
       ├── agent-skills artifact
       └── agents / rules / hooks / mcp / commands artifacts
```

Artifacts use registered string identifiers. Built-in inspectors recognize `agent-skills`, `agents`, `rules`, `hooks`, `mcp`, and `commands`, while the catalog remains open to future adapter-defined kinds without adding an agent field to every plugin.

## Boundaries

| Module | Responsibility |
|---|---|
| `codec.py` | Reject malformed or unknown external JSON and produce domain values. |
| `model.py` | Define immutable catalog, source, artifact, plugin, profile, and resolution values. |
| `profiles.py` | Resolve selectors and source-relative artifacts. |
| `artifacts.py` | Resolve every catalog artifact and dispatch its registered inspector. |
| `adapters/components.py` | Validate and inventory portable component layouts without activating them. |
| `adapters/projections.py` | Define the capability table and pure projections of semantic exports into target paths. |
| `adapters/*` | Own native format or destination knowledge. |
| `rendering.py` | Detect output collisions and atomically write deterministic files. |
| `installation.py` | Reconcile desired entries with filesystem state and produce operations. |
| `state.py` | Read and atomically write path-ownership records. |
| `sources.py` | Advance a selected upstream and validate the result. |
| `validation.py` | Aggregate catalog, artifact, skill, adapter, provenance, and drift checks. |
| `cli.py` | Parse commands and present results. It owns no domain policy. |

Adapters do not mutate the filesystem. Install targets describe desired entries. The shared reconciler decides whether each entry is created, kept, adopted, replaced, removed, or rejected. Built-ins come from `build_registry()`; embedding code can inject an `AdapterRegistry` into `Workspace.load()` without changing catalog or validation code. Automatic package discovery remains intentionally deferred.

## Extension contracts

### Marketplace renderer

A renderer declares one required artifact, validates its native package, and returns generated files. Rendering infrastructure handles ordering, collisions, check mode, and atomic writes.

### Install target

An install target resolves authorized destination roots and carries its supported component representations. Inspectors expose semantic `ComponentExport` values with a closed `ComponentKind` and an explicit representation. Pure projections consume the registered target's capabilities and return paths or stable conflicts. They perform no mutation or format conversion.

`PathResource` is a closed union of `DirectorySymlink` and `FileCopy`. Future shared-document operations belong in a separate union. A copied file carries a SHA-256 digest. One reconciler executes both resource kinds, including uninstall. Cursor, Claude, Codex, Grok, Hermes, and the shared Agent Skills directory use the same path executor.

### Source updater

A source kind owns update semantics. Git submodules can advance to a configured branch. Local and curated-copy sources reject automatic updates until a specific updater is implemented.

## Validation contract

Validation rejects:

- unknown JSON fields and schema versions;
- invalid identifiers;
- absolute paths and `..` traversal;
- duplicate source and plugin IDs;
- missing sources, artifacts, manifests, provenance notices, or required component metadata;
- native manifest names that disagree with plugin IDs;
- duplicate exported skill names;
- unknown artifact kinds;
- generated output collisions and drift.

Artifact recognition is explicit in the adapter registry rather than inferred from renderer or install-target dependencies. Component inspection is inert: hook commands are not executed, MCP servers are not started, and configuration bodies are not copied into the lock report. Skills and supported agents, rules, and commands cross the path installation boundary. Hooks and MCP remain inert.

JSON Schema files provide editor support. The strict decoder and semantic validation are authoritative.

## Installation contract

Planning is side-effect free. Applying a plan is allowed only when no conflict exists.

| Filesystem state | Recorded ownership | Result |
|---|---|---|
| destination absent | either | `create` |
| exact expected resource | owned and unchanged | `keep` |
| matching link or byte-identical regular file | unowned | conflict, or `adopt` with explicit flag |
| unchanged resource with updated desired source | owned | `replace-owned` |
| foreign or modified owned path | either | conflict |
| unchanged resource no longer selected | owned | `remove-owned` |

State is evidence of ownership, not proof that the filesystem still matches. Every plan inspects the live path. Apply acquires a target-state lock and compares the complete state snapshot captured during planning, so concurrent or stale plans cannot erase newer ownership. Destination creation and mutations are performed relative to pinned directory descriptors; parent and root swaps therefore fail instead of redirecting writes. Each destination is a direct child of a root authorized by the adapter and request. State records cannot expand those roots. All roots used by a plan have descriptor and ancestor identities captured during planning.

Copied sources are read through directory descriptors without following symlinks. Apply revalidates their planned digests before destination creation and again before use. Copies are written to temporary regular files and atomically published without overwriting an existing path. Replacements first quarantine the owned file, then compare its recorded digest. Failed ownership checks restore the quarantined path. Failed state writes roll back the current mutation while preserving ownership of completed operations.

Uninstall checks ownership evidence for every recorded component, independent of current profile component flags. Skills must still resolve to their expected sources. Copies must still match their recorded digests. Strict state v3 stores tagged resources and per-component roots at the existing state-file location. Each entry pins its source repository so an interrupted checkout migration preserves ownership of links that still use the old checkout. State v2 decodes as skills-only ownership and remains uninstallable. Installation writes v3.

## Upstream boundary

`pstack` is a directory in the `cursor/plugins` monorepo, so the complete repository is the Git submodule at `vendor/cursor-plugins`. Artifact paths select the pstack directories required by each adapter. `catalog/lock.json` records the reviewed submodule revision.

Karpathy guidelines remain a curated copy because the artifact is one small skill. Its notice and upstream URL are part of validation.

## Profiles and overlays

Profiles select existing plugins. They do not rewrite provenance or package layout. Profile v1 targets normalize to skills only. Strict profile v2 adds explicit component selections and optional absolute project roots. A missing selection still means skills. CLI component flags replace the profile selection.

Cursor supports skills, Markdown agents, and project `.mdc` rules. Claude supports skills, compatible Markdown agents, and legacy Markdown commands. The other built-ins support skills for the current formats. Unsupported requests return `COMPONENT_UNSUPPORTED` before mutation. Cursor rules without a project root return `PROJECT_ROOT_REQUIRED`. No conversion to unsupported native formats occurs. A private work repository should depend on this public core and add private sources, plugins, and profiles. The public core must never depend on the work overlay.

## Compatibility

The old scripts remain as wrappers while the stable command is:

```bash
python3 -m agent_workbench <command>
```

The wrappers contain no duplicated catalog, rendering, validation, or installation logic.

## Deferred features

These should be added only when a real target needs them:

- trusted installation and activation of executable hooks and MCP components;
- external adapter discovery;
- profile inheritance;
- a capability graph with transformations;
- signatures for third-party packages.

The current artifact and adapter boundaries allow each addition without changing existing plugin records.
