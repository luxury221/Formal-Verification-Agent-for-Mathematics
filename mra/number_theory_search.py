"""Untrusted, bounded term synthesis. Only the existing checker can certify proofs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
from itertools import product
from pathlib import Path
import time

from .domain import canonical, digest
from .formalizer import exact_object
from .number_theory_terms import REFERENCES, compile_request

SCHEMA = "mra-number-theory-search-v1"
ENGINE = "bounded-backward-nat-v1"
MAX_TERMS = 32
WALL_SECONDS = 0.75
TARGET_FIELDS = {
    "schema",
    "title",
    "original",
    "variables",
    "assumptions",
    "goal",
    "timeout_seconds",
}


def tree(value):
    """Immutable representation; input trees have already passed the compiler."""
    key, args = next(iter(value.items()))
    return (key, args) if key in ("var", "nat") else (key, *(tree(a) for a in args))


def document(value):
    key, *args = value
    return {key: args[0]} if key in ("var", "nat") else {key: [document(a) for a in args]}


def atom(op, left, right):
    return op, left, right


def substitute(pattern, bindings):
    if isinstance(pattern, str):
        return bindings[pattern]
    return tuple(
        substitute(p, bindings) if isinstance(p, tuple) or p in bindings else p for p in pattern
    )


def match(pattern, goal, bindings):
    if isinstance(pattern, str):
        if pattern in bindings:
            return bindings[pattern] == goal
        bindings[pattern] = goal
        return True
    return (
        isinstance(goal, tuple)
        and len(pattern) == len(goal)
        and all(
            match(p, g, bindings)
            if isinstance(p, tuple) or (isinstance(p, str) and p.startswith("$"))
            else p == g
            for p, g in zip(pattern, goal)
        )
    )


@dataclass(frozen=True)
class Rule:
    ref: str
    parameters: tuple
    premises: tuple
    conclusion: tuple


def rules():
    a, b, c = "$a", "$b", "$c"
    z, o = ("nat", 0), ("nat", 1)
    f = atom
    dvd, eq = lambda x, y: f("dvd", x, y), lambda x, y: f("eq", x, y)
    gcd, cp = lambda x, y: f("gcd", x, y), lambda x, y: f("coprime", x, y)
    # Specific and premise-free rules precede generic transitivity. Iterative
    # deepening bounds proof height, not the complexity of the input formula.
    result = [
        Rule("Nat.dvd_refl", (a,), (), dvd(a, a)),
        Rule("Nat.dvd_mul_right", (a, b), (), dvd(a, f("mul", a, b))),
        Rule("Nat.dvd_mul_left", (a, b), (), dvd(a, f("mul", b, a))),
        Rule("Nat.gcd_self", (a,), (), eq(gcd(a, a), a)),
        Rule("Nat.gcd_zero_left", (a,), (), eq(gcd(z, a), a)),
        Rule("Nat.gcd_zero_right", (a,), (), eq(gcd(a, z), a)),
        Rule("Nat.gcd_dvd_left", (a, b), (), dvd(gcd(a, b), a)),
        Rule("Nat.gcd_dvd_right", (a, b), (), dvd(gcd(a, b), b)),
        Rule("Nat.gcd_comm", (a, b), (), eq(gcd(a, b), gcd(b, a))),
        Rule("Nat.coprime_one_left", (a,), (), cp(o, a)),
        Rule("Nat.coprime_one_right", (a,), (), cp(a, o)),
        Rule("Nat.coprime_iff_gcd_eq_one", (a, b), (), f("iff", cp(a, b), eq(gcd(a, b), o))),
        Rule("Nat.coprime_zero_left", (a,), (), f("iff", cp(z, a), eq(a, o))),
        Rule("Nat.coprime_zero_right", (a,), (), f("iff", cp(a, z), eq(a, o))),
        Rule(
            "Nat.dvd_antisymm_iff", (a, b), (), f("iff", eq(a, b), f("and", dvd(a, b), dvd(b, a)))
        ),
        Rule(
            "Nat.dvd_sub_self_left",
            (a, b),
            (),
            f("iff", dvd(a, f("sub", a, b)), f("or", eq(b, z), f("le", a, b))),
        ),
        Rule(
            "Nat.dvd_sub_self_right",
            (a, b),
            (),
            f("iff", dvd(a, f("sub", b, a)), f("or", dvd(a, b), f("le", b, a))),
        ),
        Rule("Nat.dvd_gcd", (a, b, c), (dvd(a, b), dvd(a, c)), dvd(a, gcd(b, c))),
        Rule("Nat.dvd_add", (a, b, c), (dvd(a, b), dvd(a, c)), dvd(a, f("add", b, c))),
        Rule("Nat.Coprime.gcd_eq_one", (a, b), (cp(a, b),), eq(gcd(a, b), o)),
        Rule("Nat.dvd_antisymm", (a, b), (dvd(a, b), dvd(b, a)), eq(a, b)),
        Rule("Nat.dvd_trans", (a, b, c), (dvd(a, b), dvd(b, c)), dvd(a, c)),
    ]
    if set(REFERENCES) != {r.ref for r in result} or any(
        REFERENCES[r.ref][:2] != (len(r.parameters), len(r.premises)) for r in result
    ):
        raise ValueError("Search rules disagree with the versioned proof language")
    return tuple(result)


def reference(rule, bindings, proofs=()):
    return {
        "ref": rule.ref,
        "terms": [document(bindings[p]) for p in rule.parameters],
        "proofs": list(proofs),
    }


def term_pool(target):
    found = {}

    def visit(value):
        key, *args = value
        if key in ("var", "nat", "add", "mul", "sub", "gcd"):
            found.setdefault(value, None)
        if key not in ("var", "nat"):
            for child in args:
                visit(child)

    visit(tree(target["goal"]))
    for item in target["assumptions"]:
        visit(tree(item["formula"]))
    for name in target["variables"]:
        found.setdefault(("var", name), None)
    for n in (0, 1):
        found.setdefault(("nat", n), None)
    return tuple(found)[:MAX_TERMS], len(found)


class StopSearch(Exception):
    pass


class Search:
    def __init__(self, target, budget):
        self.target, self.budget = target, budget
        self.rules = rules()
        self.pool, self.term_count = term_pool(target)
        self.hypotheses = [(tree(h["formula"]), {"hyp": h["name"]}) for h in target["assumptions"]]
        self.expansions = self.depth_pruned = self.cycles = 0
        self.iteration = 0
        self.unclosed = {}
        self.reason = "bounded_space_exhausted"
        self.started = time.monotonic()

    def tick(self):
        if self.expansions >= self.budget["max_expansions"]:
            self.reason = "expansion_limit"
            raise StopSearch
        if time.monotonic() - self.started >= WALL_SECONDS:
            self.reason = "wall_time_limit"
            raise StopSearch
        self.expansions += 1

    def steps(self, goal):
        for rule in self.rules:
            modes = [(None, rule.conclusion, rule.premises)]
            if rule.conclusion[0] == "iff" and not rule.premises:
                _, left, right = rule.conclusion
                modes += [("mp", right, (left,)), ("mpr", left, (right,))]
            for direction, conclusion, premises in modes:
                self.tick()
                bindings = {}
                if not match(conclusion, goal, bindings):
                    continue
                missing = [p for p in rule.parameters if p not in bindings]
                for values in product(self.pool, repeat=len(missing)):
                    self.tick()
                    complete = {**bindings, **dict(zip(missing, values))}
                    yield (
                        rule,
                        direction,
                        complete,
                        tuple(substitute(p, complete) for p in premises),
                    )

    def solve(self, goal, depth, active):
        self.tick()
        if depth < 1:
            self.depth_pruned += 1
            return None
        for formula, proof in self.hypotheses:
            if formula == goal:
                return proof
        if goal in active:
            self.cycles += 1
            return None
        active = active | {goal}
        if goal[0] == "and":
            left = self.solve(goal[1], depth - 1, active)
            right = self.solve(goal[2], depth - 1, active) if left else None
            if left and right:
                return {"pair": [left, right]}
        # Exact Iff hypotheses can be applied; no projection or Or constructors
        # are invented beyond the frozen proof language.
        for formula, proof in self.hypotheses:
            if formula[0] != "iff":
                continue
            for mode, source, destination in (
                ("mp", formula[1], formula[2]),
                ("mpr", formula[2], formula[1]),
            ):
                if destination == goal:
                    premise = self.solve(source, depth - 1, active)
                    if premise:
                        return {mode: [proof, premise]}
        for rule, direction, bindings, premises in self.steps(goal):
            if direction and depth < 2:
                self.depth_pruned += 1
                continue
            proofs = []
            for premise in premises:
                proof = self.solve(premise, depth - 1, active)
                if proof is None:
                    break
                proofs.append(proof)
            else:
                if direction:
                    return {direction: [reference(rule, bindings), proofs[0]]}
                return reference(rule, bindings, proofs)
        if len(self.unclosed) < 12:
            self.unclosed.setdefault(goal, None)
        return None

    def run(self):
        proof = None
        try:
            for self.iteration in range(1, self.budget["max_depth"] + 1):
                proof = self.solve(tree(self.target["goal"]), self.iteration, frozenset())
                if proof:
                    self.reason = "candidate_found"
                    break
            if not proof and self.depth_pruned:
                self.reason = "depth_limit"
        except StopSearch:
            pass
        return proof


def search(raw, root: Path):
    """Pure proposal endpoint: no store, subprocess, model, queue, or checker calls."""
    exact_object(raw, {"schema", "target", "budget"}, "Search request")
    if raw["schema"] != SCHEMA:
        raise ValueError("Unsupported search schema")
    exact_object(raw["target"], TARGET_FIELDS, "Search target without proof")
    exact_object(raw["budget"], {"max_depth", "max_expansions"}, "Search budget")
    for key, maximum in (("max_depth", 8), ("max_expansions", 6000)):
        if type(raw["budget"][key]) is not int or not 1 <= raw["budget"][key] <= maximum:
            raise ValueError(f"{key} requires an integer from 1 to {maximum}")
    # Frozen compiler supplies the sole input-language validation. The placeholder
    # costs two nodes; it is never sent to Lean or treated as a proof of goal.
    placeholder = {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}
    compiled = compile_request({**raw["target"], "proof": placeholder})
    target = deepcopy({k: v for k, v in compiled["request"].items() if k != "proof"})
    provenance = {}
    for name in (
        "mra/number_theory_search.py",
        "mra/number_theory_terms.py",
        "mra/number_theory_lab.py",
        ".runtime/runtime-snapshot.json",
    ):
        provenance[name] = hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()
    engine = Search(target, dict(raw["budget"]))
    proof = engine.run()
    candidate, references = None, []
    if proof:
        try:
            final = compile_request({**target, "proof": proof})
        except ValueError:
            engine.reason = "proof_language_limit"
        else:
            candidate, references = final["request"], final["references"]
    result = {
        "schema": SCHEMA,
        "engine": ENGINE,
        "status": "CANDIDATE_FOUND" if candidate else "NO_CANDIDATE",
        "stop_reason": engine.reason,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "target": target,
        "formal_statement": compiled["formal_statement"],
        "candidate": candidate,
        "references": references,
        "budget": {**raw["budget"], "max_terms": MAX_TERMS, "synthesis_wall_seconds": WALL_SECONDS},
        "stats": {
            "expansions": engine.expansions,
            "last_depth": engine.iteration,
            "depth_pruned": engine.depth_pruned,
            "cycles_pruned": engine.cycles,
            "available_terms": engine.term_count,
            "used_terms": len(engine.pool),
            "term_pool_truncated": engine.term_count > MAX_TERMS,
            "elapsed_seconds": round(time.monotonic() - engine.started, 6),
        },
        "unclosed_search_goals": [document(g) for g in engine.unclosed] if not candidate else [],
        "provenance": {
            "source_sha256": provenance,
            "allowlist_sha256": digest(REFERENCES),
            "rules_sha256": digest([asdict(r) for r in engine.rules]),
            "input_sha256": digest(raw),
            "candidate_sha256": digest(candidate) if candidate else None,
        },
        "requires_preview_and_named_confirmation": True,
        "lean_calls": 0,
        "model_calls": 0,
        "limitations": [
            "Only the 22 allowed references, exact assumptions, pair and Iff application; no general tactics.",
            "Finite input subterms plus 0/1; no unrestricted term generation or arithmetic normalization.",
            "Search is incomplete. Unclosed goals are diagnostics, not necessary hypotheses or counterexamples.",
            "Compiler limits still apply; target prevalidation reserves two placeholder nodes.",
            "The wall limit is cooperative synthesis time, excluding input validation and source hashing.",
            "Adopted candidates require complete review, named alignment, real Lean and independent replay.",
        ],
    }
    result["record_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    return result
