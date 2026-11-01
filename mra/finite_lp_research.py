"""Project-bound finite lp graphs; every node keeps its complete theorem."""

from copy import deepcopy
from pathlib import Path
import os
import re
import sqlite3
import uuid

from .finite_lp_lab import FiniteLpProofStore, manifest as proof_manifest
from .finite_lp_language import MODE, bounded
from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_lab import now
from .research import ResearchStore
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import TaskStore, _identifier
from .typed_research import TypedResearchStore, source_identity

SCHEMA = "proof-project-research-v4"
LIMITS = {"graphs": 50, "nodes": 8, "attempts": 16, "worker_seconds": 480, "events": 512}
ACTIVE = ("QUEUED", "RUNNING")
ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error)
SOURCES = (
    "mra/finite_lp_research.py",
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
        "provider": proof_manifest(root),
        "files": {name: file_hash(root / name) for name in SOURCES},
    }


def child_preview(root, request):
    checker = object.__new__(FiniteLpProofStore)
    checker.root = Path(root).resolve()
    return checker.preview(request)


def graph(root, nodes):
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= LIMITS["nodes"]:
        raise ValueError("A analysis research task needs 1–8 complete nodes")
    by_id, compiled = {}, {}
    for node in nodes:
        exact_object(node, {"id", "kind", "depends_on", "target"}, "Analysis research node")
        key = _identifier(node["id"])
        if len(key) > 32 or key in by_id or node["kind"] not in ("problem", "lemma", "subproblem"):
            raise ValueError("Use distinct short node IDs and a supported node kind")
        deps = node["depends_on"]
        if not isinstance(deps, list) or len(deps) > 7 or any(not isinstance(d, str) for d in deps):
            raise ValueError("Use at most seven distinct prerequisite IDs")
        if len(set(deps)) != len(deps):
            raise ValueError("Duplicate prerequisite")
        by_id[key] = node
        compiled[key] = child_preview(root, node["target"])
    problems = [n["id"] for n in nodes if n["kind"] == "problem"]
    if len(problems) != 1:
        raise ValueError("Exactly one original problem is required")
    visiting, seen, order = set(), set(), []

    def visit(key):
        if key not in by_id or key in visiting:
            raise ValueError("Analysis research prerequisites must form a complete acyclic graph")
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


class FiniteLpResearchStore(TypedResearchStore):
    """Reuse sealed graph lifecycle; own identity, publication and calculus plans."""

    def __init__(self, root, *, base=None):
        self.root = Path(root).resolve()
        canonical_base = self.root / "artifacts/proof-finite-lp-research"
        self.base = canonical_base if base is None else Path(base).absolute()
        if (
            self.base.resolve() != self.base
            or not self.base.is_relative_to(self.root / "artifacts")
            or (
                self.base != canonical_base and self.base.name != "proof-finite-lp-research-private"
            )
        ):
            raise ValueError("Use canonical or isolated proof-finite-lp-research-private storage")
        self.base.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(self.root)
        self.research = ResearchStore(self.root)

    def directory(self, graph_id):
        directory = self.base / identifier(graph_id, "proof4flow")
        if self.base.resolve() != self.base or directory.resolve() != directory:
            raise ValueError("Analysis task storage cannot be redirected")
        return directory

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Analysis task storage cannot be redirected")
        values = sorted(
            p for p in self.base.iterdir() if re.fullmatch(r"proof4flow-[a-f0-9]{32}", p.name)
        )
        if len(values) > LIMITS["graphs"]:
            raise ValueError("Analysis task storage budget exhausted")
        return values

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
                "Analysis research record",
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
                raise ValueError("Analysis research record identity or bounds changed")
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
                    "Analysis research attempt",
                )
                identifier(attempt["attempt_id"], "proof4attempt")
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
                    raise ValueError("Analysis research attempt identity or shape changed")
                identities.add(attempt["attempt_id"])
                requests.add(attempt["request_id"])
            return row
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            atomic(directory / "invalidated.json", {"at": now(), "reason": str(error)})
            raise

    def checker(self, graph_id):
        base = self.directory(graph_id) / "proof-finite-lp-language-private"
        if not (base / "lab.sqlite3").is_file():
            raise ValueError("Analysis child ledger unavailable; never recreate missing evidence")
        with sqlite3.connect((base / "lab.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT id,mode FROM proof_finite_lp_identity").fetchall() != [(1, MODE)]:
                raise ValueError("Analysis child ledger identity changed")
        return FiniteLpProofStore(self.root, base=base)

    def preview(self, raw):
        bounded(raw)
        exact_object(raw, {"project_id", "branch_id", "nodes"}, "Complete calculus research task")
        order, problem, plans = graph(self.root, raw["nodes"])
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
                "Every node retains its own full variables, hypotheses, measures and target.",
                "Prerequisites bind exact evidence and schedule only; no hypotheses or proof terms are imported.",
                "The original problem needs its own exact Lean check and independent replay.",
                "At most 16 attempts and 480 reserved worker seconds; 1–40 shared seconds per check, integrity separate.",
                "Manual alignment is a local assertion; no model, automatic retries, PDE or novelty claim.",
            ],
        }
        value["plan_fingerprint"] = digest(value)
        return value

    def create(self, raw):
        exact_object(raw, {"request_id", "request", "plan_fingerprint"}, "Save analysis task")
        _identifier(raw["request_id"])
        plan = self.preview(raw["request"])
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Complete analysis preview changed")
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                try:
                    old = self.read(path.name)
                except ERRORS:
                    continue
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID belongs to a different analysis task")
                    key = old["graph_id"]
                    break
            else:
                if len(paths) >= LIMITS["graphs"]:
                    raise ValueError("Analysis task storage budget exhausted")
                key = "proof4flow-" + uuid.uuid4().hex
                directory = self.directory(key)
                staging = self.base / (".creating-" + key)
                if staging.resolve() != staging or directory.exists():
                    raise ValueError("Analysis publication location changed")
                staging.mkdir()
                atomic(staging / "plan.json", plan)
                FiniteLpProofStore(self.root, base=staging / "proof-finite-lp-language-private")
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
                atomic(staging / "record.json", row)
                staging.rename(directory)
                fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
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
            raise ValueError("Analysis graph plan fingerprint mismatch")
        reason = None
        try:
            if (directory / "invalidated.json").exists():
                reason = "Previously detected graph damage"
            elif plan["source_manifest"] != manifest(self.root):
                reason = "Analysis graph implementation changed"
            elif plan["parent"] != self.parent(
                plan["parent"]["project_id"], plan["parent"]["branch_id"]
            ):
                reason = "Original project question or branch context changed"
        except ERRORS as error:
            if isinstance(error, sqlite3.Error) and storage_busy(error):
                raise
            reason = "Original context unavailable: " + str(error)
        if reason and not (directory / "invalidated.json").exists():
            atomic(directory / "invalidated.json", {"permanent": True, "reason": reason})
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

    def submit(self, graph_id, data):
        exact_object(
            data,
            {"request_id", "node_id", "execution_fingerprint", "reviewer", "confirmed"},
            "Analysis node submission",
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
                raise ValueError("Analysis graph check budget exhausted")
            reserved = sum(
                plan["node_plans"][a["node_id"]]["request"]["timeout_seconds"]
                for a in row["attempts"]
            )
            if (
                reserved + current["child_plan"]["request"]["timeout_seconds"]
                > LIMITS["worker_seconds"]
            ):
                raise ValueError("Analysis task worker budget exhausted")
            key = "proof4attempt-" + uuid.uuid4().hex
            checker = self.checker(graph_id)
            with checker.transaction(False) as db:
                if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 32:
                    raise ValueError("Analysis graph child storage budget exhausted")
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

    def catalog(self, project_id, branch_id):
        parent = self.parent(project_id, branch_id)
        values = []
        for directory in self.paths():
            try:
                row = self.read(directory.name)
                plan = _envelope(directory / "plan.json")
            except ERRORS as error:
                if isinstance(error, sqlite3.Error) and storage_busy(error):
                    raise
                continue
            if (plan["parent"]["project_id"], plan["parent"]["branch_id"]) == (
                project_id,
                branch_id,
            ):
                values.append(
                    {
                        "graph_id": directory.name,
                        "created_at": row["created_at"],
                        "invalidated": row["invalidated"],
                        "nodes": len(plan["order"]),
                        "title": plan["node_plans"][plan["problem_id"]]["request"]["title"],
                    }
                )
        return {
            "schema": SCHEMA,
            "parent": parent,
            "graphs": values,
            "examples": examples(),
            "limits": dict(LIMITS),
        }


def examples():
    from .finite_lp_examples import examples

    return examples()
