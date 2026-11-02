# Explicit software and Lean test scopes

The runner uses Python's standard library. It does not change the verifier,
replace missing adoption records, reseal a runtime or promote mathematical evidence.

| Command | Actual scope | Missing requirements |
| --- | --- | --- |
| `python3 ops/scripts/run_tests.py --suite source` | Source contracts; integration flags must be disabled | Registered runtime contracts are individually deferred, never passed |
| `python3 ops/scripts/run_tests.py --suite prepared --list` | Complete inventory only; no fixtures or checks | Requirements appear in the inventory; this is not a successful test run |
| `python3 ops/scripts/run_tests.py --suite prepared` | All contracts, including original environment-dependent fixtures | Preflight fails before execution and lists missing prerequisite files |
| `MRA_REAL_LEAN=1 python3 ops/scripts/run_tests.py --suite lean-smoke` | Four exact Nat/independent replay tests from existing real integration classes | Missing flag/files fail preflight; errors, failures or any skip fail the scope |

The source suite refuses enabled `MRA_REAL_LEAN`, `MRA_DOCUMENT_RUNTIME` and
`MRA_REAL_EVIDENCE_READ` before importing test modules. Original integration
decorators remain active; two previously ungated real-Lean classes now require
`MRA_REAL_LEAN=1` as well. Real PDF/OCR remains separately opt-in as described in
the README. No model is configured or evaluated by these entry points.

`tests/suites.json` records exact class or method selectors, prerequisite file
names and a specific reason. An imported base test class is run once per exact
test identity. A distinct subclass and its inherited tests retain their own IDs.
Renamed, deleted, overlapping or unknown selectors fail classification. Newly
added unclassified tests enter the source scope; their failures are not converted
to skips. Test import errors fail discovery. Every discovered unique test is
selected or deferred, and duplicate imports are explicitly recorded.

Preflight checks file **presence**, not trust, validity or completeness. The
original tests and verifier still check integrity. Advanced adoption records are
not in Git, and public automatic preparation for all profiles is not available.
Tests using private historical proof IDs, cgroup limits or a specific host remain
local experiments; the basic smoke scope makes no claim to cover them. Full
discovery with `MRA_REAL_LEAN=1` is not a portable acceptance command.

The four smoke checks cover a positive Nat proof in two processes, a verified
counterexample, failure staying UNKNOWN, and the binding of an independent replay
receipt. They are existing authored regressions, not an independent mathematical
benchmark or a measurement of model capability. A source-suite pass proves none
of these real executions occurred. A smoke-suite pass does not certify the
advanced profiles or a complete paper.

Each invocation writes a new JSON report below `artifacts/test-suites/`, or a new
explicit path supplied with `--report`. Existing reports and paths outside the
project's `artifacts/` are refused. Reports retain selected IDs, deferred IDs and
reasons, duplicate imports, actual successes, skips, errors and failures. Exit
codes are 0 for success or inventory-only, 1 for executed test failure, and 2 for
configuration/preflight/discovery errors, or 130 for interruption. Read `status`:
`INVENTORY_ONLY` is not `PASSED`. A run with no successful tests cannot pass.

GitHub's default `contracts` job uses a clean source checkout. The manual
`real_lean=true` job prepares the pinned base runtime, then uses only the explicit
smoke scope. Reports are uploaded even after test failure with a 14-day retention;
GitHub retention and job timeout can limit availability. No private ledger,
imported paper, runtime archive or model configuration is uploaded. Review test
output before changing the report fields to include any additional information.

Decision: [ADR 0100](../adr/0100-explicit-test-scopes.md).
