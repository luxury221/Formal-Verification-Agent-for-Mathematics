"""Versioned dependency scheduling from audited evidence; full targets stay intact."""

from copy import deepcopy
import sqlite3
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lean_strategies import MODE as STRATEGY_MODE
from .mathlib_lab import now
from .number_theory_search import SCHEMA as SEARCH_MODE, search
from .research_evidence import ResearchEvidenceStore, text
from .research_flow import (
    LIMITS,
    ResearchFlowStore,
    atomic,
    check_type,
    child_preview,
    identifier,
    lease,
    manifest as flow_manifest,
)
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier

SCHEMA = "dependency-research-continuation-v1"
ROUTES = ("omega", "simp-gcd", "proof-search")
ACTIVE = ("QUEUED", "RUNNING")


def manifest(root):
    return {
        "schema": SCHEMA,
        "extension": file_hash(root / "mra/research_continuation.py"),
        "evidence_adapter": file_hash(root / "mra/research_evidence.py"),
        "flow": flow_manifest(root),
    }


class ResearchContinuationStore(ResearchFlowStore):
    """Reuse frozen check execution, cancellation and withdrawal in an isolated ledger."""

    def __init__(self, root):
        super().__init__(root)
        self.base = self.root / "artifacts/research-continuations"
        if self.base.resolve() != self.base:
            raise ValueError("Continuation storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.links = ResearchEvidenceStore(self.root)

    def read(self, flow_id):
        path = self.directory(flow_id)
        try:
            row = _envelope(path / "record.json")
            if (
                row["schema"] != SCHEMA
                or row["flow_id"] != flow_id
                or not isinstance(row["attempts"], list)
                or not isinstance(row["proposals"], dict)
                or not isinstance(row["events"], list)
                or type(row["invalidated"]) is not bool
                or len(row["attempts"]) > LIMITS["attempts"]
                or len(row["proposals"]) > LIMITS["proposals"]
            ):
                raise ValueError("Malformed continuation ledger")
            for attempt in row["attempts"]:
                exact_object(
                    attempt,
                    {
                        "attempt_id",
                        "request_id",
                        "submission_fingerprint",
                        "proposal_id",
                        "node_id",
                        "reviewer",
                        "confirmed",
                        "prerequisites",
                        "state",
                        "run_id",
                        "invalidated",
                        "reason",
                        "cancel_requested",
                        "source_fingerprint",
                        "goal_trace",
                        "error",
                    },
                    "Continuation attempt",
                )
                identifier(attempt["attempt_id"], "attempt")
                if (
                    attempt["proposal_id"] not in row["proposals"]
                    or not isinstance(attempt["prerequisites"], dict)
                    or not isinstance(attempt["state"], str)
                    or type(attempt["invalidated"]) is not bool
                    or type(attempt["cancel_requested"]) is not bool
                ):
                    raise ValueError("Malformed continuation attempt")
            if len({a["attempt_id"] for a in row["attempts"]}) != len(row["attempts"]):
                raise ValueError("Duplicate continuation attempt")
            return row
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            atomic(path / "invalidated.json", {"reason": str(error)})
            raise

    def records(self):
        rows = []
        with lease(self.base):
            for path in self.paths():
                try:
                    rows.append((path, self.read(path.name)))
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue
        yield from rows

    def preview(self, flow_id):
        original = self.links.flows.report(flow_id)
        if original["invalidated"]:
            raise ValueError("Create a newly reviewed original research task")
        plan = {
            "schema": SCHEMA,
            "original_flow_id": flow_id,
            "original_plan_fingerprint": original["plan_fingerprint"],
            **{
                k: deepcopy(original["plan"][k])
                for k in ("parent", "nodes", "order", "problem_id", "budget")
            },
            "source_manifest": manifest(self.root),
            "math_verdict": "UNKNOWN",
            "model_calls": 0,
            "limits": LIMITS,
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def create(self, raw):
        exact_object(
            raw, {"flow_id", "request_id", "plan_fingerprint"}, "Create dependency continuation"
        )
        _identifier(raw["request_id"])
        plan = self.preview(raw["flow_id"])
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Original context changed; preview again")
        path = self.directory(raw["flow_id"])
        with lease(self.base):
            if path.exists():
                row = self.read(raw["flow_id"])
                if row["request_id"] != raw["request_id"] or row[
                    "submission_fingerprint"
                ] != digest(raw):
                    raise ValueError(
                        "This flow already has a continuation; open its existing ledger"
                    )
            else:
                if len(self.paths()) >= LIMITS["flows"]:
                    raise ValueError("Continuation flow budget exhausted")
                path.mkdir()
                atomic(path / "plan.json", plan)
                self.save(
                    {
                        "schema": SCHEMA,
                        "flow_id": raw["flow_id"],
                        "request_id": raw["request_id"],
                        "submission_fingerprint": digest(raw),
                        "created_at": now(),
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "invalidated": False,
                        "reason": None,
                        "proposals": {},
                        "attempts": [],
                        "events": [],
                    }
                )
        return self.report(raw["flow_id"])

    def report(self, flow_id):
        with lease(self.base):
            exists = self.directory(flow_id).exists()
        if not exists:
            return {
                "created": False,
                "flow_id": flow_id,
                "plan": self.preview(flow_id),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
                "model_calls": 0,
            }
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            before = deepcopy(row)
            try:
                plan, states, checks = self._audit(row)
            except (WorkerBusy, sqlite3.Error):
                # Contention is temporary; never leave a permanent damage marker.
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
            "created": True,
            "routes": ROUTES,
            "limitations": [
                "Prerequisites schedule research only; no Lean assumptions or proof terms are injected.",
                "Each descendant needs its own full target audit and independent kernel replay.",
                "The original route and prefix association ledgers keep their own verdicts.",
                "One explicitly reviewed check at a time; no automatic retries or model planning.",
            ],
        }

    @staticmethod
    def prerequisites(current, node):
        return {d: current["node_states"][d]["binding_fingerprint"] for d in node["depends_on"]}

    def _audit(self, row):
        path = self.directory(row["flow_id"])
        plan = _envelope(path / "plan.json")
        if (
            plan["plan_fingerprint"] != row["plan_fingerprint"]
            or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
            != row["plan_fingerprint"]
        ):
            raise ValueError("Continuation plan changed")
        evidence = self.links.report(row["flow_id"])
        original = evidence["research_task"]
        if (
            (path / "invalidated.json").exists()
            or original["invalidated"]
            or plan["source_manifest"] != manifest(self.root)
            or original["plan_fingerprint"] != plan["original_plan_fingerprint"]
            or any(
                plan[k] != original["plan"][k] for k in ("nodes", "order", "problem_id", "parent")
            )
        ):
            row.update(
                invalidated=True,
                reason="Continuation implementation, plan or original context changed",
            )
        states, checks = {}, {}
        nodes = {n["id"]: n for n in plan["nodes"]}
        for key in plan["order"]:
            node = nodes[key]
            prerequisites = {d: states[d]["binding_fingerprint"] for d in node["depends_on"]}
            blocked = [d for d in node["depends_on"] if states[d]["math_verdict"] != "PROVED"]
            candidates = []
            native = original["node_states"][key]
            if not row["invalidated"] and not blocked:
                if native["math_verdict"] in ("PROVED", "REFUTED"):
                    candidates.append(
                        (
                            native["math_verdict"],
                            {
                                "origin": "ORIGINAL_ROUTE",
                                "fingerprint": native["binding_fingerprint"],
                            },
                        )
                    )
                for binding_id in evidence["node_evidence"][key]["active_bindings"]:
                    candidates.append(
                        (
                            "PROVED",
                            {
                                "origin": "PREFIX_LINK",
                                "binding_id": binding_id,
                                "fingerprint": digest(evidence["source_evidence"][binding_id]),
                            },
                        )
                    )
            for attempt in (a for a in row["attempts"] if a["node_id"] == key):
                if not attempt["invalidated"] and (
                    row["invalidated"] or blocked or attempt["prerequisites"] != prerequisites
                ):
                    attempt.update(
                        invalidated=True,
                        reason="Selected prerequisite evidence or original context is unavailable",
                    )
                    self.event(row, "DEPENDENCY_INVALIDATED", attempt_id=attempt["attempt_id"])
                if attempt["state"] != "COMPLETED" or attempt["invalidated"]:
                    if attempt["run_id"] and attempt["state"] not in ACTIVE:
                        try:
                            proposal = row["proposals"][attempt["proposal_id"]]
                            checks[attempt["attempt_id"]] = check_type(proposal["route"])(
                                self.root, path / attempt["attempt_id"]
                            ).report(attempt["run_id"])
                        except WorkerBusy:
                            raise
                        except sqlite3.Error as error:
                            if storage_busy(error):
                                raise
                        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                            pass
                    continue
                try:
                    proposal = row["proposals"][attempt["proposal_id"]]
                    if proposal["proposal_fingerprint"] != digest(
                        {k: v for k, v in proposal.items() if k != "proposal_fingerprint"}
                    ):
                        raise ValueError("Reviewed continuation proposal changed")
                    child = check_type(proposal["route"])(
                        self.root, path / attempt["attempt_id"]
                    ).report(attempt["run_id"])
                    checks[attempt["attempt_id"]] = child
                    if (
                        child["state"],
                        child["math_verdict"],
                        child["evidence_level"],
                        child["invalidated"],
                    ) != ("COMPLETED", "PROVED", "E4", False):
                        raise ValueError("Child no longer has complete E4 evidence")
                    if (
                        child["plan"] != proposal["child_plan"]
                        or child["plan"]["formal_statement"] != native["formal_statement"]
                    ):
                        raise ValueError("Complete original target or child plan changed")
                    source = digest({k: child[k] for k in ("plan", "alignment", "files", "result")})
                    if (
                        source != attempt["source_fingerprint"]
                        or child["alignment"]["confirmed"] is not True
                    ):
                        raise ValueError("Semantic alignment or checked evidence changed")
                    candidates.append(
                        (
                            "PROVED",
                            {
                                "origin": "CONTINUATION",
                                "attempt_id": attempt["attempt_id"],
                                "fingerprint": source,
                            },
                        )
                    )
                except WorkerBusy:
                    raise
                except sqlite3.Error as error:
                    if storage_busy(error):
                        raise
                    attempt.update(invalidated=True, reason=str(error))
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    attempt.update(invalidated=True, reason=str(error))
            verdicts = {v for v, _ in candidates}
            # Stable earliest provenance: adding unused alternatives does not revoke descendants.
            chosen = candidates[0] if len(verdicts) == 1 else None
            verdict, source = chosen if chosen else ("UNKNOWN", None)
            states[key] = {
                "id": key,
                "formal_statement": native["formal_statement"],
                "math_verdict": verdict,
                "evidence_level": "E4" if chosen else "E0",
                "source": source,
                "binding_fingerprint": digest(source) if source else None,
                "blocked_by": blocked,
                "route_math_verdict": native["math_verdict"],
                "status": "INVALIDATED"
                if row["invalidated"]
                else "CONFLICT"
                if len(verdicts) > 1
                else verdict
                if chosen
                else "BLOCKED"
                if blocked
                else "READY",
            }
        return plan, states, checks

    def propose(self, flow_id, raw):
        exact_object(raw, {"node_id", "route", "request_id"}, "Dependency check proposal")
        _identifier(raw["request_id"])
        if raw["route"] not in ROUTES:
            raise ValueError("Select omega, simp-gcd or proof-search")
        current = self.report(flow_id)
        if not current["created"] or current["invalidated"]:
            raise ValueError("Save a current continuation first")
        node = next((n for n in current["plan"]["nodes"] if n["id"] == raw["node_id"]), None)
        if node is None or current["node_states"][node["id"]]["blocked_by"]:
            raise ValueError("Every prerequisite needs current PROVED/E4 evidence")
        experiment = None
        if raw["route"] == "proof-search":
            experiment = search(
                {
                    "schema": SEARCH_MODE,
                    "target": node["target"],
                    "budget": {"max_depth": 6, "max_expansions": 3000},
                },
                self.root,
            )
            request = experiment["candidate"]
        else:
            request = {**node["target"], "schema": STRATEGY_MODE, "strategy": raw["route"]}
        proposal = {
            "proposal_id": "proposal-" + uuid.uuid4().hex,
            "request_id": raw["request_id"],
            "input_fingerprint": digest(raw),
            "node_id": node["id"],
            "route": raw["route"],
            "created_at": now(),
            "experiment": experiment,
            "child_plan": child_preview(self.root, raw["route"], request) if request else None,
            "prerequisites": self.prerequisites(current, node),
            "prerequisite_sources": {
                d: current["node_states"][d]["source"] for d in node["depends_on"]
            },
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        proposal["proposal_fingerprint"] = digest(proposal)
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            if row != {k: current[k] for k in row}:
                raise ValueError("Continuation changed; reload before preparing")
            for old in row["proposals"].values():
                if old["request_id"] == raw["request_id"]:
                    if old["input_fingerprint"] != digest(raw):
                        raise ValueError("Request ID input changed")
                    return deepcopy(old)
            if len(row["proposals"]) >= LIMITS["proposals"]:
                raise ValueError("Proposal budget exhausted")
            row["proposals"][proposal["proposal_id"]] = proposal
            self.event(row, "DEPENDENCY_PROPOSAL_PREPARED", proposal_id=proposal["proposal_id"])
            self.save(row)
        return proposal

    def submit(self, flow_id, raw):
        exact_object(
            raw,
            {"request_id", "proposal_id", "proposal_fingerprint", "reviewer", "confirmed"},
            "Named continuation check",
        )
        _identifier(raw["request_id"])
        reviewer = text(raw["reviewer"], 200, "Named reviewer")
        if raw["confirmed"] is not True:
            raise ValueError("Confirm the full target, exact dependencies and budget")
        current = self.report(flow_id)
        if not current["created"]:
            raise ValueError("Save the continuation first")
        # Request identity is checked before stale-source guards; retries never revive old work.
        for old in current["attempts"]:
            if old["request_id"] == raw["request_id"]:
                if old["submission_fingerprint"] != digest(raw):
                    raise ValueError("Request ID input changed")
                return deepcopy(old)
        proposal = current["proposals"].get(raw["proposal_id"])
        if current["invalidated"] or not proposal or not proposal["child_plan"]:
            raise ValueError("A current complete proposal is required")
        node = next(n for n in current["plan"]["nodes"] if n["id"] == proposal["node_id"])
        if (
            raw["proposal_fingerprint"] != proposal["proposal_fingerprint"]
            or proposal["proposal_fingerprint"]
            != digest({k: v for k, v in proposal.items() if k != "proposal_fingerprint"})
            or current["node_states"][node["id"]]["blocked_by"]
            or proposal["prerequisites"] != self.prerequisites(current, node)
            or child_preview(self.root, proposal["route"], proposal["child_plan"]["request"])
            != proposal["child_plan"]
        ):
            raise ValueError("Exact dependencies or reviewed plan changed; prepare again")
        with lease(self.directory(flow_id)):
            row = self.read(flow_id)
            if row != {k: current[k] for k in row}:
                raise ValueError("Continuation changed concurrently; reload")
            if len(row["attempts"]) >= LIMITS["attempts"] or any(
                a["state"] in ACTIVE for a in row["attempts"]
            ):
                raise ValueError("Attempt budget exhausted or another continuation check is active")
            attempt = {
                "attempt_id": "attempt-" + uuid.uuid4().hex,
                "request_id": raw["request_id"],
                "submission_fingerprint": digest(raw),
                "proposal_id": proposal["proposal_id"],
                "node_id": node["id"],
                "reviewer": reviewer,
                "confirmed": True,
                "prerequisites": proposal["prerequisites"],
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
            self.event(row, "NAMED_CONTINUATION_QUEUED", attempt_id=attempt["attempt_id"])
            self.save(row)
        return attempt

    def recover_locked(self):
        interrupted = []
        for path, initial in self.records():
            if not any(a["state"] == "RUNNING" for a in initial["attempts"]):
                continue
            with lease(path):
                row = self.read(path.name)
                for a in row["attempts"]:
                    if a["state"] != "RUNNING":
                        continue
                    try:
                        directory = path / a["attempt_id"]
                        if (directory / "checks/lab.sqlite3").is_file():
                            checker = check_type(row["proposals"][a["proposal_id"]]["route"])(
                                self.root, directory
                            )
                            with checker.transaction() as db:
                                for raw in db.execute("SELECT body,sha256 FROM runs").fetchall():
                                    child = checker.decode(raw)
                                    if child["state"] == "QUEUED":
                                        child.update(
                                            state="CANCELLED",
                                            stage="CANCELLED",
                                            cancel_requested=True,
                                        )
                                        checker.save(db, child)
                            checker.recover_locked()
                    except WorkerBusy:
                        raise
                    except sqlite3.Error as error:
                        if storage_busy(error):
                            raise
                        a.update(invalidated=True, reason=str(error))
                    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                        a.update(invalidated=True, reason=str(error))
                    a.update(
                        state="INTERRUPTED",
                        error="Supervisor stopped; no automatic retry or promotion",
                    )
                    interrupted.append(a["attempt_id"])
                    self.event(row, "CONTINUATION_INTERRUPTED", attempt_id=a["attempt_id"])
                self.save(row)
        return interrupted

    def work_once(self):
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            for path, initial in self.records():
                if not any(a["state"] == "QUEUED" for a in initial["attempts"]):
                    continue
                try:
                    self.report(path.name)
                except (WorkerBusy, sqlite3.Error):
                    raise
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    # A corrupt plan is quarantined; queued entries cannot execute.
                    with lease(path):
                        row = self.read(path.name)
                        for a in row["attempts"]:
                            if a["state"] == "QUEUED":
                                a.update(
                                    state="POLICY_BLOCKED",
                                    invalidated=True,
                                    error="Continuation audit unavailable",
                                )
                        self.save(row)
                    continue
                with lease(path):
                    row = self.read(path.name)
                    self._audit(row)
                    a = next((a for a in row["attempts"] if a["state"] == "QUEUED"), None)
                    if a is None:
                        continue
                    if row["invalidated"] or a["invalidated"]:
                        a.update(
                            state="POLICY_BLOCKED",
                            error="Prerequisite evidence or context invalidated",
                        )
                        self.save(row)
                        continue
                    a["state"] = "RUNNING"
                    self.event(row, "CONTINUATION_STARTED", attempt_id=a["attempt_id"])
                    self.save(row)
                self.execute(path.name, a["attempt_id"], lock_fd)
                return {
                    "worker_state": "EXECUTED",
                    "flow_id": path.name,
                    "attempt_id": a["attempt_id"],
                }
        return {"worker_state": "IDLE"}
