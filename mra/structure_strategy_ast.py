"""Untrusted v2 induction ordering from active recursive indices, with v1 fuel."""

from .domain import canonical
from .structure_strategy_terms import Planner, SearchLimit


def free_names(value, bound=()):
    """Free term variables in AST order; declaration metadata is never a use."""
    if isinstance(value, list):
        return [n for item in value for n in free_names(item, bound)]
    if not isinstance(value, dict):
        return []
    if set(value) == {"var"}:
        return [] if value["var"] in bound else [value["var"]]
    if set(value) == {"nat_fold"}:
        f = value["nat_fold"]
        step = f["step"]
        return (
            free_names(f["index"], bound)
            + free_names(f["zero"], bound)
            + free_names(step["body"], (*bound, step["index"], step["value"]))
        )
    if len(value) == 1 and next(iter(value)) in ("forall", "exists"):
        body = next(iter(value.values()))
        return free_names(body["body"], (*bound, *(b["name"] for b in body["binders"])))
    return [n for item in value.values() for n in free_names(item, bound)]


def active_indices(value, bound=()):
    """Do not activate folds inside a suspended step body or unused definition."""
    if isinstance(value, list):
        return [item for v in value for item in active_indices(v, bound)]
    if not isinstance(value, dict):
        return []
    if set(value) == {"nat_fold"}:
        f = value["nat_fold"]
        return (
            [(f["index"], bound)]
            + active_indices(f["index"], bound)
            + active_indices(f["zero"], bound)
        )
    if len(value) == 1 and next(iter(value)) in ("forall", "exists"):
        body = next(iter(value.values()))
        return active_indices(body["body"], (*bound, *(b["name"] for b in body["binders"])))
    return [item for v in value.values() for item in active_indices(v, bound)]


class RankedPlanner(Planner):
    def __init__(self, target, budget):
        super().__init__(target, budget)
        self.induction_ranking = []
        self.ranking_complete = False

    def rank(self):
        goal = self.norm(self.target["goal"])
        indices = active_indices(goal)
        order = list(dict.fromkeys(free_names(goal)))
        ranked = []
        for position, name in enumerate(order):
            if self.scope.get(name) != "Nat":
                continue
            relevant = [index for index, bound in indices if name in free_names(index, bound)]
            if not relevant:
                continue
            zeros = successors = 0
            for index in relevant:
                self.tick()
                zero = self.norm(self.replace(index, {name: {"nat": 0}}))
                successor = self.norm(
                    self.replace(index, {name: {"$succ": {"var": "$ranking_index"}}}),
                    {**self.scope, "$ranking_index": "Nat"},
                )
                zeros += zero == {"nat": 0}
                successors += set(successor) == {"$succ"} or (
                    set(successor) == {"nat"} and 0 < successor["nat"] <= 16
                )
            ranked.append(
                {
                    "variable": name,
                    "active_index_occurrences": len(relevant),
                    "zero_exposures": zeros,
                    "successor_exposures": successors,
                    "goal_occurrence_order": position,
                }
            )
        ranked.sort(
            key=lambda r: (
                -r["successor_exposures"],
                -r["zero_exposures"],
                r["goal_occurrence_order"],
            )
        )
        self.induction_ranking = ranked
        self.ranking_complete = True
        return ranked

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
