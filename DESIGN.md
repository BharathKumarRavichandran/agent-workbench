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

Artifacts use registered string identifiers. Built-in inspectors recognize `agent-skills`, `agents`, `rules`, `hooks`, `cursor-hooks`, `claude-hooks`, `mcp`, and `commands`, while the catalog remains open to future adapter-defined kinds without adding an agent field to every plugin.

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
| `state.py` | Decode legacy ownership and atomically write path and slot evidence. |
| `bundles.py` | Snapshot and publish complete managed trees without executing them. |
| `shared_config.py` | Compile static hook paths and plan logical JSON slots. |
| `trust.py` | Bind explicit grants to content and provenance. |
| `transactions.py` | Lock shared documents and recover activation transactions. |
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

Artifact recognition is explicit in the adapter registry rather than inferred from renderer or install-target dependencies. Component inspection is inert: hook commands are not executed, MCP servers are not started, and configuration bodies are not copied into the lock report. Skills and supported agents, rules, and commands cross the path installation boundary. Explicit Cursor and Claude hook and MCP selections cross separate bundle and shared-document boundaries after a content-bound grant. Every inspection path remains inert.

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

Uninstall checks ownership evidence for every recorded component, independent of current profile component flags. Skills must still resolve to their expected sources. Copies must still match their recorded digests. Strict state v3 stores tagged resources and per-component roots at the existing state-file location. Each entry pins its source repository so an interrupted checkout migration preserves ownership of links that still use the old checkout. State v2 decodes as skills-only ownership and remains uninstallable. Path-only installation writes v3. Cursor and Claude hook and MCP activation write strict v4. Existing v2 and v3 ownership migrates without changing state-file locations.

## Upstream boundary

`pstack` is a directory in the `cursor/plugins` monorepo, so the complete repository is the Git submodule at `vendor/cursor-plugins`. Artifact paths select the pstack directories required by each adapter. `catalog/lock.json` records the reviewed submodule revision.

Karpathy guidelines remain a curated copy because the artifact is one small skill. Its notice and upstream URL are part of validation.

## Profiles and overlays

Profiles select existing plugins. They do not rewrite provenance or package layout. Profile v1 targets normalize to skills only. Strict profile v2 adds explicit component selections and optional absolute project roots. A missing selection still means skills. CLI component flags replace the profile selection.

Cursor supports skills, Markdown agents, and project `.mdc` rules. Claude supports skills, compatible Markdown agents, legacy Markdown commands, native nested hooks, and project MCP. Cursor also supports native flat hooks and MCP. The other built-ins support skills for the current formats. Unsupported requests return `COMPONENT_UNSUPPORTED` before mutation. Cursor rules without a project root return `PROJECT_ROOT_REQUIRED`. No conversion to unsupported native formats occurs. A private work repository should depend on this public core and add private sources, plugins, and profiles. The public core must never depend on the work overlay.

## Compatibility

The old scripts remain as wrappers while the stable command is:

```bash
agent-workbench <command>
```

`python3 -m agent_workbench` remains the frozen oracle. The wrappers contain no duplicated catalog, rendering, validation, or installation logic.

## Exact activation transactions

`adapters/activation.py` owns each target’s document names, plugin-root variable, hook representation, version policy, lock, journal, and ledger names. The compiler, slot planner, bundle executor, trust proofs, and recovery machinery are shared. No Claude-specific transaction stack or state schema is introduced.

| Exact representation | Cursor | Claude |
| --- | --- | --- |
| Flat command hooks (`hook-bundle`) | `.cursor/hooks.json` | `REPRESENTATION_UNSUPPORTED` |
| Nested matcher/handler hooks (`claude-hook-bundle`) | `REPRESENTATION_UNSUPPORTED` | `.claude/settings.json`, top-level `hooks` |
| `mcpServers` server objects | `.cursor/mcp.json` | Project root `.mcp.json` |
| User MCP | `~/.cursor/mcp.json` | `PROJECT_ROOT_REQUIRED`; no `~/.claude.json` access |

User hooks use the adapter-resolved `~/.cursor/hooks.json` or `~/.claude/settings.json`. Tests resolve home into temporary directories. Generic `hooks` artifacts are classified by shape; mixed/ambiguous documents are invalid. Explicit `cursor-hooks`/`claude-hooks` aliases allow both representations in one plugin, while profile selections remain `hooks`. Empty generic documents require Cursor’s version field; an empty Claude artifact uses its explicit alias. Existing catalog and lock rendering stay deterministic.

Claude groups require a nested `hooks` array and optional string `matcher`. Command handlers require `type: command` and `command`; non-command handler objects are preserved inertly under the same content-bound grant. Only contained local payload references are rewritten. Unsupported local paths fail before activation. MCP server objects are preserved without variable conversion, and target-incompatible plugin-root variables fail before mutation.

Claude’s project MCP document has a different parent from its hooks/settings, ledger, journal, and bundles. Each document is read, compared, quarantined, and published relative to its own authorized parent descriptor. The resource boundary lock remains under `.claude`; no root-level MCP file is opened relative to that descriptor. Every held parent/root identity participates in authenticated recovery evidence. Bundle removal is blocked while any remaining foreign hook command references it, including during component deselection.

`DirectoryCopy` is a closed path-resource variant for a complete hook artifact. Its synthetic plugin root contains `hooks/hooks.json` and every companion file. The digest covers relative paths, bytes, and modes, including empty directories. Symlinks, special files, and special permission bits are rejected. Descriptor-relative reads and writes pin directories, and atomic no-replace rename publishes a complete tree. This operation requires macOS `renameatx_np` or Linux `renameat2`.

`shared_config.py` handles JSON documents separately from path resources. A complete byte and file-identity snapshot detects stale plans, including a rewrite with identical JSON. Duplicate keys, symlinks, and special files fail at the boundary. The pure slot planner preserves foreign keys and array order. Slots use canonical content digests; no executable definitions appear in state or plan output.

Hooks own exact Cursor entries or whole Claude matcher-group objects within event arrays, so a duplicate is ambiguous. MCP owns named server objects, so different unmanaged content is a collision. Matching unmanaged content requires adoption. An externally modified owned slot blocks installation and uninstall. The private `.agent-workbench-owners.json` ledger prevents profiles, even with different state directories, from adopting the same logical slot. Its complete revision participates in the plan.

`trust.py` hashes plugin identity, component, target config path, artifact location, source provenance, and content into an `aw-trust-v1:` token. Both hooks and MCP require a CLI grant. A recorded grant can be reused only for unchanged content and scope. Changing any bundle file or mode invalidates hook trust; changing any MCP source byte invalidates MCP trust. Profile v1 stays skills-only, and profile v2 adds no implicit activation.

`transactions.py` takes the profile state lock followed by the target policy’s `.cursor/.agent-workbench.lock` or `.claude/.agent-workbench.lock`. That second lock covers both config documents and their ownership ledger across all profiles. Apply compares state, document revisions, and sources before preparing its durable `0600` journal. The authenticated journal records prior state, complete document bytes, parent device/inode identities, and path backups. Apply rechecks complete document revisions after source validation and immediately before document mutation. Recovery pins and verifies each authorized parent independently, using authenticated identities rather than trusting a new parent at the same path. Legacy authenticated Cursor journals remain recoverable. Journal destinations must match roots authorized by the current adapter request.

The mutation sequence is:

1. Remove deselected or changing owned hook entries and MCP slots.
2. Copy or replace managed bundles, or remove bundles whose entries are inactive.
3. Publish merged config additions and the ownership ledger.
4. Persist ownership state, then remove the journal.

All document writes use a temporary regular file, file fsync, atomic replacement, and directory fsync. Bundle publication and state writes also flush their containing directories. On a normal failure, rollback restores the prior activation. On a crash or failed rollback, the durable journal remains. The next `status`, `plan`, or apply for the owning profile repairs the transaction before new work. Recovery deactivates changing entries, restores bundles, restores prior documents, then restores state. Other profiles fail closed until the owner repairs the transaction.

Recovery compares live documents, managed trees, and state against the journal evidence. External changes block automatic recovery and retain the evidence. Journal deletion is never a substitute for repair. The read-only `trust` command reports pending recovery without applying it. Config locking coordinates workbench processes; external editors do not honor that lock, so live revisions are checked again before config publication.

## Rust migration: Candidate B facade

The Rust migration uses Candidate B: a compatibility facade that takes ownership of one complete observable path at a time while Python remains the executable specification for everything else. The facade preserves the existing global-option-before-subcommand CLI shape and emits the existing operation contract, rather than exposing a second Rust-native interface that callers would later need to unlearn. Each accepted path must have a black-box Python/Rust comparison before its scope expands.

The first lifecycle unit established `pstack`/Hermes Agent Skills across `plan`, `status`, `install`, and `uninstall`; subsequent units reuse that skills lifecycle for every built-in skills-directory target without introducing a generic adapter framework. A closed Rust target policy owns only the target identifier and default destination: `~/.agents/skills`, `~/.claude/skills`, `~/.cursor/skills`, and `~/.grok/skills`, plus `$CODEX_HOME/skills` or `~/.codex/skills` and `$HERMES_HOME/skills` or `~/.hermes/skills`. Discovery, reconciliation, state, and guarded filesystem mutation remain target-neutral. The unit decodes catalog v2, profile v1/v2, and ownership state v2/v3 strictly; resolves selected `agent-skills`; validates source trees and skill frontmatter; and reconciles create, keep, adopt, checkout-relocation replacement, deselection removal, and modified/foreign conflicts. Path-only installation writes deterministic v3 skills-only state at the Python state path, migrates v2 on install, and preserves v2 uninstall compatibility. State v4 and any non-skill resource or destination root fail closed.

Apply is a private boundary behind the command facade. It locks and rechecks the complete planned state, revalidates sources, pins destination ancestor and root identities, and performs relative-symlink mutation through no-follow directory descriptors. Owned replacement and removal quarantine first and restore failed ownership checks. Progress state is persisted after each mutation, so a late failure retains evidence for already-installed links; uninstall deletes state only after all owned links are safely removed. The differential lifecycle matrix runs all six built-in skills targets through environment-, profile-, and CLI-selected destination planning (including home expansion), clean default-destination install/status/uninstall, explicit skills component overrides for applicable multi-component targets, adoption, foreign and modified conflicts, checkout relocation, v2 migration/uninstall, absent state, and state rejection. Rust filesystem tests additionally exercise root swaps, source swaps, stale state, quarantine restoration, and late-failure progress. Non-skill components, other profiles and targets, and v4 activation remain in Python until later vertical slices carry their own state and side-effect parity tests.

## Deferred features

These should be added only when a real target needs them:

- hook and MCP adapters for targets beyond Cursor and Claude;
- user-scoped Claude MCP (`~/.claude.json`);
- external adapter discovery;
- profile inheritance;
- a capability graph with transformations;
- signatures for third-party packages.

The current artifact and adapter boundaries allow each addition without changing existing plugin records.
