"""Read current E4 sources and propose explicit, untrusted proof-data compositions."""

from copy import deepcopy
from itertools import product
from pathlib import Path
import time

from .domain import canonical, digest
from .formalizer import exact_object
from .integrity import file_hash
from .structure_composition_terms import MODE, compile_composition
from .structure_lemma_search import LemmaSearch
from .structure_reuse import manifest as reuse_manifest
from .structure_strategy_terms import SearchLimit, WALL_SECONDS
from .structured_terms import compile_request
from .typed_terms import bounded_data

SCHEMA = "structure-lemma-suggestions-v1"
DEFAULT_BUDGET = {
    "max_depth": 4,
    "max_rewrite_steps": 3,
    "max_terms": 32,
    "max_expansions": 1600,
    "max_candidates": 4,
}
MAX_SOURCE_CHECKS = 8
MAX_SOURCE_SETS = 8
FILES = (
    "mra/structure_lemma_suggestions.py",
    "mra/structure_lemma_search.py",
    "mra/structure_strategy_generalization.py",
    "mra/structure_strategy_ast.py",
    "mra/structure_strategy_terms.py",
)


def manifest(root):
    return {
        "provider": reuse_manifest(root),
        "files": {n: file_hash(Path(root) / n) for n in FILES},
    }


def feedback(graph, node_id, target):
    result = []
    for attempt in graph["attempts"]:
        if attempt["node_id"] != node_id:
            continue
        child = graph["checks"].get(attempt["attempt_id"])
        if child is None:
            continue
        if {k: v for k, v in child["plan"]["request"].items() if k != "proof"} != {
            k: v for k, v in target.items() if k != "proof"
        }:
            raise ValueError("Stored feedback does not match the full original target")
        detail = child.get("result") or {}
        error = detail.get("error") or ""
        invalid = attempt["invalidated"] or child["invalidated"]
        result.append(
            {
                "attempt_id": attempt["attempt_id"],
                "state": attempt["state"],
                "math_verdict": "UNKNOWN" if invalid else child["math_verdict"],
                "invalidated": invalid,
                "error": error[:1600],
                "error_truncated": len(error) > 1600,
            }
        )
    return result[-4:]


def suggest(store, raw):
    started = time.monotonic()
    bounded_data(raw)
    exact_object(
        raw,
        {"schema", "graph_id", "node_id", "budget", "timeout_seconds"},
        "Structure lemma suggestions",
    )
    if raw["schema"] != SCHEMA:
        raise ValueError("Use the versioned structure lemma suggestion schema")
    exact_object(raw["budget"], set(DEFAULT_BUDGET), "Explicit lemma search budget")
    for name, maximum in (
        ("max_depth", 6),
        ("max_rewrite_steps", 4),
        ("max_terms", 64),
        ("max_expansions", 4000),
        ("max_candidates", 4),
    ):
        if type(raw["budget"][name]) is not int or not 1 <= raw["budget"][name] <= maximum:
            raise ValueError(name + " requires an integer from 1 to " + str(maximum))
    timeout = raw["timeout_seconds"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        raise ValueError("Later candidate and independent replay share 1–30 worker seconds")
    context, graph = store.context(raw["graph_id"], raw["node_id"])
    target = context["target"]
    expected = compile_request(target)["formal_statement"]
    catalog = store.catalog(raw["graph_id"], raw["node_id"])
    if catalog["context"] != context:
        raise ValueError("Original complete context changed while reading sources")
    audits, groups = [], []
    count = 0
    for index, node in enumerate(context["depends_on"]):
        available = []
        for key in catalog["candidate_ids"]:
            if len(available) >= 2 or count >= MAX_SOURCE_CHECKS:
                break
            # Resolve the node from its explicit namespace, without trusting a
            # forged client descriptor or using source names as mathematical hints.
            if key.startswith("node."):
                if key.split(".", 2)[2] != node:
                    continue
            else:
                saved = store.read(key.removeprefix("run."))
                if saved["plan"]["request"]["node_id"] != node:
                    continue
            count += 1
            try:
                source, _ = store.load_source({"alias": "lemma" + str(index), "item_id": key})
            except ValueError as error:
                audits.append({"item_id": key, "available": False, "reason": str(error)[:500]})
                continue
            if source["graph_id"] != context["graph_id"] or source["node_id"] != node:
                raise ValueError("Resolved source belongs to another graph or prerequisite")
            if any(
                source["request"][k] != target[k] for k in ("carriers", "operations", "definitions")
            ):
                raise ValueError("Source structure environment differs from complete target")
            audits.append(
                {
                    "item_id": key,
                    "available": True,
                    "source_fingerprint": source["source_fingerprint"],
                    "node_id": node,
                }
            )
            available.append(source)
        groups.append(available)
    source_seconds = time.monotonic() - started
    engine = LemmaSearch(target, deepcopy(raw["budget"]))
    candidates, seen = [], set()
    rejected = 0
    try:
        for sources in product(*groups) if groups and all(groups) else ():
            if engine.source_sets >= MAX_SOURCE_SETS:
                engine.stop_reason = "source_set_limit"
                break
            engine.tick()
            engine.source_sets += 1
            engine.sources = sources
            for proof, used in engine.solve(target["goal"]):
                if used != {s["alias"] for s in sources}:
                    continue
                request = {
                    "schema": MODE,
                    "graph_id": raw["graph_id"],
                    "node_id": raw["node_id"],
                    "sources": [{"alias": s["alias"], "item_id": s["item_id"]} for s in sources],
                    "proof": proof,
                    "timeout_seconds": timeout,
                }
                key = canonical(request)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    compiled = compile_composition(request, target, sources)
                except ValueError:
                    rejected += 1
                    continue
                if compiled["target"] != target or compiled["formal_statement"] != expected:
                    raise ValueError("Suggested composition changed the complete original target")
                candidates.append(
                    {
                        "candidate_id": digest(request),
                        "request": request,
                        "formal_statement": expected,
                        "applications": compiled["applications"],
                        "proof_source": compiled["proof_source"],
                        "source_fingerprints": {
                            s["item_id"]: s["source_fingerprint"] for s in sources
                        },
                        "math_verdict": "UNKNOWN",
                        "evidence_level": "E0",
                    }
                )
                if len(candidates) >= raw["budget"]["max_candidates"]:
                    break
            if len(candidates) >= raw["budget"]["max_candidates"]:
                engine.stop_reason = "candidate_limit"
                break
    except SearchLimit:
        pass
    if not groups:
        engine.note("no_direct_prerequisites", target["goal"])
    elif not all(groups):
        engine.note(
            "current_e4_source_unavailable",
            target["goal"],
            missing_nodes=[n for n, g in zip(context["depends_on"], groups) if not g],
        )
    if not candidates:
        engine.note("no_complete_program_with_all_direct_sources_within_bounds", target["goal"])
    value = {
        "schema": SCHEMA,
        "context": context,
        "target": deepcopy(target),
        "formal_statement": expected,
        "source_manifest": manifest(store.root),
        "source_audits": audits,
        "feedback": feedback(graph, raw["node_id"], target),
        "candidates": candidates,
        "diagnostics": engine.diagnostics,
        "budget": {
            **raw["budget"],
            "synthesis_wall_seconds": WALL_SECONDS,
            "max_source_checks": MAX_SOURCE_CHECKS,
            "max_source_sets": MAX_SOURCE_SETS,
            "sources_per_prerequisite": 2,
            "timeout_seconds_for_later_check": timeout,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "lean_calls": 0,
        "model_calls": 0,
        "reference_proof_used": False,
        "requires_preview_and_named_confirmation": True,
        "limitations": [
            "Only current direct prerequisite sources from the same graph and exact shared environment; no new assumptions or graph dependencies.",
            "Diagnostics are bounded symbolic obligations, separate from stored actual Lean feedback; an unmatched condition is not a counterexample.",
            "Typed finite argument pool, direct applications, conjunction, equality reversal/transitivity and congruence outside lexical binders; not complete proof search.",
            "At most two available sources per prerequisite, eight source audits and source sets; preparation/integrity time is separate from the shared synthesis checkpoint.",
            "Every selected candidate needs a new full preview, named confirmation, real proof-data import and independent replay; original graph judgment is separate.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    value["stats"] = {
        "expansions": engine.expansions,
        "source_sets": engine.source_sets,
        "pool_terms": len(engine.pool),
        "pool_truncated": engine.pool_truncated,
        "rejected_programs": rejected,
        "source_preparation_seconds": round(source_seconds, 6),
        "synthesis_seconds": round(time.monotonic() - engine.started, 6),
        "total_seconds": round(time.monotonic() - started, 6),
        "stop_reason": engine.stop_reason,
    }
    return value
