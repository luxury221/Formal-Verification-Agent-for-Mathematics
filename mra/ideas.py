"""Durable single-machine idea workflow; model proposals never grant evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import uuid

from .chat_model import ChatModelService, ModelConfig, model_status
from .domain import Alignment, canonical, digest
from .evidence import EvidenceGate
from .formalizer import SourceClaim, exact_object, strict_json, strings
from .integrity import ContentSnapshot, file_hash
from .model_service import ModelBudget, ModelRequest, text_field
from .nat_ideas import NatTarget, validate_plan
from .tasks import TaskStore, _identifier, _now
from .verifier import NativeLeanBackend

ACTIVE = ("QUEUED", "RUNNING")


class IdeaStore:
    def __init__(self, root: Path, *, service_factory=None):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/ideas"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.is_symlink() or self.base.resolve() != self.base:
            raise ValueError("Idea directory must not be redirected")
        self.path = self.base / "ideas.sqlite3"
        self.tasks = TaskStore(self.root)
        self.service_factory = service_factory
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ideas (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_fingerprint TEXT NOT NULL, body TEXT NOT NULL, body_hash TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.path.is_symlink():
            raise ValueError("Idea database must not be redirected")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
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
    def read_row(row):
        if row is None:
            raise ValueError("Unknown idea ID")
        body = strict_json(row["body"])
        if digest(body) != row["body_hash"]:
            raise ValueError("Stored idea record changed")
        return body

    @staticmethod
    def event(body, kind, **details):
        body["events"].append(
            {"sequence": len(body["events"]) + 1, "kind": kind, "at": _now(), **details}
        )

    def mutate(self, idea_id, action):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM ideas WHERE id=?", (_identifier(idea_id),)).fetchone()
            body = self.read_row(row)
            action(body)
            db.execute(
                "UPDATE ideas SET body=?, body_hash=? WHERE id=?",
                (canonical(body).decode(), digest(body), idea_id),
            )
        return body

    def raw(self, idea_id):
        with self.transaction(False) as db:
            return self.read_row(
                db.execute("SELECT * FROM ideas WHERE id=?", (_identifier(idea_id),)).fetchone()
            )

    def create(self, data):
        exact_object(data, {"request_id", "statement", "context"}, "Idea submission")
        _identifier(data["request_id"])
        text_field(data["statement"], "statement", 8000)
        context = strings(data["context"], "context", 8)
        if sum(map(len, context)) > 4000:
            raise ValueError("Context exceeds 4000 characters")
        fingerprint = digest({"statement": data["statement"], "context": list(context)})
        status = model_status(self.root)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM ideas WHERE request_id=?", (data["request_id"],)
            ).fetchone()
            if row:
                if row["request_fingerprint"] != fingerprint:
                    raise ValueError("Request ID already belongs to different idea content")
                idea_id = row["id"]
            else:
                idea_id = "idea-" + uuid.uuid4().hex
                source = SourceClaim(idea_id, 1, data["statement"], context)
                body = {
                    "schema_version": "1",
                    "idea_id": idea_id,
                    "request_id": data["request_id"],
                    "source": asdict(source),
                    "state": "QUEUED" if status["configured"] else "WAITING_FOR_MODEL",
                    "stage": "FORMALIZATION",
                    "operation": "formalize",
                    "created_at": _now(),
                    "model_config_fingerprint": status.get("config_fingerprint"),
                    "candidates": [],
                    "questions": [],
                    "review": None,
                    "selected_target": None,
                    "cancel_requested": False,
                    "model_calls": 0,
                    "simulation_calls": 0,
                    "traces": [],
                    "attempts": [],
                    "result": None,
                    "error": None,
                    "events": [],
                    "versions": [],
                }
                self.event(body, "CREATED")
                self.event(body, body["state"])
                db.execute(
                    "INSERT INTO ideas VALUES (?, ?, ?, ?, ?)",
                    (
                        idea_id,
                        data["request_id"],
                        fingerprint,
                        canonical(body).decode(),
                        digest(body),
                    ),
                )
        return self.report(idea_id)

    def generate(self, idea_id):
        config = ModelConfig.load(self.root)
        if config is None:
            raise ValueError("MODEL_NOT_CONFIGURED: configure config/model.local.json first")

        def action(body):
            if body["state"] in ACTIVE:
                raise ValueError("Idea already has an active operation")
            if body["review"] is not None:
                raise ValueError("Revise the source before replacing an accepted target")
            body.update(
                state="QUEUED",
                stage="FORMALIZATION",
                operation="formalize",
                candidates=[],
                questions=[],
                selected_target=None,
                result=None,
                error=None,
                cancel_requested=False,
                model_config_fingerprint=config.fingerprint,
            )
            self.event(body, "QUEUED", operation="formalize")

        self.mutate(idea_id, action)
        return self.report(idea_id)

    def revise(self, idea_id, data):
        exact_object(data, {"expected_version", "statement", "context"}, "Idea revision")
        text_field(data["statement"], "statement", 8000)
        context = strings(data["context"], "context", 8)
        if sum(map(len, context)) > 4000:
            raise ValueError("Context exceeds 4000 characters")

        def action(body):
            if (
                body["state"] in ACTIVE
                or type(data["expected_version"]) is not int
                or data["expected_version"] != body["source"]["version"]
            ):
                raise ValueError("Stop the active operation or reload the current source version")
            source = SourceClaim.from_dict(body["source"]).revise(data["statement"], context)
            body["versions"].append(
                {
                    key: body[key]
                    for key in (
                        "source",
                        "candidates",
                        "review",
                        "selected_target",
                        "attempts",
                        "result",
                    )
                }
            )
            body.update(
                source=asdict(source),
                state="WAITING_FOR_MODEL",
                stage="FORMALIZATION",
                operation="formalize",
                candidates=[],
                questions=[],
                review=None,
                selected_target=None,
                attempts=[],
                result=None,
                error=None,
                cancel_requested=False,
            )
            self.event(body, "SOURCE_REVISED", version=source.version)

        self.mutate(idea_id, action)
        return self.report(idea_id)

    def confirm(self, idea_id, data):
        exact_object(
            data,
            {"target_fingerprint", "source_version", "reviewer", "confirmed"},
            "Idea confirmation",
        )
        text_field(data["reviewer"], "reviewer", 200)
        if data["confirmed"] is not True or type(data["source_version"]) is not int:
            raise ValueError("Explicit semantic confirmation is required")
        config = ModelConfig.load(self.root)
        if config is None:
            raise ValueError("MODEL_NOT_CONFIGURED")
        snapshot = ContentSnapshot.load_runtime(self.root).fingerprint

        def action(body):
            if (
                body["state"] != "WAITING_FOR_REVIEW"
                or data["source_version"] != body["source"]["version"]
            ):
                raise ValueError("Candidate review is stale or unavailable")
            matches = [
                NatTarget.from_dict(value)
                for value in body["candidates"]
                if NatTarget.from_dict(value).fingerprint == data["target_fingerprint"]
            ]
            if len(matches) != 1 or matches[0].source != SourceClaim.from_dict(body["source"]):
                raise ValueError("Confirmation does not match this source/candidate version")
            body.update(
                selected_target=matches[0].to_dict(),
                review={
                    "id": uuid.uuid4().hex,
                    "target_fingerprint": matches[0].fingerprint,
                    "reviewer": data["reviewer"],
                    "source_version": data["source_version"],
                    "at": _now(),
                    "actor_authentication": "local-assertion-only",
                },
                state="QUEUED",
                stage="PROOF_SEARCH",
                operation="search",
                cancel_requested=False,
                model_config_fingerprint=config.fingerprint,
                runtime_snapshot_fingerprint=snapshot,
            )
            self.event(
                body,
                "ALIGNMENT_CONFIRMED",
                target_fingerprint=matches[0].fingerprint,
                reviewer=data["reviewer"],
            )

        self.mutate(idea_id, action)
        return self.report(idea_id)

    def cancel(self, idea_id, *, revoke=False):
        def action(body):
            if revoke:
                body["review"] = None
            if body["state"] in ACTIVE:
                body["cancel_requested"] = True
                if body["state"] == "QUEUED":
                    body["state"] = "CANCELLED"
            elif revoke:
                body["state"] = "REVOKED"
            self.event(body, "ALIGNMENT_REVOKED" if revoke else "CANCELLATION_REQUESTED")

        self.mutate(idea_id, action)
        return self.report(idea_id)

    def list_ideas(self):
        with self.transaction(False) as db:
            bodies = [
                self.read_row(row)
                for row in db.execute("SELECT * FROM ideas ORDER BY rowid DESC LIMIT 30")
            ]
        return [
            {key: body[key] for key in ("idea_id", "source", "state", "created_at")}
            for body in bodies
        ]

    def cancelled(self, idea_id):
        return self.raw(idea_id)["cancel_requested"]

    def checkpoint(self, idea_id, stage, directory=None):
        def action(body):
            body["stage"] = stage
            self.event(body, stage, **({"artifact_directory": directory} if directory else {}))

        self.mutate(idea_id, action)

    def call(self, idea_id, service, task, payload):
        request = ModelRequest(
            uuid.uuid4().hex,
            task,
            canonical(payload).decode(),
            ModelBudget(),
            "nat-formalizer-output-v1" if task == "nat_formalize" else "nat-proof-output-v1",
        )
        index = len(self.raw(idea_id)["traces"])

        def start(body):
            body["traces"].append(
                {
                    "request": asdict(request),
                    "request_fingerprint": request.fingerprint,
                    "response": None,
                    "outcome": "STARTED",
                }
            )
            body[
                "model_calls" if isinstance(service, ChatModelService) else "simulation_calls"
            ] += 1

        self.mutate(idea_id, start)
        try:
            response = service.generate(request)
            if (
                response.request_fingerprint != request.fingerprint
                or len(response.output_json.encode()) > request.budget.max_output_bytes
                or response.usage.output_tokens > request.budget.max_output_tokens
            ):
                raise ValueError("Model response binding or output budget changed")

            def finish(body):
                body["traces"][index].update(response=asdict(response), outcome="RETURNED")

            self.mutate(idea_id, finish)
            return strict_json(response.output_json)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            outcome = (
                "CANCELLED"
                if isinstance(error, InterruptedError)
                else "TIMEOUT"
                if isinstance(error, TimeoutError)
                else "FAILED"
            )
            self.mutate(idea_id, lambda body: body["traces"][index].update(outcome=outcome))
            raise

    def formalize(self, idea_id, service):
        body = self.raw(idea_id)
        source = SourceClaim.from_dict(body["source"])
        data = self.call(
            idea_id,
            service,
            "nat_formalize",
            {"source": asdict(source), "source_fingerprint": source.fingerprint},
        )
        exact_object(
            data, {"status", "source_fingerprint", "candidates", "questions"}, "Nat formalization"
        )
        if data["source_fingerprint"] != source.fingerprint or data["status"] not in (
            "CANDIDATES_READY",
            "NEEDS_CLARIFICATION",
            "UNSUPPORTED",
        ):
            raise ValueError("Formalization status or source binding is invalid")
        questions = strings(data["questions"], "questions", 3)
        if not isinstance(data["candidates"], list) or len(data["candidates"]) > 3:
            raise ValueError("Expected at most three Nat candidates")
        targets = []
        for candidate in data["candidates"]:
            exact_object(candidate, {"formula", "interpretation"}, "Nat candidate")
            targets.append(NatTarget.create(source, **candidate))
        if len({target.fingerprint for target in targets}) != len(targets):
            raise ValueError("Duplicate natural arithmetic candidate")
        if (
            data["status"] == "CANDIDATES_READY"
            and (not targets or questions)
            or data["status"] == "NEEDS_CLARIFICATION"
            and (not questions or targets)
            or data["status"] == "UNSUPPORTED"
            and (targets or questions)
        ):
            raise ValueError("Formalization candidates/questions conflict with status")

        def finish(current):
            current.update(
                candidates=[target.to_dict() for target in targets],
                questions=list(questions),
                state="CANCELLED"
                if current["cancel_requested"]
                else "WAITING_FOR_REVIEW"
                if targets
                else data["status"],
                stage="FORMALIZATION_COMPLETE",
            )
            self.event(current, current["state"])

        self.mutate(idea_id, finish)

    def search(self, idea_id, service, lock_fd):
        body = self.raw(idea_id)
        target = NatTarget.from_dict(body["selected_target"])
        review = body["review"]
        if (
            review is None
            or review["target_fingerprint"] != target.fingerprint
            or target.source != SourceClaim.from_dict(body["source"])
        ):
            raise ValueError("Search requires an effective exact-target confirmation")
        backend = NativeLeanBackend(self.root, 30)
        if backend.environment.runtime_digest != body["runtime_snapshot_fingerprint"]:
            raise ValueError("Runtime changed after confirmation; submit a new confirmation")
        gate = EvidenceGate(self.root, backend, backend.policy)
        feedback = []
        for number in range(1, 4):
            if self.cancelled(idea_id):
                raise InterruptedError("Idea cancelled")
            self.checkpoint(idea_id, "GENERATING_PROOF")
            data = self.call(
                idea_id,
                service,
                "nat_prove",
                {
                    "target_fingerprint": target.fingerprint,
                    "target": target.to_dict(),
                    "formal_statement": target.contract("prove")[0].formal_statement,
                    "attempt": number,
                    "maximum_attempts": 3,
                    "feedback": feedback,
                },
            )
            try:
                exact_object(data, {"target_fingerprint", "plan"}, "Proof proposal")
                if data["target_fingerprint"] != target.fingerprint:
                    raise ValueError("Proof proposal changes the accepted target")
                if data["plan"] is None:
                    break
                plan = validate_plan(data["plan"], target)
            except (ValueError, TypeError, KeyError) as error:
                reason = str(error)
                feedback.append({"attempt": number, "error": reason})
                self.mutate(
                    idea_id,
                    lambda current: self.event(
                        current, "PLAN_REJECTED", attempt=number, reason=reason
                    ),
                )
                continue
            claim, formalization = target.contract(plan["direction"])
            receipt = backend.check_nat_plan(
                target,
                plan,
                cancel_requested=lambda: self.cancelled(idea_id),
                checkpoint=lambda stage, directory: self.checkpoint(idea_id, stage, directory),
                lifecycle_lock_fd=lock_fd,
            )
            alignment = Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                review["reviewer"],
                "Explicit Nat target confirmation "
                + review["id"]
                + "; refutation checks the exact negation",
            )
            result, evidence = gate.assess(claim, formalization, receipt, alignment)
            directory = Path(result.artifact_directory)
            report = {
                "task": asdict(result),
                "claim": asdict(claim),
                "formalization": asdict(formalization),
                "alignment": asdict(alignment),
                "review": review,
                "target": target.to_dict(),
                "plan": plan,
                "evidence": asdict(evidence) if evidence else None,
                "model_config_fingerprint": body["model_config_fingerprint"],
                "limitations": [
                    "Bounded Nat strategies, not arbitrary mathematics",
                    "Named local assertion, not authenticated identity",
                    "Historical report, not a new attestation",
                ],
            }
            with (directory / "report.json").open("xb") as outgoing:
                outgoing.write(canonical(report))
            files = {
                name: file_hash(directory / name)
                for name in (*receipt.payload["files"], "receipt.json", "report.json")
            }
            attempt = {
                "number": number,
                "plan": plan,
                "result": asdict(result),
                "file_hashes": files,
            }
            self.mutate(idea_id, lambda current: current["attempts"].append(attempt))
            if self.cancelled(idea_id):
                raise InterruptedError("Idea cancelled before publication")
            if evidence is not None:

                def success(current):
                    if current["cancel_requested"] or current["review"] != review:
                        current.update(state="CANCELLED", result=None)
                    else:
                        current.update(state="COMPLETED", stage="COMPLETE", result=asdict(result))
                    self.event(current, current["state"])

                self.mutate(idea_id, success)
                return
            feedback.append(
                {
                    "attempt": number,
                    "plan": plan,
                    "run_status": result.run_status,
                    "reason": result.reason,
                    "lean_output": (directory / "stdout.log").read_text(errors="replace")[-3500:],
                }
            )
            if result.run_status.value in ("POLICY_BLOCKED", "CANCELLED", "EXECUTION_ERROR"):
                break

        def unknown(current):
            current.update(
                state="CANCELLED" if current["cancel_requested"] else "COMPLETED",
                stage="COMPLETE",
                result=None,
            )
            self.event(
                current, current["state"], reason="Bounded search ended without verified evidence"
            )

        self.mutate(idea_id, unknown)

    def work_once(self):
        with self.tasks.worker_lock() as lock_fd:
            with self.transaction() as db:
                queued = None
                for row in db.execute("SELECT * FROM ideas ORDER BY rowid"):
                    body = self.read_row(row)
                    if body["state"] == "RUNNING":
                        body.update(state="FAILED", result=None, error="INTERRUPTED")
                        self.event(
                            body, "INTERRUPTED", reason="No evidence recovered from saved reports"
                        )
                        db.execute(
                            "UPDATE ideas SET body=?, body_hash=? WHERE id=?",
                            (canonical(body).decode(), digest(body), body["idea_id"]),
                        )
                    elif body["state"] == "QUEUED" and queued is None:
                        queued = body
                if queued is None:
                    return {"worker_state": "IDLE"}
                queued["state"] = "RUNNING"
                self.event(queued, "RUNNING", operation=queued["operation"])
                db.execute(
                    "UPDATE ideas SET body=?, body_hash=? WHERE id=?",
                    (canonical(queued).decode(), digest(queued), queued["idea_id"]),
                )
            idea_id = queued["idea_id"]
            try:
                config = ModelConfig.load(self.root)
                if config is None or config.fingerprint != queued["model_config_fingerprint"]:
                    raise ValueError(
                        "MODEL_CONFIG_CHANGED: regenerate or reconfirm with the current configuration"
                    )
                service = (
                    self.service_factory(config)
                    if self.service_factory
                    else ChatModelService(
                        self.root,
                        config,
                        cancel_requested=lambda: self.cancelled(idea_id),
                        lifecycle_lock_fd=lock_fd,
                    )
                )
                if self.cancelled(idea_id):
                    raise InterruptedError("Idea cancelled")
                if queued["operation"] == "formalize":
                    self.formalize(idea_id, service)
                elif queued["operation"] == "search":
                    self.search(idea_id, service, lock_fd)
                else:
                    raise ValueError("Unknown idea operation")
            except (
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                TypeError,
                RecursionError,
                sqlite3.Error,
            ) as error:
                cancelled = isinstance(error, InterruptedError)
                error_code = (
                    "TIMEOUT"
                    if isinstance(error, TimeoutError)
                    else "POLICY_BLOCKED"
                    if isinstance(error, ValueError)
                    else "EXECUTION_ERROR"
                )

                def fail(body):
                    body.update(
                        state="CANCELLED" if body["cancel_requested"] or cancelled else "FAILED",
                        result=None,
                        error=error_code,
                    )
                    self.event(body, body["state"], error=body["error"])

                self.mutate(idea_id, fail)
            return {"worker_state": "EXECUTED", "idea": self.report(idea_id)}

    def report(self, idea_id):
        body = self.raw(idea_id)
        result = body["result"] if body["state"] == "COMPLETED" and body["review"] else None
        artifact_status = "NONE"
        if result:
            latest = body["attempts"][-1]
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
        targets = [
            {
                "target": value,
                "target_fingerprint": NatTarget.from_dict(value).fingerprint,
                "formal_statement": NatTarget.from_dict(value)
                .contract("prove")[0]
                .formal_statement,
            }
            for value in body["candidates"]
        ]
        return {
            **body,
            "candidates": targets,
            "result": result,
            "artifact_status": artifact_status,
            "math_verdict": result["math_verdict"]
            if result
            else "NEEDS_CLARIFICATION"
            if body["state"] == "NEEDS_CLARIFICATION"
            else "UNKNOWN",
            "evidence_level": result["evidence_level"] if result else "E0_CANDIDATE",
            "run_status": result["run_status"]
            if result
            else "CANCELLED"
            if body["state"] == "CANCELLED"
            else body["error"],
            "model_status": model_status(self.root),
            "cost": None,
            "historical_record": True,
            "fresh_attestation": False,
        }

    def evidence(self, idea_id):
        report = self.report(idea_id)
        if report["artifact_status"] != "MATCHES_RECORDED_HASHES":
            return {
                "available": False,
                "artifact_status": report["artifact_status"],
                "fresh_attestation": False,
            }
        attempt = report["attempts"][-1]
        directory = Path(attempt["result"]["artifact_directory"])
        result = {"available": True, "fresh_attestation": False}
        for filename, key in (("receipt.json", "receipt"), ("report.json", "report")):
            path = directory / filename
            raw = path.read_bytes()
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(self.root / "artifacts/runs")
                or digest_bytes(raw) != attempt["file_hashes"][filename]
            ):
                raise ValueError("Artifact changed; reload the idea report")
            result[key] = json.loads(raw)
        return result


def digest_bytes(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()
