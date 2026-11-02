"""Versioned local tools for optional orchestrators; existing stores own all evidence."""

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sqlite3

from .formalizer import exact_object, strict_json
from .model_service import text_field

SCHEMA = "product-tools-v1"
RESULT_SCHEMA = "product-tool-result-v1"
MAX_INPUT_BYTES = 131072
MAX_OUTPUT_BYTES = 8388608
MAX_DEPTH = 96
MAX_NODES = 24000
ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error)

# Tools enqueue existing work; no worker execution, inline model generation or shell.
TOOLS = {
    "catalog": (set(), False),
    "workflow.capabilities": (set(), False),
    "document.capabilities": (set(), False),
    "document.import": ({"request"}, True),
    "document.list": (set(), False),
    "document.status": ({"document_id"}, False),
    "document.draft": ({"document_id"}, False),
    "document.cancel": ({"document_id"}, True),
    "paper.capabilities": (set(), False),
    "paper.create": ({"request"}, True),
    "paper.list": (set(), False),
    "paper.status": ({"job_id"}, False),
    "paper.artifact": ({"job_id", "name"}, False),
    "paper.cancel": ({"job_id", "request"}, True),
    "paper.resume": ({"job_id", "request"}, True),
    "study.create": ({"request"}, True),
    "study.list": (set(), False),
    "study.scaffold": ({"study_id", "request"}, True),
    "mathlib.search": ({"query", "limit"}, False),
    "study.report": ({"study_id"}, False),
    "formalization.preview": ({"study_id", "node_id", "backend", "formalization"}, False),
    "study.transfer_preview": ({"study_id", "round_id"}, False),
    "study.transfer": ({"study_id", "request"}, True),
    "study.evidence": ({"study_id", "graph_id"}, False),
    "verification.preview": ({"study_id", "graph_id", "node_id"}, False),
    "verification.status": ({"study_id", "graph_id"}, False),
    "verification.submit": ({"study_id", "graph_id", "request"}, True),
    "verification.cancel": ({"study_id", "graph_id", "request"}, True),
}


def bounded_json(value):
    """Reject non-JSON objects and excessive depth before entering a domain store."""
    count = 0
    ancestors = set()

    def visit(item, depth):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Tool input exceeds its JSON node or depth budget")
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Tool input must contain finite JSON numbers")
            return
        if type(item) not in (dict, list) or id(item) in ancestors:
            raise ValueError("Tool input must be a JSON tree")
        ancestors.add(id(item))
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)
        ancestors.remove(id(item))

    visit(value, 0)
    if len(encode(value)) > MAX_INPUT_BYTES:
        raise ValueError("Tool request exceeds 128 KiB")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )


def parse(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Tool request must be UTF-8 JSON of at most 128 KiB")
    try:
        value = strict_json(raw.decode("utf-8"))
        bounded_json(value)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid or excessively nested UTF-8 JSON tool request") from error
    return value


def error_reply(code, message, *, call_id=None, tool=None, outcome="NOT_STARTED", receipt=None):
    return {
        "schema": RESULT_SCHEMA,
        "call_id": call_id,
        "tool": tool,
        "call_status": "REJECTED" if outcome == "NOT_STARTED" else "FAILED",
        "operation_outcome": outcome,
        "receipt": receipt,
        "result": None,
        "error": {"code": code, "message": message[:3000]},
    }


class CoreToolAPI:
    """A trusted local caller boundary, not authentication or semantic review authority.

    Mutating tools require an explicit execution configuration. The caller must relay
    real operator review data; an agent must never generate the operator's signature.
    Audit reads may persist invalidation flags through the original domain stores.
    """

    def __init__(self, root, *, allow_mutations=False):
        if type(allow_mutations) is not bool:
            raise ValueError("allow_mutations must be an explicit boolean")
        self.root = Path(root).resolve()
        self.allow_mutations = allow_mutations

    def catalog(self):
        return {
            "schema": SCHEMA,
            "tools": [
                {
                    "name": name,
                    "argument_fields": sorted(fields),
                    "requires_mutation_access": mutates,
                    "enabled": self.allow_mutations or not mutates,
                }
                for name, (fields, mutates) in TOOLS.items()
            ],
            "limits": {
                "input_bytes": MAX_INPUT_BYTES,
                "output_bytes": MAX_OUTPUT_BYTES,
                "json_depth": MAX_DEPTH,
                "json_nodes": MAX_NODES,
                "search_hits": 5,
            },
            "audit_reads_may_persist_invalidation": True,
            "executes_lean": False,
            "generates_model_output": False,
            "may_enqueue_model_work": True,
            "starts_worker": False,
            "worker_execution": "EXTERNAL",
            "automatic_retry": False,
            "scope": "Local business tools; authoritative stores retain review, budgets and evidence",
        }

    @staticmethod
    def _receipt(tool, args, result=None):
        value = {"tool": tool}
        for name in ("study_id", "graph_id", "document_id", "job_id"):
            if name in args:
                value[name] = args[name]
        request = args.get("request", {})
        if isinstance(request, dict):
            for name in ("request_id", "attempt_id", "round_id", "document_id"):
                if name in request:
                    value[name] = request[name]
        if isinstance(result, dict):
            for name in (
                "study_id",
                "graph_id",
                "attempt_id",
                "child_id",
                "revision",
                "document_id",
                "job_id",
            ):
                if name in result:
                    value[name] = result[name]
            if tool == "study.transfer":
                transfer = next(
                    (
                        item
                        for item in reversed(result.get("transfers", []))
                        if item["round_id"] == request.get("round_id")
                    ),
                    None,
                )
                if transfer:
                    value.update(graph_id=transfer["graph_id"], backend=transfer["backend"])
        return value

    def call(self, request):
        """call_id correlates transport only; domain request_id controls mutation replay."""
        call_id, tool, args = None, None, {}
        try:
            bounded_json(request)
            exact_object(request, {"schema", "call_id", "tool", "arguments"}, "Product tool call")
            if request["schema"] != SCHEMA:
                raise ValueError("Unknown product tool schema; no fallback")
            supplied_id = request["call_id"]
            if not isinstance(supplied_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", supplied_id
            ):
                raise ValueError("call_id must be a short ASCII correlation identifier")
            call_id = supplied_id
            name = request["tool"]
            if not isinstance(name, str) or name not in TOOLS:
                raise ValueError("Unknown product tool; arbitrary execution is unavailable")
            tool = name
            fields, mutates = TOOLS[tool]
            args = exact_object(request["arguments"], fields, "Product tool arguments")
            if mutates and not self.allow_mutations:
                raise ValueError("This caller has no mutation access")
            args = deepcopy(args)
        except (ValueError, TypeError, RecursionError, UnicodeError) as error:
            return error_reply("INVALID_CALL", str(error), call_id=call_id, tool=tool)

        try:
            result = self._dispatch(tool, args)
        except ERRORS as error:
            # A store can persist an audit or commit a mutation before a later read fails.
            # Do not promise rollback, nor create a fresh domain request on transport retry.
            return error_reply(
                "DOMAIN_ERROR",
                type(error).__name__ + ": " + str(error),
                call_id=call_id,
                tool=tool,
                outcome="UNKNOWN",
                receipt=self._receipt(tool, args),
            )

        receipt = self._receipt(tool, args, result)
        reply = {
            "schema": RESULT_SCHEMA,
            "call_id": call_id,
            "tool": tool,
            "call_status": "RETURNED",
            "operation_outcome": "RETURNED",
            "receipt": receipt,
            "result": result,
            "error": None,
        }
        if len(encode(reply)) > MAX_OUTPUT_BYTES:
            reply = error_reply(
                "RESULT_LIMIT",
                "Operation returned but its full result exceeds 8 MiB; no proof was truncated. "
                "Use the receipt to query status or repeat the SAME domain request_id and body. "
                "A changed request_id may duplicate a mutation.",
                call_id=call_id,
                tool=tool,
                outcome="RETURNED",
                receipt=receipt,
            )
        return reply

    def from_bytes(self, raw):
        try:
            request = parse(raw)
        except (ValueError, TypeError) as error:
            return error_reply("INVALID_JSON", str(error))
        return self.call(request)

    def _dispatch(self, tool, args):
        if tool == "catalog":
            return self.catalog()
        if tool == "workflow.capabilities":
            from .workflows import capabilities

            return capabilities(self.root)
        if tool.startswith("document."):
            from .document_intake import DocumentStore, capabilities

            if tool == "document.capabilities":
                return capabilities(self.root)
            store = DocumentStore(self.root)
            if tool == "document.import":
                return store.create(args["request"])
            if tool == "document.list":
                return {"documents": store.listing()}
            method = {
                "document.status": "report",
                "document.draft": "draft",
                "document.cancel": "cancel",
            }[tool]
            return getattr(store, method)(args["document_id"])
        if tool.startswith("paper."):
            from .paper_automation import PaperAutomationStore, capabilities

            if tool == "paper.capabilities":
                return capabilities(self.root)
            store = PaperAutomationStore(self.root)
            if tool == "paper.create":
                return store.create(args["request"])
            if tool == "paper.list":
                return {"jobs": store.listing()}
            key = args["job_id"]
            if tool == "paper.status":
                return store.report(key)
            if tool == "paper.artifact":
                # Read only manifest-registered, hash-matched JSON under this job.
                return store.artifact(store.read(key), args["name"])
            return store.action(key, tool.removeprefix("paper."), args["request"])
        if tool == "mathlib.search":
            from .premise_search import PremiseIndex

            text_field(args["query"], "Mathlib query", 240)
            if type(args["limit"]) is not int or not 1 <= args["limit"] <= 5:
                raise ValueError("Tool search limit must be 1-5")
            return PremiseIndex(self.root).search(args)

        from .research_studio import StudioStore, backend_store

        studio = StudioStore(self.root)
        if tool == "study.create":
            return studio.create(args["request"])
        if tool == "study.list":
            return {"studies": studio.listing()}
        key = args["study_id"]
        if tool == "study.scaffold":
            return studio.scaffold(key, args["request"])
        if tool == "study.report":
            return studio.report(key)
        if tool == "formalization.preview":
            from .research_protocol import BACKENDS
            from .research_tools import perform

            if args["backend"] not in BACKENDS:
                raise ValueError("Select a known verification backend")
            row = studio.read(key)
            context = {
                "source": row["source"],
                "source_fingerprint": row["source_fingerprint"],
                "plan_fingerprint": row["plans"][-1]["fingerprint"] if row["plans"] else None,
                "project_id": row["project_id"],
                "node": studio.node(row, args["node_id"]),
                "backend": args["backend"],
            }

            def preview(formal):
                raw = {
                    "project_id": context["project_id"],
                    "branch_id": "main",
                    **formal["request"],
                }
                return backend_store(self.root, formal["backend"]).preview(raw)

            return perform(
                self.root,
                {"kind": "preview_formalization", "formalization": args["formalization"]},
                context,
                preview,
                set(),
            )
        if tool == "study.transfer_preview":
            return studio.preview_transfer(key, {"round_id": args["round_id"]})
        if tool == "study.transfer":
            return studio.transfer(key, args["request"])
        if tool in ("study.evidence", "verification.status"):
            return studio.evidence(key, {"graph_id": args["graph_id"]})

        # The caller cannot select an arbitrary graph/backend pair. Audit the saved
        # study association and plan identity before entering the original store.
        studio.evidence(key, {"graph_id": args["graph_id"]})
        row = studio.read(key)
        transfer = next(x for x in row["transfers"] if x["graph_id"] == args["graph_id"])
        provider = backend_store(self.root, transfer["backend"])
        if tool == "verification.preview":
            return provider.preview_node(args["graph_id"], {"node_id": args["node_id"]})
        if tool == "verification.submit":
            return provider.submit(args["graph_id"], args["request"])
        if tool == "verification.cancel":
            return provider.cancel(args["graph_id"], args["request"])
        raise ValueError("Unimplemented product tool")


def main(root=None):
    """One JSON call per process; supervisors own process deadlines and cancellation."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="UTF-8 call file, or - for stdin")
    parser.add_argument("--allow-mutations", action="store_true")
    args = parser.parse_args()
    api = CoreToolAPI(
        root or Path(__file__).resolve().parents[1], allow_mutations=args.allow_mutations
    )
    try:
        if args.input == "-":
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(args.input).open("rb") as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
    except (OSError, ValueError) as error:
        reply = error_reply("INPUT_IO_ERROR", type(error).__name__ + ": " + str(error))
    else:
        reply = api.from_bytes(raw)
    sys.stdout.buffer.write(encode(reply) + b"\n")
    return 0 if reply["call_status"] == "RETURNED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
