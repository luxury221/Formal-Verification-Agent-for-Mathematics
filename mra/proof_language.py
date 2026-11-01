"""Versioned local proof composition over the unchanged typed calculus syntax."""

from copy import deepcopy

from . import calculus_language as calculus
from .analysis_language import REAL, LIMITS, array, bounded, integer, symbol, text
from .formalizer import exact_object

MODE = "proof-language-v1"
RULES = {
    "real_inner_mul_inner_self_le": (["vector", "vector"], 0),
    "sub_nonneg_of_le": ([], 1),
    "le_of_lt": ([], 1),
    "eq_of_le_of_ge": ([], 2),
    "mul_le_mul_of_nonneg_left": (["real"], 2),
    "mul_le_mul_of_nonneg_right": (["real"], 2),
    "and_left": ([], 1),
    "and_right": ([], 1),
}
REWRITES = (
    "real_inner_self_eq_norm_sq",
    "Real.norm_eq_abs",
    "sq_abs",
    "pow_two",
    "mul_pow",
    "smul_eq_mul",
    "zero_mul",
    "mul_zero",
    "zero_add",
    "add_zero",
    "sub_self",
    "sub_zero",
)


class Compiler(calculus.Compiler):
    def __init__(self, spaces):
        super().__init__(spaces)
        self.proof_steps = []

    def fresh_proof(self):
        name = "mraProof" + str(self.serial)
        self.serial += 1
        return name

    def proof(self, raw, scope, hypotheses):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().proof(raw, scope, hypotheses)
        op, value = next(iter(raw.items()))
        if op == "have":
            exact_object(value, {"name", "formula", "proof", "then"}, "Proved local lemma")
            name = symbol(value["name"])
            if name in scope or name in hypotheses:
                raise ValueError("A local lemma cannot shadow a variable or hypothesis")
            statement = self.formula(value["formula"], scope)
            # Compile the value before extending scope: no circular/self evidence.
            evidence = self.proof(value["proof"], scope, hypotheses)
            source = self.fresh_proof()
            self.proof_steps.append(
                {"kind": "have", "name": name, "lean": source, "formal_statement": statement}
            )
            body = self.proof(value["then"], scope, {**hypotheses, name: source})
            return f"(by have {source} : {statement} := {evidence}; exact {body})"
        if op == "rewrite":
            exact_object(value, {"proof", "equalities"}, "Explicit checked rewriting")
            entries = array(value["equalities"], 8, "Rewrite list")
            if not entries:
                raise ValueError("Choose at least one explicit rewrite")
            rewrites = []
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("Each rewrite needs a theorem or an equality proof")
                if "lemma" in item:
                    exact_object(item, {"lemma", "reverse"}, "Fixed rewrite theorem")
                    if not isinstance(item["lemma"], str) or item["lemma"] not in REWRITES:
                        raise ValueError("Unknown approved rewrite theorem")
                    term = item["lemma"]
                else:
                    exact_object(item, {"proof", "reverse"}, "Proved equality rewrite")
                    term = "(" + self.proof(item["proof"], scope, hypotheses) + ")"
                if type(item["reverse"]) is not bool:
                    raise ValueError("Rewrite direction must be a boolean")
                rewrites.append(("← " if item["reverse"] else "") + term)
            evidence = self.proof(value["proof"], scope, hypotheses)
            self.proof_steps.append({"kind": "rewrite", "equalities": deepcopy(entries)})
            return "(by simpa only [" + ", ".join(rewrites) + "] using (" + evidence + "))"
        if op == "arithmetic":
            exact_object(value, {"method", "proofs"}, "Fixed arithmetic discharge")
            method = value["method"]
            if not isinstance(method, str) or method not in ("linarith", "nlinarith", "ring"):
                raise ValueError("Choose a fixed arithmetic method")
            proofs = array(value["proofs"], 8, "Explicit arithmetic facts")
            if method == "ring" and proofs:
                raise ValueError("Ring normalization takes no extra facts")
            evidence = [self.proof(p, scope, hypotheses) for p in proofs]
            self.proof_steps.append({"kind": "arithmetic", "method": method, "facts": len(proofs)})
            if method == "ring":
                return "(by ring)"
            return "(by " + method + " only [" + ", ".join(evidence) + "])"
        if (
            op == "rule"
            and isinstance(value, dict)
            and isinstance(value.get("name"), str)
            and value["name"] in RULES
        ):
            exact_object(value, {"name", "terms", "proofs"}, "Approved proof-composition rule")
            name = value["name"]
            kinds, count = RULES[name]
            terms = array(value["terms"], 2, "Rule terms")
            proofs = array(value["proofs"], 2, "Rule proofs")
            if (len(terms), len(proofs)) != (len(kinds), count):
                raise ValueError("Proof-composition rule argument counts differ")
            typed = [self.expr(t, scope) for t in terms]
            for (_, typ), kind in zip(typed, kinds, strict=True):
                if kind == "real" and typ != REAL:
                    raise ValueError("The rule requires an explicit real scalar")
                if kind == "vector":
                    self.vector(typ)
            if name == "real_inner_mul_inner_self_le" and typed[0][1] != typed[1][1]:
                raise ValueError("The inner-product rule requires the same vector space")
            ts = ["(" + t[0] + ")" for t in typed]
            ps = ["(" + self.proof(p, scope, hypotheses) + ")" for p in proofs]
            self.proof_steps.append({"kind": "rule", "name": name})
            if name in ("mul_le_mul_of_nonneg_left", "mul_le_mul_of_nonneg_right"):
                return "(" + name + " (c := " + ts[0] + ") " + " ".join(ps) + ")"
            head = {
                "sub_nonneg_of_le": "sub_nonneg.mpr",
                "and_left": "And.left",
                "and_right": "And.right",
            }.get(name, name)
            return "(" + " ".join([head, *ts, *ps]) + ")"
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
        raise ValueError("Select proof-language-v1 explicitly")
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
        source = prefix + "  exact " + c.proof(proof["term"], scope, hypotheses)
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
    result = deepcopy(calculus.catalog())
    result.update(
        mode=MODE,
        local_lemmas="Proved before use, lexical scope only; no extra assumptions",
        rewrite_lemmas=list(REWRITES),
        arithmetic=["linarith", "nlinarith", "ring"],
    )
    result["proof_constructors"] = ["have", "rewrite", "arithmetic"]
    result["proof_rules"].update(
        {
            k: {"term_types": list(v[0]), "terms": len(v[0]), "proofs": v[1]}
            for k, v in RULES.items()
        }
    )
    return result
