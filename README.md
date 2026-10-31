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
    ├── skill-directory targets
    ├── profile resolution
    ├── layered validation
    └── install planner
             ↓
      explicit operations
             ↓
       safe executor + state
```

The central catalog describes sources and artifacts such as `cursor-plugin`, `claude-plugin`, and `agent-skills`. Agent adapters consume those artifacts. Adding another skill-directory agent does not require changing every plugin.

## Clone and verify

```bash
git clone --recurse-submodules <repository-url> agent-workbench
cd agent-workbench
make check
```

The runtime uses only the Python standard library and supports Python 3.9 or newer.

## Commands

```bash
# Validate the catalog, manifests, skills, lock report, and generated files.
python3 -m agent_workbench validate

# Render native marketplaces and catalog/lock.json.
python3 -m agent_workbench render
python3 -m agent_workbench render --check

# Preview an installation without changing the machine.
python3 -m agent_workbench --profile pstack plan --target hermes

# Apply it. Existing exact links require explicit adoption once.
python3 -m agent_workbench --profile pstack install --target hermes --adopt

# Inspect current desired-versus-actual state.
python3 -m agent_workbench --profile pstack status --target hermes

# Remove only paths recorded as owned by this workbench.
python3 -m agent_workbench --profile pstack uninstall --target hermes

# Advance the reviewed upstream source, then render and validate.
python3 -m agent_workbench update --source cursor-plugins
```

Use `--destination` and `--state-dir` for isolated, project-local, or CI installations.

## Profiles

- `default`: all plugins;
- `pstack`: pstack only;
- `minimal`: Karpathy guidelines only.

Profiles select plugins and allowed targets. They cannot change source provenance or artifact paths.

## Safety behavior

The installer builds a complete plan before writing anything.

- Regular files and foreign symlinks are conflicts.
- Matching unmanaged links require `--adopt`.
- Repeated installation converges to `keep` operations.
- State writes are atomic and guarded by an operation lock; stale plans are rejected against the full ownership snapshot.
- Destination roots are created and mutated through pinned directory descriptors, preventing parent/root symlink swaps from redirecting writes.
- Uninstall removes only state-owned links that still point to the expected source.
- Moved checkouts produce `replace-owned` operations rather than silent deletion.

Machine-local ownership state is stored under:

```text
${XDG_STATE_HOME:-~/.local/state}/agent-workbench/<marketplace>/<profile>/<target>.json
```

## Generated outputs

- `.cursor-plugin/marketplace.json`
- `.claude-plugin/marketplace.json`
- `catalog/lock.json`

The lock report records source revisions, plugin versions, artifact roots, and skill counts. All generated files are deterministic and checked in CI.

## Repository layout

```text
agent-workbench/
├── agent_workbench/           # typed runtime and CLI
│   ├── adapters/              # native renderers and install targets
│   ├── codec.py               # strict JSON boundary
│   ├── model.py               # immutable domain values
│   ├── installation.py        # plan, reconcile, apply, uninstall
│   ├── state.py               # atomic ownership state
│   └── validation.py
├── catalog/                   # canonical catalog, schema, lock report
├── profiles/                  # package and target selections
├── plugins/                   # locally owned or curated packages
├── vendor/                    # pinned upstream sources
├── tests/                     # isolated behavior tests
└── scripts/                   # compatibility wrappers
```

## Adding a plugin

1. Add or reuse a source in `catalog/plugins.json`.
2. Add the plugin with one or more artifact roots.
3. Preserve provenance and a notice for curated copies.
4. Add it to the appropriate profiles.
5. Run `make render check`.

## Adding an agent

If the agent consumes standard skill directories, register another `SkillDirectoryTarget` with its destination. No plugin entries change.

If it needs a native marketplace format, add one narrow renderer implementing the marketplace adapter contract, register it once, and add golden behavior tests.

See [DESIGN.md](DESIGN.md) for contracts and design decisions. Development and commit-message rules are in [CONTRIBUTING.md](CONTRIBUTING.md).
