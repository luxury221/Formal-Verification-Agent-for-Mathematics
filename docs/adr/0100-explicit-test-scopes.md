# ADR 0100: Explicit source contracts and bounded real Lean smoke

Date: 2026-11-02. Status: accepted.

## Problem

The published default CI discovered 1,772 tests and failed with 193 errors and
eight failures. The logs included missing runtime snapshots, runtime-lock and
advanced adoption records that are intentionally absent from source clones.
Two real-Lean classes also lacked the opt-in decorator and attempted to prepare
their real fixtures during ordinary discovery. A source CI failure consequently
did not distinguish missing local prerequisites from software regressions.

## Decision

Keep the original contract bodies and verification providers. Add an explicit
runner and reviewed requirement registry. Source tests execute without local
runtime files; contracts whose original fixtures depend on adopted profiles are
individually deferred and disclosed. They are not relabeled as passed. Add the
missing real-Lean opt-ins. Unclassified tests run in the source scope and fail
normally. Classification drift, test import errors and empty inventories fail.

Retain exact imported-test deduplication in the runner to avoid running the same
base class through several importing modules. Different subclasses remain
different tests. Persist every selected and deferred ID, original skip reasons
and executed outcome. Presence checks are only an early diagnostic: they never
grant trust, create snapshots or alter mathematical evidence.

Separate a manual four-case actual Lean/replay smoke scope from complete local
historical experiments. A skipped smoke case is a failed acceptance scope.
Public base preparation does not prepare advanced profiles, historical ledgers
or particular cgroup arrangements. The new workflow must not run the entire
historical real suite after bootstrapping only the base environment.

## Consequences

A clean-clone source pass covers less than full local integration and explicitly
reports that limit. Deferred coverage remains visible and should shrink through
properly separated fixtures or reproducible profile preparation in later work.
This increment does not solve full-paper formalization or model integration.
Default CI results and real Lean results remain separate.

The only new external automation is pinned `actions/upload-artifact` v4.6.2, MIT,
for retaining software test reports. Its exact version, license and constraints
are recorded in [CI dependencies](../dependencies/ci-checks-v1.md).

Validation evidence: `artifacts/ci-suites-20261102-01/`. Failed attempts are retained.
