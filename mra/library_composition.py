"""Versioned composition executor accepting complete, provenance-bound proof archives."""

from copy import deepcopy
from pathlib import Path
import sqlite3
import time

from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition import (
    LemmaCompositionStore,
    bindings,
    manifest as legacy_manifest,
    render_composition,
    audit_imports,
)
from .lemma_composition_terms import MODE as LEGACY_MODE, compile_composition
from .mathlib_lab import MathlibLabStore, POLICY, audit, run_identifier
from .proof_library import FILES, LIMITS, provenance
from .replay import extract_export, render_replay
from .tasks import TaskStore, WorkerBusy
from .verifier import NativeLeanBackend

MODE = "library-lemma-composition-v1"


def manifest(root):
    original = legacy_manifest(root)
    return {
        "mode": MODE,
        "executor": original["executor"],
        "original_protocol": original,
        "files": {n: file_hash(root / n) for n in FILES},
        "limits": deepcopy(LIMITS),
    }


class LibraryCompositionStore(LemmaCompositionStore):
    def __init__(self, library):
        self.root = Path(library.root).resolve()
        self.base, self.path = library.base, library.path
        self.tasks = TaskStore(self.root)
        self.knowledge = library

    def load_source(self, binding):
        return self.knowledge.load_source(binding)

    def read(self, run_id, db=None):
        run_identifier(run_id)
        if db is None:
            with self.transaction(False) as connection:
                return self.read(run_id, connection)
        stored = db.execute("SELECT body,sha256 FROM runs WHERE id=?", (run_id,)).fetchone()
        if stored is None:
            raise ValueError("Unknown library composition run")
        try:
            row = self.decode(stored)
            if row["run_id"] != run_id:
                raise ValueError("Library composition identity changed")
            return row
        except (ValueError, KeyError, TypeError) as error:
            self.knowledge.damage_marker("runs", run_id, error)
            raise

    def preview(self, request):
        if len(canonical(request)) > 196608:
            raise ValueError("Library composition input exceeds 192 KiB")
        request = strict_json(canonical(request).decode())
        if request.get("schema") != MODE:
            raise ValueError("Use the explicit library composition protocol")
        sources = [self.load_source(b)[0] for b in bindings(request)]
        ancestry = provenance(sources)
        compiled = compile_composition({**request, "schema": LEGACY_MODE}, sources)
        compiled["request"]["schema"] = MODE
        plan = {
            **compiled,
            "source_items": sources,
            "provenance": ancestry,
            "source_manifest": manifest(self.root),
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
                "Explicit Nat proof terms with every original premise preserved.",
                "Archived proof data is kernel-checked on import; the complete result is independently replayed.",
                "Transitive sources remain live obligations; revocation or damage persistently invalidates descendants.",
                "Two processes use the same pinned Lean kernel implementation; local names are not authenticated.",
            ],
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if self.knowledge.damage_marker("runs", row["run_id"]):
            return "Previously detected damaged library composition ledger"
        if row["invalidated"]:
            return "Previously invalidated; confirm a new complete library composition"
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Proof library, compiler, import template or sealed runtime changed"
        current_sources = []
        for recorded in row["plan"]["source_items"]:
            current, _ = self.load_source({k: recorded[k] for k in ("alias", "item_id")})
            if current != recorded:
                return "Direct or transitive proof source changed"
            current_sources.append(current)
        if provenance(current_sources) != row["plan"]["provenance"]:
            return "Transitive proof provenance changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return MathlibLabStore.gate(self, view, check_files)

    def report(self, run_id):
        return {**super().report(run_id), "mode": MODE}

    def catalog(self):
        return {
            "mode": MODE,
            "maximum_sources": 4,
            "maximum_runs": 200,
            "sources": [
                *self.knowledge.legacy.knowledge.search()["items"],
                *self.knowledge.search()["items"],
            ],
            "limits": deepcopy(LIMITS),
            "scope": "Complete private Nat theorem composition with transitive source obligations",
        }

    def execute(self, run_id, lock_fd):
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        row = self.read(run_id)
        result = {"candidate": None, "replay": None, "error": None, "worker_seconds": 0}
        state, proved = "EXECUTION_ERROR", False

        def cancelled():
            current = self.read(run_id)
            return current["cancel_requested"] or current["invalidated"]

        def checkpoint(phase, _):
            with self.transaction() as db:
                current = self.read(run_id, db)
                current["stage"] = phase
                self.event(current, phase)
                self.save(db, current)

        try:
            reason = self.gate(row, False)
            if reason:
                raise ValueError(reason)
            plan = self.preview(row["plan"]["request"])
            if plan != row["plan"]:
                raise ValueError("Composition differs from the reviewed complete plan")
            (directory / "plan.json").write_bytes(canonical(plan) + b"\n")
            backend = NativeLeanBackend(
                self.root, timeout_seconds=plan["request"]["timeout_seconds"]
            )
            (directory / "runtime-snapshot.json").write_bytes(
                canonical(backend._snapshot.data) + b"\n"
            )
            started = time.monotonic()
            for phase, folder in (("LEAN_RUNNING", "candidate"), ("REPLAY_RUNNING", "replay")):
                if cancelled():
                    state = "CANCELLED"
                    break
                if self.gate(self.read(run_id), False):
                    raise ValueError("Composition source or confirmation changed before checking")
                backend.verify_environment()
                work = directory / folder
                work.mkdir()
                if folder == "candidate":
                    for slot, recorded in enumerate(plan["source_items"]):
                        current, data = self.load_source(
                            {k: recorded[k] for k in ("alias", "item_id")}
                        )
                        if current != recorded:
                            raise ValueError("Private source changed while staging proof data")
                        (work / f"source-{slot}.ndjson").write_bytes(data)
                    source = render_composition(self.root, plan)
                else:
                    (work / "proof.ndjson").write_bytes(
                        extract_export((directory / "candidate/stdout.log").read_text())
                    )
                    (work / "policy.json").write_bytes(canonical(POLICY) + b"\n")
                    source = render_replay(self.root, plan["formal_statement"])
                (work / "Candidate.lean").write_text(source, encoding="utf-8")
                outcome = backend._run_worker(
                    work,
                    directory / ("sandbox-" + folder),
                    work / "stdout.log",
                    work / "stderr.log",
                    plan["request"]["timeout_seconds"] - result["worker_seconds"],
                    phase=phase,
                    cancel_requested=cancelled,
                    checkpoint=checkpoint,
                    lifecycle_lock_fd=lock_fd,
                )
                result[folder] = outcome
                result["worker_seconds"] += outcome["elapsed_seconds"]
                state = outcome["run_status"]
                if state != "COMPLETED":
                    break
                output, stderr = (
                    (work / "stdout.log").read_text(),
                    (work / "stderr.log").read_text(),
                )
                if stderr or outcome["returncode"] != 0:
                    state = (
                        "REJECTED"
                        if folder == "candidate"
                        and outcome["returncode"]
                        and outcome["returncode"] > 0
                        and not stderr
                        else "EXECUTION_ERROR"
                    )
                    result["error"] = (stderr or output.split("MRA_EXPORT_BEGIN", 1)[0])[-6000:]
                    break
                outcome["audit"] = audit(
                    output,
                    "MRA_AUDIT:" if folder == "candidate" else "MRA_REPLAY:",
                    replay=folder == "replay",
                )
                if folder == "candidate":
                    outcome["imports"] = audit_imports(output, len(plan["source_items"]))
                    for slot, item in enumerate(plan["source_items"]):
                        if file_hash(work / f"source-{slot}.ndjson") != item["data_sha256"]:
                            raise ValueError("Staged private proof data changed during checking")
                else:
                    backend.verify_environment()
                    proved = True
            result["elapsed_with_integrity_seconds"] = time.monotonic() - started
        except WorkerBusy:
            raise  # Temporary contention is not mathematical invalidity.
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            state, result["error"] = "POLICY_BLOCKED", str(error)
        files = {
            str(p.relative_to(directory)): file_hash(p)
            for p in directory.rglob("*")
            if p.is_file()
            and not any(part.startswith("sandbox-") for part in p.relative_to(directory).parts)
        }
        with self.transaction() as db:
            current = self.read(run_id, db)
            if current["cancel_requested"]:
                state, proved = "CANCELLED", False
            try:
                reason = self.gate(current, False)
            except WorkerBusy:
                raise
            except sqlite3.Error as error:
                if storage_busy(error):
                    raise
                reason = str(error)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                reason = str(error)
            if reason:
                current["invalidated"] = True
                state, proved = "POLICY_BLOCKED", False
                result["error"] = reason
            current.update(
                state=state,
                stage=state,
                files=files,
                result=result,
                math_verdict="PROVED" if proved and state == "COMPLETED" else "UNKNOWN",
                evidence_level="E4" if proved and state == "COMPLETED" else "E0",
            )
            self.event(current, state)
            self.save(db, current)
        return self.report(run_id)
