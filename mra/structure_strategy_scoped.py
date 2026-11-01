"""Untrusted v4 scope-aware matching and bounded, diverse program selection."""

from .domain import canonical, digest
from .structure_strategy_ast import RankedPlanner, free_names
from .structure_strategy_terms import SearchLimit, alpha_key


class SliceLimit(Exception):
    """A local search slice ended; other induction directions may still be tried."""


class ScopedPlanner(RankedPlanner):
    def __init__(self, target, budget):
        super().__init__(target, budget)
        self.generalization_analysis = []
        self.selection_analysis = []
        self.local_search_limits = 0
        self.slice_end = None

    def tick(self):
        super().tick()
        if self.slice_end is not None and self.expansions > self.slice_end:
            raise SliceLimit

    def solve(self, goal, rules):
        self.slice_end = self.expansions + min(128, max(8, self.budget["max_expansions"] // 8))
        try:
            return super().solve(goal, rules)
        except SliceLimit:
            self.local_search_limits += 1
            return None
        finally:
            self.slice_end = None

    def match(self, pattern, value, variables, bindings):
        """Match free parameters, comparing local binders by lexical depth.

        A parameter cannot acquire a value dependent on a local binder. Changes
        are transactional, including repeated parameters and mismatched bodies.
        """
        trial = dict(bindings)

        def depth(name, bound):
            return next((i for i, (n, _) in enumerate(reversed(bound)) if n == name), None)

        def walk(a, b, left=(), right=()):
            if type(a) is not type(b):
                return False
            if isinstance(a, list):
                return len(a) == len(b) and all(walk(x, y, left, right) for x, y in zip(a, b))
            if not isinstance(a, dict):
                return a == b
            if set(a) == {"var"}:
                name = a["var"]
                local = depth(name, left)
                if local is not None:
                    return set(b) == {"var"} and local == depth(b["var"], right)
                if name in variables:
                    if set(free_names(b)) & {n for n, _ in right}:
                        return False
                    if self.typ(b, self.scope) != variables[name]:
                        return False
                    if name in trial:
                        return alpha_key(trial[name]) == alpha_key(b)
                    trial[name] = b
                    return True
                return set(b) == {"var"} and depth(b["var"], right) is None and a == b
            if a.keys() != b.keys():
                return False
            if set(a) == {"nat_fold"}:
                x, y = a["nat_fold"], b["nat_fold"]
                if not walk(x["index"], y["index"], left, right) or not walk(
                    x["zero"], y["zero"], left, right
                ):
                    return False
                xs, ys = x["step"], y["step"]
                xt = self.typ(x["zero"], {**self.scope, **variables, **dict(left)})
                yt = self.typ(y["zero"], {**self.scope, **dict(right)})
                return xt == yt and walk(
                    xs["body"],
                    ys["body"],
                    (*left, (xs["index"], "Nat"), (xs["value"], xt)),
                    (*right, (ys["index"], "Nat"), (ys["value"], yt)),
                )
            if len(a) == 1 and next(iter(a)) in ("forall", "exists"):
                x, y = next(iter(a.values())), next(iter(b.values()))
                if [v["type"] for v in x["binders"]] != [v["type"] for v in y["binders"]]:
                    return False
                return walk(
                    x["body"],
                    y["body"],
                    (*left, *((v["name"], v["type"]) for v in x["binders"])),
                    (*right, *((v["name"], v["type"]) for v in y["binders"])),
                )
            return all(walk(a[k], b[k], left, right) for k in a)

        if not walk(pattern, value):
            return False
        bindings.update(trial)
        return True

    def rules(self, extra=()):
        result = list(extra)
        for assumption in self.target["assumptions"]:
            body, variables = assumption["formula"], {}
            while set(body) == {"forall"}:
                variables.update({v["name"]: v["type"] for v in body["forall"]["binders"]})
                body = body["forall"]["body"]
            if set(body) == {"eq"}:
                left, right = (self.norm(x, {**self.scope, **variables}) for x in body["eq"])
                result.append((assumption["name"], variables, left, right))
        return result

    def options(self, ih, induction):
        premises = set(free_names([a["formula"] for a in self.target["assumptions"]]))
        originals = {v["name"] for v in self.target["variables"]}
        eligible, excluded = [], []
        for name in dict.fromkeys(free_names(ih)):
            if name not in originals or name == induction:
                continue
            if name in premises:
                excluded.append({"variable": name, "reason": "original_premise_dependency"})
            else:
                eligible.append(name)
        unique = []
        for option in [eligible, *[[n] for n in eligible], []]:
            if option not in unique:
                unique.append(option)
        self.generalization_analysis.append(
            {
                "induction_variable": induction,
                "eligible": eligible,
                "excluded": excluded,
                "options": unique,
            }
        )
        return unique

    def programs(self):
        found = []

        def add(label, proof, origin, solved, group):
            found.append((label, proof, origin, solved, group))

        try:
            ranking = self.rank()
            rules = self.rules()
            direct = self.solve(self.target["goal"], rules)
            if direct:
                add("符号闭合 · 直接", direct, "symbolic-rewrite", 2, "direct")
            states = []
            for variable in ranking:
                name = variable["variable"]
                index = "$induction_index"
                self.scope[index] = "Nat"
                base_goal = self.replace(self.target["goal"], {name: {"nat": 0}})
                step_goal = self.replace(self.target["goal"], {name: {"$succ": {"var": index}}})
                ih = self.norm(self.replace(self.target["goal"], {name: {"var": index}}))
                states.append((name, base_goal, step_goal, ih, self.options(ih, name)))
            # One option for each direction before spending fuel on extra variants.
            for position in range(max((len(s[4]) for s in states), default=0)):
                for name, base_goal, step_goal, ih, options in states:
                    if position >= len(options):
                        continue
                    generalize = options[position]
                    quantified = {n: self.scope[n] for n in generalize}
                    extra = [("@ih", quantified, *ih["eq"])] if set(ih) == {"eq"} else []
                    base = self.solve(base_goal, rules)
                    step = self.solve(step_goal, self.rules(extra))
                    proof = {
                        "method": "nat_induction",
                        "variable": name,
                        "generalize": generalize,
                        "base": base or {"method": "grind"},
                        "step": step or {"method": "grind"},
                    }
                    solved = int(base is not None) + int(step is not None)
                    origin = "symbolic" if solved == 2 else "heuristic"
                    origin += "-generalized-induction" if generalize else "-induction"
                    add(
                        "归纳 · " + name + (" / " + ", ".join(generalize) if generalize else ""),
                        proof,
                        origin,
                        solved,
                        name,
                    )
        except SearchLimit:
            pass
        # Symbolically closed candidates lead. For incomplete candidates, retain
        # different induction directions before extra generalization variants.
        unique = {}
        for entry in found:
            unique.setdefault(canonical(entry[1]), entry)
        entries = list(unique.values())
        closed = [e for e in entries if e[3] == 2]
        pending = [e for e in entries if e[3] != 2]
        pending.sort(key=lambda e: -e[3])
        diverse, rest, seen = [], [], set()
        for entry in pending:
            (rest if entry[4] in seen else diverse).append(entry)
            seen.add(entry[4])
        ordered = [*closed, *diverse, *rest]
        ordered += [
            ("固定 grind 尝试", {"method": "grind"}, "fixed", 0, "fixed"),
            ("直接自反备选", {"method": "rfl"}, "fixed", 0, "fixed"),
        ]
        unique = {}
        for entry in ordered:
            unique.setdefault(canonical(entry[1]), entry)
        entries = list(unique.values())
        selected = entries[: self.budget["max_candidates"]]
        self.selection_analysis = [
            {
                "proof_fingerprint": digest(e[1]),
                "origin": e[2],
                "symbolically_closed_branches": e[3],
                "direction": e[4],
                "selected": e in selected,
            }
            for e in entries
        ]
        return [(e[0], e[1], e[2]) for e in selected]
