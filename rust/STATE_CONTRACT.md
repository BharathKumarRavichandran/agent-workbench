# Installation state wire contract (Section D, unit 1)

The executable specification is `agent_workbench/state.py:load_state` and
`InstallState.as_dict`; Python production behavior is unchanged. The differential
matrix in `scripts/check_rust_plan_parity.py:state_wire_rows` invokes a test-only
Rust driver and compares acceptance, rejection diagnostics/error classes, and
pretty-printed round-trip bytes. Multi-invalid fixtures verify Python's validation
order, including insertion-order traversal and nested duplicate-key precedence.

* Versions 2, 3 and 4 are integers (not booleans, floats or strings). Objects
  reject duplicate keys recursively and reject unknown/missing fields.
* v2 contains skill symlinks with four entry fields. v3 adds component roots,
  typed resources and source repositories. Only v4 permits hook directory copies
  and requires `slots`, including when empty.
* Directory copies require lowercase SHA-256 digest and syntactically valid
  `aw-trust-v1:<64 lowercase hex>` evidence. `trustProof` is optional, but a
  present null or malformed `aw-proof-v1:<64 lowercase hex>` is rejected.
  Evidence fields are not allowed on symlinks or file copies.
* Shared slots require mapping/embedded identity `component:plugin:slot`, hooks
  or mcp component, identifier plugin, nonempty opaque slot name, absolute
  document, digest and trust. Proof is optional, never nullable. Slot names are
  not filesystem paths: even `../server` is valid when the identity agrees.
* Paths reject NUL, relative absolute-path fields and parent traversal. Source
  paths follow PurePosixPath lexical normalization (`./plugins//example/` is
  accepted and preserved); they do not resolve filesystem objects.
* Entry prefix/component/resource agree; only skills are symlinks and only hooks
  are directory copies. Mapping key equals embedded key and destination basename.
  Every entry is a direct child of its declared component root, destinations are
  unique, and the skills root equals destinationRoot.
* Loading is syntactic, not authorization. Arbitrary absolute slot documents and
  well-shaped but unverified grants/proofs load successfully. Python callers in
  `installation._validated_state` authorize metadata, roots and shared documents;
  activation/trust code verifies executable evidence separately.

Rust `lifecycle` loads and rechecks state before applying operations. Until the
remaining Section D execution/transaction units are ported, the CLI rejects all
v4 lifecycle plans (including status and uninstall) before classification or
mutation. This intentionally conservative temporary boundary prevents silent
slot loss, downgrade to v3, or treating parsed evidence as execution authority.
The differential gate separately checks that rejection leaves state and an
owned symlink untouched. v2/v3 lifecycle compatibility remains enabled.

This unit does not implement v4 activation planning, grants/proof verification,
shared-document edits, transactions/recovery, or executable uninstall. Rejection
fixtures compare Python ValueError messages exactly; no diagnostic normalization
is applied. Existing CLI differential rows additionally compare exit status and
stdout/stderr, state bytes, modes, and typed filesystem effects.
