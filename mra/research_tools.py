"""Bounded model/search/representation loop. Tools never execute a proof candidate."""

from copy import deepcopy
from dataclasses import asdict
import sqlite3
import re
import time

from .domain import canonical
from .formalizer import exact_object, strict_json
from .model_service import ModelBudget, ModelRequest, text_field
from .premise_metadata import PremiseMetadata
from .premise_search import PremiseIndex, query_terms
from .research_protocol import validate_round

VERSION = "research-tools-v1"
MAX_CALLS = 4
MAX_TOOLS = 3
MAX_OUTPUT_TOKENS = 8192
MAX_OUTPUT_BYTES = 131072
TIMEOUT = 90
ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error)
TOOLS = {
    "search_mathlib": {"query": "nonempty text, at most 240 characters", "limit": 5},
    "preview_formalization": {
        "formalization": "complete {backend, request}; selected backend only"
    },
}


def calls(operation):
    return len(operation["calls"]) if operation.get("loop_version") == VERSION else 1


def metadata(root, item):
    try:
        row = PremiseMetadata(root).status(item)
        value = {"state": row["state"]}
        if row["state"] in ("READY", "AMBIGUOUS", "NOT_FOUND"):
            value["report"] = row["report"]
        elif row.get("error"):
            value["error"] = row["error"][:240]
        return value
    except ERRORS as error:
        return {"state": "UNAVAILABLE", "error": str(error)[:240]}


def tool_request(output, context):
    if "tool_request" not in output:
        return None
    exact_object(
        output,
        {"source_fingerprint", "plan_fingerprint", "node_id", "tool_request"},
        "Research tool request",
    )
    if tuple(output[k] for k in ("source_fingerprint", "plan_fingerprint", "node_id")) != (
        context["source_fingerprint"],
        context["plan_fingerprint"],
        context["node"]["id"],
    ):
        raise ValueError("Tool request changed the original source, plan or step")
    request = output["tool_request"]
    if not isinstance(request, dict) or request.get("kind") not in TOOLS:
        raise ValueError("Unsupported research tool; no arbitrary execution is available")
    fields = {"kind", "query"} if request["kind"] == "search_mathlib" else {"kind", "formalization"}
    exact_object(request, fields, "Controlled research tool")
    return request


def perform(root, request, context, preview, seen_queries):
    """Return bounded research data/diagnostics; no graph creation, submission or Lean."""
    base = {"math_verdict": "UNKNOWN", "evidence_level": "E0", "new_lean_checks": 0}
    try:
        if request["kind"] == "search_mathlib":
            text_field(request["query"], "Mathlib search query", 240)
            normalized = tuple(query_terms(request["query"]))
            if normalized in seen_queries:
                raise ValueError(
                    "This query already ran in the current operation; refine the search"
                )
            seen_queries.add(normalized)
            value = PremiseIndex(root).search({"query": request["query"], "limit": 5})
            return {
                **base,
                "state": "RETURNED",
                "query_terms": value["query_terms"],
                "index": value["index"],
                "hits": [
                    {"item": item, "metadata": metadata(root, item)} for item in value["hits"]
                ],
                "scope": "Retrieved candidates and cached types; no applicability or proof check",
            }
        formal = request["formalization"]
        # Reuse the exact same source/step checks as a saved proposal.
        validate_round(
            {
                "source_fingerprint": context["source_fingerprint"],
                "plan_fingerprint": context["plan_fingerprint"],
                "node_id": context["node"]["id"],
                "reasoning": "Representation preview only",
                "gaps": [],
                "next_actions": [],
                "formalization": formal,
            },
            context["source_fingerprint"],
            context["plan_fingerprint"],
            context["node"],
        )
        if (
            formal is None
            or formal["backend"] != context["backend"]
            or context["backend"] == "none"
        ):
            raise ValueError("Preview must use the explicitly selected verification backend")
        plan = preview(formal)
        return {
            **base,
            "state": "REPRESENTABLE",
            "formal_plan": plan,
            "scope": "Structure and expression compilation only; semantic alignment and real Lean still required",
        }
    except ERRORS as error:
        return {
            **base,
            "state": "DIAGNOSTIC",
            "error": type(error).__name__ + ": " + str(error)[:3000],
            "scope": "Tool failure or representation diagnostic; not a mathematical refutation",
        }


def refreshed(root, history):
    """Historical excerpts remain archived; changed sources cannot enter the next model input."""
    values = deepcopy(history)
    for event in values:
        if event["request"]["kind"] != "search_mathlib" or event["result"]["state"] != "RETURNED":
            continue
        usable, unavailable = [], []
        for hit in event["result"]["hits"]:
            item = hit["item"]
            try:
                if PremiseIndex(root).lookup(item["item_id"]) != item:
                    raise ValueError("Retrieved source changed during the research operation")
                usable.append({"item": item, "metadata": metadata(root, item)})
            except ERRORS as error:
                unavailable.append({"item_id": item["item_id"], "error": str(error)[:240]})
        event["result"].update(hits=usable, unavailable=unavailable)
    return values


def bounded_tool(
    root, request, context, seen_queries, deadline, cancel_requested, lifecycle_lock_fd
):
    import os
    from pathlib import Path
    import signal
    import subprocess
    import sys

    payload = canonical(
        {
            "root": str(root),
            "request": request,
            "context": context,
            "seen_queries": sorted(seen_queries),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "mra.research_tool_worker"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=() if lifecycle_lock_fd is None else (lifecycle_lock_fd,),
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )
    try:
        while True:
            if cancel_requested():
                raise InterruptedError("Research operation cancelled during tool execution")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Shared research deadline reached during tool execution")
            try:
                raw, _ = process.communicate(input=payload, timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                payload = None
        if process.returncode or len(raw) > 1048576:
            raise OSError("Research tool process failed or returned an oversized result")
        reply = strict_json(raw.decode())
        exact_object(reply, {"result", "seen_queries"}, "Controlled research tool response")
        result = reply["result"]
        if (result["math_verdict"], result["evidence_level"], result["new_lean_checks"]) != (
            "UNKNOWN",
            "E0",
            0,
        ):
            raise ValueError("A research tool cannot grant evidence or execute Lean")
        seen_queries.update(tuple(x) for x in reply["seen_queries"])
        return result
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate()


def run(
    root,
    config,
    factory,
    context,
    operation_id,
    operation,
    call_limit,
    *,
    cancel_requested,
    lifecycle_lock_fd,
    journal,
    fixture,
):
    deadline = time.monotonic() + TIMEOUT
    output_tokens, output_bytes, input_tokens = 0, 0, 0
    history, seen_queries = [], set()
    identity = None

    def checkpoint():
        if cancel_requested():
            raise InterruptedError("Research operation cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("Shared 90-second research operation budget exhausted")

    for ordinal in range(1, call_limit + 1):
        checkpoint()
        current = deepcopy(context)
        if operation == "step":
            current["tool_session"] = {
                "version": VERSION,
                "tools": TOOLS,
                "remaining_model_calls": call_limit - ordinal + 1,
                "remaining_tool_requests": min(MAX_TOOLS - len(history), call_limit - ordinal),
                "history": refreshed(root, history),
            }
        checkpoint()
        if output_tokens >= MAX_OUTPUT_TOKENS or output_bytes >= MAX_OUTPUT_BYTES:
            raise ValueError("Shared model output budget exhausted")
        request = ModelRequest(
            operation_id + "-" + str(ordinal),
            "math_research_" + operation,
            canonical(current).decode(),
            ModelBudget(
                deadline - time.monotonic(),
                100000,
                MAX_OUTPUT_TOKENS - output_tokens,
                MAX_OUTPUT_BYTES - output_bytes,
            ),
            "math-research-" + operation + "-v1",
        )
        if len(request.input_json) > request.budget.max_input_chars:
            raise ValueError(
                "Complete research context exceeds budget; original assumptions were not dropped"
            )
        journal("CALL_STARTED", asdict(request))
        response = factory(
            root, config, cancel_requested=cancel_requested, lifecycle_lock_fd=lifecycle_lock_fd
        ).generate(request)
        if (
            response.request_fingerprint != request.fingerprint
            or len(response.output_json.encode()) > request.budget.max_output_bytes
            or response.usage.output_tokens > request.budget.max_output_tokens
        ):
            raise ValueError("Model response identity or shared output budget changed")
        if not fixture and response.identity.kind != "model":
            raise ValueError("Production operation cannot use a fixture identity")
        if identity is not None and identity != response.identity:
            raise ValueError("Model identity changed within a research operation")
        identity = response.identity
        journal("CALL_FINISHED", asdict(response))
        output_tokens += response.usage.output_tokens
        input_tokens += response.usage.input_tokens
        output_bytes += len(response.output_json.encode())
        checkpoint()
        output = strict_json(response.output_json)
        if not isinstance(output, dict):
            raise ValueError("Model research response must be a JSON object")
        if operation == "plan":
            if "tool_request" in output:
                raise ValueError("Research planning does not execute tools")
            requested = None
        else:
            requested = tool_request(output, context)
        if requested is None:
            return response, output, {"input_tokens": input_tokens, "output_tokens": output_tokens}
        if ordinal == call_limit or len(history) >= MAX_TOOLS:
            raise ValueError("Research tool/call budget exhausted without a final proposal")
        if output_tokens >= MAX_OUTPUT_TOKENS or output_bytes >= MAX_OUTPUT_BYTES:
            raise ValueError("Shared model output budget exhausted before another tool")
        checkpoint()
        result = bounded_tool(
            root, requested, context, seen_queries, deadline, cancel_requested, lifecycle_lock_fd
        )
        checkpoint()
        event = {"request": requested, "result": result, "call_id": request.id}
        journal("TOOL_FINISHED", event)
        history.append(event)
    raise ValueError("Research operation ended without a final proposal")


def summary(event):
    result = event["result"]
    value = {
        "kind": event["request"]["kind"],
        "state": result["state"],
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
    }
    if value["kind"] == "search_mathlib":
        value.update(
            query=event["request"]["query"],
            candidates=[
                {
                    "item_id": h["item"]["item_id"],
                    "name": h["item"]["written_name"],
                    "module": h["item"]["module"],
                    "metadata_state": h["metadata"]["state"],
                }
                for h in result.get("hits", [])
            ],
        )
    if result.get("error"):
        value["error"] = result["error"]
    return value


def validate_operation(operation):
    if operation.get("loop_version") != VERSION:
        return
    if not re.fullmatch(r"studio-[a-f0-9]{32}", operation["id"]):
        raise ValueError("Invalid research operation identity")
    if len(operation["calls"]) > MAX_CALLS or len(operation["tools"]) > MAX_TOOLS:
        raise ValueError("Saved research operation exceeds its tool budget")
    for value in operation["tools"]:
        if (value["math_verdict"], value["evidence_level"]) != ("UNKNOWN", "E0"):
            raise ValueError("Research tool logs cannot grant mathematical evidence")
        if value["kind"] not in TOOLS or value["state"] not in (
            "RETURNED",
            "REPRESENTABLE",
            "DIAGNOSTIC",
        ):
            raise ValueError("Unknown saved research tool state")
    if len(operation["trace_files"]) > MAX_CALLS * 2 + MAX_TOOLS:
        raise ValueError("Research trace file budget exceeded")
    for call in operation["calls"]:
        ModelBudget(**call["budget"])
        if call["state"] not in ("STARTED", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            raise ValueError("Unknown model call lifecycle state")


def trace(store, key, operation_id):
    """Return actual archived inputs/outputs with hashes; never execute or promote evidence."""
    from .integrity import file_hash
    from .search_evaluation import _envelope

    row = store.read(key)
    operation = next((x for x in row["operations"] if x["id"] == operation_id), None)
    if operation is None or operation.get("loop_version") != VERSION:
        raise ValueError("No tool trace for this research operation")
    directory = store.directory(key) / operation_id
    if directory.resolve() != directory:
        raise ValueError("Research trace directory was redirected")
    events = []
    for reference in operation["trace_files"]:
        # Only generated ordinal filenames may be read; no caller-supplied path.
        if not re.fullmatch(
            r"(?:call-[1-4]-(?:request|response)|tool-[1-3])\.json", reference["name"]
        ):
            raise ValueError("Invalid research trace filename")
        path = directory / reference["name"]
        if path.resolve() != path or file_hash(path) != reference["sha256"]:
            raise ValueError("Research trace artifact changed")
        events.append(_envelope(path))
    return {
        "operation_id": operation_id,
        "producer_kind": operation["producer_kind"],
        "events": events,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "scope": "Historical research actions, inputs and outputs; not a proof or current source audit",
    }
