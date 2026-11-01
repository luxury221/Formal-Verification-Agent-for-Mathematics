"""Versioned finite vectors, explicit finite lp expressions and real powers."""

from copy import deepcopy
from textwrap import indent

from . import optimization_language as prior
from . import calculus_language as calculus
from .analysis_language import REAL, array, integer, pair, symbol, text
from .formalizer import exact_object

MODE = "proof-language-v4"
LIMITS = {**prior.LIMITS, "finite_dimension": 8, "rational_power": 8}
bounded = prior.bounded
RULES = {
    "lp_norm_rpow": (2, 1),
    "lp_norm_nonneg": (2, 0),
    "lp_sum_nonneg": (2, 0),
    "real_rpow_mul": (3, 1),
    "real_rpow_nat": (2, 0),
}
IMPLICIT_VECTOR_RULES = {
    "inner_self_nonneg",
    "cauchy_schwarz",
    "norm_nonneg",
    "norm_add_le",
    "norm_smul",
    "inner_self_norm_sq",
    "real_inner_mul_inner_self_le",
    "norm_cs_equality",
    "least_squares_minimum",
    "least_squares_derivative",
    "least_squares_stationary",
}


class Compiler(prior.Compiler):
    def typ(self, raw, depth=0):
        if isinstance(raw, dict) and set(raw) == {"finite_vector"}:
            if depth > LIMITS["type_depth"]:
                raise ValueError("Finite vector type exceeds depth budget")
            dimension = integer(raw["finite_vector"], 1, LIMITS["finite_dimension"])
            return ("finite_vector", dimension)
        return super().typ(raw, depth)

    def type_source(self, typ):
        if typ[0] == "finite_vector":
            return f"(Fin {typ[1]} → ℝ)"
        return super().type_source(typ)

    @staticmethod
    def vector(typ):
        if typ[0] != "finite_vector":
            prior.Compiler.vector(typ)

    @staticmethod
    def finite(typ):
        if typ[0] != "finite_vector":
            raise ValueError("An explicit finite real vector is required")
        return typ[1]

    def lp_parts(self, value, scope):
        exact_object(value, {"exponent", "vector"}, "Finite lp expression")
        p = self.require(value["exponent"], scope, REAL)
        u, typ = self.expr(value["vector"], scope)
        n = self.finite(typ)
        total = f"(Finset.univ.sum (fun (mraLpIndex : Fin {n}) => |({u}) mraLpIndex| ^ ({p})))"
        return p, u, typ, total

    def expr(self, raw, scope):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().expr(raw, scope)
        op, value = next(iter(raw.items()))
        if op == "finite_vector":
            entries = array(value, LIMITS["finite_dimension"], "Finite vector coordinates")
            if not entries:
                raise ValueError("Finite vector literals require at least one coordinate")
            terms = [self.require(term, scope, REAL) for term in entries]
            body = terms[-1]
            for index in reversed(range(len(terms) - 1)):
                body = f"(if mraLpIndex.val = {index} then {terms[index]} else {body})"
            return f"(fun (mraLpIndex : Fin {len(terms)}) => {body})", ("finite_vector", len(terms))
        if op == "coordinate":
            exact_object(value, {"vector", "index"}, "Finite vector coordinate")
            source, typ = self.expr(value["vector"], scope)
            index = integer(value["index"], 0, self.finite(typ) - 1)
            return f"(({source}) ⟨{index}, by decide⟩)", REAL
        if op in ("lp_norm", "lp_sum"):
            p, _, _, total = self.lp_parts(value, scope)
            return (f"(({total}) ^ ((1 : ℝ) / ({p})))" if op == "lp_norm" else total), REAL
        if op == "real_rpow":
            x, y = pair(value)
            return f"(({self.require(x, scope, REAL)}) ^ ({self.require(y, scope, REAL)}))", REAL
        if op == "dot":
            a, b = pair(value)
            u, ut = self.expr(a, scope)
            v, vt = self.expr(b, scope)
            n = self.finite(ut)
            if ut != vt:
                raise ValueError("Finite dot product dimensions differ")
            return (
                f"(Finset.univ.sum (fun (mraLpIndex : Fin {n}) => ({u}) mraLpIndex * ({v}) mraLpIndex))",
                REAL,
            )
        if op in ("norm", "inner"):
            terms = [value] if op == "norm" else pair(value)
            if any(self.expr(term, scope)[1][0] == "finite_vector" for term in terms):
                raise ValueError(
                    "Finite vectors require explicit lp_norm or dot; no implicit Pi norm"
                )
        return super().expr(raw, scope)

    def proof(self, raw, scope, hypotheses):
        if not isinstance(raw, dict) or len(raw) != 1:
            return super().proof(raw, scope, hypotheses)
        op, value = next(iter(raw.items()))
        if op == "rule":
            exact_object(value, {"name", "terms", "proofs"}, "Finite lp or inherited rule")
            if not isinstance(value["name"], str):
                raise ValueError("Proof rule names must be strings")
        if (
            op == "rule"
            and isinstance(value, dict)
            and isinstance(value.get("name"), str)
            and value["name"] in IMPLICIT_VECTOR_RULES
        ):
            exact_object(value, {"name", "terms", "proofs"}, "Inherited inner-product rule")
            terms = array(value["terms"], 3, "Inherited inner-product rule terms")
            if any(self.expr(term, scope)[1][0] == "finite_vector" for term in terms):
                raise ValueError(
                    "Finite vectors require explicit lp rules; no inherited Pi norm or inner product"
                )
        if op in ("finite_compute", "finite_simplify"):
            exact_object(
                value,
                {"equalities"} if op == "finite_compute" else {"equalities", "proof"},
                "Fixed finite simplification",
            )
            terms = self.rewrite_terms(value["equalities"], scope, hypotheses)
            fixed = ["Fin.sum_univ_succ", "Real.rpow_natCast", "Pi.smul_apply", "smul_eq_mul"]
            simp = ", ".join([*fixed, *terms])
            self.proof_steps.append({"kind": op, "equalities": deepcopy(value["equalities"])})
            if op == "finite_compute":
                return "(by norm_num [" + simp + "])"
            evidence = self.proof(value["proof"], scope, hypotheses)
            return "(by simpa [" + simp + "] using (" + evidence + "))"
        if op == "coordinate_eq":
            exact_object(value, {"left", "right", "index", "proof"}, "Coordinate congruence")
            left, ut = self.expr(value["left"], scope)
            right, vt = self.expr(value["right"], scope)
            if ut != vt:
                raise ValueError("Coordinate equality requires the same finite vector type")
            index = integer(value["index"], 0, self.finite(ut) - 1)
            evidence = self.proof(value["proof"], scope, hypotheses)
            self.proof_steps.append({"kind": op, "index": index})
            return f"(congrArg (fun (mraLpVector : {self.type_source(ut)}) => mraLpVector ⟨{index}, by decide⟩) (show ({left}) = ({right}) from ({evidence})))"
        if op == "rational_power":
            exact_object(
                value,
                {"base", "root", "numerator", "denominator", "nonnegative", "root_power"},
                "Certified rational power",
            )
            x = self.require(value["base"], scope, REAL)
            y = self.require(value["root"], scope, REAL)
            m = integer(value["numerator"], 0, LIMITS["rational_power"])
            n = integer(value["denominator"], 1, LIMITS["rational_power"])
            nonnegative = self.proof(value["nonnegative"], scope, hypotheses)
            equation = self.proof(value["root_power"], scope, hypotheses)
            self.proof_steps.append({"kind": op, "numerator": m, "denominator": n})
            return f"""(show ({x}) ^ (({m} : ℝ) / ({n} : ℝ)) = ({y}) ^ {m} from by
  have mraLpRootNonneg : (0 : ℝ) ≤ ({y}) := {nonnegative}
  have mraLpRootPower : ({y}) ^ {n} = ({x}) := {equation}
  rw [← mraLpRootPower, ← Real.rpow_natCast_mul mraLpRootNonneg]
  norm_num [Real.rpow_natCast])"""
        if (
            op == "rule"
            and isinstance(value, dict)
            and isinstance(value.get("name"), str)
            and value["name"] in RULES
        ):
            exact_object(value, {"name", "terms", "proofs"}, "Finite lp rule")
            name = value["name"]
            terms = array(value["terms"], 3, "Finite lp rule terms")
            proofs = array(value["proofs"], 1, "Finite lp rule proofs")
            if (len(terms), len(proofs)) != RULES[name]:
                raise ValueError("Finite lp rule argument counts differ")
            typed = [self.expr(term, scope) for term in terms]
            ps = [self.proof(proof, scope, hypotheses) for proof in proofs]
            ts = ["(" + term + ")" for term, _ in typed]
            self.proof_steps.append({"kind": "rule", "name": name})
            if name.startswith("lp_"):
                if typed[0][1] != REAL:
                    raise ValueError("The lp exponent must be Real")
                self.finite(typed[1][1])
                p, _, _, total = self.lp_parts({"exponent": terms[0], "vector": terms[1]}, scope)
                nonneg = f"(Finset.sum_nonneg (fun mraLpIndex _ => Real.rpow_nonneg (abs_nonneg _) ({p})))"
                if name == "lp_sum_nonneg":
                    return nonneg
                if name == "lp_norm_nonneg":
                    return f"(Real.rpow_nonneg {nonneg} ((1 : ℝ) / ({p})))"
                return f"""(show (({total}) ^ ((1 : ℝ) / ({p}))) ^ ({p}) = ({total}) from by
  have mraLpPositive : (0 : ℝ) < ({p}) := {ps[0]}
  rw [one_div]
  exact Real.rpow_inv_rpow {nonneg} (ne_of_gt mraLpPositive))"""
            if any(typ != REAL for _, typ in typed):
                raise ValueError("Real power rules require scalar arguments")
            if name == "real_rpow_mul":
                return f"((Real.rpow_mul (show (0 : ℝ) ≤ {ts[0]} from ({ps[0]})) {ts[1]} {ts[2]}).symm)"
            literal = terms[1]
            if not isinstance(literal, dict) or set(literal) != {"real"}:
                raise ValueError("Natural real power needs an explicit integer literal")
            numerator, denominator = pair(literal["real"])
            integer(numerator, 0, LIMITS["power"])
            if denominator != 1:
                raise ValueError("Natural real power literal needs denominator one")
            return f"(Real.rpow_natCast {ts[0]} {numerator})"
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
        raise ValueError("Select proof-language-v4 explicitly")
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
    result.update(mode=MODE, limits=dict(LIMITS))
    result["types"].append({"finite_vector": "dimension 1..8"})
    result["terms"].extend(["finite_vector", "coordinate", "dot", "lp_sum", "lp_norm", "real_rpow"])
    result["finite_vector_semantics"] = {
        "type": "Fin n → Real; 1 <= n <= 8; pointwise linear operations",
        "norm": "lp_norm(p,u)=(sum_i |u_i|^p)^(1/p); the power identity requires p>0; a genuine lp norm requires p>=1",
        "dot": "sum_i u_i*v_i; no default Pi supremum norm or inner-product instance",
        "real_rpow": "Mathlib Real.rpow is total; no positivity premise is inserted",
        "inherited_rules": "Implicit norm and inner-product rules exclude finite vectors; pointwise linear operations remain available",
    }
    result["proof_rules"].update(
        {name: {"terms": sizes[0], "proofs": sizes[1]} for name, sizes in RULES.items()}
    )
    result["proof_constructors"] = [
        *result.get("proof_constructors", []),
        "finite_compute",
        "finite_simplify",
        "coordinate_eq",
        "rational_power",
    ]
    return result
