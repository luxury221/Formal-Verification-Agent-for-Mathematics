"""Bounded paired programs: archived structure proof versus the identical fixed tactic inline."""

from copy import deepcopy

from .domain import canonical
from .formalizer import exact_object
from .structure_composition_terms import MODE, compile_composition
from .structured_proofs import compile_proof
from .structured_terms import compile_request
from .typed_terms import bounded_data

SCHEMA = "structure-proof-reuse-evaluation-v1"
IMPORT_MODE = "structure-evaluation-import-v1"
INLINE_MODE = "structure-evaluation-inline-v1"
ALIAS = "prior"
ITEM = "eval-source"
FIELDS = {"schema", "source", "target", "proof", "timeout_seconds"}


def source_program(source):
    """Use identical closed controlled induction/rewrite program in both arms."""
    compiled = compile_request(source)
    # Clear every local the frozen composition compiler can place around a call,
    # in dependency order, before introducing the closed source's own binders.
    names = ["mra_c0"] + [
        f"mra_{prefix}{i}"
        for prefix, count in (("h", 8), ("v", 8), ("d", 6), ("o", 8), ("t", 2))
        for i in reversed(range(count))
    ]
    boundary = "".join("  try clear " + name + "\n" for name in names)
    return compile_proof(source["proof"], compiled).replace("by\n", "by\n" + boundary, 1)


def compile_program(request):
    bounded_data(request)
    exact_object(request, FIELDS, "Paired structure program")
    if request["schema"] not in (IMPORT_MODE, INLINE_MODE):
        raise ValueError("Use an explicit private structure evaluation program")
    source, target = (compile_request(request[k]) for k in ("source", "target"))
    if (
        type(request["timeout_seconds"]) is not int
        or request["timeout_seconds"] != 30
        or any(
            type(request[k]["timeout_seconds"]) is not int or request[k]["timeout_seconds"] != 30
            for k in ("source", "target")
        )
    ):
        raise ValueError("Every source and target has the same fixed 30 second worker budget")
    raw = {
        "schema": MODE,
        "graph_id": "evaluation-only",
        "node_id": "target",
        "sources": [{"alias": ALIAS, "item_id": ITEM}],
        "proof": request["proof"],
        "timeout_seconds": 30,
    }
    descriptor = {
        "alias": ALIAS,
        "item_id": ITEM,
        "request": request["source"],
        "formal_statement": source["formal_statement"],
    }
    compiled = compile_composition(raw, request["target"], [descriptor])
    if compiled["formal_statement"] != target["formal_statement"]:
        raise ValueError("The complete target changed during composition")
    if request["schema"] == INLINE_MODE:
        # The source type is closed. Its generated binders and definitions are scoped
        # inside this show expression, never textually substituted into the caller.
        strategy = source_program(request["source"])
        closed = (
            "(show "
            + source["formal_statement"].replace("\n", "\n  ")
            + " from\n  ("
            + strategy.replace("\n", "\n  ")
            + "))"
        )
        token = "@MraCandidate.Reused.s0.checked"
        if compiled["proof_source"].count(token) != len(compiled["applications"]):
            raise ValueError("The frozen compiler's source token format changed")
        compiled["proof_source"] = compiled["proof_source"].replace(token, closed)
        if "MraCandidate.Reused" in compiled["proof_source"]:
            raise ValueError("Inline programs must not refer to archived declarations")
        if len(compiled["proof_source"].encode()) > 196608:
            raise ValueError("Expanded structure proof program exceeds 192 KiB")
    return {**compiled, "request": deepcopy(request)}


def compile_case(case):
    exact_object(
        case,
        {"id", "source", "source_available", "target", "proof", "note", "exposure"},
        "Structure paired case",
    )
    if case["exposure"] != "author-visible":
        raise ValueError("Authored cases cannot claim independent or hidden exposure")
    if type(case["source_available"]) is not bool:
        raise ValueError("Source archive availability must be explicit")
    base = {k: deepcopy(case[k]) for k in ("source", "target", "proof")}
    imported = compile_program({"schema": IMPORT_MODE, **base, "timeout_seconds": 30})
    inline_request = {"schema": INLINE_MODE, **base, "timeout_seconds": 30}
    inline = compile_program(inline_request)
    if imported["formal_statement"] != inline["formal_statement"]:
        raise ValueError("Both arms must check the byte-identical complete type")
    if len(canonical(case)) > 196608:
        raise ValueError("Paired case exceeds 192 KiB")
    return {
        **deepcopy(case),
        "formal_statement": imported["formal_statement"],
        "import_request": imported["request"],
        "inline_request": inline_request,
        "inline_blocked": None,
        "programs": {
            "source": source_program(case["source"]),
            "import": imported["proof_source"],
            "inline": inline["proof_source"],
        },
        "application_count": len(imported["applications"]),
        "applications": imported["applications"],
    }
