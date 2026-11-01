"""Checked local-lemma exports and acyclic, data-only cross-task applications."""

from copy import deepcopy
from pathlib import Path
import sqlite3

from .domain import digest
from .formalizer import exact_object
from .function_dynamics_checks import verified
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition import LemmaCompositionStore, bindings, manifest as import_manifest
from .library_composition import LibraryCompositionStore
from .mathlib_lab import MathlibLabStore, run_identifier
from .mathlib_terms import text
from .number_theory_terms import identifier
from .proof_library import proof_data, provenance
from .recursion_programs import RecursionStore, compile_program, manifest as recursion_manifest
from .research_flow import atomic
from .structure_composition_terms import MODE as COMPOSITION_MODE, compile_composition
from .structure_reuse import FILES as REUSE_FILES
from .structured_terms import FIELDS, compile_request
from .tasks import TaskStore, WorkerBusy
from .typed_terms import bounded_data

EXPORT = "structure-lemma-export-v1"
APPLICATION = "structure-export-application-v1"
FILES = (*REUSE_FILES, "mra/structure_lemma_library.py", "mra/function_dynamics_checks.py")


def manifest(root, mode):
    original = import_manifest(root)
    return {
        "mode": mode,
        "executor": original["executor"],
        "imports": original,
        "recursion": recursion_manifest(root),
        "files": {n: file_hash(root / n) for n in FILES},
        "source_chain": ["recursion", "export", "application"],
    }


def identity(row):
    return digest(
        {
            k: row[k]
            for k in ("run_id", "plan", "alignment", "files", "result", "submission_fingerprint")
        }
    )


def temporary_busy(call):
    try:
        return call()
    except sqlite3.Error as error:
        if storage_busy(error):
            raise WorkerBusy("Source ledger temporarily busy; retry the audit later") from error
        raise


def explicit(target):
    return {**deepcopy(target), "proof": {"method": "rfl"}}


class CheckedStore(LibraryCompositionStore):
    """Separate databases keep the fixed parent/export/application chain acyclic."""

    def __init__(self, root, area, mode, base=None):
        self.root = Path(root).resolve()
        self.mode = mode
        self.base = Path(base) if base is not None else self.root / "artifacts" / area
        if self.base.resolve() != self.base or not self.base.is_relative_to(
            self.root / "artifacts"
        ):
            raise ValueError("Lemma storage must stay inside project artifacts without redirects")
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        self.knowledge = self
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    def damage_marker(self, namespace, key, reason=None):
        if namespace != "runs":
            raise ValueError("Unknown lemma damage namespace")
        run_identifier(key)
        directory = self.base / "invalidated-runs"
        if directory.resolve() != directory:
            raise ValueError("Lemma damage marker redirected")
        p = directory / (key + ".json")
        if reason is not None:
            directory.mkdir(exist_ok=True)
            atomic(p, {"reason": str(reason)[:1000]})
        return p.exists()

    def finish(self, compiled, sources, **context):
        plan = {
            **compiled,
            **context,
            "source_items": sources,
            "provenance": provenance(sources),
            "source_manifest": manifest(self.root, self.mode),
            "budget": {
                "candidate_processes": 1,
                "replay_processes": 1,
                "shared_worker_seconds": compiled["request"]["timeout_seconds"],
                "integrity_time_separate": True,
                "automatic_retries": 0,
                "source_imports_inside_candidate": len(sources),
            },
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "limitations": [
                "Complete original environment and premises; all original variables may include unused quantifiers.",
                "Exports are freshly checked; applications kernel-import proof data and independently replay the full result.",
                "Live provenance is audited on access; revocation or damage persistently invalidates descendants.",
                "Exact shared carriers, operations and definitions; applications cannot be sources in this version.",
                "Same pinned kernel implementation; local reviewer names are not authenticated; no model calls.",
            ],
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if self.damage_marker("runs", row["run_id"]) or row["invalidated"]:
            return "Previously invalidated lemma evidence; confirm a new run"
        if row["plan"]["source_manifest"] != manifest(self.root, self.mode):
            return "Lemma compiler, import executor or runtime identity changed"
        if self.preview(row["plan"]["request"]) != row["plan"]:
            return "Direct or transitive source, complete statement or reviewed plan changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return MathlibLabStore.gate(self, view, check_files)

    def report(self, run_id):
        return {**LemmaCompositionStore.report(self, run_id), "mode": self.mode}

    def list_runs(self):
        with self.transaction(False) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        return [
            {
                k: row[k]
                for k in (
                    "run_id",
                    "state",
                    "math_verdict",
                    "evidence_level",
                    "invalidated",
                    "created_at",
                )
            }
            | {"title": row["plan"]["target"]["title"]}
            for row in (self.report(key) for key in ids)
        ]


class LemmaExportStore(CheckedStore):
    def __init__(self, root, *, parents=None, base=None):
        super().__init__(root, "structure-lemma-exports", EXPORT, base)
        self.parents = parents if parents is not None else RecursionStore(self.root)
        if self.parents.path.resolve() == self.path.resolve():
            raise ValueError("Parent and export ledgers must be separate")

    def parent(self, run_id):
        # Use the busy-aware reporter with the unchanged recursion gate. The older
        # recursion reporter catches RuntimeError, which also includes WorkerBusy.
        row = temporary_busy(
            lambda: LemmaCompositionStore.report(self.parents, run_identifier(run_id))
        )
        verified(row)
        compiled = compile_program(row["plan"]["request"])
        if (
            compiled["formal_statement"] != row["plan"]["formal_statement"]
            or compiled["proof_source"] != row["plan"]["proof_source"]
        ):
            raise ValueError("Parent differs from the checked complete recursion program")
        return row

    def preview(self, raw):
        bounded_data(raw)
        exact_object(
            raw,
            {"schema", "source", "lemma", "title", "original", "timeout_seconds"},
            "Local lemma export",
        )
        if raw["schema"] != EXPORT:
            raise ValueError("Use structure-lemma-export-v1")
        raw = deepcopy(raw)
        exact_object(raw["source"], {"run_id", "fingerprint"}, "Canonical recursion source")
        row = self.parent(raw["source"]["run_id"])
        if raw["source"]["fingerprint"] != identity(row):
            raise ValueError("Parent fingerprint changed; review its current evidence")
        name = identifier(raw["lemma"])
        program = row["plan"]["request"]
        lemmas = program["program"]["lemmas"]
        slot = next((i for i, v in enumerate(lemmas) if v["name"] == name), None)
        if slot is None:
            raise ValueError("Unknown local lemma in the complete parent program")
        target = {
            **deepcopy(program["target"]),
            "title": text(raw["title"], 200, "Export title"),
            "original": text(raw["original"], 12000, "Export semantic description"),
            "goal": deepcopy(lemmas[slot]["goal"]),
            "timeout_seconds": raw["timeout_seconds"],
        }
        standalone = {
            "schema": program["schema"],
            "target": target,
            "program": {
                "lemmas": deepcopy(lemmas[:slot]),
                "proof": deepcopy(lemmas[slot]["proof"]),
            },
            "timeout_seconds": raw["timeout_seconds"],
        }
        compiled = compile_program(standalone)
        compiled["request"] = raw
        return self.finish(
            compiled,
            [],
            parent_source={
                **raw["source"],
                "alignment": deepcopy(row["alignment"]),
                "lemma": name,
                "slot": slot,
            },
            standalone_program=standalone,
        )

    def catalog(self):
        with self.parents.transaction(False) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        parents = []
        for key in ids:
            row = temporary_busy(lambda: LemmaCompositionStore.report(self.parents, key))
            if row["math_verdict"] == "PROVED" and not row["invalidated"]:
                verified(row)
                parents.append(
                    {
                        "run_id": key,
                        "fingerprint": identity(row),
                        "lemmas": [
                            {
                                "name": v["name"],
                                "formal_statement": v["compiled"]["formal_statement"],
                            }
                            for v in row["plan"]["local_lemmas"]
                        ],
                    }
                )
        return {
            "mode": self.mode,
            "parents": parents,
            "exports": self.list_runs(),
            "new_lean_calls": 0,
            "fresh_attestation": False,
        }


class ExportApplicationStore(CheckedStore):
    def __init__(self, root, *, exports=None, base=None):
        super().__init__(root, "structure-export-applications", APPLICATION, base)
        self.exports = exports if exports is not None else LemmaExportStore(self.root)
        if self.path.resolve() in (
            self.exports.path.resolve(),
            self.exports.parents.path.resolve(),
        ):
            raise ValueError("Application, export and parent ledgers must be separate")

    def load_source(self, binding):
        exact_object(binding, {"alias", "item_id"}, "Export source binding")
        identifier(binding["alias"])
        if not isinstance(binding["item_id"], str) or not binding["item_id"].startswith("export."):
            raise ValueError("Use export.<run-id>; applications cannot be sources")
        run_id = run_identifier(binding["item_id"].removeprefix("export."))
        row = temporary_busy(lambda: self.exports.report(run_id))
        verified(row)
        target = explicit(row["plan"]["target"])
        statement = compile_request(target)["formal_statement"]
        if statement != row["plan"]["formal_statement"]:
            raise ValueError("Export full signature differs from checked evidence")
        data = proof_data(
            self.exports.directory(run_id) / "replay/proof.ndjson",
            row["files"].get("replay/proof.ndjson"),
        )
        fingerprint = identity(row)
        parent = row["plan"]["parent_source"]
        descriptor = {
            **deepcopy(binding),
            "version": 1,
            "archive_fingerprint": fingerprint,
            "source_fingerprint": fingerprint,
            "request": target,
            "formal_statement": statement,
            "alignment": deepcopy(row["alignment"]),
            "data_sha256": row["files"]["replay/proof.ndjson"],
            "data_bytes": len(data),
            "depth": 1,
            "ancestors": [
                {
                    "item_id": "recursion." + parent["run_id"],
                    "version": 1,
                    "archive_fingerprint": parent["fingerprint"],
                },
                {"item_id": binding["item_id"], "version": 1, "archive_fingerprint": fingerprint},
            ],
        }
        return descriptor, data

    def preview(self, raw):
        bounded_data(raw)
        exact_object(
            raw,
            {"schema", "target", "sources", "proof", "timeout_seconds"},
            "Cross-task export application",
        )
        if raw["schema"] != APPLICATION:
            raise ValueError("Use structure-export-application-v1")
        exact_object(raw["target"], FIELDS - {"proof"}, "Complete application target without proof")
        if (
            type(raw["timeout_seconds"]) not in (int, float)
            or raw["timeout_seconds"] != raw["target"]["timeout_seconds"]
        ):
            raise ValueError("Outer and complete target budgets must agree")
        raw = deepcopy(raw)
        sources = [self.load_source(b)[0] for b in bindings(raw)]
        inner = {k: raw[k] for k in ("sources", "proof", "timeout_seconds")}
        inner.update(
            schema=COMPOSITION_MODE, graph_id="independent-export", node_id="independent-target"
        )
        compiled = compile_composition(inner, explicit(raw["target"]), sources)
        compiled["request"], compiled["target"] = raw, deepcopy(raw["target"])
        return self.finish(compiled, sources)

    def catalog(self):
        return {
            "mode": self.mode,
            "exports": self.exports.list_runs(),
            "maximum_sources": 4,
            "maximum_runs": 200,
            "fresh_attestation": False,
        }
