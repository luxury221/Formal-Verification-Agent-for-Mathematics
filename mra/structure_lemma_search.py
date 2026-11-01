"""Untrusted bounded proof-data applications; no theorem is certified by this search."""

from collections import deque
from copy import deepcopy

from .structure_strategy_generalization import GeneralizingPlanner
from .structure_strategy_terms import alpha_key


def children(term):
    """Only data arguments; never descend into lexical binders or metadata."""
    op, value = next(iter(term.items()))
    if op == "call":
        return [(("call", i), v) for i, v in enumerate(value["args"])]
    if op in ("add", "sub", "mul", "div", "neg"):
        return (
            [((op, i), v) for i, v in enumerate(value)]
            if isinstance(value, list)
            else [((op, None), value)]
        )
    if op == "cast":
        return [((op, None), value["value"])]
    return []


def plug(term, slot, value):
    result = deepcopy(term)
    op, index = slot
    if op == "call":
        result[op]["args"][index] = value
    elif op == "cast":
        result[op]["value"] = value
    elif index is None:
        result[op] = value
    else:
        result[op][index] = value
    return result


class LemmaSearch(GeneralizingPlanner):
    def __init__(self, target, budget):
        super().__init__(target, budget)
        self.sources = []
        self.diagnostics = []
        self.diagnostic_keys = set()
        self.pool = []
        self.pool_seen = set()
        self.pool_truncated = False
        self.source_sets = 0
        self.congruence_name = "reusearg0"
        used = set(self.scope)
        while self.congruence_name in used:
            self.congruence_name += "x"
        for v in target["variables"]:
            self.add_term({"var": v["name"]})
        self.collect(target["goal"])
        for a in target["assumptions"]:
            self.collect(a["formula"])

    def match(self, pattern, value, variables, bindings):
        self.tick()
        return super().match(pattern, value, variables, bindings)

    def add_term(self, term):
        key = alpha_key(term)
        if key in self.pool_seen:
            return
        if len(self.pool) >= self.budget["max_terms"]:
            self.pool_truncated = True
            return
        self.pool_seen.add(key)
        self.pool.append(deepcopy(term))

    def collect(self, value):
        if not isinstance(value, dict):
            return
        op, body = next(iter(value.items()))
        if op in ("forall", "exists", "nat_fold"):
            # A whole closed data fold may be an explicit argument. Its local
            # variables never become free members of the argument pool.
            if op == "nat_fold":
                self.add_term(value)
            return
        if self.typ(value, self.scope) != "Prop":
            self.add_term(value)
            for _, child in children(value):
                self.collect(child)
        elif isinstance(body, list):
            for item in body:
                self.collect(item)
        elif isinstance(body, dict):
            self.collect(body)

    def note(self, reason, goal, source=None, **detail):
        if len(self.diagnostics) >= 16:
            return
        value = {"reason": reason, "goal": deepcopy(goal), **detail}
        if source is not None:
            value["item_id"] = source["item_id"]
        key = alpha_key(value)
        if key not in self.diagnostic_keys:
            self.diagnostic_keys.add(key)
            self.diagnostics.append(value)

    def instantiations(self, source, pattern, value):
        signature = source["request"]
        variables = {v["name"]: v["type"] for v in signature["variables"]}
        bindings = {}
        if not self.match(pattern, value, variables, bindings):
            return
        missing = [v for v in signature["variables"] if v["name"] not in bindings]

        def fill(index, current):
            if index == len(missing):
                yield current
                return
            parameter = missing[index]
            choices = [t for t in self.pool if self.typ(t, self.scope) == parameter["type"]]
            if not choices:
                self.note("no_typed_argument_in_bounded_pool", value, source, parameter=parameter)
            for term in choices:
                self.tick()
                yield from fill(index + 1, {**current, parameter["name"]: term})

        yield from fill(0, bindings)

    def premises(self, source, bindings, depth, path):
        formulas = [self.replace(a["formula"], bindings) for a in source["request"]["assumptions"]]
        any_result = False

        def combine(index, proofs, used):
            if index == len(formulas):
                yield proofs, used
                return
            for proof, names in self.solve(formulas[index], depth + 1, path):
                yield from combine(index + 1, [*proofs, proof], used | names)

        for proofs, used in combine(0, [], set()):
            any_result = True
            yield proofs, used
        if not any_result:
            self.note(
                "premise_program_not_found_within_bounds",
                self.replace(source["request"]["goal"], bindings),
                source,
                substitution=deepcopy(bindings),
                obligations=[
                    {
                        "formula": f,
                        "matched_original_hypothesis": any(
                            alpha_key(f) == alpha_key(a["formula"])
                            for a in self.target["assumptions"]
                        ),
                    }
                    for f in formulas
                ],
            )

    def application(self, source, bindings, proofs):
        return {
            "lemma": source["alias"],
            "terms": [deepcopy(bindings[v["name"]]) for v in source["request"]["variables"]],
            "proofs": proofs,
        }

    def positions(self, term, context=()):
        yield term, context
        for slot, value in children(term):
            yield from self.positions(value, (*context, (term, slot)))

    def lift(self, value, proof, context):
        result, current = proof, value
        for outer, slot in reversed(context):
            body = plug(outer, slot, {"var": self.congruence_name})
            result = {
                "congr": {
                    "variable": {
                        "name": self.congruence_name,
                        "type": self.typ(current, self.scope),
                    },
                    "body": body,
                    "proof": result,
                }
            }
            current = outer
        return result

    def equalities(self, goal, depth, path):
        left, right = goal["eq"]
        queue = deque([(left, None, set(), 0)])
        seen = {(alpha_key(left), frozenset())}
        while queue:
            current, prefix, used, steps = queue.popleft()
            self.tick()
            if alpha_key(current) == alpha_key(right):
                yield prefix or {"refl": left}, used
                continue
            if steps >= self.budget["max_rewrite_steps"]:
                continue
            for term, context in self.positions(current):
                for source in self.sources:
                    equation = source["request"]["goal"]
                    if set(equation) != {"eq"}:
                        continue
                    for reverse in (False, True):
                        a, b = equation["eq"][::-1] if reverse else equation["eq"]
                        for bindings in self.instantiations(source, a, term):
                            replacement = self.replace(b, bindings)
                            if alpha_key(replacement) == alpha_key(term):
                                continue
                            new = replacement
                            for outer, slot in reversed(context):
                                new = plug(outer, slot, new)
                            for proofs, names in self.premises(source, bindings, depth, path):
                                names = used | names | {source["alias"]}
                                key = (alpha_key(new), frozenset(names))
                                if key in seen:
                                    continue
                                seen.add(key)
                                step = self.application(source, bindings, proofs)
                                if reverse:
                                    step = {"symm": step}
                                step = self.lift(term, step, context)
                                proof = {"trans": [prefix, step]} if prefix else step
                                queue.append((new, proof, names, steps + 1))

    def solve(self, goal, depth=0, path=()):
        self.tick()
        if depth > self.budget["max_depth"]:
            self.note("proof_depth_limit", goal)
            return
        key = alpha_key(goal)
        if key in path:
            self.note("cyclic_obligation", goal)
            return
        path = (*path, key)
        for assumption in self.target["assumptions"]:
            if alpha_key(assumption["formula"]) == key:
                yield {"hyp": assumption["name"]}, set()
        if set(goal) == {"eq"} and alpha_key(goal["eq"][0]) == alpha_key(goal["eq"][1]):
            yield {"refl": goal["eq"][0]}, set()
        if set(goal) == {"and"}:
            for first, names in self.solve(goal["and"][0], depth + 1, path):
                for second, others in self.solve(goal["and"][1], depth + 1, path):
                    yield {"pair": [first, second]}, names | others
        for source in self.sources:
            for bindings in self.instantiations(source, source["request"]["goal"], goal):
                for proofs, names in self.premises(source, bindings, depth, path):
                    yield self.application(source, bindings, proofs), names | {source["alias"]}
        if set(goal) == {"eq"}:
            yield from self.equalities(goal, depth, path)
