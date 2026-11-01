"""Typed Nat/Int data targets and bounded, host-owned proof strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import ClassVar

from .domain import Claim, Formalization, canonical, digest
from .formalizer import SourceClaim, exact_object, strict_json
from .model_service import integer_field, text_field

MODE = "nat-bounded-search-v1"
BUNDLE_FILE = "input/nat-search.json"
STRATEGIES = ("omega", "simp", "decide", "counterexample")
SCHEMA = "nat-formula-v1"
TEMPLATE = "lean_workspace/nat-search-checker.lean.in"
INT_MODE = "int-bounded-search-v1"
INT_SCHEMA = "int-formula-v1"
INT_BUNDLE_FILE = "input/int-search.json"
INT_TEMPLATE = "lean_workspace/int-search-checker.lean.in"
NAME = re.compile(r"[a-z][a-z0-9_]{0,15}\Z")


def render_formula(formula: dict, domain: str = "Nat") -> str:
    """Validate all fields/scope before generating any Lean syntax."""
    if domain not in ("Nat", "Int"):
        raise ValueError("Arithmetic domain must be explicitly Nat or Int")
    nodes = 0

    def visit(value, prop, scope=(), depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 24 or nodes > 256 or not isinstance(value, dict):
            raise ValueError("Natural arithmetic formula exceeds its structural budget")
        op = value.get("op")
        if not prop and op == "num":
            exact_object(value, {"op", "value"}, "Numeral")
            integer_field(value["value"], "numeral", -1000000 if domain == "Int" else 0, 1000000)
            return f"({value['value']} : {domain})"
        if not prop and op == "neg" and domain == "Int":
            exact_object(value, {"op", "body"}, "Integer negation")
            return f"(- {visit(value['body'], False, scope, depth + 1)})"
        if not prop and op == "var":
            exact_object(value, {"op", "name"}, "Variable")
            if value["name"] not in scope:
                raise ValueError("Unbound natural number variable")
            return value["name"]
        if not prop and op in ("add", "mul", "sub"):
            exact_object(value, {"op", "left", "right"}, "Arithmetic")
            left = visit(value["left"], False, scope, depth + 1)
            right = visit(value["right"], False, scope, depth + 1)
            return f"({left} {dict(add='+', mul='*', sub='-')[op]} {right})"
        if prop and op in ("eq", "le", "lt", "and", "or", "imp"):
            exact_object(value, {"op", "left", "right"}, "Relation")
            logical = op in ("and", "or", "imp")
            left = visit(value["left"], logical, scope, depth + 1)
            right = visit(value["right"], logical, scope, depth + 1)
            symbol = {"eq": "=", "le": "≤", "lt": "<", "and": "∧", "or": "∨", "imp": "→"}[op]
            return f"({left} {symbol} {right})"
        if prop and op == "not":
            exact_object(value, {"op", "body"}, "Negation")
            return f"(¬ {visit(value['body'], True, scope, depth + 1)})"
        if prop and op in ("forall", "exists"):
            exact_object(value, {"op", "name", "body"}, "Quantifier")
            name = value["name"]
            if (
                not isinstance(name, str)
                or NAME.fullmatch(name) is None
                or name in ("by", "in", "do", "if", "then", "else", "let", "fun", "match", "with")
                or name in scope
                or len(scope) >= 8
            ):
                raise ValueError("Invalid, shadowed or excessive quantified variable")
            body = visit(value["body"], True, (*scope, name), depth + 1)
            return f"({'∀' if op == 'forall' else '∃'} ({name} : {domain}), {body})"
        raise ValueError("Unsupported or incorrectly typed natural arithmetic constructor")

    return visit(formula, True)


@dataclass(frozen=True)
class NatTarget:
    DOMAIN: ClassVar[str] = "Nat"
    TARGET_SCHEMA: ClassVar[str] = SCHEMA
    source: SourceClaim
    formula_json: str
    interpretation: str

    def __post_init__(self):
        if not isinstance(self.source, SourceClaim):
            raise ValueError("Target requires a versioned source")
        text_field(self.formula_json, "formula", 32768)
        text_field(self.interpretation, "interpretation", 4000)
        formula = strict_json(self.formula_json)
        render_formula(formula, self.DOMAIN)
        if canonical(formula).decode() != self.formula_json:
            raise ValueError("Formula must use canonical JSON")

    @classmethod
    def create(cls, source, formula, interpretation):
        return cls(source, canonical(formula).decode(), interpretation)

    @classmethod
    def from_dict(cls, data):
        exact_object(data, {"schema", "source", "formula", "interpretation"}, "Nat target")
        if data["schema"] != cls.TARGET_SCHEMA:
            raise ValueError("Unsupported Nat target schema")
        return cls.create(
            SourceClaim.from_dict(data["source"]), data["formula"], data["interpretation"]
        )

    def to_dict(self):
        return {
            "schema": self.TARGET_SCHEMA,
            "source": json.loads(canonical(asdict(self.source))),
            "formula": json.loads(self.formula_json),
            "interpretation": self.interpretation,
        }

    @property
    def fingerprint(self):
        return digest(self.to_dict())

    def contract(self, direction):
        if direction not in ("prove", "refute"):
            raise ValueError("Search direction must be prove or refute")
        statement = render_formula(json.loads(self.formula_json), self.DOMAIN)
        claim = Claim(
            self.DOMAIN.lower() + "-" + self.fingerprint[:32],
            self.source.version,
            self.source.statement,
            statement,
            (),
        )
        checked = statement if direction == "prove" else f"¬ ({statement})"
        return claim, Formalization(claim.fingerprint, checked, direction)


class IntTarget(NatTarget):
    DOMAIN = "Int"
    TARGET_SCHEMA = INT_SCHEMA


def target_from_dict(data):
    schema = data.get("schema") if isinstance(data, dict) else None
    if schema == SCHEMA:
        return NatTarget.from_dict(data)
    if schema == INT_SCHEMA:
        return IntTarget.from_dict(data)
    raise ValueError("Unsupported arithmetic target schema")


def target_mode(target):
    return INT_MODE if target.DOMAIN == "Int" else MODE


def bundle_file(mode):
    if mode not in (MODE, INT_MODE):
        raise ValueError("Unknown arithmetic input mode")
    return INT_BUNDLE_FILE if mode == INT_MODE else BUNDLE_FILE


def bundle_digest_key(mode):
    bundle_file(mode)
    return "int_bundle_digest" if mode == INT_MODE else "nat_bundle_digest"


def validate_plan(data: dict, target: NatTarget) -> dict:
    exact_object(data, {"direction", "strategy", "witnesses"}, "Proof plan")
    if data["direction"] not in ("prove", "refute") or data["strategy"] not in STRATEGIES:
        raise ValueError("Unsupported proof direction or bounded strategy")
    witnesses = data["witnesses"]
    if not isinstance(witnesses, list) or len(witnesses) > 8:
        raise ValueError("Expected at most eight natural witnesses")
    for value in witnesses:
        integer_field(value, "witness", -1000000 if target.DOMAIN == "Int" else 0, 1000000)
    if data["strategy"] == "counterexample":
        if data["direction"] != "refute" or not witnesses:
            raise ValueError("Counterexample requires refutation and explicit witnesses")
        formula = json.loads(target.formula_json)
        for _ in witnesses:
            if formula.get("op") != "forall":
                raise ValueError("Witnesses must instantiate leading universal quantifiers")
            formula = formula["body"]
    elif witnesses:
        raise ValueError("Only counterexample plans accept witnesses")
    return json.loads(canonical(data))


def proof_source(plan: dict, target: NatTarget) -> str:
    plan = validate_plan(plan, target)
    strategy = plan["strategy"]
    if strategy == "counterexample":
        witnesses = " ".join(
            f"({value} : Int)" if target.DOMAIN == "Int" else str(value)
            for value in plan["witnesses"]
        )
        return (
            f"by\n  intro mraHypothesis\n  have mraInstance := mraHypothesis {witnesses}\n  omega"
        )
    if strategy == "omega":
        return "by\n  intros\n  omega"
    if strategy == "simp":
        return "by\n  simp_all"
    return "by decide"


def search_bundle(target: NatTarget, plan: dict) -> dict:
    return {
        "mode": target_mode(target),
        "target": target.to_dict(),
        "plan": validate_plan(plan, target),
    }


def render_checker(root: Path, target: NatTarget, plan: dict) -> str:
    plan = validate_plan(plan, target)
    template = (root / (INT_TEMPLATE if target.DOMAIN == "Int" else TEMPLATE)).read_text(
        encoding="utf-8"
    )
    if template.count("__MRA_STATEMENT__") != 2 or template.count("__MRA_PROOF__") != 1:
        raise ValueError("Invalid trusted natural arithmetic checker template")
    return template.replace(
        "__MRA_STATEMENT__", target.contract(plan["direction"])[1].checked_statement
    ).replace("__MRA_PROOF__", proof_source(plan, target))


def parse_bundle(raw: bytes):
    if not 0 < len(raw) <= 65536:
        raise ValueError("Natural arithmetic search bundle exceeds byte budget")
    data = strict_json(raw.decode("utf-8"))
    exact_object(data, {"mode", "target", "plan"}, "Nat search bundle")
    target = target_from_dict(data["target"])
    if data["mode"] != target_mode(target):
        raise ValueError("Arithmetic mode and target domain do not match")
    return target, validate_plan(data["plan"], target)
