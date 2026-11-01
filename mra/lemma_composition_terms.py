"""Explicit application of archived Nat theorems; no candidate Lean source."""

from copy import deepcopy

from .domain import canonical
from .formalizer import exact_object, strict_json
from .number_theory_terms import MODE as TARGET_MODE, REFERENCES, compile_request

MODE = "private-lemma-composition-v1"
DUMMY = {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}


def target(raw):
    """Use the existing bounded target compiler without accepting its proof input."""
    return compile_request({**raw, "schema": TARGET_MODE, "proof": deepcopy(DUMMY)})


def compile_composition(raw, sources):
    if len(canonical(raw)) > 196608:
        raise ValueError("Composition input exceeds 192 KiB")
    raw = strict_json(canonical(raw).decode())
    exact_object(
        raw,
        {
            "schema",
            "title",
            "original",
            "variables",
            "assumptions",
            "goal",
            "proof",
            "sources",
            "timeout_seconds",
        },
        "Lemma composition",
    )
    if raw["schema"] != MODE:
        raise ValueError("Unsupported lemma composition schema")
    complete = {k: v for k, v in raw.items() if k not in ("proof", "sources")}
    compiled = target(complete)
    vmap = compiled["variable_mapping"]
    hmap = {h["name"]: f"mra_h{i}" for i, h in enumerate(raw["assumptions"])}
    by_alias = {s["alias"]: (i, s) for i, s in enumerate(sources)}
    if len(by_alias) != len(sources) or raw["sources"] != [
        {"alias": s["alias"], "item_id": s["item_id"]} for s in sources
    ]:
        raise ValueError("Exact source bindings required")
    nodes = 0
    used = set()
    references = set()
    applications = []

    def tick(depth):
        nonlocal nodes
        nodes += 1
        if depth > 14 or nodes > 240:
            raise ValueError("Composition exceeds depth 14 / 240 proof and term nodes")

    def expression(value, depth):
        tick(depth)
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("Expected one Nat expression constructor")
        op, arg = next(iter(value.items()))
        if op == "var" and isinstance(arg, str) and arg in vmap:
            return vmap[arg]
        if op == "nat" and type(arg) is int and 0 <= arg <= 1000000:
            return f"({arg} : Nat)"
        if op in ("add", "mul", "sub", "gcd") and isinstance(arg, list) and len(arg) == 2:
            a, b = (expression(v, depth + 1) for v in arg)
            return (
                f"(Nat.gcd {a} {b})"
                if op == "gcd"
                else f"({a} { {'add': '+', 'mul': '*', 'sub': '-'}[op] } {b})"
            )
        raise ValueError("Unknown Nat expression or variable in theorem application")

    def proof(value, depth=0):
        tick(depth)
        if not isinstance(value, dict):
            raise ValueError("Expected a structured proof")
        if set(value) == {"hyp"} and isinstance(value["hyp"], str) and value["hyp"] in hmap:
            return hmap[value["hyp"]]
        if set(value) in ({"lemma", "terms", "proofs"}, {"ref", "terms", "proofs"}):
            private = "lemma" in value
            name = value["lemma" if private else "ref"]
            if not isinstance(name, str) or name not in (by_alias if private else REFERENCES):
                raise ValueError("Unknown bound private lemma or library reference")
            if private:
                index, source = by_alias[name]
                signature = source["request"]
                nterms, nproofs = len(signature["variables"]), len(signature["assumptions"])
                constant = f"MraCandidate.Reused.s{index}.checked"
                used.add(name)
            else:
                nterms, nproofs, *_ = REFERENCES[name]
                constant = name
                references.add(name)
            terms, proofs = value["terms"], value["proofs"]
            if (
                not isinstance(terms, list)
                or not isinstance(proofs, list)
                or (len(terms), len(proofs)) != (nterms, nproofs)
            ):
                raise ValueError("Every theorem variable and premise needs an explicit argument")
            arguments = [expression(t, depth + 1) for t in terms] + [
                proof(p, depth + 1) for p in proofs
            ]
            if private:
                substitution = dict(zip(signature["variables"], deepcopy(terms)))

                def replace(v):
                    if isinstance(v, dict):
                        if set(v) == {"var"}:
                            return deepcopy(substitution[v["var"]])
                        return {k: replace(x) for k, x in v.items()}
                    if isinstance(v, list):
                        return [replace(x) for x in v]
                    return v

                instantiated = {
                    **complete,
                    "assumptions": replace(signature["assumptions"]),
                    "goal": replace(signature["goal"]),
                }
                # Source binder names need not be disjoint from target variable names.
                premise_names = [f"premise{i}" for i in range(12) if f"premise{i}" not in vmap]
                instantiated["assumptions"] = [
                    {"name": name, "formula": h["formula"]}
                    for name, h in zip(premise_names, instantiated["assumptions"])
                ]
                preview = target(instantiated)
                applications.append(
                    {
                        "alias": name,
                        "item_id": source["item_id"],
                        "substitution": substitution,
                        "instantiated_assumptions": instantiated["assumptions"],
                        "instantiated_goal": instantiated["goal"],
                        "formal_statement": preview["formal_statement"],
                        "premise_proofs": deepcopy(proofs),
                    }
                )
            return f"(@{constant}{''.join(' (' + a + ')' for a in arguments)})"
        if len(value) == 1:
            op, args = next(iter(value.items()))
            if op in ("pair", "mp", "mpr") and isinstance(args, list) and len(args) == 2:
                constructor = {"pair": "And.intro", "mp": "Iff.mp", "mpr": "Iff.mpr"}[op]
                return (
                    f"({constructor} ({proof(args[0], depth + 1)}) ({proof(args[1], depth + 1)}))"
                )
        raise ValueError("Unknown proof constructor or undeclared hypothesis")

    body = proof(raw["proof"])
    if used != set(by_alias):
        raise ValueError("Every declared private source must be explicitly used")
    binders = [f"({v} : Nat)" for v in vmap.values()] + [
        f"({h} : {f})" for h, f in zip(hmap.values(), compiled["assumptions"])
    ]
    return {
        "request": {
            **raw,
            "title": compiled["request"]["title"],
            "original": compiled["request"]["original"],
            "timeout_seconds": compiled["request"]["timeout_seconds"],
        },
        "formal_statement": compiled["formal_statement"],
        "variable_mapping": vmap,
        "assumptions": compiled["assumptions"],
        "proof_source": f"fun {' '.join(binders)} => {body}" if binders else body,
        "references": sorted(references),
        "applications": applications,
        "node_count": nodes + compiled["node_count"],
    }
