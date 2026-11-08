# Content-bound trust contract (D2)

## Implemented primitives

The Python implementation remains the executable specification (`trust.py`,
`transactions._journal_key/_canonical`, and the grant call sites in
`installation.py`). Rust's trust module is private; the differential test driver
is test-only and cannot create authority or execute payloads.

A grant is `aw-trust-v1:` plus lowercase SHA-256 of compact, ASCII-escaped JSON:

```
[plugin_id, component, target, document,
 [repository, source_id, source_kind, source_path, source_upstream, source_branch,
  resolved_artifact, plugin_upstream, plugin_notice], content_digest]
```

Order matters. Optional provenance is JSON null. An explicit resolved artifact
wins over the resolved fallback; no artifact becomes the string `"None"`.
Path-valued arguments use Python's POSIX pathlib lexical spelling: repeated
separators, `.` segments, and trailing slashes collapse; `..` is retained; exactly
two leading slashes are retained. This is not canonicalization, symlink resolution,
or normalization of catalog source/notice strings. Control characters, DEL,
non-ASCII characters, and supplementary-plane surrogate pairs match Python JSON.
Plugin identity, source identity/provenance, repository, document, component,
target, artifact, and content are all bound into the token.

A persisted proof is `aw-proof-v1:` plus lowercase HMAC-SHA256 of sorted-key,
compact, ASCII JSON:

```
["aw-persisted-trust-v1", state_file_path, record_as_dict_without_trustProof]
```

The exact record wire representation is authenticated, including every emitted
field. A proof is not independently transferable to another state file, record,
key, or changed token. `trustProof` is excluded from the MAC, but the supplied
proof must itself exactly match the calculated value. Comparison uses HMAC's
constant-time verifier. Proof reuse occurs only when the prior record authenticates
and replacing the candidate's proof with the prior proof makes the records equal;
otherwise the original candidate is returned, including any existing proof.

The read-only key reader walks absolute parent directories from `/` using pinned,
no-follow descriptors; parent traversal and symlink ancestors are rejected. The
key name is `.<state-file-name>.transaction-key`. The open descriptor must identify
a regular file, exact mode 0600 (including rejection of special bits), current real
UID, one hard link, and exactly 32 bytes. Reading is bounded to 33 bytes, with
`O_NONBLOCK` to avoid hanging on FIFO replacements. Verification never creates or
repairs a key. Unsafe/missing keys make authentication false; proof computation
returns an error.

## Policy scope and caller preconditions

Explicit grants match opaque token strings exactly. Bundle-entry reuse requires
a matching token and authenticated prior entry. Its caller must select the prior
entry by the intended operation key, as Python does; authentication alone does not
perform lifecycle ownership selection.

Shared-slot reuse checks plugin, component, matching token, and authenticated
record. It may cover another slot name within that plugin/component. Python's
caller prefilters slots by component; Rust makes that precondition explicit in the
helper. Hook slots may alternatively reuse an authenticated entry selected by
`hook:<plugin>` and matching token. MCP cannot reuse hook bundle evidence.
No helper authorizes an arbitrary record merely because its proof is valid.

## Available CLI and dependency boundaries

Within the existing JSON-only Rust CLI and supported path-resource lifecycle,
`trust` performs read-only planning, with the same operation output/conflict exit
as Python (0 without conflicts, 1 with conflicts). It does not install, save state,
create a lock/key, repair recovery, or execute anything. `plan`, `status`, `install`,
and `trust` accept repeatable `--trust TOKEN`; tokens have no effect on these
non-executable resources, matching Python. `uninstall` does not accept `--trust`.
Existing profile/target/path-component and `--json` restrictions remain.

Cursor/Claude trust inspection checks the target's home or project journal location
independently of the skills-directory override. A pending journal rejects inspection
with exit 2 and the Python pending-recovery diagnostic, without reading or repairing
journal contents. Unsafe journal paths fail closed. Full descriptor-relative journal
snapshot validation and recovery diagnostics remain dependency-bound; this existence
guard is not journal authentication or transaction recovery.

Hooks/MCP requested explicitly or through profile configuration remain rejected.
All lifecycle operations, including `trust`, reject existing v4 state rather than
classifying, downgrading, or dropping its slots/proofs. D4/D5/D6 must supply validated
snapshots, transaction recovery, authority-key creation and durable proof persistence,
and executable activation planning before these CLI paths can be enabled. D2 does
not claim executable CLI activation or proof persistence support.

## Verification

`scripts/check_rust_plan_parity.py` exercises Python and Rust directly. Token rows
cover both executable component identities and every source kind. Proof rows cover
authentication/reuse, explicit grants, scope, transplanting, unsafe keys, and an
independent mutation of every emitted record field (including proof). Mutations
that also violate state-shape invariants occur after strict fixture decoding to
isolate the authentication primitive; strict malformed-state coverage remains in
the existing state-wire matrix. Typed snapshots assert file kind/mode, UID, link
count, bytes, and lexical symlink targets without following links or opening FIFOs.
Available CLI rows compare exact normalized stdout/stderr/exits and snapshots for
all six skills targets, clean/installed/conflicting state, repeated grants, and
pending Cursor/Claude journals. Dependency-bound drivers are explicitly invoked by
the parity script even though ordinary `cargo test` marks them ignored.
