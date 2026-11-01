"""Independent Mathlib term experiment; never changes the legacy evidence protocol."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import ContentSnapshot, file_hash, file_record
from .mathlib_terms import MODE, IMPORT, REFERENCES, compile_request, examples, text
from .replay import extract_export, render_replay
from .tasks import TaskStore
from .verifier import NativeLeanBackend, render_source

TRUST_FILES = (
    "lean_workspace/mathlib-export.lean.in",
    "mra/mathlib_lab.py",
    "mra/mathlib_terms.py",
    "mra/verifier.py",
    "mra/replay.py",
    "mra/integrity.py",
    "mra/domain.py",
    "mra/formalizer.py",
    "mra/tasks.py",
    "lean_workspace/replay-checker.lean.in",
    "lean_workspace/evidence-policy.json",
    "lean_workspace/environment.json",
    "lean_workspace/runtime-lock.json",
)
POLICY = {
    "mode": MODE,
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "forbidden_axioms": ["sorryAx", "Lean.ofReduceBool"],
    "require_exact_target": True,
    "require_independent_replay": True,
}
ACTIVE = ("QUEUED", "RUNNING")


def now():
    return datetime.now(timezone.utc).isoformat()


def run_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"mathlib-[a-f0-9]{32}", value):
        raise ValueError("Invalid Mathlib run identifier")
    return value


def source_manifest(root):
    snapshot = ContentSnapshot.load_runtime(root)
    return {
        "mode": MODE,
        "runtime_snapshot": snapshot.fingerprint,
        "files": {name: file_record(root / name)["sha256"] for name in TRUST_FILES},
        "policy": POLICY,
        "catalog": {name: list(values) for name, values in REFERENCES.items()},
    }


def prepare(root, request):
    compiled = compile_request(strict_json(canonical(request).decode("utf-8")))
    manifest = source_manifest(root)
    result = {
        **compiled,
        "source_manifest": manifest,
        "budget": {
            "candidate_processes": 1,
            "replay_processes": 1,
            "shared_worker_seconds": compiled["request"]["timeout_seconds"],
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
    }
    result["plan_fingerprint"] = digest(result)
    return result


def render_candidate(root, plan):
    source = render_source(
        {"extra_declarations": "", "proof": plan["proof_source"]}, plan["formal_statement"]
    )
    marker = "LeanExport.dumpEnv env (some locals.toList)"
    if source.count(marker) != 1:
        raise ValueError("Legacy export adapter changed; revalidation is required")
    fragment = (root / "lean_workspace/mathlib-export.lean.in").read_text(encoding="utf-8").rstrip()
    return source.replace(marker, fragment)


def audit(output, marker, *, replay=False):
    lines = [line.split(marker, 1)[1] for line in output.splitlines() if marker in line]
    if len(lines) != 1:
        raise ValueError("Missing or duplicate kernel audit")
    value = strict_json(lines[0])
    if (
        value.get("target") != "MraCandidate.result"
        or value.get("declaration_kind") != "theorem"
        or value.get("type_matches") is not True
        or value.get("local_axioms") != []
    ):
        raise ValueError("Kernel did not check the exact theorem without local axioms")
    for key in ("axioms", "local_dependency_axioms"):
        axioms = value.get(key)
        if not isinstance(axioms, list) or any(
            a not in POLICY["allowed_axioms"] or a in POLICY["forbidden_axioms"] for a in axioms
        ):
            raise ValueError("Unapproved axiom in the proof closure")
    if replay and (
        value.get("mode") != "data-only-kernel-replay-v1"
        or value.get("candidate_module_imported") is not False
        or type(value.get("replayed_declarations")) is not int
        or value["replayed_declarations"] < 1
    ):
        raise ValueError("Independent proof-data replay was not performed")
    return value


class MathlibLabStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.base = self.root / "artifacts/mathlib-lab"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.is_symlink():
            raise ValueError("Mathlib store must remain inside the project")
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.path.is_symlink():
            raise ValueError("Mathlib ledger cannot be redirected")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("Unknown Mathlib run")
        value = strict_json(row[0])
        if digest(value) != row[1]:
            raise ValueError("Mathlib ledger digest mismatch")
        return value

    def read(self, run_id, db=None):
        run_identifier(run_id)
        if db is not None:
            return self.decode(
                db.execute("SELECT body, sha256 FROM runs WHERE id=?", (run_id,)).fetchone()
            )
        with self.transaction(False) as conn:
            return self.read(run_id, conn)

    @staticmethod
    def save(db, row):
        db.execute(
            "UPDATE runs SET body=?, sha256=? WHERE id=?",
            (canonical(row).decode(), digest(row), row["run_id"]),
        )

    @staticmethod
    def event(row, kind, **data):
        row["events"].append({"kind": kind, "at": now(), **data})
        row["updated_at"] = now()

    def catalog(self):
        config = json.loads((self.root / "lean_workspace/environment.json").read_text())
        return {
            "mode": MODE,
            "import": IMPORT,
            "lean_version": config["lean_version"],
            "mathlib_commit": config["mathlib_commit"],
            "license": "Apache-2.0",
            "definition": {
                "symbol": "(· ∣ ·) : Nat → Nat → Prop",
                "meaning": "a ∣ b ↔ ∃ k : Nat, b = a * k",
                "zero": "0 ∣ b iff b = 0; every Nat divides 0",
                "subtraction": "Nat subtraction truncates at zero",
                "source": ".runtime/lean-4.35.0-linux/src/lean/Init/Data/Nat/Div/Basic.lean:24",
            },
            "references": [
                {
                    "name": name,
                    "nat_arguments": values[0],
                    "proof_arguments": values[1],
                    "type": values[2],
                    "title": values[3],
                    "origin": values[4],
                    "source": ".runtime/mathlib/Mathlib/Data/Nat/Basic.lean"
                    if values[4] == "mathlib"
                    else ".runtime/lean-4.35.0-linux/src/lean/Init/Data/Nat",
                }
                for name, values in REFERENCES.items()
            ],
            "examples": examples(),
            "scope": "manual structured terms; no automated search or private-knowledge admission",
        }

    def preview(self, request):
        return prepare(self.root, request)

    def submit(self, data):
        exact_object(
            data,
            {"request_id", "request", "plan_fingerprint", "reviewer", "confirmed"},
            "Mathlib submission",
        )
        request_id = text(data["request_id"], 128, "Request ID")
        reviewer = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True:
            raise ValueError(
                "Explicit confirmation of original, full formula, library terms and budget is required"
            )
        plan = self.preview(data["request"])
        if data["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Preview changed; review the current complete statement again")
        key = digest({"plan": plan["plan_fingerprint"], "reviewer": reviewer})
        with self.transaction() as db:
            previous = db.execute(
                "SELECT body, sha256 FROM runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous:
                row = self.decode(previous)
                if row["submission_fingerprint"] != key:
                    raise ValueError("Request ID is already bound to a different confirmation")
                return row
            if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 200:
                raise ValueError("Mathlib experiment ledger is limited to 200 runs")
            run_id = "mathlib-" + uuid.uuid4().hex
            row = {
                "run_id": run_id,
                "request_id": request_id,
                "submission_fingerprint": key,
                "plan": plan,
                "alignment": {
                    "reviewer": reviewer,
                    "confirmed": True,
                    "at": now(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "authentication": "local-assertion-only",
                },
                "state": "QUEUED",
                "stage": "QUEUED",
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
                "created_at": now(),
                "updated_at": now(),
                "cancel_requested": False,
                "invalidated": False,
                "events": [],
                "files": {},
                "result": None,
                "model_calls": 0,
            }
            self.event(row, "CONFIRMED_AND_QUEUED", reviewer=reviewer)
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (run_id, request_id, canonical(row).decode(), digest(row)),
            )
        return row

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
                    "created_at",
                    "invalidated",
                )
            }
            | {"title": row["plan"]["request"]["title"]}
            for row in (self.report(run_id) for run_id in ids)
        ]

    def directory(self, run_id):
        path = self.base / "runs" / run_identifier(run_id)
        if not path.resolve().is_relative_to(self.base.resolve()) or path.is_symlink():
            raise ValueError("Mathlib artifact path escapes its store")
        return path

    def gate(self, row, check_files=True):
        if row["invalidated"]:
            return "Previously invalidated; a new preview and run are required"
        if row["plan"]["source_manifest"] != source_manifest(self.root):
            return "Versioned compiler, reference catalog, replay policy or runtime seal changed"
        if check_files:
            directory = self.directory(row["run_id"])
            for name, expected in row["files"].items():
                path = directory / name
                if (
                    not path.resolve().is_relative_to(directory.resolve())
                    or path.is_symlink()
                    or file_record(path)["sha256"] != expected
                ):
                    return "Proof artifacts changed or were redirected"
        return None

    def report(self, run_id):
        row = self.read(run_id)
        try:
            reason = self.gate(row)
        except (OSError, RuntimeError, ValueError) as error:
            reason = "Evidence unavailable: " + str(error)
        if reason and not row["invalidated"]:
            with self.transaction() as db:
                row = self.read(run_id, db)
                row.update(invalidated=True, math_verdict="UNKNOWN", evidence_level="E0")
                self.event(row, "EVIDENCE_INVALIDATED", reason=reason)
                self.save(db, row)
        row["historical_evidence"] = row["state"] == "COMPLETED"
        row["artifact_status"] = (
            "UNAVAILABLE"
            if row["invalidated"]
            else "MATCHES_RECORDED_HASHES"
            if row["files"]
            else "NOT_YET_RECORDED"
        )
        return row

    def cancel(self, run_id):
        with self.transaction() as db:
            row = self.read(run_id, db)
            if row["state"] in ACTIVE:
                row["cancel_requested"] = True
                if row["state"] == "QUEUED":
                    row.update(state="CANCELLED", stage="CANCELLED")
                self.event(row, "CANCELLATION_REQUESTED")
                self.save(db, row)
        return self.report(run_id)

    def revoke(self, run_id, data):
        exact_object(data, {"reviewer", "reason", "confirmed"}, "Revocation")
        reviewer = text(data["reviewer"], 200, "Reviewer")
        reason = text(data["reason"], 500, "Reason")
        if data["confirmed"] is not True:
            raise ValueError("Revocation requires explicit confirmation")
        with self.transaction() as db:
            row = self.read(run_id, db)
            row.update(invalidated=True, math_verdict="UNKNOWN", evidence_level="E0")
            self.event(row, "ALIGNMENT_REVOKED", reviewer=reviewer, reason=reason)
            self.save(db, row)
        return self.report(run_id)

    def recover_locked(self):
        interrupted = []
        with self.transaction() as db:
            for value in db.execute("SELECT body, sha256 FROM runs").fetchall():
                row = self.decode(value)
                if row["state"] == "RUNNING":
                    row.update(
                        state="INTERRUPTED",
                        stage="INTERRUPTED",
                        math_verdict="UNKNOWN",
                        evidence_level="E0",
                    )
                    self.event(
                        row, "INTERRUPTED", reason="Supervisor stopped; artifacts are not promoted"
                    )
                    self.save(db, row)
                    interrupted.append(row["run_id"])
        return interrupted

    def recover(self):
        with self.tasks.worker_lock():
            return {"interrupted": self.recover_locked(), "automatic_retry": False}

    def execute(self, run_id, lock_fd):
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        row = self.read(run_id)
        result = {"candidate": None, "replay": None, "error": None, "worker_seconds": 0}
        state = "EXECUTION_ERROR"
        proved = False

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
                raise ValueError("Compiled request differs from the confirmed plan")
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
                    raise ValueError("Confirmation or trusted content changed before checking")
                backend.verify_environment()
                work = directory / folder
                work.mkdir()
                if folder == "candidate":
                    source = render_candidate(self.root, plan)
                else:
                    raw = extract_export((directory / "candidate/stdout.log").read_text())
                    (work / "proof.ndjson").write_bytes(raw)
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
                if folder == "replay":
                    backend.verify_environment()
                    proved = True
            result["elapsed_with_integrity_seconds"] = time.monotonic() - started
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            state, result["error"] = "POLICY_BLOCKED", str(error)
        files = {
            str(path.relative_to(directory)): file_hash(path)
            for path in directory.rglob("*")
            if path.is_file()
            and not any(p.startswith("sandbox-") for p in path.relative_to(directory).parts)
        }
        with self.transaction() as db:
            current = self.read(run_id, db)
            if current["cancel_requested"]:
                state, proved = "CANCELLED", False
            try:
                reason = self.gate(current, False)
            except (OSError, RuntimeError, ValueError) as error:
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

    def work_once(self, run_id=None):
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            with self.transaction() as db:
                if run_id:
                    row = self.read(run_id, db)
                else:
                    rows = [
                        self.decode(r)
                        for r in db.execute(
                            "SELECT body, sha256 FROM runs ORDER BY rowid"
                        ).fetchall()
                    ]
                    row = next((r for r in rows if r["state"] == "QUEUED"), None)
                if row is None or row["state"] != "QUEUED":
                    return {"worker_state": "IDLE"}
                row.update(state="RUNNING", stage="CHECKING")
                self.event(row, "STARTED")
                self.save(db, row)
            self.execute(row["run_id"], lock_fd)
            return {"worker_state": "EXECUTED", "run_id": row["run_id"]}
