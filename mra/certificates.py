"""Bounded, version-bound kernel expressions; no Lean source or elaborator input."""

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re

from .domain import Claim, Formalization, canonical, digest
from .formalizer import exact_object, strict_json

MODE = "kernel-expression-certificate-v1"
REFERENCE_MODE = "repository-lean-reference-v1"
TARGETS = ("nat_add_zero", "false_all_nat_zero")
TEMPLATE = "lean_workspace/certificate-checker.lean.in"
CERTIFICATE_FILE = "input/certificate.json"
MAX_BYTES = 65536
MAX_NODES = 2048
MAX_DEPTH = 64
MAX_LEVEL = 8
MAX_NAT = 2**32 - 1
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*\Z")


def target_contract(case: dict) -> tuple[Claim, Formalization]:
    if case["id"] not in TARGETS:
        raise ValueError("Certificate target is not enabled")
    claim = Claim(
        case["id"],
        1,
        case["original_statement"],
        case["formal_statement"],
        tuple(case.get("annotation", {}).get("assumptions", ())),
    )
    statement = (
        claim.formal_statement if case["direction"] == "prove" else f"¬ ({claim.formal_statement})"
    )
    return claim, Formalization(claim.fingerprint, statement, case["direction"])


def target_descriptor(case: dict) -> dict:
    claim, formalization = target_contract(case)
    return {
        "kind": "certificate-target-description",
        "claim": asdict(claim),
        "formalization": asdict(formalization),
        "certificate_header": {
            "schema_version": "1",
            "format": MODE,
            "claim_fingerprint": claim.fingerprint,
            "formalization_fingerprint": digest(asdict(formalization)),
        },
        "limits": {
            "bytes": MAX_BYTES,
            "nodes": MAX_NODES,
            "depth": MAX_DEPTH,
            "universe_level": MAX_LEVEL,
            "natural_literal": MAX_NAT,
        },
        "evidence_level": "E0_CANDIDATE",
        "math_verdict": "UNKNOWN",
        "lean_calls": 0,
    }


@dataclass(frozen=True)
class KernelCertificate:
    body: bytes
    nodes: int

    @property
    def fingerprint(self):
        return hashlib.sha256(self.body).hexdigest()


def parse_certificate(raw: bytes, claim: Claim, formalization: Formalization) -> KernelCertificate:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Certificate must be bounded UTF-8 JSON bytes")
    if claim.id not in TARGETS:
        raise ValueError("Certificate target is not enabled")
    try:
        data = strict_json(raw.decode("utf-8"))
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid or excessively nested certificate JSON") from error
    exact_object(
        data,
        {"schema_version", "format", "claim_fingerprint", "formalization_fingerprint", "proof"},
        "Certificate",
    )
    if (
        data["schema_version"] != "1"
        or data["format"] != MODE
        or data["claim_fingerprint"] != claim.fingerprint
        or data["formalization_fingerprint"] != digest(asdict(formalization))
    ):
        raise ValueError("Certificate format or exact target binding changed")
    nodes = 0

    def integer(value, maximum, name):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"Invalid bounded integer: {name}")

    def visit(expr, depth=1, binders=0):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise ValueError("Certificate expression exceeds depth or node budget")
        if not isinstance(expr, dict) or not isinstance(expr.get("op"), str):
            raise ValueError("Expected a certificate expression object")
        op = expr["op"]
        if op == "const":
            exact_object(expr, {"op", "name", "levels"}, "Constant")
            name, levels = expr["name"], expr["levels"]
            if not isinstance(name, str) or len(name) > 192 or NAME.fullmatch(name) is None:
                raise ValueError("Invalid constant name; source syntax is not accepted")
            if not isinstance(levels, list) or len(levels) > MAX_LEVEL:
                raise ValueError("Invalid constant universe levels")
            for level in levels:
                integer(level, MAX_LEVEL, "universe level")
        elif op == "app":
            exact_object(expr, {"op", "fn", "arg"}, "Application")
            visit(expr["fn"], depth + 1, binders)
            visit(expr["arg"], depth + 1, binders)
        elif op in ("lam", "forall"):
            exact_object(expr, {"op", "type", "body"}, "Binder")
            visit(expr["type"], depth + 1, binders)
            visit(expr["body"], depth + 1, binders + 1)
        elif op in ("bvar", "nat", "sort"):
            field = {"bvar": "index", "nat": "value", "sort": "level"}[op]
            exact_object(expr, {"op", field}, "Atomic expression")
            integer(expr[field], MAX_LEVEL if op == "sort" else MAX_NAT, field)
            if op == "bvar" and expr[field] >= binders:
                raise ValueError("Unbound certificate variable")
        else:
            raise ValueError("Unsupported certificate constructor; no source, tactics or IO")

    visit(data["proof"])
    body = canonical(data) + b"\n"
    if len(body) > MAX_BYTES:
        raise ValueError("Canonical certificate exceeds byte budget")
    return KernelCertificate(body, nodes)


def read_certificate(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(MAX_BYTES + 1)


def render_certificate_checker(root: Path, expected_statement: str) -> str:
    template = (root / TEMPLATE).read_text(encoding="utf-8")
    if template.count("__MRA_EXPECTED_STATEMENT__") != 1:
        raise ValueError("Invalid trusted certificate template")
    return template.replace("__MRA_EXPECTED_STATEMENT__", expected_statement)
