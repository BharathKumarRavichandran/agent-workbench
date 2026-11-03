# Immutable managed bundles — Section D, unit 3

## Scope and authority

`src/bundles.rs` is an inert filesystem dependency, **not activation lifecycle
integration**. Its only callers are private `#[cfg(test)]` differential drivers.
The production hooks/MCP and schema-v4 lifecycle guards remain unchanged and
fail closed. Nothing executes checkout content or installs a checkout symlink.
Python production code is unchanged and remains the executable specification.

## Traced Python boundaries

* `agent_workbench/bundles.py`: `TreeFile`, `Tree`, `open_directory`, `read_tree`,
  `source_tree`, `tree_digest`, `write_tree`, `delete_tree`, `rename_new`.
* `installation._validate_install_source`: snapshot hooks again and compare its
  digest with the planned `DirectoryCopy` digest, including KEEP/ADOPT paths.
* `installation._DestinationRoot.same_resource/create`: compare installed tree
  digest; write a private staging tree, refuse foreign destinations, atomically
  rename without replacement, fsync the pinned parent, and clean staging on error.
* `_DestinationRoot.quarantine/restore/discard`: validate the moved tree against
  expected ownership evidence before deletion, restore on mismatch, and never
  recursively follow links. Full ownership/recovery orchestration is **not** D3.

## Snapshot and digest contract

The closed `Tree` owns private directory records and private `TreeFile` records.
Only descriptor-relative source traversal constructs production snapshots; no
public mutable fields or arbitrary path/content constructors are exposed.

Traversal opens every absolute ancestor with `DIRECTORY|NOFOLLOW`. Directories
record their exact ordinary permission bits; symlinks, FIFOs, sockets, other
special files and any `07000` permission bit are rejected. Regular files are
opened `NOFOLLOW|NONBLOCK`, rechecked for regular-file type, read as arbitrary
bytes, and rejected if inode, size, mtime or ctime changes across the read.
This intentionally matches Python's per-file check, not a stronger atomic
whole-directory snapshot guarantee.

`source_tree(hooks)` creates a synthetic root `("", 0755)` and prefixes the
actual hooks directory and descendants with `hooks`. The artifact's root mode
is retained at `hooks`; the synthetic root is not umask-derived.

Digest = SHA-256 of compact ASCII JSON of sorted records:

```
[relative-name, ordinary-mode-as-decimal, null-or-hex-SHA256(file-bytes)]
```

Sorting follows Python Unicode scalar order, including POSIX surrogateescape.
Raw POSIX filename bytes remain intact for syscalls. JSON matches
`json.dumps(..., separators=(',', ':'), ensure_ascii=True)`, including control
characters, DEL, non-BMP surrogate pairs and unpaired surrogateescape scalars.
The latter needs a bundle-specific encoder because serde JSON strings cannot
represent unpaired surrogates; the trust encoder remains private and unchanged.

`Tree::validate_source` resnapshots and rejects content/mode/tree changes against
the planned digest. `tree_digest` maps unsafe/missing/unreadable destinations to
`None`, so they never count as matching resources. Reuse requires a matching
source snapshot **and** installed digest; digest matching alone confers no trust.

## Publication, modes and cleanup

`write_tree` creates private directories at `0700`, files at `0600`, writes and
flushes exact bytes, fchmods every file to its captured mode, fsyncs files, then
fchmods/fsyncs directories in reverse traversal order. Effective published modes
are the captured source modes, not `source-mode & ~umask`. Tests distinguish
actual source creation under umasks `022` and `077` from final mode restoration,
including executable bits and read-only directories.

`publish` stages under an exclusively created process/serial name, then calls
`rename_new`: Darwin `RENAME_EXCL` / Linux `RENAME_NOREPLACE`, through rustix.
Existing destinations (including links) are never overwritten. The parent is
fsynced after publication. Failed staging or publication cleans only the newly
created private tree; a preexisting staging-name collision is not deleted.
Temporary spelling is not persisted. A collision fails closed rather than
assuming ownership. No ordinary overwriting rename fallback exists.

`delete_tree` pins each directory, adds owner `0700` for removal, inspects children
without following links, recursively removes only directories and unlinks other
entries. This supports read-only bundle directories without traversing outside
symlink targets. The caller still supplies an authorized pinned parent/name.

## Dependency obligations (not implemented by this unit)

D4/D5/D6 must provide document locks, authorized/pinned destination roots, live
root identity verification before/after mutation, authenticated ownership state,
quarantine restoration, rollback, transaction journals and crash recovery. In
particular, publication into a detached pinned root is possible just as it is in
Python's isolated primitive: the lifecycle caller must detect detachment and
remove its owned publication through pinned descriptors. The differential driver
exercises this primitive cleanup, **not a production recovery transaction**.
A post-publication fsync failure is likewise a later orchestration obligation.
No production code path is enabled by this module.

## Evidence and reproducibility

Run with isolated scratch storage (no real configs/credentials):

```
TMPDIR=/Users/teslash/.hermes/profiles/agent-workbench/cache/scratch \
  python3 scripts/check_rust_plan_parity.py
```

The parity gate explicitly invokes the ignored test-only Rust drivers:

* `differential_bundles`: 2 read-only Unicode/control/binary/mode snapshot rows;
* `differential_names`: 3 synthetic POSIX-name rows, including malformed UTF-8,
  surrogateescape ordering and truncated sequences. APFS rejects these raw names,
  so these digest rows do not claim macOS filesystem creation coverage;
* `differential_publication`: 34 paired rows under umasks `022` and `077`, comparing
  outcomes plus typed `lstat` snapshots of source, destination, outside sentinel
  and detached paths. Cases cover create, reuse, conflict/staging cleanup,
  foreign symlink, quarantine deletion, destination byte/mode edits, source
  byte/mode changes after planning, symlink/FIFO/special-mode/ancestor rejection,
  exact binary/Unicode bytes, immutable snapshots after source mutation, and
  deterministic mutation during a file read.

The Python side uses real `_DestinationRoot.create/same_resource/quarantine/
discard` and `_validate_install_source` in isolation. The only mock forces a
change at the per-file after-read `fstat` boundary. Rust has a matching test-only
seam compiled out of the production binary. In-read failures assert the exact
`bundle changed while reading` classification, not just rejection.

Observed REDs before implementation: absent snapshot backend returned null
instead of Python's digest; absent publication rejected Python's accepted create;
missing deterministic read-change seam accepted instead of rejecting; absent
POSIX-name backend returned null instead of Python's three digests. All are
covered by the executable GREEN differential rows. Lifecycle evidence is
reported separately by the existing fail-closed and supported skills/file-copy
matrix; it must not be interpreted as D5 activation integration.
