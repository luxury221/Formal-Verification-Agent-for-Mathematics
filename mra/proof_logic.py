"""Version two: bounded logical witnesses, cases and equivalences over real analysis."""

from copy import deepcopy

from . import proof_language as prior
from . import calculus_language as calculus
from .analysis_language import REAL, LIMITS as OLD_LIMITS, array, integer, pair, symbol, text
from .domain import canonical
from .formalizer import exact_object

MODE = "proof-language-v2"
LIMITS = {**OLD_LIMITS, "depth": 64, "nodes": 2400}
REWRITES = (
    *prior.REWRITES,
    "one_smul",
    "zero_smul",
    "smul_zero",
    "smul_smul",
    "div_eq_mul_inv",
    "mul_comm",
    "norm_zero",
    "inner_zero_left",
    "inner_zero_right",
    "zero_pow",
    "one_ne_zero",
    "sub_eq_zero",
    "eq_comm",
)
RULES = {
    "iff_intro": ([], 2),
    "iff_mp": ([], 2),
    "iff_mpr": ([], 2),
    "iff_trans": ([], 2),
    "iff_symm": ([], 1),
    "or_left": ([], 1),
    "or_right": ([], 1),
    "false_elim": ([], 1),
    "not_elim": ([], 2),
    "eq_refl": (["any"], 0),
    "one_ne_zero": ([], 0),
    "norm_cs_equality": (["vector", "vector"], 0),
    "sq_eq_sq_nonneg": ([], 2),
    "smul_eq_zero_iff": (["real", "vector"], 1),
    "inv_smul_eq_iff": (["real", "vector", "vector"], 1),
}


def bounded(raw):
    pending, count = [(raw, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > LIMITS["nodes"] or depth > LIMITS["depth"]:
            raise ValueError("Logical proof exceeds node/depth budget")
        if isinstance(value, dict):
            if not all(isinstance(k, str) for k in value):
                raise ValueError("Logical JSON keys must be strings")
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
        elif value is not None and type(value) not in (str, int, bool):
            raise ValueError("Logical proof requires ordinary JSON without floats")
    if len(canonical(raw)) > LIMITS["bytes"]:
        raise ValueError("Logical proof exceeds byte budget")


class Compiler(prior.Compiler):
    @staticmethod
    def new_name(raw, scope, hypotheses):
        name = symbol(raw)
        if name in scope or name in hypotheses:
            raise ValueError("A logical binder cannot shadow a variable or hypothesis")
        return name

    def formula(self, raw, scope):
        if isinstance(raw, dict) and len(raw) == 1 and "iff" in raw:
            left, right = pair(raw["iff"])
            return "(" + self.formula(left, scope) + " ↔ " + self.formula(right, scope) + ")"
        return super().formula(raw, scope)

    def rewrite_terms(self, entries, scope, hypotheses):
        entries = array(entries, 8, "Explicit rewrites")
        terms = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("A rewrite requires an explicit theorem or proof")
            key = "lemma" if "lemma" in item else "proof"
            exact_object(item, {key, "reverse"}, "Explicit rewrite")
            if type(item["reverse"]) is not bool:
                raise ValueError("Rewrite direction must be a boolean")
            if key == "lemma":
                if not isinstance(item[key], str) or item[key] not in REWRITES:
                    raise ValueError("Unknown fixed logical rewrite")
                term = item[key]
            else:
                term = "(" + self.proof(item[key], scope, hypotheses) + ")"
            terms.append(("← " if item["reverse"] else "") + term)
        return terms

    def proof(self, raw, scope, hypotheses):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().proof(raw, scope, hypotheses)
        op, value = next(iter(raw.items()))
        if op == "steps":
            exact_object(value, {"lemmas", "then"}, "Sequential proved lemmas")
            local, declarations = dict(hypotheses), []
            for lemma in array(value["lemmas"], 8, "Sequential lemmas"):
                exact_object(lemma, {"name", "formula", "proof"}, "Sequential lemma")
                name = self.new_name(lemma["name"], scope, local)
                statement = self.formula(lemma["formula"], scope)
                evidence = self.proof(lemma["proof"], scope, local)
                source = self.fresh_proof()
                declarations.append(f"have {source} : {statement} := {evidence}")
                local[name] = source
                self.proof_steps.append(
                    {"kind": "have", "name": name, "formal_statement": statement}
                )
            body = self.proof(value["then"], scope, local)
            return "(by " + "; ".join([*declarations, "exact " + body]) + ")"
        if op == "exists_intro":
            exact_object(value, {"witness", "proof"}, "Existential witness")
            witness = self.expr(value["witness"], scope)[0]
            evidence = self.proof(value["proof"], scope, hypotheses)
            self.proof_steps.append({"kind": "exists_intro"})
            return f"(Exists.intro ({witness}) ({evidence}))"
        if op == "exists_elim":
            exact_object(
                value,
                {"proof", "name", "type", "hypothesis", "formula", "then"},
                "Existential elimination",
            )
            name = self.new_name(value["name"], scope, hypotheses)
            evidence = self.proof(value["proof"], scope, hypotheses)
            _, source, typ, nested = self.binding(
                {"name": name, "type": value["type"]}, scope, "exists-elim"
            )
            hyp = self.new_name(value["hypothesis"], nested, hypotheses)
            statement = self.formula(value["formula"], nested)
            proof_name = self.fresh_proof()
            body = self.proof(value["then"], nested, {**hypotheses, hyp: proof_name})
            self.proof_steps.append(
                {"kind": "exists_elim", "name": name, "formal_statement": statement}
            )
            return f"(Exists.elim ({evidence}) (fun ({source} : {self.type_source(typ)}) ({proof_name} : {statement}) => {body}))"
        if op == "or_cases":
            exact_object(value, {"proof", "left", "right"}, "Disjunction elimination")
            evidence = self.proof(value["proof"], scope, hypotheses)
            branches = []
            for side in ("left", "right"):
                branch = value[side]
                exact_object(branch, {"name", "proof"}, "Disjunction branch")
                name = self.new_name(branch["name"], scope, hypotheses)
                source = self.fresh_proof()
                body = self.proof(branch["proof"], scope, {**hypotheses, name: source})
                branches.append(f"(fun {source} => {body})")
            self.proof_steps.append({"kind": "or_cases"})
            return "(Or.elim (" + evidence + ") " + " ".join(branches) + ")"
        if op == "by_cases":
            exact_object(value, {"name", "formula", "yes", "no"}, "Classical case split")
            name = self.new_name(value["name"], scope, hypotheses)
            source = self.fresh_proof()
            statement = self.formula(value["formula"], scope)
            yes = self.proof(value["yes"], scope, {**hypotheses, name: source})
            no = self.proof(value["no"], scope, {**hypotheses, name: source})
            self.proof_steps.append(
                {"kind": "by_cases", "name": name, "formal_statement": statement}
            )
            return (
                f"(@dite _ ({statement}) (Classical.propDecidable _) "
                f"(fun ({source} : {statement}) => {yes}) "
                f"(fun ({source} : ¬ {statement}) => {no}))"
            )
        if op in ("rewrite", "normalize"):
            exact_object(
                value,
                {"equalities", "proof"} if op == "rewrite" else {"equalities"},
                "Explicit logical simplification",
            )
            entries = value["equalities"]
            terms = self.rewrite_terms(entries, scope, hypotheses)
            self.proof_steps.append({"kind": op, "equalities": deepcopy(entries)})
            if op == "rewrite":
                if not terms:
                    raise ValueError("Choose at least one explicit rewrite")
                evidence = self.proof(value["proof"], scope, hypotheses)
                return "(by simpa only [" + ", ".join(terms) + "] using (" + evidence + "))"
            return "(by simp only [" + ", ".join(terms) + "])"
        if (
            op == "rule"
            and isinstance(value, dict)
            and isinstance(value.get("name"), str)
            and value["name"] in RULES
        ):
            exact_object(value, {"name", "terms", "proofs"}, "Logical proof rule")
            name = value["name"]
            kinds, count = RULES[name]
            terms = array(value["terms"], 3, "Logical rule terms")
            proofs = array(value["proofs"], 2, "Logical rule proofs")
            if (len(terms), len(proofs)) != (len(kinds), count):
                raise ValueError("Logical rule argument counts differ")
            typed = [self.expr(term, scope) for term in terms]
            for (_, typ), kind in zip(typed, kinds, strict=True):
                if kind == "real" and typ != REAL:
                    raise ValueError("Logical rule requires an explicit real scalar")
                if kind == "vector":
                    self.vector(typ)
            vectors = [typ for (_, typ), kind in zip(typed, kinds, strict=True) if kind == "vector"]
            if vectors and any(t != vectors[0] for t in vectors):
                raise ValueError("Logical rule vectors belong to different spaces")
            ts = ["(" + term + ")" for term, _ in typed]
            ps = ["(" + self.proof(p, scope, hypotheses) + ")" for p in proofs]
            self.proof_steps.append({"kind": "rule", "name": name})
            if name == "norm_cs_equality":
                return (
                    "(by simpa only [Real.norm_eq_abs] using ((norm_inner_eq_norm_tfae ℝ "
                    + " ".join(ts)
                    + ").out 1 3))"
                )
            if name == "smul_eq_zero_iff":
                return (
                    "(smul_eq_zero_iff_right (r := "
                    + ts[0]
                    + ") (m := "
                    + ts[1]
                    + ") "
                    + ps[0]
                    + ")"
                )
            if name == "inv_smul_eq_iff":
                return (
                    "(inv_smul_eq_iff₀ (a := "
                    + ts[0]
                    + ") (x := "
                    + ts[1]
                    + ") (y := "
                    + ts[2]
                    + ") "
                    + ps[0]
                    + ")"
                )
            if name == "one_ne_zero":
                return "(show (1 : ℝ) ≠ 0 from one_ne_zero)"
            if name == "not_elim":
                return "(" + ps[0] + " " + ps[1] + ")"
            head = {
                "iff_intro": "Iff.intro",
                "iff_mp": "Iff.mp",
                "iff_mpr": "Iff.mpr",
                "iff_trans": "Iff.trans",
                "iff_symm": "Iff.symm",
                "or_left": "Or.inl",
                "or_right": "Or.inr",
                "false_elim": "False.elim",
                "eq_refl": "Eq.refl",
                "sq_eq_sq_nonneg": "sq_eq_sq₀",
            }[name]
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
        raise ValueError("Select proof-language-v2 explicitly")
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
    result = prior.catalog()
    result.update(mode=MODE, limits=dict(LIMITS), rewrite_lemmas=list(REWRITES))
    result["formulas"] = [*result["formulas"], "iff"]
    result["proof_constructors"] += [
        "steps",
        "exists_intro",
        "exists_elim",
        "or_cases",
        "by_cases",
        "normalize",
    ]
    result["proof_rules"].update(
        {
            k: {"term_types": list(v[0]), "terms": len(v[0]), "proofs": v[1]}
            for k, v in RULES.items()
        }
    )
    return result
