# Intentional parity exceptions

## E1 — late ownership-state parent replacement (user authorized)

The user explicitly authorized this **narrow safety exception**: leave the Python
production oracle frozen; Rust must reject replacement of the ownership-state
parent at the final state publication/removal boundary rather than reproduce an
outside overwrite. This is not authorization for general parity drift, broader
validation changes, public activation, or declaring D5 complete.

Source: `agent_workbench/state.py:288-307` (`write_state`/`remove_state`) and
`agent_workbench/transactions.py:694-706` (last pre-state parent verification,
state publication, and ordinary-error recovery). The original disposable
reproduction remains `src/transaction/oracle_state_race.py`, unchanged.

### Exact difference

- Before the final rename/unlink, the attacker detaches the authorized state
  directory and substitutes either a different directory at that same lexical
  path or a symlink to an isolated outside directory. For install, the fixture
  also seeds the same temporary basename there after the real temporary is synced.
- Python install overwrites the foreign state (bytes, inode and 0640 → 0644 mode
  change); its original 0600 synced temporary remains detached. Python uninstall
  removes the foreign state. Both retain the original detached ownership state.
- A symlink replacement makes Python's subsequent nofollow parent sync fail;
  unauthenticated recovery then retains the journal. A regular-directory
  replacement instead permits false success and journal deletion. After
  reattachment, that uninstall leaves stale ownership and a real retry conflict.
  The paired regression asserts these distinctions rather than treating all
  oracle outcomes as equivalent failures.
- Rust verifies the authorized parent identities immediately before and after
  descriptor-relative rename/unlink. A replacement observed before mutation
  fails closed **without changing the old detached state**. It preserves the
  foreign state bytes, mode and inode, retains authenticated intent, and removes
  only its own state temporary through the pinned descriptor. There is no
  check-then-path-rename fallback.
- A swap observed after mutation cannot undo the already completed state
  rename/unlink. Rust reports failure and retains authenticated intent for
  recovery; it never mutates the replacement directory. The test distinguishes
  this post-publication case explicitly rather than claiming old-state retention
  after an already completed mutation. Python again falsely succeeds for a
  directory replacement and fails for a symlink.

### Executable evidence

`src/transaction/state_parent_contract.py` runs **16 paired rows**: Cursor/Claude
× regular-directory/symlink substitution × install/uninstall × immediately
before/after state mutation. Python invokes actual `installation.apply_plan` or
`apply_uninstall`; Rust invokes the private real-plan apply driver at
`StateReplace`/`StateUnlink` or `StateParentSync`/`StateRemovedSync`. These rows run
unconditionally from `scripts/check_rust_plan_parity.py` in normal CI.

The pre-mutation Rust expectation was observed RED before the production change:
`AssertionError: Rust published/removed state in detached parent (missing late
revalidation)`. This demonstrates missing revalidation, not merely the existing
pinned-descriptor outside-write protection.

Typed observations retain literal links, presence, bytes, modes and per-fixture
(dev, inode) identities. The raw journal MAC and authenticated parent identities
are verified without normalization; seeded outside temporaries and Python's
abandoned synced temporary are asserted separately, never normalized away.
Reattaching the **exact original directory inode** permits real Rust recovery
and a freshly planned, real install/uninstall retry in every row; the foreign
replacement tree remains byte/mode/identity unchanged throughout repair/retry.
The oracle's regular-directory pre-uninstall false success/retry conflict is an
explicit exception outcome, not hidden by fabricated recovery.

All paths are disposable fixture paths under explicit scratch `TMPDIR`. The
16-row E1 gate and all five mandatory macOS gates passed on the current D5
implementation. The current acceptance-to-test map and platform evidence live
in `TRANSACTION_CONTRACT.md`; no uncommitted Linux execution is inferred.

These are deterministic software boundary tests, not proof against
arbitrary scheduling, process death, or power loss. Public activation remains
disabled pending D6; other unproven D5 edges remain listed in
`TRANSACTION_CONTRACT.md`.

## E2 — foreign replacement at a consumed bundle staging name (user authorized)

The user explicitly selected **“Authorize narrow E2 and continue”** after four
real Cursor/Claude Python/Rust apply reproductions showed deletion of a foreign
directory recreated at the now-vacant `.agent-workbench-copy-*` name. Python
production remains unchanged. This authorization does not permit other parity
changes, implicit trust, or marking incomplete units complete.

### Exact difference and scope

Python `installation._DestinationRoot.create` renames a completed hook bundle
into its destination, then unconditionally calls `bundles.delete_tree` on the
old staging name in `finally`. A foreign directory created there after the
rename is recursively deleted. With no injected persistence error, apply
falsely reports success and removes the authenticated journal. With a parent
fsync failure immediately after the rename, ordinary rollback removes the
journal but still does not restore the deleted foreign directory.

Rust records whether the no-replace rename actually consumed its stage, even
when a subsequent fsync fails. Once consumed, that name confers no cleanup
ownership: Rust checks it without following links, never recursively deletes
its replacement, and reports a typed foreign-stage conflict. This specific
conflict retains the authenticated journal rather than automatically rolling
back and erasing the evidence. Ordinary failures keep their existing rollback
protocol; stages not consumed by publication are not covered by this exception.

### Executable evidence

`src/transaction/oracle_cleanup_race.py` preserves the original unsafe oracle
reproduction; `--require-preservation` intentionally remains RED for frozen
Python. Before the Rust change, both engines deleted the foreign directory,
returned success, and removed the journal in the four target/engine rows.

`src/transaction/stage_cleanup_contract.py` runs eight target/engine/phase rows:
Cursor/Claude × Python/Rust × successful publication/parent-fsync failure after
rename. The normal parity gate invokes it unconditionally. Python outcomes are
asserted literally, not normalized. Rust preserves the foreign directory's
original device/inode, `0750` mode, sentinel bytes and `0640` sentinel mode;
retains and authenticates the `0600` journal; then performs real Rust recovery,
fresh-plan retry, and uninstall without touching the foreign replacement.

The safety regression was observed RED on Rust falsely accepting and deleting
the replacement. A second RED after an injected post-rename fsync failure proved
that successful-operation status was insufficient: mutation completion must be
tracked separately. Both phase regressions now pass locally. Full gate and Ubuntu
evidence are tracked separately in `TRANSACTION_CONTRACT.md`.

## VAL-GIT-1 — uncaught validation Git revision failure (user authorized)

The user explicitly approved **Python 3.12 as the exact diagnostic oracle** and
this narrowly bounded diagnostic exception while completing Section E2. Python
production remains frozen. This identifier is distinct from the transaction
E1/E2 exceptions above; neither transaction authorization is changed.

### Exact boundary and observable difference

`agent_workbench/adapters/lock.py:35-38` invokes real `git -C <source>
rev-parse HEAD` while the internal canonical lock renderer checks generated
files. If Git exits unsuccessfully, its stderr is inherited and Python's
`subprocess.CalledProcessError` escapes `validation.validate` (which catches
only `OSError`, `ValueError`, and `KeyError`). Python emits **empty stdout,
exit 1, the real Git stderr bytes, followed by its uncaught traceback**.

Rust intentionally emits **empty stdout, exit 1, and those real Git stderr bytes
unchanged**, omitting only the Python traceback. It emits neither a JSON report
nor a human report, even when earlier validation issues were accumulated. This
is a separate `RenderError::Git(Vec<u8>)` channel, not a structured
`RENDER_FAILED`, trimming/decoding of stderr, or an exception for ordinary
renderer errors. Failed process spawning remains the ordinary renderer-error
path. `--no-generated-check` bypasses the Git renderer as in Python.

This authorization covers only unsuccessful Git revision lookup during public
validation. It does not authorize public `render`, source `update`, altered
lifecycle stderr/status, execution of Python by Rust, or normalization of any
other error. The frozen oracle still runs literally in the differential tests;
its traceback is asserted, not silently discarded from a generic comparison.

### Acceptance-to-test map

| Authorized edge | Executable evidence |
| --- | --- |
| Existing nonrepository and initialized repository without HEAD | `check_validate_parity.py::git_revision_rows`, actual Git commands in scratch, no mocks |
| Human and JSON modes, with and without an earlier validation issue | Eight real paired public rows; Python traceback and Rust's exact raw stderr/empty stdout/exit 1 asserted separately |
| No partial report or filesystem mutation | Every exception row compares complete fixture file kinds, modes, bytes, lexical links, inodes and timestamps before/after |
| Generated-check bypass | Same fixture with `--no-generated-check`, ordinary exact differential |
| Python 3.12 runtime contract, including subprocess `python3` | `scripts/parity_oracle.py`, invoked before parity fixtures/builds and unconditionally by the full parity gate; both mismatched top-level Python and mismatched PATH are rejected |

The E2 acceptance map and finite JSON evidence live in `SECTION_E_CONTRACT.md`.
Local evidence does not substitute for focused review or exact-SHA Ubuntu CI.
