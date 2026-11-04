# D8 — activation inertness proof

D8 began only after D7 commit `ca427d53d948767a213a16686c67fe526d84cfed`
passed every Ubuntu job in [run 41794499584](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41794499584).
D6 was separately verified at `7075849d3a23c080f1874646469ee72b806c3900`
in [run 41791075323](https://github.com/BharathKumarRavichandran/agent-workbench/actions/runs/41791075323)
before D7 began. Python production and the narrow authorized E1/E2 differences
remain unchanged. D8 adds proof tests, not new production behavior.

## Real observers, not mock execution assertions

`scripts/check_activation_inertness.py` creates disposable fixtures only beneath
explicit scratch `TMPDIR`. For each native target and engine it declares:

- A harmless Python hook payload inside the managed bundle source, which writes
  `HOOK_EXECUTED` and connects to the isolated loopback listener if run.
- A harmless stdio MCP command whose script writes `MCP_STARTED` and connects to
  that listener if started. Its absolute fixture script is outside the plugin
  source root, preserving the oracle's standalone MCP validation contract.
- A real HTTP-style MCP URL on an ephemeral `127.0.0.1` listener. The listener
  captures any connection, including clients that send no HTTP data.
- Executable-looking Markdown, prompt and opaque copied text containing shell
  substitution, HTML/JavaScript and Python import/file-writing strings. Every
  executable-looking marker uses an absolute fixture path, so detection does
  not depend on a subprocess's working directory. These
  are always data and are never run, even in the positive controls.

The test explicitly runs only the harmless executable canaries as positive
controls. Each must make the filesystem execution observer fail (RED), and the
real socket must receive its exact canary bytes. It clears the canary evidence
before exercising application behavior. It also executes the exact installed
hook command as a final positive control, proving the copied payload remains
executable rather than accidentally testing an inert/nonexistent command.
These intentional test-owned canary executions are not application execution.

The observers are checked after every application operation. Public operation
subprocesses have a bounded timeout so an accidental server connection cannot
hang the test indefinitely. No declared external server, actual host agent
configuration, credential or network endpoint is used.

## Acceptance-to-test map

| Contract | Actual executable evidence |
| --- | --- |
| Validation reads executable-looking source without executing or connecting | Python's real `validation.validate(..., check_generated=False)` and Rust's real native `activation::inspect_requested` through ignored test `activation_parity_tests::inert_native_validation`; fixture inode/mtime/ctime/mode/size unchanged |
| Untrusted plan, trust inspection, status and failed install cannot execute/start/connect or grant implicitly | Four actual public CLI phases per engine/target; exact output plus whole-fixture typed snapshots; private key and ownership state remain absent |
| Grant-token inspection is not a grant | Real public plan supplies tokens; no filesystem grant or state appears until explicit installation |
| Explicitly granted installation is inert | Actual mixed skills/copied paths/hooks/MCP install; exact state/document bytes/modes and actual copied hook bytes; observers remain quiet |
| Persisted trusted plan/trust/status/reinstall remain inert | Four actual public CLI phases; signed ownership state remains byte-identical |
| Changed content invalidates persisted/old grants without implicit renewal | Four real plan/trust/status/stale-token-install phases; `TRUST_REQUIRED` and unchanged saved state; no executable/network activity |
| Uninstall is inert | Real public uninstall after changed source; observers remain quiet |
| Both target-native policies and both independent public implementations | 30 paired public CLI rows plus native validator executions, at the same lexical paths with deterministic test authority after the untrusted phase |
| Native validation and public command scope stay distinct | Rust public `validate` is explicitly asserted fail-closed; full public `validate` remains Section E, not silently ported or claimed by D8 |
| Coverage runs in Ubuntu CI | `scripts/check_rust_plan_parity.py` invokes the inertness script and private validator unconditionally, alongside all D5–D7 and E1/E2 suites |

The test's initial implementation passed the existing inert production behavior;
no production defect or new behavior was invented to manufacture RED. Real
positive controls independently demonstrate observer sensitivity. The proof is
bounded deterministic executable/endpoint coverage of the supported lifecycle,
not a claim to cover arbitrary hardware faults, scheduling or every possible
future implementation.

## Delivery gate

All five mandatory local macOS gates passed: format check, all-target Clippy
with warnings denied, Cargo tests, the full parity matrix including the
inertness script/private validator, and 188 Python tests. Focused independent
D8 review passed with no blocking findings; its marker-path strengthening
suggestion was applied and all five gates rerun successfully. The focused
commit requires exact-SHA Ubuntu verification. PR #7 must remain open and
unmerged; Section E is not part of this work.
