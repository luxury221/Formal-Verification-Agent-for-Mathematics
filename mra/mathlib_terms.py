"""Versioned, data-only natural divisibility statements and explicit library terms."""

from __future__ import annotations

import re

from .formalizer import exact_object

MODE = "mathlib-nat-divisibility-v1"
IMPORT = "Mathlib.Data.Nat.Basic"
# Argument order is explicit, including Lean's normally implicit Nat parameters.
REFERENCES = {
    "Nat.dvd_refl": (1, 0, "∀ a : Nat, a ∣ a", "自反性", "lean"),
    "Nat.dvd_trans": (3, 2, "∀ {a b c : Nat}, a ∣ b → b ∣ c → a ∣ c", "整除传递", "lean"),
    "Nat.dvd_add": (3, 2, "∀ {a b c : Nat}, a ∣ b → a ∣ c → a ∣ b + c", "整除加法", "lean"),
    "Nat.dvd_antisymm": (2, 2, "∀ {m n : Nat}, m ∣ n → n ∣ m → m = n", "双向整除", "lean"),
    "Nat.dvd_mul_right": (2, 0, "∀ a b : Nat, a ∣ a * b", "整除右倍数", "lean"),
    "Nat.dvd_mul_left": (2, 0, "∀ a b : Nat, a ∣ b * a", "整除左倍数", "lean"),
    "Nat.dvd_antisymm_iff": (
        2,
        0,
        "∀ {m n : Nat}, m = n ↔ m ∣ n ∧ n ∣ m",
        "相等与双向整除",
        "mathlib",
    ),
    "Nat.dvd_sub_self_left": (
        2,
        0,
        "∀ {n m : Nat}, n ∣ n - m ↔ m = 0 ∨ n ≤ m",
        "截断减法左整除",
        "mathlib",
    ),
    "Nat.dvd_sub_self_right": (
        2,
        0,
        "∀ {n m : Nat}, n ∣ m - n ↔ n ∣ m ∨ m ≤ n",
        "截断减法右整除",
        "mathlib",
    ),
}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", value):
        raise ValueError("Names require 1–20 lowercase ASCII letters, digits or underscores")
    return value


def text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must contain 1–{limit} characters")
    return value.strip()


def compile_request(raw):
    """Validate bounded trees and compile host-owned Lean, never candidate source."""
    exact_object(
        raw,
        {
            "schema",
            "title",
            "original",
            "variables",
            "assumptions",
            "goal",
            "proof",
            "timeout_seconds",
        },
        "Mathlib request",
    )
    if raw["schema"] != MODE:
        raise ValueError("Unsupported Mathlib schema")
    title = text(raw["title"], 160, "Title")
    original = text(raw["original"], 12000, "Original statement")
    variables = raw["variables"]
    if not isinstance(variables, list) or len(variables) > 6:
        raise ValueError("At most six explicitly typed Nat variables")
    variables = [identifier(name) for name in variables]
    if len(set(variables)) != len(variables):
        raise ValueError("Duplicate variable")
    # Host names prevent keywords, shadowing and candidate Lean syntax injection.
    vmap = {name: f"mra_v{i}" for i, name in enumerate(variables)}
    assumptions = raw["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 6:
        raise ValueError("At most six complete assumptions")
    hmap = {}
    nodes = 0
    used = set()

    def tick(depth):
        nonlocal nodes
        nodes += 1
        if depth > 14 or nodes > 240:
            raise ValueError("Mathlib term exceeds depth or node budget")

    def expression(value, depth=0):
        tick(depth)
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("Expression must have exactly one constructor")
        key, arg = next(iter(value.items()))
        if key == "var" and isinstance(arg, str) and arg in vmap:
            return vmap[arg]
        if key == "nat" and type(arg) is int and 0 <= arg <= 1000000:
            return f"({arg} : Nat)"
        if key in ("add", "mul", "sub") and isinstance(arg, list) and len(arg) == 2:
            op = {"add": "+", "mul": "*", "sub": "-"}[key]
            return f"({expression(arg[0], depth + 1)} {op} {expression(arg[1], depth + 1)})"
        raise ValueError("Unknown expression or undeclared variable; only Nat is supported")

    def formula(value, depth=0):
        tick(depth)
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("Formula must have exactly one constructor")
        key, args = next(iter(value.items()))
        if not isinstance(args, list) or len(args) != 2:
            raise ValueError("Formula requires two operands")
        if key in ("dvd", "eq", "le"):
            op = {"dvd": "∣", "eq": "=", "le": "≤"}[key]
            return f"({expression(args[0], depth + 1)} {op} {expression(args[1], depth + 1)})"
        if key in ("and", "or", "iff"):
            op = {"and": "∧", "or": "∨", "iff": "↔"}[key]
            return f"({formula(args[0], depth + 1)} {op} {formula(args[1], depth + 1)})"
        raise ValueError("Definition is unavailable; formula was not rewritten")

    rendered_assumptions = []
    for i, item in enumerate(assumptions):
        exact_object(item, {"name", "formula"}, "Assumption")
        name = identifier(item["name"])
        if name in hmap or name in vmap:
            raise ValueError("Duplicate assumption or variable name")
        hmap[name] = f"mra_h{i}"
        rendered_assumptions.append(formula(item["formula"]))

    def proof(value, depth=0):
        tick(depth)
        if not isinstance(value, dict):
            raise ValueError("Proof must be a structured term")
        if set(value) == {"hyp"} and isinstance(value["hyp"], str) and value["hyp"] in hmap:
            return hmap[value["hyp"]]
        if set(value) == {"ref", "terms", "proofs"}:
            ref, terms, proofs = value["ref"], value["terms"], value["proofs"]
            if not isinstance(ref, str) or ref not in REFERENCES:
                raise ValueError("Library reference is not on the versioned allowlist")
            nterms, nproofs, *_ = REFERENCES[ref]
            if (
                not isinstance(terms, list)
                or not isinstance(proofs, list)
                or (len(terms), len(proofs)) != (nterms, nproofs)
            ):
                raise ValueError("Every implicit Nat argument and proof premise must be explicit")
            used.add(ref)
            args = [expression(t, depth + 1) for t in terms] + [proof(p, depth + 1) for p in proofs]
            return f"(@{ref} {' '.join('(' + a + ')' for a in args)})"
        if len(value) == 1:
            op, args = next(iter(value.items()))
            if op in ("pair", "mp", "mpr") and isinstance(args, list) and len(args) == 2:
                constructor = {"pair": "And.intro", "mp": "Iff.mp", "mpr": "Iff.mpr"}[op]
                return (
                    f"({constructor} ({proof(args[0], depth + 1)}) ({proof(args[1], depth + 1)}))"
                )
        raise ValueError("Unknown proof term, hypothesis, or arbitrary source")

    goal = formula(raw["goal"])
    body = proof(raw["proof"])
    statement = " → ".join([*rendered_assumptions, goal])
    if variables:
        statement = f"∀ {' '.join(vmap.values())} : Nat, {statement}"
    binders = [f"({v} : Nat)" for v in vmap.values()] + [
        f"({h} : {f})" for h, f in zip(hmap.values(), rendered_assumptions)
    ]
    proof_source = f"fun {' '.join(binders)} => {body}" if binders else body
    timeout = raw["timeout_seconds"]
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        raise ValueError("One candidate and one replay share 1–30 worker seconds")
    return {
        "request": {**raw, "title": title, "original": original, "timeout_seconds": float(timeout)},
        "formal_statement": statement,
        "proof_source": proof_source,
        "references": sorted(used),
        "variable_mapping": vmap,
        "assumptions": rendered_assumptions,
        "node_count": nodes,
    }


def examples():
    def v(name):
        return {"var": name}

    def atom(op, a, b):
        return {op: [a, b]}

    def ref(name, terms, proofs=None):
        return {"ref": name, "terms": terms, "proofs": proofs or []}

    a, b, c = map(v, ("a", "b", "c"))
    base = {"schema": MODE, "timeout_seconds": 30}
    chain = {
        **base,
        "title": "整除链与加法的组合",
        "original": "设 a、b、c 为自然数。若 a 整除 b 且 b 整除 c，则 a 整除 b+c。",
        "variables": ["a", "b", "c"],
        "assumptions": [
            {"name": "hab", "formula": atom("dvd", a, b)},
            {"name": "hbc", "formula": atom("dvd", b, c)},
        ],
        "goal": atom("dvd", a, {"add": [b, c]}),
        "proof": ref(
            "Nat.dvd_add",
            [a, b, c],
            [{"hyp": "hab"}, ref("Nat.dvd_trans", [a, b, c], [{"hyp": "hab"}, {"hyp": "hbc"}])],
        ),
    }
    antisymm = {
        **base,
        "title": "相等与双向整除",
        "original": "任意自然数 a、b，相等当且仅当 a 整除 b 且 b 整除 a。",
        "variables": ["a", "b"],
        "assumptions": [],
        "goal": atom("iff", atom("eq", a, b), atom("and", atom("dvd", a, b), atom("dvd", b, a))),
        "proof": ref("Nat.dvd_antisymm_iff", [a, b]),
    }
    subtraction = {
        **base,
        "title": "整除与截断减法边界",
        "original": "任意自然数 a、b，a 整除 a-b，当且仅当 b=0 或 a≤b；减法在自然数上截断。",
        "variables": ["a", "b"],
        "assumptions": [],
        "goal": atom(
            "iff",
            atom("dvd", a, {"sub": [a, b]}),
            atom("or", atom("eq", b, {"nat": 0}), atom("le", a, b)),
        ),
        "proof": ref("Nat.dvd_sub_self_left", [a, b]),
    }
    failed = {
        **chain,
        "title": "遗漏前提的失败对照",
        "original": "任意自然数 a、b、c，若 a 整除 b，就有 a 整除 b+c。此陈述缺少条件，提交的证明应被拒绝。",
        "assumptions": chain["assumptions"][:1],
        "proof": ref("Nat.dvd_add", [a, b, c], [{"hyp": "hab"}, {"hyp": "hab"}]),
    }
    return [chain, antisymm, subtraction, failed]
