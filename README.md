# Agent Plugins

A personal, multi-agent marketplace for reusable plugins and skills.

The repository keeps one catalog and emits native Cursor and Claude-compatible marketplace manifests. Claude-compatible marketplaces are also consumable by Grok, while filesystem skill links cover Codex, Hermes, Grok, Claude, and agents that use the common `~/.agents/skills` location.

## Included

| Plugin | Ownership | Update model |
|---|---|---|
| `pstack` | Cursor/Lauren Tan | `vendor/cursor-plugins` git submodule |
| `karpathy-guidelines` | Forrest Chang / multica-ai | copied skill with attribution |

## Clone

```bash
git clone --recurse-submodules <your-repository-url>
cd agent-plugins
python3 scripts/validate.py
```

A normal recursive clone checks out the **pinned** upstream commit. To deliberately advance pstack to the latest `main` commit:

```bash
make update
git diff --submodule=log
# Review, then commit the new submodule pointer and generated files.
```

This review step is intentional: upstream changes should not silently reach work or personal agents without a committed, auditable pin.

## Install

### Native marketplaces

Publish this repository to GitHub, then add it as a marketplace in Cursor, Claude Code, or Grok. The native manifests are:

- `.cursor-plugin/marketplace.json`
- `.claude-plugin/marketplace.json`

Grok supports Claude-compatible plugin marketplaces, so it uses the Claude manifest.

### Filesystem skills

Preview links before installing:

```bash
python3 scripts/install_skills.py --target codex --dry-run
python3 scripts/install_skills.py --target grok --dry-run
python3 scripts/install_skills.py --target hermes --dry-run
python3 scripts/install_skills.py --target agents --dry-run
```

Install one plugin or all plugins:

```bash
python3 scripts/install_skills.py --target codex --plugin karpathy-guidelines
python3 scripts/install_skills.py --target codex --plugin pstack
python3 scripts/install_skills.py --target agents
```

The installer creates relative symlinks and refuses to overwrite existing files or foreign links.

## Repository layout

```text
agent-plugins/
├── catalog/
│   ├── plugins.json              # canonical inventory and adapter paths
│   └── schema.json
├── plugins/
│   └── karpathy-guidelines/      # owned/copied package
├── vendor/
│   └── cursor-plugins/           # upstream submodule; pstack lives here
├── .cursor-plugin/
│   └── marketplace.json          # generated
├── .claude-plugin/
│   └── marketplace.json          # generated; also used by Grok
└── scripts/
    ├── render_marketplaces.py
    ├── install_skills.py
    ├── update.py
    └── validate.py
```

## Add another plugin

1. Put an owned or copied plugin under `plugins/<id>`, or add its repository under `vendor/` as a submodule.
2. Ensure it has Cursor and Claude package roots plus a directory of standard `*/SKILL.md` skills.
3. Add one entry to `catalog/plugins.json`.
4. Run `make render validate`.
5. Review attribution, license, and generated manifests before committing.

See [DESIGN.md](DESIGN.md) for the architecture and extension rules.
