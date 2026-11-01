"""Project-bound research routes with explicit checks and no inferred graph proofs."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import counterexamples
from .domain import canonical, digest
from .formalizer import exact_object
from .integrity import file_hash
from .lean_goal_trace import MODE as GOAL_MODE, PROGRAM, GoalTraceChecks, read_trace
from .lean_strategies import MODE as STRATEGY_MODE, LeanStrategyStore
from .knowledge_instances import storage_busy
from .mathlib_lab import now, source_manifest
from .number_theory_lab import NumberTheoryStore
from .number_theory_search import SCHEMA as SEARCH_MODE, search
from .number_theory_terms import compile_request
from .research import ResearchStore
from .search_evaluation import _envelope
from .tasks import TaskStore, WorkerBusy, _identifier

SCHEMA = "project-research-flow-v1"
ROUTES = ("proof-search", "omega", "simp-gcd", "counterexample", "goal-trace")
LIMITS = {"flows": 50, "nodes": 8, "proposals": 24, "attempts": 16, "bytes": 2097152}
SOURCE_FILES = (
    "mra/research_flow.py",
    "mra/lean_goal_trace.py",
    "mra/research.py",
    "mra/number_theory_terms.py",
    "mra/number_theory_search.py",
    "mra/number_theory_lab.py",
    "mra/lean_strategies.py",
    "mra/counterexamples.py",
    "mra/search_evaluation.py",
)


def atomic(path, value):
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError("Research flow storage cannot be redirected")
    raw = canonical({"payload": value, "sha256": digest(value)}) + b"\n"
    if len(raw) > LIMITS["bytes"]:
        raise ValueError("Research flow exceeds 2 MiB; export before continuing")
    pending = path.with_name(".pending-" + uuid.uuid4().hex)
    try:
        with pending.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if pending.exists():
            pending.unlink()


@contextmanager
def lease(directory):
    if directory.resolve() != directory:
        raise ValueError("Research flow directory cannot be redirected")
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerBusy("Research record is being updated; retry after reloading") from error
        yield
    finally:
        os.close(fd)


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"-[a-f0-9]{32}", value):
        raise ValueError("Invalid research " + prefix + " identifier")
    return value


def manifest(root):
    return {
        "schema": SCHEMA,
        "executor": source_manifest(root),
        "files": {name: file_hash(root / name) for name in SOURCE_FILES},
    }


def graph(nodes):
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= LIMITS["nodes"]:
        raise ValueError("A research task needs 1–8 complete nodes")
    by_id = {}
    for node in nodes:
        exact_object(node, {"id", "kind", "target", "depends_on"}, "Research task node")
        key = _identifier(node["id"])
        if len(key) > 32 or key in by_id or node["kind"] not in ("problem", "lemma", "subproblem"):
            raise ValueError("Use unique short node IDs and a supported node kind")
        counterexamples.target(node["target"])
        deps = node["depends_on"]
        if not isinstance(deps, list) or len(deps) > 7:
            raise ValueError("Use at most seven distinct prerequisite IDs")
        for dep in deps:
            _identifier(dep)
        if len(set(deps)) != len(deps):
            raise ValueError("Duplicate research prerequisite")
        by_id[key] = node
    problems = [n["id"] for n in nodes if n["kind"] == "problem"]
    if len(problems) != 1:
        raise ValueError("Exactly one node must be the original problem")
    order, visiting, seen = [], set(), set()

    def visit(key):
        if key not in by_id or key in visiting:
            raise ValueError("Research prerequisites must form a complete acyclic graph")
        if key in seen:
            return
        visiting.add(key)
        for dep in by_id[key]["depends_on"]:
            visit(dep)
        visiting.remove(key)
        seen.add(key)
        order.append(key)

    visit(problems[0])
    if seen != set(by_id):
        raise ValueError("Every node must contribute to the original problem's prerequisite graph")
    return order, problems[0]


class FlowCheckMixin:
    def __init__(self, root, directory, create=False):
        self.root = Path(root).resolve()
        self.base = directory / "checks"
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Research child evidence cannot be redirected")
        if create:
            self.base.mkdir(exist_ok=False)
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
        elif not self.path.is_file():
            raise ValueError("Research child evidence is unavailable")

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Research child ledger cannot be redirected")
        with super().transaction(write) as db:
            yield db

    def preview(self, request):
        plan = super().preview(request)
        plan["source_manifest"] = {"flow": manifest(self.root), "provider": plan["source_manifest"]}
        plan.pop("plan_fingerprint")
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"]["flow"] != manifest(self.root):
            return "Research flow adapter or trusted content changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["provider"]},
        }
        return super().gate(view, check_files)


class FlowProofChecks(FlowCheckMixin, NumberTheoryStore):
    pass


class FlowStrategyChecks(FlowCheckMixin, LeanStrategyStore):
    pass


class FlowRefutationChecks(FlowCheckMixin, counterexamples.CounterexampleChecks):
    pass


class FlowGoalChecks(FlowCheckMixin, GoalTraceChecks):
    pass


def check_type(route):
    return {
        "proof-search": FlowProofChecks,
        "omega": FlowStrategyChecks,
        "simp-gcd": FlowStrategyChecks,
        "counterexample": FlowRefutationChecks,
        "goal-trace": FlowGoalChecks,
    }[route]


def child_preview(root, route, request):
    # Preview needs no child ledger. All provider preview methods are pure compilers.
    checker = object.__new__(check_type(route))
    checker.root = Path(root).resolve()
    return checker.preview(request)


class ResearchFlowStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-flows"
        if (self.root / "artifacts").is_symlink() or self.base.resolve() != self.base:
            raise ValueError("Research task storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        self.research = ResearchStore(self.root)

    def directory(self, flow_id):
        path = self.base / identifier(flow_id, "flow")
        if path.resolve() != path:
            raise ValueError("Research task directory cannot be redirected")
        return path

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Research task storage changed")
        paths = sorted(p for p in self.base.iterdir() if re.fullmatch(r"flow-[a-f0-9]{32}", p.name))
        if len(paths) > LIMITS["flows"]:
            raise ValueError("Research task history exceeds fifty records")
        return paths

    def parent(self, project_id, branch_id):
        path = self.research.path
        if path.resolve() != path or path.is_symlink():
            raise ValueError("Parent research ledger cannot be redirected")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            body = ResearchStore.read(db, project_id)
        branch = ResearchStore.branch(body, branch_id)
        context = {
            "project_id": project_id,
            "question": body["question"],
            "branch_id": branch_id,
            "revision": branch["revision"],
            "nodes": branch["nodes"],
        }
        return {
            "project_id": project_id,
            "branch_id": branch_id,
            "title": body["title"],
            "question": body["question"],
            "branch_label": branch["label"],
            "context_fingerprint": digest(context),
        }

    @staticmethod
    def event(row, kind, **details):
        if len(row["events"]) >= 512:
            raise ValueError("Research task event budget exhausted")
        row["events"].append({"at": now(), "kind": kind, **details})

    def read(self, flow_id):
        directory = self.directory(flow_id)
        try:
            row = _envelope(directory / "record.json")
            if row["flow_id"] != flow_id or row["schema"] != SCHEMA:
                raise ValueError("Research task identity mismatch")
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            # Restoring a damaged record must not silently revive its evidence.
            atomic(directory / "invalidated.json", {"at": now(), "reason": str(error)})
            raise
        return row

    def save(self, row):
        atomic(self.directory(row["flow_id"]) / "record.json", row)

    def preview(self, raw):
        exact_object(raw, {"project_id", "branch_id", "nodes"}, "Complete research task")
        order, problem = graph(raw["nodes"])
        nodes = [
            {**deepcopy(n), "target": counterexamples.target(n["target"])} for n in raw["nodes"]
        ]
        value = {
            "schema": SCHEMA,
            "parent": self.parent(raw["project_id"], raw["branch_id"]),
            "nodes": nodes,
            "order": order,
            "problem_id": problem,
            "source_manifest": manifest(self.root),
            "limits": LIMITS,
            "budget": {
                "maximum_checks": 16,
                "maximum_total_worker_seconds": 480,
                "automatic_retries": 0,
                "integrity_time_separate": True,
            },
            "math_verdict": "UNKNOWN",
            "model_calls": 0,
            "limitations": [
                "Prerequisites gate research progress; they are never added as Lean assumptions.",
                "Every node, especially the original problem, needs its own exact full theorem check.",
                "Graph decomposition and natural-language alignment remain human supplied.",
                "Proposals and numeric witnesses are UNKNOWN until individually reviewed and checked.",
                "No automatic research, retries, model calls, arbitrary tactics or live session restoration.",
            ],
        }
        value["plan_fingerprint"] = digest(value)
        return value

    def create(self, raw):
        exact_object(
            raw,
            {"request_id", "project_id", "branch_id", "nodes", "plan_fingerprint"},
            "Save research task",
        )
        request_id = _identifier(raw["request_id"])
        plan = self.preview({k: raw[k] for k in ("project_id", "branch_id", "nodes")})
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Research task preview changed; inspect the full graph again")
        fingerprint = digest(raw)
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                try:
                    old = self.read(path.name)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue  # Quarantined records still count toward the storage limit.
                if old["request_id"] == request_id:
                    if old["submission_fingerprint"] != fingerprint:
                        raise ValueError("Request ID belongs to another research task")
                    return self.report(old["flow_id"])
            if len(paths) >= LIMITS["flows"]:
                raise ValueError("Export before creating more than fifty research tasks")
            key = "flow-" + uuid.uuid4().hex
            directory = self.directory(key)
            directory.mkdir()
            atomic(directory / "plan.json", plan)
            row = {
                "schema": SCHEMA,
                "flow_id": key,
                "request_id": request_id,
                "submission_fingerprint": fingerprint,
                "created_at": now(),
                "plan_fingerprint": plan["plan_fingerprint"],
                "invalidated": False,
                "reason": None,
                "proposals": {},
                "attempts": [],
                "events": [],
            }
            self.event(row, "CREATED_WITHOUT_EXECUTION")
            self.save(row)
        return self.report(key)

    def _audit(self, row):
        plan = _envelope(self.directory(row["flow_id"]) / "plan.json")
        if (
            plan["plan_fingerprint"] != row["plan_fingerprint"]
            or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
            != row["plan_fingerprint"]
        ):
            raise ValueError("Research task plan fingerprint mismatch")
        reason = None
        marker = self.directory(row["flow_id"]) / "invalidated.json"
        try:
            if marker.exists():
                reason = "Previously detected unavailable or changed task evidence"
            elif plan["source_manifest"] != manifest(self.root):
                reason = "Research task compiler or trusted content changed"
            elif plan["parent"] != self.parent(
                plan["parent"]["project_id"], plan["parent"]["branch_id"]
            ):
                reason = "Parent research question or branch context changed"
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            reason = "Parent research storage unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            reason = "Research context unavailable: " + str(error)
        if reason and not row["invalidated"]:
            row.update(invalidated=True, reason=reason)
            self.event(row, "TASK_INVALIDATED", reason=reason)
        by_id = {node["id"]: node for node in plan["nodes"]}
        states, checks = {}, {}
        for key in plan["order"]:
            node = by_id[key]
            prerequisites = {d: states[d]["binding_fingerprint"] for d in node["depends_on"]}
            blocked = [d for d in node["depends_on"] if states[d]["math_verdict"] != "PROVED"]
            valid = []
            for attempt in (a for a in row["attempts"] if a["node_id"] == key):
                if not attempt["invalidated"] and (
                    row["invalidated"] or blocked or attempt["prerequisites"] != prerequisites
                ):
                    attempt.update(
                        invalidated=True,
                        reason="Research context or checked prerequisites no longer match",
                    )
                    self.event(row, "ATTEMPT_INVALIDATED", attempt_id=attempt["attempt_id"])
                if attempt["state"] != "COMPLETED" or attempt["invalidated"]:
                    if attempt["run_id"] and attempt["state"] not in ("QUEUED", "RUNNING"):
                        try:
                            proposal = row["proposals"][attempt["proposal_id"]]
                            checker = check_type(proposal["route"])(
                                self.root, self.directory(row["flow_id"]) / attempt["attempt_id"]
                            )
                            checks[attempt["attempt_id"]] = checker.report(attempt["run_id"])
                        except sqlite3.Error as error:
                            if storage_busy(error):
                                raise
                        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                            pass  # The parent remains UNKNOWN; the original recorded error is retained.
                    continue
                try:
                    proposal = row["proposals"][attempt["proposal_id"]]
                    checker = check_type(proposal["route"])(
                        self.root, self.directory(row["flow_id"]) / attempt["attempt_id"]
                    )
                    child = checker.report(attempt["run_id"])
                    checks[attempt["attempt_id"]] = child
                    if (
                        child["state"],
                        child["math_verdict"],
                        child["evidence_level"],
                        child["invalidated"],
                    ) != ("COMPLETED", "PROVED", "E4", False) or child["plan"] != proposal[
                        "child_plan"
                    ]:
                        raise ValueError("Child no longer has exact complete PROVED/E4 evidence")
                    source = digest({k: child[k] for k in ("plan", "alignment", "files", "result")})
                    if source != attempt["source_fingerprint"]:
                        raise ValueError("Child evidence changed")
                    if proposal["route"] == "goal-trace":
                        observed = read_trace(
                            (
                                checker.directory(child["run_id"]) / "candidate/stdout.log"
                            ).read_text()
                        )
                        if observed != attempt["goal_trace"]:
                            raise ValueError("Actual Lean goal observations changed")
                    valid.append(
                        (attempt, "REFUTED" if proposal["route"] == "counterexample" else "PROVED")
                    )
                except sqlite3.Error as error:
                    if storage_busy(error):
                        raise
                    attempt.update(
                        invalidated=True, reason="Child storage unavailable: " + str(error)
                    )
                    self.event(
                        row,
                        "ATTEMPT_INVALIDATED",
                        attempt_id=attempt["attempt_id"],
                        reason=attempt["reason"],
                    )
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    attempt.update(
                        invalidated=True, reason="Child evidence unavailable: " + str(error)
                    )
                    self.event(
                        row,
                        "ATTEMPT_INVALIDATED",
                        attempt_id=attempt["attempt_id"],
                        reason=attempt["reason"],
                    )
            verdicts = {verdict for _, verdict in valid}
            chosen = valid[-1][0] if len(verdicts) == 1 else None
            verdict = next(iter(verdicts)) if chosen else "UNKNOWN"
            states[key] = {
                "id": key,
                "formal_statement": compile_request(
                    {**node["target"], "proof": counterexamples.PLACEHOLDER}
                )["formal_statement"],
                "math_verdict": verdict,
                "evidence_level": "E4" if chosen else "E0",
                "blocked_by": blocked,
                "status": "INVALIDATED"
                if row["invalidated"]
                else "CONFLICT"
                if len(verdicts) > 1
                else verdict
                if chosen
                else "BLOCKED"
                if blocked
                else "READY",
                "best_attempt": chosen["attempt_id"] if chosen else None,
                "binding_fingerprint": digest(
                    {"attempt_id": chosen["attempt_id"], "source": chosen["source_fingerprint"]}
                )
                if chosen
                else None,
            }
        return plan, states, checks

    def report(self, flow_id):
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            before = deepcopy(row)
            try:
                plan, states, checks = self._audit(row)
            except sqlite3.Error:
                raise
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                atomic(
                    self.directory(flow_id) / "invalidated.json",
                    {"at": now(), "reason": str(error)},
                )
                raise
            if row != before:
                self.save(row)
        problem = states[plan["problem_id"]]
        return {
            **row,
            "plan": plan,
            "node_states": states,
            "checks": checks,
            "math_verdict": problem["math_verdict"],
            "evidence_level": problem["evidence_level"],
            "fresh_attestation": False,
            "model_calls": 0,
        }

    def catalog(self, project_id, branch_id):
        parent = self.parent(project_id, branch_id)
        rows = []
        for path in self.paths():
            try:
                row = self.read(path.name)
                plan = _envelope(path / "plan.json")
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                continue  # Detail access exposes the error; no verdict is inferred here.
            if (plan["parent"]["project_id"], plan["parent"]["branch_id"]) == (
                project_id,
                branch_id,
            ):
                rows.append(
                    {
                        "flow_id": row["flow_id"],
                        "created_at": row["created_at"],
                        "invalidated": row["invalidated"],
                        "nodes": len(plan["nodes"]),
                    }
                )
        return {
            "schema": SCHEMA,
            "parent": parent,
            "flows": rows,
            "routes": ROUTES,
            "limits": LIMITS,
            "example": example(),
        }

    def propose(self, flow_id, raw):
        exact_object(raw, {"node_id", "route", "request_id"}, "Research route proposal")
        _identifier(raw["request_id"])
        if raw["route"] not in ROUTES:
            raise ValueError("Select a supported research route")
        current = self.report(flow_id)
        if current["invalidated"]:
            raise ValueError("Research task is invalidated; create a newly reviewed task")
        node = next((n for n in current["plan"]["nodes"] if n["id"] == raw["node_id"]), None)
        if node is None:
            raise ValueError("Unknown research node")
        target, route = node["target"], raw["route"]
        experiment, request = None, None
        if route == "proof-search":
            experiment = search(
                {
                    "schema": SEARCH_MODE,
                    "target": target,
                    "budget": {"max_depth": 6, "max_expansions": 3000},
                },
                self.root,
            )
            request = experiment["candidate"]
        elif route == "counterexample":
            experiment = counterexamples.search(
                {
                    "schema": counterexamples.SEARCH_SCHEMA,
                    "target": target,
                    "budget": {"max_value": 4, "max_assignments": 1000},
                },
                self.root,
            )
            request = experiment["candidate"]
        elif route == "goal-trace":
            request = {**target, "schema": GOAL_MODE, "program": PROGRAM}
        else:
            request = {**target, "schema": STRATEGY_MODE, "strategy": route}
        proposal = {
            "proposal_id": "proposal-" + uuid.uuid4().hex,
            "request_id": raw["request_id"],
            "input_fingerprint": digest(raw),
            "node_id": node["id"],
            "route": route,
            "created_at": now(),
            "experiment": experiment,
            "child_plan": child_preview(self.root, route, request) if request else None,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        proposal["proposal_fingerprint"] = digest(proposal)
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            for old in row["proposals"].values():
                if old["request_id"] == raw["request_id"]:
                    if old["input_fingerprint"] != digest(raw):
                        raise ValueError("Proposal request ID belongs to different input")
                    return deepcopy(old)
            if len(row["proposals"]) >= LIMITS["proposals"]:
                raise ValueError("Research route proposal budget exhausted")
            row["proposals"][proposal["proposal_id"]] = proposal
            self.event(row, "ROUTE_PROPOSED_WITHOUT_LEAN", proposal_id=proposal["proposal_id"])
            self.save(row)
        return proposal

    def submit(self, flow_id, raw):
        exact_object(
            raw,
            {"request_id", "proposal_id", "proposal_fingerprint", "reviewer", "confirmed"},
            "Reviewed research check",
        )
        _identifier(raw["request_id"])
        reviewer = counterexamples.text(raw["reviewer"], 200, "Named semantic reviewer")
        if raw["confirmed"] is not True:
            raise ValueError(
                "Review the full original target, direction, proof, budget and prerequisites"
            )
        current = self.report(flow_id)
        proposal = current["proposals"].get(raw["proposal_id"])
        if current["invalidated"] or not proposal or proposal["child_plan"] is None:
            raise ValueError("A current complete candidate plan is required")
        if (
            raw["proposal_fingerprint"] != proposal["proposal_fingerprint"]
            or digest({k: v for k, v in proposal.items() if k != "proposal_fingerprint"})
            != raw["proposal_fingerprint"]
        ):
            raise ValueError("Research route preview changed")
        if (
            child_preview(self.root, proposal["route"], proposal["child_plan"]["request"])
            != proposal["child_plan"]
        ):
            raise ValueError("Exact child check plan changed; prepare another proposal")
        node = next(n for n in current["plan"]["nodes"] if n["id"] == proposal["node_id"])
        if current["node_states"][node["id"]]["blocked_by"]:
            raise ValueError("Every research prerequisite needs available PROVED/E4 evidence first")
        prerequisites = {
            d: current["node_states"][d]["binding_fingerprint"] for d in node["depends_on"]
        }
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            if row != {k: current[k] for k in row}:
                raise ValueError("Research task changed concurrently; reload before submitting")
            for old in row["attempts"]:
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID belongs to another named confirmation")
                    return deepcopy(old)
            if len(row["attempts"]) >= LIMITS["attempts"]:
                raise ValueError("Research check budget exhausted")
            if any(a["state"] in ("QUEUED", "RUNNING") for a in row["attempts"]):
                raise ValueError("Finish or cancel the active check before starting another route")
            attempt = {
                "attempt_id": "attempt-" + uuid.uuid4().hex,
                "request_id": raw["request_id"],
                "submission_fingerprint": digest(raw),
                "proposal_id": proposal["proposal_id"],
                "node_id": node["id"],
                "reviewer": reviewer,
                "confirmed": True,
                "prerequisites": prerequisites,
                "state": "QUEUED",
                "run_id": None,
                "invalidated": False,
                "reason": None,
                "cancel_requested": False,
                "source_fingerprint": None,
                "goal_trace": None,
                "error": None,
            }
            row["attempts"].append(attempt)
            self.event(row, "NAMED_CHECK_QUEUED", attempt_id=attempt["attempt_id"])
            self.save(row)
        return attempt

    def cancel(self, flow_id, attempt_id):
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            attempt = next((a for a in row["attempts"] if a["attempt_id"] == attempt_id), None)
            if attempt is None:
                raise ValueError("Unknown research check")
            if attempt["state"] in ("QUEUED", "RUNNING"):
                attempt["cancel_requested"] = True
                if attempt["state"] == "QUEUED":
                    attempt["state"] = "CANCELLED"
                if attempt["run_id"]:
                    proposal = row["proposals"][attempt["proposal_id"]]
                    check_type(proposal["route"])(
                        self.root, self.directory(flow_id) / attempt_id
                    ).cancel(attempt["run_id"])
                self.event(row, "CHECK_CANCEL_REQUESTED", attempt_id=attempt_id)
                self.save(row)
        return self.report(flow_id)

    def withdraw(self, flow_id, raw):
        exact_object(
            raw, {"attempt_id", "reviewer", "reason", "confirmed"}, "Withdraw research binding"
        )
        reviewer = counterexamples.text(raw["reviewer"], 200, "Reviewer")
        reason = counterexamples.text(raw["reason"], 500, "Reason")
        if raw["confirmed"] is not True:
            raise ValueError("Explicit withdrawal confirmation required")
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            attempt = next(
                (a for a in row["attempts"] if a["attempt_id"] == raw["attempt_id"]), None
            )
            if attempt is None or attempt["state"] in ("QUEUED", "RUNNING"):
                raise ValueError("Cancel active checks before withdrawing their research binding")
            attempt.update(invalidated=True, reason=reason)
            self.event(
                row,
                "RESEARCH_BINDING_WITHDRAWN",
                attempt_id=attempt["attempt_id"],
                reviewer=reviewer,
                reason=reason,
            )
            self.save(row)
        return self.report(flow_id)

    def recover_locked(self):
        interrupted = []
        for path in self.paths():
            with lease(path):
                try:
                    row = self.read(path.name)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue
                changed = False
                for attempt in row["attempts"]:
                    if attempt["state"] != "RUNNING":
                        continue
                    directory = path / attempt["attempt_id"]
                    if (directory / "checks/lab.sqlite3").is_file():
                        proposal = row["proposals"][attempt["proposal_id"]]
                        checker = check_type(proposal["route"])(self.root, directory)
                        with checker.transaction() as db:
                            for raw in db.execute("SELECT body,sha256 FROM runs").fetchall():
                                child = checker.decode(raw)
                                if child["state"] == "QUEUED":
                                    child.update(
                                        state="CANCELLED", stage="CANCELLED", cancel_requested=True
                                    )
                                    checker.save(db, child)
                        checker.recover_locked()
                    attempt.update(
                        state="INTERRUPTED",
                        error="Supervisor stopped; no automatic retry or promotion",
                    )
                    self.event(row, "CHECK_INTERRUPTED", attempt_id=attempt["attempt_id"])
                    interrupted.append(attempt["attempt_id"])
                    changed = True
                if changed:
                    self.save(row)
        return interrupted

    def recover(self):
        with self.tasks.worker_lock():
            return {"interrupted": self.recover_locked(), "automatic_retry": False}

    def execute(self, flow_id, attempt_id, lock_fd):
        row = self.read(flow_id)
        attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
        proposal = row["proposals"][attempt["proposal_id"]]
        directory = self.directory(flow_id) / attempt_id
        checker, checked, trace, error = None, None, None, None
        try:
            directory.mkdir(exist_ok=False)
            checker = check_type(proposal["route"])(self.root, directory, create=True)
            child = checker.submit(
                {
                    "request_id": attempt_id,
                    "request": proposal["child_plan"]["request"],
                    "plan_fingerprint": proposal["child_plan"]["plan_fingerprint"],
                    "reviewer": attempt["reviewer"],
                    "confirmed": True,
                }
            )
            with lease(self.directory(flow_id)):
                row = self.read(flow_id)
                attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
                attempt["run_id"] = child["run_id"]
                cancel = attempt["cancel_requested"] or attempt["invalidated"] or row["invalidated"]
                self.save(row)
            if cancel:
                checker.cancel(child["run_id"])
                checked = checker.report(child["run_id"])
            else:
                with checker.transaction() as db:
                    child = checker.read(child["run_id"], db)
                    child.update(state="RUNNING", stage="CHECKING")
                    checker.event(child, "STARTED")
                    checker.save(db, child)
                checked = checker.execute(child["run_id"], lock_fd)
                if checked["math_verdict"] == "PROVED" and proposal["route"] == "goal-trace":
                    trace = read_trace(
                        (checker.directory(child["run_id"]) / "candidate/stdout.log").read_text()
                    )
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as caught:
            error = str(caught)
            if checker:
                checker.recover_locked()
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            attempt = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            state = "EXECUTION_ERROR" if error or checked is None else checked["state"]
            if attempt["cancel_requested"]:
                state = "CANCELLED"
            attempt.update(
                state=state,
                error=error
                or (checked["result"]["error"] if checked and checked["result"] else None),
                goal_trace=trace,
            )
            if state == "COMPLETED" and checked["math_verdict"] == "PROVED":
                attempt["source_fingerprint"] = digest(
                    {k: checked[k] for k in ("plan", "alignment", "files", "result")}
                )
            self.event(row, "CHECK_FINISHED", attempt_id=attempt_id, state=state)
            self.save(row)
        return self.report(flow_id)

    def work_once(self):
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            for path in self.paths():
                try:
                    raw = self.read(path.name)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue
                if not any(a["state"] == "QUEUED" for a in raw["attempts"]):
                    continue
                try:
                    current = self.report(path.name)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue
                with lease(path):
                    row = self.read(path.name)
                    self._audit(row)
                    attempt = next((a for a in row["attempts"] if a["state"] == "QUEUED"), None)
                    if attempt is None:
                        continue
                    if current["invalidated"] or attempt["invalidated"]:
                        attempt.update(
                            state="POLICY_BLOCKED",
                            error="Research context or prerequisites invalidated",
                        )
                        self.save(row)
                        continue
                    attempt["state"] = "RUNNING"
                    self.event(row, "CHECK_STARTED", attempt_id=attempt["attempt_id"])
                    self.save(row)
                self.execute(path.name, attempt["attempt_id"], lock_fd)
                return {
                    "worker_state": "EXECUTED",
                    "flow_id": path.name,
                    "attempt_id": attempt["attempt_id"],
                }
        return {"worker_state": "IDLE"}


def example():
    def v(name):
        return {"var": name}

    def op(name, a, b):
        return {name: [a, b]}

    def target(title, original, variables, assumptions, goal):
        return {
            "schema": counterexamples.MODE,
            "title": title,
            "original": original,
            "variables": variables,
            "assumptions": assumptions,
            "goal": goal,
            "timeout_seconds": 30,
        }

    d, a, b, c = (v(n) for n in ("d", "a", "b", "c"))
    hypotheses = [
        {"name": "h" + name, "formula": op("dvd", d, value)}
        for name, value in (("a", a), ("b", b), ("c", c))
    ]
    return [
        {
            "id": "sum",
            "kind": "lemma",
            "target": target(
                "和的整除引理",
                "若 d 整除 a 且 d 整除 b，则 d 整除 a+b。",
                ["d", "a", "b"],
                hypotheses[:2],
                op("dvd", d, op("add", a, b)),
            ),
            "depends_on": [],
        },
        {
            "id": "gcd",
            "kind": "lemma",
            "target": target(
                "公因子的 gcd 引入引理",
                "若 d 整除 a 且 d 整除 b，则 d 整除 gcd(a,b)。",
                ["d", "a", "b"],
                hypotheses[:2],
                op("dvd", d, op("gcd", a, b)),
            ),
            "depends_on": [],
        },
        {
            "id": "problem",
            "kind": "problem",
            "target": target(
                "原问题：和与 gcd 的组合",
                "若自然数 d 分别整除 a、b、c，则 d 整除 gcd(a+b,c)。",
                ["d", "a", "b", "c"],
                hypotheses,
                op("dvd", d, op("gcd", op("add", a, b), c)),
            ),
            "depends_on": ["sum", "gcd"],
        },
    ]
