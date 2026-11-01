"""Bounded typed real, inner-product and function syntax; no client Lean fragments."""

from copy import deepcopy
import re
import math

from .domain import canonical
from .formalizer import exact_object

MODE = "analysis-language-v1"
LIMITS = {
    "bytes": 131072,
    "nodes": 1200,
    "depth": 32,
    "spaces": 3,
    "variables": 20,
    "assumptions": 16,
    "type_depth": 4,
    "integer": 1000000000,
    "power": 8,
}
REAL = ("Real",)
RULES = {
    "inner_self_nonneg": (1, 0),
    "cauchy_schwarz": (2, 0),
    "norm_nonneg": (1, 0),
    "norm_add_le": (2, 0),
    "norm_smul": (2, 0),
    "inner_self_norm_sq": (1, 0),
    "sq_nonneg": (1, 0),
    "abs_nonneg": (1, 0),
    "le_refl": (1, 0),
    "le_trans": (0, 2),
    "add_le_add": (0, 2),
    "mul_nonneg": (0, 2),
    "and_intro": (0, 2),
    "eq_trans": (0, 2),
    "eq_symm": (0, 1),
    "div_self": (0, 1),
    "congr_arg": (1, 1),
}


def bounded(raw):
    pending = [(raw, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > LIMITS["nodes"] or depth > LIMITS["depth"]:
            raise ValueError("Analysis syntax exceeds node/depth budget")
        if isinstance(value, dict):
            if not all(isinstance(k, str) for k in value):
                raise ValueError("Analysis JSON keys must be strings")
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
        elif value is not None and type(value) not in (str, int, bool):
            raise ValueError("Analysis syntax requires ordinary JSON, without floats")
    if len(canonical(raw)) > LIMITS["bytes"]:
        raise ValueError("Analysis request exceeds byte budget")


def text(value, maximum, name):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(name + " must be nonempty bounded text")
    return value


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value):
        raise ValueError("Use a 1-32 character ASCII symbol")
    return value


def array(value, maximum, name):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(name + " exceeds array budget")
    return value


def pair(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Expected exactly two operands")
    return value


def integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Integer outside approved bounds")
    return value


class Compiler:
    def __init__(self, spaces):
        self.spaces = {}
        self.bindings = []
        self.serial = 0
        for item in array(spaces, LIMITS["spaces"], "Spaces"):
            exact_object(item, {"name", "structure"}, "Space")
            name = symbol(item["name"])
            if name in self.spaces or item["structure"] != "real-inner-product":
                raise ValueError("Declare each real inner-product space once")
            self.spaces[name] = "mraSpace" + str(len(self.spaces))

    def typ(self, raw, depth=0):
        if depth > LIMITS["type_depth"]:
            raise ValueError("Function type exceeds depth budget")
        if raw == "Real":
            return REAL
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("Type must be Real, a declared space, or a function")
        key, value = next(iter(raw.items()))
        if key == "space" and isinstance(value, str) and value in self.spaces:
            return ("space", value)
        if key == "function":
            domain, codomain = pair(value)
            return ("function", self.typ(domain, depth + 1), self.typ(codomain, depth + 1))
        raise ValueError("Unknown analysis type; no implicit casts or space instances")

    def type_source(self, typ):
        if typ == REAL:
            return "ℝ"
        if typ[0] == "space":
            return self.spaces[typ[1]]
        return "(" + self.type_source(typ[1]) + " → " + self.type_source(typ[2]) + ")"

    def binding(self, item, scope, purpose):
        exact_object(item, {"name", "type"}, "Typed binding")
        name, typ = symbol(item["name"]), self.typ(item["type"])
        source = "mraBound" + str(self.serial)
        self.serial += 1
        extended = dict(scope)
        extended[name] = (source, typ)
        self.bindings.append(
            {"name": name, "lean": source, "type": deepcopy(item["type"]), "scope": purpose}
        )
        return name, source, typ, extended

    @staticmethod
    def vector(typ):
        if typ != REAL and typ[0] != "space":
            raise ValueError("This operation requires a real scalar or inner-product vector")

    def expr(self, raw, scope):
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("A term must contain exactly one constructor")
        op, value = next(iter(raw.items()))
        if op == "var":
            name = symbol(value)
            if name not in scope:
                raise ValueError("Unbound analysis variable: " + name)
            return scope[name]
        if op == "real":
            numerator, denominator = pair(value)
            integer(numerator, -LIMITS["integer"], LIMITS["integer"])
            integer(denominator, 1, LIMITS["integer"])
            common = math.gcd(numerator, denominator)
            numerator, denominator = numerator // common, denominator // common
            if denominator == 1:
                return f"({numerator} : ℝ)", REAL
            return f"(({numerator} : ℝ) / ({denominator} : ℝ))", REAL
        if op == "zero":
            typ = self.typ(value)
            self.vector(typ)
            return "(0 : " + self.type_source(typ) + ")", typ
        if op == "lambda":
            exact_object(value, {"name", "type", "body"}, "Lambda")
            _, source, typ, nested = self.binding(
                {"name": value["name"], "type": value["type"]}, scope, "lambda"
            )
            body, result = self.expr(value["body"], nested)
            return f"(fun ({source} : {self.type_source(typ)}) => {body})", (
                "function",
                typ,
                result,
            )
        if op in ("neg", "norm", "abs"):
            source, typ = self.expr(value, scope)
            self.vector(typ)
            if op == "abs" and typ != REAL:
                raise ValueError("Absolute value requires Real; use norm for vectors")
            if op == "neg":
                return "(-(" + source + "))", typ
            return ("‖" + source + "‖" if op == "norm" else "|" + source + "|"), REAL
        if op == "pow":
            term, exponent = pair(value)
            source, typ = self.expr(term, scope)
            integer(exponent, 0, LIMITS["power"])
            if typ != REAL:
                raise ValueError("Powers require Real")
            return f"(({source}) ^ {exponent})", REAL
        if op not in ("add", "sub", "mul", "div", "inner", "smul", "apply"):
            raise ValueError("Unknown analysis term constructor")
        left, right = pair(value)
        x, xt = self.expr(left, scope)
        y, yt = self.expr(right, scope)
        if op == "apply":
            if xt[0] != "function" or xt[1] != yt:
                raise ValueError("Function argument type does not match its domain")
            return f"(({x}) ({y}))", xt[2]
        if op == "smul":
            self.vector(yt)
            if xt != REAL:
                raise ValueError("Scalar multiplication requires a Real scalar")
            return f"(({x}) • ({y}))", yt
        if xt != yt:
            raise ValueError("Operand types differ; no implicit casts or space maps")
        self.vector(xt)
        if op in ("mul", "div") and xt != REAL:
            raise ValueError("Vector products/division are not analysis scalar operations")
        if op == "inner":
            return f"(inner ℝ ({x}) ({y}))", REAL
        signs = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
        return f"(({x}) {signs[op]} ({y}))", xt

    def formula(self, raw, scope):
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("A formula must contain exactly one constructor")
        op, value = next(iter(raw.items()))
        if op == "truth" and type(value) is bool:
            return "True" if value else "False"
        if op in ("forall", "exists"):
            exact_object(value, {"name", "type", "body"}, "Quantifier")
            _, source, typ, nested = self.binding(
                {"name": value["name"], "type": value["type"]}, scope, op
            )
            body = self.formula(value["body"], nested)
            quantifier = "∀" if op == "forall" else "∃"
            return f"({quantifier} ({source} : {self.type_source(typ)}), {body})"
        if op == "not":
            return "(¬ " + self.formula(value, scope) + ")"
        if op in ("and", "or", "imp"):
            x, y = pair(value)
            signs = {"and": "∧", "or": "∨", "imp": "→"}
            return (
                "(" + self.formula(x, scope) + " " + signs[op] + " " + self.formula(y, scope) + ")"
            )
        if op not in ("eq", "le", "lt"):
            raise ValueError("Unknown analysis formula constructor")
        left, right = pair(value)
        x, xt = self.expr(left, scope)
        y, yt = self.expr(right, scope)
        if xt != yt or (op in ("le", "lt") and xt != REAL):
            raise ValueError("Formula operands need the same type; order is only on Real")
        signs = {"eq": "=", "le": "≤", "lt": "<"}
        return f"(({x}) {signs[op]} ({y}))"

    def proof(self, raw, scope, hypotheses):
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("A proof must contain exactly one constructor")
        op, value = next(iter(raw.items()))
        if op == "hyp":
            name = symbol(value)
            if name not in hypotheses:
                raise ValueError("Unknown hypothesis")
            return hypotheses[name]
        if op == "use_hyp":
            exact_object(value, {"name", "terms", "proofs"}, "Hypothesis application")
            head = self.proof({"hyp": value["name"]}, scope, hypotheses)
            terms = [self.expr(t, scope)[0] for t in array(value["terms"], 8, "Hypothesis terms")]
            proofs = [
                self.proof(p, scope, hypotheses)
                for p in array(value["proofs"], 8, "Hypothesis proofs")
            ]
            return "(" + " ".join([head, *["(" + t + ")" for t in terms + proofs]]) + ")"
        if op == "intro":
            exact_object(value, {"name", "type", "proof"}, "Universal proof introduction")
            _, source, typ, nested = self.binding(
                {"name": value["name"], "type": value["type"]}, scope, "proof-intro"
            )
            body = self.proof(value["proof"], nested, hypotheses)
            return f"(fun ({source} : {self.type_source(typ)}) => {body})"
        if op == "intro_hyp":
            exact_object(value, {"name", "formula", "proof"}, "Implication proof introduction")
            name = symbol(value["name"])
            source = "mraProof" + str(self.serial)
            self.serial += 1
            statement = self.formula(value["formula"], scope)
            nested = {**hypotheses, name: source}
            return f"(fun ({source} : {statement}) => {self.proof(value['proof'], scope, nested)})"
        if op != "rule":
            raise ValueError("No arbitrary Lean, axioms, placeholders or proof tactics")
        exact_object(value, {"name", "terms", "proofs"}, "Library proof rule")
        name = value["name"]
        if not isinstance(name, str) or name not in RULES:
            raise ValueError("Unknown approved analysis proof rule")
        terms = array(value["terms"], 2, "Rule terms")
        proofs = array(value["proofs"], 2, "Rule proofs")
        if (len(terms), len(proofs)) != RULES[name]:
            raise ValueError("Analysis rule argument counts differ")
        typed = [self.expr(t, scope) for t in terms]
        ts = ["(" + item[0] + ")" for item in typed]
        ps = ["(" + self.proof(p, scope, hypotheses) + ")" for p in proofs]
        if name in (
            "inner_self_nonneg",
            "cauchy_schwarz",
            "norm_nonneg",
            "norm_add_le",
            "norm_smul",
            "inner_self_norm_sq",
        ):
            for _, typ in typed:
                self.vector(typ)
            if name in ("cauchy_schwarz", "norm_add_le") and typed[0][1] != typed[1][1]:
                raise ValueError("Library rule uses different vector spaces")
            if name == "norm_smul" and typed[0][1] != REAL:
                raise ValueError("norm_smul requires a Real scalar")
        if name in ("sq_nonneg", "abs_nonneg", "le_refl") and typed[0][1] != REAL:
            raise ValueError("This proof rule requires Real")
        if name == "congr_arg" and typed[0][1][0] != "function":
            raise ValueError("congr_arg requires a function")
        if name == "inner_self_nonneg":
            return "(real_inner_self_nonneg (x := " + ts[0] + "))"
        if name == "cauchy_schwarz":
            return "(norm_inner_le_norm (𝕜 := ℝ) " + " ".join(ts) + ")"
        if name == "inner_self_norm_sq":
            return "(real_inner_self_eq_norm_sq " + ts[0] + ")"
        heads = {
            "and_intro": "And.intro",
            "eq_trans": "Eq.trans",
            "eq_symm": "Eq.symm",
            "congr_arg": "congrArg",
        }
        head = heads.get(name, name)
        return "(" + " ".join([head, *ts, *ps]) + ")"


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
        raise ValueError("Select analysis-language-v1 explicitly")
    text(raw["title"], 200, "Title")
    text(raw["original"], 4000, "Original statement")
    integer(raw["timeout_seconds"], 1, 30)
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
    return {
        "mode": MODE,
        "limits": dict(LIMITS),
        "types": ["Real", {"space": "declared name"}, {"function": ["domain", "codomain"]}],
        "terms": [
            "var",
            "real",
            "zero",
            "lambda",
            "apply",
            "add",
            "sub",
            "neg",
            "mul",
            "div",
            "pow",
            "abs",
            "norm",
            "inner",
            "smul",
        ],
        "formulas": ["eq", "le", "lt", "and", "or", "imp", "not", "forall", "exists", "truth"],
        "proof_rules": {k: {"terms": v[0], "proofs": v[1]} for k, v in RULES.items()},
        "tactics": ["rfl", "simp", "grind"],
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
    }
