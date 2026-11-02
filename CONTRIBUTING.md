# Development and verification

Use Linux or WSL Ubuntu 22.04 with Python 3.10 or later. The core uses the Python
standard library; preparing real verification also requires the pinned Lean and
Mathlib toolchain described in the README.

Run the source gate and the explicit source test suite before proposing a change.
The suite inventories every discovered test. Contracts requiring adopted local
profiles are individually deferred with their requirements and reasons, and
integration opt-ins stay skipped. Neither category is counted as passed:

```bash
python3 ops/scripts/check_source.py
python3 ops/scripts/run_tests.py --suite source
```

Inventory the environment-dependent contracts without running fixtures:

```bash
python3 ops/scripts/run_tests.py --suite prepared --list
```

After preparing and sealing the pinned base runtime, run the four real Lean and
independent replay smoke checks with
`MRA_REAL_LEAN=1 python3 ops/scripts/run_tests.py --suite lean-smoke`.
Every selected smoke test must pass; a skip fails that scope. GitHub Actions offers
the same scope through manual dispatch with `real_lean=true`.

`--suite prepared` runs all contracts after checking the listed environment
prerequisites; real integrations still require their original opt-in flags.
Advanced experiments may also require private historical ledgers, profile-specific
adoption and host resource limits. Direct `unittest discover` is retained as a
diagnostic and does not provide a source-only gate. Do not use
`MRA_REAL_LEAN=1` with full discovery on a fresh clone and describe that as a basic
smoke test. Reports preserve selected, deferred, skipped and failed cases under
`artifacts/`. See [test scopes](docs/protocols/test-suites-v1.md).

Review code changes yourself, especially cancellation, recovery, upgrades and
rollback. Retain failed-run evidence and describe the exact validation scope.
Keep execution status separate from mathematical judgment. PROVED and REFUTED
require real checking artifacts and a version-bound semantic alignment; a model
response or mock cannot substitute for them. A failed proof attempt is not a
counterexample.

Record major decisions in `docs/adr/` and local validation evidence in `artifacts/`.
Check versions, licenses, uses and restrictions before adopting dependencies.
Do not incorporate an implementation under a noncommercial license into the core
without the necessary authorization. Keep imported papers, local model settings,
credentials, research ledgers and machine-specific agent instructions out of Git.

Third-party notices are retained with their assets and in `docs/security/` and
`docs/dependencies/`. No new license for the project's original code is selected
by this publication.
