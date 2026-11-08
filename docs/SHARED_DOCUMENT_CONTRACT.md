# Shared JSON documents — Section D, unit 4

## Scope and authority

`src/shared_document.rs` and its `json`, `ownership`, `snapshot`, and
`references` modules implement inert document dependencies. Their callers are
private differential tests, not production activation lifecycle commands.
Python `agent_workbench/shared_config.py` remains the executable specification;
no Python production behavior is changed. Hooks/MCP and schema-v4 lifecycle
commands still fail closed. A proposed slot, ledger record, or matching digest
is not authenticated authority and does not permit filesystem mutation.

## Slot planning and ordering

`plan_document` accepts the original optional **bytes**, desired slots with
explicit grant decisions, prior slot state, adoption policy, and target. It
returns separate removal and final optional byte images, ordered operations,
and proposed next slot state. It never reads a source tree, compiles a hook,
executes a command, writes a document, or creates a grant.

* MCP owns individual names in `mcpServers`; hooks own digest-identified entries
  within an event array. Foreign fields, unmanaged slots, native Claude prompt
  handlers, and unrelated event entries survive a logical change.
* Unchanged owned slots keep; unchanged obsolete slots remove; externally changed,
  missing, or ambiguous owned slots conflict. Matching unmanaged slots require
  adoption. Duplicate desired slots and duplicate matching hooks conflict.
* Prior removals/conflicts use sorted state keys. Desired operations and hook
  additions use caller order. Source object insertion order is retained for
  rewritten documents; setting an existing field does not relocate it.
* Trust rejection overrides collision, ambiguity, adoption, and keep decisions.
  Proposed next slot state is not a permission to persist it after a conflict.
* Cursor hook version requires the integer `1`, not `true` or `1.0`. Claude
  matcher groups and native handlers are validated without translating their
  representation into Cursor commands.
* Operation JSON field order matches Python `SlotOperation.as_dict`, including
  explicit null diagnostics/tokens and explicit trust-required booleans.

## JSON bytes, equality, and canonical numbers

`state::wire::Wire` retains object insertion order and rejects duplicate keys at
any depth. Children finish decoding before an outer duplicate error, matching
Python's `object_pairs_hook`; the existing strict state diagnostic matrix remains
a gate. Raw JSON containers are decoded explicitly as maps/sequences, never as
serde's synthetic arbitrary-precision number map. Real user objects are not
reinterpreted as numeric metadata.

No-op planning returns the exact original bytes, including whitespace, newline
count, Unicode escaping, and encoding/BOM. Absence is distinct from an empty
file or an empty object. Logical equality follows Python recursively: object
order is irrelevant, array order matters, booleans compare numerically, signed
float zero compares equal, and integral floats compare to integers by their
**exact binary64 integer value**, not by a rounded integer-to-float conversion.
Distinct large integers never collapse to a common float. Equal replacement
values can therefore produce a remove/create plan while preserving final bytes.

A changed document is UTF-8, two-space indented JSON with insertion-order object
keys, literal Unicode, and one trailing newline. Digest input is sorted-key,
compact ASCII JSON without a trailing newline, hashed with SHA-256. These are
different encodings and must not be interchanged.

Integer tokens use serde JSON arbitrary precision and stay decimal integers in
both renderers, including negative values and values beyond binary64 range;
integer rendering and integer comparison do not pass through `f64`. Decimal or
exponent tokens instead parse as binary64, with float-roundtrip parsing and
Python's shortest formatting, notation thresholds, signed zero, and padded
exponents. Canonicalization normalizes integer `-0` to `0`. Float and integer
digests remain representation-sensitive even where Python equality is true.

## Snapshots and byte encodings

Snapshots pin the parent directory through nofollow ancestor traversal, open the
entry `RDONLY|NOFOLLOW|NONBLOCK`, require a regular file, and compare device,
inode, size, nanosecond mtime, and nanosecond ctime before/after reading. Missing
entries or missing parents produce absent snapshots. Symlinks, FIFOs, directories,
invalid JSON, and in-read drift reject without writes. No atomic whole-path or
whole-transaction snapshot guarantee is claimed.

The snapshot stores original bytes. JSON decoding handles UTF-8 with/without BOM
and UTF-16/UTF-32 little/big endian, using BOM or JSON null-byte detection. Invalid
UTF sequences reject. A no-op preserves the input encoding; a rewrite emits the
UTF-8 format above. Ownership planning and the reference guard use the same byte
decoder, rather than requiring a lossy intermediate UTF-8 string.

## Multi-owner ledger

The ownership ledger is separate from per-profile slot state. Its slot identity
is the SHA-256 digest of canonical JSON `[document, component, slot]`; a record
binds an owner state-file path and slot digest. Snapshot validation checks the
exact ledger/record shape and lowercase SHA-256 spelling. As in Python, ledger
version equality accepts `true` and `1.0`, unlike Cursor hook version validation.

Prior records require matching owner **and** digest; missing or changed authority
produces `SLOT_OWNERSHIP_MISMATCH` and preserves the original ledger bytes.
Removal affects only this profile's records. Another profile's claim produces
`SLOT_OWNED`, including when only part of a multi-slot proposal is rejected;
unrelated owners survive. The returned plan is provisional. No record is
published and no authority is synthesized by these functions.

## Bundle reference guard

Before later lifecycle code deletes a managed bundle, the guard scans direct
Cursor commands and nested Claude handlers, including foreign/native entries.
It checks the absolute bundle and relevant config-relative spellings, raw
commands and POSIX-shlex-style token candidates, with path boundaries to avoid
matching similarly prefixed bundles. Malformed shell quoting still leaves the
raw command available. Invalid event/entry structure rejects in traversal order;
a reference found earlier can return before a later malformed entry, as in
Python. This is a conservative reference predicate, not shell execution or a
proof that removal is authorized.

## Explicit D5 and later dependencies

D5 must provide document/ledger locks; authorized pinned destination roots;
source and installed bundle validation; complete revision revalidation at each
mutation; compare/capture/publish without overwriting foreign arrivals; file and
parent fsync; quarantine restoration and cleanup; transaction journals; durable
state/ledger/document sequencing; rollback and restart recovery; and cleanup of
owned publications in detached roots. The reference predicate must be applied
to the effective documents before bundle removal. A plan alone establishes none
of those guarantees.

D5/D6 must also verify persisted content-bound grants and ownership proofs,
create authority only inside the authorized transaction, apply target policy and
source compilation, and integrate plan/status/install/uninstall before enabling
hooks/MCP or v4 lifecycle commands. D4 does not discard v4 evidence or downgrade
it to make existing lifecycle commands appear supported.

## Verified evidence

Run from the repository root:

```
TMPDIR=/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch \
  python3 scripts/check_rust_plan_parity.py
cargo fmt --manifest-path rust/Cargo.toml -- --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
TMPDIR=/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch \
  cargo test --manifest-path rust/Cargo.toml
```

The parity gate explicitly runs the otherwise ignored private Rust driver:

* 472 document/encoding/digest rows compare byte arrays, operation values and
  compact ordered operation JSON, and next slot state. They include foreign
  positive/negative 41-digit integers through no-op and rewrite; exact and unequal
  huge integer/float replacements in both directions; integers beyond binary64
  range; booleans/signed zero; and 256 seeded binary64 candidates (finite only).
* 11 ledger rows cover multi-owner retention, multiple prior slots, multiple
  conflicts, partial rejection, adoption, removal, and authority mismatch.
* 18 read-only snapshot rows cover absent/regular entries, nofollow/special-file
  rejection, deterministic in-read mutation, ledger shape, BOM/UTF encodings,
  and invalid UTF-8. Typed metadata/content snapshots prove inspection does not
  mutate fixtures apart from the explicitly injected adversarial write.
* 39 reference rows cover direct/nested native/foreign commands, path boundaries,
  quoting/escaping, malformed structure/traversal order, and BOM/UTF encodings.

Observed regression REDs: two foreign integer rewrites rounded to `1e+40`;
five large-number equality replacement rows wrongly preserved original bytes;
and five encoded reference rows missed live bundle references. All have paired
Python/Rust GREEN cases. The 366-row strict state diagnostic gate and all prior
bundle/trust/supported lifecycle gates are separate required regressions, not
claims of production D5 activation or crash recovery coverage.
