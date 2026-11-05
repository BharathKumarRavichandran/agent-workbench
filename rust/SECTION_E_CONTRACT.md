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
or installed host resource was touched. E1 commit
`d1ca24b676cf854a83760ad004ad094cc9f2b269` passed all Ubuntu jobs in
[run 41876226575](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41876226575)
before E2 began (exact-SHA result freshly confirmed by the parent). The initial
attempt failed an existing quarantine-lock test; the rerun passed at the same
SHA. Its root cause is unconfirmed; no fix is claimed.

The private API contract is distinct from public `validate` (E2). Generalized
JSON syntax diagnostic/encoding exhaustiveness is not claimed here: the E1
matrix pins nonobjects, duplicates and EOF-value diagnostics, not every possible
malformed JSON lexeme. Custom registration evidence covers effective capability
validation/projection, not a newly added custom-target public CLI or native
activation lifecycle. Ubuntu evidence requires CI for the eventual exact SHA;
local macOS success is not substituted for it.

# Section E2 — public validate implementation checkpoint (review pending)

Python production is unchanged. `validation.rs` handles real public `validate`,
`--json`, and `--no-generated-check`; `validation_codec.rs` independently models
ordered catalog/profile decoding and workspace resolution. The validation-only
`validation_json.rs` boundary retains Python code points (including lone
surrogates), arbitrary integers, binary64 floats and nonfinite constants; it
does not widen lifecycle/shared-document decoding. The existing E1
inspector is shared through `inspect_decoded`, without importing Python data.
Transaction exceptions E1/E2 are unrelated and untouched.

The read-only deterministic renderer is an **E2 dependency**: Cursor and Claude
marketplace JSON and the catalog lock are computed internally to check canonical
default-profile bytes. Public `render`/`render --check` remain unsupported (E3);
there is no generated-output write implementation here. Update, lifecycle human
output, broad parser/help parity and later units remain unimplemented.

## Acceptance-to-test map

`check_validate_parity.py` runs unconditionally in `check_rust_plan_parity.py`.
Every paired public row compares stdout bytes, stderr bytes, exit status, and
whole-fixture file type/mode/content/link/inode/mtime/ctime before and after.
Fixtures and subprocess temporary files require explicit scratch TMPDIR.

| E2 acceptance edge | Executable evidence |
| --- | --- |
| First actual CLI tracer, no generated check | Empty workspace JSON: Python `[]`/0 versus Rust unsupported command/2 RED, then GREEN before expansion |
| Human no-components summary, duplicate/unknown profile-target order, repeated unknown targets | Public tracer group, both human and JSON output |
| Inspection then provenance then renderer then duplicate-skill diagnostics; Unicode JSON escaping | Combined native/skills/unknown-artifact fixture, including duplicate unknown targets and Unicode skill names |
| Full catalog decode before profile, full profile before identity resolution, artifacts before plugin identity | `malformed`: 109 paired omitted/null/wrong-type/unknown/combined-defect rows |
| Source/plugin duplicates, unknown include/exclude sorting, selected missing sources/roots | Same malformed rows through the real public CLI |
| Nested duplicate-key hook precedence and exact Python 3.12 syntax diagnostic coordinates | `wire_rows`: 44 CLI rows; `wire_boundary_rows`: 160 rows through catalog, profile, native manifest, hooks and MCP readers |
| Canonical profile selection despite a subset profile; no-generated-check skips canonical loading | `generated_rows`, including malformed default profile with valid selected subset |
| All three generated outputs, missing/stale/current files, directory and symlink outputs | `generated_rows`; current bytes are oracle-written fixture files, independently recomputed by Rust |
| Native manifest fallback vs required native validation, name/description validation, malformed/duplicate manifests | `generated_rows` and `filesystem_rows` |
| Lock version truthiness, ordered foreign version maps, floats and huge integers | Six generated-version rows; Python-compatible existing shared-document formatter reused after a real stale-lock RED |
| Missing input files, invalid UTF-8/UTF-16 rejection, invalid/mismatched/escaping profile identifiers, symlink manifest rejection | `filesystem_rows`; actual UTF-8 and manifest-link REDs corrected |
| Unselected unknown-source plugins do not receive resolved-plugin provenance diagnostics | Final filesystem row: concrete extra-provenance RED corrected |
| Public validation never executes declared hooks/MCP and creates no state/trust | Existing D8 executable/loopback positive controls and private native validator retained; public no-generated-check validation now exercised in both engines |
| Existing lifecycle dispatch cannot mistake an option value for the validate command | Cargo test `validation::tests::validate_as_an_option_value_is_not_a_command`, observed RED then GREEN |
| All six skills, copied paths, native activation, transaction races/recovery/exceptions | Explicit Python 3.12 full parity gate passed again with all 549 E2 rows; inherited 170-row evidence is retained historically |

## Approved oracle and exception contracts

The user approved **Python 3.12 as the exact diagnostic oracle**. Both the full
parity gate and E2 entry point call `scripts/parity_oracle.py` before fixtures or
builds; the gate also invokes it as a script. It rejects a top-level interpreter
other than 3.12 and separately verifies that subprocess `python3` on PATH is
3.12. No diagnostics are normalized between Python releases. On this host the
actual Python 3.14.8 entry point and the combination of top-level 3.12.15 with
PATH `python3` 3.14 were each rejected; the fully pinned 3.12.15 path passed.
CI already selects 3.12. A reproducible local invocation is:

```sh
TMPDIR=/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch \
PATH=/opt/homebrew/opt/python@3.12/libexec/bin:$PATH \
/opt/homebrew/bin/python3.12 scripts/check_rust_plan_parity.py
```

The user separately approved **VAL-GIT-1**, documented in
`PARITY_EXCEPTIONS.md`: unsuccessful Git revision lookup during validation
retains real Git stderr bytes and exit 1, omits only Python's uncaught traceback,
and emits no JSON or human report. This is not a `RENDER_FAILED` issue, broad
error normalization, a production-oracle edit, or either transaction E1/E2.

## Additional acceptance-to-test map

| E2 completion edge | Executable evidence |
| --- | --- |
| Uncaught Git revision error, including pre-existing issues and both output modes | `git_revision_rows`: eight actual paired exception rows, two real Git failure classes; one ordinary no-generated-check bypass row |
| Nonfinite constants and exponent overflow/underflow, leading zeros, incomplete fraction/exponent, invalid literals, truncated arrays, malformed escapes and control characters | `wire_rows` plus all five real reader boundaries in `wire_boundary_rows`; exact bytes, coordinates and statuses, not an accept/reject-only check |
| Ignored catalog/profile `$schema` and foreign native-manifest fields retain permissive JSON acceptance | `json_field_rows`, NaN/±Infinity, high/low lone surrogates, separated surrogates, surrogate object keys with nested values, valid surrogate pairs |
| Manifest version preserves nonfinite values in canonical lock bytes; malformed name/description types reject at the right boundary | Same rows check independently recomputed current/stale lock bytes, both generated and no-generated-check modes; lexical `1e999` version row |
| Accepted lone-surrogate names/descriptions are not scalar-decoder failures | Same rows: catalog descriptive fields validate without generation; generated UTF-8 encoding failures retain exact Python diagnostic positions; manifest name mismatch remains distinct from manifest shape errors |
| Hook/MCP foreign values, command fields, event/server names and invalid version values remain inert | `artifact_json_rows`: 60 paired rows; E2 lossless loader is injected only into readonly inspection; ordinary lifecycle inspection/compilation is unchanged |
| Whole-fixture immutability | Every paired E2 row checks types, modes, bytes, literal links, inodes, mtime and ctime; the eight exception rows independently assert the authorized outcomes |
| Source and provenance upstream parsing matches Python 3.12 without rewriting stored strings | `upstream_rows`: 182 public pairs, including scheme case, leading C0/space, embedded tab/CR/LF, eager bracket/IPv6/IPvFuture checks, NFKC delimiter rejection, exact SCP fullmatch, combined-defect precedence, both output modes and oracle-written current generated bytes |
| Descriptive NUL rejection precedes scalar projection and later catalog defects | `descriptive_nul_rows`: 84 public pairs across marketplace name/description/owner.name, NUL before/after lone surrogates, ordinary NUL, accepted high/low lone surrogates, generated/no-generated modes and combined source defects |

## Checkpoint evidence and remaining delivery work

The inherited 170-row checkpoint was preserved before editing in a recoverable
scratch snapshot containing tracked binary diffs, every dirty tracked/untracked
file, and a mode/SHA-256 manifest. The completed focused matrix now passes
**549 paired public CLI rows**, including the eight explicit VAL-GIT-1 exception
rows. Real behavioral REDs preceded correction: structured Git reports instead
of raw stderr; rejection of nonfinite values and lone-surrogate strings at the
JSON boundary; wrong malformed-number/escape diagnostics; valid ignored native
fields incorrectly making manifests invalid; and hook/MCP foreign values being
rejected by the narrower shared-document decoder. None is claimed as exhaustive
fuzz evidence. The native/shared-document/lifecycle parsers remain independent.

The finite matrix pins the named accepted and rejected fields/lexemes above;
it does not claim arbitrary malformed-input, recursion-limit, interpreter
configuration, filesystem-surrogate-path, or scheduling exhaustiveness. The
read-only renderer remains an E2 dependency only. Public render and render
--check are still E3, and update/general parser/help migration is not enabled.

All five local macOS gates passed at this stable implementation checkpoint:
format check, warnings-denied all-target Clippy, Cargo tests (16 library and
15 integration passed; 17 private drivers intentionally exercised by parity),
full explicit Python 3.12 parity, and all 188 Python unittest tests. D8's real
canaries and both native targets, all six skills lifecycles, copied/race/recovery
suites and both transaction exceptions passed in that complete gate. The
repository's CI unittest command passed without installing test dependencies.
No new unsafe oracle behavior was observed.

Focused review identified two reproducible parity blockers; both were corrected
and independently rechecked. All five parent mandatory local gates passed after
the corrections, including 815 public validation pairs and 188 Python tests.
E2 is locally accepted; exact-SHA Ubuntu verification remains required before E3.
Python production remains unchanged.

### Independent E2 review corrections (stable, uncommitted)

Both review blockers were reproduced by permanent public differential rows before
production edits. `upstream_rows` observed actual acceptance/diagnostic drift for
uppercase HTTPS, leading C0/space, embedded scheme controls, malformed brackets
and SCP LF paths; its full pre-fix failure log is retained in scratch as
`e2-upstream-red.log`. `descriptive_nul_rows` separately observed Python's
non-empty-string rejection versus Rust acceptance/later errors for surrogate+NUL
in name, description and sibling owner.name (`e2-descriptive-red.log`).

The correction traces frozen `codec.py::_upstream` and both source/provenance
callers, matching the relevant Python 3.12 `urlsplit` eager validation while
retaining original strings. It checks descriptive code-point NUL before scalar
projection and continues accepting otherwise valid lone surrogates. The complete
E2 matrix now passes **815 paired public CLI rows** (182 upstream + 84 descriptive
review rows added). All original independent-review reproductions are GREEN.
Format, warnings-denied all-target Clippy and Cargo tests also pass. The parent
will recheck the corrections and rerun all five mandatory gates at this stable
checkpoint; no new full-gate or Ubuntu result is claimed here. Public render E3,
Python production, VAL-GIT-1 and transaction exceptions remain unchanged.

Pre-edit dirty tracked/untracked files, modes, SHA-256 manifest and binary diff
are preserved in scratch `e2-blocker-checkpoint-20261104-185613`. No commit,
TODO acceptance change or push was performed.

# Section E3 — partial public rendering checkpoint

E2 implementation `101a05f7a49315e07898a3e57d421b0164212d87` and its CI
correction `4254c250bbba1b55d5b55d90589f03685bae01ba` passed every Ubuntu job
in [run 41961140695](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41961140695)
before E3 began. The corrected workflow pins Ubuntu 24.04 (Python 3.9 is not
available to setup-python on Ubuntu 26.04) and explicitly checks out the exact
PR head. Checkout logs confirm `4254c25`, not a synthetic merge SHA.

The first real public tracer observed Rust's unsupported-render exit 2 against
Python's missing-output check/write behavior, then reached four-row GREEN.
`rendering.rs` reuses E2's independently computed generated bytes and publishes
through pinned parent descriptors with a synced 0600 exclusive temporary and
atomic rename. No-op output retains bytes, modes and filesystem identity.

| E3 checkpoint edge | Executable evidence |
| --- | --- |
| Missing/current generated files, write/check/repeated no-op | `check_render_parity.py empty`, four actual public rows |
| Native manifests, Unicode, ordered foreign versions and large numbers | `native`, four rows; generated bytes and typed filesystem snapshot |
| Stale output modes replaced with 0600 | `stale-modes`, four rows |
| Nondefault canonical-profile refusal and malformed catalog | `nondefault`, `malformed-catalog` |
| Directory output, non-directory parent, output/parent lexical symlink rejection and literal partial-write residue | `output-directory`, `parent-file`, `output-symlink`, `parent-symlink` |
| Check/no-op immutability | Exact inode/mtime/ctime snapshots within each engine; bytes/types/modes/lexical links across engines |

The matrix now contains 20 named fixtures: **80 render pairs plus four public
validation pairs** retaining E2 generated-check/no-generated-check behavior.
It runs unconditionally from the main parity gate. The separate private failure
matrix adds seven paired syscall-boundary rows. Final independent correction
recheck, full mandatory gates and exact-SHA Ubuntu verification remain pending;
no E3 checkbox or commit has been made.

## Authorized Git exception (resolved)

The user subsequently authorized RENDER-GIT-1: the same raw-Git-stderr/exit-1/
no-traceback contract now covers both public rendering modes. The permanent
`git-nonrepository` and `git-no-head` rows observed RED before the authorized
error-channel change and now pass. No Python traceback is synthesized and
Python production remains unchanged. The matrix also adds a real successful
local Git revision and mixed six-component inventory. Fixture Git index bytes
embed setup inode/timestamps; the test reuses only the initial fixture index
bytes at the same lexical path, not renderer output. Every command's full tree
remains captured, and check/no-op identity is asserted without filtering.

All named rows run in the full parity gate. Independent review and final
mandatory gates/acceptance/commit/exact-SHA Ubuntu remain pending. E4–E6 have
not started.

## Independent E3 review corrections (stable, uncommitted)

Before edits, all inherited tracked/untracked dirty files were preserved with a
binary diff and mode/SHA-256 manifest in scratch
`e3-review-snapshot-q2ii5yy9`.

Permanent public rows reproduced both review blockers before production changes:
`catalog-parent-symlink` observed Python's preflight exit 2 with no mutations
versus Rust's stale/check exit 1 and two published marketplaces before write
failure; `readonly-root` observed Python's operand-bearing `[Errno 13] Permission
denied: '.cursor-plugin'` versus Rust's native generic diagnostic. The lock now
checks every lexical output component at its existing generation boundary,
matching marketplaces and preserving E2's shared generated/no-generated checks.
Publication errors now retain Python's syscall operand rules: root/component or
temporary filename for open/mkdir/stat, no filename for write/fsync, and both
source/destination operands for rename.

| Review edge | Permanent evidence |
| --- | --- |
| Catalog lexical parent and lock-file symlinks reject before any output | `catalog-parent-symlink`, `catalog-lock-symlink`; each includes four render modes plus two public validation modes |
| Nonroot read-only repository and both native output parents | `readonly-root`, `readonly-cursor-parent`, `readonly-claude-parent`; chmod restoration runs in fixture cleanup even on failure |
| Sibling Cursor non-directory/symlink parents retain preflight/error ordering | `cursor-parent-file`, `cursor-parent-symlink` alongside existing Claude cases |
| Deterministic mkdir, root-open, parent-open, temporary-open, write, fsync and rename failures | `check_render_failures.py`, seven private pairs; ignored Rust `rendering::parity_tests::differential_failure` accepts fixture/fault names only, runs the actual writer, and requires independent fault hits in both engines |
| Failure diagnostics and residue | Exact errno/operands and bytes/types/modes/links compared after every private failure; existing destination survives failed rename, failed temporaries are removed |

The private driver first failed behaviorally because Rust publication succeeded
without reaching the requested mkdir fault. No public fault option was added.
Only fresh temporary filename nonces are normalized, after asserting the exact
16-lowercase-hex shape; all deterministic diagnostic text remains exact.
Python production, RENDER-GIT-1, VAL-GIT-1 and transaction E1/E2 are unchanged.
No new unsafe oracle behavior was observed.

All five parent mandatory local gates passed after these corrections, including
84 public pairs, seven private failure pairs, the complete retained parity gate,
and all 188 Python tests. Independent correction recheck passed without remaining
blockers. E3 is locally accepted; exact-SHA Ubuntu verification remains the gate
before E4. Python production is unchanged.
