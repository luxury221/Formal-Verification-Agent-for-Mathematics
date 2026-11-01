"""Simultaneous, bounded expansion for visible authored mechanism comparisons."""

from copy import deepcopy

from .domain import canonical
from .formalizer import exact_object
from .lemma_composition_terms import MODE, compile_composition
from .number_theory_terms import MODE as TARGET_MODE, compile_request

ALIAS = "prior"
ITEM = "eval-source"


def expand(proof, source):
    """No local binders in this AST; inserted actual arguments are never revisited."""
    count = 0

    def copy(value, variables, hypotheses, depth=0):
        nonlocal count
        count += 1
        if count > 4096 or depth > 64:
            raise ValueError("Expansion exceeds depth 64 / 4096 traversal nodes")
        if isinstance(value, dict):
            if set(value) == {"var"} and value["var"] in variables:
                return deepcopy(variables[value["var"]])
            if set(value) == {"hyp"} and value["hyp"] in hypotheses:
                return deepcopy(hypotheses[value["hyp"]])
            if "lemma" in value:
                exact_object(value, {"lemma", "terms", "proofs"}, "Expansion application")
                if value["lemma"] != ALIAS or variables or hypotheses:
                    raise ValueError(
                        "Only the single direct, nonrecursive prerequisite is supported"
                    )
                terms = value["terms"]
                proofs = [copy(p, {}, {}, depth + 1) for p in value["proofs"]]
                if len(terms) != len(source["variables"]) or len(proofs) != len(
                    source["assumptions"]
                ):
                    raise ValueError("Every source variable and premise needs an actual argument")
                return copy(
                    source["proof"],
                    dict(zip(source["variables"], terms)),
                    dict(zip((h["name"] for h in source["assumptions"]), proofs)),
                    depth + 1,
                )
            return {k: copy(v, variables, hypotheses, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [copy(v, variables, hypotheses, depth + 1) for v in value]
        return value

    result = copy(proof, {}, {})
    if len(canonical(result)) > 196608:
        raise ValueError("Expanded program exceeds 192 KiB")
    return result


def compile_case(case):
    exact_object(
        case, {"id", "source", "source_available", "target", "proof", "note"}, "Paired case"
    )
    if type(case["source_available"]) is not bool:
        raise ValueError("Source availability must be explicit")
    source = compile_request(case["source"])
    if source["request"] != case["source"] or source["request"]["timeout_seconds"] != 30:
        raise ValueError("Canonical source and fixed 30 second budget required")
    request = {
        **case["target"],
        "schema": MODE,
        "proof": case["proof"],
        "sources": [{"alias": ALIAS, "item_id": ITEM}],
    }
    # Validate the complete target and the entire application tree before expansion.
    compiled = compile_composition(
        request, [{"alias": ALIAS, "item_id": ITEM, "request": case["source"]}]
    )
    expected = {k: v for k, v in compiled["request"].items() if k not in ("proof", "sources")}
    expected["schema"] = TARGET_MODE
    if expected != case["target"] or expected["timeout_seconds"] != 30:
        raise ValueError("Canonical complete target and fixed 30 second budget required")
    expanded = {**case["target"], "proof": expand(case["proof"], case["source"])}
    error = None
    try:
        inline = compile_request(expanded)
        if inline["formal_statement"] != compiled["formal_statement"]:
            raise ValueError("Expansion changed the complete target")
    except ValueError as problem:
        if "depth or node budget" not in str(problem):
            raise
        error = str(problem)
    return {
        **deepcopy(case),
        "formal_statement": compiled["formal_statement"],
        "import_request": request,
        "inline_request": expanded,
        "inline_blocked": error,
    }
