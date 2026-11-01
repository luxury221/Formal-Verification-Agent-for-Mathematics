"""Explicit algebraic structures and Nat induction through the frozen real executor."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .domain import digest
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore, source_manifest
from .tasks import TaskStore
from .structured_proofs import METHODS, compile_proof
from .structured_terms import MODE, LIMITS, compile_request

FILES = (
    "mra/structured_terms.py",
    "mra/structured_proofs.py",
    "mra/structures.py",
    "mra/structured_examples.py",
    "mra/typed_terms.py",
    "mra/number_theory_terms.py",
)
LIMITATIONS = [
    "Carrier parameters live in universe Type; operations and every law are explicit theorem premises.",
    "Names and law descriptions are local claims; consistency, existence and originality are not certified.",
    "Carrier arithmetic uses explicit operations. Commutativity and nonzero conditions are never inferred from labels.",
    "Definitions depend only on carrier/operation parameters and earlier definitions; Nat folds use the kernel recursor.",
    "One controlled proof program and at most one outer Nat induction per check, with explicit generalization.",
    "Candidate and independent proof-data replay use the same pinned Lean kernel implementation.",
    "Failure or timeout is UNKNOWN, never a mathematical refutation.",
    "No raw Lean, extra imports, arbitrary tactics, model calls or automatic old research-graph admission.",
]


def manifest(root):
    return {
        "mode": MODE,
        "executor": deepcopy(source_manifest(root)),
        "files": {n: file_hash(root / n) for n in FILES},
        "limits": dict(LIMITS),
        "methods": list(METHODS),
    }


def prepare(root, request):
    compiled = compile_request(request)
    plan = {
        **compiled,
        "proof_source": compile_proof(request["proof"], compiled),
        "source_manifest": manifest(Path(root).resolve()),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": request["timeout_seconds"],
            "max_heartbeats": 200000,
            "automatic_retries": 0,
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": list(LIMITATIONS),
    }
    plan["plan_fingerprint"] = digest(plan)
    return plan


def catalog():
    from .structured_examples import examples

    return {
        "mode": MODE,
        "carrier_universe": "Type",
        "numeric_types": ["Nat", "Int", "Rat"],
        "proof_methods": list(METHODS),
        "limits": dict(LIMITS),
        "maximum_runs": 200,
        "examples": examples(),
        "limitations": list(LIMITATIONS),
        "provenance": "repository-author-visible-programs-not-model-output",
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
    }


class StructureStore(MathlibLabStore):
    def __init__(self, root, *, base=None):
        self.root = Path(root).resolve()
        self.base = Path(base) if base is not None else self.root / "artifacts/explicit-structures"
        if self.base.resolve() != self.base or not self.base.is_relative_to(
            self.root / "artifacts"
        ):
            raise ValueError("Structure checks must remain inside project artifacts")
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Structure ledger cannot be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Explicit structure language, proof program, executor or runtime changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return MathlibLabStore.gate(self, view, check_files)

    def catalog(self):
        return catalog()

    def report(self, run_id):
        return {**super().report(run_id), "mode": MODE, "fresh_attestation": False}
