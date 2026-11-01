# Framework-independent local tools v1

The product's own stores retain the original source, semantic review, task budgets,
idempotency, cancellation and mathematical evidence. An optional orchestrator calls
these tools through JSON without importing its SDK into the mathematical verifier.
This release does not install or integrate openJiuwen, invoke a model, start a worker,
or certify natural-language correspondence.

## Entry points

Python: `mra.core_tools.CoreToolAPI(root, allow_mutations=False).call(request)`.

CLI, from the project root:

```sh
PYTHONPATH=. python3 ops/scripts/core_tools.py --input call.json
PYTHONPATH=. python3 ops/scripts/core_tools.py --allow-mutations --input call.json
```

Omitting `--input` or passing `-` reads one UTF-8 JSON call from stdin. One invocation
produces one JSON result, then exits. Successful transport return exits 0; a rejected
or failed call exits 2. A returned formalization diagnostic is still a successful tool
call, and is not a mathematical refutation. Supervisors own process deadlines and
cancellation; this bridge does not claim a bounded whole-paper recheck duration.
Missing input files, directories, unreadable input files and input stream I/O failures
produce a single JSON reply with `INPUT_IO_ERROR`, outcome `NOT_STARTED`, and exit 2.
The tool is not dispatched and no traceback is emitted for these input failures.

```json
{
  "schema": "product-tools-v1",
  "call_id": "example-search-1",
  "tool": "mathlib.search",
  "arguments": {"query": "inner_self", "limit": 5}
}
```

The envelope and each tool's outer arguments reject missing or unknown fields.
Duplicate JSON keys, nonfinite numbers, non-JSON Python objects, cycles, redirected
domain paths and unsupported tool names are rejected. Input is limited to 128 KiB,
96 JSON levels and 24,000 values. Search uses at most 240 query characters and 5 hits.
Existing backend limits and supported mathematical languages remain authoritative.

## Fixed tools

| Tool | Arguments | Business mutation access |
| --- | --- | --- |
| catalog | empty object | No |
| mathlib.search | query, limit | No |
| study.report | study_id | No |
| formalization.preview | study_id, node_id, backend, formalization | No |
| study.transfer_preview | study_id, round_id | No |
| study.transfer | study_id, request | Yes |
| study.evidence | study_id, graph_id | No |
| verification.preview | study_id, graph_id, node_id | No |
| verification.status | study_id, graph_id | No |
| verification.submit | study_id, graph_id, request | Yes |
| verification.cancel | study_id, graph_id, request | Yes |

`catalog` exposes this list and whether the current execution configuration enables
each tool. There are no model generation, worker execution, recovery, shell,
filesystem, arbitrary Lean or arbitrary backend tools. Mutation access is an explicit
local process configuration, not an additional per-call approval workflow.

The preview derives source, plan, node and project context from the stored study.
It delegates to the existing controlled research preview and reports `UNKNOWN/E0`.
The caller cannot substitute a context object. Verification tools require a graph
already associated with that study and check its saved plan identity.

`request` is the complete original business request, without automatic filling:

- Transfer: request_id, expected_revision, round_id, preview_fingerprint, reviewer,
  confirmed. Saving a graph does not enqueue a check.
- Submit: request_id, node_id, execution_fingerprint, reviewer, confirmed. It enqueues
  the existing bounded check; an existing authorized worker must execute it.
- Cancel: attempt_id. Cancellation follows the existing task/commit ordering.

This is a trusted local caller boundary, not user authentication. Named reviewers
retain the project's existing local-assertion semantics. A framework must relay an
operator's actual review of the full preview, not manufacture reviewer names,
confirmation flags or fingerprints. Existing semantic confirmation is preserved;
the wrapper does not grant a model semantic review authority.

Read access means no requested business mutation. Original report/evidence methods
can write audit locks and persist permanent invalidation after detecting damaged
sources or artifacts. They are not guaranteed to be physically read-only.

## Responses and retries

Each reply has schema `product-tool-result-v1`, call_id, tool, call_status,
operation_outcome, receipt, result and error. `call_status` describes the API call,
never mathematical truth. Mathematical fields, when present, are inside the original
domain result; only that domain verifier may issue them. Study prose remains
unverified even when one formal graph has evidence.

`call_id` is transport correlation only. It is not a task identifier or idempotency
key, and reusing it never caches a historical mathematical verdict. Domain request_id
and its complete request body control mutation idempotency.

- Rejected validation/access has operation_outcome `NOT_STARTED`.
- A returned domain result has operation_outcome `RETURNED`.
- A domain error has operation_outcome `UNKNOWN`: the store may have written an
  audit record or committed before a later failure. No rollback is promised.
- A result exceeding 8 MiB is omitted in full with error `RESULT_LIMIT`, outcome
  `RETURNED` and a stable receipt containing the available study/graph/request/attempt
  identifiers. Proofs and conditions are never silently truncated.

On a transport error or result limit, query the receipt or replay the **same business
request_id and exact body**. Do not generate a fresh request_id automatically. A local
CLI process can also be killed after commit but before stdout; preserve the original
request file for this reason. There is no new adapter queue or evidence database to
recover, migrate or roll back. Removing the optional facade leaves domain records
under their existing contracts. Deleting a database to roll back the facade would
destroy unrelated authoritative state and is not part of this design.

Tests use actual local stores with explicitly non-executable runtime metadata. Queue,
preview and invalidation checks are software-contract evidence, not Lean results or
model capability measurements.
