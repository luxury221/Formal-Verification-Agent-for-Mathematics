"""Source references bound to exact research steps, never proof dependencies."""

from copy import deepcopy
import uuid
import sqlite3

from .domain import digest
from .formalizer import exact_object
from .premise_search import PremiseIndex
from .premise_metadata import PremiseMetadata
from .research_flow import identifier
from .tasks import _now


def validate_citations(row):
    citations = row.get("citations", [])
    if not isinstance(citations, list) or len(citations) > 12:
        raise ValueError("Research source reference budget exceeded")
    seen = set()
    plans = {p["fingerprint"]: p["plan"] for p in row["plans"]}
    for citation in citations:
        exact_object(
            citation,
            {
                "citation_id",
                "node_id",
                "plan_fingerprint",
                "item",
                "item_fingerprint",
                "withdrawn",
                "at",
            },
            "Research source reference",
        )
        identifier(citation["citation_id"], "citation")
        if citation["citation_id"] in seen or type(citation["withdrawn"]) is not bool:
            raise ValueError("Research reference identity changed")
        seen.add(citation["citation_id"])
        if digest(citation["item"]) != citation["item_fingerprint"]:
            raise ValueError("Saved reference excerpt changed")
        plan = plans[citation["plan_fingerprint"]]
        if not any(n["id"] == citation["node_id"] for n in plan["steps"]):
            raise ValueError("Source reference belongs to a different research step")
        if (citation["item"]["math_verdict"], citation["item"]["evidence_level"]) != (
            "UNKNOWN",
            "E0",
        ):
            raise ValueError("Retrieval cannot certify a research claim")


def states(root, row):
    result = []
    if not row.get("citations"):
        return result
    index = PremiseIndex(root)
    current = row["plans"][-1]["fingerprint"] if row["plans"] else None
    for citation in row.get("citations", []):
        value = {**deepcopy(citation), "status": "AVAILABLE", "reason": None}
        if citation["withdrawn"]:
            value["status"] = "WITHDRAWN"
        elif citation["plan_fingerprint"] != current:
            value["status"] = "PREVIOUS_PLAN"
        else:
            try:
                if index.lookup(citation["item"]["item_id"]) != citation["item"]:
                    raise ValueError("Current source does not match the saved excerpt")
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as error:
                value.update(status="UNAVAILABLE", reason=str(error)[:240])
            if value["status"] == "AVAILABLE":
                try:
                    value["metadata"] = PremiseMetadata(root).status(citation["item"])
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                    value["metadata"] = {"state": "UNAVAILABLE", "error": str(error)[:240]}
        result.append(value)
    return result


def context(root, row, node_id):
    values = [
        x
        for x in states(root, row)
        if x["node_id"] == node_id
        and not x["withdrawn"]
        and x["plan_fingerprint"] == row["plans"][-1]["fingerprint"]
    ]
    return {
        "available": [x["item"] for x in values if x["status"] == "AVAILABLE"],
        "unavailable": [
            {"item_id": x["item"]["item_id"], "reason": x["reason"]}
            for x in values
            if x["status"] == "UNAVAILABLE"
        ],
        "declaration_metadata": [
            {
                "item_id": x["item"]["item_id"],
                "resolution": x["metadata"]["state"],
                "report": x["metadata"]["report"],
                "scope": "Cached Lean type inspection; no applicability check or proof of the current goal",
            }
            for x in values
            if x["status"] == "AVAILABLE"
            and x.get("metadata", {}).get("state") in ("READY", "AMBIGUOUS", "NOT_FOUND")
        ],
        "scope": "Untrusted source excerpts for research; not proof dependencies or verified applicability",
    }


def search(store, key, data):
    exact_object(data, {"node_id", "query", "limit"}, "Search premises for a research step")
    row = store.read(key)
    store.node(row, data["node_id"])
    return {
        **PremiseIndex(store.root).search({"query": data["query"], "limit": data["limit"]}),
        "study_id": key,
        "node_id": data["node_id"],
        "plan_fingerprint": row["plans"][-1]["fingerprint"],
    }


def attach(store, key, data):
    exact_object(
        data,
        {"request_id", "expected_revision", "node_id", "item_id"},
        "Attach a current source reference",
    )

    def action(row):
        store.node(row, data["node_id"])
        references = row.setdefault("citations", [])
        if len(references) >= 12:
            raise ValueError("Twelve source references per research notebook")
        plan_fp = row["plans"][-1]["fingerprint"]
        if any(
            x["node_id"] == data["node_id"]
            and x["plan_fingerprint"] == plan_fp
            and x["item"]["item_id"] == data["item_id"]
            and not x["withdrawn"]
            for x in references
        ):
            raise ValueError("This source is already attached to the selected step")
        item = PremiseIndex(store.root).lookup(data["item_id"])
        references.append(
            {
                "citation_id": "citation-" + uuid.uuid4().hex,
                "node_id": data["node_id"],
                "plan_fingerprint": plan_fp,
                "item": item,
                "item_fingerprint": digest(item),
                "withdrawn": False,
                "at": _now(),
            }
        )

    return store.change(key, data, "RESEARCH_SOURCE_ATTACHED_WITHOUT_PROOF", action)


def withdraw(store, key, data):
    exact_object(
        data, {"request_id", "expected_revision", "citation_id"}, "Withdraw a research reference"
    )

    def action(row):
        citation = next(
            (x for x in row.get("citations", []) if x["citation_id"] == data["citation_id"]), None
        )
        if citation is None:
            raise ValueError("Unknown research source reference")
        citation["withdrawn"] = True

    return store.change(key, data, "RESEARCH_SOURCE_WITHDRAWN", action)


def inspect(store, key, data):
    exact_object(
        data, {"request_id", "expected_revision", "citation_id"}, "Read a Lean source type"
    )
    row = store.read(key)
    if type(data["expected_revision"]) is not int or data["expected_revision"] != row["revision"]:
        raise ValueError("Notebook changed; reload before reading source metadata")
    if any(x["state"] == "RUNNING" for x in row["operations"]):
        raise ValueError("Finish the current model operation first")
    citation = next(
        (x for x in row.get("citations", []) if x["citation_id"] == data["citation_id"]), None
    )
    if (
        citation is None
        or citation["withdrawn"]
        or citation["plan_fingerprint"] != row["plans"][-1]["fingerprint"]
    ):
        raise ValueError("Select a current, active source reference")
    PremiseMetadata(store.root).start(citation["item"], data["request_id"])
    return store.report(key)
