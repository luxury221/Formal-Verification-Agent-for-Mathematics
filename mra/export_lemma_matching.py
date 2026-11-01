"""Bounded discovery of current standalone exports; suggestions are never evidence."""

from copy import deepcopy
from pathlib import Path
import time

from .domain import canonical, digest
from .formalizer import exact_object
from .integrity import file_hash
from .structure_composition_terms import MODE, compile_composition
from .structure_lemma_library import APPLICATION, explicit, manifest as application_manifest
from .structure_lemma_search import LemmaSearch
from .structure_lemma_suggestions import DEFAULT_BUDGET, FILES as SEARCH_FILES
from .structure_strategy_terms import SearchLimit, WALL_SECONDS
from .structured_terms import FIELDS, compile_request
from .typed_terms import bounded_data

SCHEMA = "export-lemma-matching-v1"
MAX_HEADERS = 200
MAX_AUDITS = 8
FILES = ("mra/export_lemma_matching.py", *SEARCH_FILES)


def demo_target(store):
    """Derive a disclosed example from a live export, retaining its exact environment."""
    with store.exports.transaction(False) as db:
        ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
    checked = 0
    for key in ids:
        row = store.exports.read(key)
        if row["invalidated"] or row["math_verdict"] != "PROVED" or row["evidence_level"] != "E4":
            continue
        if checked >= MAX_AUDITS:
            break
        checked += 1
        try:
            source, _ = store.load_source({"alias": "core", "item_id": "export." + key})
        except ValueError:
            continue
        target = deepcopy(source["request"])
        del target["proof"]
        if set(target["goal"]) != {"eq"}:
            continue
        equation = target["goal"]
        target.update(
            title="已证等式的双向新目标",
            original="在以下完整原环境与全部原条件下，同时证明已导出等式及其反向等式。此目标由已知结论构造，只用于体验复用流程。",
            goal={"and": [equation, {"eq": equation["eq"][::-1]}]},
        )
        return {
            "target": target,
            "source_item_id": source["item_id"],
            "source_fingerprint": source["source_fingerprint"],
            "provenance": "Project-authored derivation from a disclosed current export; not a blind task",
            "lean_calls": 0,
        }
    raise ValueError("No current equality export available for the disclosed demonstration")


def manifest(root):
    return {
        "application_provider": application_manifest(Path(root), APPLICATION),
        "files": {n: file_hash(Path(root) / n) for n in FILES},
    }


def symbols(value):
    """Syntactic hints only; ranking never authenticates a source."""
    result = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if set(item) == {"call"}:
                result.add(item["call"]["name"])
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


def suggest(store, raw):
    started = time.monotonic()
    bounded_data(raw)
    exact_object(raw, {"schema", "target", "budget"}, "Export lemma matching")
    if raw["schema"] != SCHEMA:
        raise ValueError("Use export-lemma-matching-v1")
    exact_object(raw["target"], FIELDS - {"proof"}, "Full new target without proof")
    exact_object(raw["budget"], set(DEFAULT_BUDGET), "Explicit export matching budget")
    for name, maximum in (
        ("max_depth", 6),
        ("max_rewrite_steps", 4),
        ("max_terms", 64),
        ("max_expansions", 4000),
        ("max_candidates", 4),
    ):
        if type(raw["budget"][name]) is not int or not 1 <= raw["budget"][name] <= maximum:
            raise ValueError(name + " requires an integer from 1 to " + str(maximum))
    target = deepcopy(raw["target"])
    expected = compile_request(explicit(target))["formal_statement"]
    with store.exports.transaction(False) as db:
        ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 201")]
    truncated = len(ids) > MAX_HEADERS
    audits, ranked = [], []
    hints = symbols(target["goal"])
    for index, key in enumerate(ids[:MAX_HEADERS]):
        try:
            row = store.exports.read(key)
        except ValueError as error:
            if len(audits) < MAX_AUDITS:
                audits.append(
                    {"item_id": "export." + key, "available": False, "reason": str(error)[:500]}
                )
            continue
        if row["invalidated"] or row["math_verdict"] != "PROVED" or row["evidence_level"] != "E4":
            continue
        signature = row["plan"]["target"]
        if any(signature[k] != target[k] for k in ("carriers", "operations", "definitions")):
            continue
        score = len(hints & symbols(signature["goal"]))
        ranked.append((-score, index, key))
    sources, checked = [], 0
    for _, _, key in sorted(ranked)[:MAX_AUDITS]:
        checked += 1
        binding = {"alias": "lemma" + str(len(sources)), "item_id": "export." + key}
        try:
            source, _ = store.load_source(binding)
        except ValueError as error:
            audits.append(
                {"item_id": binding["item_id"], "available": False, "reason": str(error)[:500]}
            )
            continue
        if any(
            source["request"][k] != target[k] for k in ("carriers", "operations", "definitions")
        ):
            raise ValueError("Live source environment changed during discovery")
        sources.append(source)
        audits.append(
            {
                "item_id": source["item_id"],
                "available": True,
                "source_fingerprint": source["source_fingerprint"],
                "formal_statement": source["formal_statement"],
            }
        )
    source_seconds = time.monotonic() - started
    engine = LemmaSearch(explicit(target), deepcopy(raw["budget"]))
    engine.sources = sources
    candidates, seen, rejected = [], set(), 0
    try:
        if sources:
            for proof, used in engine.solve(target["goal"]):
                if not used:
                    continue
                if len(used) > 4:
                    engine.note("application_source_limit", target["goal"])
                    continue
                selected = [s for s in sources if s["alias"] in used]
                request = {
                    "schema": APPLICATION,
                    "target": deepcopy(target),
                    "sources": [{"alias": s["alias"], "item_id": s["item_id"]} for s in selected],
                    "proof": proof,
                    "timeout_seconds": target["timeout_seconds"],
                }
                key = canonical(request)
                if key in seen:
                    continue
                seen.add(key)
                inner = {k: request[k] for k in ("sources", "proof", "timeout_seconds")}
                inner.update(
                    schema=MODE, graph_id="independent-export", node_id="independent-target"
                )
                try:
                    compiled = compile_composition(inner, explicit(target), selected)
                except ValueError:
                    rejected += 1
                    continue
                if compiled["formal_statement"] != expected:
                    raise ValueError("Matching changed the complete original target")
                candidates.append(
                    {
                        "candidate_id": digest(request),
                        "request": request,
                        "formal_statement": expected,
                        "applications": compiled["applications"],
                        "proof_source": compiled["proof_source"],
                        "source_fingerprints": {
                            s["item_id"]: s["source_fingerprint"] for s in selected
                        },
                        "math_verdict": "UNKNOWN",
                        "evidence_level": "E0",
                    }
                )
                if len(candidates) >= raw["budget"]["max_candidates"]:
                    engine.stop_reason = "candidate_limit"
                    break
    except SearchLimit:
        pass
    if not sources:
        engine.note("no_current_compatible_export_within_bounds", target["goal"])
    if not candidates:
        engine.note("no_complete_application_within_bounds", target["goal"])
    value = {
        "schema": SCHEMA,
        "target": target,
        "formal_statement": expected,
        "source_manifest": manifest(store.root),
        "source_audits": audits,
        "candidates": candidates,
        "diagnostics": engine.diagnostics,
        "budget": {
            **raw["budget"],
            "max_catalog_headers": MAX_HEADERS,
            "max_live_source_audits": MAX_AUDITS,
            "maximum_imported_sources": 4,
            "synthesis_wall_seconds": WALL_SECONDS,
            "timeout_seconds_for_later_check": target["timeout_seconds"],
        },
        "catalog_truncated": truncated or len(ranked) > MAX_AUDITS,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "lean_calls": 0,
        "model_calls": 0,
        "requires_preview_and_named_confirmation": True,
        "limitations": [
            "Only current standalone exports in the canonical ledger; untrusted headers only rank audits.",
            "Exact shared carriers, operations and definitions; every original premise needs a proof.",
            "Bounded typed arguments, conjunction and equality rewriting outside lexical binders; incomplete search.",
            "Suggestions remain UNKNOWN/E0; re-preview live fingerprints before named checking and independent replay.",
            "Source auditing and compilation time is separate from synthesis checkpoints; no end-to-end hard deadline.",
            "No original graph promotion, source-environment renaming, model call or novelty claim.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    value["stats"] = {
        "headers_scanned": min(len(ids), MAX_HEADERS),
        "live_source_audits": checked,
        "compatible_headers": len(ranked),
        "available_sources": len(sources),
        "expansions": engine.expansions,
        "typed_terms": len(engine.pool),
        "term_pool_truncated": engine.pool_truncated,
        "compiler_rejections": rejected,
        "stop_reason": engine.stop_reason,
        "source_seconds": source_seconds,
        "elapsed_seconds": time.monotonic() - started,
    }
    return value
