# Section F — packaging, compatibility, and cutover

Python production remains the frozen executable specification until F8. Rust
does not invoke Python or consume Python-produced plans or results. F1 consumes
only authentic ownership state and installed resources written by the pinned
Python CLI.

## Pinned Python baseline (no historical release)

Git tags, remote tags, and GitHub releases were empty at F1 start. There is no
released Python artifact to install. The accepted producer is the in-tree
`python3 -m agent_workbench` package at Section E merge

`04502a0054fcd3c666e06f4e20ef7603293cfed3`

(PR #8, implementation head `14cb413cc1abf23dc34a9d6cfbfcdafca8581350`). The F1
gate fails if a tag or GitHub release appears without updating this pin. Do not
invent released versions.

The pinned baseline emits **v3** for skills-only ownership and **v4** for
native activation (directory copies and shared slots). It still **loads**
legacy v2. F1 obtains v2 bytes by rewriting an authentic baseline install
through Python `InstallState.as_dict` / `write_state` at the same lexical
paths; it does not hand-write lookalike JSON.

## Supported and rejected upgrade directions

| Direction | F1 status |
| --- | --- |
| Empty tree → Rust clean install | Supported. Compared against a paired empty-tree Python install. |
| Python v2/v3/v4 ownership → Rust status/reinstall/uninstall | Supported upgrade toward cutover. |
| Rust ownership → Python | **Not a supported upgrade.** Incidental shared-schema reads are not an F1 promise. F1 asserts Python does not delete or rewrite the evidence on a read-only status. |
| v4 activation rewritten as v3 (slots dropped or retained) | **Rejected.** Both engines fail closed and leave the file bytes unchanged. |
| Reverse schema downgrade on Rust reinstall | **Rejected.** Rust reinstall of Python v4 state remains v4. |

Transaction exceptions E1/E2 and VAL-GIT-1 / RENDER-GIT-1 / UPDATE-GIT-1 /
UPDATE-CONFIG-1 are unrelated identifiers and stay unchanged. Relocation,
interruption, and authenticated journal replay are F2.

## F1 acceptance-to-test map

`scripts/check_python_baseline_upgrade.py` runs unconditionally from
`scripts/check_rust_plan_parity.py`. Every fixture uses explicit scratch
`TMPDIR`. Producer commands are always the pinned Python CLI; Rust never
installs the fixture it later upgrades except in the independent clean-install
rows.

| F1 acceptance | Runnable evidence |
| --- | --- |
| First same-tree tracer: Python skills install, then Python vs Rust status | `check_python_baseline_upgrade.py tracer` (`skills-status@hermes`); exact stdout/stderr/status and inert typed snapshot |
| Clean install, all six skills targets | `skills-clean@{agents,claude,codex,cursor,grok,hermes}` |
| Python→Rust status/reinstall/uninstall, all six skills targets | `skills-status`, `skills-reinstall`, `skills-uninstall` per target |
| Foreign file and modified owned link conflicts | `skills-foreign`, `skills-modified` per target |
| Legacy v2 and current v3 from authentic skills installs | `skills-v2-migrate`, `skills-v2-uninstall` per target; v3 asserted on baseline install |
| Copied Cursor agents/rules (project) and Claude agents/commands | `copied-cursor`, `copied-claude`; status/reinstall/uninstall/conflict/foreign |
| Native Cursor/Claude activation, trust, slots, foreign, reinstall, uninstall | `activation-cursor`, `activation-claude` using `prepare()` catalogs; Python writes grants/state/bundles, Rust consumes them |
| Content-bound trust retained on unchanged reinstall | Activation reinstall rows require no new `--trust` and keep grants |
| Unsupported reverse and v4→v3 downgrade | `reverse-status-not-supported`, `reject-v4-as-v3-slots-kept`, `reject-v4-as-v3-slots-dropped` |
| No silent evidence discard | Downgrade rows compare pre/post file bytes; reverse status leaves Rust state intact |
| Inertness | `PAYLOAD_EXECUTED` absent; status/trust do not mutate snapshots |

## Later units (not F1–F7)

F8 Python removal.

## Observed RED → GREEN

The first same-tree tracer (`skills-status@hermes`) was **characterization
GREEN**: Python 3.12.15 produced v3 ownership, then Python and Rust `status`
matched on stdout, stderr, exit 0, and an inert typed snapshot. That is
inherited Section E behavior, not a production defect.

Harness-only failures while expanding the matrix were not behavioral RED:
reusing a fixture directory after the tracer, `prepare()` without a parent
directory, and paired-tree activation stdout that still contains path-bound
slot digests. Production Rust was not changed. Activation status/reinstall
are compared on the **same lexical tree** after a real Python install.
Paired uninstall compares return status, BASE-normalized remaining trees,
and destination/kind/reason — not fixture-specific slot hashes.

Independent review passed with no security or logic blockers. Applied
non-blocking follow-ups: fail if `agent_workbench/` drifts from the pin,
cover copied-resource leftover foreign files, and reject unknown argv.
The producer is current-tree Python whose `HEAD:agent_workbench` tree must
equal `01c263007031df3ea599813796e2d3c127f4b4ce` (the Section E merge tree).
Ancestor checks run only when that commit object is present, so shallow CI
checkouts still pin package bytes.

## F2 — relocation, interruption, and recovery handoff

`scripts/check_python_rust_recovery_handoff.py` runs unconditionally in the
umbrella parity gate, alongside all Section D/E/F1 scripts. Run its `tracer`
argument for the narrow held-resource-lock regression. Python production and
the baseline tree pin above are unchanged. F4 is a later packaging unit.

### Acceptance criteria and test map

| F2 acceptance | Runnable evidence in `check_python_rust_recovery_handoff.py` |
| --- | --- |
| Authentic Python ownership survives checkout relocation or fails closed without rewriting recorded paths | `relocation_skills` for all six targets; `relocation_activation` for Cursor/Claude; actual checkout rename, then same-tree differential status/install/uninstall |
| Source-only move does not manufacture authorization | Both relocation helpers with `source_only=True`: rename `plugins` without editing catalog/state/grants; compare exact failure output and unchanged trees on rejection |
| Python interrupted apply → Rust public status/recovery/retry | `tracer` / `recover_tracer`, both native targets, journal-durable and before-state boundaries, install and uninstall; real CLI planning and apply, authenticated journal, exact public output and recovered bytes/modes/types |
| Rust interrupted apply → Rust recovery/retry | Same tracer matrix with `engine='rust'`: ignored private driver takes CLI argv and independently plans/applies; Rust public status consumes its genuine journal |
| Interruption combined with checkout relocation | `tracer(..., relocate=True)`: Python before-state interruption, checkout rename, unchanged authenticated destination parents, Rust recovery/retry/uninstall |
| Authentic parent inodes remain attached during replay | `parents` / `assert_parents` before oracle recovery, before and after file/evidence replay; no fixture-tree restore. Only non-authority temporary directories may be recreated |
| Copied/unauthenticated journals are NOT successful handoffs | `rejected_journal`: copied tree (including key but unchanged journal paths), bad MAC, substituted state parent; Python/Rust status/install/uninstall reject identically without mutating either original or copied evidence. Only the untouched original or reattached original inode then recovers |
| Interrupted recovery can itself be retried | `recovery_interrupted`: Python and Rust real apply journals; existing private recovery driver faults actual `AfterPath`, asserts fault hit, failure class, retained journal and parent identities, then public Rust status/retry/uninstall |
| Resource and document lock contention retains evidence | `locked_recovery`, both native targets, independently opened `flock` descriptors, status/install/uninstall: exact Python/Rust diagnostics and immutable filesystem identities; release then real recovery/retry |
| Shared slots, copied resources, foreign fields, publication residue | Native tracer includes skills, agents, Cursor rules / Claude commands, hooks bundles and MCP; interrupts before and after publication, compares recovered typed tree/state, preserves foreign hook/MCP fields and removes only the managed bundle on uninstall |
| Content-bound trust remains read-only and cannot recover pending journals | Native tracer asserts pending `trust` refusal is identity-inert; after recovery, changed hook bytes invalidate old tokens, failed install keeps ownership bytes, explicit fresh grants permit retry |
| Skills-only interrupted ownership, all six targets | `skills_interrupted`: real Python durable progress interruption during two-skill install/uninstall, then same-tree Python/Rust status/install/uninstall. Skills-only uses progress state, NOT an activation journal; no synthetic journal is asserted |
| Hooks/MCP never execute or connect during handoff | Native tracer reuses executable/loopback `Observer` fixtures and real positive controls, checks tripwires after interruption/recovery/trust/retry/uninstall. Existing D8/E inertness/inspection/validation/render/migration gates remain in the umbrella unchanged |

### Direction and evidence boundaries

Supported direction is Python-produced ownership/journals → Rust recovery and
retry. Rust-produced interrupted apply → Rust recovery is also tested. Reverse
Rust → Python recovery is **not supported as an upgrade**. The Python consumer
used to characterize recovery of a Rust journal in the differential harness is
only an executable oracle comparison at identical lexical parents, not a
downgrade promise. No journal is discarded to make a reverse operation succeed.
The private recovery driver receives roots from authentic ownership bytes,
not a Python plan; successful public recovery is separately exercised.

### Behavioral RED → GREEN

The first uncontented Python-journal → Rust status tracer characterized inherited
GREEN behavior. Extending that exact handoff to a held ownership-state lock
produced a real behavioral RED: Python returned exit 2 with
`installation state is locked by another operation: <state-file>`, while Rust
returned exit 2 with `File exists (os error 17)`. This was not a build/import
failure. `check_python_rust_recovery_handoff.py tracer` reproduces the boundary.

Rust recovery was converting the typed `LockContention` to a generic string,
unlike the public apply boundary. F2 preserves the typed operand through the
public recovery printer for both state and document locks. It does not change
authentication, locking, rollback, journal formats, or the frozen oracle.
The tracer is GREEN with exact stdout/stderr/status, unchanged pending bytes
and identities under contention, and successful recovery after lock release.
Harness-only file replay/isolated-HOME setup failures are not behavioral REDs.
E1/E2 and VAL-GIT-1 / RENDER-GIT-1 / UPDATE-GIT-1 / UPDATE-CONFIG-1 remain intact.

## F3 — full macOS/Linux parity and required filesystem primitives

The `rust-plan-parity` CI job now has independent `ubuntu-24.04` and `macos-15`
matrix cells with `fail-fast: false`. Both check out the literal PR head SHA,
pin top-level and nested Python to 3.12, publish a job-owned scratch subdirectory
beneath `runner.temp` through `GITHUB_ENV` for builds and fixtures, and run fmt, check, all-target clippy with denied
warnings, Rust tests, the **entire** umbrella parity suite, and Python tests.
There is no smoke-only substitute, platform exclusion, or continue-on-error.
Existing Python 3.9/3.12 catalog jobs remain unchanged.

### Acceptance-to-test map

| F3 acceptance | Runnable evidence |
| --- | --- |
| Required primitives work on the actual fixture filesystem | `scripts/check_platform_primitives.py` and `platform_parity_tests::required_filesystem_primitives`: descriptor-relative operations, no-follow directory open, file/directory fsync, exact modes, hardlink publication refusing replacement, native exclusive directory rename refusing an occupied empty directory, independent-descriptor nonblocking flock, and mutation through the retained parent descriptor after lexical substitution |
| Python and actual Rust backend have matching primitive effects | The script invokes Python production `bundles.open_directory` / `rename_new`, then the Rust production counterparts and rustix operations; compares literal typed bytes/modes/link targets. Primitive-only fixtures carry no ownership/journals and may be rebuilt |
| Missing primitives are hard failures, not skipped success | Every probe requires positive syscall execution and expected refusal behavior. Python requires `supports_dir_fd`; no-follow, no-replace, lock contention, fsync or parent-pinning failure stops the gate |
| Full earlier acceptance survives on each OS | Both CI matrix cells run `check_rust_plan_parity.py` unconditionally, including all D/E/F1/F2 drivers and the new platform script; no narrowing of journal recovery, trust, output, migration, rendering or executable/network tripwires |
| Actual fsync fault instrumentation is working | Existing `capture_contract.py` canaries exercise Darwin dyld interposition or Linux x86_64 thread-following ptrace against the actual configured rustix backend; unsupported/blocked observers fail closed |
| Raw filenames are characterized rather than normalized | Existing `wire_contract.py` probes actual file/directory creation; publication rows run only when the filesystem accepts those raw names. Rejected names are reported as rejection evidence, not successful publication |
| Literal tested revision and platform are auditable | Each matrix cell records `uname -sm`, rustc and oracle versions; verify run `headSha`, each checkout's `HEAD is now at <implementation>`, job success, primitive report and final umbrella summary before declaring F3 complete |

The first primitive tracer is inherited characterization GREEN on local
Darwin/arm64 with Python 3.12.15; it does not establish a new production defect
or a behavioral RED. F3 adds tests and CI configuration, not runtime behavior.
Compilation/format failures are not behavioral REDs. Linux and hosted macOS
results require their own exact-head CI execution; local macOS is not a
substitute. The platform scope is the named runners and their tested filesystem
primitives, not Windows, Linux arm64, network filesystems, arbitrary kernels,
or a claim of hardware/power-loss durability. F4–F8 remain separate units.

The initial F3 workflow was rejected before any jobs ran because `runner.temp`
is not an allowed job-level `env` context. The correction sets scratch through
a runner step instead; local actionlint 1.7.12 validates the corrected workflow.
That configuration failure is not a test/behavioral RED or platform evidence.

## F4 — release executable

`scripts/package_release.py` builds `cargo --release` and installs one
`agent-workbench` executable at `$PREFIX/bin/agent-workbench`. The crate debug
binary remains `agent-workbench-rs` for existing parity drivers. Python remains
the default CLI. No Git tag or GitHub release is published; the F1 producer pin
still fails if one appears. Production Python and Rust behavior are unchanged.

`scripts/check_release_executable.py` runs unconditionally in the umbrella
parity gate. It requires explicit scratch `TMPDIR` and the pinned Python 3.12
oracle.

### Acceptance-to-test map

| F4 acceptance | Runnable evidence |
| --- | --- |
| Release build produces one installed executable named `agent-workbench` | `package_release.py --prefix <scratch>` copies `rust/target/release/agent-workbench-rs` to `$PREFIX/bin/agent-workbench` mode `0755`; the prefix contains no `agent-workbench-rs`; header is ELF or Mach-O |
| Documented install path | README / `make rust-release PREFIX=...`; gate invokes the packager then `PATH=$PREFIX/bin agent-workbench --help` |
| Documented upgrade path | Python hermes skills install, then same-tree Python vs installed-release `status` and keep-`install`; exact stdout/stderr/status and inert snapshot |
| Documented uninstall path | Python hermes install, then paired Python vs installed-release `uninstall`; return status, BASE-normalized output, and remaining typed snapshot |
| Clean install with the release binary | Paired empty-tree Python vs installed-release hermes install |
| Help names the installed executable | `--help` contains `usage: agent-workbench` and not `agent-workbench-rs` |
| Default CLI and producer pin unchanged | README Commands remain `python3 -m agent_workbench`; F1 tag/release refusal still runs via `require_pinned_baseline` |

The first same-tree status tracer is inherited characterization GREEN: the
release binary matches debug/Python status on producer-written hermes
ownership. Packaging absence was the F4 RED (`package_release.py` missing).

## F5 — invocation compatibility

Python remains the default CLI until F7. Existing `python3 -m agent_workbench`
invocations, Makefile recipes, and command-specific shebang wrappers stay on
the Python engine. `scripts/agent-workbench` is an explicit, tested wrapper
that execs `python3 -m agent_workbench`. It is not installed as
`$PREFIX/bin/agent-workbench` and does not change the default engine. No Git
tag or GitHub release is published; the F1 producer pin still fails if one
appears. Production Python and Rust command behavior are unchanged.

`scripts/check_invocation_compatibility.py` runs unconditionally in the
umbrella parity gate. It requires explicit scratch `TMPDIR` and the pinned
Python 3.12 oracle.

### Acceptance-to-test map

| F5 acceptance | Runnable evidence |
| --- | --- |
| `python3 -m agent_workbench` still works | `documented_module_form`: argv is `python3 -m agent_workbench`; `--help` exits 0 |
| Makefile entry points stay on the Python module | `makefile_stays_on_python_module`: `make -n` for render/validate/update/plan-hermes contains `-m agent_workbench`; `install-dry-run` still calls `scripts/install_skills.py`; `rust-release` still calls `package_release.py` and does not install the wrapper |
| Documented shebang wrappers still run | `shebang_entry_points_still_run`: `validate.py`, `render_marketplaces.py`, `update.py`, `install_skills.py` keep `#!/usr/bin/env python3` and `--help` exits 0 |
| Explicit wrapper does not silently change the default engine | `explicit_wrapper_is_python`: `scripts/agent-workbench` is a shebang script that execs `-m agent_workbench`; `$PREFIX/bin` still contains only the Rust `agent-workbench` binary; README Commands remain `python3 -m agent_workbench` |
| Wrapper vs real binary vs `python3 -m` are distinguishable | `three_forms_distinguishable`: module argv, wrapper file kind `python-wrapper`, installed file kind `rust-binary`; wrapper `--help` matches the module and not the binary's file kind |
| Upgrade/uninstall of producer-written ownership through the preserved invocation | `upgrade_uninstall_through_preserved`: Python hermes install, then same-tree module vs wrapper `status` (inert snapshot) and paired `uninstall` |
| Default CLI and producer pin unchanged | `default_engine_unchanged` plus `require_pinned_baseline`; F6/F7/F8 remain later units |

Documented module, Makefile, and shebang forms were inherited characterization
GREEN. The F5 RED was the missing explicit wrapper (`scripts/agent-workbench`).
F7–F8 remain separate units.

## F6 — intentional differences and migration

Python remains the frozen oracle and the default CLI
(`python3 -m agent_workbench`) until F7. F6 records already-authorized
differences and the state/config migration required before cutover. It does
not change production Python or Rust behavior, invent divergences, replace
`$PREFIX/bin/agent-workbench`, or publish Git tags or GitHub releases.

`scripts/check_intentional_differences.py` runs unconditionally in the
umbrella parity gate. It requires explicit scratch `TMPDIR` and the pinned
Python 3.12 oracle.

### Acceptance-to-test map

| F6 acceptance | Runnable evidence in `check_intentional_differences.py` |
| --- | --- |
| Every authorized difference is named with command, boundary, stdout/stderr class, exit, and state/config effect | `CATALOG` versus `PARITY_EXCEPTIONS.md` headings; this section copies each field |
| Each difference has a permanent paired regression (oracle and replacement asserted separately) | `exception_tests_assert_separately` plus `paired_git_validate`, `paired_git_render`, `paired_update_exceptions`; E1/E2 remain `state_parent_contract.py` / `stage_cleanup_contract.py` |
| Do not hide a difference in a generic normalizer or extend it to another command | Separate Python/Rust assertions in the mapped scripts; `normalize_traceback` is absent |
| Producer-written ownership → rewrite before default cutover | `same_tree_status` hermes (`PYTHON-TO-RUST-UPGRADE`) |
| Unsupported reverse/downgrade still fail closed | `reverse_and_downgrade` (`REVERSE-STATUS-NOT-SUPPORTED`, `REJECT-V4-AS-V3`) |
| Default CLI is still `python3 -m agent_workbench` | `default_engine_unchanged`; README Commands stay on the module form |
| F7/F8 stay later | `TODO.md` default-cutover and Python-removal boxes remain unchecked |
| Document differences and migration | README section plus this map |

### E1

- command: install/uninstall apply (Cursor/Claude private driver)
- boundary: ownership-state parent replaced immediately before or after final rename/unlink
- python_stdout: empty or success operations
- python_stderr: none on directory replacement; Git-unrelated
- python_exit: 0 on regular-directory replacement; nonzero on symlink nofollow sync
- python_state: overwrites or removes foreign state; original detached state retained
- rust_stdout: empty on failure
- rust_stderr: typed parent-identity failure
- rust_exit: nonzero
- rust_state: foreign replacement bytes/mode/inode unchanged; authenticated journal retained
- evidence: rust/src/transaction/state_parent_contract.py

### E2

- command: install apply (Cursor/Claude private driver)
- boundary: foreign directory recreated at consumed .agent-workbench-copy-* staging name
- python_stdout: success operations when no injected fsync fault
- python_stderr: none on false success
- python_exit: 0 without injected fault
- python_state: recursively deletes the foreign staging replacement; journal removed on false success
- rust_stdout: empty on conflict
- rust_stderr: typed foreign-stage conflict
- rust_exit: nonzero
- rust_state: foreign inode/bytes/modes preserved; authenticated journal retained
- evidence: rust/src/transaction/stage_cleanup_contract.py

### VAL-GIT-1

- command: validate
- boundary: uncaught git rev-parse HEAD failure during generated-file lock rendering
- python_stdout: empty
- python_stderr: raw Git stderr plus CalledProcessError traceback
- python_exit: 1
- python_state: unchanged
- rust_stdout: empty
- rust_stderr: raw Git stderr only
- rust_exit: 1
- rust_state: unchanged
- evidence: scripts/check_validate_parity.py::git_revision_rows

### RENDER-GIT-1

- command: render / render --check
- boundary: uncaught git rev-parse HEAD failure during lock rendering
- python_stdout: empty
- python_stderr: raw Git stderr plus CalledProcessError traceback
- python_exit: 1
- python_state: unchanged
- rust_stdout: empty
- rust_stderr: raw Git stderr only
- rust_exit: 1
- rust_state: unchanged
- evidence: scripts/check_render_parity.py git-nonrepository git-no-head

### UPDATE-GIT-1

- command: update --source sample
- boundary: uncaught source-update Git failures, including after rollback
- python_stdout: empty except dirty-checkout Git stdout
- python_stderr: actual Git streams plus CalledProcessError traceback
- python_exit: 1
- python_state: pre-snapshot failures remain pre-mutation; rollback matches Python
- rust_stdout: matches Python without traceback
- rust_stderr: actual Git streams without traceback
- rust_exit: 1
- rust_state: matches Python filesystem and Git semantic state
- evidence: scripts/check_update_parity.py missing-branch

### UPDATE-CONFIG-1

- command: update --source sample
- boundary: .gitmodules configparser.Error family during registration parse
- python_stdout: empty
- python_stderr: traceback plus exact final configparser.<Class>Error message
- python_exit: 1
- python_state: unchanged
- rust_stdout: empty
- rust_stderr: exact final configparser.<Class>Error message without stack frames
- rust_exit: 1
- rust_state: unchanged
- evidence: scripts/check_update_parity.py config-duplicate-section

### PYTHON-TO-RUST-UPGRADE

- command: status after python3 -m agent_workbench install
- boundary: producer-written ownership consumed in place by Rust
- python_stdout: status operations
- python_stderr: empty on success
- python_exit: 0
- python_state: inert snapshot; v3 skills / v4 activation retained
- rust_stdout: matches Python
- rust_stderr: matches Python
- rust_exit: 0
- rust_state: inert snapshot; no rewrite of producer bytes on status
- evidence: scripts/check_python_baseline_upgrade.py tracer

### REVERSE-STATUS-NOT-SUPPORTED

- command: python3 -m agent_workbench status on Rust-written ownership
- boundary: unsupported reverse upgrade
- python_stdout: status or error report
- python_stderr: error class if rejected
- python_exit: 0, 1, or 2
- python_state: Rust evidence bytes unchanged
- rust_stdout: not a reverse consumer
- rust_stderr: not a reverse consumer
- rust_exit: n/a
- rust_state: producer for this row
- evidence: scripts/check_python_baseline_upgrade.py reverse_and_downgrade

### REJECT-V4-AS-V3

- command: status on v4 activation rewritten as v3
- boundary: unsupported schema downgrade with slots kept or dropped
- python_stdout: empty or error report
- python_stderr: schema rejection
- python_exit: nonzero
- python_state: file bytes unchanged
- rust_stdout: empty or error report
- rust_stderr: schema rejection
- rust_exit: nonzero
- rust_state: file bytes unchanged
- evidence: scripts/check_python_baseline_upgrade.py reverse_and_downgrade

The F6 RED was missing catalog documentation and the dedicated gate. Authorized
exception behavior is inherited characterization GREEN. F8 remains a later unit.

## F7 — default cutover

Rust is the documented default CLI. The default entry point is the Rust
`agent-workbench` executable (`$PREFIX/bin/agent-workbench` and the transition
wrapper). python3 -m agent_workbench still runs as the oracle; F8
removes Python later. F6 catalog IDs and producer→rewrite / fail-closed
reverse/downgrade evidence remain required. Do not invent divergences, hide
them in a generic normalizer, delete `agent_workbench/` or Python tests, or
publish Git tags or GitHub releases.

`scripts/check_default_cutover.py` runs unconditionally in the umbrella parity
gate. It requires explicit scratch `TMPDIR` and the pinned Python 3.12 oracle.
The F7 TODO box is checked only after local gates; this test must not require
the box already checked.

### Acceptance-to-test map

| F7 acceptance | Runnable evidence in `check_default_cutover.py` |
| --- | --- |
| Documented default CLI is the Rust `agent-workbench` executable, not `python3 -m agent_workbench` | `documented_default_is_rust` |
| README Commands, Makefile recipes that were Python module invocations, and the transition wrapper dispatch to that default | `leftover_documented_default_is_not_python`, `makefile_dispatches_to_default`, `wrapper_dispatches_to_rust` |
| Fail if a leftover documented path still execs Python as default | `leftover_documented_default_is_not_python` |
| `python3 -m agent_workbench` still runs as the oracle; F8 stays later | `oracle_module_still_runs`; TODO Python-removal box remains unchecked |
| Producer-written ownership → default-Rust status/reinstall/uninstall | `migration_through_default` |
| Unsupported reverse/downgrade still fail closed | `migration_through_default` via `reverse_and_downgrade` |
| Authorized differences stay the F6 catalog | `f6_catalog_required` |
| F6 catalog and migration evidence remain required | `f6_catalog_required`; umbrella still runs `check_intentional_differences.py` |
| Document the default cutover | `documentation_records_cutover` |
