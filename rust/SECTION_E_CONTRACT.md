# Section E1 — inert inspection and registered capabilities

Python production remains frozen. This unit does not enable public `validate`,
`render`, `update`, human output, general parser parity, or new native targets.
The transaction exceptions named E1/E2 in `PARITY_EXCEPTIONS.md` are unrelated
identifiers and remain unchanged.

## Implementation boundary

- `artifacts.rs` inspects skills, Markdown agents/commands, Cursor rules, both
  hook representations and aliases, MCP documents and opaque native packages.
  Catalog inspection independently decodes the real catalog, preserves artifact
  insertion order, accumulates independent errors, and counts nonempty inventories.
- `capabilities.rs` carries the effective capabilities on a registered target.
  Built-ins and custom skills-only registrations use the same projection logic;
  an explicit capability override replaces rather than supplements the built-in
  map. Semantic exports retain exact native representation and source filename.
- Existing lifecycle commands inspect requested, supported, rooted artifacts in
  **plugin order, then requested-component order**, after pending recovery and
  before ownership-state loading. Hook command/MCP payload compilation stays
  after state authorization. Unsupported/unrooted selections bypass inspection.
- Public lifecycle capability diagnostics use the same registered projection
  implementation exercised by the private custom-registration driver. No Python
  plans, inventories, operations or results are inputs to Rust planning.

## Acceptance-to-test map

Both scripts run unconditionally from `scripts/check_rust_plan_parity.py` and
therefore its existing CI job. All fixtures use explicit scratch `TMPDIR`.

| Acceptance | Runnable evidence |
| --- | --- |
| First real public inventory-before-state tracer | `check_artifact_inspection_parity.py inventory-before-state`; real plan/trust/status/install, not a compiler failure |
| Cursor and Claude requested inventory errors before invalid old state | `inventory-before-state`, `skills-before-state`, `request-order`, plus Cursor rules / Claude commands rows |
| Plugin order before component order | `plugin-request-order@cursor` / `@claude`, two selected plugins with disagreeing defects |
| Later hook/MCP compilation remains after state authorization | `late-hook-state`, `late-mcp-state@cursor` / `@claude` |
| Exact component-name diagnostics, required metadata, lexical file selection | `agent-name`, `agent-mismatch`, `agent-description`, `document-order`, both native targets |
| Missing optional skills artifact is not an error | `skills-absent`, both targets, including real install |
| Unsupported built-in capability cells inspect nothing and mutate nothing | `unsupported-{agents,codex,grok,hermes}` through four real public commands |
| Unsupported diagnostics precede missing-root diagnostics | `capability-order`; `skip-no-root` also proves malformed rejected artifacts are not read |
| Disagreeing profile and CLI component/project-root overrides affect real paths and inspection | `profile-component-override` / `profile-root-override`, both targets through install; inherited activation override lifecycle remains mandatory |
| Nonobject hook/MCP JSON and truncated MCP value | `hook-array`, `mcp-array`, `mcp-syntax`, both targets; exact stdout/stderr/status and typed snapshots |
| All built-in inert inventories, native representations, component names/relative paths | `check_artifact_inventory.py valid`; private ignored `artifact_parity_tests::differential_inspection` |
| Skill-root children and SKILL.md symlinks have distinct diagnostics; unselected non-skill subtrees are ignored | `skill-directory-link`, `skill-manifest-link`, `skills-ignored-tree` |
| Tree symlinks, wrong root type, duplicate keys and nonobject JSON | `document-link`, `document-root-file`, `mcp-array`, `mcp-duplicate`, `hook-duplicate`, `hook-array` |
| Skill metadata diagnostics, universal newlines, quoted-name rejection and folded regex whitespace | `skill-missing-name`, `skill-unsafe-name`, `skill-crlf`, `skill-quotes`, `skill-missing-description`, `skill-folded` |
| Catalog-wide source/artifact errors, error accumulation and insertion order, inventories/counts | `combined-order`, `missing-artifact`, `unknown-kind`, `extra-source`; catalog is independently loaded by Rust |
| Six built-in registrations, custom skills-only default, explicit replacement of Cursor capabilities, rooted/unrooted projections, exact representation conflicts and path keys | Every private inventory case runs the same effective-capability matrix; Rust constructs independent semantic exports from its own catalog inspection |
| Read-only inventory is inert | Whole-fixture typed bytes/modes/lexical-link snapshots before/after every private inspection; public rejection snapshots; unchanged D8 executable/loopback tripwires |
| Source hook/MCP JSON uses strict UTF-8 text, not shared-document byte-encoding acceptance | `mcp-bom`, `mcp-utf16`, `hook-bom`, `hook-utf16` in both scripts; exact rejection diagnostics, both public targets; private `mcp-utf8-truncated` / `mcp-utf8-continuation` pin UTF-8 error spans |
| Python splitlines frontmatter separators | `unicode-frontmatter` in both scripts; U+2028 separates agent name/description and real public install succeeds |
| Non-directory artifact ancestor accumulates missing-path issue without dropping valid inventories | Private `catalog-parent-file`; exact catalog issues, remaining inventories/counts and capability projections |
| Existing safety and lifecycle preservation | Full parity gate retains six skills lifecycles, copied paths/races/recovery, Cursor/Claude activation, authenticated recovery and both authorized transaction exceptions |

## Observed RED → GREEN

The first tracer reached actual public behavior: malformed requested agent
frontmatter plus invalid state produced Python's `frontmatter must start at byte
zero` but Rust's lower-priority `unsupported installation state`. It passed after
moving inert requested inventory to the correct boundary. Rebuilding frozen
`4145e640f62d21b96f827cddec5fe4bbaad91fcf` offline in scratch and replaying the
same test reproduces that RED (`AW_E1_RUST_BINARY` is a test-only selector).

Further behavioral REDs captured missing optional skills being rejected,
unsupported executable-component selections failing in the Rust parser rather
than producing capability conflicts, omitted name/path diagnostic details,
component-before-plugin validation order, root/unsupported conflict ordering,
artifact-vs-skill symlink diagnostics, nonobject JSON diagnostics, CRLF/folded
skill metadata and quote handling. These have permanent differential rows.
Private API construction also had ordinary compile failures; those are not
claimed as behavioral RED evidence.

Independent review corrections were reproduced before editing production code,
then pinned as failing permanent differentials: a regular-file artifact ancestor
aborted Rust catalog inspection with ENOTDIR while Python retained seven valid
inventories; BOM/UTF-16 hook/MCP sources were accepted by Rust but rejected by
Python's strict UTF-8 reader; U+2028 between name and description was accepted
by Python but rejected by Rust. Each row passed after its narrow correction.
Shared-document byte decoding and installation path authorization are unchanged.

## Verification and bounded remaining evidence

All five mandatory local gates passed after the review corrections: format,
all-target Clippy with warnings denied, Cargo tests, complete parity (including
D8, both transaction safety exceptions, and 196 E1 public paired rows), and all
188 Python tests. Independent correction recheck reproduced the three original
findings as GREEN and found no remaining blocker. No declared external endpoint
or installed host resource was touched. Exact-SHA Ubuntu CI remains the delivery
gate before E2 begins.

The private API contract is distinct from public `validate` (E2). Generalized
JSON syntax diagnostic/encoding exhaustiveness is not claimed here: the E1
matrix pins nonobjects, duplicates and EOF-value diagnostics, not every possible
malformed JSON lexeme. Custom registration evidence covers effective capability
validation/projection, not a newly added custom-target public CLI or native
activation lifecycle. Ubuntu evidence requires CI for the eventual exact SHA;
local macOS success is not substituted for it.
