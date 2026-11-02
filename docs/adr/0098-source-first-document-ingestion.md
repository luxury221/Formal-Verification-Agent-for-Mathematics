# ADR 0098: Source-first paper ingestion with local PDF and OCR fallback

Date: 2026-11-01. Status: accepted and implemented; local acceptance recorded.

## Decision

Add a document intake workspace separate from existing mathematical evidence.
Prefer supplied HTML or LaTeX sources, retain the original PDF for page comparison,
use its text layer when no source is supplied, and attempt local English OCR only
on pages without usable text. Do not compile uploaded TeX or fetch external links.
Support bounded local TeX includes, retain unresolved references and macro warnings,
and preserve exact source spans. Source-to-PDF page matches are suggestions, not
certified correspondences.

Produce a versioned structural draft: sections, definitions, claims, proofs,
equations, references, and a checklist of candidates needing semantic review.
Automatic classification, dependencies and OCR do not constitute formalization or
proof. Assumptions not extracted remain explicitly unreviewed. Existing article,
proof and adoption records are not migrated or re-certified.

Parsing and rendering run in a bounded cancellable child process. Files, page
images and the completed draft are published together only after successful
validation; cancellation or interruption cannot publish partial success. Local
revision edits preserve the extracted original and never grant mathematical
authority. The browser displays escaped content and page images, not uploaded HTML.

## Components and boundaries

Use pinned pypdf for PDF text, pypdfium2 for rendering, Pillow for image output,
and optional Tesseract with English data for fallback OCR. Keep their installation
and caches inside this project and record versions, hashes and license notices.
Do not adopt the installed AGPL PDF library. PDFium's bundled third-party notices
and OCR data licensing need their own retained notices.

No language-model generation or Lean execution is added in this increment. A
rule-based obligation checklist is a structural draft, not an exhaustive semantic
inventory. Scanned mathematical formulas may remain unresolved after text OCR.
Do not claim whole-paper verification or automatic faithful formalization.

## Acceptance

Exercise supplied-source precedence, native PDF text, actual scanned-page OCR,
source and page comparison, unresolved formulas/references, versioned edits,
refresh recovery, cancellation, rejected malformed files, and old-record
preservation. Inspect actual desktop and mobile pages and rendered PDF samples.
Keep original failures and the exact scope of each validation run.

The worker waits up to five seconds for a document record commit. A busy HTTP
record write must not be mistaken for another queue owner. The deterministic
cancel/commit contention test reproduced the prior premature EXTERNAL exit and
now passes without releasing queue ownership or publishing a draft.

Operation and limits: `docs/protocols/document-intake-v1.md`. Local evidence:
`artifacts/document-ingestion-20261101-01/REPORT.md` (not shipped with clones).
