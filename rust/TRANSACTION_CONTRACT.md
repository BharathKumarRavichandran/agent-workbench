# D5 — transactions and authenticated recovery

**Local D5 acceptance gates pass; exact-SHA Ubuntu verification is pending.**
TODO D5 records the locally completed implementation; D6 is gated on Ubuntu CI.
Python production is the executable specification except for explicitly
authorized [E1/E2](PARITY_EXCEPTIONS.md).
Public executable activation remains fail-closed pending D6/D7. Serialized
Python plans are private test inputs, not independent Rust planning APIs.

## Implementation and oracle call graph

Python `installation.apply_plan` / `apply_uninstall` hold the state lock and call
`transactions.apply_lifecycle`. It takes the target document lock, recovers
pending authenticated intent, validates ownership/documents/sources/resources,
pins parents, signs proofs, and durably publishes the journal. It deactivates
slots before mutation and checks resources before activation, publishes ownership
state, and only then deletes and syncs the journal. Ordinary errors run real
authenticated recovery under the same locks; interruptions retain intent.
Rust's private equivalent is `transaction/apply.rs`.

`transaction/recovery.rs` authenticates before lock creation and again under both
locks. Caller-owned target/root policy bounds journal authority. Ownership-state
inspection precedes parent evidence and document/resource validation; opening
parent evidence preserves authenticated mapping order. Recovery validates live
and evidence images, deactivates slots, restores paths in reverse order, restores
documents/state, and finally removes the journal. Primitive restoration never
substitutes for authenticated orchestration.

`transaction.rs` supplies authority, journal publication, document publication and
locks. `path_recovery.rs` supplies typed capture/restoration/evidence validation.
`journal_wire.rs` preserves Python code points, unknown fields and key ordering
through full-record MAC verification; schema projection happens only afterward.

## Acceptance-to-test map

Differential drivers below run from `scripts/check_rust_plan_parity.py`; ordinary
cargo tests are identified separately. Ignored Rust drivers run through that
gate, not bare `cargo test`. Counts describe observed execution, not completion
criteria.

| Acceptance edge | Executable evidence | Verified scope |
| --- | --- | --- |
| Exact private-key type/mode/uid/link-count/size; unsafe authority rejection; target locks and missing-root behavior | `transaction_durable_rows`: 28 paired rows | Actual key/journal/lock operations and typed residue |
| Exclusive create/chmod/entropy; partial buffered writes, flush/close and journal cleanup failure; no silent authority repair | `authority_contract.py`: 12 rows | Independently reached faults, literal untrusted names/bytes/modes, retained key identity and real retry |
| Document freshness, capture/no-replace races, persistence and cleanup failures | `transaction_publication_rows`: 13 rows | Actual publication primitives; late document/root races also run through apply |
| Strict journal JSON, profile binding, MAC and tamper rejection | `transaction_journal_rows`: 12 rows | Real interrupted apply intent; this tracer's final recovery is Python-only |
| Surrogate keys/values and snapshot identity; UTF-8/16/32 surrogatepass; raw symlink capture/restoration, raw basenames and partial unencodable tree staging | `wire_contract.py`: 27 macOS rows; actual filesystem capability probes gate additional raw-filename publication rows | Actual authenticated recovery at original parent inodes; malformed encodings rejected; high-surrogate failure/residue and blocked retry match; Linux publication runs only after its real probe succeeds |
| Caller-authorized documents/resources/parents, MAC before locks and under locks, foreign images/evidence, interrupted recovery | `transaction_recovery_rows`: 71 rows | Actual install/uninstall journals, Cursor and multi-parent Claude, cross-engine interrupted recovery and real retries |
| Invalid private caller requests: target/root/document/resource binding | `request_authority_tests.rs`: 14 real-apply rejection rows in normal cargo tests | Rejection before lock/directory/key creation; foreign bytes/mode/inode preserved; explicitly not public target-planning coverage |
| Combined ownership/parent/document/resource defects and validation precedence | `recovery_order_contract.py`: 14 rows | Exact diagnostics and unchanged rejected fixtures; parent insertion order exercised |
| File/tree/link restoration, wire identity versus decoded bytes, aliases, partial evidence, staging conflicts, detached roots, named write/chmod/fsync boundaries | `path_contract.py`: 100 rows | Typed first-failure snapshots and unmocked retries, including correctly blocked partial stages |
| Mixed link/file/bundle/MCP apply; install/keep/adopt/replace/narrow/uninstall; relocation, stale snapshots, ordering, interruption/recovery and capture-parent substitution | `apply_contract.py`: 167 rows | Real Python-built plans consumed by actual Rust apply; target-native document/ledger/state bytes and modes, live/detached/outside snapshots and real retries |
| Tree staging file write/chmod/fsync, directory chmod/fsync and root-sync failure | Included in apply matrix: 12 paired rows | Partial-stage cleanup, authenticated ordinary rollback and fresh-plan retry |
| Actual apply capture/discard persistence protocol | `capture_contract.py`: 66 macOS rows | Real-rustix EIO canary; Python has zero capture/discard fsyncs and Rust matches, without changing journal/final-root/recovery synchronization |
| Integrated recovery capture/publication/evidence fsync, staging, discard, parent substitution and retained partial stages | `recovery_persistence_contract.py`: 50 rows (25 Cursor, 25 Claude) | Real interrupted Rust mixed-apply journals, symlink/file/tree rollback, later directory ordinals, directory/symlink parent substitutions, first-failure/outside snapshots, actual cross-engine recovery and fresh apply retries |
| State-parent replacement before/after rename/removal | `state_parent_contract.py`: 16 E1 rows | Outside bytes/modes/identity; retained authenticated evidence; exact parent reattachment; real recovery/retry; unsafe Python outcomes literal |
| Foreign replacement at consumed bundle staging name, also after post-rename fsync failure | `stage_cleanup_contract.py`: 8 E2 target/engine/phase rows | Exact foreign inode/bytes/modes; retained authenticated intent; actual Rust recovery, fresh retry and uninstall |
| Shared-slot handoffs, foreign fields/order, protected bundle references, grants and strict v4 | Existing D1–D4 matrices | Retained completed-unit coverage; public integration belongs to D6/D7 |
| All six skills lifecycles and copied-path/race/recovery behavior | Existing normal parity gate and Rust integration tests | Mandatory Section B/C preservation |
| Exact implementation SHA on Ubuntu | `validate.yml` → `rust-plan-parity` | Not yet obtained; no Linux success inferred from macOS |

## Snapshot and injection rules

Compare presence, lstat kind, permission bits, complete bytes/digests, lexical
links, live/detached/outside trees, quarantines, ledger and ownership state.
Authenticate journal bytes and original serialization before normalizing only
nondeterministic parent identities and authenticated quarantine nonces. Normalize
fresh key content only after its exact length is checked; failed/partial writes
and unauthenticated residual names stay literal.

Reach actual Python filesystem calls and corresponding private Rust seams.
Assert fault hits independently from recovery observation. An absent Python
syscall needs a real Rust syscall observer with a positive canary, not invented
mock hits or a substituted backend. Probe locks through independently opened
nonblocking descriptors. No production race-control flags are introduced.

Replay intent at original authorized parent inodes. Compare actual interrupted
Rust journals consumed by both engines, retained failure images, real recovery
and fresh-plan retry. Modified/partial staging blocks retry with authenticated
intent retained, rather than being silently repaired.

## Remaining acceptance edges and platform evidence

No named local transaction edge above remains failing. All five mandatory macOS
gates passed on the current implementation: fmt check; all-target clippy with
warnings denied; cargo tests (15 library and 15 integration tests, with 14 private
differential drivers ignored by bare cargo and exercised by parity); full parity;
and 188 Python unittests. One focused independent pre-commit reviewer found no
reproducible security or logic blocker and independently exercised the wire,
E1/E2, ordering and caller-authority rejection contracts.

Ubuntu verification remains mandatory for the exact implementation SHA. The
Linux x86_64 capture observer uses thread-following ptrace on the configured raw
syscall backend, with an EIO canary; it must not silently skip or substitute libc.
`wire_contract.py` probes actual filename support and runs real raw-filename tree
and basename publication rows where supported. macOS reports EILSEQ for both
file and directory creation probes; those rejected probes are not publication
coverage. No Linux success is claimed before CI execution.

After review, update only D5's TODO checkbox, make the focused implementation
commit, push, open the new unmerged PR, and inspect exact-SHA Ubuntu results before
D6. A remote failure reopens its concrete edge; local success is not a substitute.

Independent target selection/construction/trust/output belongs to D6/D7. D8's
execution/network tripwires remain a separate ordered unit. These deterministic
software tests do not claim arbitrary power-loss or hardware durability proofs.
