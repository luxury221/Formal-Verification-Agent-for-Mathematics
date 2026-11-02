# ADR 0099: Fixed source snapshots and complete mathematical obligations

Date: 2026-11-01. Status: accepted; additive source/verification bridge.

## Problem

A structural block is not necessarily a complete mathematical obligation. A claim
can depend on a definition or condition in another paragraph, and a single block
can contain several claims. The previous document-to-article export preserved
unclassified text but did not provide a many-to-many semantic correspondence.

## Decision

Add a separate, versioned obligation dossier. Freeze the exact document revision,
original-file hashes, extracted and corrected fragments, source spans and suggested
page mappings in an immutable source envelope. Store complete obligations with
objects/types, definitions, quantifiers, all assumptions, every conclusion,
references, prerequisite obligations and open clarification questions. Fragments
have explicit roles and may support several obligations.

Keep extraction confirmation, semantic acceptance, execution and evidence return
separate. Semantic acceptance requires a named local reviewer, seven explicit
confirmations and no unresolved questions. It grants UNKNOWN/E0 only. Acceptance
binds the fixed source, the obligation version, transitive prerequisite versions
and the latest relevant extraction reviews. Changing then restoring content or
withdrawing then re-accepting a review cannot revive an old workspace.

Generate the research question deterministically from all mathematical fields;
never truncate a condition to fit a bridge budget. Prerequisite claims and cited
lemmas are research context, not additional assumptions. Reuse the existing Studio
and verifier providers without modifying their sealed mathematical code, templates,
profiles, policies or proof records. Unsupported targets stay unverified.

Return only a current PROVED/E4 final node actually transferred from the bound
Studio notebook. Check its exact complete original text, plan, checked target,
attempt identity and artifact hashes through the original provider. Require a new
named correspondence review for the return. Ordinary GETs show links as historical
NOT_RECHECKED observations with UNKNOWN/E0 and do not start an expensive provider
read. An explicit POST rechecks one obligation through the existing bounded
evidence reader (300 seconds per graph); only that response may grant current E4.
Reloading returns to history. Unavailable or stale sources, reviews, plans or
artifacts suppress the current verdict; changes during a read discard the
observation. The dossier's whole-article status always stays unverified.

The DTO is also a contract for future model proposals. This increment does not
call a model, generate semantic interpretations automatically, certify the author's
individual proof steps, or support automatic REFUTED returns.

## Storage and compatibility

Use existing atomic envelopes, local directory leases, strict IDs, bounded JSON,
request idempotency and optimistic revisions. Lock the document during semantic
commits so a concurrent extraction edit cannot authorize an old revision. Keep
previous obligation versions, reviews, workspaces and evidence links for inspection.
A new document revision needs a new dossier; old snapshots remain readable.
Saving identical content does not create a gratuitous new semantic version.

No runtime dependency is added. The existing local Host/Origin/CSRF/CSP guards and
escaped text/math rendering apply. Named reviewers remain local assertions without
account authentication. The trusted local filesystem and original providers retain
their existing trust boundaries.

Protocol: [document-obligations-v1](../protocols/document-obligations-v1.md).
Local validation is retained in `artifacts/document-semantics-20261101-01/`.
