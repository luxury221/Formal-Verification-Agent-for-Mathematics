# ADR 0102: Locate explicit HTML references without adopting proof dependencies

Date: 2026-11-02. Status: accepted; source locations only.

## Problem

Literal obligation drafts can use explicit LaTeX labels, but the existing HTML
extraction supplies no link metadata. Users must find referenced equations and
definitions manually. Structural links alone cannot establish mathematical scope,
complete hypotheses or the correctness of a cited statement.

## Decision

Enrich newly imported HTML with local anchor aliases and link observations using
the existing bounded structural parser. Preserve its blocks, classifications,
quotes, source spans and hashes. Bind the helper and structural implementation in
extraction provenance; also bind the helper to the draft generator fingerprint.

Resolve only fragment-only links, exact uploaded filenames or exact citation URL
identity. Do not fetch targets, equate paper versions or adopt relative URLs under
an HTML base. Reject ambiguous attributes and duplicate IDs, even outside the
article. Ignore navigation, script, style and template sources. Map an inner ID to
its nearest existing full fragment. Only a unique outermost descendant fragment
can stand for an unindexed wrapper; sections and multiple fragments stay unresolved.
Use element identity when traversing ancestors so the synthetic root cannot be
confused with a real block at source offset zero.

Record parsed href, label, link span, status, target span and target block span.
All spans use original Unicode code points and half-open intervals. In-document
self references retain their locations but add no missing prerequisite because
the complete primary statement already contains that target. Unresolved and
foreign links remain explicit questions. A corrected statement retains original
HTML provenance and requires a new applicability check.

Uniquely located non-self targets may add complete definition quotes or context
sources to unreviewed drafts. Never infer premises, quantifiers or proved
dependencies. Both document and draft UIs expose local target navigation and
inert link text. Existing semantic review and verifier policies remain required.
All results from this step remain UNKNOWN/E0 with whole-article verification false.

Fail explicitly above 4096 fragment/link observations, 8192 located aliases or
2000 characters per href. The existing input fallback order and record byte limits
still apply; partial metadata is not published as a successful HTML extraction.
No new package, network fetch, model invocation or Lean execution is introduced.

## Compatibility and limits

Existing fixed documents, dossiers, edits, reviews and evidence are not reparsed or
migrated. New metadata requires an explicit new import and dossier. Legacy LaTeX
and HTML label lookup remains readable. An implementation update invalidates old
draft plans but does not replace previously imported drafts or their manual edits.

The parser is a bounded structural indexer, not a full browser DOM or semantic
mathematics parser. PDF links, indirect definitions, implicit assumptions and
complete mathematical dependency recovery remain open work. Reference counts
measure source observations, not proof coverage or semantic accuracy.

Protocol: [document-intake-v1](../protocols/document-intake-v1.md).
Validation: [HTML reference results](../evaluation/html-reference-results.md).
