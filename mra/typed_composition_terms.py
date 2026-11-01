"""Explicit typed proof applications; complete conditions and capture-free substitution."""

from copy import deepcopy

from .formalizer import exact_object
from .number_theory_terms import identifier
from .typed_terms import Compiler, bounded_data, compile_request

MODE = "typed-proof-composition-v1"
FIELDS = {"schema", "graph_id", "node_id", "sources", "proof", "timeout_seconds"}
LIMITS = {"proof_nodes": 240, "proof_depth": 14, "expression_nodes": 320}


def substitute(formula, substitution, target_names):
    """Simultaneously substitute free parameters and alpha-rename quantified binders."""
    used = set(target_names) | set(substitution)

    def names(value):
        if isinstance(value, dict):
            if set(value) == {"var"}:
                used.add(value["var"])
            for op, body in value.items():
                if op in ("forall", "exists"):
                    used.update(b["name"] for b in body["binders"])
                names(body)
        elif isinstance(value, list):
            for item in value:
                names(item)

    names(formula)
    for term in substitution.values():
        names(term)

    def walk(value, bound):
        if isinstance(value, dict):
            if set(value) == {"var"}:
                key = value["var"]
                if key in bound:
                    return {"var": bound[key]}
                return deepcopy(substitution.get(key, value))
            if len(value) == 1 and next(iter(value)) in ("forall", "exists"):
                op, body = next(iter(value.items()))
                inner, binders = dict(bound), []
                for binder in body["binders"]:
                    i = 0
                    while f"instq{i}" in used:
                        i += 1
                    name = f"instq{i}"
                    used.add(name)
                    inner[binder["name"]] = name
                    binders.append({**binder, "name": name})
                return {op: {"binders": binders, "body": walk(body["body"], inner)}}
            return {k: walk(v, bound) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, bound) for v in value]
        return value

    return walk(formula, {})


def compile_composition(raw, target, sources):
    bounded_data(raw)
    exact_object(raw, FIELDS, "Typed proof composition")
    if raw["schema"] != MODE:
        raise ValueError("Use the explicit typed proof composition protocol")
    timeout = raw["timeout_seconds"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        raise ValueError("Candidate and independent replay share 1–30 worker seconds")
    raw = deepcopy(raw)
    compiled = compile_request(target)
    compiler = Compiler()
    compiler.nodes = compiled["node_count"]
    compiler.definitions = {
        d["name"]: {**d, "lean": detail["lean"]}
        for d, detail in zip(target["definitions"], compiled["definitions"])
    }
    scope = compiled["variable_mapping"]
    hmap = {a["name"]: f"mra_h{i}" for i, a in enumerate(target["assumptions"])}
    by_alias = {s["alias"]: (i, s) for i, s in enumerate(sources)}
    if (
        not 1 <= len(sources) <= 4
        or len(by_alias) != len(sources)
        or raw["sources"] != [{"alias": s["alias"], "item_id": s["item_id"]} for s in sources]
    ):
        raise ValueError("One to four exact distinct source bindings are required")
    for source in sources:
        identifier(source["alias"])
        original = compile_request(source["request"])
        if original["formal_statement"] != source["formal_statement"]:
            raise ValueError("Source signature differs from its complete typed target")
        if source["request"]["definitions"] != target["definitions"]:
            raise ValueError("Source and target require exactly the same closed definitions")
    used, applications, nodes = set(), [], 0

    def term(value):
        return compiler.term(value, scope)

    def proposition(value):
        typ, rendered = term(value)
        if typ != "Prop":
            raise ValueError("An instantiated premise or conclusion must be Prop")
        return rendered

    def proof(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > LIMITS["proof_nodes"] or depth > LIMITS["proof_depth"]:
            raise ValueError("Proof exceeds 240 nodes / depth 14")
        if not isinstance(value, dict):
            raise ValueError("Expected a structured proof")
        if set(value) == {"hyp"}:
            name = identifier(value["hyp"])
            if name not in hmap:
                raise ValueError("Undeclared original hypothesis")
            return hmap[name]
        if set(value) == {"lemma", "terms", "proofs"}:
            alias = identifier(value["lemma"])
            if alias not in by_alias:
                raise ValueError("Unknown explicitly bound source lemma")
            slot, source = by_alias[alias]
            signature = source["request"]
            terms, proofs = value["terms"], value["proofs"]
            if (
                not isinstance(terms, list)
                or not isinstance(proofs, list)
                or len(terms) != len(signature["variables"])
                or len(proofs) != len(signature["assumptions"])
            ):
                raise ValueError(
                    "Every source variable and original premise needs an explicit argument"
                )
            arguments, substitution = [], {}
            for parameter, argument in zip(signature["variables"], terms):
                typ, rendered = term(argument)
                if typ != parameter["type"]:
                    raise ValueError("Source argument type differs; use an explicit cast")
                arguments.append(rendered)
                substitution[parameter["name"]] = argument
            premises = [
                {**a, "formula": substitute(a["formula"], substitution, scope)}
                for a in signature["assumptions"]
            ]
            goal = substitute(signature["goal"], substitution, scope)
            detail = {
                "alias": alias,
                "item_id": source["item_id"],
                "substitution": deepcopy(substitution),
                "instantiated_assumptions": premises,
                "instantiated_goal": goal,
                "rendered_assumptions": [proposition(a["formula"]) for a in premises],
                "rendered_goal": proposition(goal),
                "premise_proofs": deepcopy(proofs),
            }
            applications.append(detail)
            arguments.extend(proof(p, depth + 1) for p in proofs)
            used.add(alias)
            return (
                f"(@MraCandidate.Reused.s{slot}.checked"
                + "".join(f" ({a})" for a in arguments)
                + ")"
            )
        if len(value) == 1:
            op, args = next(iter(value.items()))
            if (
                op in ("pair", "mp", "mpr", "apply", "trans")
                and isinstance(args, list)
                and len(args) == 2
            ):
                a, b = (proof(p, depth + 1) for p in args)
                if op == "apply":
                    return f"(({a}) ({b}))"
                constructor = {
                    "pair": "And.intro",
                    "mp": "Iff.mp",
                    "mpr": "Iff.mpr",
                    "trans": "Eq.trans",
                }[op]
                return f"({constructor} ({a}) ({b}))"
            if op in ("fst", "snd", "symm"):
                constructor = {"fst": "And.left", "snd": "And.right", "symm": "Eq.symm"}[op]
                return f"({constructor} ({proof(args, depth + 1)}))"
            if op == "refl":
                typ, rendered = term(args)
                if typ == "Prop":
                    raise ValueError("Reflexivity requires a numeric term")
                return f"(Eq.refl ({rendered}))"
        raise ValueError(
            "Unsupported proof constructor; arbitrary Lean and tactic fallback are forbidden"
        )

    body = proof(raw["proof"])
    if used != set(by_alias):
        raise ValueError("Every declared source must be explicitly used")
    binders = [f"({v['lean']} : {v['type']})" for v in scope.values()] + [
        f"({h} : {a['formula']})" for h, a in zip(hmap.values(), compiled["assumptions"])
    ]
    body = f"fun {' '.join(binders)} => {body}" if binders else body
    # Definitions must also be in the proof's scope, not only in its expected type.
    proof_source = "\n".join([*compiled["formal_statement"].splitlines()[:-1], body])
    return {
        "request": raw,
        "target": deepcopy(target),
        "formal_statement": compiled["formal_statement"],
        "variable_mapping": scope,
        "definitions": compiled["definitions"],
        "assumptions": compiled["assumptions"],
        "proof_source": proof_source,
        "applications": applications,
        "node_count": nodes + compiler.nodes,
    }
