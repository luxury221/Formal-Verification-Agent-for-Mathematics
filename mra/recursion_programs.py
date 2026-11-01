"""Complete targets with checked local lemmas, using the unchanged kernel/replay executor."""

from copy import deepcopy
from pathlib import Path

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .mathlib_lab import MathlibLabStore
from .number_theory_terms import identifier
from .recursion_proofs import compile_proof, ARITHMETIC
from .structure_strategy_ast import free_names
from .structured_terms import FIELDS, compile_request
from .structures import StructureStore, manifest as provider_manifest
from .typed_terms import bounded_data

MODE = "structure-recursion-program-v1"
FILES = (
    "mra/recursion_proofs.py",
    "mra/recursion_programs.py",
    "mra/structure_strategy_ast.py",
    "mra/structure_strategy_terms.py",
)


def manifest(root):
    return {
        "mode": MODE,
        "provider": provider_manifest(Path(root)),
        "files": {n: file_hash(Path(root) / n) for n in FILES},
        "max_local_lemmas": 2,
        "arithmetic_rules": list(ARITHMETIC),
    }


def compile_program(raw):
    bounded_data(raw)
    exact_object(
        raw, {"schema", "target", "program", "timeout_seconds"}, "Complete recursion program"
    )
    if raw["schema"] != MODE:
        raise ValueError("Use structure-recursion-program-v1")
    exact_object(
        raw["target"], FIELDS - {"proof"}, "Complete original target without reference proof"
    )
    if (
        type(raw["timeout_seconds"]) not in (int, float)
        or raw["timeout_seconds"] != raw["target"]["timeout_seconds"]
    ):
        raise ValueError("The outer shared worker budget must equal the complete target budget")
    target = deepcopy(raw["target"])
    compiled = compile_request({**target, "proof": {"method": "rfl"}})
    program = raw["program"]
    exact_object(program, {"lemmas", "proof"}, "Bounded local lemma program")
    lemmas = program["lemmas"]
    if not isinstance(lemmas, list) or len(lemmas) > 2:
        raise ValueError("At most two checked local lemmas")
    if lemmas and set(free_names([a["formula"] for a in target["assumptions"]])):
        raise ValueError(
            "Local lemmas currently require original laws closed over target variables"
        )
    known, names, lines = [], set(), ["by"]
    for i, lemma in enumerate(lemmas):
        exact_object(lemma, {"name", "goal", "proof"}, "Checked local lemma")
        name = identifier(lemma["name"])
        if name in names:
            raise ValueError("Duplicate local lemma name")
        used = set(free_names(lemma["goal"]))
        helper = compile_request(
            {
                **target,
                "variables": [v for v in target["variables"] if v["name"] in used],
                "goal": lemma["goal"],
                "proof": lemma["proof"],
            }
        )
        source = compile_proof(lemma["proof"], helper, known)
        lines += [
            f"  have mra_lemma_{i} : (",
            *["    " + s for s in helper["formal_statement"].splitlines()],
            "  ) := " + source.splitlines()[0],
            *["  " + s for s in source.splitlines()[1:]],
        ]
        known.append(
            {"name": name, "lean": f"mra_lemma_{i}", "compiled": helper, "proof_source": source}
        )
        names.add(name)
    proof = compile_proof(program["proof"], compiled, known)
    lines += ["  exact (", *["    " + s for s in proof.splitlines()], "  )"]
    return {
        "request": deepcopy(raw),
        "target": target,
        "formal_statement": compiled["formal_statement"],
        "proof_source": "\n".join(lines),
        "local_lemmas": known,
        "variable_mapping": compiled["variable_mapping"],
        "assumptions": compiled["assumptions"],
        "node_count": compiled["node_count"],
    }


def prepare(root, raw):
    value = {
        **compile_program(raw),
        "source_manifest": manifest(root),
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": raw["timeout_seconds"],
            "automatic_retries": 0,
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "Local lemmas are obligations, never extra assumptions; only earlier lemmas can be referenced.",
            "All original environment, variables and premises stay in the final target.",
            "Fixed Nat rules and at most two local lemma proofs, each with at most one Nat induction.",
            "A failed local lemma rejects the entire program; no partial success is published.",
            "Same pinned kernel with a distinct independent proof-data replay process; no model or raw Lean.",
        ],
    }
    value["plan_fingerprint"] = digest(value)
    return value


class RecursionStore(StructureStore):
    def __init__(self, root, *, base=None):
        super().__init__(
            root, base=base if base is not None else Path(root) / "artifacts/structure-recursion"
        )

    def preview(self, request):
        return prepare(self.root, request)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Recursion compiler, fixed arithmetic, provider or runtime changed"
        view = {
            **row,
            "plan": {
                **row["plan"],
                "source_manifest": row["plan"]["source_manifest"]["provider"]["executor"],
            },
        }
        return MathlibLabStore.gate(self, view, check_files)

    def report(self, run_id):
        return {**MathlibLabStore.report(self, run_id), "mode": MODE, "fresh_attestation": False}

    def list_runs(self):
        with self.transaction(False) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        fields = ("run_id", "state", "math_verdict", "evidence_level", "created_at", "invalidated")
        return [
            {**{name: row[name] for name in fields}, "title": row["plan"]["target"]["title"]}
            for row in (self.report(run_id) for run_id in ids)
        ]

    def catalog(self):
        return {
            "mode": MODE,
            "max_local_lemmas": 2,
            "arithmetic_rules": list(ARITHMETIC),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
        }
