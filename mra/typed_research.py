"""Project research graphs whose complete targets actually include typed definitions."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_lab import now
from .research import ResearchStore
from .research_flow import ResearchFlowStore, atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import TaskStore, WorkerBusy, _identifier
from .typed_definitions import TypedDefinitionStore, examples, manifest as typed_manifest
from .typed_terms import FIELDS, MODE, bounded_data

SCHEMA = "typed-project-research-v1"
LIMITS = {"graphs": 50, "nodes": 8, "attempts": 16, "worker_seconds": 480, "events": 512}
SOURCES = (
    "mra/typed_research.py",
    "mra/research_flow.py",
    "mra/research.py",
    "mra/search_evaluation.py",
    "mra/knowledge_instances.py",
)
ACTIVE = ("QUEUED", "RUNNING")
ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error)


def manifest(root):
    return {
        "schema": SCHEMA,
        "provider": typed_manifest(root),
        "files": {n: file_hash(root / n) for n in SOURCES},
    }


def source_identity(child):
    return digest({k: child[k] for k in ("plan", "alignment", "files", "result")})


class TypedGraphChecks(TypedDefinitionStore):
    def __init__(self, root, directory, create=False):
        self.root = Path(root).resolve()
        self.base = directory / "checks"
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Typed graph checks cannot be redirected")
        if create:
            self.base.mkdir(exist_ok=False)
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file():
            raise ValueError("Typed graph child ledger is unavailable")

    def preview(self, request):
        value = super().preview(request)
        value["source_manifest"] = {
            "graph": manifest(self.root),
            "provider": value["source_manifest"],
        }
        value.pop("plan_fingerprint")
        value["plan_fingerprint"] = digest(value)
        return value

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["graph"] != manifest(self.root):
            return "Typed graph adapter changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["provider"]},
        }
        return super().gate(view, check_files)


def child_preview(root, request):
    checker = object.__new__(TypedGraphChecks)
    checker.root = Path(root).resolve()
    return checker.preview(request)


def graph(root, definitions, nodes):
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= LIMITS["nodes"]:
        raise ValueError("A typed research task needs 1–8 complete nodes")
    by_id, compiled = {}, {}
    for node in nodes:
        exact_object(node, {"id", "kind", "depends_on", "target"}, "Typed research node")
        key = _identifier(node["id"])
        if len(key) > 32 or key in by_id or node["kind"] not in ("problem", "lemma", "subproblem"):
            raise ValueError("Use distinct short node IDs and a supported node kind")
        exact_object(
            node["target"], FIELDS - {"schema", "definitions"}, "Complete typed node target"
        )
        deps = node["depends_on"]
        if not isinstance(deps, list) or len(deps) > 7 or any(not isinstance(d, str) for d in deps):
            raise ValueError("Use at most seven distinct prerequisite IDs")
        if len(set(deps)) != len(deps):
            raise ValueError("Duplicate prerequisite")
        by_id[key] = node
        compiled[key] = child_preview(
            root, {"schema": MODE, "definitions": deepcopy(definitions), **node["target"]}
        )
    problems = [n["id"] for n in nodes if n["kind"] == "problem"]
    if len(problems) != 1:
        raise ValueError("Exactly one original problem is required")
    visiting, seen, order = set(), set(), []

    def visit(key):
        if key not in by_id or key in visiting:
            raise ValueError("Typed research prerequisites must form a complete acyclic graph")
        if key in seen:
            return
        visiting.add(key)
        for dep in by_id[key]["depends_on"]:
            visit(dep)
        visiting.remove(key)
        seen.add(key)
        order.append(key)

    visit(problems[0])
    if len(seen) != len(nodes):
        raise ValueError("Every node must belong to the original problem's prerequisite graph")
    return order, problems[0], compiled


class TypedResearchStore(ResearchFlowStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/typed-research"
        if self.base.resolve() != self.base:
            raise ValueError("Typed research storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        self.research = ResearchStore(self.root)

    def directory(self, graph_id):
        path = self.base / identifier(graph_id, "typedflow")
        if path.resolve() != path:
            raise ValueError("Typed research directory cannot be redirected")
        return path

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Typed research storage changed")
        values = sorted(
            p for p in self.base.iterdir() if re.fullmatch(r"typedflow-[a-f0-9]{32}", p.name)
        )
        if len(values) > LIMITS["graphs"]:
            raise ValueError("Typed research graph limit exceeded")
        return values

    @staticmethod
    def event(row, kind, **details):
        if len(row["events"]) >= LIMITS["events"]:
            raise ValueError("Typed research event budget exhausted")
        row["revision"] += 1
        row["events"].append({"at": now(), "kind": kind, **details})

    def read(self, graph_id):
        directory = self.directory(graph_id)
        try:
            row = _envelope(directory / "record.json")
            exact_object(
                row,
                {
                    "schema",
                    "graph_id",
                    "request_id",
                    "submission_fingerprint",
                    "created_at",
                    "plan_fingerprint",
                    "revision",
                    "invalidated",
                    "reason",
                    "attempts",
                    "events",
                },
                "Typed research record",
            )
            if (
                row["schema"] != SCHEMA
                or row["graph_id"] != graph_id
                or type(row["revision"]) is not int
                or type(row["invalidated"]) is not bool
                or not isinstance(row["attempts"], list)
                or len(row["attempts"]) > LIMITS["attempts"]
                or not isinstance(row["events"], list)
                or not 1 <= len(row["events"]) <= LIMITS["events"]
                or row["revision"] != len(row["events"])
            ):
                raise ValueError("Typed research record identity or bounds changed")
            identities, requests = set(), set()
            for attempt in row["attempts"]:
                exact_object(
                    attempt,
                    {
                        "attempt_id",
                        "request_id",
                        "node_id",
                        "submission_fingerprint",
                        "reviewer",
                        "execution_fingerprint",
                        "prerequisites",
                        "state",
                        "child_id",
                        "source_fingerprint",
                        "invalidated",
                        "reason",
                        "cancel_requested",
                    },
                    "Typed research attempt",
                )
                identifier(attempt["attempt_id"], "typedattempt")
                _identifier(attempt["request_id"])
                _identifier(attempt["node_id"])
                if (
                    attempt["attempt_id"] in identities
                    or attempt["request_id"] in requests
                    or type(attempt["invalidated"]) is not bool
                    or type(attempt["cancel_requested"]) is not bool
                    or not isinstance(attempt["prerequisites"], dict)
                    or not isinstance(attempt["child_id"], str)
                    or not isinstance(attempt["state"], str)
                ):
                    raise ValueError("Typed research attempt identity or shape changed")
                identities.add(attempt["attempt_id"])
                requests.add(attempt["request_id"])
            return row
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            atomic(directory / "invalidated.json", {"at": now(), "reason": str(error)})
            raise

    def save(self, row):
        atomic(self.directory(row["graph_id"]) / "record.json", row)

    def checker(self, graph_id):
        return TypedGraphChecks(self.root, self.directory(graph_id))

    def preview(self, raw):
        bounded_data(raw)
        exact_object(raw, {"project_id", "branch_id", "definitions", "nodes"}, "Typed project task")
        order, problem, plans = graph(self.root, raw["definitions"], raw["nodes"])
        value = {
            "schema": SCHEMA,
            "request": deepcopy(raw),
            "parent": self.parent(raw["project_id"], raw["branch_id"]),
            "order": order,
            "problem_id": problem,
            "node_plans": plans,
            "source_manifest": manifest(self.root),
            "limits": dict(LIMITS),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "limitations": [
                "Shared definitions are included in each complete checked theorem.",
                "Prerequisites gate scheduling; they are never added as assumptions or imported proof terms.",
                "Every node and the original problem need their own exact real check and independent replay.",
                "One named check at a time, at most sixteen checks and 480 reserved worker seconds; no automatic retries.",
                "Same kernel implementation; local names are assertions, not authenticated identities.",
                "Manual graph and semantic alignment; no model, arbitrary Lean, or automatic legacy graph migration.",
            ],
        }
        value["plan_fingerprint"] = digest(value)
        return value

    def create(self, raw):
        exact_object(
            raw, {"request_id", "request", "plan_fingerprint"}, "Save typed research graph"
        )
        _identifier(raw["request_id"])
        plan = self.preview(raw["request"])
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Full graph preview changed; review all definitions and targets again")
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                try:
                    old = self.read(path.name)
                except ERRORS:
                    continue
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID is bound to another typed graph")
                    key = old["graph_id"]
                    break
            else:
                if len(paths) >= LIMITS["graphs"]:
                    raise ValueError("Export before saving more than fifty typed graphs")
                key = "typedflow-" + uuid.uuid4().hex
                directory = self.directory(key)
                directory.mkdir()
                atomic(directory / "plan.json", plan)
                TypedGraphChecks(self.root, directory, create=True)
                row = {
                    "schema": SCHEMA,
                    "graph_id": key,
                    "request_id": raw["request_id"],
                    "submission_fingerprint": digest(raw),
                    "created_at": now(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "revision": 0,
                    "invalidated": False,
                    "reason": None,
                    "attempts": [],
                    "events": [],
                }
                self.event(row, "SAVED_WITHOUT_LEAN")
                self.save(row)
        return self.report(key)

    def _audit(self, row):
        directory = self.directory(row["graph_id"])
        plan = _envelope(directory / "plan.json")
        if (
            plan["schema"] != SCHEMA
            or plan["plan_fingerprint"] != row["plan_fingerprint"]
            or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
            != row["plan_fingerprint"]
        ):
            raise ValueError("Typed graph plan fingerprint mismatch")
        reason = None
        try:
            if (directory / "invalidated.json").exists():
                reason = "Previously detected graph damage"
            elif plan["source_manifest"] != manifest(self.root):
                reason = "Typed graph implementation changed"
            elif plan["parent"] != self.parent(
                plan["parent"]["project_id"], plan["parent"]["branch_id"]
            ):
                reason = "Original project question or branch context changed"
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            reason = "Original context unavailable: " + str(error)
        if reason and not row["invalidated"]:
            row.update(invalidated=True, reason=reason)
            self.event(row, "GRAPH_INVALIDATED", reason=reason)
        by_id = {n["id"]: n for n in plan["request"]["nodes"]}
        states, checks = {}, {}
        checker = self.checker(row["graph_id"])
        for key in plan["order"]:
            node = by_id[key]
            deps = {d: states[d]["source_fingerprint"] for d in node["depends_on"]}
            blocked = [d for d in node["depends_on"] if states[d]["math_verdict"] != "PROVED"]
            valid, active = [], []
            for attempt in (a for a in row["attempts"] if a["node_id"] == key):
                why = None
                if row["invalidated"] or blocked or attempt["prerequisites"] != deps:
                    why = "Original context or concrete prerequisite evidence changed"
                try:
                    child = checker.report(attempt["child_id"])
                    checks[attempt["attempt_id"]] = child
                    if child["plan"] != plan["node_plans"][key] or child["invalidated"]:
                        why = "Complete typed child target or evidence became unavailable"
                    if attempt["state"] == "COMPLETED" and not why and not attempt["invalidated"]:
                        if (child["state"], child["math_verdict"], child["evidence_level"]) != (
                            "COMPLETED",
                            "PROVED",
                            "E4",
                        ) or source_identity(child) != attempt["source_fingerprint"]:
                            why = "No exact current PROVED/E4 child evidence"
                        else:
                            valid.append(attempt)
                    if (
                        attempt["state"] in ACTIVE
                        and not attempt["cancel_requested"]
                        and not attempt["invalidated"]
                        and not why
                    ):
                        active.append(attempt)
                    if why and child["state"] in ACTIVE:
                        checker.cancel(child["run_id"])
                except ERRORS as error:
                    if isinstance(error, sqlite3.Error) and storage_busy(error):
                        raise
                    why = "Child evidence unavailable: " + str(error)
                if why and not attempt["invalidated"]:
                    attempt.update(invalidated=True, reason=why)
                    self.event(
                        row, "ATTEMPT_INVALIDATED", attempt_id=attempt["attempt_id"], reason=why
                    )
            best = valid[-1] if valid else None
            states[key] = {
                "id": key,
                "math_verdict": "PROVED" if best else "UNKNOWN",
                "evidence_level": "E4" if best else "E0",
                "blocked_by": blocked,
                "status": "INVALIDATED"
                if row["invalidated"]
                else "COMPLETED"
                if best
                else active[-1]["state"]
                if active
                else "BLOCKED"
                if blocked
                else "READY",
                "best_attempt": best["attempt_id"] if best else None,
                "source_fingerprint": digest(
                    {"attempt_id": best["attempt_id"], "source": best["source_fingerprint"]}
                )
                if best
                else None,
            }
        return plan, states, checks

    def _view(self, row, plan, states, checks):
        problem = states[plan["problem_id"]]
        return {
            **deepcopy(row),
            "plan": plan,
            "node_states": states,
            "checks": checks,
            "math_verdict": problem["math_verdict"],
            "evidence_level": problem["evidence_level"],
            "fresh_attestation": False,
            "model_calls": 0,
            "checks_reserved": len(row["attempts"]),
            "worker_seconds_reserved": sum(
                plan["node_plans"][a["node_id"]]["request"]["timeout_seconds"]
                for a in row["attempts"]
            ),
        }

    @contextmanager
    def audited(self, graph_id):
        directory = self.directory(graph_id)
        with lease(directory):
            row = self.read(graph_id)
            before = deepcopy(row)
            try:
                plan, states, checks = self._audit(row)
            except ERRORS as error:
                if isinstance(error, sqlite3.Error) and storage_busy(error):
                    raise
                atomic(directory / "invalidated.json", {"at": now(), "reason": str(error)})
                raise
            try:
                yield row, plan, states, checks
            finally:
                if row != before:
                    self.save(row)

    def report(self, graph_id):
        with self.audited(graph_id) as values:
            return self._view(*values)

    @contextmanager
    def worker_audited(self, graph_id):
        """Wait for a reader while retaining the global checker lease.

        Retry only context entry, never a yielded mutation or a Lean check.
        A genuine failure still leaves RUNNING for conservative recovery.
        """
        deadline = time.monotonic() + 30
        with ExitStack() as stack:
            while True:
                try:
                    values = stack.enter_context(self.audited(graph_id))
                    break
                except WorkerBusy:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            yield values

    @staticmethod
    def execution_preview(row, plan, states, node_id):
        node = next((n for n in plan["request"]["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise ValueError("Unknown typed research node")
        if row["invalidated"] or states[node_id]["blocked_by"]:
            raise ValueError("Every prerequisite needs current exact PROVED/E4 evidence")
        value = {
            "graph_id": row["graph_id"],
            "graph_fingerprint": plan["plan_fingerprint"],
            "node_id": node_id,
            "prerequisites": {d: states[d]["source_fingerprint"] for d in node["depends_on"]},
            "child_plan": deepcopy(plan["node_plans"][node_id]),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        value["execution_fingerprint"] = digest(value)
        return value

    def preview_node(self, graph_id, data):
        exact_object(data, {"node_id"}, "Typed node preview")
        with self.audited(graph_id) as (row, plan, states, _):
            return self.execution_preview(row, plan, states, data["node_id"])

    def submit(self, graph_id, data):
        exact_object(
            data,
            {"request_id", "node_id", "execution_fingerprint", "reviewer", "confirmed"},
            "Typed node submission",
        )
        _identifier(data["request_id"])
        from .number_theory_terms import text

        reviewer = text(data["reviewer"], 200, "Named semantic reviewer")
        if data["confirmed"] is not True:
            raise ValueError(
                "Review all definitions, variable types, original conditions and budget"
            )
        with self.audited(graph_id) as (row, plan, states, _):
            for attempt in row["attempts"]:
                if attempt["request_id"] == data["request_id"]:
                    if attempt["submission_fingerprint"] != digest(data):
                        raise ValueError("Request ID belongs to different named node confirmation")
                    return deepcopy(attempt)
            current = self.execution_preview(row, plan, states, data["node_id"])
            if current["execution_fingerprint"] != data["execution_fingerprint"]:
                raise ValueError("Complete typed node preview or prerequisite identities changed")
            if any(a["state"] in ACTIVE and not a["invalidated"] for a in row["attempts"]):
                raise ValueError("Finish or cancel the active typed check first")
            if states[data["node_id"]]["math_verdict"] == "PROVED":
                raise ValueError("Node already has current complete evidence")
            if len(row["attempts"]) >= LIMITS["attempts"]:
                raise ValueError("Typed graph check budget exhausted")
            key = "typedattempt-" + uuid.uuid4().hex
            checker = self.checker(graph_id)
            with checker.transaction(False) as db:
                if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 32:
                    raise ValueError("Typed graph child storage budget exhausted")
            child = checker.submit(
                {
                    "request_id": key,
                    "request": current["child_plan"]["request"],
                    "plan_fingerprint": current["child_plan"]["plan_fingerprint"],
                    "reviewer": reviewer,
                    "confirmed": True,
                }
            )
            attempt = {
                "attempt_id": key,
                "request_id": data["request_id"],
                "node_id": data["node_id"],
                "submission_fingerprint": digest(data),
                "reviewer": reviewer,
                "execution_fingerprint": current["execution_fingerprint"],
                "prerequisites": current["prerequisites"],
                "state": "QUEUED",
                "child_id": child["run_id"],
                "source_fingerprint": None,
                "invalidated": False,
                "reason": None,
                "cancel_requested": False,
            }
            row["attempts"].append(attempt)
            self.event(row, "NAMED_CHECK_QUEUED", attempt_id=key)
            return deepcopy(attempt)

    def cancel(self, graph_id, data):
        exact_object(data, {"attempt_id"}, "Cancel typed check")
        with self.audited(graph_id) as (row, _, _, _):
            attempt = next(
                (a for a in row["attempts"] if a["attempt_id"] == data["attempt_id"]), None
            )
            if attempt is None:
                raise ValueError("Unknown typed research attempt")
            if attempt["state"] in ACTIVE:
                self.checker(graph_id).cancel(attempt["child_id"])
                attempt["cancel_requested"] = True
                if attempt["state"] == "QUEUED":
                    attempt["state"] = "CANCELLED"
                self.event(row, "CANCELLATION_REQUESTED", attempt_id=attempt["attempt_id"])
        return self.report(graph_id)

    def withdraw(self, graph_id, data):
        exact_object(
            data, {"node_id", "reviewer", "reason", "confirmed"}, "Withdraw typed node evidence"
        )
        from .number_theory_terms import text

        reviewer = text(data["reviewer"], 200, "Reviewer")
        reason = text(data["reason"], 500, "Reason")
        if data["confirmed"] is not True:
            raise ValueError("Explicit withdrawal confirmation required")
        with self.audited(graph_id) as (row, plan, _, _):
            if data["node_id"] not in plan["order"]:
                raise ValueError("Unknown typed research node")
            for attempt in row["attempts"]:
                if attempt["node_id"] == data["node_id"] and not attempt["invalidated"]:
                    self.checker(graph_id).revoke(
                        attempt["child_id"],
                        {"reviewer": reviewer, "reason": reason, "confirmed": True},
                    )
                    attempt.update(invalidated=True, reason=reason)
            self.event(
                row,
                "NODE_EVIDENCE_WITHDRAWN",
                node_id=data["node_id"],
                reviewer=reviewer,
                reason=reason,
            )
            self._audit(row)
        return self.report(graph_id)

    def catalog(self, project_id, branch_id):
        parent = self.parent(project_id, branch_id)
        values = []
        for path in self.paths():
            try:
                row = self.read(path.name)
                plan = _envelope(path / "plan.json")
            except ERRORS:
                continue
            if (plan["parent"]["project_id"], plan["parent"]["branch_id"]) == (
                project_id,
                branch_id,
            ):
                values.append(
                    {
                        "graph_id": path.name,
                        "created_at": row["created_at"],
                        "invalidated": row["invalidated"],
                        "nodes": len(plan["order"]),
                    }
                )
        return {
            "schema": SCHEMA,
            "parent": parent,
            "graphs": values,
            "example": example(),
            "limits": dict(LIMITS),
        }

    def recover_locked(self):
        interrupted = []
        for directory in self.paths():
            try:
                # Owning the global worker lock proves that no live checker is
                # entitled to this ledger. Recovery only demotes; complete
                # mathematical audits belong to reports and execution gates.
                with lease(directory):
                    row = self.read(directory.name)
                    before = deepcopy(row)
                    checker = self.checker(directory.name)
                    linked = {a["child_id"] for a in row["attempts"]}
                    abandoned = {a["child_id"] for a in row["attempts"] if a["state"] == "RUNNING"}
                    with checker.transaction() as db:
                        for raw in db.execute("SELECT body,sha256 FROM runs").fetchall():
                            child = checker.decode(raw)
                            if child["state"] == "QUEUED" and (
                                child["run_id"] not in linked or child["run_id"] in abandoned
                            ):
                                child.update(
                                    state="CANCELLED", stage="CANCELLED", cancel_requested=True
                                )
                                checker.save(db, child)
                    checker.recover_locked()
                    for attempt in row["attempts"]:
                        if attempt["state"] == "RUNNING":
                            attempt.update(
                                state="INTERRUPTED",
                                reason="Supervisor stopped; no automatic retry or promotion",
                            )
                            self.event(row, "CHECK_INTERRUPTED", attempt_id=attempt["attempt_id"])
                            interrupted.append(attempt["attempt_id"])
                    if row != before:
                        self.save(row)
            except ERRORS as error:
                if isinstance(error, sqlite3.Error) and storage_busy(error):
                    raise
                # Damaged graphs stay quarantined; they must not block unrelated work.
                continue
        return interrupted

    def execute(self, graph_id, attempt_id, lock_fd):
        checker = self.checker(graph_id)
        with self.worker_audited(graph_id) as (row, _, _, _):
            attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            child_id = attempt["child_id"]
        with checker.transaction() as db:
            child = checker.read(child_id, db)
            if child["state"] == "QUEUED":
                child.update(state="RUNNING", stage="CHECKING")
                checker.event(child, "STARTED")
                checker.save(db, child)
        result = checker.execute(child_id, lock_fd)
        with self.worker_audited(graph_id) as (row, _, _, _):
            attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            if attempt["cancel_requested"]:
                attempt["state"] = "CANCELLED"
            elif attempt["invalidated"] or row["invalidated"]:
                attempt["state"] = "POLICY_BLOCKED"
            else:
                attempt["state"] = result["state"]
                if result["math_verdict"] == "PROVED" and result["state"] == "COMPLETED":
                    attempt["source_fingerprint"] = source_identity(result)
            self.event(row, "CHECK_FINISHED", attempt_id=attempt_id, state=attempt["state"])
            self._audit(row)
        return self.report(graph_id)

    def work_once(self, graph_id=None):
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            paths = [self.directory(graph_id)] if graph_id else self.paths()
            for directory in paths:
                try:
                    raw = self.read(directory.name)
                except ERRORS as error:
                    if isinstance(error, sqlite3.Error) and storage_busy(error):
                        raise
                    continue
                if not any(
                    a["state"] == "QUEUED" and not a["invalidated"] for a in raw["attempts"]
                ):
                    continue
                try:
                    with self.audited(directory.name) as (row, _, _, _):
                        attempt = next(
                            (
                                a
                                for a in row["attempts"]
                                if a["state"] == "QUEUED" and not a["invalidated"]
                            ),
                            None,
                        )
                        if attempt is None or row["invalidated"]:
                            continue
                        attempt["state"] = "RUNNING"
                        self.event(row, "CHECK_STARTED", attempt_id=attempt["attempt_id"])
                        key = attempt["attempt_id"]
                except ERRORS as error:
                    if isinstance(error, sqlite3.Error) and storage_busy(error):
                        raise
                    continue
                self.execute(directory.name, key, lock_fd)
                return {"worker_state": "EXECUTED", "graph_id": directory.name, "attempt_id": key}
        return {"worker_state": "IDLE"}


def example():
    values = examples()
    definitions = [values[2]["definitions"][0], *values[4]["definitions"]]
    seed = {k: deepcopy(v) for k, v in values[2].items() if k not in ("schema", "definitions")}
    q, n = {"var": "q"}, {"var": "n"}
    lift = {"call": {"name": "lift", "args": [n]}}

    def square(x):
        return {"call": {"name": "square", "args": [x]}}

    problem = {
        **seed,
        "title": "自然数参数的有理数平方展开",
        "original": "对任意自然数n、有理数q，square(q+lift(n))=square(q)+2*q*lift(n)+square(lift(n))。",
        "variables": [{"name": "n", "type": "Nat"}, {"name": "q", "type": "Rat"}],
        "goal": {
            "eq": [
                square({"add": [q, lift]}),
                {
                    "add": [
                        {"add": [square(q), {"mul": [{"mul": [{"rat": [2, 1]}, q]}, lift]}]},
                        square(lift),
                    ]
                },
            ]
        },
    }
    return {
        "definitions": definitions,
        "nodes": [
            {"id": "expansion", "kind": "lemma", "depends_on": [], "target": seed},
            {"id": "problem", "kind": "problem", "depends_on": ["expansion"], "target": problem},
        ],
    }
