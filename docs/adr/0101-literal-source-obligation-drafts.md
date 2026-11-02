# ADR 0101: Prepare literal source drafts before semantic interpretation

Date: 2026-11-02. Status: accepted; additive unreviewed drafting only.

## Problem

After document extraction, users still need to copy every claim into an obligation.
A model endpoint has not been configured. Structural classification and lexical
patterns cannot establish the complete mathematical meaning of a paper.

## Decision

Prepare a deterministic inventory of all fixed fragments classified as claims.
Preview each complete literal statement, uniquely located explicit reference
targets, quoted definitions, lexical cues and nearby context. Keep premises,
quantifier interpretation and proof dependencies unresolved. Nearby text is
advisory. Ambiguous, missing, self-referential or unreadable references grant no
dependency. HTML/PDF references absent from extraction metadata stay absent.

Create selected drafts in batches of at most 16 through the existing dossier and
document leases, revision checks, idempotent requests and atomic envelope writer.
Validate every candidate before committing any. Bind a stable generated ID to the
source fingerprint and main block. Record original content and import provenance;
skip previously imported drafts without overwriting their edited versions or
semantic reviews. Reject collisions, stale plans, exhausted capacities and oversized
records. Never shorten a mathematical statement to fit a field or verifier budget.

Each draft starts with mandatory clarification questions. The existing separate
extraction and seven-part semantic reviews remain required. Draft preparation and
import execute no model, proof search, Lean worker or provider evidence read. Their
mathematical state remains UNKNOWN/E0. An empty or fully imported inventory does
not establish full mathematical coverage or validate the author's proof steps.

Expose `draft-plan`, `draft-preview` and `import-drafts` through the existing guarded
obligation routes and local UI. Invalidate the plan when the dossier changes and
bind it to the source, generator implementation and contract limits. Import receipts
are optional for old dossiers; old snapshots and proof records remain unchanged.

## Consequences

Users can begin from exact source packets and focus on missing mathematical detail.
No semantic accuracy or model performance claim follows from this convenience.
Source classification errors and missing cross-paragraph conditions still require
review. Future model proposals must use the same complete-obligation contract and
pass the same review and verification boundaries. No runtime dependency, sealed
provider, mathematical policy or adoption record changes in this increment.

Protocol: [document-obligations-v1](../protocols/document-obligations-v1.md).
