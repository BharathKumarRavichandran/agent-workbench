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
       └── agent-skills artifact
```

Artifacts use registered string identifiers. This keeps the catalog open to future `agents`, `rules`, `hooks`, `commands`, and MCP artifacts without adding an agent field to every plugin.

## Boundaries

| Module | Responsibility |
|---|---|
| `codec.py` | Reject malformed or unknown external JSON and produce domain values. |
| `model.py` | Define immutable catalog, source, artifact, plugin, profile, and resolution values. |
| `profiles.py` | Resolve selectors and source-relative artifacts. |
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

An install target declares one required artifact, resolves its default destination, and expands selected plugins into desired entries. Codex, Claude, Grok, Hermes, and the shared Agent Skills directory are configurations of the same skill-directory target.

### Source updater

A source kind owns update semantics. Git submodules can advance to a configured branch. Local and curated-copy sources reject automatic updates until a specific updater is implemented.

## Validation contract

Validation rejects:

- unknown JSON fields and schema versions;
- invalid identifiers;
- absolute paths and `..` traversal;
- duplicate source and plugin IDs;
- missing sources, artifacts, manifests, provenance notices, or skill metadata;
- native manifest names that disagree with plugin IDs;
- duplicate exported skill names;
- unknown artifact kinds;
- generated output collisions and drift.

JSON Schema files provide editor support. The strict decoder and semantic validation are authoritative.

## Installation contract

Planning is side-effect free. Applying a plan is allowed only when no conflict exists.

| Filesystem state | Recorded ownership | Result |
|---|---|---|
| destination absent | either | `create` |
| exact expected link | owned | `keep` |
| exact expected link | unowned | conflict, or `adopt` with explicit flag |
| stale symlink | owned | `replace-owned` |
| regular file or foreign link | any | conflict |
| no longer selected expected link | owned | `remove-owned` |

State is evidence of ownership, not proof that the filesystem still matches. Every plan inspects the live path. Apply acquires a target-state lock and compares the complete state snapshot captured during planning, so concurrent or stale plans cannot erase newer ownership. Destination creation and mutations are performed relative to pinned directory descriptors; parent and root swaps therefore fail instead of redirecting writes. Uninstall removes only an owned link that still resolves to its expected source.

## Upstream boundary

`pstack` is a directory in the `cursor/plugins` monorepo, so the complete repository is the Git submodule at `vendor/cursor-plugins`. Artifact paths select the pstack directories required by each adapter. `catalog/lock.json` records the reviewed submodule revision.

Karpathy guidelines remain a curated copy because the artifact is one small skill. Its notice and upstream URL are part of validation.

## Profiles and overlays

Profiles select existing plugins. They do not rewrite provenance or package layout. A private work repository should depend on this public core and add private sources, plugins, and profiles. The public core must never depend on the work overlay.

## Compatibility

The old scripts remain as wrappers while the stable command is:

```bash
python3 -m agent_workbench <command>
```

The wrappers contain no duplicated catalog, rendering, validation, or installation logic.

## Deferred features

These should be added only when a real target needs them:

- copy installation for ephemeral cloud containers;
- trusted executable hooks and MCP components;
- external adapter discovery;
- profile inheritance;
- a capability graph with transformations;
- signatures for third-party packages.

The current artifact and adapter boundaries allow each addition without changing existing plugin records.
