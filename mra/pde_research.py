"""Project-bound classical PDE graphs with exact proof-source imports and conservative publication."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
from pathlib import Path
import os
import re
import sqlite3
import uuid

from mra.domain import canonical, digest
from mra.formalizer import exact_object
from mra.integrity import file_hash
from mra.knowledge_instances import storage_busy
from mra.mathlib_lab import now
from mra.number_theory_terms import text
from mra.pde_language import MODE
from mra.pde_language_lab import PDELanguageStore, manifest as provider_manifest, run_identifier
from mra.research import ResearchStore
from mra.research_flow import atomic, identifier, lease
from mra.search_evaluation import _envelope
from mra.tasks import TaskStore, _identifier
from mra.typed_research import TypedResearchStore, source_identity
from .pde_research_bridge import prepare_node_preview
from .pde_expansion import expand_graph
from .pde_research_graph import SCHEMA as DRAFT_SCHEMA, bind_node_draft, compile_graph, node_id

SCHEMA = "pde-project-research-v1"
LIMITS = {"graphs": 50, "attempts": 16, "worker_seconds": 480, "events": 512}
ACTIVE = ("QUEUED", "RUNNING")
STATES = (
    *ACTIVE,
    "COMPLETED",
    "CANCELLED",
    "INTERRUPTED",
    "REJECTED",
    "POLICY_BLOCKED",
    "EXECUTION_ERROR",
    "TIMED_OUT",
    "TIMEOUT",
)
ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error)
SOURCES = (
    "mra/pde_research.py",
    "mra/pde_expansion.py",
    "mra/pde_research_graph.py",
    "mra/pde_research_bridge.py",
    "mra/typed_research.py",
    "mra/research_flow.py",
    "mra/research.py",
    "mra/search_evaluation.py",
    "mra/knowledge_instances.py",
    "mra/number_theory_terms.py",
)


def manifest(root):
    return {
        "schema": SCHEMA,
        "provider": provider_manifest(root),
        "files": {name: file_hash(root / name) for name in SOURCES},
    }


class PDEResearchStore(TypedResearchStore):
    def __init__(self, root, *, base=None):
        self.root = Path(root).resolve()
        canonical_base = self.root / "artifacts/pde-research"
        self.base = canonical_base if base is None else Path(base).absolute()
        if (
            self.base.resolve() != self.base
            or not self.base.is_relative_to(self.root / "artifacts")
            or (self.base != canonical_base and self.base.name != "pde-research-private")
        ):
            raise ValueError("Use canonical or isolated PDE graph storage")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        self.research = ResearchStore(self.root)

    def directory(self, graph_id):
        directory = self.base / identifier(graph_id, "pdeflow")
        if self.base.resolve() != self.base or directory.resolve() != directory:
            raise ValueError("PDE graph storage was redirected")
        return directory

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("PDE graph storage changed")
        paths = sorted(
            p for p in self.base.iterdir() if re.fullmatch(r"pdeflow-[a-f0-9]{32}", p.name)
        )
        if len(paths) > LIMITS["graphs"]:
            raise ValueError("PDE graph storage budget exceeded")
        return paths

    def checker(self, graph_id):
        base = self.directory(graph_id) / "pde-language-private"
        path = base / "lab.sqlite3"
        if base.resolve() != base or path.is_symlink() or not path.is_file():
            raise ValueError("PDE child ledger is missing or redirected; never recreate it")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT id,mode FROM pde_language_identity").fetchall() != [(1, MODE)]:
                raise ValueError("PDE child ledger identity changed")
        return PDELanguageStore(self.root, base=base)

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
                "PDE graph record",
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
                raise ValueError("PDE graph record identity or bounds changed")
            attempts, requests, children = set(), set(), set()
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
                        "ancestor_evidence",
                        "child_plan",
                        "budget_seconds",
                        "state",
                        "child_id",
                        "source_fingerprint",
                        "invalidated",
                        "reason",
                        "cancel_requested",
                    },
                    "PDE node attempt",
                )
                identifier(attempt["attempt_id"], "pdeattempt")
                _identifier(attempt["request_id"])
                node_id(attempt["node_id"])
                run_identifier(attempt["child_id"])
                if (
                    attempt["attempt_id"] in attempts
                    or attempt["request_id"] in requests
                    or attempt["child_id"] in children
                    or type(attempt["invalidated"]) is not bool
                    or type(attempt["cancel_requested"]) is not bool
                    or not isinstance(attempt["prerequisites"], dict)
                    or not isinstance(attempt["ancestor_evidence"], dict)
                    or not isinstance(attempt["child_plan"], dict)
                    or attempt["state"] not in STATES
                    or type(attempt["budget_seconds"]) is not int
                    or not 1 <= attempt["budget_seconds"] <= 40
                ):
                    raise ValueError("PDE attempt identity, state or bounds changed")
                text(attempt["reviewer"], 200, "Reviewer")
                preview = {
                    "graph_id": graph_id,
                    "graph_fingerprint": row["plan_fingerprint"],
                    "node_id": attempt["node_id"],
                    "prerequisites": attempt["prerequisites"],
                    "ancestor_evidence": attempt["ancestor_evidence"],
                    "child_plan": attempt["child_plan"],
                    "math_verdict": "UNKNOWN",
                    "evidence_level": "E0",
                }
                submission = {
                    "request_id": attempt["request_id"],
                    "node_id": attempt["node_id"],
                    "execution_fingerprint": attempt["execution_fingerprint"],
                    "reviewer": attempt["reviewer"],
                    "confirmed": True,
                }
                if (
                    digest(preview) != attempt["execution_fingerprint"]
                    or digest(submission) != attempt["submission_fingerprint"]
                ):
                    raise ValueError("PDE attempt confirmation fingerprint changed")
                attempts.add(attempt["attempt_id"])
                requests.add(attempt["request_id"])
                children.add(attempt["child_id"])
            return row
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            if directory.is_dir():
                atomic(directory / "invalidated.json", {"at": now(), "reason": str(error)})
            raise

    def preview(self, raw):
        exact_object(raw, {"project_id", "branch_id", "nodes"}, "Complete PDE research task")
        compiled = compile_graph({"schema": DRAFT_SCHEMA, "nodes": raw["nodes"]})
        value = {
            "schema": SCHEMA,
            "request": deepcopy(raw),
            "parent": self.parent(raw["project_id"], raw["branch_id"]),
            "graph_compilation": compiled,
            "order": compiled["order"],
            "problem_id": compiled["problem_id"],
            "source_manifest": manifest(self.root),
            "limits": dict(LIMITS),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "worker_execution": "explicit-confirmation",
            "source_authorization_checked": False,
            "limitations": [
                "Saving compiles complete statements; sources are authorized only by actual node previews.",
                "The original problem requires its own complete E4 check.",
                "Classical one-dimensional PDE identities; general weak solutions and existence are outside this protocol.",
            ],
        }
        value["plan_fingerprint"] = digest(value)
        if len(canonical({"payload": value, "sha256": digest(value)})) + 1 > 2097152:
            raise ValueError("Complete PDE graph plan exceeds its 2 MiB storage budget")
        return value

    def preview_expanded(self, raw):
        exact_object(raw, {"project_id", "branch_id", "nodes"}, "Complete PDE expansion input")
        expansion = expand_graph({"schema": DRAFT_SCHEMA, "nodes": raw["nodes"]})
        value = self.preview({**raw, "nodes": expansion["nodes"]})
        value["expansion"] = expansion
        value["plan_fingerprint"] = digest(
            {k: v for k, v in value.items() if k != "plan_fingerprint"}
        )
        if len(canonical(value)) > 2000000:
            raise ValueError("Expanded plan exceeds its storage budget")
        return value

    def create_expanded(self, data):
        return self._create(data, expanded=True)

    def create(self, data):
        return self._create(data)

    def _create(self, data, *, expanded=False):
        exact_object(data, {"request_id", "request", "plan_fingerprint"}, "Save PDE research task")
        _identifier(data["request_id"])
        plan = self.preview_expanded(data["request"]) if expanded else self.preview(data["request"])
        data = {**data, "request": plan["request"]}
        if data["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Full PDE graph preview changed")
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                previous = self.read(path.name)
                if previous["request_id"] == data["request_id"]:
                    if previous["submission_fingerprint"] != digest(data):
                        raise ValueError("Graph request ID is bound to a different task")
                    key = previous["graph_id"]
                    break
            else:
                if len(paths) >= LIMITS["graphs"]:
                    raise ValueError("PDE graph storage budget exhausted")
                key = "pdeflow-" + uuid.uuid4().hex
                target = self.directory(key)
                staging = self.base / (".creating-" + key)
                if staging.resolve() != staging or staging.exists() or target.exists():
                    raise ValueError("PDE graph publication target changed")
                staging.mkdir()
                atomic(staging / "plan.json", plan)
                PDELanguageStore(self.root, base=staging / "pde-language-private")
                row = {
                    "schema": SCHEMA,
                    "graph_id": key,
                    "request_id": data["request_id"],
                    "submission_fingerprint": digest(data),
                    "created_at": now(),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "revision": 0,
                    "invalidated": False,
                    "reason": None,
                    "attempts": [],
                    "events": [],
                }
                self.event(row, "SAVED_WITHOUT_LEAN")
                atomic(staging / "record.json", row)
                staging.rename(target)
                fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return self.report(key)

    def progress(self, graph_id):
        """Read atomic execution metadata without authorizing any mathematical verdict."""
        row = self.read(graph_id)
        return {
            "schema": "pde-execution-progress-v1",
            "graph_id": graph_id,
            "record_fingerprint": digest(row),
            "evidence_checked": False,
            "attempts": [
                {
                    key: attempt[key]
                    for key in ("attempt_id", "node_id", "state", "cancel_requested", "invalidated")
                }
                for attempt in row["attempts"]
            ],
        }

    @contextmanager
    def _audit_record(self, graph_id):
        """Audit and save under a directory lease already owned by the caller."""
        directory = self.directory(graph_id)
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

    @contextmanager
    def audited(self, graph_id):
        with lease(self.directory(graph_id)), self._audit_record(graph_id) as values:
            yield values

    @contextmanager
    def worker_audited(self, graph_id):
        """Retain the global worker lease while waiting for an existing full reader.

        Reader contention is not a supervisor failure. The blocking directory
        lease does not retry a mutation or checker, or promote incomplete evidence.
        """
        directory = self.directory(graph_id)
        fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self._audit_record(graph_id) as values:
                yield values
        finally:
            os.close(fd)

    def _audit(self, row):
        directory = self.directory(row["graph_id"])
        plan = _envelope(directory / "plan.json")
        compiled = compile_graph({"schema": DRAFT_SCHEMA, "nodes": plan["request"]["nodes"]})
        if (
            plan["schema"] != SCHEMA
            or plan["plan_fingerprint"] != row["plan_fingerprint"]
            or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
            != row["plan_fingerprint"]
            or canonical(compiled) != canonical(plan["graph_compilation"])
            or plan["order"] != compiled["order"]
            or plan["problem_id"] != compiled["problem_id"]
        ):
            raise ValueError("PDE graph plan or complete compilation changed")
        if row["submission_fingerprint"] != digest(
            {
                "request_id": row["request_id"],
                "request": plan["request"],
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        ):
            raise ValueError("PDE graph save confirmation changed")
        if "expansion" in plan:
            expected = expand_graph(plan["expansion"]["source_graph"])
            if canonical(expected) != canonical(plan["expansion"]) or canonical(
                expected["nodes"]
            ) != canonical(plan["request"]["nodes"]):
                raise ValueError("Original derivation or expanded final proof changed")
        reason = row["reason"] if row["invalidated"] else None
        try:
            if (directory / "invalidated.json").exists():
                reason = reason or "Previously detected graph damage"
            elif canonical(plan["source_manifest"]) != canonical(manifest(self.root)):
                reason = "PDE graph implementation changed"
            elif canonical(plan["parent"]) != canonical(
                self.parent(plan["request"]["project_id"], plan["request"]["branch_id"])
            ):
                reason = "Original project question or branch changed"
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            reason = "Original PDE context unavailable: " + str(error)
        if reason:
            if not (directory / "invalidated.json").exists():
                atomic(directory / "invalidated.json", {"permanent": True, "reason": reason})
            if not row["invalidated"]:
                row.update(invalidated=True, reason=reason)
                self.event(row, "GRAPH_INVALIDATED", reason=reason)
        if any(a["node_id"] not in plan["order"] for a in row["attempts"]):
            raise ValueError("Attempt belongs to an unknown PDE node")
        by_id = {node["id"]: node for node in plan["request"]["nodes"]}
        checker = self.checker(row["graph_id"])
        states, checks = {}, {}
        for key in plan["order"]:
            node = by_id[key]
            deps = {dep: states[dep]["source_fingerprint"] for dep in node["depends_on"]}
            blocked = [dep for dep in node["depends_on"] if states[dep]["math_verdict"] != "PROVED"]
            valid, active = [], []
            for attempt in (a for a in row["attempts"] if a["node_id"] == key):
                why = None
                if (
                    row["invalidated"]
                    or blocked
                    or canonical(attempt["prerequisites"]) != canonical(deps)
                ):
                    why = "Original context or exact prerequisite evidence changed"
                try:
                    child = checker.report(attempt["child_id"])
                    checks[attempt["attempt_id"]] = child
                    if (
                        canonical(child["plan"]) != canonical(attempt["child_plan"])
                        or child["invalidated"]
                    ):
                        why = "Confirmed complete child plan or evidence changed"
                    if child["alignment"]["reviewer"] != attempt["reviewer"]:
                        why = "Named graph confirmation differs from the actual child confirmation"
                    if not why:
                        bindings = {
                            source["node_id"]: {
                                "run_id": states[source["node_id"]]["best_child_id"],
                                "type_fingerprint": source["type_fingerprint"],
                            }
                            for source in compiled["node_plans"][key]["source_nodes"]
                        }
                        expected = bind_node_draft(compiled, key, bindings)["request"]
                        if (
                            canonical(child["plan"]["request"]) != canonical(expected)
                            or child["plan"]["formal_statement"]
                            != compiled["node_plans"][key]["formal_statement"]
                            or attempt["budget_seconds"] != expected["timeout_seconds"]
                        ):
                            why = "Actual child no longer matches its complete original graph node"
                    if (
                        attempt["state"] == "QUEUED"
                        and child["state"] != "QUEUED"
                        and not attempt["cancel_requested"]
                    ):
                        why = "Unmanaged child transition before graph execution"
                    if (
                        attempt["state"] == "COMPLETED"
                        and not why
                        and not attempt["invalidated"]
                        and not attempt["cancel_requested"]
                    ):
                        if (child["state"], child["math_verdict"], child["evidence_level"]) != (
                            "COMPLETED",
                            "PROVED",
                            "E4",
                        ) or source_identity(child) != attempt["source_fingerprint"]:
                            why = "No exact current complete child E4 evidence"
                        else:
                            valid.append(attempt)
                    if (
                        attempt["state"] in ACTIVE
                        and not attempt["invalidated"]
                        and not attempt["cancel_requested"]
                        and not why
                    ):
                        active.append(attempt)
                    if why and child["state"] in ACTIVE:
                        checker.cancel(child["run_id"])
                except ERRORS as error:
                    if isinstance(error, sqlite3.Error) and storage_busy(error):
                        raise
                    why = "PDE child evidence unavailable: " + str(error)
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
                "best_child_id": best["child_id"] if best else None,
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
            "worker_execution": "explicit-confirmation",
            "checks_reserved": len(row["attempts"]),
            "worker_seconds_reserved": sum(a["budget_seconds"] for a in row["attempts"]),
        }

    def execution_preview(self, row, plan, states, key):
        node_id(key)
        if key not in states or row["invalidated"] or states[key]["blocked_by"]:
            raise ValueError("Select a known node with all exact current prerequisites")
        by_id = {node["id"]: node for node in plan["request"]["nodes"]}
        ancestors = set()

        def visit(current):
            for dep in by_id[current]["depends_on"]:
                if dep not in ancestors:
                    ancestors.add(dep)
                    visit(dep)

        visit(key)
        runs = {dep: states[dep]["best_child_id"] for dep in ancestors}
        bridge = prepare_node_preview(
            self.checker(row["graph_id"]), plan["graph_compilation"], key, runs
        )
        value = {
            "graph_id": row["graph_id"],
            "graph_fingerprint": plan["plan_fingerprint"],
            "node_id": key,
            "prerequisites": {
                dep: states[dep]["source_fingerprint"] for dep in by_id[key]["depends_on"]
            },
            "ancestor_evidence": bridge["prerequisites"],
            "child_plan": bridge["child_plan"],
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        value["execution_fingerprint"] = digest(value)
        return value

    def submit(self, graph_id, data):
        exact_object(
            data,
            {"request_id", "node_id", "execution_fingerprint", "reviewer", "confirmed"},
            "PDE node submission",
        )
        _identifier(data["request_id"])
        reviewer = text(data["reviewer"], 200, "Named semantic reviewer")
        data = {**data, "reviewer": reviewer}
        if data["confirmed"] is not True:
            raise ValueError("Explicit complete-target confirmation is required")
        with self.audited(graph_id) as (row, plan, states, _):
            for previous in row["attempts"]:
                if previous["request_id"] == data["request_id"]:
                    if previous["submission_fingerprint"] != digest(data):
                        raise ValueError("Node request ID belongs to a different confirmation")
                    return deepcopy(previous)
            current = self.execution_preview(row, plan, states, data["node_id"])
            if current["execution_fingerprint"] != data["execution_fingerprint"]:
                raise ValueError("Complete node preview or prerequisite identity changed")
            if any(a["state"] in ACTIVE and not a["invalidated"] for a in row["attempts"]):
                raise ValueError("Finish or cancel the active PDE attempt first")
            if states[data["node_id"]]["math_verdict"] == "PROVED":
                raise ValueError("PDE node already has current E4 evidence")
            seconds = current["child_plan"]["request"]["timeout_seconds"]
            if (
                len(row["attempts"]) >= LIMITS["attempts"]
                or sum(a["budget_seconds"] for a in row["attempts"]) + seconds
                > LIMITS["worker_seconds"]
            ):
                raise ValueError("PDE graph attempt or shared worker budget exhausted")
            key = "pdeattempt-" + uuid.uuid4().hex
            attempt = {
                "attempt_id": key,
                "request_id": data["request_id"],
                "node_id": data["node_id"],
                "submission_fingerprint": digest(data),
                "reviewer": reviewer,
                "execution_fingerprint": current["execution_fingerprint"],
                "prerequisites": current["prerequisites"],
                "ancestor_evidence": current["ancestor_evidence"],
                "child_plan": current["child_plan"],
                "budget_seconds": seconds,
                "state": "QUEUED",
                "child_id": "pdelang-" + "0" * 32,
                "source_fingerprint": None,
                "invalidated": False,
                "reason": None,
                "cancel_requested": False,
            }
            # Inert UUID-sized placeholder is used only to check storage capacity before queueing.
            prospective = deepcopy(row)
            prospective["attempts"].append(deepcopy(attempt))
            self.event(prospective, "NAMED_CHECK_QUEUED", attempt_id=key)
            if (
                len(canonical({"payload": prospective, "sha256": digest(prospective)})) + 65
                > 2097152
            ):
                raise ValueError("PDE graph record would exceed its 2 MiB storage budget")
            if len(row["events"]) + 3 > LIMITS["events"]:
                raise ValueError("Reserve graph event capacity for check start and completion")
            child = self.checker(graph_id).submit(
                {
                    "request_id": key,
                    "request": current["child_plan"]["request"],
                    "plan_fingerprint": current["child_plan"]["plan_fingerprint"],
                    "reviewer": reviewer,
                    "confirmed": True,
                }
            )
            if (
                canonical(child["plan"]) != canonical(current["child_plan"])
                or child["state"] != "QUEUED"
            ):
                raise ValueError("Actual child submission differs from the confirmed plan")
            attempt["child_id"] = child["run_id"]
            row["attempts"].append(attempt)
            self.event(row, "NAMED_CHECK_QUEUED", attempt_id=key)
            return deepcopy(attempt)

    def recover(self):
        with self.tasks.worker_lock():
            interrupted = self.recover_locked()
        return {"interrupted": interrupted, "automatic_retry": False}

    def execute(self, graph_id, attempt_id, lock_fd):
        checker = self.checker(graph_id)
        started = False
        with self.worker_audited(graph_id) as (row, _, states, _):
            attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            child_id = attempt["child_id"]
            if (
                attempt["state"] != "RUNNING"
                or attempt["cancel_requested"]
                or attempt["invalidated"]
                or row["invalidated"]
                or states[attempt["node_id"]]["blocked_by"]
            ):
                checker.cancel(child_id)
                attempt["state"] = "CANCELLED" if attempt["cancel_requested"] else "POLICY_BLOCKED"
                self.event(row, "EXECUTION_BLOCKED_BEFORE_WORKER", attempt_id=attempt_id)
            else:
                with checker.transaction() as db:
                    child = checker.read(child_id, db)
                    if (
                        child["state"] == "QUEUED"
                        and not child["cancel_requested"]
                        and not child["invalidated"]
                    ):
                        child.update(state="RUNNING", stage="CHECKING")
                        checker.event(child, "STARTED")
                        checker.save(db, child)
                        started = True
                    else:
                        attempt["state"] = (
                            "CANCELLED" if child["cancel_requested"] else "POLICY_BLOCKED"
                        )
                        self.event(row, "EXECUTION_BLOCKED_BEFORE_WORKER", attempt_id=attempt_id)
        if not started:
            return self.report(graph_id)
        result = checker.execute(child_id, lock_fd)
        with self.worker_audited(graph_id) as (row, _, states, _):
            attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            if attempt["cancel_requested"]:
                attempt["state"] = "CANCELLED"
            elif (
                attempt["invalidated"]
                or row["invalidated"]
                or states[attempt["node_id"]]["blocked_by"]
            ):
                attempt["state"] = "POLICY_BLOCKED"
            else:
                attempt["state"] = result["state"]
                if (result["state"], result["math_verdict"], result["evidence_level"]) == (
                    "COMPLETED",
                    "PROVED",
                    "E4",
                ):
                    attempt["source_fingerprint"] = source_identity(result)
            self.event(row, "CHECK_FINISHED", attempt_id=attempt_id, state=attempt["state"])
            self._audit(row)
        return self.report(graph_id)

    def catalog(self, project_id, branch_id):
        self.parent(project_id, branch_id)
        graphs = []
        for directory in self.paths():
            row = self.read(directory.name)
            plan = _envelope(directory / "plan.json")
            if (
                plan["request"]["project_id"] == project_id
                and plan["request"]["branch_id"] == branch_id
            ):
                graphs.append(
                    {
                        "graph_id": row["graph_id"],
                        "plan": plan,
                        "invalidated": row["invalidated"]
                        or (directory / "invalidated.json").exists(),
                        "evidence_checked": False,
                    }
                )
        return {
            "schema": SCHEMA,
            "graphs": graphs,
            "model_calls": 0,
            "worker_execution": "explicit-confirmation",
        }
