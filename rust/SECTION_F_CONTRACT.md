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

## Later units (not F1)

F2 relocation / interruption / recovery handoff, F3 platform primitives, F4
release executable, F5 invocation wrappers, F6 intentional differences, F7
default cutover, F8 Python removal.

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
The producer is current-tree Python whose package bytes must match
`04502a0054fcd3c666e06f4e20ef7603293cfed3`.
