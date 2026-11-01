"""Typed real calculus syntax with explicit measures, domains and lexical binders."""

from copy import deepcopy

from . import analysis_language as analysis
from .analysis_language import REAL, LIMITS, array, bounded, integer, symbol, text
from .formalizer import exact_object

MODE = "calculus-language-v1"
MEASURE = ("MeasureReal",)
REAL_FUNCTION = ("function", REAL, REAL)
CALCULUS_RULES = {
    "has_deriv_id": (["real"], 0),
    "has_deriv_const": (["real", "real"], 0),
    "has_deriv_add": ([], 2),
    "deriv_of_has_deriv": ([], 1),
    "interval_const": (["real", "real", "real"], 0),
    "interval_const_measure": (["measure", "real", "real", "real"], 0),
    "interval_add": ([], 2),
    "interval_reverse": (["function", "measure", "real", "real"], 0),
    "interval_zero_measure": (["function", "real", "real"], 0),
    "interval_ae_norm_bound": (["function", "real", "real", "real"], 1),
}
SEMANTICS = [
    "HasDerivAt is explicit evidence; deriv returns zero at a nondifferentiable point.",
    "Bochner integral is total and defaults to zero if not integrable or codomain incomplete.",
    "IntervalIntegrable is a separate proposition; no integrability is inferred from a value.",
    "Endpoints are signed; no lower<=upper hypothesis is added.",
    "volume, zero and arbitrary Measure Real remain distinct; measure.real uses toReal.",
    "Almost everywhere is relative to the explicit measure and is not pointwise.",
    "Only Real-to-Real differentiation and interval integration are calculus operations here.",
    "All original hypotheses, including unused ones, remain in the complete host statement.",
]


class Compiler(analysis.Compiler):
    def typ(self, raw, depth=0):
        if depth > LIMITS["type_depth"]:
            raise ValueError("Function type exceeds depth budget")
        return MEASURE if raw == "MeasureReal" else super().typ(raw, depth)

    def type_source(self, typ):
        return "Measure ℝ" if typ == MEASURE else super().type_source(typ)

    def require(self, raw, scope, expected):
        source, typ = self.expr(raw, scope)
        if typ != expected:
            raise ValueError("Calculus operand has the wrong explicit type")
        return source

    def endpoints(self, value, scope):
        return tuple(self.require(value[k], scope, REAL) for k in ("lower", "upper"))

    def expr(self, raw, scope):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().expr(raw, scope)
        op, value = next(iter(raw.items()))
        if op in ("volume", "measure_zero"):
            if value is not True:
                raise ValueError("Measure constants use the literal true marker")
            return ("(volume : Measure ℝ)" if op == "volume" else "(0 : Measure ℝ)"), MEASURE
        if op == "deriv":
            exact_object(value, {"function", "at"}, "Derivative value")
            f = self.require(value["function"], scope, REAL_FUNCTION)
            x = self.require(value["at"], scope, REAL)
            return f"(deriv ({f}) ({x}))", REAL
        if op == "measure_ioc_real":
            exact_object(value, {"measure", "lower", "upper"}, "Measure real value")
            mu = self.require(value["measure"], scope, MEASURE)
            a, b = self.endpoints(value, scope)
            return f"(({mu}).real (Set.Ioc ({a}) ({b})))", REAL
        if op == "interval_integral":
            exact_object(value, {"name", "lower", "upper", "measure", "body"}, "Interval integral")
            # Bounds and measure belong to the OUTER scope, never to this binder.
            a, b = self.endpoints(value, scope)
            mu = self.require(value["measure"], scope, MEASURE)
            _, source, _, nested = self.binding(
                {"name": value["name"], "type": "Real"}, scope, "interval-integral"
            )
            body = self.require(value["body"], nested, REAL)
            return f"(∫ ({source} : ℝ) in ({a})..({b}), {body} ∂({mu}))", REAL
        return super().expr(raw, scope)

    def formula(self, raw, scope):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().formula(raw, scope)
        op, value = next(iter(raw.items()))
        if op == "has_deriv_at":
            exact_object(value, {"function", "derivative", "at"}, "Derivative assertion")
            f = self.require(value["function"], scope, REAL_FUNCTION)
            df = self.require(value["derivative"], scope, REAL)
            x = self.require(value["at"], scope, REAL)
            return f"(HasDerivAt ({f}) ({df}) ({x}))"
        if op == "interval_integrable":
            exact_object(value, {"function", "measure", "lower", "upper"}, "Interval integrability")
            f = self.require(value["function"], scope, REAL_FUNCTION)
            mu = self.require(value["measure"], scope, MEASURE)
            a, b = self.endpoints(value, scope)
            return f"(IntervalIntegrable ({f}) ({mu}) ({a}) ({b}))"
        if op in ("in_Ioc", "in_uIoc"):
            exact_object(value, {"term", "lower", "upper"}, "Interval membership")
            x = self.require(value["term"], scope, REAL)
            a, b = self.endpoints(value, scope)
            constructor = "Set.Ioc" if op == "in_Ioc" else "Set.uIoc"
            return f"(({x}) ∈ {constructor} ({a}) ({b}))"
        if op == "ae":
            exact_object(value, {"name", "measure", "body"}, "Almost everywhere")
            mu = self.require(value["measure"], scope, MEASURE)
            _, source, _, nested = self.binding(
                {"name": value["name"], "type": "Real"}, scope, "almost-everywhere"
            )
            return f"(∀ᵐ ({source} : ℝ) ∂({mu}), {self.formula(value['body'], nested)})"
        return super().formula(raw, scope)

    def proof(self, raw, scope, hypotheses):
        if not isinstance(raw, dict) or len(raw) != 1 or "rule" not in raw:
            return super().proof(raw, scope, hypotheses)
        value = raw["rule"]
        exact_object(value, {"name", "terms", "proofs"}, "Library proof rule")
        name = value["name"]
        if not isinstance(name, str) or name not in CALCULUS_RULES:
            return super().proof(raw, scope, hypotheses)
        kinds, count = CALCULUS_RULES[name]
        terms, proofs = (
            array(value["terms"], 4, "Calculus terms"),
            array(value["proofs"], 2, "Calculus proofs"),
        )
        if len(terms) != len(kinds) or len(proofs) != count:
            raise ValueError("Calculus rule argument counts differ")
        types = {"real": REAL, "measure": MEASURE, "function": REAL_FUNCTION}
        ts = [
            "(" + self.require(t, scope, types[k]) + ")" for t, k in zip(terms, kinds, strict=True)
        ]
        ps = ["(" + self.proof(p, scope, hypotheses) + ")" for p in proofs]
        if name == "has_deriv_id":
            return f"(hasDerivAt_id' {ts[0]})"
        if name == "has_deriv_const":
            return f"(hasDerivAt_const {ts[0]} {ts[1]})"
        if name == "has_deriv_add":
            return f"({ps[0]}.add {ps[1]})"
        if name == "deriv_of_has_deriv":
            return f"({ps[0]}.deriv)"
        if name == "interval_add":
            return f"(intervalIntegral.integral_add {ps[0]} {ps[1]})"
        if name == "interval_const":
            a, b, c = ts
            return f"(by simpa only [smul_eq_mul] using (intervalIntegral.integral_const (a := {a}) (b := {b}) {c}))"
        if name == "interval_const_measure":
            mu, a, b, c = ts
            return f"(by simpa only [smul_eq_mul] using (intervalIntegral.integral_const' (μ := {mu}) (a := {a}) (b := {b}) {c}))"
        if name == "interval_reverse":
            f, mu, a, b = ts
            return f"(intervalIntegral.integral_symm (f := {f}) (μ := {mu}) {a} {b})"
        if name == "interval_zero_measure":
            f, a, b = ts
            return f"(show (∫ t in {a}..{b}, {f} t ∂(0 : Measure ℝ)) = 0 from by simp [intervalIntegral])"
        f, a, b, bound = ts
        return f"(intervalIntegral.norm_integral_le_of_norm_le_const_ae (f := {f}) (a := {a}) (b := {b}) (C := {bound}) {ps[0]})"


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
        "Analysis request",
    )
    if raw["schema"] != MODE:
        raise ValueError("Select calculus-language-v1 explicitly")
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
    return {
        "mode": MODE,
        "request": deepcopy(raw),
        "formal_statement": statement,
        "proof_source": source,
        "variables": variables,
        "conditions": conditions,
        "bindings": c.bindings,
        "calculus_semantics": list(SEMANTICS),
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
    result = deepcopy(analysis.catalog())
    result.update(mode=MODE, worker_seconds=[1, 40], calculus_semantics=list(SEMANTICS))
    result["types"].append("MeasureReal")
    result["terms"] += ["volume", "measure_zero", "deriv", "measure_ioc_real", "interval_integral"]
    result["formulas"] += ["has_deriv_at", "interval_integrable", "in_Ioc", "in_uIoc", "ae"]
    result["proof_rules"].update(
        {
            k: {"term_types": list(v[0]), "terms": len(v[0]), "proofs": v[1]}
            for k, v in CALCULUS_RULES.items()
        }
    )
    return result
