"""Untrusted bounded parameter generalization; only Lean can validate the result."""

from .domain import canonical
from .structure_strategy_ast import RankedPlanner, free_names
from .structure_strategy_terms import SearchLimit, alpha_key


def protected_names(value):
    """Parameters beneath lexical binders are outside this matcher extension."""
    if isinstance(value, list):
        return {n for v in value for n in protected_names(v)}
    if not isinstance(value, dict):
        return set()
    if len(value) == 1 and next(iter(value)) in ("nat_fold", "forall", "exists"):
        return set(free_names(value))
    return {n for v in value.values() for n in protected_names(v)}


class GeneralizingPlanner(RankedPlanner):
    def __init__(self, target, budget):
        super().__init__(target, budget)
        self.generalization_analysis = []

    def match(self, pattern, value, variables, bindings):
        if (
            isinstance(pattern, dict)
            and len(pattern) == 1
            and next(iter(pattern)) in ("nat_fold", "forall", "exists")
        ):
            # Closed binder subterms compare modulo lexical names. Never bind a
            # generalized parameter to a locally bound variable inside them.
            if set(free_names(pattern)) & set(variables):
                return False
            return alpha_key(pattern) == alpha_key(value)
        return super().match(pattern, value, variables, bindings)

    def options(self, ih, induction):
        premises = set(free_names([a["formula"] for a in self.target["assumptions"]]))
        protected = protected_names(ih)
        eligible, excluded = [], []
        originals = {v["name"] for v in self.target["variables"]}
        for name in dict.fromkeys(free_names(ih)):
            if name not in originals or name == induction:
                continue
            reason = (
                "original_premise_dependency"
                if name in premises
                else "parameter_under_lexical_binder"
                if name in protected
                else None
            )
            if reason:
                excluded.append({"variable": name, "reason": reason})
            else:
                eligible.append(name)
        options = [eligible, *[[n] for n in eligible], []]
        unique = []
        for option in options:
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
        found = [("直接自反", {"method": "rfl"}, "fixed")]
        try:
            ranking = self.rank()
            rules = self.rules()
            for variable in ranking:
                name = variable["variable"]
                index = "$induction_index"
                self.scope[index] = "Nat"
                base_goal = self.replace(self.target["goal"], {name: {"nat": 0}}, self.scope)
                step_goal = self.replace(
                    self.target["goal"], {name: {"$succ": {"var": index}}}, self.scope
                )
                ih = self.norm(
                    self.replace(self.target["goal"], {name: {"var": index}}, self.scope)
                )
                base = self.solve(base_goal, rules)
                for generalize in self.options(ih, name):
                    quantified = {n: self.scope[n] for n in generalize}
                    extra = [("@ih", quantified, *ih["eq"])] if set(ih) == {"eq"} else []
                    step = self.solve(step_goal, self.rules(extra))
                    proof = {
                        "method": "nat_induction",
                        "variable": name,
                        "generalize": generalize,
                        "base": base or {"method": "grind"},
                        "step": step or {"method": "grind"},
                    }
                    label = (
                        "泛化归纳 · " + name + " / " + ", ".join(generalize)
                        if generalize
                        else "自然数归纳 · " + name
                    )
                    origin = "symbolic" if base and step else "heuristic"
                    origin += "-generalized-induction" if generalize else "-induction"
                    found.append((label, proof, origin))
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
