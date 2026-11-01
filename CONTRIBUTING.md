# Development and verification

Use Linux or WSL Ubuntu 22.04 with Python 3.10 or later. The core uses the Python
standard library; preparing real verification also requires the pinned Lean and
Mathlib toolchain described in the README.

Run the source gate before proposing a change. The full suite below currently
includes tests that require a prepared, sealed runtime even without the real-Lean
opt-in flag. A source-only clone does not provide those snapshots or advanced
profile adoption files, so full discovery and the default CI can fail on missing
environment prerequisites. Retain and report those failures; do not describe this
command as an environment-independent contract gate:

```bash
python3 ops/scripts/check_source.py
python3 -m unittest discover -s tests -v
```

Real Lean tests are a separate opt-in after runtime preparation:

```bash
MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v
```

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
