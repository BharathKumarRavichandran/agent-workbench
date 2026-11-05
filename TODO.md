# Roadmap

This file tracks remaining work. The existing Cursor and Claude project lifecycle is complete; the unchecked Feature #1 items broaden native host coverage rather than repair that implementation.

## Completed baseline

- [x] Inventory and validate skills, agents, rules, commands, hooks, and MCP artifacts without executing them.
- [x] Install target-compatible skills and non-executable path components with recorded ownership.
- [x] Activate trusted Cursor hooks and MCP with managed payload copies, shared-slot ownership, transactional recovery, and safe uninstall.
- [x] Activate trusted Claude-native hooks and project MCP through `.claude/settings.json` and `.mcp.json`.
- [x] Preserve skills-only behavior for existing profiles and reject unsupported or lossy target/component combinations before mutation.

## Feature #9 — Rust conversion before further feature growth

Convert the workbench to a standalone Rust CLI without extending product behavior. Python remains the executable specification until cutover. Work through these checkboxes in order; each unchecked target or subsystem should normally be one focused, parity-gated PR.

### A. Migration rules and completed foundation

- [x] Freeze feature growth during the rewrite; accept only correctness fixes that preserve or clarify existing behavior.
- [x] Keep Python runnable as the oracle and retain all Python tests in CI.
- [x] Create a separate Rust crate and run format, check, clippy, tests, and Python/Rust parity in CI.
- [x] Strictly decode catalog v2 and profile v1/v2, including duplicate keys, unknown fields, nulls, versions, and unsafe paths.
- [x] Discover and validate `agent-skills` trees without following symlinks or executing content.
- [x] Emit deterministic JSON operations with Python-compatible field order and exit codes.
- [x] Implement strict ownership state v2/v3 loading, deterministic v3 writes, v2 migration, locks, stale-state checks, and mode `0600`.
- [x] Implement create, keep, adopt, replace-owned, remove-owned, conflict, checkout relocation, progress state, rollback, and uninstall.
- [x] Perform destination mutation through pinned, no-follow directory descriptors with quarantine and root/source identity revalidation.
- [x] Add deterministic race regressions for source swaps, destination-root swaps, stale state, quarantine restoration, and late failures.
- [x] Prove the complete `pstack` skills lifecycle for `hermes` against Python.
- [x] Generalize that lifecycle through a closed target policy and prove the `agents` target without adding a generic adapter framework.
- [x] Cover environment, profile, and CLI destination precedence; home expansion; component overrides; state isolation; and default install/status/uninstall.

### B. Remaining skills-directory targets

- [x] Port `codex` skills with `CODEX_HOME`/`~/.codex/skills` precedence and full lifecycle parity.
- [x] Port `grok` skills with `~/.grok/skills` precedence and full lifecycle parity.
- [x] Port `claude` skills with `~/.claude/skills`, explicit skills overrides, and full lifecycle parity.
- [x] Port `cursor` skills with `~/.cursor/skills`, explicit skills overrides, and full lifecycle parity.
- [x] Run one shared all-target matrix proving state-file isolation when targets use the same marketplace, profile, and state root.
- [x] Reassess the closed target policy after all six skills targets; extract a deeper abstraction only if repeated code remains.

### C. Copied path components and multi-root state

- [x] Model copied files/directories and digests as closed Rust resource types rather than stringly operation variants.
- [x] Port state v3 `destinationRoots` validation and authorization for multiple component roots.
- [x] Port copied-resource create, keep, exact-copy adoption, replace-owned, remove-owned, conflict, rollback, and uninstall.
- [x] Port Cursor agents, rules, and commands with project-root requirements and Python/Rust differential tests.
- [x] Port Claude agents and legacy commands with exact representation selection and differential tests.
- [x] Prove source modes, installed bytes, digests, symlink rejection, parent/root swaps, mixed roots, and interrupted removal on macOS and Linux.

### D. Trusted activation and state v4

- [x] Port strict state v4 decoding while retaining safe v2/v3 uninstall and migration behavior.
- [x] Port content-bound trust grants, token verification, changed-content invalidation, and read-only trust inspection.
- [x] Port immutable managed bundle publication and digest/mode verification.
- [x] Port slot-level shared-document ownership while preserving foreign fields, entries, and ordering.
- [x] Port resource/document locks, stale snapshots, atomic publication, durable authenticated journals, and retryable recovery.
- [x] Port Cursor hooks and project MCP lifecycle with full Python/Rust operation, byte, mode, state, recovery, and uninstall parity.
- [x] Port Claude hooks and project MCP lifecycle with the same parity requirements.
- [x] Prove planning, validation, status, and installation never execute hooks, start MCP servers, connect to networks, or grant trust implicitly.

### E. Remaining commands and generated artifacts

- [x] Port artifact inspection and registered target capability validation by behavior rather than Python module layout.
- [x] Port `validate` with identical diagnostics, ordering, exit codes, and malformed-input coverage.
- [x] Port deterministic `render` and `render --check`; keep every generated byte identical unless a reviewed migration is intentional.
- [ ] Port source `update`, including branch validation, source containment, Git failure behavior, and generated-output rollback.
- [ ] Port deterministic human-readable plan/status/install/uninstall output in addition to JSON.
- [ ] Match help, aliases, global-option placement, repeated options, omitted-versus-null behavior, and all supported command exit codes.

### F. Packaging, compatibility, and cutover

- [ ] Add clean-install and upgrade tests using ownership states produced by released Python behavior.
- [ ] Test relocation, interruption, and recovery across Python-to-Rust handoff boundaries.
- [ ] Run the full parity suite on macOS and Linux with supported filesystem primitives.
- [ ] Build release binaries and provide one `agent-workbench` executable with documented install, upgrade, and uninstall paths.
- [ ] Preserve existing invocation compatibility or ship an explicit, tested wrapper during the transition.
- [ ] Record every intentional behavioral difference and its state/config migration before changing the default executable.
- [ ] Make Rust the default only after all supported commands, targets, states, generated artifacts, and recovery paths pass parity gates.
- [ ] Remove Python runtime code and compatibility scaffolding only after a clean-install and existing-state release candidate passes end to end.

## Feature #8 — Generated compatibility matrix

Recommended next product feature after the Rust conversion. Make the adapter capability model the authoritative source for both documentation and later discovery commands.

- [ ] Define a matrix record containing target, scope, component kind, accepted representation, activation route, trust requirement, install support, uninstall support, and diagnostic for unsupported combinations.
- [ ] Generate the matrix from registered target adapters rather than maintaining a second hand-written capability table.
- [ ] Represent project and user scope separately where their lifecycles differ.
- [ ] Keep unsupported combinations explicit; never omit them in a way that implies unknown support.
- [ ] Add deterministic JSON output for tooling.
- [ ] Generate a human-readable Markdown table for README or documentation use.
- [ ] Add a check mode that fails when committed generated output is stale.
- [ ] Test custom registered skill targets so the matrix does not reintroduce a closed global capability table.
- [ ] Use this matrix as the data source for Feature #5 `list` and `info` output.

## Feature #5 — Catalog discovery and health commands

- [ ] Add `list` output for packages, available components, compatible targets, and installation state.
- [ ] Add `info <package>` output showing source provenance, artifact representations, component counts, target compatibility, and trust requirements.
- [ ] Add a machine-readable `--json` form with a stable schema.
- [ ] Add filtering by target, component kind, source, installed state, and compatibility.
- [ ] Show why a target/component pair is unsupported using the same stable diagnostics as installation planning.
- [ ] Show whether executable content is trusted, untrusted, or stale without granting trust or executing anything.
- [ ] Add health/status checks for missing sources, stale generated files, modified owned copies, shared-slot drift, pending authenticated recovery, and broken skill links.
- [ ] Keep discovery inert: no hook execution, MCP startup, OAuth flow, network connection, or configuration mutation.
- [ ] Add deterministic snapshot tests and isolated status tests for installed, foreign, modified, and recoverable states.

## Feature #1 — Optional native target breadth

### Shared acceptance criteria for every new target

A target adapter is complete only when all applicable items below are satisfied.

- [ ] Accept only exact target-native representations; do not heuristically translate incompatible formats.
- [ ] Add target/scope/component entries to the generated compatibility matrix.
- [ ] Emit stable plan-time diagnostics for unsupported or lossy combinations before mutation.
- [ ] Copy executable payloads to immutable managed locations rather than activating mutable checkout paths.
- [ ] Require per-plugin, per-component, content-bound trust; changed content must invalidate prior trust.
- [ ] Own logical slots in shared configuration while preserving foreign fields, ordering, and entries.
- [ ] Coordinate writes with resource locks, stale-snapshot detection, descriptor-relative atomic replacement, and authenticated recovery evidence.
- [ ] Make reinstall idempotent and uninstall ownership-aware.
- [ ] Prevent managed payload deletion while any surviving foreign configuration references it.
- [ ] Exercise install, reinstall, conflict, recovery, and uninstall against isolated target roots.
- [ ] Prove installation does not execute hooks or start MCP servers.
- [ ] Run one focused security review and reproduce findings against the real implementation before fixing them.

### Codex

Official surfaces: project/user `hooks.json`, project/user `config.toml` MCP entries, Codex agent configuration, and native Agent Plugins.

- [ ] Add explicit `codex-hooks`, `codex-mcp`, `codex-agents`, and, if safely inspectable, `codex-plugin` representations.
- [ ] Activate project hooks through `.codex/hooks.json` with managed payloads under a stable `.codex/.agent-workbench-hooks/<plugin>/` location.
- [ ] Preserve Codex's native hook review while also enforcing agent-workbench content-bound trust.
- [ ] Add slot-level TOML ownership for project `[mcp_servers.<name>]` entries in `.codex/config.toml`.
- [ ] Add project MCP install/reinstall/uninstall and recovery tests.
- [ ] Add exact Codex agent declarations only when supplied in native TOML form; reject Markdown-agent conversion.
- [ ] Evaluate native Agent Plugin installation only if requested components, activation scope, and uninstall ownership can be inspected and constrained.
- [ ] Treat user-level hooks and `~/.codex/config.toml` MCP as separate global-scope adapters rather than silently widening project installation.

References:

- <https://developers.openai.com/codex/hooks>
- <https://developers.openai.com/codex/mcp>
- <https://developers.openai.com/plugins/build/plugins>

### Grok Build

Use native `.grok` resources even though Grok can consume some Claude and Cursor compatibility files.

- [ ] Add explicit `grok-hooks`, `grok-mcp`, `grok-agents`, and, if safely inspectable, `grok-plugin` representations.
- [ ] Activate project hooks as native `.grok/hooks/*.json` with managed payload copies.
- [ ] Add slot-level TOML ownership for project MCP entries in `.grok/config.toml`.
- [ ] Preserve Grok's project trust mechanism while independently enforcing agent-workbench content-bound trust.
- [ ] Support native Grok plugin installation only when enabled components and uninstall scope can be inspected exactly.
- [ ] Reject reliance on implicit Claude/Cursor compatibility as an ownership strategy.
- [ ] Add isolated lifecycle tests using `grok inspect` when available, without running installed hooks or connecting to MCP servers.

References:

- <https://docs.x.ai/build/features/hooks>
- <https://docs.x.ai/build/features/mcp-servers>
- <https://docs.x.ai/build/features/skills-plugins-marketplaces>

### Hermes

Hermes plugins, gateway event hooks, shell hooks, and MCP are separate native surfaces and are profile-scoped.

- [ ] Add explicit `hermes-plugin`, `hermes-event-hooks`, `hermes-shell-hooks`, and `hermes-mcp` representations.
- [ ] Install native Hermes plugins through an inspectable disabled-by-default lifecycle; enabling must remain explicit.
- [ ] Install gateway event-hook bundles as exact `HOOK.yaml` plus handler payloads with content-bound trust.
- [ ] Add owned shell-hook entries to the selected profile's `config.yaml`.
- [ ] Add owned `mcp_servers.<name>` entries to the selected profile's `config.yaml`.
- [ ] Make profile identity explicit in planning and state so one Hermes profile cannot modify another accidentally.
- [ ] Account for the gateway-wide effect of user/profile activation; do not present it as project-local behavior.
- [ ] Never convert Cursor or Claude hook JSON into Hermes Python, YAML, or plugin code.
- [ ] Use Hermes-native validation and isolated profile roots for lifecycle tests.

References:

- <https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins>
- <https://hermes-agent.nousresearch.com/docs/developer-guide/plugins>
- <https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks>
- <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp>

### Claude user-level MCP

Claude project MCP through `<project>/.mcp.json` is already supported. This adapter would separately own global entries in `~/.claude.json`.

- [ ] Model Claude user MCP as a distinct global scope with explicit plan output and confirmation.
- [ ] Add slot ownership for individual user-level MCP server names without owning the whole document.
- [ ] Preserve every unrelated top-level field and foreign server entry.
- [ ] Coordinate user-level locking independently from project locks.
- [ ] Add stale-document detection, atomic replacement, authenticated journaling, and interruption recovery.
- [ ] Refuse removal if ownership-ledger identity or expected value digest does not match.
- [ ] Add isolated fake-home install/reinstall/conflict/recovery/uninstall tests; never touch the real `~/.claude.json`.

### Generic Agent Skills target

- [ ] Keep the generic target skills-only unless a documented portable standard emerges for another component kind.
- [ ] Report hooks, MCP, agents, rules, and commands as explicitly unsupported rather than silently dropping them.

## Feature #6 — Copy and export modes

- [ ] Add an explicit copy install mode for containers, remote machines, and hosts that cannot follow repository symlinks.
- [ ] Record copied-resource digests and provenance in installation state.
- [ ] Detect locally modified owned copies and conflict instead of overwriting or deleting them.
- [ ] Make repeated copy installation converge without unnecessary rewrites.
- [ ] Add deterministic export of a resolved profile into a self-contained directory or archive.
- [ ] Include only selected packages, components, and exact target-native representations.
- [ ] Include a manifest containing source pins, digests, target/scope requirements, trust requirements, and unsupported exclusions.
- [ ] Reject unresolved plugin-root references, local-only paths, symlink escapes, and nonportable executable references before export.
- [ ] Detect likely embedded secrets and require environment-variable references rather than exporting credentials.
- [ ] Ensure export creation remains inert: do not execute hooks, start MCP servers, or grant trust.
- [ ] Validate exports from a clean temporary consumer root, including install, reinstall, conflict, and uninstall.
- [ ] Verify byte-for-byte deterministic output for identical inputs.

## Suggested implementation order

1. [ ] Feature #9 migration gate: prove the Rust vertical slice against Python behavior.
2. [ ] Feature #9 full conversion, parity verification, and Python removal.
3. [ ] Feature #8: generated compatibility matrix.
4. [ ] Feature #5: `list`, `info`, and inert health/status commands.
5. [ ] Codex project hooks and MCP.
6. [ ] Grok project hooks and MCP.
7. [ ] Hermes native plugins, hooks, and MCP.
8. [ ] Claude user-level MCP.
9. [ ] Feature #6: copy and export modes.
10. [ ] Reassess user-level Codex/Grok activation and native-package breadth using the completed project adapters as evidence.
