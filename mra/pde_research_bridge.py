"""Materialize full graph nodes against actual current sealed-ledger evidence."""

from copy import deepcopy
import re

from mra.domain import canonical, digest
from mra.integrity import IntegrityError
from mra.pde_language_lab import PDELanguageStore
from .pde_research_graph import bind_node_draft, compile_graph, node_id


def prepare_node_preview(checker, graph, key, ancestor_runs):
    """Re-audit actual predecessor records, preserve all targets, then call the real preview."""
    if type(checker) is not PDELanguageStore:
        raise ValueError("Use the actual sealed PDE ledger for this bridge preflight")
    if not isinstance(graph, dict) or canonical(graph) != canonical(
        compile_graph(graph.get("request"))
    ):
        raise ValueError("Recompile the complete graph draft before binding actual sources")
    node_id(key)
    if key not in graph["node_plans"]:
        raise ValueError("Unknown PDE graph node")
    by_id = {node["id"]: node for node in graph["request"]["nodes"]}
    ancestors = set()

    def visit(current):
        for dep in by_id[current]["depends_on"]:
            if dep not in ancestors:
                ancestors.add(dep)
                visit(dep)

    visit(key)
    if not isinstance(ancestor_runs, dict) or set(ancestor_runs) != ancestors:
        raise ValueError("Supply exactly one literal run ID for every required graph ancestor")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"pdelang-[a-f0-9]{32}", value)
        for value in ancestor_runs.values()
    ):
        raise ValueError("Caller-provided status/type objects cannot authorize graph prerequisites")
    audited = {}

    def expected_request(current):
        bindings = {}
        for source in graph["node_plans"][current]["source_nodes"]:
            dep = source["node_id"]
            row = audited[dep]
            bindings[dep] = {
                "run_id": row["run_id"],
                "type_fingerprint": digest(row["plan"]["formal_statement"]),
            }
        return bind_node_draft(graph, current, bindings)["request"]

    for ancestor in graph["order"]:
        if ancestor not in ancestors:
            continue
        row = checker.report(ancestor_runs[ancestor])
        if (row["state"], row["math_verdict"], row["evidence_level"], row["invalidated"]) != (
            "COMPLETED",
            "PROVED",
            "E4",
            False,
        ):
            raise IntegrityError("Graph prerequisite lacks current complete E4 evidence")
        expected = expected_request(ancestor)
        if row["plan"]["formal_statement"] != graph["node_plans"][ancestor][
            "formal_statement"
        ] or canonical(row["plan"]["request"]) != canonical(expected):
            raise IntegrityError("Actual predecessor does not match its complete graph target")
        audited[ancestor] = row
    request = expected_request(key)
    child = checker.preview(request)
    if (
        child["source_authorization_checked"] is not True
        or child["formal_statement"] != graph["node_plans"][key]["formal_statement"]
        or canonical(child["request"]) != canonical(request)
        or (child["math_verdict"], child["evidence_level"]) != ("UNKNOWN", "E0")
    ):
        raise IntegrityError(
            "Materialized node preview changed its original target or evidence level"
        )
    prerequisite_records = {
        dep: {
            "run_id": row["run_id"],
            "plan_fingerprint": row["plan"]["plan_fingerprint"],
            "complete_type_fingerprint": digest(row["plan"]["formal_statement"]),
            "evidence_identity": digest(
                {
                    "run_id": row["run_id"],
                    "plan": row["plan"],
                    "alignment": row["alignment"],
                    "files": row["files"],
                    "result": row["result"],
                }
            ),
        }
        for dep, row in audited.items()
    }
    value = {
        "schema": "pde-graph-materialization-v1",
        "node_id": key,
        "graph_fingerprint": graph["draft_fingerprint"],
        "prerequisites": prerequisite_records,
        "child_plan": deepcopy(child),
        "source_authorization_checked": True,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "submission_supported": False,
        "worker_started": False,
        "limits": [
            "Materialization is a preview; it does not submit, execute or publish a proof.",
            "Existing source authorization is time-specific; production must re-audit before submission/execution.",
            "A predecessor proof is never a proof of the requested new target.",
        ],
    }
    value["execution_preview_fingerprint"] = digest(value)
    return value
