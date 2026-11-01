"""Attained real minima and fixed least-squares rules; preserves proof v1/v2."""

from copy import deepcopy
from textwrap import indent

from . import proof_logic as prior
from . import calculus_language as calculus
from .analysis_language import REAL, array, integer, symbol, text
from .formalizer import exact_object
from .optimization_kernels import MINIMUM, MINIMUM_STATEMENT, DERIVATIVE, STATIONARY, instantiate

MODE = "proof-language-v3"
LIMITS = dict(prior.LIMITS)
REWRITES = (*prior.REWRITES, "norm_sub_rev", "real_inner_comm")
RULES = {
    "least_squares_minimum": (["vector", "vector"], 0),
    "least_squares_derivative": (["vector", "vector", "real"], 0),
    "least_squares_stationary": (["vector", "vector", "real"], 1),
}
bounded = prior.bounded


class Compiler(prior.Compiler):
    def __init__(self, spaces):
        super().__init__(spaces)
        self.minimum_helpers = {}

    def minimum_rule(self, typed):
        typ = typed[0][1]
        if typ not in self.minimum_helpers:
            name = self.fresh_proof()
            terms = ["mraMinU", "mraMinV"]
            statement = instantiate(MINIMUM_STATEMENT, terms).strip()
            evidence = instantiate(MINIMUM, terms).strip()
            declaration = (
                f"have {name} (mraMinU mraMinV : {self.type_source(typ)}) : {statement} :=\n"
                + indent(evidence, "  ")
            )
            self.minimum_helpers[typ] = (name, declaration)
        name = self.minimum_helpers[typ][0]
        return "(" + name + " " + " ".join("(" + term + ")" for term, _ in typed) + ")"

    def formula(self, raw, scope):
        if isinstance(raw, dict) and len(raw) == 1 and "is_minimum" in raw:
            value = raw["is_minimum"]
            exact_object(value, {"function", "value"}, "Attained real minimum")
            function = self.require(value["function"], scope, ("function", REAL, REAL))
            minimum = self.require(value["value"], scope, REAL)
            source = self.fresh_proof()
            return (
                f"((∃ ({source} : ℝ), ({function}) {source} = ({minimum})) ∧ "
                f"(∀ ({source} : ℝ), ({minimum}) ≤ ({function}) {source}))"
            )
        return super().formula(raw, scope)

    def rewrite_terms(self, entries, scope, hypotheses):
        terms = []
        for item in array(entries, 8, "Explicit optimization rewrites"):
            if not isinstance(item, dict):
                raise ValueError("A rewrite requires a fixed theorem or proved fact")
            key = "lemma" if "lemma" in item else "proof"
            exact_object(item, {key, "reverse"}, "Explicit rewrite")
            if type(item["reverse"]) is not bool:
                raise ValueError("Rewrite direction must be a boolean")
            if key == "lemma":
                if not isinstance(item[key], str) or item[key] not in REWRITES:
                    raise ValueError("Unknown fixed optimization rewrite")
                term = item[key]
            else:
                term = "(" + self.proof(item[key], scope, hypotheses) + ")"
            terms.append(("← " if item["reverse"] else "") + term)
        return terms

    def proof(self, raw, scope, hypotheses):
        if isinstance(raw, dict) and len(raw) == 1:
            op, value = next(iter(raw.items()))
            if op in ("steps", "have"):
                exact_object(
                    value,
                    {"lemmas", "then"} if op == "steps" else {"name", "formula", "proof", "then"},
                    "Sequential proved lemmas" if op == "steps" else "Proved local lemma",
                )
                lemmas = (
                    array(value["lemmas"], 8, "Sequential lemmas")
                    if op == "steps"
                    else [{key: value[key] for key in ("name", "formula", "proof")}]
                )
                local, declarations = dict(hypotheses), []
                for lemma in lemmas:
                    exact_object(lemma, {"name", "formula", "proof"}, "Sequential lemma")
                    name = self.new_name(lemma["name"], scope, local)
                    statement = self.formula(lemma["formula"], scope)
                    # Prove before binding: neither self-reference nor forward evidence.
                    evidence = self.proof(lemma["proof"], scope, local)
                    source = self.fresh_proof()
                    declarations.append(
                        f"have {source} : {statement} :=\n" + indent(evidence.strip(), "  ")
                    )
                    local[name] = source
                    self.proof_steps.append(
                        {
                            "kind": "have",
                            "name": name,
                            "lean": source,
                            "formal_statement": statement,
                        }
                    )
                body = self.proof(value["then"], scope, local)
                declarations.append("exact (\n" + indent(body.strip(), "  ") + "\n)")
                return "(by\n" + indent("\n".join(declarations), "  ") + "\n)"
        if isinstance(raw, dict) and len(raw) == 1 and "rule" in raw:
            value = raw["rule"]
            if (
                isinstance(value, dict)
                and isinstance(value.get("name"), str)
                and value["name"] in RULES
            ):
                exact_object(value, {"name", "terms", "proofs"}, "Fixed least-squares rule")
                name = value["name"]
                kinds, count = RULES[name]
                terms = array(value["terms"], 3, "Optimization terms")
                proofs = array(value["proofs"], 1, "Optimization proofs")
                if (len(terms), len(proofs)) != (len(kinds), count):
                    raise ValueError("Optimization rule argument counts differ")
                typed = [self.expr(term, scope) for term in terms]
                for (_, typ), kind in zip(typed, kinds, strict=True):
                    if kind == "real" and typ != REAL:
                        raise ValueError("Optimization scalar must be Real")
                    if kind == "vector":
                        self.vector(typ)
                if typed[0][1] != typed[1][1]:
                    raise ValueError("Least-squares vectors require the same space")
                ps = [self.proof(proof, scope, hypotheses) for proof in proofs]
                self.proof_steps.append({"kind": "rule", "name": name})
                if name == "least_squares_minimum":
                    return self.minimum_rule(typed)
                template = {
                    "least_squares_minimum": MINIMUM,
                    "least_squares_derivative": DERIVATIVE,
                    "least_squares_stationary": STATIONARY,
                }[name]
                return instantiate(template, [term for term, _ in typed], ps)
        return super().proof(raw, scope, hypotheses)


def compile_request(raw):
    bounded(raw)
    exact_object(
        raw,
        {
            "schema",
            "title",
            "original",
            "spaces",
            "variables",
            "assumptions",
            "goal",
            "proof",
            "timeout_seconds",
        },
        "Complete proof request",
    )
    if raw["schema"] != MODE:
        raise ValueError("Select proof-language-v3 explicitly")
    text(raw["title"], 200, "Title")
    text(raw["original"], 4000, "Original statement")
    integer(raw["timeout_seconds"], 1, 40)
    c = Compiler(raw["spaces"])
    scope, hypotheses, quantified, intros, variables = {}, {}, [], [], []
    for source in c.spaces.values():
        index = len(intros) // 3
        n, i = "mraNormed" + str(index), "mraInner" + str(index)
        quantified.extend(
            [
                f"({source} : Type)",
                f"[{n} : NormedAddCommGroup {source}]",
                f"[{i} : InnerProductSpace ℝ {source}]",
            ]
        )
        intros.extend([source, n, i])
    for item in array(raw["variables"], LIMITS["variables"], "Variables"):
        name, source, typ, extended = c.binding(item, scope, "variable")
        if name in scope:
            raise ValueError("Duplicate top-level proof variable")
        scope = extended
        quantified.append(f"({source} : {c.type_source(typ)})")
        intros.append(source)
        variables.append({"name": name, "lean": source, "type": deepcopy(item["type"])})
    conditions = []
    for item in array(raw["assumptions"], LIMITS["assumptions"], "Assumptions"):
        exact_object(item, {"name", "formula"}, "Assumption")
        name = symbol(item["name"])
        if name in hypotheses or name in scope:
            raise ValueError("Duplicate or ambiguous hypothesis name")
        source = "mraHyp" + str(len(hypotheses))
        formula = c.formula(item["formula"], scope)
        hypotheses[name] = source
        quantified.append(f"({source} : {formula})")
        intros.append(source)
        conditions.append({"name": name, "lean": source, "formal_statement": formula})
    goal = c.formula(raw["goal"], scope)
    statement = ("∀ " + " ".join(quantified) + ", " if quantified else "") + goal
    proof = raw["proof"]
    if not isinstance(proof, dict) or len(proof) != 1:
        raise ValueError("Choose a fixed tactic or structured proof term")
    prefix = "by\n" + ("  intro " + " ".join(intros) + "\n" if intros else "")
    if "tactic" in proof:
        tactics = {"rfl": "rfl", "simp": "simp_all", "grind": "all_goals (grind -verbose)"}
        if not isinstance(proof["tactic"], str) or proof["tactic"] not in tactics:
            raise ValueError("Only fixed tactics or a structured proof term are admitted")
        source = prefix + "  intros\n  " + tactics[proof["tactic"]]
    elif "term" in proof:
        body = c.proof(proof["term"], scope, hypotheses).strip()
        helpers = "".join(
            indent(declaration, "  ") + "\n" for _, declaration in c.minimum_helpers.values()
        )
        source = prefix + helpers + "  exact (\n" + indent(body, "    ") + "\n  )"
    else:
        raise ValueError("Unknown proof mode")
    return {
        "mode": MODE,
        "request": deepcopy(raw),
        "formal_statement": statement,
        "proof_source": source,
        "variables": variables,
        "conditions": conditions,
        "bindings": c.bindings,
        "proof_steps": c.proof_steps,
        "calculus_semantics": list(calculus.SEMANTICS),
        "space_instances": [
            {
                "name": k,
                "lean": v,
                "type": "Type 0",
                "instances": ["NormedAddCommGroup", "InnerProductSpace ℝ"],
            }
            for k, v in c.spaces.items()
        ],
    }


def catalog():
    result = prior.catalog()
    result.update(mode=MODE, limits=dict(LIMITS), rewrite_lemmas=list(REWRITES))
    result["formulas"].append("is_minimum")
    result["minimum_semantics"] = (
        "An attained value: existence of a real witness AND a lower bound for every real input. No default infimum."
    )
    result["proof_rules"].update(
        {
            k: {"term_types": list(v[0]), "terms": len(v[0]), "proofs": v[1]}
            for k, v in RULES.items()
        }
    )
    return result
