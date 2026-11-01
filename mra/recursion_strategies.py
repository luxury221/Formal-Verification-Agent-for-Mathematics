"""Untrusted bounded Nat arithmetic and commuting-fold local lemma proposals."""

from copy import deepcopy
import time

from .domain import canonical, digest
from .formalizer import exact_object
from .integrity import file_hash
from .recursion_programs import MODE, compile_program, manifest as program_manifest
from .structure_strategy_ast import free_names
from .structure_strategy_scoped import ScopedPlanner
from .structure_strategy_terms import WALL_SECONDS, MAX_NORMALIZATION_NODES, MAX_INTERMEDIATE_NODES
from .structured_terms import FIELDS, compile_request
from .typed_terms import bounded_data

SCHEMA = "structure-recursion-proposals-v1"
DEFAULT_BUDGET = {"max_candidates": 2, "max_expansions": 800, "max_rewrite_steps": 4}
FILES = (
    "mra/recursion_strategies.py",
    "mra/structure_strategy_scoped.py",
    "mra/structure_composition_terms.py",
)


class ArithmeticPlanner(ScopedPlanner):
    def __init__(self, target, budget, lemmas=()):
        super().__init__(target, budget)
        self.lemma_rules = lemmas

    def norm(self, value, scope=None, depth=0):
        scope = self.scope if scope is None else scope
        result = super().norm(value, scope, depth)
        if isinstance(result, dict) and set(result) == {"add"}:
            left, right = result["add"]
            if self.typ(left, scope) == self.typ(right, scope) == "Nat":
                if left == {"nat": 0}:
                    return right
                if set(left) == {"$succ"}:
                    return {"$succ": self.norm({"add": [left["$succ"], right]}, scope, depth + 1)}
        return result

    def rules(self, extra=()):
        result = super().rules(extra)
        for name, variables, goal in self.lemma_rules:
            left, right = (self.norm(v, {**self.scope, **variables}) for v in goal["eq"])
            result.append(("@lemma." + name, variables, left, right))
        return result


def templates(target):
    """Read only explicit one-variable commuting laws and primitive unary folds."""
    pairs = set()

    def unary(value):
        if isinstance(value, dict) and set(value) == {"call"} and len(value["call"]["args"]) == 1:
            return value["call"]["name"], value["call"]["args"][0]
        return None

    for a in target["assumptions"]:
        formula = a["formula"]
        if set(formula) != {"forall"} or len(formula["forall"]["binders"]) != 1:
            continue
        b = formula["forall"]
        goal = b["body"]
        if set(goal) != {"eq"}:
            continue
        left, right = (unary(v) for v in goal["eq"])
        if not left or not right:
            continue
        li, ri = unary(left[1]), unary(right[1])
        if (
            li
            and ri
            and (left[0], li[0]) == (ri[0], right[0])
            and li[1] == ri[1] == {"var": b["binders"][0]["name"]}
        ):
            pairs.update(((left[0], li[0]), (li[0], left[0])))
    if set(free_names([a["formula"] for a in target["assumptions"]])):
        return []
    order = list(dict.fromkeys(free_names(target["goal"])))
    types = {v["name"]: v["type"] for v in target["variables"]}
    result = []
    for definition in target["definitions"]:
        body = definition["body"]
        params = definition["parameters"]
        if set(body) != {"nat_fold"} or len(params) != 2:
            continue
        f = body["nat_fold"]
        step = f["step"]
        operation = unary(step["body"])
        if not operation or operation[1] != {"var": step["value"]}:
            continue
        index = next(
            (p["name"] for p in params if p["type"] == "Nat" and f["index"] == {"var": p["name"]}),
            None,
        )
        state = next(
            (
                p["name"]
                for p in params
                if p["type"] == definition["result_type"] and f["zero"] == {"var": p["name"]}
            ),
            None,
        )
        if index is None or state is None or index == state:
            continue
        n = next((n for n in order if types.get(n) == "Nat"), None)
        x = next(
            (
                state_name
                for state_name in order
                if types.get(state_name) == definition["result_type"] and state_name != n
            ),
            None,
        )
        if n is None or x is None:
            continue
        for op in target["operations"]:
            if (
                op["parameters"] != [definition["result_type"]]
                or op["result_type"] != definition["result_type"]
                or (op["name"], operation[0]) not in pairs
            ):
                continue
            arguments = {index: {"var": n}, state: {"var": x}}

            def invoke(args):
                return {
                    "call": {"name": definition["name"], "args": [args[p["name"]] for p in params]}
                }

            base = invoke(arguments)
            transformed = {"call": {"name": op["name"], "args": [{"var": x}]}}
            goal = {
                "eq": [
                    {"call": {"name": op["name"], "args": [base]}},
                    invoke({**arguments, state: transformed}),
                ]
            }
            if all(e["goal"] != goal for e in result):
                result.append(
                    {
                        "name": "transport_" + str(len(result)),
                        "goal": goal,
                        "template": "explicit-commuting-operation-through-primitive-fold",
                    }
                )
    return result[:2]


def propose(root, raw):
    bounded_data(raw)
    exact_object(raw, {"schema", "target", "budget"}, "Recursion proposal")
    if raw["schema"] != SCHEMA:
        raise ValueError("Use structure-recursion-proposals-v1")
    exact_object(raw["target"], FIELDS - {"proof"}, "Complete target without reference proof")
    exact_object(raw["budget"], set(DEFAULT_BUDGET), "Explicit synthesis budget")
    for name, maximum in (
        ("max_candidates", 8),
        ("max_expansions", 2000),
        ("max_rewrite_steps", 6),
    ):
        if type(raw["budget"][name]) is not int or not 1 <= raw["budget"][name] <= maximum:
            raise ValueError(name + " requires a bounded positive integer")
    target = deepcopy(raw["target"])
    compiled = compile_request({**target, "proof": {"method": "rfl"}})
    started = time.monotonic()
    expansions = nodes = 0
    analyses = []
    candidates = []

    def synthesize(t, lemmas=()):
        nonlocal expansions, nodes
        limit = min(
            raw["budget"]["max_expansions"],
            expansions + max(16, raw["budget"]["max_expansions"] // 3),
        )
        engine = ArithmeticPlanner(
            t, {**raw["budget"], "max_candidates": 8, "max_expansions": limit}, lemmas
        )
        engine.started = started
        engine.expansions = expansions
        engine.normalization_nodes = nodes
        programs = engine.programs()
        expansions = engine.expansions
        nodes = engine.normalization_nodes
        analyses.append(
            {
                "target_goal": deepcopy(t["goal"]),
                "lemma_rules": [rule[0] for rule in lemmas],
                "stop_reason": engine.stop_reason,
                "expansions_total": expansions,
                "normalization_nodes_total": nodes,
                "selection": engine.selection_analysis,
            }
        )
        return programs

    def add(proof, lemmas, origin):
        request = {
            "schema": MODE,
            "target": deepcopy(target),
            "program": {"lemmas": deepcopy(lemmas), "proof": proof},
            "timeout_seconds": target["timeout_seconds"],
        }
        value = compile_program(request)
        if value["formal_statement"] != compiled["formal_statement"]:
            raise ValueError("Proposal changed the full original target")
        candidates.append(
            {
                "candidate_id": digest(request),
                "request": request,
                "proof_source": value["proof_source"],
                "origin": origin,
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
        )

    basic = synthesize(target)
    for _, proof, origin in basic:
        add(proof, [], origin)
    proposed = []
    for template in templates(target):
        if (
            time.monotonic() - started >= WALL_SECONDS
            or expansions >= raw["budget"]["max_expansions"]
        ):
            break
        helper = {
            **target,
            "variables": [
                v for v in target["variables"] if v["name"] in set(free_names(template["goal"]))
            ],
            "goal": template["goal"],
        }
        programs = synthesize(helper)
        closed = next((p for p in programs if p[2].startswith("symbolic")), None)
        proposed.append({**template, "symbolic_program_found": closed is not None})
        if closed is None:
            continue
        lemma = {"name": template["name"], "goal": template["goal"], "proof": closed[1]}
        rule = (lemma["name"], {v["name"]: v["type"] for v in helper["variables"]}, lemma["goal"])
        for _, proof, origin in synthesize(target, [rule]):
            if "@lemma." in canonical(proof).decode():
                add(proof, [lemma], origin + "-with-local-lemma")
    unique = {}
    for c in candidates:
        unique.setdefault(c["candidate_id"], c)
    ordered = sorted(unique.values(), key=lambda c: not c["origin"].startswith("symbolic"))
    value = {
        "schema": SCHEMA,
        "target": target,
        "formal_statement": compiled["formal_statement"],
        "budget": {
            **raw["budget"],
            "synthesis_wall_seconds": WALL_SECONDS,
            "normalization_nodes": MAX_NORMALIZATION_NODES,
            "intermediate_data_nodes": MAX_INTERMEDIATE_NODES,
        },
        "candidates": ordered[: raw["budget"]["max_candidates"]],
        "lemma_templates": proposed,
        "synthesis_analysis": analyses,
        "source_manifest": {
            "provider": program_manifest(root),
            "files": {n: file_hash(root / n) for n in FILES},
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "lean_calls": 0,
        "model_calls": 0,
        "reference_proof_used": False,
        "requires_preview_and_named_confirmation": True,
        "limitations": [
            "Bounded authored template search, not discovery, completeness, originality or a proof.",
            "Only closed original laws and two-parameter primitive unary folds can seed automatic local lemma proposals.",
            "All local obligations and the unchanged final target must pass the genuine checker in one shared worker budget.",
            "Synthesis fuel and wall checkpoints are shared across all helper/main searches; compilation and integrity preparation cost separate.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return {
        **value,
        "stats": {
            "expansions": expansions,
            "normalization_nodes": nodes,
            "preparation_wall_seconds": round(time.monotonic() - started, 6),
        },
    }
