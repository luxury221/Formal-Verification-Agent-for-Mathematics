"""Closed, explicitly typed numeric definitions compiled only from bounded data."""

from copy import deepcopy
import math

from .domain import canonical
from .formalizer import exact_object
from .number_theory_terms import identifier, text

MODE = "typed-definitions-v1"
TYPES = ("Nat", "Int", "Rat")
LIMITS = {"bytes": 196608, "nodes": 320, "depth": 16, "definitions": 6, "variables": 8}
FIELDS = {
    "schema",
    "title",
    "original",
    "definitions",
    "variables",
    "assumptions",
    "goal",
    "strategy",
    "timeout_seconds",
}


def bounded_data(raw):
    """Bound traversal before serialization, including cycles and malformed Python callers."""
    pending, count = [(raw, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 48:
            raise ValueError("Request data exceeds structural limits")
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise ValueError("Object keys must be strings")
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("Only JSON data is accepted")
        if isinstance(value, str) and len(value) > LIMITS["bytes"]:
            raise ValueError("Text exceeds request limit")
        if type(value) is int and value.bit_length() > 32:
            raise ValueError("Integer exceeds request limit")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Nonfinite number")
    if len(canonical(raw)) > LIMITS["bytes"]:
        raise ValueError("Typed definition request exceeds 192 KiB")


def numeric_type(value):
    if not isinstance(value, str) or value not in TYPES:
        raise ValueError("An explicit Nat, Int or Rat type is required")
    return value


def bindings(raw, maximum):
    if not isinstance(raw, list) or len(raw) > maximum:
        raise ValueError(f"At most {maximum} typed bindings")
    names = set()
    for binding in raw:
        exact_object(binding, {"name", "type"}, "Typed binding")
        name = identifier(binding["name"])
        numeric_type(binding["type"])
        if name in names:
            raise ValueError("Duplicate binding")
        names.add(name)
    return raw


class Compiler:
    def __init__(self):
        self.nodes = 0
        self.quantifiers = 0
        self.definitions = {}
        self.calls = set()

    def term(self, raw, scope, depth=0):
        self.nodes += 1
        if self.nodes > LIMITS["nodes"] or depth > LIMITS["depth"]:
            raise ValueError("Expression exceeds node or depth budget")
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("Each expression needs one supported constructor")
        op, value = next(iter(raw.items()))

        def sub(v):
            return self.term(v, scope, depth + 1)

        if op == "var":
            name = identifier(value)
            if name not in scope:
                raise ValueError("Unbound variable: " + name)
            return scope[name]["type"], scope[name]["lean"]
        if op in ("nat", "int"):
            if type(value) is not int or not (-1000000 <= value <= 1000000):
                raise ValueError("Numeric literals require bounded integers")
            if op == "nat" and value < 0:
                raise ValueError("Nat literals cannot be negative")
            typ = "Nat" if op == "nat" else "Int"
            return typ, f"({value} : {typ})"
        if op == "rat":
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(v) is not int for v in value)
                or not -1000000 <= value[0] <= 1000000
                or not 1 <= value[1] <= 1000000
            ):
                raise ValueError(
                    "Rat literal is [signed numerator, positive denominator], bounded by 1000000"
                )
            return "Rat", f"((({value[0]} : Int) : Rat) / ({value[1]} : Rat))"
        if op == "cast":
            exact_object(value, {"to", "value"}, "Explicit cast")
            typ, src = sub(value["value"])
            target = numeric_type(value["to"])
            if (typ, target) not in (("Nat", "Int"), ("Nat", "Rat"), ("Int", "Rat")):
                raise ValueError("Only explicit widening Nat→Int, Nat→Rat or Int→Rat casts")
            return target, f"({src} : {target})"
        if op == "call":
            exact_object(value, {"name", "args"}, "Definition application")
            name = identifier(value["name"])
            if name not in self.definitions:
                raise ValueError(
                    "Definition must be declared earlier; recursion is forbidden: " + name
                )
            signature = self.definitions[name]
            args = value["args"]
            if not isinstance(args, list) or len(args) != len(signature["parameters"]):
                raise ValueError("Every definition parameter needs an explicit argument")
            rendered = []
            for arg, parameter in zip(args, signature["parameters"]):
                typ, src = sub(arg)
                if typ != parameter["type"]:
                    raise ValueError("Definition argument type differs; use an explicit cast")
                rendered.append(src)
            self.calls.add(name)
            return signature["result_type"], "(" + " ".join([signature["lean"], *rendered]) + ")"
        if op in ("forall", "exists"):
            exact_object(value, {"binders", "body"}, "Quantifier")
            bs = bindings(value["binders"], 4)
            if not bs:
                raise ValueError("Quantifier needs at least one binding")
            inner, rendered = dict(scope), []
            for binding in bs:
                if binding["name"] in inner:
                    raise ValueError("Quantified variables cannot shadow another binding")
                lean = f"mra_q{self.quantifiers}"
                self.quantifiers += 1
                inner[binding["name"]] = {"type": binding["type"], "lean": lean}
                rendered.append(f"({lean} : {binding['type']})")
            typ, src = self.term(value["body"], inner, depth + 1)
            if typ != "Prop":
                raise ValueError("Quantifier body must be a proposition")
            symbol = "∀" if op == "forall" else "∃"
            return "Prop", f"({symbol} {' '.join(rendered)}, {src})"
        if op in ("neg", "not"):
            typ, src = sub(value)
            if op == "neg" and typ in ("Int", "Rat"):
                return typ, f"(-{src})"
            if op == "not" and typ == "Prop":
                return "Prop", f"(¬ {src})"
            raise ValueError("Negation is for Int/Rat; logical not is for Prop")
        symbols = {
            "add": "+",
            "mul": "*",
            "sub": "-",
            "div": "/",
            "eq": "=",
            "le": "≤",
            "lt": "<",
            "and": "∧",
            "or": "∨",
            "iff": "↔",
            "imp": "→",
        }
        if op not in symbols or not isinstance(value, list) or len(value) != 2:
            raise ValueError("Unsupported expression or invalid binary operands")
        left, a = sub(value[0])
        right, b = sub(value[1])
        if left != right:
            raise ValueError("Operand types differ; implicit casts are forbidden")
        if op in ("and", "or", "iff", "imp"):
            if left != "Prop":
                raise ValueError("Logical operands must be propositions")
        elif left not in TYPES:
            raise ValueError("Numeric operands are required")
        if op == "div" and left != "Rat":
            raise ValueError("Division in this protocol requires Rat operands")
        result = "Prop" if op in ("eq", "le", "lt", "and", "or", "iff", "imp") else left
        return result, f"({a} {symbols[op]} {b})"


def compile_request(raw):
    bounded_data(raw)
    exact_object(raw, FIELDS, "Complete typed target")
    if raw["schema"] != MODE:
        raise ValueError("Unsupported typed definition schema")
    # Preserve exact original and definition text; whitespace edits invalidate confirmation.
    text(raw["title"], 160, "Title")
    text(raw["original"], 12000, "Original statement")
    timeout = raw["timeout_seconds"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        raise ValueError("Shared candidate/replay budget must be 1–30 seconds")
    variables = bindings(raw["variables"], LIMITS["variables"])
    definitions = raw["definitions"]
    if not isinstance(definitions, list) or len(definitions) > LIMITS["definitions"]:
        raise ValueError("At most six nonrecursive definitions")
    compiler, lets, definition_details = Compiler(), [], []
    for i, definition in enumerate(definitions):
        exact_object(
            definition, {"name", "original", "parameters", "result_type", "body"}, "Definition"
        )
        name = identifier(definition["name"])
        text(definition["original"], 3000, "Definition original")
        if name in compiler.definitions:
            raise ValueError("Duplicate definition")
        result = definition["result_type"]
        if not isinstance(result, str) or result not in (*TYPES, "Prop"):
            raise ValueError("Definition result must be Nat, Int, Rat or Prop")
        params = bindings(definition["parameters"], 4)
        scope = {
            p["name"]: {"type": p["type"], "lean": f"mra_p{i}_{j}"} for j, p in enumerate(params)
        }
        compiler.calls = set()
        typ, body = compiler.term(definition["body"], scope)
        if typ != result:
            raise ValueError("Definition body does not have its declared result type")
        lean = f"mra_d{i}"
        signature = " → ".join([p["type"] for p in params] + [result])
        bs = " ".join(f"({scope[p['name']]['lean']} : {p['type']})" for p in params)
        value = f"fun {bs} => {body}" if params else body
        lets.append(f"let {lean} : {signature} := {value};")
        definition_details.append(
            {
                "name": name,
                "lean": lean,
                "signature": signature,
                "body": body,
                "depends_on": sorted(compiler.calls),
            }
        )
        compiler.definitions[name] = {**definition, "lean": lean}
    scope = {v["name"]: {"type": v["type"], "lean": f"mra_v{i}"} for i, v in enumerate(variables)}
    assumptions = raw["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 8:
        raise ValueError("At most eight complete original assumptions")
    names, rendered = set(), []
    for assumption in assumptions:
        exact_object(assumption, {"name", "formula"}, "Assumption")
        name = identifier(assumption["name"])
        if name in names:
            raise ValueError("Duplicate assumption")
        names.add(name)
        typ, src = compiler.term(assumption["formula"], scope)
        if typ != "Prop":
            raise ValueError("Assumption must be a proposition")
        rendered.append({"name": name, "formula": src})
    typ, goal = compiler.term(raw["goal"], scope)
    if typ != "Prop":
        raise ValueError("Goal must be a proposition")
    body = " → ".join([a["formula"] for a in rendered] + [goal])
    if variables:
        bs = " ".join(f"({scope[v['name']]['lean']} : {v['type']})" for v in variables)
        body = f"∀ {bs}, {body}"
    return {
        "request": deepcopy(raw),
        "formal_statement": "\n".join([*lets, body]),
        "variable_mapping": scope,
        "definitions": definition_details,
        "assumptions": rendered,
        "node_count": compiler.nodes,
    }
