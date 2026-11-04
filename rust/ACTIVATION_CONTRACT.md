# D6 — independently planned Cursor activation

Python production is the frozen executable specification, apart from the
explicitly authorized [E1/E2](PARITY_EXCEPTIONS.md) transaction safety exceptions.
D5 is complete; its primitive/plan-fed evidence is retained, not substituted for
public activation planning. Claude activation remains fail-closed until D7.

## Implementation boundary

`activation.rs` constructs target-native hook bundle entries, content-bound
grants, shared-document slots/ownership plans, source snapshots and D5 apply
requests independently. `activation_commands.rs` compiles local hook payload
references without executing them. Public `plan`, `trust`, `status`, `install`
and `uninstall` use the same planning path. Pending journals are recovered before
source inspection; read-only trust refuses pending recovery without mutation.
The existing pinned-descriptor transaction/recovery machinery remains the only
activation mutation path. There are no public fault-control options or Python
plan inputs.

## Acceptance-to-test map

All three public differential scripts are invoked from
`scripts/check_rust_plan_parity.py`. Fixtures live beneath explicit scratch
`TMPDIR`, never real installed target roots. Both engines replay at identical
lexical paths with a private deterministic test key, comparing operation output
bytes/order, stderr, exit status, typed filesystem contents, permission bits,
and lexical symlinks. Python bytecode generation is disabled in child CLIs so
interpreter cache files do not masquerade as lifecycle mutations.

| Contract | Executable evidence |
| --- | --- |
| Untrusted plan, read-only trust, explicitly granted plan/install, keep/status/reinstall, uninstall and post-uninstall plan | `check_cursor_activation_parity.py`: mixed, hooks-only, MCP-only, user layout and shell-hook command tracers |
| Trust inspection inode/mtime/ctime/mode/size preservation and inert hook sentinel | Every trust step in both public scripts; not a substitute for D8 endpoint/execution proof |
| Native Cursor alias and fallback hook representation, ambiguous exact representations, empty bundles | `check_cursor_activation_edges.py`: native-hooks, native-ambiguous, empty-hooks; baseline fallback |
| Explicit CLI overrides defeating disagreeing profile components, project root and destination settings | profile-overrides |
| Mixed skills/copied agents/rules/hooks/MCP and project requirement rejection | mixed-paths, missing-project |
| Foreign document fields, entries, ordering and bytes | foreign-fields, foreign-document |
| Changed executable content requires renewed grant, old token does not authorize | changed-trust |
| Component selection narrowing removes old bundles and slots | narrowing |
| Modified bundle preservation, surviving foreign bundle references block removal | foreign-bundle, referenced-bundle |
| Forged slot document cannot become mutation authority | forged-state; scratch outside sentinel remains unchanged |
| Custom profile, disjoint profiles sharing documents, owned-slot adoption rejection and real ownership handoff | custom-profile, ownership-handoff, owned-slot-adoption |
| Hook escape/absolute/wrong-target/missing/empty payload rejection before mutation | reject-hook-* plan/trust/status/install rows |
| Standalone MCP local payload/argument and plugin-variable rejection before mutation | reject-mcp-* plan/trust/status/install rows |
| Inspector-level malformed MCP and incompatible representation multiplicity | inspect-mcp-name/object/missing, inspect-incompatible-many |
| Combined malformed requested inventory plus invalid old state, exact diagnostic order | order-hooks, order-mcp through plan/trust/status/install |
| Real interrupted independent Rust planning, authenticated retained intent and public recovery/retry | `check_cursor_activation_recovery.py`: install/uninstall × durable-journal/before-state; private ignored `activation_parity_tests::differential_public_activation` receives public CLI arguments only |
| Pending journal trust inspection is read-only; status performs recovery; retry/uninstall succeed | Same four paired interruption rows; journal MAC, wire formatting and original parent identities validated before nonce/inode normalization |
| D5 recovery/persistence/race contracts and E1/E2 | Existing mandatory gate, unchanged oracle and paired exception regressions |
| All six skills targets, Section C copy paths/races/recovery | Existing mandatory gate; Claude-only fail-closed assertions retained until D7 |

## RED → GREEN evidence

The inherited D5 baseline rejected executable components instead of producing
Python's untrusted operations. The resumed first tracer exposed only Python
interpreter caches in isolated HOME, not a lifecycle mismatch; disabling those
caches yielded the first nine-row end-to-end GREEN. The next shell-hook tracer
exposed command rewriting/slot-digest drift and was fixed against the oracle.
The forged-state public edge separately exposed missing adapter-authorized slot
path validation before ownership classification; it now matches Python's
rejection rather than returning a lower-priority ownership conflict.

## Delivery gate

D6's five mandatory macOS gates pass: format check, all-target Clippy with
warnings denied, Cargo tests (15 library + 15 integration tests; 15 private
drivers ignored by bare Cargo and invoked by parity), complete public/private
parity, and 188 Python tests. The independent review reproduced validation-order
drift; the parent reproduced it, added eight paired combined-defect rows and
corrected inventory-before-state while retaining recovery precedence. Focused
independent correction recheck passed, including late compiler/payload validation
and recovery-precedence probes. Exact-commit Ubuntu verification is
required after the D6 commit and before beginning D7. Local results do not imply
Ubuntu success. D8's full execution/network tripwires remain a separate unit.
