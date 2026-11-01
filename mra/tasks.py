"""Durable local queue for approved references and bounded fixed-target certificates."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterator
import uuid

from .certificates import MODE, parse_certificate, target_contract
from .datasets import DATASET_ID, load_dataset
from .domain import EvidenceLevel, RunStatus, TaskResult, Verdict, canonical, digest
from .harness import MinimalResearchEngine
from .integrity import ContentSnapshot, IntegrityError, file_hash

MAX_ATTEMPTS = 3
RETRYABLE = frozenset(("INTERRUPTED", "EXECUTION_ERROR", "TIMEOUT"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Identifier must contain 1-128 ASCII letters, digits, dots, _ or -")
    return value


def _request_context(
    root: Path,
    case_id: str,
    *,
    suite: str = "golden",
    timeout_seconds: float = 30.0,
    reviewer: str | None = None,
) -> tuple[dict, dict]:
    """Freeze exact inputs and local runtime seal without executing Lean."""
    if suite not in ("golden", DATASET_ID):
        raise ValueError("Unknown repository reference suite")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (float, int))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 120
    ):
        raise ValueError("Timeout must be finite and in (0, 120] seconds")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        raise ValueError("Reviewer must be a nonempty local assertion")
    dataset = load_dataset(root) if suite == DATASET_ID else None
    cases = (
        dataset.cases
        if dataset
        else json.loads((root / "evals/golden_cases.json").read_text(encoding="utf-8"))
    )
    case = next((case for case in cases if case["id"] == case_id), None)
    if case is None:
        raise ValueError("Unknown approved reference case")
    if dataset and case_id in dataset.manifest["splits"]["reserved"]:
        raise ValueError("Reserved references require the explicit reference-benchmark protocol")
    snapshot = ContentSnapshot.load_runtime(root)
    policy = json.loads((root / "lean_workspace/evidence-policy.json").read_text(encoding="utf-8"))
    request = {
        "schema_version": "1",
        "kind": "repository-reference-check",
        "suite": suite,
        "case_id": case_id,
        "fixture_fingerprint": digest(case),
        "original_statement": case["original_statement"],
        "formal_statement": case["formal_statement"],
        "assumptions": list(case.get("annotation", {}).get("assumptions", ())),
        "direction": case["direction"],
        "reviewer": reviewer,
        "actor_authentication": "local-assertion-only",
        "timeout_seconds": float(timeout_seconds),
        "max_attempts": MAX_ATTEMPTS,
        "runtime_snapshot_fingerprint": snapshot.fingerprint,
        "policy_fingerprint": digest(policy),
        "model_calls": 0,
    }
    return request, case


def reference_request(root: Path, case_id: str, **options) -> dict:
    """Preserve the v1 reference request shape and fingerprint."""
    return _request_context(root, case_id, **options)[0]


def certificate_request(
    root: Path,
    case_id: str,
    certificate_raw: bytes,
    *,
    timeout_seconds: float = 30.0,
    reviewer: str | None = None,
) -> dict:
    """Freeze canonical certificate data, never a mutable input filename."""
    request, case = _request_context(
        root, case_id, timeout_seconds=timeout_seconds, reviewer=reviewer
    )
    certificate = parse_certificate(certificate_raw, *target_contract(case))
    return {
        **request,
        "kind": "kernel-certificate-check",
        "input_mode": MODE,
        "certificate": certificate.body.decode("utf-8"),
        "certificate_digest": certificate.fingerprint,
    }


def _execution_request(root: Path, request: dict) -> tuple[dict, bytes | None]:
    """Rebuild a recognized frozen request before any Lean initialization."""
    options = {
        "reviewer": request["reviewer"],
        "timeout_seconds": request["timeout_seconds"],
    }
    if request["kind"] == "repository-reference-check":
        return reference_request(root, request["case_id"], suite=request["suite"], **options), None
    if request["kind"] == "kernel-certificate-check":
        if not isinstance(request["certificate"], str):
            raise ValueError("Stored certificate must be canonical UTF-8 JSON text")
        raw = request["certificate"].encode("utf-8")
        return certificate_request(root, request["case_id"], raw, **options), raw
    raise ValueError("Unknown task request kind")


class WorkerBusy(RuntimeError):
    """A worker or its bounded checker still owns the local queue lock."""


class TaskStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.base = self.root / "artifacts/tasks"
        self.path = self.base / "tasks.sqlite3"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.is_symlink() or self.base.resolve() != self.base:
            raise ValueError("Task directory must not be redirected by a symlink")
        with self._transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported task database version")
            if version == 0:
                for statement in (
                    "CREATE TABLE tasks (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, "
                    "request TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, "
                    "cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
                    "CREATE TABLE attempts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES "
                    "tasks(id), number INTEGER NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL, "
                    "artifact_directory TEXT, result TEXT, file_hashes TEXT, error_code TEXT, "
                    "error TEXT, started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(task_id, number))",
                    "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "task_id TEXT NOT NULL REFERENCES tasks(id), attempt_id TEXT REFERENCES "
                    "attempts(id), kind TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL)",
                    "CREATE TABLE retries (request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL "
                    "REFERENCES tasks(id), attempt_id TEXT NOT NULL REFERENCES attempts(id))",
                ):
                    db.execute(statement)
                db.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        if self.path.is_symlink():
            raise ValueError("Task database must not be a symlink")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def worker_lock(self) -> Iterator[int]:
        # Linux advisory locks avoid clocks, PID reuse and lease expiry races.
        import fcntl

        path = self.base / "worker.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerBusy("A task worker/checker is still active") from error
            yield fd
        finally:
            # Do not LOCK_UN: inherited descriptors must keep the lock held if
            # the supervisor exits while its checker is still running.
            os.close(fd)

    @staticmethod
    def _task(db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (_identifier(task_id),)).fetchone()
        if row is None:
            raise ValueError("Unknown task ID")
        if digest(json.loads(row["request"])) != row["fingerprint"]:
            raise ValueError("Stored task request changed")
        return row

    @staticmethod
    def _event(db, task_id, kind, payload=None, attempt_id=None) -> None:
        db.execute(
            "INSERT INTO events(task_id, attempt_id, kind, payload, at) VALUES (?, ?, ?, ?, ?)",
            (task_id, attempt_id, kind, canonical(payload or {}).decode(), _now()),
        )

    def submit(self, request_id: str, case_id: str, **options) -> dict:
        return self._submit(request_id, reference_request(self.root, case_id, **options))

    def submit_certificate(
        self, request_id: str, case_id: str, certificate_raw: bytes, **options
    ) -> dict:
        return self._submit(
            request_id, certificate_request(self.root, case_id, certificate_raw, **options)
        )

    def _submit(self, request_id: str, request: dict) -> dict:
        _identifier(request_id)
        fingerprint = digest(request)
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM tasks WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["fingerprint"] != fingerprint
                    or json.loads(existing["request"]) != request
                ):
                    raise ValueError("Request ID was already used for different content")
                task_id = existing["id"]
            else:
                task_id = "task-" + uuid.uuid4().hex
                db.execute(
                    "INSERT INTO tasks(id, request_id, request, fingerprint, state, created_at) "
                    "VALUES (?, ?, ?, ?, 'QUEUED', ?)",
                    (task_id, request_id, canonical(request).decode(), fingerprint, _now()),
                )
                self._event(db, task_id, "CREATED", {"request_fingerprint": fingerprint})
                self._event(db, task_id, "QUEUED")
        return self.report(task_id)

    def cancel(self, task_id: str) -> dict:
        with self._transaction() as db:
            task = self._task(db, task_id)
            if task["state"] == "QUEUED":
                db.execute(
                    "UPDATE tasks SET state='CANCELLED', cancel_requested=1 WHERE id=?", (task_id,)
                )
                self._event(db, task_id, "CANCELLED", {"before_execution": True})
            elif task["state"] == "RUNNING" and not task["cancel_requested"]:
                db.execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (task_id,))
                self._event(db, task_id, "CANCEL_REQUESTED")
        return self.report(task_id)

    def retry(self, task_id: str, attempt_id: str, request_id: str) -> dict:
        _identifier(request_id)
        _identifier(attempt_id)
        with self._transaction() as db:
            task = self._task(db, task_id)
            existing = db.execute(
                "SELECT * FROM retries WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                if (existing["task_id"], existing["attempt_id"]) != (task_id, attempt_id):
                    raise ValueError("Retry request ID was already used for different content")
            else:
                latest = db.execute(
                    "SELECT * FROM attempts WHERE task_id=? ORDER BY number DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if (
                    task["state"] != "FAILED"
                    or latest is None
                    or latest["id"] != attempt_id
                    or latest["error_code"] not in RETRYABLE
                    or latest["number"] >= MAX_ATTEMPTS
                ):
                    raise ValueError(
                        "Only the latest retryable failed attempt may be retried; at most 3 total attempts"
                    )
                db.execute(
                    "INSERT INTO retries VALUES (?, ?, ?)", (request_id, task_id, attempt_id)
                )
                db.execute("UPDATE tasks SET state='QUEUED' WHERE id=?", (task_id,))
                self._event(db, task_id, "RETRY_QUEUED", {"request_id": request_id}, attempt_id)
        return self.report(task_id)

    def _recover(self) -> list[str]:
        recovered = []
        with self._transaction() as db:
            for task in db.execute("SELECT * FROM tasks WHERE state='RUNNING'").fetchall():
                self._task(db, task["id"])
                state = "CANCELLED" if task["cancel_requested"] else "FAILED"
                db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
                db.execute(
                    "UPDATE attempts SET state=?, stage='INTERRUPTED', error_code='INTERRUPTED', "
                    "error='Worker stopped before terminal commit; a fresh check is required', "
                    "finished_at=? WHERE task_id=? AND state='RUNNING'",
                    (state, _now(), task["id"]),
                )
                self._event(db, task["id"], "RECOVERED", {"state": state})
                recovered.append(task["id"])
        return recovered

    def recover(self) -> list[str]:
        with self.worker_lock():
            return self._recover()

    def _claim(self, task_id: str | None) -> tuple[str, str, dict] | None:
        with self._transaction() as db:
            if task_id is None:
                row = db.execute(
                    "SELECT id FROM tasks WHERE state='QUEUED' ORDER BY rowid LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                task_id = row["id"]
            task = self._task(db, task_id)
            if task["state"] != "QUEUED":
                return None
            attempt_id = "attempt-" + uuid.uuid4().hex
            number = (
                db.execute("SELECT count(*) FROM attempts WHERE task_id=?", (task_id,)).fetchone()[
                    0
                ]
                + 1
            )
            db.execute(
                "INSERT INTO attempts(id, task_id, number, state, stage, started_at) "
                "VALUES (?, ?, ?, 'RUNNING', 'STARTING', ?)",
                (attempt_id, task_id, number, _now()),
            )
            db.execute("UPDATE tasks SET state='RUNNING' WHERE id=?", (task_id,))
            self._event(db, task_id, "STARTED", {"number": number}, attempt_id)
            return task_id, attempt_id, json.loads(task["request"])

    def _checkpoint(self, task_id: str, attempt_id: str, stage: str, directory: str = "") -> None:
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE attempts SET stage=?, artifact_directory=COALESCE(NULLIF(?, ''), "
                "artifact_directory) WHERE id=? AND task_id=? AND state='RUNNING'",
                (stage, directory, attempt_id, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Attempt no longer owns execution")
            self._event(
                db,
                task_id,
                "CHECKPOINT",
                {"stage": stage, "artifact_directory": directory},
                attempt_id,
            )

    def _cancel_requested(self, task_id: str) -> bool:
        with self._transaction(write=False) as db:
            return bool(self._task(db, task_id)["cancel_requested"])

    def _bundle_hashes(self, result: TaskResult) -> dict:
        directory = Path(result.artifact_directory)
        if not directory.resolve().is_relative_to(self.root / "artifacts/runs"):
            raise ValueError("Task artifact path escapes the project")
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        if report["task"] != asdict(result):
            raise ValueError("Task result differs from its checker report")
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        names = set(receipt["payload"]["files"]) | {"report.json", "receipt.json"}
        hashes = {}
        for name in names:
            path = directory / name
            if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
                raise ValueError("Task artifact file escapes its bundle")
            hashes[name] = file_hash(path)
            expected = receipt["payload"]["files"].get(name)
            if expected is not None and hashes[name] != expected:
                raise ValueError("Task artifact differs from the checked receipt")
        if report.get("evidence") and report["evidence"]["receipt_digest"] != digest(receipt):
            raise ValueError("Task receipt differs from the assessed evidence")
        return hashes

    def _finish(
        self,
        task_id: str,
        attempt_id: str,
        result: TaskResult | None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> None:
        hashes = self._bundle_hashes(result) if result is not None else None
        with self._transaction() as db:
            task = self._task(db, task_id)
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if (
                task["state"] != "RUNNING"
                or attempt is None
                or attempt["state"] != "RUNNING"
                or attempt["task_id"] != task_id
            ):
                raise ValueError("Attempt cannot commit twice or replace another result")
            if task["cancel_requested"] or (
                result is not None and result.run_status == RunStatus.CANCELLED
            ):
                state, result, hashes = "CANCELLED", None, None
                error_code, error = (
                    "CANCELLED",
                    "Cancellation won the terminal transaction; no task evidence published",
                )
            elif result is None:
                state = "FAILED"
            else:
                state = (
                    "COMPLETED"
                    if result.run_status in (RunStatus.COMPLETED, RunStatus.POLICY_BLOCKED)
                    else "FAILED"
                )
                error_code = result.run_status.value if state == "FAILED" else None
                error = result.reason if state == "FAILED" else None
            db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task_id))
            db.execute(
                "UPDATE attempts SET state=?, stage='FINISHED', result=?, file_hashes=?, "
                "error_code=?, error=?, finished_at=? WHERE id=?",
                (
                    state,
                    canonical(asdict(result)).decode() if result else None,
                    canonical(hashes).decode() if hashes else None,
                    error_code,
                    error,
                    _now(),
                    attempt_id,
                ),
            )
            self._event(db, task_id, state, {"error_code": error_code}, attempt_id)

    def work_once(self, task_id: str | None = None) -> dict:
        with self.worker_lock() as lock_fd:
            recovered = self._recover()
            claimed = self._claim(task_id)
            if claimed is None:
                return {
                    "worker_state": "IDLE",
                    "recovered_task_ids": recovered,
                    "task": self.report(task_id) if task_id else None,
                }
            task_id, attempt_id, request = claimed
            try:
                self._checkpoint(task_id, attempt_id, "PREPARING")
                if self._cancel_requested(task_id):
                    self._finish(task_id, attempt_id, None)
                else:
                    current, certificate_raw = _execution_request(self.root, request)
                    if current != request:
                        raise ValueError(
                            "Frozen input, policy or runtime seal changed; submit a new request"
                        )
                    engine = MinimalResearchEngine(
                        self.root, request["timeout_seconds"], request["suite"]
                    )
                    if (
                        engine.backend.environment.runtime_digest
                        != request["runtime_snapshot_fingerprint"]
                        or digest(engine.backend.policy) != request["policy_fingerprint"]
                    ):
                        raise ValueError("Prepared runtime differs from the frozen request")
                    result = engine.run_case(
                        request["case_id"],
                        request["reviewer"],
                        cancel_requested=lambda: self._cancel_requested(task_id),
                        checkpoint=lambda stage, directory: self._checkpoint(
                            task_id, attempt_id, stage, directory
                        ),
                        lifecycle_lock_fd=lock_fd,
                        certificate_raw=certificate_raw,
                    )
                    self._finish(task_id, attempt_id, result)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                code = (
                    "POLICY_BLOCKED"
                    if isinstance(error, (IntegrityError, ValueError, KeyError, TypeError))
                    else "EXECUTION_ERROR"
                )
                self._finish(task_id, attempt_id, None, code, str(error))
            return {
                "worker_state": "EXECUTED",
                "recovered_task_ids": recovered,
                "task": self.report(task_id),
            }

    def list_tasks(self, limit: int = 30) -> list[dict]:
        """List durable execution metadata; mathematical results require report()."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Task list limit must be an integer in [1, 100]")
        with self._transaction(write=False) as db:
            rows = db.execute(
                "SELECT id FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
            result = []
            for row in rows:
                task = self._task(db, row["id"])
                request = json.loads(task["request"])
                result.append(
                    {
                        "task_id": task["id"],
                        "case_id": request["case_id"],
                        "kind": request["kind"],
                        "state": task["state"],
                        "created_at": task["created_at"],
                    }
                )
            return result

    def report(self, task_id: str) -> dict:
        with self._transaction(write=False) as db:
            task = dict(self._task(db, task_id))
            attempts = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM attempts WHERE task_id=? ORDER BY number", (task_id,)
                )
            ]
            events = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY sequence", (task_id,)
                )
            ]
        for attempt in attempts:
            attempt["result"] = json.loads(attempt["result"]) if attempt["result"] else None
            attempt["file_hashes"] = (
                json.loads(attempt["file_hashes"]) if attempt["file_hashes"] else None
            )
        for event in events:
            event["payload"] = json.loads(event["payload"])
        latest = attempts[-1] if attempts else None
        result = latest["result"] if latest and task["state"] in ("COMPLETED", "FAILED") else None
        artifact_status = "NONE"
        if result is not None:
            directory = Path(result["artifact_directory"])
            try:
                intact = directory.resolve().is_relative_to(self.root / "artifacts/runs") and all(
                    (directory / name).resolve().is_relative_to(directory.resolve())
                    and not (directory / name).is_symlink()
                    and file_hash(directory / name) == expected
                    for name, expected in latest["file_hashes"].items()
                )
            except OSError:
                intact = False
            artifact_status = "MATCHES_RECORDED_HASHES" if intact else "CHANGED_OR_MISSING"
            if not intact:
                result = None
        return {
            "schema_version": "1",
            "task_id": task_id,
            "request_id": task["request_id"],
            "request": json.loads(task["request"]),
            "request_fingerprint": task["fingerprint"],
            "state": task["state"],
            "cancel_requested": bool(task["cancel_requested"]),
            "created_at": task["created_at"],
            "attempts": attempts,
            "events": events,
            "latest_attempt_id": latest["id"] if latest else None,
            "stage": latest["stage"]
            if latest
            else "QUEUED"
            if task["state"] == "QUEUED"
            else task["state"],
            "last_error_code": latest["error_code"] if latest else None,
            "run_status": result["run_status"]
            if result
            else ("CANCELLED" if task["state"] == "CANCELLED" else None),
            "math_verdict": result["math_verdict"] if result else Verdict.UNKNOWN.value,
            "evidence_level": result["evidence_level"] if result else EvidenceLevel.E0.value,
            "artifact_status": artifact_status,
            "retryable": task["state"] == "FAILED"
            and latest is not None
            and latest["error_code"] in RETRYABLE
            and latest["number"] < MAX_ATTEMPTS,
            "historical_record": True,
            "fresh_attestation": False,
            "limitations": [
                "Approved references and two fixed certificate targets only; model_calls=0",
                "Local names and SQLite owner are trusted; no identity authentication or persistent signature",
                "One local worker; cancellation is polled during checking, not during runtime content hashing",
                "Crash recovery never promotes saved reports; retry performs a fresh bounded Lean check",
                "Recorded artifact hashes do not revalidate current runtime dependencies",
            ],
        }
