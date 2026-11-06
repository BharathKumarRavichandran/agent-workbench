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

# Section E4 — source update checkpoint (review pending)

E3 exact-head `8979e99205e91ca55b2ad140af8338024af695f7` passed Ubuntu
[run 41967692601](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41967692601)
before this E4 work began (parent-provided exact-SHA evidence). This checkpoint
is uncommitted, has no E4 Ubuntu evidence, and must not be accepted or advanced
to E5/E6. Python production and every TODO checkbox are unchanged.

`sources.rs` now dispatches real public `update`, independently loads E2
workspace/catalog/profile data, performs registration and revision checks,
snapshots generated outputs, fetches the exact reviewed `refs/heads/...`, checks
out detached FETCH_HEAD, validates before/after actual recursive nested updates,
renders internally, and validates final outputs. E3's real generated writer is
shared; descriptor-relative deletion implements the oracle's absent-image
rollback. Caught failure restores the old detached revision and recursively
updates nested modules before restoring outputs in renderer order. Existing
output restoration intentionally produces 0600, not the previous mode.
`validation::collect_issues` is extracted for shared internal validation;
Rust consumes no Python results, plans or fixtures at runtime.

The first permanent public tracer was genuinely RED on unsupported `update` /
exit 2 against Python's successful old-to-new revision, then GREEN before
expansion. Further REDs pinned DEFAULT interpolation, lazy ignored percent
values and invalid UTF-8 `.gitmodules` decoding. Build errors are not counted
as behavioral evidence. UPDATE-GIT-1 is recorded separately in
`PARITY_EXCEPTIONS.md`; it does not authorize ConfigParser exceptions.

## Acceptance-to-test map

`scripts/check_update_parity.py` runs unconditionally **after all inherited
checks** in `check_rust_plan_parity.py`. Each scenario executes paired actual
CLIs once with separate stdout/stderr and once with a shared descriptor for
cross-stream ordering. All paths are scratch. Git system/global config, hooks,
author identity/date and locale are controlled; the exact reviewed HTTPS
strings are rewritten child-only to disposable file repositories with
`GIT_ALLOW_PROTOCOL=file`. No declared endpoint or installed resource is used.

| E4 edge | Runnable evidence |
| --- | --- |
| Actual public old-to-new update and generated bytes | `success`; complete typed working tree and semantic Git observations |
| Reviewed branch wins over a disagreeing remote default branch | `reviewed-disagrees` |
| Branch and source path validation before mutation | Five `branch:...` and two `path:...` rows |
| Nondefault profile, unknown source, local/curated source refusal | `nondefault`, `unknown-source`, `local-source`, `curated-source` |
| Source lexical symlink containment, including an outside sentinel | `source-link-inside`, `source-link-outside` |
| Gitlink registration, absent modules, nonunique path, mismatched upstream | `no-registration`, `no-modules`, `duplicate-path`, `wrong-url` |
| ConfigParser-style DEFAULT interpolation, case-insensitive options and colon delimiters, inert unused percent values | `config-defaults`, `config-case-colon`, `config-unused-percent` |
| Strict UTF-8 modules error | `config-utf8`, actual Python diagnostic bytes |
| Pre-snapshot actual Git revision failure | `revision-failure`, no working-tree changes, explicit UPDATE-GIT-1 outcomes |
| Fetch failure plus rollback and restore modes | `missing-branch`, `rollback-modes`; saved bytes and 0640-to-0600 restoration asserted literally |
| Actual dirty-worktree checkout failure and rollback stdout | `checkout-dirty`; retained `M\tskills/one/SKILL.md` stdout and exact stderr/order |
| Candidate validation rejection and real old-HEAD rollback | `candidate-invalid` |
| Actual recursive initialization, post-nested validation and local clone failure | `nested-success`, `nested-invalid`, `nested-git-failure`; retained nested checkout residue and Git registration state compared |
| Generated preflight and partial-publication rollback, including rollback error replacement | `output-link`, `render-failure`, `partial-render-restore`; no filtering of empty directory residue or effective modes |
| Exact output channel bytes, status, stream ordering and typed working-tree state | Every row; only explicitly authorized CalledProcessError tracebacks are separately asserted/omitted |
| HEAD, branch/detachment, refs, staged/unstaged/untracked state, local config, reflog subjects | Real Git semantic queries in every row; `.git` directories are not compared as raw timestamp/inode-bearing databases; `.git` indirection files remain in typed snapshots |
| Duplicate `.gitmodules` options/sections, parsing, missing-header and interpolation rejection | `config-duplicate-key` and `CONFIG_CASES`; UPDATE-CONFIG-1 exact final diagnostics and no mutation |
| ConfigParser whitespace/indentation and multiline/error accumulation | Five permanent `CONFIG_CASES` review regressions; original reviewer script and independent correction recheck passed |
| Six skills targets, copied/race/recovery/native activation, D8, both transaction exceptions, E2/E3 | Full parity gate retained |

## ConfigParser contract resolved; E4 review pending

The user approved UPDATE-CONFIG-1 after the original duplicate-option RED:
omit Python stack frames, retain its exact final ConfigParser exception class
and complete message, exit 1, empty stdout and no mutation. The interrupted
worker's dirty files were preserved in scratch `e4-resume-snapshot-lqw0fc6a`
with binary diffs, copied files and a mode/hash manifest before resuming.

Duplicate options/sections (including DEFAULT and case-folded duplicates),
missing/empty section headers, accumulated parsing lines, EOF errors and
precedence rows now pass as permanent public local-Git pairs. Six additional
interpolation rows first observed RED on Rust's generic exit-2 error, then GREEN
with exact syntax, invalid-reference, missing-option, depth, nested substitution
and URL-field diagnostics. Section names, requested option and original raw
value remain bound to the final diagnostic. Ignored values remain lazy.

The E4 matrix now contains 55 scenarios, each replayed with separate and merged
streams. Independent correction recheck passed after three reproduced review
findings: Python whitespace classification and character-count indentation,
blank multiline retention/final rstrip, and empty-key continuation truthiness.
The original five reviewer repros now match, including no mutation on rejection.
Full mandatory local gates remain in progress; E5/E6 have not begun.


## Checkpoint verification

All five mandatory parent local gates passed after correction: format,
all-target Clippy with warnings denied, Cargo tests, the complete parity gate
(including 55 update scenarios / 110 paired executions), and all 188 Python
unittests. Independent correction review passed. E4 is locally accepted under
UPDATE-GIT-1 and UPDATE-CONFIG-1; exact-SHA Ubuntu evidence is required before E5.
E5 began only after E4 head `5ef60af40158b9043a3b54194b223665ae4dbe2e`
passed all Ubuntu jobs in [run 41995623812](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41995623812);
its checkout log confirms the literal head. Python production remains unchanged.

# Section E5 — deterministic lifecycle text (review pending)

`operations_output` and the native slot printer reproduce the existing Python
14-character operation-kind padding, component/resource labels, optional source
arrow, slot brackets, diagnostic/reason and required/granted trust text. JSON
serialization remains the same ordered serializer. The CLI no longer requires
`--json`; this unit does not replace the general parser/help contract (E6).

Lifecycle and native apply now retain the already-rendered operation stdout
when apply returns an ordinary error. A typed lock-contention cause preserves
the primitive's existing diagnostic while allowing the public state-lock error
to identify the actual state file, not infer authority from a generic errno.
No lock or transaction mutation protocol is changed.

| E5 acceptance edge | Runnable evidence |
| --- | --- |
| Initial public text tracer | Worker observed missing-`--json` rejection RED for Hermes, then six-row GREEN before expansion; inherited interruption snapshot `e5-resume-snapshot-h8uc9yi0` preserves every changed file and binary diffs/hash/mode manifest |
| All six skills targets, empty/repeated uninstall, install/keep/status output and state | `check_lifecycle_text_parity.py`: each target full real lifecycle |
| Mixed copied paths, skills, hook bundles and shared slots | `native-cursor`, `native-claude`, real human commands plus independent JSON probes |
| Untrusted/granted/persisted trust text and exact slot ordering | Same native cases; plan/trust/status/install/refusal/reinstall/uninstall |
| Conflict/adopt/replace-owned/remove-owned, unsupported resource and missing project diagnostics | `edges`, all six targets; CLI adopt changes real ownership and source-path relocation replaces only the owned link |
| Apply failure retains operation bytes, exact state-lock stderr and exit 2 | `failures`, six skills targets and both native targets, install/uninstall in human and JSON modes; lock held through an independent real descriptor |
| Separate stdout/stderr and merged-stream ordering under apply failure | Same failures execute twice while lock held; assert fixture unchanged |
| Existing JSON, transaction and inertness behavior | Full existing parity gate retained |

The focused matrix now passes 307 paired public rows and is wired
unconditionally into `check_rust_plan_parity.py`. Review exposed inherited
`PYTHONUNBUFFERED=1` masking default merged-stream ordering. The explicit
absent/empty/zero/nonempty buffering matrix observed RED, then GREEN after
matching the oracle's text and binary buffering layers and device block size.
Large human and single-write JSON reports cross buffer thresholds in both
buffered and unbuffered modes; their real lock failure order is compared, not
just separate streams. Constructor metadata records JSON's single print payload
without inferring output semantics from string contents.

The public interruption driver was migrated to the new nonzero CommandOutput
failure channel: it requires exit 2, the injected diagnostic and retained
operation stdout. Existing authenticated journal/recovery/retry assertions are
unchanged; both native recovery suites pass. No Python output is consumed by
Rust. Independent correction review and all five parent mandatory local gates
passed after correction, including the full retained parity matrix and 188
Python tests. E5 is locally accepted; exact-SHA Ubuntu remains required before
E6 begins.

# Section E6 — first CLI help tracer (incomplete review checkpoint)

E6 starts from E5 head `2470e3f07bb4031feadface932a6e57725655b20`;
the parent verified literal-head Ubuntu run `42068777930` before delegation.
This bounded checkpoint implements **direct help only**, not E6 acceptance.
Python production, executable defaults, JSON codecs, target policy, lifecycle
parsing and transaction exceptions remain unchanged. Rust does not execute
Python or consume its plans/results. `cli_help.rs` formats option declarations
and greedy 78-column usage independently; root help retains the exact frozen
public command order. Existing output metadata/protocol is unchanged.

## Acceptance-to-test map

`scripts/check_cli_parity.py` is wired unconditionally into the existing umbrella.
It pins Python 3.12 (including nested `python3`), requires scratch TMPDIR, uses
isolated HOME/state and `COLUMNS=80`, asserts oracle success/empty stderr, and
compares exact stdout, stderr, exit status and unchanged typed fixture snapshots.
Selectors reject unknown names and the executed count must equal selected rows.

| E6 edge | Current executable evidence |
| --- | --- |
| Root direct help bytes and exit 0 | `root-help---help` |
| Every direct subcommand help, exact option order, wrapping, descriptions and metavar | `{validate,render,plan,install,status,trust,uninstall,update}-help---help` |
| Direct short help, long-option help abbreviations and doubled short help | Cartesian matrix of root/eight commands with `--help`, `-h`, `--h`, `--he`, `--hel`, `-hh`: 54 public pairs |
| Help does not load a workspace or create fixture state | Every pair executes without fixture catalog/profile and checks typed tree unchanged |
| All other E6 acceptance | **Unproven**, named next rows below; no general parser parity claim |

## Observed behavioral RED → GREEN

Every command below used this environment prefix (including Cargo commands):

```sh
TMPDIR=/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch \
PATH=/opt/homebrew/opt/python@3.12/libexec/bin:$PATH \
PYTHONDONTWRITEBYTECODE=1
```

1. Initial `python3 scripts/check_cli_parity.py root-help` (before selector
   expansion) exited 1: Python emitted the complete argparse help with public
   program name; Rust emitted the legacy one-line Rust-unit usage. After the
   minimal presentation fix and offline Cargo build, the same command emitted
   `CLI surface parity: 1 paired public rows` / exit 0. An intermediate rerun
   caught an extra trailing newline, which was removed before that GREEN.
2. `python3 scripts/check_cli_parity.py validate-help` (before selector expansion)
   exited 1: Python help/exit 0 versus Rust `unsupported validate argument:
   --help`/exit 2. The independent help formatter and direct dispatch produced
   `CLI surface parity: 9 paired public rows` / exit 0 before widening.
3. Expanded `python3 scripts/check_cli_parity.py root-help---he plan-help--h`
   exited 1 on Python help/0 versus Rust `unsupported global option: --he`/2.
   After direct help-alias recognition, the complete tracer produced
   `CLI surface parity: 54 paired public rows` / exit 0.

## Named remaining unproven rows

These are real public argv probes for the next vertical tracer, not implemented
speculation or passing acceptance rows. Replay them with a scratch workspace;
all options/defaults must also be tested through real lifecycle effects.

| Next row | Public argv / required extension |
| --- | --- |
| help-after-global | `--profile pstack plan --help`; repeated/abbreviated/equals globals before command |
| help-after-options | `plan --target hermes --help`; valid options, unknown deferred extras, and help before later invalid arguments |
| missing-value-before-help | `plan --target --help`; Python emits command usage plus expected-one-argument, exit 2 |
| choice-before-help | `plan --component bad --help`; invalid choice precedes help |
| explicit-help-value | `plan --help=bad`; ignored-explicit-argument diagnostic |
| command-not-abbreviated | `p --help`; Python rejects command choice, not a subcommand alias |
| double-dash | `-- --help`, `plan -- --help`; help after terminator is not a help action |
| values-not-flags | `plan --target=--help`, global profile/root values `--json`/`--check`; typed dispatch must not rescan values |
| ordinary-parser-errors | Empty argv, missing required command/target/source, unknown options/positionals, ambiguous prefixes, invalid components, combined defects and validation precedence |
| normalized-dispatch | Equals values, accepted abbreviations, repetition (last scalar, append/list dedup), global placement and local placement for every command |
| omitted-vs-literal-null | Omitted options versus CLI string `null` through profile, target, source, destination, project root, state dir, component and trust; do not collapse to wire None |
| defaults-and-target-failure-order | Trace Python defaults and callers; non-native non-pstack restriction, unknown target after workspace loading, HOME/XDG_STATE_HOME absent/empty/relative/tilde, disagreeing profile/CLI overrides |
| help-environment | Alternate/invalid COLUMNS, arbitrary repeated short-help clusters, help following parsed prefixes; this checkpoint proves COLUMNS=80 only |

The legacy broad `help_requested` scan and separate legacy command parsers remain
outside this direct-help tracer and are **not** evidence of correct placement or
precedence. A canonical parser/typed dispatch should replace them in the next
slice rather than extending token-anywhere scans. Full retained suites, five
parent gates, independent review, eventual commit and exact-head Ubuntu evidence
are deliberately left to the parent after this worker pauses. E6 is not marked
complete; no TODO, commit, push or merge is performed here.

Final focused recheck: 54 CLI pairs and six Hermes human lifecycle pairs passed;
`validation::tests::validate_as_an_option_value_is_not_a_command` passed (one
selected Rust test, 33 filtered out). Offline build and Cargo formatting ran;
these are not substitutes for the parent's full retained gates.

## E6 continuation — canonical argv / typed dispatch checkpoint (incomplete)

This continuation supersedes the direct-help-only implementation limitations
above, **not** the remaining E6 acceptance. The inherited dirty help checkpoint
is preserved at
`/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch/e6-help-checkpoint-sechfsfw`.
No Python production, dependencies, default executable, safety exceptions,
transaction engine or publication codec was changed. No commit/push/full gates.

### Implemented boundary

* `rust/src/cli.rs` now owns canonical argv parsing, returning a typed command
  or a complete help/parser-error `CommandOutput`. Root/eight command option
  placement, equals forms, unique long prefixes, ambiguous prefixes, immediate
  option-value/choice failures, deferred extras, required-before-extras,
  terminators, help action order and Python 3.12.15 short-help behavior are
  exercised through real public subprocesses.
* Parsed values are data, not normalized argv to be rescanned. Validate/render/
  update now receive typed root/profile/flags/source arguments; their loose
  `requested`/`run` parsers were removed. Public lifecycle dispatch preloads the
  workspace before converting the raw target string to `SkillsTarget`, matching
  workspace-before-unknown-target order. The closed target registry remains
  unchanged. Known targets are not hidden by an early enum conversion.
* `parse_cli` retains its public signature and filesystem-free lifecycle parse
  boundary for private drivers. Its `Cli` remains typed by `SkillsTarget`; public
  `execute` instead uses the canonical raw-target command and loads the workspace
  first. `help_requested` also uses the canonical parser, not a token-anywhere scan.
* Scalars use the last value; flags remain booleans; components append and are
  deduplicated in first-seen order at lifecycle request conversion; trust tokens
  append without interpretation. The arbitrary non-native `profile != pstack`
  rejection was removed. Default/custom profiles are publicly exercised against
  the non-native `agents` target. Empty explicit root is converted to `.` like
  Python `Path('')`.

### Public differential evidence

All commands used the environment prefix above. Local oracle is **Python
3.12.15**, not a claim about every 3.12 patch release. The harness still pins
major/minor, not patch; remote patch-release behavior needs parent verification.

1. `python3 scripts/check_cli_parity.py missing-value-before-help` initially
   exited 1 on real Python exit 2/command usage/expected-one-argument versus
   Rust exit 0/root help. A minimal command-specific fix built and made that
   exact row GREEN before parser expansion.
2. `... plan-missing-required` then exited 1 on Python command usage/required
   diagnostic versus Rust `error: --target is required`. Canonical parsing and
   typed dispatch replaced the temporary special case. The first expanded
   matrix reached **189 paired public rows**, exit 0, after literal oracle
   corrections for unquoted choice lists, short help tails and help-before-
   ambiguity on 3.12.15.
3. `... unknown-target-workspace-first unknown-target-valid-workspace` observed
   RED on Python invalid-profile versus Rust unsupported-target before workspace
   loading. Public workspace preload/unknown-target diagnostic made both GREEN.
4. `... dispatch-validate-empty-root dispatch-plan-empty-root
   dispatch-render-empty-root dispatch-update-empty-root` observed RED on
   Python successful planning versus Rust `path has no existing ancestor: `.
   Explicit empty-root normalization made all four GREEN.
5. Final expanded matrix: **297 paired public rows** / exit 0. The matrix includes
   every declared option's missing-value-before-help or boolean-explicit-value
   error, valid flag-looking equals values before help, both parser scopes'
   extras, all eight normalized dispatch paths, duplicate scalars/components/
   flags, opaque trust values, real install bytes/modes/state and real uninstall
   after an independently executed installation. A relative checkout named
   `--json` proves root values do not turn on JSON output; human validate and
   human lifecycle variants pin that separation. Actual typed snapshots compare
   without filtering; install authority is a deterministic private fixture key.
   Selector names are unique and selected/executed counts are asserted.
6. Focused retained checks: `check_activation_inertness.py` passed **30 paired
   public CLI rows**, both native validators and live executable/loopback
   canaries; `check_lifecycle_text_parity.py hermes` passed **six public pairs**;
   `cargo test --offline --manifest-path rust/Cargo.toml
   validation::tests::validate_as_an_option_value_is_not_a_command` passed its
   selected Rust test. `cargo test --offline --manifest-path rust/Cargo.toml
   --test plan_cli --test lifecycle_cli` passed both five-test targets. Its first
   run exposed a stale `unsupported subcommand` assertion; `plan_cli.rs` now
   checks complete canonical argparse usage/error bytes plus empty stdout,
   consistent with the public differential rows (no weakened status check).
   Offline build is warning-free; Cargo formatting and `git diff --check` passed.

### Exact remaining edges / handoff

* **Known lexer mismatch, reproduced but deliberately outside this bounded
  GREEN matrix:** `plan --target -١ --help` (Arabic-Indic digit) gives Python
  help/exit 0 versus Rust expected-one-argument/exit 2. Python's negative-number
  regex uses Unicode decimal digits; the current Rust helper covers ASCII
  digits only. Add paired Unicode-decimal and non-decimal-numeric controls
  before changing this helper; do not broaden it to all `is_numeric()` values.
* HOME/XDG_STATE_HOME omitted/empty/relative/tilde and every explicit default
  lifecycle sequence remain unproven here. In particular the existing
  `default_state_dir` treats empty XDG as a path rather than Python's fallback;
  this continuation did not change default-home/state policy.
* Full omitted-option versus literal CLI `null` matrix and corresponding private
  wire omitted-versus-null consumers remain to be traced; no codec distinction
  was collapsed to make argv tests pass.
* Help/usage widths other than `COLUMNS=80`, invalid COLUMNS, Unicode diagnostic
  repr corner cases (including POSIX undecodable argv), full Unicode path lexical
  normalization and the complete negative-number token language are not proved.
* Every long-option prefix and every combined-defect permutation is not
  exhaustively enumerated. Public normalized render uses `--check`; normalized
  update reaches the real local-source refusal, not a successful Git update.
  Successful normalized Git updates, alternate native-target activation grants,
  mixed-component first-seen order and all path override channels still need
  retained/full-matrix evidence. Existing dedicated suites remain the parent gate.
* Workspace preloading now uses the existing validation workspace reader before
  lifecycle reparses its domain model. Malformed-workspace precedence and
  lossless/native codec interactions across every lifecycle need parent full
  retained gates; passing these focused rows is not that evidence.
* Independent review, parent full gates and literal-head Ubuntu evidence are
  intentionally not run. E6 remains incomplete; no F/default cutover.

## E6 defaults / actual normalized lifecycle continuation

The parent added `check_cli_defaults_parity.py` (unconditionally wired into the
umbrella) after the canonical parser worker paused. Its 160 paired public
executions close the defaults/null/successful-update/native grant rows above:

| Edge | Executable cases |
| --- | --- |
| XDG state fallback and path expansion | `xdg-{omitted,empty,relative,absolute,tilde}`, `state-{empty,tilde,null}` through plan/install/status/uninstall |
| Literal `null` is a value, not omission | destination/project/state/trust paths or tokens; component/target/profile/root rejections; a real profile named `null`; update source named `null` refusal |
| Explicit wire null remains distinct from missing | profile destination/projectRoot/components set to JSON null are rejected, while omitted fields work |
| HOME behavior | empty and absent HOME with explicit scratch roots; relative HOME; empty-HOME default destination through read-only plan only |
| Target-specific environment truthiness | Codex/Hermes home absent, empty, relative, absolute and tilde through complete lifecycle |
| Overrides change effective behavior | profile destination/project/components disagree with CLI; CLI controls actual installed paths/components and persisted state |
| Successful normalized Git command | real disposable local Git update with repeated/equals/abbreviated root/profile/source; separate and merged streams, generated bytes/modes and semantic Git state |
| Native normalized activation | Cursor/Claude mixed skills/agents/hooks/MCP/rules-or-commands lifecycle with repeated scalar/component/trust options, real content-bound grants and uninstall; exact JSON/human output and snapshots |

The existing E4/E5 drivers expose opt-in normalized-argv modes; their original
default matrices and assertions are unchanged. All test state/authority stays
under scratch. HOME absence uses explicit project/destination/state. Empty HOME
has Python's `/` expansion, so its fallback destination is exercised by a
read-only plan rather than publication outside scratch.

Observed RED: `... check_cli_defaults_parity.py xdg-empty` produced Python plan
exit 0 versus Rust exit 2 (`path has no existing ancestor: `). Filtering empty
XDG and expanding both default and explicit state roots made the lifecycle
matrix GREEN. A separate read-only `home-empty-default-plan` RED showed Python
`/.agents/skills/example` versus Rust's cwd-relative `.agents/skills/example`;
the HOME-empty expansion fix made it GREEN. The completed script reports
`CLI defaults parity: 160 paired public lifecycle executions` / exit 0.
Help-width/Unicode work remains independently in progress; no E6 acceptance or
full-gate claim is made at this intermediate checkpoint.

## E6 final integration / review corrections (acceptance pending)

This section supersedes the intermediate remaining-edge lists above. All three
E6 differential scripts run unconditionally in the umbrella gate:

* `check_cli_parity.py`: 297 public pairs for all eight commands, exact help/
  parser diagnostics and status, aliases/prefixes, global placement, option
  values versus flags, repeated scalars/flags/components, required/choice/help/
  extras precedence, custom/default profiles and real normalized dispatch.
* `check_cli_help_edges.py`: 675 public pairs for Unicode-15 decimal operands
  (not all Unicode numerics), regex terminal-newline behavior, environment-driven
  help/usage width, description/hyphen wrapping and program-length thresholds.
  Its matrix includes absent/empty/invalid/Unicode/underscored/large COLUMNS and
  Python's 4300-digit integer-conversion boundary. The initial Arabic-digit
  operand RED and 116 width mismatches were corrected before matrix expansion.
* `check_cli_defaults_parity.py`: now 328 paired public executions. In addition
  to the 160 rows mapped above, 144 executions preserve ignored schema
  surrogate/NaN/nested-infinity/null annotations through plan/install/status/
  uninstall for every target. Twenty-four rows cover bare, dotted and absolute
  missing roots through all eight commands and workspace-before-target/source
  failure precedence, with exact diagnostics and read-only typed snapshots.

The independent precommit reviewer reproduced two blockers. Parent reproductions
were added permanently before fixes: ignored catalog surrogate produced Python
plan/0 versus Rust `unexpected end of hex escape`/2; a missing relative root
produced Python's absolute catalog-read diagnostic versus Rust's ancestor or
strict-root error. Lifecycle and native activation now use the same accepted
workspace catalog/profile readers as public preload instead of narrower serde
reparsing. No source-artifact, shared-document, state, journal or trust decoder
was widened. Relative non-strict workspace resolution is anchored to cwd, and
validate/render/update use it as lifecycle already does. The corrected defaults
matrix passed 328 paired executions. The focused reviewer also passed 1,781
additional deterministic parser comparisons; these are review evidence, not
substitutes for permanent CI rows.

The independent correction recheck passed both original reproductions, all 328
expanded lifecycle executions and Cargo authorization/containment/rejection
tests, with no remaining blocker. All five mandatory parent gates passed on the
stable corrected tree, including the complete retained parity matrix and 188
Python tests. E6 is locally accepted; exact-SHA Ubuntu remains pending.
E5's literal corrected head
`2470e3f07bb4031feadface932a6e57725655b20` passed Ubuntu run `42068777930`
(checkout log verified) before E6 began. Python production, default executable,
Section F and all intentional parity exceptions remain unchanged.
