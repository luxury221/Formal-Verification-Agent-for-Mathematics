"""Confirmed, bounded graph scheduling over the existing real Lean review pipeline."""

from contextlib import contextmanager
import copy
from pathlib import Path
import sqlite3
import time
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import ContentSnapshot, file_hash
from .model_service import integer_field, text_field
from .paper_review import PaperReviewStore, prepare_review
from .research import ResearchStore, validate_nodes
from .tasks import _identifier, _now

SCHEMA = "controlled-research-run-v1"
ACTIVE = ("QUEUED", "RUNNING")
VISIBLE = ("RUNNING", "COMPLETED", "BUDGET_EXHAUSTED")


class ControlledReview(PaperReviewStore):
    """Keep graph children out of the general queue and defer result publication."""

    def __init__(self, parent, run_id):
        super().__init__(parent.root)
        self.parent, self.run_id = parent, run_id
        self.last_audit = 0.0

    def cancelled(self, review_id):
        if super().cancelled(review_id):
            return True
        body = self.parent.raw(self.run_id)
        if body["state"] != "RUNNING" or self.parent.expired(body):
            return True
        if time.monotonic() - self.last_audit >= 1:
            self.last_audit = time.monotonic()
            return self.parent.invalid_reason(body) is not None
        return False

    def mutate(self, review_id, action):
        def guarded(body):
            action(body)
            if body["state"] == "COMPLETED":
                body.update(state="RUNNING", stage="GRAPH_COMMIT_PENDING")
                body["events"][-1]["kind"] = "GRAPH_COMMIT_PENDING"

        return super().mutate(review_id, guarded)

    def checkpoint(self, review_id, key, stage, directory=None):
        super().checkpoint(review_id, key, stage, directory)

        def progress(body):
            if body["state"] == "RUNNING":
                body["stage"] = stage
                body["artifact_directory"] = directory

        self.parent.mutate(self.run_id, progress)


class ResearchRunStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-runs"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "runs.sqlite3"
        self.research = ResearchStore(self.root)
        self.papers = self.research.papers
        self.tasks = self.papers.tasks
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Research execution storage must not be redirected")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read(row):
        if row is None:
            raise ValueError("Unknown research execution ID")
        body = strict_json(row["body"])
        if digest(body) != row["hash"] or body["run_id"] != row["id"]:
            raise ValueError("Research execution ledger changed")
        return body

    @staticmethod
    def save(db, body):
        db.execute(
            "UPDATE runs SET body=?, hash=? WHERE id=?",
            (canonical(body).decode(), digest(body), body["run_id"]),
        )

    @staticmethod
    def event(body, kind, **details):
        body["events"].append({"at": _now(), "kind": kind, **details})

    def raw(self, run_id):
        with self.transaction() as db:
            return self.read(
                db.execute("SELECT * FROM runs WHERE id=?", (_identifier(run_id),)).fetchone()
            )

    def mutate(self, run_id, action):
        with self.transaction() as db:
            body = self.read(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            action(body)
            self.save(db, body)
        return body

    @staticmethod
    def graph_context(project, branch):
        return {
            "title": project["title"],
            "question": project["question"],
            "branch_id": branch["branch_id"],
            "label": branch["label"],
            "revision": branch["revision"],
            "nodes": branch["nodes"],
        }

    def prepare(self, project_id, data):
        exact_object(data, {"branch_id", "items", "budget"}, "Research execution plan")
        budget = exact_object(
            data["budget"], {"max_checks", "worker_seconds", "wall_seconds"}, "Total budget"
        )
        integer_field(budget["max_checks"], "maximum checks", 1, 8)
        integer_field(budget["worker_seconds"], "total worker seconds", 30, 240)
        integer_field(budget["wall_seconds"], "total wall seconds", 1, 600)
        items = data["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise ValueError("Select between one and eight mathematical nodes")
        project = self.research.report(project_id)
        branch = self.research.branch(project, data["branch_id"])
        nodes = {node["id"]: node for node in branch["nodes"]}
        selected = {}
        for item in items:
            exact_object(item, {"node_id", "plan"}, "Node execution plan")
            key = _identifier(item["node_id"])
            if key not in nodes or nodes[key]["kind"] == "definition" or key in selected:
                raise ValueError("Select unique typed mathematical nodes from this branch")
            selected[key] = item["plan"]
        external, obligations = {}, []
        for key in validate_nodes(branch["nodes"]):
            if key not in selected:
                continue
            node = nodes[key]
            for dependency in node["depends_on"]:
                if dependency in selected:
                    continue
                binding = branch["bindings"].get(dependency)
                if (
                    not binding
                    or binding["state"] != "ACTIVE"
                    or branch["node_states"][dependency]["math_verdict"] != "PROVED"
                ):
                    raise ValueError("Select unresolved prerequisite too: " + dependency)
                external[dependency] = digest(binding)
            bundle = self.research.export_node(project_id, branch["branch_id"], key)["bundle"]
            bundle["claim_plan"] = selected[key]
            review_plan = prepare_review(bundle)
            if review_plan["obligations"][0]["plan"] is None:
                raise ValueError("Each selected node needs a controlled checker plan")
            obligations.append(
                {"node_id": key, "node": node, "bundle": bundle, "review_plan": review_plan}
            )
        # Export performs its own audited reads; reject a graph changed during preparation.
        latest = self.research.report(project_id)
        latest_branch = self.research.branch(latest, branch["branch_id"])
        if self.graph_context(latest, latest_branch) != self.graph_context(project, branch):
            raise ValueError("Research graph changed during preview; prepare again")
        if any(digest(latest_branch["bindings"].get(k)) != v for k, v in external.items()):
            raise ValueError("Prerequisite evidence changed during preview")
        plan = {
            "schema": SCHEMA,
            "project_id": project_id,
            "branch_id": branch["branch_id"],
            "graph": self.graph_context(project, branch),
            "graph_fingerprint": digest(self.graph_context(project, branch)),
            "external_prerequisites": external,
            "items": obligations,
            "input": {
                "branch_id": data["branch_id"],
                "items": [
                    {"node_id": o["node_id"], "plan": o["bundle"]["claim_plan"]}
                    for o in obligations
                ],
                "budget": budget,
            },
            "budget": budget,
            "implementation_sha256": file_hash(Path(__file__)),
            "research_implementation_sha256": file_hash(Path(__file__).with_name("research.py")),
            "runtime_snapshot_fingerprint": ContentSnapshot.load_runtime(self.root).fingerprint,
            "runtime_snapshot_file_sha256": file_hash(self.root / ".runtime/runtime-snapshot.json"),
            "model_calls": 0,
            "scope": "Independent full targets; graph dependencies schedule checks and are never Lean assumptions; no automatic binding",
        }
        return {**plan, "plan_fingerprint": digest(plan)}

    def submit(self, project_id, data):
        exact_object(
            data,
            {"request_id", "input", "plan_fingerprint", "reviewer", "confirmed_nodes", "confirmed"},
            "Research execution submission",
        )
        _identifier(data["request_id"])
        text_field(data["reviewer"], "reviewer", 200)
        request_hash = digest({"project_id": project_id, "data": data})
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM runs WHERE request_id=?", (data["request_id"],)
            ).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise ValueError("Request ID belongs to different execution content")
            return self.report(old["id"])
        plan = self.prepare(project_id, data["input"])
        expected = [item["node_id"] for item in plan["items"]]
        if (
            data["confirmed"] is not True
            or data["confirmed_nodes"] != expected
            or data["plan_fingerprint"] != plan["plan_fingerprint"]
        ):
            raise ValueError("Confirm every displayed node, formula, order and total budget")
        with self.transaction() as db:
            # Repeat the idempotency lookup after preparation, including concurrent requests.
            old = db.execute(
                "SELECT * FROM runs WHERE request_id=?", (data["request_id"],)
            ).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("Request ID belongs to different execution content")
                run_id = old["id"]
            else:
                for row in db.execute("SELECT * FROM runs"):
                    active = self.read(row)
                    if (
                        active["state"] in ACTIVE
                        and active["plan"]["project_id"] == project_id
                        and active["plan"]["branch_id"] == plan["branch_id"]
                    ):
                        raise ValueError("This branch already has an active execution")
                run_id = "research-run-" + uuid.uuid4().hex
                body = {
                    "schema": SCHEMA,
                    "run_id": run_id,
                    "plan": plan,
                    "state": "QUEUED",
                    "stage": "QUEUED",
                    "created_at": _now(),
                    "started_at": None,
                    "deadline_monotonic": None,
                    "cancel_requested": False,
                    "error": None,
                    "confirmation": {
                        "reviewer": data["reviewer"],
                        "confirmed_nodes": expected,
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "actor_authentication": "local-assertion-only",
                    },
                    "used": {"checks": 0, "worker_seconds_reserved": 0},
                    "nodes": [
                        {
                            "node_id": key,
                            "review_id": "paper-graph-"
                            + digest({"run_id": run_id, "node_id": key})[:32],
                            "state": "PENDING",
                            "math_verdict": "UNKNOWN",
                            "source_fingerprint": None,
                            "reason": None,
                        }
                        for key in expected
                    ],
                    "events": [],
                }
                self.event(body, "QUEUED")
                db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                    (
                        run_id,
                        data["request_id"],
                        request_hash,
                        canonical(body).decode(),
                        digest(body),
                    ),
                )
        return self.report(run_id)

    @staticmethod
    def expired(body):
        return (
            body["deadline_monotonic"] is not None
            and time.monotonic() >= body["deadline_monotonic"]
        )

    def invalid_reason(self, body):
        plan = body["plan"]
        try:
            if (
                file_hash(Path(__file__)) != plan["implementation_sha256"]
                or file_hash(Path(__file__).with_name("research.py"))
                != plan["research_implementation_sha256"]
                or file_hash(self.root / ".runtime/runtime-snapshot.json")
                != plan["runtime_snapshot_file_sha256"]
            ):
                return "Execution protocol or runtime snapshot changed"
            project = self.research.report(plan["project_id"])
            branch = self.research.branch(project, plan["branch_id"])
            if digest(self.graph_context(project, branch)) != plan["graph_fingerprint"]:
                return "Confirmed research graph or original question changed"
            for key, fingerprint in plan["external_prerequisites"].items():
                if (
                    digest(branch["bindings"].get(key)) != fingerprint
                    or branch["node_states"][key]["math_verdict"] != "PROVED"
                ):
                    return "External prerequisite evidence changed: " + key
            for item in plan["items"]:
                if prepare_review(item["bundle"]) != item["review_plan"]:
                    return "Single-theorem verification protocol changed"
            for node in body["nodes"]:
                if node["state"] == "COMPLETED":
                    review = self.papers.report(node["review_id"])
                    if (
                        not review["results_available"]
                        or review["artifact_status"] != "MATCHES_RECORDED_HASHES"
                    ):
                        return "Node evidence changed: " + node["node_id"]
                    if (
                        node["source_fingerprint"] is not None
                        and digest(self.research.source(node["review_id"], "claim"))
                        != node["source_fingerprint"]
                    ):
                        return "Verified node source changed: " + node["node_id"]
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as error:
            return "Evidence audit failed: " + str(error)
        return None

    def revoke_child(self, review_id, reason):
        try:
            self.papers.raw(review_id)
        except ValueError as error:
            if str(error) == "Unknown paper review ID":
                return
            raise

        def revoke(child):
            child.update(revoked=True, cancel_requested=True, state="REVOKED", stage=reason)
            self.papers.event(child, "GRAPH_SOURCE_REVOKED", reason=reason)

        self.papers.mutate(review_id, revoke)

    def stop(self, run_id, state, reason, *, revoke_all=True):
        def action(body):
            if body["state"] in ("CANCELLED", "INVALIDATED", "INTERRUPTED"):
                return
            for node in body["nodes"]:
                if revoke_all or node["state"] == "RUNNING":
                    self.revoke_child(node["review_id"], state)
                if node["state"] in ("PENDING", "RUNNING"):
                    node.update(state=state, reason=reason, math_verdict="UNKNOWN")
            body.update(state=state, stage=state, error=reason)
            if state == "CANCELLED":
                body["cancel_requested"] = True
            self.event(body, state, reason=reason)

        return self.mutate(run_id, action)

    def cancel(self, run_id):
        self.stop(
            run_id, "CANCELLED", "User cancelled this execution and withdrew its generated sources"
        )
        return self.report(run_id)

    def report(self, run_id):
        body = self.raw(run_id)
        if body["state"] in (*ACTIVE, "COMPLETED", "BUDGET_EXHAUSTED"):
            reason = self.invalid_reason(body)
            if reason:
                body = self.stop(run_id, "INVALIDATED", reason)
        output = copy.deepcopy(body)
        for node in output["nodes"]:
            available = body["state"] in VISIBLE and node["state"] == "COMPLETED"
            node["results_available"] = available
            if not available:
                node["math_verdict"] = "UNKNOWN"
            node["evidence_level"] = (
                "E4_ALIGNED_AND_VERIFIED"
                if available and node["math_verdict"] in ("PROVED", "REFUTED")
                else "E0_NONE"
            )
        return {
            **output,
            "model_calls": 0,
            "cost": 0,
            "historical_record": True,
            "fresh_attestation": False,
            "auto_binding": False,
        }

    def list_runs(self, project_id):
        self.research.report(project_id)
        with self.transaction() as db:
            ids = [
                row["id"]
                for row in db.execute("SELECT * FROM runs ORDER BY rowid DESC")
                if self.read(row)["plan"]["project_id"] == project_id
            ][:30]
        return [self.report(run_id) for run_id in ids]

    def recover_locked(self):
        with self.transaction() as db:
            ids = [
                row["id"]
                for row in db.execute("SELECT * FROM runs")
                if self.read(row)["state"] == "RUNNING"
            ]
        for run_id in ids:
            self.stop(
                run_id,
                "INTERRUPTED",
                "Worker interrupted; retained artifacts are not republished; confirm a new execution",
            )
        return ids

    def recover(self):
        with self.tasks.worker_lock():
            return {"interrupted": self.recover_locked(), "automatic_retry": False}

    def create_child(self, body, item, node):
        now = _now()
        child = {
            "schema": item["bundle"]["schema"],
            "review_id": node["review_id"],
            "request_id": node["review_id"],
            "bundle": item["bundle"],
            "plan": item["review_plan"],
            "created_at": now,
            "confirmation": {
                "reviewer": body["confirmation"]["reviewer"],
                "plan_fingerprint": item["review_plan"]["plan_fingerprint"],
                "at": now,
                "actor_authentication": "local-assertion-only",
            },
            "runtime_snapshot_fingerprint": body["plan"]["runtime_snapshot_fingerprint"],
            "state": "RUNNING",
            "stage": "GRAPH_CHECK",
            "cancel_requested": False,
            "revoked": False,
            "checks": [],
            "events": [],
            "error": None,
            "research_run_id": body["run_id"],
        }
        self.papers.event(child, "GRAPH_RUNNING", run_id=body["run_id"], node_id=node["node_id"])
        with self.papers.transaction() as db:
            db.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                (
                    child["review_id"],
                    child["request_id"],
                    digest(child),
                    canonical(child).decode(),
                    digest(child),
                ),
            )
        return child

    def guard(self, run_id):
        body = self.raw(run_id)
        if body["state"] != "RUNNING":
            return False
        reason = self.invalid_reason(body)
        if reason:
            self.stop(run_id, "INVALIDATED", reason)
            return False
        if self.expired(body):
            self.stop(run_id, "BUDGET_EXHAUSTED", "Total wall time exhausted", revoke_all=False)
            return False
        return True

    def execute(self, run_id, lock_fd):
        for index, item in enumerate(self.raw(run_id)["plan"]["items"]):
            if not self.guard(run_id):
                return
            body = self.raw(run_id)
            states = {node["node_id"]: node for node in body["nodes"]}
            blocked = [
                key
                for key in item["node"]["depends_on"]
                if key in states and states[key]["math_verdict"] != "PROVED"
            ]
            if blocked:

                def skip(current):
                    if current["state"] == "RUNNING":
                        current["nodes"][index].update(
                            state="SKIPPED", reason="Unproved prerequisites: " + ", ".join(blocked)
                        )
                        self.event(
                            current, "NODE_SKIPPED", node_id=item["node_id"], blocked_by=blocked
                        )

                self.mutate(run_id, skip)
                continue
            budget = body["plan"]["budget"]
            if (
                body["used"]["checks"] >= budget["max_checks"]
                or body["used"]["worker_seconds_reserved"] + 30 > budget["worker_seconds"]
            ):
                self.stop(
                    run_id,
                    "BUDGET_EXHAUSTED",
                    "Total check or worker allocation exhausted",
                    revoke_all=False,
                )
                return
            child = None

            def reserve(current):
                nonlocal child
                if current["state"] != "RUNNING":
                    return
                current["used"]["checks"] += 1
                current["used"]["worker_seconds_reserved"] += 30
                current["nodes"][index]["state"] = "RUNNING"
                current["stage"] = "CHECKING:" + item["node_id"]
                self.event(current, "NODE_RESERVED", node_id=item["node_id"], worker_seconds=30)
                child = self.create_child(current, item, current["nodes"][index])

            self.mutate(run_id, reserve)
            if child is None:
                return
            error = None
            try:
                ControlledReview(self, run_id).execute(child, lock_fd)
            except (
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                TypeError,
                sqlite3.Error,
            ) as caught:
                error = str(caught)
            if not self.guard(run_id):
                return

            def publish(current):
                if current["state"] != "RUNNING":
                    return
                # This parent transaction serializes publication with cancellation.
                if self.expired(current) or self.invalid_reason(current):
                    return
                node = current["nodes"][index]
                raw = self.papers.raw(node["review_id"])
                if (
                    error is not None
                    or raw["revoked"]
                    or raw["cancel_requested"]
                    or raw["stage"] != "GRAPH_COMMIT_PENDING"
                ):
                    self.revoke_child(node["review_id"], "CHECK_FAILED")
                    node.update(state="FAILED", reason=error or "Child confirmation withdrawn")
                else:

                    def finish(review):
                        if (
                            not review["cancel_requested"]
                            and not review["revoked"]
                            and review["state"] == "RUNNING"
                        ):
                            review.update(state="COMPLETED", stage="FINISHED")
                            self.papers.event(review, "COMPLETED")

                    self.papers.mutate(node["review_id"], finish)
                    report = self.papers.report(node["review_id"])
                    if not report["results_available"]:
                        raise RuntimeError("Child evidence was not available for publication")
                    verdict = report["claim_verdict"]
                    source = (
                        self.research.source(node["review_id"], "claim")
                        if verdict in ("PROVED", "REFUTED")
                        else None
                    )
                    node.update(
                        state="COMPLETED",
                        math_verdict=verdict,
                        source_fingerprint=digest(source) if source else None,
                    )
                self.event(
                    current,
                    "NODE_FINISHED",
                    node_id=node["node_id"],
                    state=node["state"],
                    math_verdict=node["math_verdict"],
                )

            self.mutate(run_id, publish)
            if not self.guard(run_id):
                return
        if self.guard(run_id):

            def finish(current):
                if current["state"] == "RUNNING":
                    current.update(state="COMPLETED", stage="FINISHED")
                    self.event(current, "COMPLETED")

            self.mutate(run_id, finish)

    def work_once(self, run_id=None):
        if run_id is not None:
            _identifier(run_id)
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            with self.transaction() as db:
                queued = next(
                    (
                        self.read(row)
                        for row in db.execute("SELECT * FROM runs ORDER BY rowid")
                        if self.read(row)["state"] == "QUEUED"
                        and (run_id is None or row["id"] == run_id)
                    ),
                    None,
                )
                if queued is None:
                    return {"worker_state": "IDLE"}
                queued.update(
                    state="RUNNING",
                    stage="STARTING",
                    started_at=_now(),
                    deadline_monotonic=time.monotonic() + queued["plan"]["budget"]["wall_seconds"],
                )
                self.event(queued, "RUNNING")
                self.save(db, queued)
            try:
                self.execute(queued["run_id"], lock_fd)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.stop(queued["run_id"], "FAILED", str(error))
            return {"worker_state": "EXECUTED", "run": self.report(queued["run_id"])}
