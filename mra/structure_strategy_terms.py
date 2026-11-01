"""Untrusted bounded rewrite proposals; Python normalization never certifies a theorem."""

from collections import deque
from copy import deepcopy
import time

from .domain import canonical
from .structure_composition_terms import substitute

WALL_SECONDS = 0.75
MAX_NORMALIZATION_NODES = 32768
MAX_INTERMEDIATE_NODES = 4096


class SearchLimit(Exception):
    pass


def alpha_key(value, bound=()):
    """Canonicalize lexical fold/quantifier binders without capturing free variables."""
    if isinstance(value, list):
        return tuple(alpha_key(v, bound) for v in value)
    if not isinstance(value, dict):
        return value
    if set(value) == {"var"}:
        name = value["var"]
        return ("bound", bound[::-1].index(name)) if name in bound else ("free", name)
    if set(value) == {"nat_fold"}:
        f = value["nat_fold"]
        step = f["step"]
        return (
            "fold",
            alpha_key(f["index"], bound),
            alpha_key(f["zero"], bound),
            alpha_key(step["body"], (*bound, step["index"], step["value"])),
        )
    if len(value) == 1 and next(iter(value)) in ("forall", "exists"):
        op, body = next(iter(value.items()))
        return (
            op,
            tuple(b["type"] for b in body["binders"]),
            alpha_key(body["body"], (*bound, *(b["name"] for b in body["binders"]))),
        )
    return tuple((k, alpha_key(v, bound)) for k, v in sorted(value.items()))


class Planner:
    def __init__(self, target, budget):
        self.target, self.budget = target, budget
        self.started = time.monotonic()
        self.expansions = self.normalization_nodes = 0
        self.stop_reason = "bounded_programs_generated"
        self.scope = {v["name"]: v["type"] for v in target["variables"]}
        self.symbols = {v["name"]: v for v in (*target["operations"], *target["definitions"])}
        self.definitions = {v["name"]: v for v in target["definitions"]}

    def tick(self):
        if time.monotonic() - self.started >= WALL_SECONDS:
            self.stop_reason = "synthesis_wall_limit"
            raise SearchLimit
        if self.expansions >= self.budget["max_expansions"]:
            self.stop_reason = "expansion_limit"
            raise SearchLimit
        self.expansions += 1

    def typ(self, value, scope):
        op, args = next(iter(value.items()))
        if op == "var":
            return scope.get(args)
        if op in ("nat", "$succ"):
            return "Nat"
        if op in ("int", "rat"):
            return op.title()
        if op == "call":
            return self.symbols[args["name"]]["result_type"]
        if op == "cast":
            return args["to"]
        if op == "nat_fold":
            return self.typ(args["zero"], scope)
        if op in ("add", "mul", "sub", "div", "neg"):
            return self.typ(args[0] if isinstance(args, list) else args, scope)
        return "Prop"

    def replace(self, formula, bindings, scope=None):
        """Reject expansion before the frozen substitution helper copies its arguments."""

        def size(value):
            pending, count = [value], 0
            while pending:
                v = pending.pop()
                count += 1
                if count > MAX_INTERMEDIATE_NODES:
                    return count
                if isinstance(v, dict):
                    pending.extend(v.values())
                elif isinstance(v, list):
                    pending.extend(v)
            return count

        sizes = {name: size(v) for name, v in bindings.items()}
        pending, estimate = [formula], 0
        while pending:
            v = pending.pop()
            estimate += 1
            if isinstance(v, dict):
                if set(v) == {"var"} and v["var"] in sizes:
                    estimate += sizes[v["var"]]
                pending.extend(v.values())
            elif isinstance(v, list):
                pending.extend(v)
            if estimate > MAX_INTERMEDIATE_NODES:
                self.stop_reason = "intermediate_expression_limit"
                raise SearchLimit
        return substitute(formula, bindings, self.scope if scope is None else scope)

    def norm(self, value, scope=None, depth=0):
        """Approximate only closed definitions and Nat computation, with hard fuel."""
        scope = self.scope if scope is None else scope
        if self.normalization_nodes >= MAX_NORMALIZATION_NODES or depth > 64:
            self.stop_reason = "normalization_limit"
            raise SearchLimit
        self.normalization_nodes += 1
        if time.monotonic() - self.started >= WALL_SECONDS:
            self.stop_reason = "synthesis_wall_limit"
            raise SearchLimit
        if not isinstance(value, dict):
            if isinstance(value, list):
                return [self.norm(v, scope, depth + 1) for v in value]
            return value
        op, args = next(iter(value.items()))
        if op == "call":
            items = [self.norm(v, scope, depth + 1) for v in args["args"]]
            if args["name"] in self.definitions:
                d = self.definitions[args["name"]]
                body = self.replace(
                    d["body"], dict(zip((p["name"] for p in d["parameters"]), items)), scope
                )
                return self.norm(body, scope, depth + 1)
            return {"call": {"name": args["name"], "args": items}}
        if op == "nat_fold":
            index = self.norm(args["index"], scope, depth + 1)
            zero = self.norm(args["zero"], scope, depth + 1)
            step = args["step"]
            if index == {"nat": 0}:
                return zero
            predecessor = None
            if set(index) == {"$succ"}:
                predecessor = index["$succ"]
            elif set(index) == {"nat"} and 0 < index["nat"] <= 16:
                predecessor = {"nat": index["nat"] - 1}
            if predecessor is not None:
                previous = {"nat_fold": {**args, "index": predecessor}}
                body = self.replace(
                    step["body"], {step["index"]: predecessor, step["value"]: previous}, scope
                )
                return self.norm(body, scope, depth + 1)
            inner = {**scope, step["index"]: "Nat", step["value"]: self.typ(zero, scope)}
            return {
                "nat_fold": {
                    "index": index,
                    "zero": zero,
                    "step": {**step, "body": self.norm(step["body"], inner, depth + 1)},
                }
            }
        if op in ("forall", "exists"):
            inner = {**scope, **{b["name"]: b["type"] for b in args["binders"]}}
            return {op: {**args, "body": self.norm(args["body"], inner, depth + 1)}}
        if op == "add":
            left, right = (self.norm(v, scope, depth + 1) for v in args)
            if self.typ(left, scope) == self.typ(right, scope) == "Nat":
                if right == {"nat": 0}:
                    return left
                if set(right) == {"nat"} and 0 < right["nat"] <= 16:
                    result = left
                    for _ in range(right["nat"]):
                        result = {"$succ": result}
                    return result
                if set(right) == {"$succ"}:
                    return {"$succ": self.norm({"add": [left, right["$succ"]]}, scope, depth + 1)}
            return {op: [left, right]}
        if op in ("var", "nat", "int", "rat"):
            return deepcopy(value)
        if op == "cast":
            return {op: {**args, "value": self.norm(args["value"], scope, depth + 1)}}
        if op == "$succ":
            return {op: self.norm(args, scope, depth + 1)}
        return {op: self.norm(args, scope, depth + 1)}

    def rules(self, extra=()):
        result = []
        for assumption in self.target["assumptions"]:
            body, variables = assumption["formula"], {}
            while set(body) == {"forall"}:
                variables.update({v["name"]: v["type"] for v in body["forall"]["binders"]})
                body = body["forall"]["body"]
            if set(body) != {"eq"}:
                continue
            left, right = (self.norm(x, {**self.scope, **variables}) for x in body["eq"])
            # Binder-containing patterns are left to Lean/grind, not an unsound matcher.
            if b'"nat_fold"' in canonical([left, right]) or b'"forall"' in canonical([left, right]):
                continue
            result.append((assumption["name"], variables, left, right))
        return [*extra, *result]

    def match(self, pattern, value, variables, bindings):
        if set(pattern) == {"var"} and pattern["var"] in variables:
            name = pattern["var"]
            if self.typ(value, self.scope) != variables[name]:
                return False
            if name in bindings:
                return alpha_key(bindings[name]) == alpha_key(value)
            bindings[name] = value
            return True
        if not variables:
            return alpha_key(pattern) == alpha_key(value)
        if type(pattern) is not type(value) or pattern.keys() != value.keys():
            return False

        def walk(a, b):
            if isinstance(a, dict):
                return isinstance(b, dict) and self.match(a, b, variables, bindings)
            if isinstance(a, list):
                return (
                    isinstance(b, list)
                    and len(a) == len(b)
                    and all(walk(x, y) for x, y in zip(a, b))
                )
            return a == b

        return all(walk(pattern[k], value[k]) for k in pattern)

    def rewrite(self, goal, rule, reverse):
        _, variables, left, right = rule
        if reverse:
            left, right = right, left

        def walk(value):
            bindings = {}
            if self.match(left, value, variables, bindings):
                # Every right-side rule variable must have been determined by matching.
                missing = set(variables) - set(bindings)
                if any(b'"var":"' + n.encode() + b'"' in canonical(right) for n in missing):
                    return value, False
                return self.replace(right, bindings, self.scope), True
            op, args = next(iter(value.items()))
            if op == "call":
                items = list(args["args"])
                for i, arg in enumerate(items):
                    new, changed = walk(arg)
                    if changed:
                        items[i] = new
                        return {op: {**args, "args": items}}, True
            elif isinstance(args, list):
                for i, arg in enumerate(args):
                    new, changed = walk(arg)
                    if changed:
                        return {op: [*args[:i], new, *args[i + 1 :]]}, True
            # Do not rewrite under lexical binders or recurse through metadata.
            return value, False

        goal_left, goal_right = goal["eq"]
        new, changed = walk(goal_left)
        if changed:
            return {"eq": [new, goal_right]}
        new, changed = walk(goal_right)
        return {"eq": [goal_left, new]} if changed else None

    def solve(self, goal, rules):
        goal = self.norm(goal)
        if set(goal) != {"eq"}:
            return None
        queue = deque([(goal, [])])
        seen = {alpha_key(goal)}
        while queue:
            current, steps = queue.popleft()
            if alpha_key(current["eq"][0]) == alpha_key(current["eq"][1]):
                return (
                    {"method": "rewrite", "steps": steps, "finish": "rfl"}
                    if steps
                    else {"method": "rfl"}
                )
            if len(steps) >= self.budget["max_rewrite_steps"]:
                continue
            for rule in rules:
                for reverse in (False, True):
                    self.tick()
                    new = self.rewrite(current, rule, reverse)
                    if new is None:
                        continue
                    new = self.norm(new)
                    key = alpha_key(new)
                    if key in seen:
                        continue
                    seen.add(key)
                    queue.append((new, [*steps, {"rule": rule[0], "reverse": reverse}]))
        return None

    def programs(self):
        found = [("直接自反", {"method": "rfl"}, "fixed")]
        try:
            rules = self.rules()
            has_fold = b'"nat_fold"' in canonical([self.target["definitions"], self.target["goal"]])
            if has_fold:
                # Prefer the last declared Nat variable, without using names or a reference proof.
                for variable in reversed(self.target["variables"]):
                    if variable["type"] != "Nat":
                        continue
                    name = variable["name"]
                    index = "$induction_index"
                    self.scope[index] = "Nat"
                    base_goal = self.replace(self.target["goal"], {name: {"nat": 0}}, self.scope)
                    step_goal = self.replace(
                        self.target["goal"], {name: {"$succ": {"var": index}}}, self.scope
                    )
                    ih = self.norm(
                        self.replace(self.target["goal"], {name: {"var": index}}, self.scope)
                    )
                    extra = [("@ih", {}, *ih["eq"])] if set(ih) == {"eq"} else []
                    base = self.solve(base_goal, rules)
                    step = self.solve(step_goal, self.rules(extra))
                    proof = {
                        "method": "nat_induction",
                        "variable": name,
                        "generalize": [],
                        "base": base or {"method": "grind"},
                        "step": step or {"method": "grind"},
                    }
                    found.append(
                        (
                            "自然数归纳 · " + name,
                            proof,
                            "symbolic-induction" if base and step else "heuristic-induction",
                        )
                    )
            direct = self.solve(self.target["goal"], rules)
            if direct and direct["method"] != "rfl":
                found.append(("显式定律改写", direct, "symbolic-rewrite"))
        except SearchLimit:
            pass
        found.append(("固定 grind 尝试", {"method": "grind"}, "fixed"))
        unique = {}
        for label, proof, origin in found:
            unique.setdefault(canonical(proof), (label, proof, origin))
        return list(unique.values())[: self.budget["max_candidates"]]
