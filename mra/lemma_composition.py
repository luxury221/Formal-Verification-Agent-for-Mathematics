"""Fresh kernel-checked compositions of private, archived theorem proof data."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import time

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition_terms import MODE, compile_composition
from .mathlib_knowledge import MathlibKnowledgeStore
from .mathlib_lab import POLICY, MathlibLabStore, audit, render_candidate, source_manifest
from .mathlib_terms import compile_request as source_compile
from .number_theory_terms import identifier
from .replay import extract_export, render_replay
from .tasks import TaskStore, WorkerBusy, _identifier
from .verifier import NativeLeanBackend

TEMPLATE = "lean_workspace/lemma-import.lean.in"
FILES = (
    "mra/lemma_composition.py",
    "mra/lemma_composition_terms.py",
    "mra/number_theory_terms.py",
    "mra/mathlib_knowledge.py",
    "mra/research_mathlib.py",
    "mra/knowledge_instances.py",
    TEMPLATE,
)


def manifest(root):
    return {
        "mode": MODE,
        "executor": deepcopy(source_manifest(root)),
        "extension_files": {name: file_hash(root / name) for name in FILES},
        "maximum_sources": 4,
        "policy": deepcopy(POLICY),
    }


def bindings(request):
    if not isinstance(request, dict):
        raise ValueError("Composition must be an object")
    values = request.get("sources")
    if not isinstance(values, list) or not 1 <= len(values) <= 4:
        raise ValueError("Select one to four private E4 sources")
    seen = set()
    for value in values:
        exact_object(value, {"alias", "item_id"}, "Private lemma binding")
        alias = identifier(value["alias"])
        _identifier(value["item_id"])
        if alias in seen:
            raise ValueError("Duplicate private lemma alias")
        seen.add(alias)
    return values


def load_source(knowledge, binding):
    row = knowledge.report(binding["item_id"])
    if (row["state"], row["math_verdict"], row["evidence_level"], row["artifact_status"]) != (
        "ACTIVE",
        "PROVED",
        "E4",
        "MATCHES_RECORDED_HASHES",
    ):
        raise ValueError("Private lemma requires an active, intact E4 archive")
    content = row["content"]
    source = content["source"]
    compiled = source_compile(source["request"])
    if compiled["formal_statement"] != source["formal_statement"]:
        raise ValueError("Private lemma signature differs from its full source statement")
    directory = knowledge.directory(binding["item_id"])
    name = "proof/replay/proof.ndjson"
    path = directory / name
    if path.stat().st_size > 524288:
        raise ValueError("Private proof data exceeds 512 KiB")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != content["file_hashes"].get(name):
        raise ValueError("Private proof data differs from its archived hash")
    if extract_export("MRA_EXPORT_BEGIN\n" + raw.decode("utf-8") + "MRA_EXPORT_END\n") != raw:
        raise ValueError("Private proof data is not in the canonical bounded export format")
    return {
        **binding,
        "version": row["version"],
        "archive_fingerprint": digest(content),
        "source_fingerprint": source["source_fingerprint"],
        "request": source["request"],
        "formal_statement": source["formal_statement"],
        "alignment": source["source_alignment"],
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "data_bytes": len(raw),
    }, raw


def render_composition(root, plan):
    source = render_candidate(root, plan)
    imports = []
    template = (root / TEMPLATE).read_text(encoding="utf-8")
    if template.count("__MRA_SOURCE_STATEMENT__") != 1 or "__MRA_SLOT__" not in template:
        raise ValueError("Invalid trusted lemma import template")
    for slot, item in enumerate(plan["source_items"]):
        imports.append(
            template.replace("__MRA_SOURCE_STATEMENT__", item["formal_statement"]).replace(
                "__MRA_SLOT__", str(slot)
            )
        )
    old_locals = """  let locals := env.checked.get.constants.foldStage2
    (fun names name _ => names.push name) (#[] : Array Name)"""
    new_locals = """  let mut locals : Array Name := #[]
  for (name, _) in env.constants do
    if (`MraCandidate).isPrefixOf name then
      locals := locals.push name"""
    if source.count(old_locals) != 1 or source.count("namespace MraCandidate\n") != 1:
        raise ValueError("Frozen candidate adapter changed; composition needs revalidation")
    return (
        source.replace(
            "import LeanExport.Basic",
            "import LeanExport.Basic\nimport LeanExport.Parse\nimport Lean.Replay",
        )
        .replace("namespace MraCandidate\n", "\n".join(imports) + "\nnamespace MraCandidate\n", 1)
        .replace(old_locals, new_locals)
    )


def audit_imports(output, count):
    records = [
        strict_json(line.split("MRA_IMPORT:", 1)[1])
        for line in output.splitlines()
        if "MRA_IMPORT:" in line
    ]
    if len(records) != count:
        raise ValueError("Missing or repeated private proof-data import audit")
    for slot, record in enumerate(records):
        exact_object(
            record,
            {
                "slot",
                "type_matches",
                "candidate_module_imported",
                "replayed_declarations",
                "axioms",
            },
            "Proof import audit",
        )
        if (
            type(record["slot"]) is not int
            or record["slot"] != slot
            or record["type_matches"] is not True
            or record["candidate_module_imported"] is not False
            or type(record["replayed_declarations"]) is not int
            or record["replayed_declarations"] < 1
            or not isinstance(record["axioms"], list)
            or any(a not in POLICY["allowed_axioms"] for a in record["axioms"])
        ):
            raise ValueError("Private proof data was not checked against its complete type")
    return records


class LemmaCompositionStore(MathlibLabStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/lemma-compositions"
        self.path = self.base / "lab.sqlite3"
        if (self.root / "artifacts").is_symlink() or self.base.is_symlink():
            raise ValueError("Composition storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        self.knowledge = MathlibKnowledgeStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Composition ledger cannot be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        # Snapshot caller-owned data before inspecting mutable source ledgers.
        if len(canonical(request)) > 196608:
            raise ValueError("Composition input exceeds 192 KiB")
        request = strict_json(canonical(request).decode())
        sources = [load_source(self.knowledge, b)[0] for b in bindings(request)]
        compiled = compile_composition(request, sources)
        plan = {
            **compiled,
            "source_items": sources,
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
                "Explicit Nat proof composition, not autonomous theorem search.",
                "All quantified arguments and premise proofs must be supplied.",
                "Premise consistency and natural-language alignment require separate review.",
                "Independent processes use the same pinned Lean kernel implementation.",
                "Source archives are revalidated; historical status is not a fresh attestation.",
            ],
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if row["invalidated"]:
            return "Previously invalidated; create a new confirmed composition"
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Composition compiler, source adapter, import template or runtime changed"
        for recorded in row["plan"]["source_items"]:
            current, _ = load_source(self.knowledge, {k: recorded[k] for k in ("alias", "item_id")})
            if current != recorded:
                return "Private source changed; review a new full composition plan"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)

    def report(self, run_id):
        row = self.read(run_id)
        try:
            reason = self.gate(row)
        except WorkerBusy:
            raise
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            reason = "Source storage unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            reason = "Evidence unavailable: " + str(error)
        if reason and not row["invalidated"]:
            with self.transaction() as db:
                row = self.read(run_id, db)
                if not row["invalidated"]:
                    row.update(invalidated=True, math_verdict="UNKNOWN", evidence_level="E0")
                    self.event(row, "EVIDENCE_INVALIDATED", reason=reason)
                    self.save(db, row)
        return {
            **row,
            "mode": MODE,
            "historical_evidence": row["state"] == "COMPLETED",
            "artifact_status": "UNAVAILABLE"
            if row["invalidated"]
            else "MATCHES_RECORDED_HASHES"
            if row["files"]
            else "NOT_YET_RECORDED",
            "fresh_attestation": False,
        }

    def catalog(self):
        return {
            "mode": MODE,
            "maximum_sources": 4,
            "maximum_runs": 200,
            "sources": self.knowledge.search()["items"],
            "scope": "Explicit private theorem applications with all arguments and premises; data-only kernel import and fresh independent replay",
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
                        current, data = load_source(
                            self.knowledge, {k: recorded[k] for k in ("alias", "item_id")}
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
