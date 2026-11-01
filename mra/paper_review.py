"""Version-bound, manually annotated single-theorem review using real Nat checks."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import Alignment, canonical, digest
from .evidence import EvidenceGate
from .formalizer import SourceClaim, exact_object, strict_json, strings
from .integrity import ContentSnapshot, file_hash
from .model_service import integer_field, text_field
from .nat_ideas import NatTarget, IntTarget, target_from_dict, render_formula, validate_plan
from .tasks import TaskStore, _identifier, _now
from .verifier import NativeLeanBackend

SCHEMA = "nat-paper-review-v1"
INT_SCHEMA = "int-paper-review-v1"
ACTIVE = ("QUEUED", "RUNNING")


def prepare_review(bundle):
    """Compile visible inference obligations, with no model or Lean invocation."""
    exact_object(
        bundle,
        {
            "schema",
            "document",
            "variables",
            "assumptions",
            "conclusion",
            "claim_span",
            "claim_plan",
            "steps",
        },
        "Paper annotations",
    )
    if bundle["schema"] not in (SCHEMA, INT_SCHEMA) or len(canonical(bundle)) > 65536:
        raise ValueError("Unsupported or oversized single-theorem annotations")
    target_class = IntTarget if bundle["schema"] == INT_SCHEMA else NatTarget
    domain = target_class.DOMAIN
    document = exact_object(bundle["document"], {"id", "version", "title", "text"}, "Document")
    _identifier(document["id"])
    integer_field(document["version"], "document version", 1, 1000000)
    text_field(document["title"], "title", 200)
    text_field(document["text"], "document text", 12000)
    lines = document["text"].splitlines()
    variables = strings(bundle["variables"], "variables", 4)
    if len(set(variables)) != len(variables):
        raise ValueError("Variables must be unique")
    assumptions = bundle["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 4:
        raise ValueError("Expected at most four explicit theorem assumptions")

    def close(formula, premises=()):
        for premise in reversed(premises):
            formula = {"op": "imp", "left": premise, "right": formula}
        for name in reversed(variables):
            formula = {"op": "forall", "name": name, "body": formula}
        render_formula(formula, domain)
        return formula

    for assumption in assumptions:
        close(assumption)
    close(bundle["conclusion"])
    fingerprint = digest(bundle)

    def obligation(key, span, formula, plan, dependencies):
        if not isinstance(span, list) or len(span) != 2:
            raise ValueError("Source span must contain first and last line numbers")
        first = integer_field(span[0], "first line", 1, len(lines))
        last = integer_field(span[1], "last line", first, len(lines))
        quote = "\n".join(lines[first - 1 : last])
        text_field(quote, "source quote", 8000)
        source = SourceClaim(
            "paper-" + fingerprint[:24] + "-" + key,
            document["version"],
            quote,
            ("Document fingerprint: " + digest(document), "Obligation: " + key),
        )
        target = target_class.create(
            source, formula, f"人工标注的{domain}命题及原文对应；须逐项具名确认。"
        )
        return {
            "key": key,
            "span": span,
            "quote": quote,
            "dependencies": dependencies,
            "target": target.to_dict(),
            "target_fingerprint": target.fingerprint,
            "formal_statement": target.contract("prove")[0].formal_statement,
            "plan": validate_plan(plan, target) if plan is not None else None,
        }

    obligations = [
        obligation(
            "claim",
            bundle["claim_span"],
            close(bundle["conclusion"], assumptions),
            bundle["claim_plan"],
            [],
        )
    ]
    if not isinstance(bundle["steps"], list) or len(bundle["steps"]) > 4:
        raise ValueError("Expected at most four authored proof steps")
    previous = {}
    for step in bundle["steps"]:
        exact_object(step, {"id", "span", "formula", "depends_on", "plan"}, "Author step")
        key = _identifier(step["id"])
        if key == "claim" or key in previous or len(key) > 32:
            raise ValueError("Step IDs must be unique and cannot be claim")
        dependencies = strings(step["depends_on"], "step dependencies", 4)
        if len(set(dependencies)) != len(dependencies) or any(
            key not in previous for key in dependencies
        ):
            raise ValueError(
                "Only earlier annotated steps may be cited; external facts and cycles are unsupported"
            )
        close(step["formula"])
        formula = close(step["formula"], [*assumptions, *(previous[key] for key in dependencies)])
        obligations.append(obligation(key, step["span"], formula, step["plan"], list(dependencies)))
        previous[key] = step["formula"]
    plan = {
        "schema": bundle["schema"],
        "domain": domain,
        "bundle_fingerprint": fingerprint,
        "implementation_sha256": file_hash(Path(__file__)),
        "document": document,
        "variables": list(variables),
        "assumptions": [render_formula(close(item), domain) for item in assumptions],
        "obligations": obligations,
        "conclusion_reached": bool(bundle["steps"])
        and bundle["steps"][-1]["formula"] == bundle["conclusion"],
        "budget": {
            "maximum_checks": len(obligations),
            "worker_seconds_per_check": 30,
            "model_calls": 0,
        },
        "scope": f"manually-annotated-single-{domain}-theorem; source line mapping is a local named assertion",
    }
    return {**plan, "plan_fingerprint": digest(plan)}


class PaperReviewStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/paper-reviews"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.is_symlink() or self.base.resolve() != self.base:
            raise ValueError("Paper review directory must not be redirected")
        self.path = self.base / "reviews.sqlite3"
        self.tasks = TaskStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL, body_hash TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.path.is_symlink():
            raise ValueError("Paper review database must not be redirected")
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
            raise ValueError("Unknown paper review ID")
        body = strict_json(row["body"])
        if digest(body) != row["body_hash"]:
            raise ValueError("Stored paper review changed")
        return body

    @staticmethod
    def event(body, kind, **details):
        body["events"].append(
            {"sequence": len(body["events"]) + 1, "kind": kind, "at": _now(), **details}
        )

    def raw(self, review_id):
        with self.transaction(False) as db:
            return self.read_row(
                db.execute("SELECT * FROM reviews WHERE id=?", (_identifier(review_id),)).fetchone()
            )

    def mutate(self, review_id, action):
        with self.transaction() as db:
            body = self.read_row(
                db.execute("SELECT * FROM reviews WHERE id=?", (_identifier(review_id),)).fetchone()
            )
            action(body)
            db.execute(
                "UPDATE reviews SET body=?, body_hash=? WHERE id=?",
                (canonical(body).decode(), digest(body), review_id),
            )
        return body

    def submit(self, data):
        exact_object(
            data,
            {"request_id", "bundle", "plan_fingerprint", "reviewer", "confirmed"},
            "Paper review submission",
        )
        _identifier(data["request_id"])
        text_field(data["reviewer"], "reviewer", 200)
        plan = prepare_review(data["bundle"])
        if data["confirmed"] is not True or data["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError(
                "Explicit confirmation must match the displayed source, annotations, dependencies and plans"
            )
        fingerprint = digest(data)
        snapshot = ContentSnapshot.load_runtime(self.root).fingerprint
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM reviews WHERE request_id=?", (data["request_id"],)
            ).fetchone()
            if row:
                if row["request_hash"] != fingerprint:
                    raise ValueError("Request ID already belongs to different paper review content")
                review_id = row["id"]
            else:
                review_id = "paper-" + uuid.uuid4().hex
                body = {
                    "schema": data["bundle"]["schema"],
                    "review_id": review_id,
                    "request_id": data["request_id"],
                    "bundle": data["bundle"],
                    "plan": plan,
                    "created_at": _now(),
                    "confirmation": {
                        "reviewer": data["reviewer"],
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "at": _now(),
                        "actor_authentication": "local-assertion-only",
                    },
                    "runtime_snapshot_fingerprint": snapshot,
                    "state": "QUEUED",
                    "stage": "QUEUED",
                    "cancel_requested": False,
                    "revoked": False,
                    "checks": [],
                    "events": [],
                    "error": None,
                }
                self.event(body, "QUEUED")
                db.execute(
                    "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                    (
                        review_id,
                        data["request_id"],
                        fingerprint,
                        canonical(body).decode(),
                        digest(body),
                    ),
                )
        return self.report(review_id)

    def list_reviews(self):
        with self.transaction(False) as db:
            bodies = [
                self.read_row(row)
                for row in db.execute("SELECT * FROM reviews ORDER BY rowid DESC LIMIT 30")
            ]
        return [
            {
                "review_id": body["review_id"],
                "title": body["bundle"]["document"]["title"],
                "version": body["bundle"]["document"]["version"],
                "state": body["state"],
                "created_at": body["created_at"],
            }
            for body in bodies
        ]

    def cancel(self, review_id, *, revoke=False):
        def action(body):
            if revoke:
                body["revoked"] = True
            if body["state"] in ACTIVE:
                body["cancel_requested"] = True
                if body["state"] == "QUEUED":
                    body["state"] = "CANCELLED"
            elif revoke:
                body["state"] = "REVOKED"
            self.event(body, "CONFIRMATION_REVOKED" if revoke else "CANCELLATION_REQUESTED")

        self.mutate(review_id, action)
        return self.report(review_id)

    def cancelled(self, review_id):
        body = self.raw(review_id)
        return body["cancel_requested"] or body["revoked"]

    def checkpoint(self, review_id, key, stage, directory=None):
        def action(body):
            body["stage"] = key + ":" + stage
            self.event(body, stage, key=key, artifact_directory=directory)

        self.mutate(review_id, action)

    def execute(self, body, lock_fd):
        review_id = body["review_id"]
        plan = prepare_review(body["bundle"])
        if plan != body["plan"]:
            raise ValueError("Paper review protocol changed; prepare and confirm a new review")
        backend = NativeLeanBackend(self.root, 30)
        if backend.environment.runtime_digest != body["runtime_snapshot_fingerprint"]:
            raise ValueError("Runtime changed after paper review confirmation")
        gate = EvidenceGate(self.root, backend, backend.policy)
        proved = set()
        for item in plan["obligations"]:
            key = item["key"]
            if self.cancelled(review_id):
                raise InterruptedError("Paper review cancelled")
            if item["plan"] is None or any(
                dependency not in proved for dependency in item["dependencies"]
            ):
                self.mutate(
                    review_id,
                    lambda current: self.event(
                        current,
                        "OBLIGATION_SKIPPED",
                        key=key,
                        reason="No plan or an unverified step dependency",
                    ),
                )
                continue
            target = target_from_dict(item["target"])
            claim, formalization = target.contract(item["plan"]["direction"])
            checker = backend.check_int_plan if target.DOMAIN == "Int" else backend.check_nat_plan
            receipt = checker(
                target,
                item["plan"],
                cancel_requested=lambda: self.cancelled(review_id),
                checkpoint=lambda stage, directory: self.checkpoint(
                    review_id, key, stage, directory
                ),
                lifecycle_lock_fd=lock_fd,
            )
            alignment = Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                body["confirmation"]["reviewer"],
                "Explicit paper source/step alignment: "
                + plan["plan_fingerprint"]
                + "; obligation "
                + key,
            )
            result, evidence = gate.assess(claim, formalization, receipt, alignment)
            directory = Path(result.artifact_directory)
            report = {
                "review_id": review_id,
                "plan_fingerprint": plan["plan_fingerprint"],
                "obligation": item,
                "alignment": asdict(alignment),
                "task": asdict(result),
                "evidence": asdict(evidence) if evidence else None,
            }
            with (directory / "report.json").open("xb") as output:
                output.write(canonical(report))
            check = {
                "key": key,
                "target_fingerprint": target.fingerprint,
                "result": asdict(result),
                "file_hashes": {
                    name: file_hash(directory / name)
                    for name in (*receipt.payload["files"], "receipt.json", "report.json")
                },
            }
            self.mutate(review_id, lambda current: current["checks"].append(check))
            if self.cancelled(review_id):
                raise InterruptedError("Paper review cancelled before publication")
            if result.math_verdict.value == "PROVED":
                proved.add(key)
            if result.run_status.value in ("POLICY_BLOCKED", "EXECUTION_ERROR", "CANCELLED"):
                raise RuntimeError(
                    "Checker could not complete the paper obligation: " + result.run_status.value
                )

        def finish(current):
            current["state"] = (
                "CANCELLED" if current["cancel_requested"] or current["revoked"] else "COMPLETED"
            )
            current["stage"] = "FINISHED"
            self.event(current, current["state"])

        self.mutate(review_id, finish)

    def work_once(self, review_id=None):
        with self.tasks.worker_lock() as lock_fd:
            queued = None
            with self.transaction() as db:
                for row in db.execute("SELECT * FROM reviews ORDER BY rowid"):
                    body = self.read_row(row)
                    if body["state"] == "RUNNING":
                        body.update(state="FAILED", error="INTERRUPTED")
                        self.event(
                            body,
                            "INTERRUPTED",
                            reason="Saved checks are not republished after a crash",
                        )
                        db.execute(
                            "UPDATE reviews SET body=?, body_hash=? WHERE id=?",
                            (canonical(body).decode(), digest(body), body["review_id"]),
                        )
                    elif (
                        body["state"] == "QUEUED"
                        and queued is None
                        and (review_id is None or body["review_id"] == review_id)
                    ):
                        queued = body
                if queued is None:
                    return {"worker_state": "IDLE"}
                queued["state"] = "RUNNING"
                self.event(queued, "RUNNING")
                db.execute(
                    "UPDATE reviews SET body=?, body_hash=? WHERE id=?",
                    (canonical(queued).decode(), digest(queued), queued["review_id"]),
                )
            try:
                self.execute(queued, lock_fd)
            except (
                OSError,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                RecursionError,
                sqlite3.Error,
            ) as error:
                interrupted = isinstance(error, InterruptedError)
                error_code = (
                    "POLICY_BLOCKED" if isinstance(error, ValueError) else "EXECUTION_ERROR"
                )
                reason = str(error)

                def fail(current):
                    current["state"] = (
                        "CANCELLED"
                        if current["cancel_requested"] or current["revoked"] or interrupted
                        else "FAILED"
                    )
                    current["error"] = error_code
                    self.event(current, current["state"], reason=reason)

                self.mutate(queued["review_id"], fail)
            return {"worker_state": "EXECUTED", "review": self.report(queued["review_id"])}

    def report(self, review_id):
        body = self.raw(review_id)
        protocol_matches = prepare_review(body["bundle"]) == body["plan"]
        intact = protocol_matches
        for check in body["checks"]:
            directory = Path(check["result"]["artifact_directory"])
            try:
                intact = (
                    intact
                    and not directory.is_symlink()
                    and directory.resolve().is_relative_to(self.root / "artifacts/runs")
                    and all(
                        not (directory / name).is_symlink()
                        and (directory / name).resolve().is_relative_to(directory.resolve())
                        and file_hash(directory / name) == expected
                        for name, expected in check["file_hashes"].items()
                    )
                )
            except OSError:
                intact = False
        available = (
            intact
            and body["state"] == "COMPLETED"
            and not body["revoked"]
            and not body["cancel_requested"]
        )
        checks = {check["key"]: check for check in body["checks"]} if available else {}
        verdicts = {key: check["result"]["math_verdict"] for key, check in checks.items()}
        claim_verdict = verdicts.get("claim", "UNKNOWN")
        steps = body["plan"]["obligations"][1:]
        author_verdict = "UNKNOWN"
        diagnostics = []
        if available:
            for item in steps:
                verdict = verdicts.get(item["key"], "UNKNOWN")
                if verdict != "PROVED":
                    diagnostics.append(
                        {
                            "key": item["key"],
                            "span": item["span"],
                            "quote": item["quote"],
                            "formal_statement": item["formal_statement"],
                            "kind": "INFERENCE_REFUTED"
                            if verdict == "REFUTED"
                            else "UNRESOLVED_OBLIGATION",
                            "confidence": "KERNEL_CHECKED_WITH_LOCAL_ALIGNMENT"
                            if verdict == "REFUTED"
                            else "INCONCLUSIVE",
                            "message": "给定原文前提下，此步骤推理的否定已核验。"
                            if verdict == "REFUTED"
                            else "此义务尚未闭合；可能是策略能力、形式化或运行限制，不能据此认定作者错误。",
                        }
                    )
            if any(verdicts.get(item["key"]) == "REFUTED" for item in steps):
                author_verdict = "GAP_FOUND"
            elif steps and not body["plan"]["conclusion_reached"]:
                author_verdict = "GAP_FOUND"
                diagnostics.append(
                    {
                        "key": "conclusion",
                        "span": body["bundle"]["claim_span"],
                        "kind": "CONCLUSION_NOT_REACHED",
                        "confidence": "STRUCTURAL",
                        "message": "标注的最后一步没有陈述原结论；未宣称这是数学反例。",
                    }
                )
            elif steps and all(verdicts.get(item["key"]) == "PROVED" for item in steps):
                author_verdict = "PROVED"
        return {
            **body,
            "claim_verdict": claim_verdict,
            "author_proof_verdict": author_verdict,
            "diagnostics": diagnostics,
            "artifact_status": "CHANGED_OR_MISSING"
            if not intact
            else "MATCHES_RECORDED_HASHES"
            if body["checks"]
            else "NONE",
            "protocol_matches": protocol_matches,
            "results_available": available,
            "run_status": "CANCELLED"
            if body["state"] in ("CANCELLED", "REVOKED")
            else body["error"] or ("COMPLETED" if body["state"] == "COMPLETED" else None),
            "model_calls": 0,
            "cost": 0,
            "historical_record": True,
            "fresh_attestation": False,
            "scope": "Only the explicitly annotated single-theorem inference steps; not full-paper or authenticated review",
        }

    def evidence(self, review_id, key):
        report = self.report(review_id)
        if (
            not report["results_available"]
            or report["artifact_status"] != "MATCHES_RECORDED_HASHES"
        ):
            return {"available": False, "fresh_attestation": False}
        check = next((item for item in report["checks"] if item["key"] == key), None)
        if check is None:
            raise ValueError("Unknown or unchecked paper obligation")
        directory = Path(check["result"]["artifact_directory"])
        output = {"available": True, "fresh_attestation": False}
        for name in ("receipt.json", "report.json"):
            path = directory / name
            raw = path.read_bytes()
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(directory.resolve())
                or file_hash(path) != check["file_hashes"][name]
            ):
                raise ValueError("Paper review evidence changed during reading")
            output[name.removesuffix(".json")] = json.loads(raw)
        return output
