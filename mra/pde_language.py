"""Bounded classical PDE statements and explicit proof programs, without client Lean."""

from copy import deepcopy
import re

from . import calculus_language as calculus
from .analysis_language import REAL, LIMITS, array, symbol, text
from .formalizer import exact_object

MODE = "pde-language-v1"
SOURCE_LIMIT = 4
PDE_RULES = {
    "eq_refl": (["real"], 0),
    "mul_comm": (["real", "real"], 0),
    "mul_self_nonneg": (["real"], 0),
    "interval_neg": (["function", "measure", "real", "real"], 0),
    "interval_nonneg_forall": (["function", "measure", "real", "real"], 2),
}


class Compiler(calculus.Compiler):
    def __init__(self, spaces, sources):
        if spaces != []:
            raise ValueError(
                "This classical PDE language requires spaces=[]; functions are Real-to-Real"
            )
        super().__init__(spaces)
        self.sources = {}
        self.used_sources = set()
        for index, source in enumerate(array(sources, SOURCE_LIMIT, "Explicit sources")):
            exact_object(source, {"name", "run_id", "type_fingerprint"}, "PDE source identity")
            name = symbol(source["name"])
            if name in self.sources:
                raise ValueError("Duplicate source alias")
            if not isinstance(source["run_id"], str) or not re.fullmatch(
                r"(?:pdewatched|pdelang)-[a-f0-9]{32}", source["run_id"]
            ):
                raise ValueError("Select a complete versioned PDE source ID")
            if not isinstance(source["type_fingerprint"], str) or not re.fullmatch(
                r"[a-f0-9]{64}", source["type_fingerprint"]
            ):
                raise ValueError("An exact complete source type fingerprint is required")
            self.sources[name] = {"slot": index, **deepcopy(source)}

    def formula(self, raw, scope):
        if isinstance(raw, dict) and len(raw) == 1 and "in_uIcc" in raw:
            value = raw["in_uIcc"]
            exact_object(value, {"term", "lower", "upper"}, "Closed interval membership")
            x = self.require(value["term"], scope, REAL)
            a, b = self.endpoints(value, scope)
            return f"(({x}) ∈ Set.uIcc ({a}) ({b}))"
        return super().formula(raw, scope)

    def proof(self, raw, scope, hypotheses):
        if isinstance(raw, dict) and len(raw) == 1 and "source_apply" in raw:
            value = raw["source_apply"]
            exact_object(value, {"source", "terms", "proofs"}, "Exact source application")
            name = symbol(value["source"])
            if name not in self.sources:
                raise ValueError("Unknown explicit PDE source")
            source = self.sources[name]
            terms = [
                self.expr(term, scope)[0] for term in array(value["terms"], 12, "Source terms")
            ]
            proofs = [
                self.proof(proof, scope, hypotheses)
                for proof in array(value["proofs"], 16, "Source condition proofs")
            ]
            self.used_sources.add(name)
            head = "MraCandidate.Reused.s" + str(source["slot"]) + ".checked"
            return "(" + " ".join([head, *["(" + t + ")" for t in terms + proofs]]) + ")"
        if isinstance(raw, dict) and len(raw) == 1 and "rewrite" in raw:
            value = raw["rewrite"]
            exact_object(value, {"proof", "equalities"}, "Checked equality rewriting")
            equalities = []
            for item in array(value["equalities"], 8, "Equality rewrites"):
                exact_object(item, {"proof", "reverse"}, "Rewrite direction")
                if type(item["reverse"]) is not bool:
                    raise ValueError("Rewrite direction must be a boolean")
                term = self.proof(item["proof"], scope, hypotheses)
                equalities.append(("← " if item["reverse"] else "") + "(" + term + ")")
            fixed = [
                "zero_mul",
                "mul_zero",
                "zero_add",
                "add_zero",
                "sub_self",
                "sub_zero",
                "zero_sub",
                "neg_neg",
                "neg_mul",
                "mul_neg",
            ]
            term = self.proof(value["proof"], scope, hypotheses)
            return "(by simpa only [" + ", ".join(fixed + equalities) + "] using (" + term + "))"
        if isinstance(raw, dict) and len(raw) == 1 and "interval_congr" in raw:
            value = raw["interval_congr"]
            exact_object(
                value,
                {"name", "membership", "lower", "upper", "measure", "proof"},
                "Closed interval pointwise equality",
            )
            a, b = self.endpoints(value, scope)
            mu = self.require(value["measure"], scope, calculus.MEASURE)
            name, variable, _, nested = self.binding(
                {"name": value["name"], "type": "Real"}, scope, "integral-congruence"
            )
            member = symbol(value["membership"])
            if member == name or member in scope or member in hypotheses:
                raise ValueError("Ambiguous interval membership proof name")
            hypothesis = "mraProof" + str(self.serial)
            self.serial += 1
            inner = self.proof(value["proof"], nested, {**hypotheses, member: hypothesis})
            return (
                f"(intervalIntegral.integral_congr (μ := {mu}) (a := {a}) (b := {b}) "
                f"(fun ({variable} : ℝ) ({hypothesis} : {variable} ∈ Set.uIcc {a} {b}) => {inner}))"
            )
        if isinstance(raw, dict) and len(raw) == 1 and "rule" in raw:
            value = raw["rule"]
            exact_object(value, {"name", "terms", "proofs"}, "PDE rule")
            rule = value["name"]
            if isinstance(rule, str) and rule in PDE_RULES:
                kinds, count = PDE_RULES[rule]
                terms = array(value["terms"], 4, "PDE rule terms")
                proofs = array(value["proofs"], 2, "PDE rule proofs")
                if len(terms) != len(kinds) or len(proofs) != count:
                    raise ValueError("PDE rule argument counts differ")
                types = {
                    "real": REAL,
                    "function": calculus.REAL_FUNCTION,
                    "measure": calculus.MEASURE,
                }
                ts = [
                    "(" + self.require(t, scope, types[k]) + ")"
                    for t, k in zip(terms, kinds, strict=True)
                ]
                ps = ["(" + self.proof(p, scope, hypotheses) + ")" for p in proofs]
                if rule in ("eq_refl", "mul_comm", "mul_self_nonneg"):
                    head = {
                        "eq_refl": "Eq.refl",
                        "mul_comm": "mul_comm",
                        "mul_self_nonneg": "mul_self_nonneg",
                    }[rule]
                    return "(" + " ".join([head, *ts]) + ")"
                f, mu, a, b = ts
                if rule == "interval_neg":
                    return f"(intervalIntegral.integral_neg (f := {f}) (μ := {mu}) (a := {a}) (b := {b}))"
                return (
                    f"(intervalIntegral.integral_nonneg_of_forall (μ := {mu}) "
                    f"(f := {f}) (a := {a}) (b := {b}) {' '.join(ps)})"
                )
        return super().proof(raw, scope, hypotheses)


def compile_request(raw):
    calculus.bounded(raw)
    exact_object(
        raw,
        {
            "schema",
            "sources",
            "title",
            "original",
            "spaces",
            "variables",
            "assumptions",
            "goal",
            "proof",
            "timeout_seconds",
        },
        "Analysis request",
    )
    if raw["schema"] != MODE:
        raise ValueError("Select pde-language-v1 explicitly")
    calculus.text(raw["title"], 200, "Title")
    text(raw["original"], 4000, "Original statement")
    calculus.integer(raw["timeout_seconds"], 1, 40)
    c = Compiler(raw["spaces"], raw["sources"])
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
    for item in array(raw["variables"], calculus.LIMITS["variables"], "Variables"):
        name, source, typ, extended = c.binding(item, scope, "variable")
        if name in scope:
            raise ValueError("Duplicate top-level analysis variable")
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
            raise ValueError("Only rfl, simp or compact grind are fixed tactics")
        source = prefix + "  intros\n  " + tactics[proof["tactic"]]
    elif "term" in proof:
        source = prefix + "  exact " + c.proof(proof["term"], scope, hypotheses)
    else:
        raise ValueError("Unknown analysis proof mode")
    if c.used_sources != set(c.sources):
        raise ValueError("Every claimed source must occur in the proof program")
    return {
        "mode": MODE,
        "evidence_level": "E0",
        "math_verdict": "UNKNOWN",
        "source_authorization_checked": False,
        "sources": deepcopy(list(c.sources.values())),
        "request": deepcopy(raw),
        "formal_statement": statement,
        "proof_source": source,
        "variables": variables,
        "conditions": conditions,
        "bindings": c.bindings,
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
    result = deepcopy(calculus.catalog())
    result.update(
        mode=MODE,
        source_limit=SOURCE_LIMIT,
        spaces=0,
        source_providers=["pde-energy-watched-v2", MODE],
        source_authorization_checked=False,
        limits={**result["limits"], "spaces": 0},
    )
    result["types"] = ["Real", "MeasureReal", {"function": ["domain", "codomain"]}]
    result["formulas"].append("in_uIcc")
    result["proof_constructors"] = ["source_apply", "rewrite", "interval_congr"]
    result["proof_rules"].update(
        {
            k: {"term_types": list(v[0]), "terms": len(v[0]), "proofs": v[1]}
            for k, v in PDE_RULES.items()
        }
    )
    result["scope"] = (
        "Classical one-dimensional identities; no arbitrary Lean, weak solutions or existence claim"
    )
    return result
