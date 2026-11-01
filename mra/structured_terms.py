"""Bounded explicit carriers, operations, closed definitions and primitive Nat folds."""

from copy import deepcopy

from .formalizer import exact_object
from .number_theory_terms import identifier, text
from .typed_terms import Compiler as NumericCompiler, TYPES, bounded_data

MODE = "explicit-structures-v1"
LIMITS = {
    "bytes": 196608,
    "nodes": 320,
    "depth": 16,
    "carriers": 2,
    "operations": 8,
    "definitions": 6,
    "variables": 8,
    "folds": 4,
}
FIELDS = {
    "schema",
    "title",
    "original",
    "carriers",
    "operations",
    "definitions",
    "variables",
    "assumptions",
    "goal",
    "proof",
    "timeout_seconds",
}


class Compiler:
    def __init__(self, carriers):
        self.carriers = carriers
        self.symbols = {}
        self.nodes = self.quantifiers = self.folds = 0
        self.calls = set()

    def typ(self, raw, proposition=False):
        if not isinstance(raw, str) or raw not in (
            *TYPES,
            *self.carriers,
            *(("Prop",) if proposition else ()),
        ):
            raise ValueError("Use an explicit declared carrier or Nat/Int/Rat type")
        return self.carriers.get(raw, raw)

    def bindings(self, raw, maximum, prefix):
        if not isinstance(raw, list) or len(raw) > maximum:
            raise ValueError("Too many typed bindings")
        result = {}
        for i, item in enumerate(raw):
            exact_object(item, {"name", "type"}, "Typed binding")
            name = identifier(item["name"])
            self.typ(item["type"])
            if name in result:
                raise ValueError("Duplicate binding")
            result[name] = {"type": item["type"], "lean": prefix + str(i)}
        return result

    def term(self, raw, scope, depth=0):
        self.nodes += 1
        if self.nodes > LIMITS["nodes"] or depth > LIMITS["depth"]:
            raise ValueError("Expression exceeds node or depth budget")
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError("Each expression requires one supported constructor")
        op, value = next(iter(raw.items()))

        def sub(value):
            return self.term(value, scope, depth + 1)

        if op == "var":
            name = identifier(value)
            if name not in scope:
                raise ValueError("Unbound variable: " + name)
            return scope[name]["type"], scope[name]["lean"]
        if op in ("nat", "int", "rat"):
            return NumericCompiler().term(raw, {})
        if op == "cast":
            exact_object(value, {"to", "value"}, "Explicit cast")
            typ, src = sub(value["value"])
            target = value["to"]
            if (typ, target) not in (("Nat", "Int"), ("Nat", "Rat"), ("Int", "Rat")):
                raise ValueError("Only explicit widening numeric casts")
            return target, f"({src} : {target})"
        if op == "call":
            exact_object(value, {"name", "args"}, "Operation or definition application")
            name = identifier(value["name"])
            if name not in self.symbols:
                raise ValueError("Unknown, forward or recursive operation/definition: " + name)
            symbol, args = self.symbols[name], value["args"]
            if not isinstance(args, list) or len(args) != len(symbol["parameters"]):
                raise ValueError("Every operation/definition argument must be explicit")
            rendered = []
            for arg, typ in zip(args, symbol["parameters"]):
                actual, src = sub(arg)
                if actual != typ:
                    raise ValueError("Argument type differs; implicit casts are forbidden")
                rendered.append(src)
            self.calls.add(name)
            return symbol["result_type"], "(" + " ".join([symbol["lean"], *rendered]) + ")"
        if op in ("forall", "exists"):
            exact_object(value, {"binders", "body"}, "Quantifier")
            names = self.bindings(value["binders"], 4, "mra_q")
            if not names or set(names) & set(scope):
                raise ValueError("Quantifier must bind fresh variables")
            names = {
                n: {**v, "lean": f"mra_q{self.quantifiers + i}"}
                for i, (n, v) in enumerate(names.items())
            }
            self.quantifiers += len(names)
            typ, src = self.term(value["body"], {**scope, **names}, depth + 1)
            if typ != "Prop":
                raise ValueError("Quantifier body must be a proposition")
            binders = " ".join(f"({b['lean']} : {self.typ(b['type'])})" for b in names.values())
            symbol = "∀" if op == "forall" else "∃"
            return "Prop", f"({symbol} {binders}, {src})"
        if op == "nat_fold":
            exact_object(value, {"index", "zero", "step"}, "Primitive natural fold")
            exact_object(value["step"], {"index", "value", "body"}, "Fold step")
            ntyp, nsrc = sub(value["index"])
            typ, zero = sub(value["zero"])
            if ntyp != "Nat" or typ == "Prop":
                raise ValueError("Fold index must be Nat and result must be a data type")
            step = value["step"]
            k, acc = identifier(step["index"]), identifier(step["value"])
            if k == acc or k in scope or acc in scope:
                raise ValueError("Fold binders must be distinct and cannot shadow outer variables")
            self.folds += 1
            if self.folds > LIMITS["folds"]:
                raise ValueError("At most four primitive natural folds")
            ki, ai = f"mra_k{self.folds}", f"mra_acc{self.folds}"
            actual, body = self.term(
                step["body"],
                {**scope, k: {"type": "Nat", "lean": ki}, acc: {"type": typ, "lean": ai}},
                depth + 1,
            )
            if actual != typ:
                raise ValueError("Fold step changes the declared result type")
            ty = self.typ(typ)
            return (
                typ,
                f"(Nat.rec (motive := fun _ => {ty}) ({zero}) (fun ({ki} : Nat) ({ai} : {ty}) => {body}) ({nsrc}))",
            )
        if op in ("not", "neg"):
            typ, src = sub(value)
            if op == "not" and typ == "Prop":
                return "Prop", f"(¬ {src})"
            if op == "neg" and typ in ("Int", "Rat"):
                return typ, f"(-{src})"
            raise ValueError("Use logical negation or numeric Int/Rat negation")
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
            raise ValueError("Unsupported expression or malformed binary operands")
        a, left = sub(value[0])
        b, right = sub(value[1])
        if a != b:
            raise ValueError("Operand types differ")
        if op in ("and", "or", "iff", "imp"):
            if a != "Prop":
                raise ValueError("Logical operands must be propositions")
        elif op == "eq":
            if a == "Prop":
                raise ValueError("Use iff for propositions")
        elif a not in TYPES:
            raise ValueError("Carrier operations must be explicitly declared and called")
        if op == "div" and a != "Rat":
            raise ValueError("Numeric division requires Rat")
        result = "Prop" if op in ("eq", "le", "lt", "and", "or", "iff", "imp") else a
        return result, f"({left} {symbols[op]} {right})"


def compile_request(raw):
    bounded_data(raw)
    exact_object(raw, FIELDS, "Complete explicit structure target")
    if raw["schema"] != MODE:
        raise ValueError("Use explicit-structures-v1")
    text(raw["title"], 160, "Title")
    text(raw["original"], 12000, "Original statement")
    timeout = raw["timeout_seconds"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        raise ValueError("Candidate and independent replay share 1–30 worker seconds")
    carriers = raw["carriers"]
    if not isinstance(carriers, list) or len(carriers) > LIMITS["carriers"]:
        raise ValueError("At most two explicit carriers of universe Type")
    cs, outer = {}, []
    for i, c in enumerate(carriers):
        exact_object(c, {"name", "original"}, "Carrier")
        name = identifier(c["name"])
        text(c["original"], 2000, "Carrier meaning")
        if name in cs:
            raise ValueError("Duplicate carrier")
        cs[name] = f"mra_t{i}"
        outer.append(f"({cs[name]} : Type)")
    compiler = Compiler(cs)
    operations = raw["operations"]
    if not isinstance(operations, list) or len(operations) > LIMITS["operations"]:
        raise ValueError("At most eight operation or predicate parameters")
    operation_details = []
    for i, op in enumerate(operations):
        exact_object(op, {"name", "original", "parameters", "result_type"}, "Operation")
        name = identifier(op["name"])
        text(op["original"], 2000, "Operation meaning")
        if (
            name in compiler.symbols
            or not isinstance(op["parameters"], list)
            or len(op["parameters"]) > 4
        ):
            raise ValueError("Distinct operations have at most four explicit data parameters")
        params = [compiler.typ(t) for t in op["parameters"]]
        result = compiler.typ(op["result_type"], True)
        lean = f"mra_o{i}"
        sig = " → ".join([*params, result])
        outer.append(f"({lean} : {sig})")
        compiler.symbols[name] = {**deepcopy(op), "lean": lean}
        operation_details.append({"name": name, "lean": lean, "signature": sig})
    definitions = raw["definitions"]
    if not isinstance(definitions, list) or len(definitions) > LIMITS["definitions"]:
        raise ValueError("At most six closed, nonrecursive definitions")
    lets, details = [], []
    for i, d in enumerate(definitions):
        exact_object(d, {"name", "original", "parameters", "result_type", "body"}, "Definition")
        name = identifier(d["name"])
        text(d["original"], 3000, "Definition meaning")
        if name in compiler.symbols:
            raise ValueError("Duplicate operation/definition")
        params = compiler.bindings(d["parameters"], 4, f"mra_p{i}_")
        result = compiler.typ(d["result_type"], True)
        compiler.calls = set()
        typ, body = compiler.term(d["body"], params)
        if typ != d["result_type"]:
            raise ValueError("Definition body differs from declared result type")
        lean = f"mra_d{i}"
        sig = " → ".join([*[compiler.typ(b["type"]) for b in params.values()], result])
        bs = " ".join(f"({b['lean']} : {compiler.typ(b['type'])})" for b in params.values())
        value = f"fun {bs} => {body}" if params else body
        lets.append(f"let {lean} : {sig} := {value};")
        details.append(
            {
                "name": name,
                "lean": lean,
                "signature": sig,
                "body": body,
                "depends_on": sorted(compiler.calls),
            }
        )
        compiler.symbols[name] = {
            "lean": lean,
            "parameters": [b["type"] for b in params.values()],
            "result_type": d["result_type"],
        }
    scope = compiler.bindings(raw["variables"], LIMITS["variables"], "mra_v")
    assumptions = raw["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 8:
        raise ValueError("At most eight complete explicit conditions/laws")
    names, rendered = set(), []
    for item in assumptions:
        exact_object(item, {"name", "formula"}, "Original condition or law")
        name = identifier(item["name"])
        if name in names:
            raise ValueError("Duplicate condition/law")
        names.add(name)
        typ, src = compiler.term(item["formula"], scope)
        if typ != "Prop":
            raise ValueError("Conditions and laws must be propositions")
        rendered.append({"name": name, "formula": src})
    typ, goal = compiler.term(raw["goal"], scope)
    if typ != "Prop":
        raise ValueError("Complete goal must be a proposition")
    body = " → ".join([*[a["formula"] for a in rendered], goal])
    if scope:
        bs = " ".join(f"({b['lean']} : {compiler.typ(b['type'])})" for b in scope.values())
        body = f"∀ {bs}, {body}"
    statement = "\n".join([*lets, body])
    if outer:
        statement = "∀ " + " ".join(outer) + ",\n" + statement
    return {
        "request": deepcopy(raw),
        "formal_statement": statement,
        "carriers": cs,
        "operations": operation_details,
        "definitions": details,
        "variable_mapping": scope,
        "assumptions": rendered,
        "node_count": compiler.nodes,
        "fold_count": compiler.folds,
    }
