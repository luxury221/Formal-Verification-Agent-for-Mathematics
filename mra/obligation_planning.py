"""Untrusted bounded plans of checked equalities through shared term contexts."""

from copy import deepcopy
import time

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .recursion_programs import MODE, compile_program, manifest as program_manifest
from .recursion_strategies import ArithmeticPlanner, DEFAULT_BUDGET, FILES as BASE_FILES, templates
from .structure_strategy_ast import free_names
from .structure_strategy_terms import WALL_SECONDS, MAX_NORMALIZATION_NODES, MAX_INTERMEDIATE_NODES
from .structured_terms import FIELDS, compile_request
from .typed_terms import bounded_data

SCHEMA = "structure-recursion-proposals-v2"
FILES = ("mra/obligation_planning.py", *BASE_FILES)
MAX_CONTEXT_DEPTH = 8


def inner_equalities(goal):
    """Sufficient stronger obligations only; never infer injectivity or enter binders."""
    result = []
    while isinstance(goal, dict) and set(goal) == {"eq"} and len(result) < MAX_CONTEXT_DEPTH:
        left, right = goal["eq"]
        if not isinstance(left, dict) or not isinstance(right, dict) or left.keys() != right.keys():
            break
        op = next(iter(left))
        if op == "call":
            a, b = left[op], right[op]
            if a["name"] != b["name"]:
                break
            xs, ys = a["args"], b["args"]
        elif op in ("add", "sub", "mul", "div"):
            xs, ys = left[op], right[op]
        elif op == "neg":
            xs, ys = [left[op]], [right[op]]
        elif op == "cast" and left[op]["to"] == right[op]["to"]:
            xs, ys = [left[op]["value"]], [right[op]["value"]]
        else:
            break
        if len(xs) != len(ys):
            break
        differences = [(a, b) for a, b in zip(xs, ys) if a != b]
        if len(differences) != 1:
            break
        a, b = differences[0]
        goal = {"eq": [deepcopy(a), deepcopy(b)]}
        result.append(goal)
    return result


class ObligationPlanner:
    def __init__(self, target, budget):
        self.target, self.budget = target, budget
        self.started = time.monotonic()
        self.expansions = self.nodes = 0
        self.analyses = []

    def available(self):
        return (
            self.expansions < self.budget["max_expansions"]
            and self.nodes < MAX_NORMALIZATION_NODES
            and time.monotonic() - self.started < WALL_SECONDS
        )

    def target_for(self, goal):
        used = set(free_names(goal))
        return {
            **self.target,
            "goal": deepcopy(goal),
            "variables": [v for v in self.target["variables"] if v["name"] in used],
        }

    def search(self, target, helpers=()):
        if not self.available():
            return []
        self.expansions += 1  # Planning a search also consumes the shared fuel.
        rules = [
            (
                h["name"],
                {v["name"]: v["type"] for v in self.target_for(h["goal"])["variables"]},
                h["goal"],
            )
            for h in helpers
        ]
        limit = min(
            self.budget["max_expansions"],
            self.expansions + max(16, self.budget["max_expansions"] // 4),
        )
        engine = ArithmeticPlanner(
            target, {**self.budget, "max_candidates": 8, "max_expansions": limit}, rules
        )
        engine.started, engine.expansions, engine.normalization_nodes = (
            self.started,
            self.expansions,
            self.nodes,
        )
        found = engine.programs()
        self.expansions, self.nodes = engine.expansions, engine.normalization_nodes
        self.analyses.append(
            {
                "goal": deepcopy(target["goal"]),
                "helpers": [h["name"] for h in helpers],
                "expansions_total": self.expansions,
                "normalization_nodes_total": self.nodes,
                "stop_reason": engine.stop_reason,
                "selection": engine.selection_analysis,
            }
        )
        return found

    def solve(self, target, max_helpers):
        found = self.search(target)
        closed = next((p for p in found if p[2].startswith("symbolic")), None)
        if closed:
            return closed[1], []
        if not max_helpers:
            return None
        for template in templates(target):
            if not self.available():
                break
            helper_target = self.target_for(template["goal"])
            proofs = self.search(helper_target)
            closed = next((p for p in proofs if p[2].startswith("symbolic")), None)
            if closed is None:
                continue
            helper = {"name": "aux_0", "goal": template["goal"], "proof": closed[1]}
            found = self.search(target, [helper])
            closed = next((p for p in found if p[2].startswith("symbolic")), None)
            if closed:
                return closed[1], [helper]
        return None


def propose(root, raw):
    bounded_data(raw)
    exact_object(raw, {"schema", "target", "budget"}, "Versioned obligation proposals")
    if raw["schema"] != SCHEMA:
        raise ValueError("Use structure-recursion-proposals-v2")
    exact_object(raw["target"], FIELDS - {"proof"}, "Complete original target")
    exact_object(raw["budget"], set(DEFAULT_BUDGET), "Shared synthesis budget")
    for name, maximum in (
        ("max_candidates", 8),
        ("max_expansions", 2000),
        ("max_rewrite_steps", 6),
    ):
        if type(raw["budget"][name]) is not int or not 1 <= raw["budget"][name] <= maximum:
            raise ValueError(name + " requires a bounded positive integer")
    target = deepcopy(raw["target"])
    compiled = compile_request({**target, "proof": {"method": "rfl"}})
    planner = ObligationPlanner(target, raw["budget"])
    candidates = []
    contexts = []
    closed_laws = not free_names([a["formula"] for a in target["assumptions"]])

    def add(proof, helpers, origin):
        request = {
            "schema": MODE,
            "target": deepcopy(target),
            "program": {"lemmas": deepcopy(helpers), "proof": proof},
            "timeout_seconds": target["timeout_seconds"],
        }
        value = compile_program(request)
        if value["formal_statement"] != compiled["formal_statement"]:
            raise ValueError("An obligation proposal changed the original complete target")
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

    if closed_laws:
        contexts = inner_equalities(target["goal"])
        if contexts:
            core = planner.target_for(contexts[-1])
            solved = planner.solve(core, 1)
            if solved:
                proof, helpers = solved
                name = "aux_" + str(len(helpers))
                helpers = [*helpers, {"name": name, "goal": core["goal"], "proof": proof}]
                add(
                    {
                        "method": "rewrite",
                        "steps": [{"rule": "@lemma." + name, "reverse": False}],
                        "finish": "rfl",
                    },
                    helpers,
                    "symbolic-checked-obligation-context-plan",
                )
        elif planner.available():
            solved = planner.solve(target, 2)
            if solved:
                proof, helpers = solved
                add(proof, helpers, "symbolic-checked-obligation-plan")
    # Complete-goal fallbacks keep dependent original assumptions untouched.
    if planner.available():
        for _, proof, origin in planner.search(target):
            add(proof, [], origin)
    for method in ("grind", "rfl"):
        add({"method": method}, [], "fixed-complete-goal-fallback")
    unique = {}
    for candidate in candidates:
        unique.setdefault(candidate["candidate_id"], candidate)
    value = {
        "schema": SCHEMA,
        "target": target,
        "formal_statement": compiled["formal_statement"],
        "budget": {
            **raw["budget"],
            "synthesis_wall_seconds": WALL_SECONDS,
            "normalization_nodes": MAX_NORMALIZATION_NODES,
            "intermediate_data_nodes": MAX_INTERMEDIATE_NODES,
            "max_context_depth": MAX_CONTEXT_DEPTH,
            "max_local_lemmas": 2,
        },
        "candidates": list(unique.values())[: raw["budget"]["max_candidates"]],
        "context_obligations": contexts,
        "synthesis_analysis": planner.analyses,
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
            "A stronger inner equality is a sufficient obligation, never inferred from equality after a noninjective operation.",
            "Shared term contexts only: no binder-body descent, no logical strengthening and no arbitrary inverse rule.",
            "At most two inline checked lemmas; closed original laws required for helpers; no persistent lemma export.",
            "All searches share fuel, normalization nodes and wall checkpoints; final compilation/preparation is not a hard wall deadline.",
            "Untrusted authored bounded plans; no completeness, novelty, generic research or proof claim.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return {
        **value,
        "stats": {
            "expansions": planner.expansions,
            "normalization_nodes": planner.nodes,
            "preparation_wall_seconds": round(time.monotonic() - planner.started, 6),
        },
    }
