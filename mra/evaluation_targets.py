"""Authored Nat AST mappings for sealed references; no model-generated source is parsed."""

from .formalizer import SourceClaim
from .nat_ideas import NatTarget


def binary(op, left, right):
    return {"op": op, "left": left, "right": right}


def quantified(names, body):
    for name in reversed(names.split()):
        body = {"op": "forall", "name": name, "body": body}
    return body


def reference_formulas():
    a, b, c, n = ({"op": "var", "name": name} for name in ("a", "b", "c", "n"))
    zero, one = ({"op": "num", "value": value} for value in (0, 1))

    def add(x, y):
        return binary("add", x, y)

    def mul(x, y):
        return binary("mul", x, y)

    def eq(x, y):
        return binary("eq", x, y)

    def le(x, y):
        return binary("le", x, y)

    def imp(x, y):
        return binary("imp", x, y)

    restore = eq(add(binary("sub", a, b), b), a)
    return {
        "∀ a b : Nat, a + b = b + a": quantified("a b", eq(add(a, b), add(b, a))),
        "∀ a b : Nat, a + b = a": quantified("a b", eq(add(a, b), a)),
        "∀ a b : Nat, Nat.succ a = Nat.succ b → a = b": quantified(
            "a b", imp(eq(add(a, one), add(b, one)), eq(a, b))
        ),
        "∀ n : Nat, Nat.succ n = n": quantified("n", eq(add(n, one), n)),
        "∀ n : Nat, n ≤ n": quantified("n", le(n, n)),
        "∀ n : Nat, n < n": quantified("n", binary("lt", n, n)),
        "∀ a b : Nat, b ≤ a → a - b + b = a": quantified("a b", imp(le(b, a), restore)),
        "∀ a b : Nat, a - b + b = a": quantified("a b", restore),
        "∀ a b c : Nat, a + b = a + c → b = c": quantified(
            "a b c", imp(eq(add(a, b), add(a, c)), eq(b, c))
        ),
        "∀ a b c : Nat, a * b = a * c → b = c": quantified(
            "a b c", imp(eq(mul(a, b), mul(a, c)), eq(b, c))
        ),
        "∀ a b c : Nat, a * (b + c) = a * b + a * c": quantified(
            "a b c", eq(mul(a, add(b, c)), add(mul(a, b), mul(a, c)))
        ),
        "∀ a b : Nat, (a + b) * (a + b) = a * a + b * b": quantified(
            "a b", eq(mul(add(a, b), add(a, b)), add(mul(a, a), mul(b, b)))
        ),
        "∀ a b : Nat, a * b = 0 → a = 0 ∨ b = 0": quantified(
            "a b", imp(eq(mul(a, b), zero), binary("or", eq(a, zero), eq(b, zero)))
        ),
        "∀ a b : Nat, a * b = 0 → a = 0 ∧ b = 0": quantified(
            "a b", imp(eq(mul(a, b), zero), binary("and", eq(a, zero), eq(b, zero)))
        ),
        "∀ a b c : Nat, a ≤ b → a + c ≤ b + c": quantified(
            "a b c", imp(le(a, b), le(add(a, c), add(b, c)))
        ),
        "∀ a b c : Nat, a ≤ b → a + c < b + c": quantified(
            "a b c", imp(le(a, b), binary("lt", add(a, c), add(b, c)))
        ),
        "∀ n : Nat, n * 1 = n": quantified("n", eq(mul(n, one), n)),
        "∀ n : Nat, n * 0 = n": quantified("n", eq(mul(n, zero), n)),
    }


def reference_target(case):
    formula = reference_formulas().get(case["formal_statement"])
    if formula is None:
        raise ValueError("Reference has no reviewed structured Nat mapping")
    return NatTarget.create(
        SourceClaim(case["id"], 1, case["original_statement"]),
        formula,
        "项目作者核对的 Nat 结构化目标；全部前提保留在公式内，Nat.succ 表示加一。"
        "此映射不是模型形式化结果或独立专家确认。",
    )
