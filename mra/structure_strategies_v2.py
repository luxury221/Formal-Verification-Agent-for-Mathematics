"""Complete-target structure proposals with feedback from existing checked graph attempts."""

from copy import deepcopy
from pathlib import Path
import time

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .structure_strategy_ast import RankedPlanner
from .structure_strategy_terms import (
    MAX_NORMALIZATION_NODES,
    MAX_INTERMEDIATE_NODES,
    WALL_SECONDS,
)
from .structured_terms import FIELDS, compile_request
from .structured_proofs import compile_proof
from .structures import manifest as structure_manifest
from .typed_terms import bounded_data

SCHEMA = "structure-strategy-proposals-v2"
DEFAULT_BUDGET = {"max_rewrite_steps": 4, "max_expansions": 800, "max_candidates": 8}
FILES = (
    "mra/structure_strategies_v2.py",
    "mra/structure_strategy_ast.py",
    "mra/structure_strategy_terms.py",
    "mra/structure_composition_terms.py",
)


def manifest(root):
    return {
        "provider": structure_manifest(root),
        "files": {n: file_hash(Path(root) / n) for n in FILES},
    }


def propose(root, raw):
    bounded_data(raw)
    exact_object(raw, {"schema", "target", "budget"}, "Structure strategy proposal")
    if raw["schema"] != SCHEMA:
        raise ValueError("Use the versioned structure strategy proposal protocol")
    exact_object(raw["target"], FIELDS - {"proof"}, "Complete target without reference proof")
    exact_object(raw["budget"], set(DEFAULT_BUDGET), "Explicit synthesis budget")
    for name, maximum in (
        ("max_rewrite_steps", 6),
        ("max_expansions", 2000),
        ("max_candidates", 8),
    ):
        if type(raw["budget"][name]) is not int or not 1 <= raw["budget"][name] <= maximum:
            raise ValueError(name + " requires an integer from 1 to " + str(maximum))
    compiled = compile_request({**raw["target"], "proof": {"method": "rfl"}})
    target = deepcopy(raw["target"])
    engine = RankedPlanner(target, deepcopy(raw["budget"]))
    candidates = []
    programs = engine.programs()
    synthesis_elapsed = time.monotonic() - engine.started
    for label, proof, origin in programs:
        request = {**deepcopy(target), "proof": proof}
        checked = compile_request(request)
        if checked["formal_statement"] != compiled["formal_statement"]:
            raise ValueError("Proposed program changed the complete target")
        source = compile_proof(proof, checked)
        candidates.append(
            {
                "candidate_id": digest(request),
                "label": label,
                "origin": origin,
                "request": request,
                "proof_source": source,
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
        )
    value = {
        "schema": SCHEMA,
        "target": target,
        "target_fingerprint": digest(target),
        "formal_statement": compiled["formal_statement"],
        "source_manifest": manifest(root),
        "budget": {
            **raw["budget"],
            "synthesis_wall_seconds": WALL_SECONDS,
            "normalization_nodes": MAX_NORMALIZATION_NODES,
            "intermediate_data_nodes": MAX_INTERMEDIATE_NODES,
        },
        "candidates": candidates,
        "induction_ranking": engine.induction_ranking,
        "ranking_complete": engine.ranking_complete,
        "ranking_policy": "successor exposures, zero exposures, free goal occurrence order",
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "lean_calls": 0,
        "model_calls": 0,
        "reference_proof_used": False,
        "requires_preview_and_named_confirmation": True,
        "limitations": [
            "Bounded heuristic proposals, not proofs, complete search, independent evaluation or research ability.",
            "Uses only the full target, explicit original equations and closed definitions; never adds a premise.",
            "Symbolic normalization and first-match rewriting may differ from Lean elaboration; every candidate needs the existing real checker.",
            "At most one outer Nat induction, no generalization synthesis, no new lemmas or raw Lean.",
            "Synthesis budget is separate from later candidate/replay worker budgets; no automatic execution or retries.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return {
        **value,
        "stats": {
            "expansions": engine.expansions,
            "normalization_nodes": engine.normalization_nodes,
            "synthesis_elapsed_seconds": round(synthesis_elapsed, 6),
            "preparation_wall_seconds": round(time.monotonic() - engine.started, 6),
            "stop_reason": engine.stop_reason,
        },
    }


def graph_proposals(store, raw):
    bounded_data(raw)
    exact_object(raw, {"graph_id", "node_id", "budget"}, "Current structure node strategy request")
    graph = store.report(raw["graph_id"])
    if graph["invalidated"] or raw["node_id"] not in graph["plan"]["node_plans"]:
        raise ValueError("An intact existing complete structure node is required")
    target = deepcopy(graph["plan"]["node_plans"][raw["node_id"]]["request"])
    target.pop("proof")
    value = propose(store.root, {"schema": SCHEMA, "target": target, "budget": raw["budget"]})
    feedback = []
    for attempt in graph["attempts"]:
        if attempt["node_id"] != raw["node_id"]:
            continue
        child = graph["checks"].get(attempt["attempt_id"])
        if child is None:
            continue
        observed = {k: v for k, v in child["plan"]["request"].items() if k != "proof"}
        if observed != target:
            raise ValueError("Feedback does not belong to the complete current target")
        result = child.get("result") or {}
        invalidated = attempt["invalidated"] or child["invalidated"]
        error = result.get("error") or ""
        feedback.append(
            {
                "attempt_id": attempt["attempt_id"],
                "run_id": child["run_id"],
                "state": attempt["state"],
                "math_verdict": "UNKNOWN" if invalidated else child["math_verdict"],
                "evidence_level": "E0" if invalidated else child["evidence_level"],
                "invalidated": invalidated,
                "proof_fingerprint": digest(child["plan"]["request"]["proof"]),
                "error": error[:1600],
                "error_truncated": len(error) > 1600,
                "candidate_returncode": (result.get("candidate") or {}).get("returncode"),
                "replay_state": (result.get("replay") or {}).get("run_status"),
                "worker_seconds": result.get("worker_seconds", 0),
            }
        )
    rejected = {
        f["proof_fingerprint"]
        for f in feedback
        if f["state"] == "REJECTED" and not f["invalidated"]
    }
    for candidate in value["candidates"]:
        candidate["previously_rejected"] = digest(candidate["request"]["proof"]) in rejected
    value["candidates"].sort(key=lambda c: c["previously_rejected"])
    value.update(
        context={
            "graph_id": graph["graph_id"],
            "node_id": raw["node_id"],
            "revision": graph["revision"],
            "parent": graph["plan"]["parent"],
        },
        feedback=feedback,
    )
    value.pop("plan_fingerprint")
    value["plan_fingerprint"] = digest({k: v for k, v in value.items() if k != "stats"})
    return value
