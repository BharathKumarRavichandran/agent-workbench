# Design: Personal Multi-Agent Plugin Marketplace

## Goals

1. Keep one auditable repository for personal and work-safe agent capabilities.
2. Serve native plugin marketplaces where an agent supports them.
3. Serve plain `SKILL.md` directories where an agent only supports filesystem discovery.
4. Track large upstream collections without copying or rewriting them.
5. Allow selected skills to be copied locally when independent curation is more useful than upstream coupling.
6. Make updates explicit, reviewable, and reproducible.

## Non-goals

- A universal runtime API across every agent.
- Automatic execution of untrusted upstream hooks or MCP servers.
- Silent tracking of upstream `main` in production environments.
- Storing credentials, employer-only content, or machine-specific configuration.

## Core model

The repository separates four concerns:

```text
catalog -> package sources -> agent adapters -> installations
```

### 1. Catalog

`catalog/plugins.json` is the small source of truth. It records:

- stable plugin id;
- ownership/update model;
- upstream provenance;
- package root for Cursor;
- package root for Claude-compatible consumers;
- generic skills root.

It does not duplicate skill bodies or plugin versions. Those remain in each package's own manifest.

### 2. Package sources

There are three ownership modes:

| Mode | Location | Use when |
|---|---|---|
| `local` | `plugins/<id>` | You author and release the plugin. |
| `copied` | `plugins/<id>` | You intentionally curate a small upstream artifact and retain attribution. |
| `submodule` | `vendor/<repo>` | You want an auditable upstream commit and recursive checkout. |

`pstack` is a directory inside the `cursor/plugins` monorepo, not an independent Git repository. Git cannot make only that directory a submodule. Therefore the correct boundary is:

```text
vendor/cursor-plugins                 # the actual submodule
vendor/cursor-plugins/pstack          # Cursor package root
vendor/cursor-plugins/harness/pstack/claude-code
                                       # Claude package root
```

This preserves upstream history and requires no fork. The cost is checking out the upstream monorepo rather than only the pstack directory.

`karpathy-guidelines` uses the copied mode because it is one small skill and the requirement is to avoid a second submodule. Its upstream URL and MIT declaration are retained in `NOTICE.md`.

### 3. Agent adapters

Adapters are intentionally thin.

#### Cursor

The generated `.cursor-plugin/marketplace.json` points each catalog entry to its Cursor package root. Package-owned `.cursor-plugin/plugin.json` files remain authoritative.

#### Claude Code

The generated `.claude-plugin/marketplace.json` points each entry to its Claude package root. pstack uses the Claude harness shipped by the upstream repository rather than pretending the Cursor package is identical.

#### Grok

Grok consumes Claude-compatible plugin marketplaces and also discovers skills from `.agents/skills` and `.grok/skills`. It therefore needs no forked manifest format. Use the Claude marketplace for complete plugins or `install_skills.py --target grok` for skill-only installation.

#### Codex

Codex discovers skills from its skills directory but does not need this repository to invent a second plugin format. `install_skills.py --target codex` links each standard skill into `$CODEX_HOME/skills` (default `~/.codex/skills`).

#### Hermes and other agents

Hermes receives links under `$HERMES_HOME/skills`. Agents that implement the shared Agent Skills convention can use `--target agents`, which installs into `~/.agents/skills`.

### 4. Installations

The repository remains canonical. User-level skill directories receive relative symlinks, not copies. This has three properties:

- one update changes every linked local agent;
- removing the checkout makes breakage obvious instead of leaving stale copies;
- the installer can identify links it owns and refuse destructive overwrites.

Cloud agents should install through the Git marketplace URL because local symlinks do not exist in their containers.

## Update semantics

There are two different operations that are easy to confuse:

1. `git pull --recurse-submodules` checks out the submodule commit pinned by this repository.
2. `git submodule update --remote --recursive` advances the submodule to the newest commit on its configured branch.

`make update` performs the second operation, regenerates manifests, and validates the result. The changed submodule pointer must then be reviewed and committed. This gives work and personal environments the same reproducible version while retaining a one-command upgrade path.

## Trust and security boundary

Treat every plugin as executable supply-chain input, even when it currently contains only Markdown.

- Pin every submodule through the superproject commit.
- Review `git diff --submodule=log` before accepting updates.
- Validate license and provenance for copied content.
- Never commit secrets, OAuth tokens, private MCP URLs, or employer instructions.
- Keep company-only plugins in a separate private overlay repository rather than a branch of the personal marketplace.
- Prefer skills-only installation when a full plugin contains hooks, scripts, MCP configuration, or tools that are unnecessary for the target agent.

## Public core and private overlays

Recommended deployment:

```text
agent-plugins                 public or personal core
agent-plugins-work-overlay    private company-specific marketplace
```

The work overlay may include this repository as a submodule and add internal plugins. Do not make the core repository depend on the work overlay. This keeps personal clones usable and prevents accidental publication of internal material.

## Versioning

- The superproject commit is the release identifier for the full marketplace.
- Local plugins own their semantic versions in `plugin.json`.
- Submodule plugins keep their upstream versions.
- Tag stable marketplace snapshots, for example `marketplace-2026.10` or `v0.1.0`.
- Update one upstream at a time when possible so reviews show a narrow submodule diff.

## Validation contract

A valid catalog must satisfy all of the following:

- plugin ids are unique;
- every Cursor and Claude package root has its native plugin manifest;
- every generic skill directory contains child directories with `SKILL.md`;
- each skill has frontmatter containing `name` and `description`;
- skill names are globally unique across the selected catalog;
- generated marketplace files exactly match the catalog.

`scripts/validate.py` enforces this initial contract. Add agent-native validators later as the repository grows.

## Next extensions

Add these only when needed:

1. CI running `python3 scripts/validate.py` on pull requests.
2. A lock/report file recording submodule commit, plugin version, and skill count.
3. Per-environment profiles such as `profiles/minimal.json` and `profiles/work.json`.
4. Signature or allow-list policy for plugins containing executable hooks.
5. Automated upstream update pull requests with human review.

The initial design stays small: one catalog, two generated marketplaces, one submodule, one copied skill, and one safe linker.
