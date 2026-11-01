# Background article evidence rechecks v1

A recheck reads the existing verifier reports referenced by one frozen article
revision, one backend/graph at a time. It records historical observations; it does
not run Lean or a model, change article bindings, grant E4, prove a local claim, or
certify that an entire article is correct. Existing backend reports and exact
`article_audit.evidence_identity` checks retain their original semantics.

## HTTP contract

| Method | Path | Body / result |
| --- | --- | --- |
| POST | `/v1/article-audits/<article>/rechecks` | `{request_id, expected_revision}`; creates or replays a durable job |
| GET | `/v1/article-audits/<article>/rechecks` | Compact historical job list |
| GET | `/v1/article-audits/<article>/rechecks/<job>` | Progress and article-snapshot observation |
| GET | `/v1/article-audits/<article>/rechecks/<job>/result` | `{job, result}`; historical result exists only for COMPLETED |
| POST | `/v1/article-audits/<article>/rechecks/<job>/cancel` | Empty object; records cooperative cancellation |

Creation strictly accepts the two named fields, requires the stored article
revision and preserves the original request content. Reusing a request ID with
different content is rejected. An exact replay returns the original job, including
its terminal state; an intentional rerun needs a new request ID. A job belonging to
another article cannot be read or cancelled through that article's path.

States are `QUEUED`, `RUNNING`, `COMPLETED`, `CANCELLED`, `INTERRUPTED`, `FAILED`,
and `STALE`. Execution state is separate from mathematical judgment. `COMPLETED`
means the scheduled reads finished; a job can complete with unavailable, busy or
changed evidence. A provider is read once per backend/graph, even when several
article links point to it. Provider failures are localized to the graph, retained
in history, and never automatically retried.

Polling returns `as_of_only=true`, `fresh_attestation=false`,
`evidence_authority=false`, `model_calls=0` and `new_lean_executions=0`. A matching
link is called `MATCHED_AS_OF_CHECK`, never CURRENT. Other observations are
`STALE_BINDING`, `DIFFERENT_EVIDENCE`, `UNAVAILABLE` and `BUSY`. The report stores
only exact attempt/source/plan references and digests of the formal target and
artifact hashes; it does not copy or manufacture a proof.

## Snapshot and history

The frozen snapshot includes article/source/inventory fingerprints, source hash,
revision, the complete article-record digest and each link's binding digest.
Changing an annotation, note, workspace attachment, binding or source invalidates
the original snapshot even when the HTML remains identical. The worker compares
this snapshot before each graph and immediately before publishing completion.
A change yields STALE; an unreadable article yields FAILED. No completion summary
is published for that attempt.

Status queries compare the article snapshot at read time, without calling verifier
providers or rescanning their mathematical artifacts. `snapshot_status` is
`MATCHES_AT_READ`, `CHANGED` or `UNAVAILABLE`. These observations do not promise
that verifier artifacts remain unchanged after the earlier graph read.

When a completed job's article later changes, its status summary is hidden, while
its detailed historical result remains readable with
`applicable_to_snapshot_at_read=false`. The result always has
`whole_article_verified=false` and `evidence_authority=false`. Displaying an archive
must not replace the article's own current evidence report or mathematical verdict.

## Ownership, locks and cancellation

A pump holds `worker_session()` for its entire lifetime, including idle periods
and the gaps between graphs. The independent `.owner.lock` lifecycle lease stops
another process or server port from recovering or executing the live owner's jobs.
It is separate from global mathematical checker locks. The session owner token is
thread-local; the pump must open and use its session inside the worker thread.

Provider reads happen with neither the article lock nor the job lock held. Short
article-then-job locks protect snapshot validation and final publication; short job
locks protect progress and cancellation commits. The lifecycle owner lease stays
held during the provider read. The unchanged provider retains control of any
verifier-specific locks it requires. Other article edits and cancellation requests
can therefore proceed while a provider is reading.

A queued job or a running job between graphs can be cancelled immediately. Since
the controlled-reader increment (ADR0097), the default provider runs in a fixed
isolated Python subprocess with a 300-second wall-clock budget per graph and a
16 MiB complete-result budget. It still calls the original `backend_report`,
including every original target, source and artifact check; no successful report
is truncated, cached or reused as current evidence. The input is limited to the
allowlisted backend, a graph identifier and internal lifecycle metadata. The
transport accepts no configurable command, module, Lean source or model endpoint.

Cancellation is persisted and polled while that process runs. The parent kills
its process group, reaps the direct child and discards the uncommitted observation.
It checks cancellation again after a complete response and before observation
commit. If cancellation commits first, no completion summary is published; if
completion commits first, a later cancel leaves that history unchanged. A timeout,
crash, malformed response or result overflow is recorded as UNAVAILABLE, never as
a mathematical rejection, and is not automatically retried. COMPLETED can include
these unavailable graphs; it describes completion of scheduled reads only.

The job's `read_control` describes the current transport and explicitly marks
whether it applies to the job's implementation. It must not be used to claim that
an old implementation had a cancellable reader. Explicitly injected in-process
providers used by embeddings and fixtures retain their own interruption contract;
the service's standard provider uses the bounded process transport.

On service shutdown, the same control stops an in-flight default reader and marks
the job INTERRUPTED, preserving previously committed observations but publishing
no summary. A stop or implementation change during final snapshot/history reads
is checked again immediately before completion publication. There is no automatic
restart or retry of the interrupted job. The 300 seconds apply to an individual
provider read, not the entire multi-graph job or all file-lock and status I/O.

The child inherits the lifecycle owner descriptor, so a parent crash cannot let a
new pump recover the queue while that reader still exists. It also installs its
own wall timer before importing a provider; expiration kills its process group.
Process groups do not contain descendants that deliberately start other sessions.
The current report path does not submit new Lean checks. This is a bounded local
transport, not a general sandbox for arbitrary provider code.

Original provider reports can persist invalidation diagnostics and cancel an
already-invalid prerequisite task. Stopping the reader does not roll those
original side effects back. It prevents this job from adopting incomplete output
and does not alter the mathematical verifier or reverse an invalidation.

A crash, missing owner, or unfinished provider step becomes INTERRUPTED on recovery;
it is not replayed. A historical observation written before a crash but not
referenced by the committed job record is not adopted. If final publication alone
is blocked by an article lock, a later step only finalizes the committed graphs;
it does not repeat provider reads.

## Implementation upgrades and rollback

Each job retains the fingerprint of the recheck implementation, article audit,
source inventory and controlled-reader code that created it. Storage shape, envelopes, file hashes,
snapshot/request identities and exact summary counts are still strictly validated
when the implementation changes.

A completed job from another implementation remains readable as history with
`implementation_status=CHANGED`, the original implementation fingerprint and
`applicable_to_current_implementation=false`. The compact status hides its summary.
New jobs can still be created alongside that history. Rolling back code makes the
same comparison against the restored implementation; it never rewrites provenance
or promotes historical observations into current authority.

Old QUEUED/RUNNING jobs are marked INTERRUPTED (or CANCELLED when cancellation was
already requested) by `recover()` or `work_once()`. They are never continued across
an implementation change, including a change during a provider read or before
final publication. They do not consume the new implementation's active-job quota.
An explicit new job is required. Returning to their original implementation does
not restart a terminal interrupted job. A live owner lease cannot be stolen during
an upgrade; the previous owner must end before another pump can recover it.

## Storage limits and operational constraints

Jobs are stored under `artifacts/article-rechecks/articlecheck-<32 hex>/` with an
immutable `snapshot.json`, atomic `record.json` and numbered graph observations.
Each envelope is limited to 2 MiB and verified by the existing atomic/envelope
protocol; observation references also record whole-file SHA-256 values. Redirected
paths, symlinks, malformed fields, changed references, altered summaries and invalid
IDs are rejected.

Limits are 200 total jobs, 20 per article, 8 active jobs for the current
implementation, and 64 linked obligations/graphs per snapshot. History counts
toward total and per-article budgets. There is no automatic pruning or evidence
deletion. A malformed job discovered while scanning the queue deliberately fails
closed and may block listing, new job creation or pump progress until diagnosed;
old implementation provenance by itself is not treated as corruption. Incompatible
future storage schemas require an explicit migration rather than silent adoption.

Contract fixtures in `tests/test_article_rechecks.py`,
`tests/test_article_read_control.py` and `tests/test_article_evidence_reader.py`
exercise software lifecycle, publication races, real subprocess control, crash
recovery, upgrade/rollback and integrity checks in temporary roots.
They are not Lean validation results. Test evidence for this increment is recorded
under `artifacts/article-audit-v1-20261101-01/rechecks/`.
