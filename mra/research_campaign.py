"""Reviewed whole-graph execution with exact existing proofs and typed node imports."""

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
import math
import fcntl
import os
import time
import uuid

from .campaign_checks import CampaignChecks
from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition import bindings
from .lemma_composition_terms import MODE as COMPILE_MODE, compile_composition, target
from .library_composition import MODE as COMPOSE_MODE, manifest as library_manifest
from .mathlib_lab import now
from .mathlib_terms import text
from .model_service import integer_field
from .proof_library import ProofLibraryStore, proof_data, provenance, signature
from .research_compositions import complete_target
from .research_flow import ResearchFlowStore, atomic, identifier
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier

SCHEMA = "reviewed-proof-campaign-v1"
ACTIVE = ("QUEUED", "RUNNING")
VISIBLE = ("COMPLETED", "FAILED", "BUDGET_EXHAUSTED")
LIMITS = {"campaigns": 50, "nodes": 8, "requests": 64, "bytes": 196608}
_path = ContextVar("mra_campaign_source_path", default=())


@contextmanager
def lease(directory):
    """Serialize campaign reads and commits, including across local processes.

    A reader must not turn a live supervisor into an interrupted run by briefly
    owning the ledger. Keep the global worker lock nonblocking; only this bounded
    record audit waits for its current owner. No checker executes under this lock.
    """
    if directory.resolve() != directory:
        raise ValueError("Campaign directory cannot be redirected")
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def snapshot(row):
    return {k: deepcopy(row[k]) for k in ("run_id", "plan", "alignment", "files", "result")}


def e4(row):
    if (
        row["state"],
        row["math_verdict"],
        row["evidence_level"],
        row["invalidated"],
        row["cancel_requested"],
        row["artifact_status"],
    ) != ("COMPLETED", "PROVED", "E4", False, False, "MATCHES_RECORDED_HASHES"):
        raise ValueError("An intact, active, complete E4 proof is required")
    if (
        row["alignment"]["confirmed"] is not True
        or row["result"]["candidate"]["pid"] == row["result"]["replay"]["pid"]
        or row["result"]["candidate"]["audit"]["type_matches"] is not True
        or row["result"]["replay"]["audit"]["mode"] != "data-only-kernel-replay-v1"
    ):
        raise ValueError("Named alignment and separate genuine replay are required")
    return snapshot(row)


class ResearchCampaignStore:
    def __init__(self, root, library=None):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-campaigns"
        if self.base.resolve() != self.base:
            raise ValueError("Campaign storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.library = library or ProofLibraryStore(self.root)
        self.flows = ResearchFlowStore(self.root)
        self.tasks = self.library.compositions.tasks

    def manifest(self):
        return {
            "schema": SCHEMA,
            "files": {
                n: file_hash(self.root / n)
                for n in (
                    "mra/research_campaign.py",
                    "mra/campaign_checks.py",
                    "mra/research_compositions.py",
                )
            },
            "provider": library_manifest(self.root),
        }

    def directory(self, key):
        path = self.base / identifier(key, "campaign")
        if path.resolve() != path:
            raise ValueError("Campaign path redirected")
        return path

    def paths(self):
        return sorted(p for p in self.base.glob("campaign-*") if p.is_dir())

    @staticmethod
    def event(row, kind, **detail):
        if len(row["events"]) >= 256:
            raise ValueError("Campaign event budget exhausted")
        row["events"].append({"at": now(), "kind": kind, **detail})

    def save(self, row):
        atomic(self.directory(row["campaign_id"]) / "record.json", row)

    def read(self, key):
        path = self.directory(key)
        try:
            row = _envelope(path / "record.json")
            if (
                row["schema"] != SCHEMA
                or row["campaign_id"] != key
                or len(row["nodes"]) > LIMITS["nodes"]
                or len({n["node_id"] for n in row["nodes"]}) != len(row["nodes"])
                or len(row["requests"]) > LIMITS["requests"]
                or type(row["invalidated"]) is not bool
                or type(row["cancel_requested"]) is not bool
                or row["state"]
                not in (*ACTIVE, *VISIBLE, "CANCELLED", "INTERRUPTED", "POLICY_BLOCKED")
                or not 1 <= len(row["nodes"]) <= LIMITS["nodes"]
                or any(
                    n["state"]
                    not in ("PENDING", "RUNNING", "VERIFIED", "FAILED", "STOPPED", "INTERRUPTED")
                    or type(n["invalidated"]) is not bool
                    or not isinstance(n["prerequisites"], dict)
                    for n in row["nodes"]
                )
                or any(
                    type(row["used"][k]) not in (int, float)
                    or not math.isfinite(row["used"][k])
                    or row["used"][k] < 0
                    for k in ("checks", "worker_seconds_reserved", "worker_seconds_actual")
                )
                or type(row["used"]["checks"]) is not int
                or row["used"]["checks"] > 8
                or row["used"]["worker_seconds_reserved"] > 240
                or not isinstance(row["events"], list)
                or len(row["events"]) > 256
            ):
                raise ValueError("Campaign ledger identity or shape changed")
            return row
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            if path.exists():
                atomic(path / "invalidated.json", {"reason": str(error)})
            raise

    def plan(self, row):
        path = self.directory(row["campaign_id"])
        try:
            plan = _envelope(path / "plan.json")
            if (
                plan["plan_fingerprint"] != row["plan_fingerprint"]
                or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
                != row["plan_fingerprint"]
                or [n["node_id"] for n in row["nodes"]] != plan["flow_plan"]["order"]
            ):
                raise ValueError("Campaign plan differs from named review")
            return plan
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            atomic(path / "invalidated.json", {"reason": str(error)})
            raise

    def existing(self, ref, node):
        ref = self.library.source_ref(ref)
        store = self.library.source_store(ref["origin"])
        source = e4(store.report(ref["run_id"]))
        if complete_target(source["plan"]["request"]) != node["target"]:
            raise ValueError("Existing proof must match the exact full research target")
        if source["plan"]["formal_statement"] != target(node["target"])["formal_statement"]:
            raise ValueError("Existing proof type differs from the complete node type")
        return source

    def preview(self, raw):
        if len(canonical(raw)) > LIMITS["bytes"]:
            raise ValueError("Campaign input exceeds 192 KiB")
        raw = strict_json(canonical(raw).decode())
        exact_object(raw, {"flow_id", "actions", "budget"}, "Reviewed research campaign")
        flow = self.flows.report(raw["flow_id"])
        if flow["invalidated"]:
            raise ValueError("Create a newly reviewed research task")
        exact_object(
            raw["budget"], {"max_checks", "worker_seconds", "wall_seconds"}, "Total budget"
        )
        for name, high in (("max_checks", 8), ("worker_seconds", 240), ("wall_seconds", 600)):
            integer_field(raw["budget"][name], name, 1, high)
        nodes = {n["id"]: n for n in flow["plan"]["nodes"]}
        if not isinstance(raw["actions"], dict) or set(raw["actions"]) != set(nodes):
            raise ValueError("Choose exactly one explicit action for every original research node")
        prepared = {}
        for key in flow["plan"]["order"]:
            action, node = raw["actions"][key], nodes[key]
            if not isinstance(action, dict):
                raise ValueError("Each node needs an explicit existing or compose action")
            if action.get("kind") == "existing":
                exact_object(action, {"kind", "source"}, "Existing node action")
                prepared[key] = {**action, "source_snapshot": self.existing(action["source"], node)}
            elif action.get("kind") == "compose":
                exact_object(action, {"kind", "request"}, "Composition node action")
                request = action["request"]
                if (
                    not isinstance(request, dict)
                    or request.get("schema") != COMPOSE_MODE
                    or complete_target(request) != node["target"]
                ):
                    raise ValueError(
                        "New check must preserve title, wording, variables, every premise, goal and budget"
                    )
                sources, inputs = [], []
                for binding in bindings(request):
                    item = binding["item_id"]
                    if item.startswith("node."):
                        dep = item[5:]
                        if dep not in node["depends_on"] or dep not in prepared:
                            raise ValueError(
                                "Node proof inputs must be explicit earlier direct prerequisites"
                            )
                        descriptor = {
                            **binding,
                            "request": signature(nodes[dep]["target"]),
                            "formal_statement": target(nodes[dep]["target"])["formal_statement"],
                        }
                        inputs.append({"kind": "node", "node_id": dep, "signature": descriptor})
                    else:
                        descriptor, _ = self.library.load_source(binding)
                        inputs.append({"kind": "archive", "source": descriptor})
                    sources.append(descriptor)
                compiled = compile_composition({**request, "schema": COMPILE_MODE}, sources)
                compiled["request"]["schema"] = COMPOSE_MODE
                prepared[key] = {**action, "compiled": compiled, "inputs": inputs}
            else:
                raise ValueError("Select an explicit existing or compose action")
        plan = {
            "schema": SCHEMA,
            "input": raw,
            "flow_plan": flow["plan"],
            "actions": prepared,
            "source_manifest": self.manifest(),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "new_lean_calls": 0,
            "model_calls": 0,
            "resolution_policy": "Only proof-data identities for declared prerequisite nodes are resolved during execution; all types, terms and root archives are pre-reviewed.",
            "limitations": [
                "Local names are not authenticated; both processes use the same Lean kernel.",
                "Graph and proof programs are human supplied; no automatic planning, retry, sharing or training.",
                "Full reserved time is charged per started check; integrity scans are not preemptible.",
            ],
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def submit(self, raw):
        exact_object(
            raw,
            {"request_id", "input", "plan_fingerprint", "reviewer", "confirmed"},
            "Named campaign",
        )
        _identifier(raw["request_id"])
        reviewer = text(raw["reviewer"], 200, "Named reviewer")
        if raw["confirmed"] is not True:
            raise ValueError(
                "Confirm the complete graph, proof programs, exact inputs and total budget"
            )
        with lease(self.base):
            for path in self.paths():
                old = self.read(path.name)
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Campaign request ID belongs to different content")
                    return self.report(old["campaign_id"])
        plan = self.preview(raw["input"])
        if plan["plan_fingerprint"] != raw["plan_fingerprint"]:
            raise ValueError("Campaign preview changed; review again")
        with lease(self.base):
            for path in self.paths():
                old = self.read(path.name)
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Campaign request ID belongs to different content")
                    return self.report(old["campaign_id"])
            if len(self.paths()) >= LIMITS["campaigns"]:
                raise ValueError("Campaign storage budget exhausted")
            key = "campaign-" + uuid.uuid4().hex
            path = self.directory(key)
            path.mkdir()
            atomic(path / "plan.json", plan)
            row = {
                "schema": SCHEMA,
                "campaign_id": key,
                "request_id": raw["request_id"],
                "submission_fingerprint": digest(raw),
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": reviewer,
                "created_at": now(),
                "state": "QUEUED",
                "cancel_requested": False,
                "invalidated": False,
                "reason": None,
                "deadline": None,
                "started_at": None,
                "used": {"checks": 0, "worker_seconds_reserved": 0, "worker_seconds_actual": 0},
                "requests": {},
                "events": [],
                "nodes": [
                    {
                        "node_id": k,
                        "state": "PENDING",
                        "invalidated": False,
                        "reason": None,
                        "child_id": None,
                        "source_fingerprint": None,
                        "prerequisites": {},
                        "resolution_fingerprint": None,
                    }
                    for k in plan["flow_plan"]["order"]
                ],
            }
            self.event(row, "REVIEWED_CAMPAIGN_QUEUED")
            self.save(row)
        return self.report(key)

    @staticmethod
    def expired(row):
        return row["deadline"] is not None and time.time() >= row["deadline"]

    def context_reason(self, row, plan):
        if (self.directory(row["campaign_id"]) / "invalidated.json").exists():
            return "Previously detected campaign ledger or plan damage"
        if plan["source_manifest"] != self.manifest():
            return "Campaign or verification implementation changed"
        flow = self.flows.report(plan["input"]["flow_id"])
        if flow["invalidated"] or flow["plan"] != plan["flow_plan"]:
            return "Original research context or full task graph changed"
        return None

    def checked(self, row, plan, key):
        entry = next(n for n in row["nodes"] if n["node_id"] == key)
        if entry["state"] != "VERIFIED" or entry["invalidated"] or row["invalidated"]:
            raise ValueError("Prerequisite has no committed, available campaign proof")
        action = plan["actions"][key]
        if action["kind"] == "existing":
            node = next(n for n in plan["flow_plan"]["nodes"] if n["id"] == key)
            source = self.existing(action["source"], node)
            if source != action["source_snapshot"]:
                raise ValueError("Selected existing proof changed")
            store = self.library.source_store(action["source"]["origin"])
        else:
            store = CampaignChecks(self, row["campaign_id"])
            source = e4(store.report(entry["child_id"]))
            resolution = _envelope(self.directory(row["campaign_id"]) / (key + "-resolution.json"))
            if (
                digest(resolution) != entry["resolution_fingerprint"]
                or resolution != source["plan"]
            ):
                raise ValueError("Resolved child differs from the committed exact proof program")
        if digest(source) != entry["source_fingerprint"]:
            raise ValueError("Checked campaign source changed")
        return source, store

    def node_source(self, campaign_id, binding):
        exact_object(binding, {"alias", "item_id"}, "Campaign node source")
        key = binding["item_id"][5:]
        _identifier(key)
        marker = (campaign_id, key)
        path = _path.get()
        if marker in path or len(path) >= 8:
            raise ValueError("Cyclic or over-depth campaign proof source")
        token = _path.set((*path, marker))
        try:
            row = self.read(campaign_id)
            plan = self.plan(row)
            if row["state"] not in (*VISIBLE, "RUNNING") or row["cancel_requested"]:
                raise ValueError("Campaign proof is unavailable")
            source, store = self.checked(row, plan, key)
            proof = proof_data(
                store.directory(source["run_id"]) / "replay/proof.ndjson",
                source["files"].get("replay/proof.ndjson"),
            )
            origin = provenance(
                [
                    item
                    if "ancestors" in item
                    else self.library.load_source({k: item[k] for k in ("alias", "item_id")})[0]
                    for item in source["plan"]["source_items"]
                ]
            )
            descriptor = {
                **binding,
                "origin": "campaign-node",
                "version": 1,
                "archive_fingerprint": digest(source),
                "source_fingerprint": digest(source),
                "request": signature(source["plan"]["request"]),
                "formal_statement": source["plan"]["formal_statement"],
                "alignment": source["alignment"],
                "data_sha256": file_hash(store.directory(source["run_id"]) / "replay/proof.ndjson"),
                "data_bytes": len(proof),
                **origin,
            }
            descriptor["ancestors"] = [
                *origin["ancestors"],
                {
                    "item_id": campaign_id + "." + key,
                    "version": 1,
                    "archive_fingerprint": digest(source),
                },
            ]
            return descriptor, proof
        finally:
            _path.reset(token)

    def _audit(self, row):
        plan = self.plan(row)
        reason = self.context_reason(row, plan)
        if reason and not row["invalidated"]:
            row.update(invalidated=True, reason=reason)
            self.event(row, "CAMPAIGN_INVALIDATED", reason=reason)
        nodes = {n["id"]: n for n in plan["flow_plan"]["nodes"]}
        states = {}
        for entry in row["nodes"]:
            key = entry["node_id"]
            node = nodes[key]
            blocked = [d for d in node["depends_on"] if states[d]["math_verdict"] != "PROVED"]
            deps = {d: states[d]["binding_fingerprint"] for d in node["depends_on"]}
            if (
                entry["state"] in ("RUNNING", "VERIFIED")
                and not entry["invalidated"]
                and (row["invalidated"] or blocked or entry["prerequisites"] != deps)
            ):
                entry.update(invalidated=True, reason="Selected prerequisites or context changed")
            proved = False
            if entry["state"] == "VERIFIED" and not entry["invalidated"]:
                try:
                    self.checked(row, plan, key)
                    proved = True
                except WorkerBusy:
                    raise
                except sqlite3.Error as error:
                    if storage_busy(error):
                        raise
                    entry.update(invalidated=True, reason=str(error))
                except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                    entry.update(invalidated=True, reason=str(error))
            states[key] = {
                "math_verdict": "PROVED" if proved else "UNKNOWN",
                "evidence_level": "E4" if proved else "E0",
                "blocked_by": blocked,
                "binding_fingerprint": digest(
                    {
                        "campaign_id": row["campaign_id"],
                        "node_id": key,
                        "source": entry["source_fingerprint"],
                    }
                )
                if proved
                else None,
            }
        return plan, states

    def report(self, key):
        with lease(self.directory(key)):
            row = self.read(key)
            before = deepcopy(row)
            try:
                plan, states = self._audit(row)
            except (WorkerBusy, sqlite3.Error):
                raise
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                atomic(self.directory(key) / "invalidated.json", {"reason": str(error)})
                raise
            if row != before:
                self.save(row)
        published = (
            row["state"] in VISIBLE and not row["invalidated"] and not row["cancel_requested"]
        )
        if not published:
            states = {
                k: {**s, "math_verdict": "UNKNOWN", "evidence_level": "E0"}
                for k, s in states.items()
            }
        problem = states[plan["flow_plan"]["problem_id"]]
        return {
            **row,
            "plan": plan,
            "node_states": states,
            "math_verdict": problem["math_verdict"],
            "evidence_level": problem["evidence_level"],
            "fresh_attestation": False,
            "model_calls": 0,
            "historical_record": True,
        }

    def cancel(self, key):
        with lease(self.directory(key)):
            row = self.read(key)
            if row["state"] in ACTIVE:
                row["cancel_requested"] = True
                if row["state"] == "QUEUED":
                    row["state"] = "CANCELLED"
                self.event(row, "CAMPAIGN_CANCEL_REQUESTED")
                self.save(row)
        return self.report(key)

    def withdraw(self, key, raw):
        exact_object(
            raw,
            {"request_id", "node_id", "reviewer", "reason", "confirmed"},
            "Withdraw campaign node",
        )
        _identifier(raw["request_id"])
        text(raw["reviewer"], 200, "Reviewer")
        text(raw["reason"], 500, "Reason")
        if raw["confirmed"] is not True:
            raise ValueError("Explicit withdrawal confirmation required")
        with lease(self.directory(key)):
            row = self.read(key)
            if raw["request_id"] in row["requests"]:
                if row["requests"][raw["request_id"]] != digest(raw):
                    raise ValueError("Withdrawal request ID changed")
            else:
                if len(row["requests"]) >= LIMITS["requests"]:
                    raise ValueError("Campaign request budget exhausted")
                entry = next((n for n in row["nodes"] if n["node_id"] == raw["node_id"]), None)
                if entry is None or entry["state"] != "VERIFIED":
                    raise ValueError("Choose a verified campaign node")
                entry.update(invalidated=True, reason=raw["reason"])
                row["requests"][raw["request_id"]] = digest(raw)
                self.event(row, "NODE_WITHDRAWN", node_id=raw["node_id"], reviewer=raw["reviewer"])
                self.save(row)
        return self.report(key)

    def recover_locked(self):
        interrupted = []
        for path in self.paths():
            with lease(path):
                row = self.read(path.name)
                if row["state"] != "RUNNING":
                    continue
                row["state"] = "INTERRUPTED"
                for n in row["nodes"]:
                    if n["state"] == "RUNNING":
                        n.update(
                            state="INTERRUPTED",
                            invalidated=True,
                            reason="Supervisor exited before graph commit",
                        )
                self.event(row, "INTERRUPTED_WITHOUT_PROMOTION")
                self.save(row)
                interrupted.append(path.name)
        return interrupted

    def recover(self):
        with self.tasks.worker_lock():
            return {"interrupted": self.recover_locked()}

    def execute(self, key, lock_fd):
        try:
            while True:
                with lease(self.directory(key)):
                    row = self.read(key)
                    plan, states = self._audit(row)
                    if row["cancel_requested"]:
                        row["state"] = "CANCELLED"
                    elif row["invalidated"]:
                        row["state"] = "POLICY_BLOCKED"
                    elif self.expired(row):
                        row.update(state="BUDGET_EXHAUSTED", reason="Wall budget expired")
                    if row["state"] != "RUNNING":
                        self.save(row)
                        break
                    entry = next((n for n in row["nodes"] if n["state"] == "PENDING"), None)
                    if entry is None:
                        row["state"] = "COMPLETED"
                        self.event(row, "CAMPAIGN_COMPLETED")
                        self.save(row)
                        break
                    name = entry["node_id"]
                    action = plan["actions"][name]
                    node = next(n for n in plan["flow_plan"]["nodes"] if n["id"] == name)
                    if states[name]["blocked_by"]:
                        row.update(
                            state="FAILED", reason="A complete prerequisite proof is unavailable"
                        )
                        self.save(row)
                        break
                    entry["prerequisites"] = {
                        d: states[d]["binding_fingerprint"] for d in node["depends_on"]
                    }
                    if action["kind"] == "existing":
                        source = self.existing(action["source"], node)
                        if source != action["source_snapshot"]:
                            raise ValueError("Selected existing proof changed")
                        entry.update(state="VERIFIED", source_fingerprint=digest(source))
                        self.event(row, "EXISTING_E4_ACCEPTED", node_id=name)
                        self.save(row)
                        continue
                    seconds = action["request"]["timeout_seconds"]
                    budget = plan["input"]["budget"]
                    if (
                        row["used"]["checks"] >= budget["max_checks"]
                        or row["used"]["worker_seconds_reserved"] + seconds
                        > budget["worker_seconds"]
                    ):
                        row.update(
                            state="BUDGET_EXHAUSTED",
                            reason="No remaining check or full worker reservation",
                        )
                        self.save(row)
                        break
                    row["used"]["checks"] += 1
                    row["used"]["worker_seconds_reserved"] += seconds
                    entry["state"] = "RUNNING"
                    self.event(row, "CHECK_RESERVED", node_id=name, worker_seconds=seconds)
                    self.save(row)
                checker = CampaignChecks(
                    self, key, create=not (self.directory(key) / "checks").exists()
                )
                resolved = checker.preview(action["request"])
                # The runtime may fill only checked proof-data identities, never proof programs or types.
                actual = compile_composition(
                    {**resolved["request"], "schema": COMPILE_MODE}, resolved["source_items"]
                )
                actual["request"]["schema"] = COMPOSE_MODE
                if actual != action["compiled"]:
                    raise ValueError("Resolved check changed the pre-reviewed proof program")
                for planned, current in zip(action["inputs"], resolved["source_items"]):
                    if planned["kind"] == "archive" and planned["source"] != current:
                        raise ValueError("Pre-reviewed root archive changed")
                    if planned["kind"] == "node" and planned["signature"] != {
                        k: current[k] for k in ("alias", "item_id", "request", "formal_statement")
                    }:
                        raise ValueError("Prerequisite source type changed")
                atomic(self.directory(key) / (name + "-resolution.json"), resolved)
                child = checker.submit(
                    {
                        "request_id": key + "." + name,
                        "request": action["request"],
                        "plan_fingerprint": resolved["plan_fingerprint"],
                        "reviewer": row["reviewer"],
                        "confirmed": True,
                    }
                )
                with lease(self.directory(key)):
                    row = self.read(key)
                    entry = next(n for n in row["nodes"] if n["node_id"] == name)
                    entry.update(child_id=child["run_id"], resolution_fingerprint=digest(resolved))
                    self.save(row)
                with checker.transaction() as db:
                    child = checker.read(child["run_id"], db)
                    child.update(state="RUNNING", stage="CHECKING")
                    checker.save(db, child)
                checked = checker.execute(child["run_id"], lock_fd)
                with lease(self.directory(key)):
                    row = self.read(key)
                    entry = next(n for n in row["nodes"] if n["node_id"] == name)
                    row["used"]["worker_seconds_actual"] += (checked["result"] or {}).get(
                        "worker_seconds", 0
                    )
                    if row["cancel_requested"]:
                        row["state"] = "CANCELLED"
                    elif self.expired(row):
                        row.update(state="BUDGET_EXHAUSTED", reason="Wall budget expired")
                    if row["state"] != "RUNNING" or entry["invalidated"]:
                        entry.update(
                            state="STOPPED",
                            invalidated=True,
                            reason="Campaign stopped before node commit",
                        )
                    else:
                        try:
                            source = e4(checked)
                        except ValueError:
                            entry.update(state="FAILED", reason=checked["state"])
                            row["state"] = "FAILED"
                        else:
                            entry.update(state="VERIFIED", source_fingerprint=digest(source))
                    self.event(row, "NODE_CHECK_FINISHED", node_id=name, state=entry["state"])
                    self.save(row)
        except WorkerBusy:
            raise
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            self.fail(key, error)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            self.fail(key, error)
        return self.report(key)

    def fail(self, key, error):
        with lease(self.directory(key)):
            row = self.read(key)
            row.update(state="FAILED", reason=str(error))
            for n in row["nodes"]:
                if n["state"] == "RUNNING":
                    n.update(state="FAILED", reason=str(error))
            self.event(row, "CAMPAIGN_FAILED", reason=str(error))
            self.save(row)

    def work_once(self, key=None):
        with self.tasks.worker_lock() as lock_fd:
            self.recover_locked()
            for path in [self.directory(key)] if key else self.paths():
                with lease(path):
                    row = self.read(path.name)
                    if row["state"] != "QUEUED":
                        continue
                    plan = self.plan(row)
                    row.update(
                        state="RUNNING",
                        started_at=now(),
                        deadline=time.time() + plan["input"]["budget"]["wall_seconds"],
                    )
                    self.event(row, "CAMPAIGN_STARTED")
                    self.save(row)
                self.execute(path.name, lock_fd)
                return {"worker_state": "EXECUTED", "campaign_id": path.name}
        return {"worker_state": "IDLE"}

    def list_runs(self, flow_id=None):
        rows = []
        for path in self.paths():
            row = self.read(path.name)
            plan = self.plan(row)
            if flow_id is None or flow_id == plan["input"]["flow_id"]:
                rows.append(
                    {k: row[k] for k in ("campaign_id", "created_at", "state", "invalidated")}
                )
        return rows

    def catalog(self, flow_id):
        flow = self.flows.report(flow_id)
        return {
            "schema": SCHEMA,
            "flow": flow,
            "campaigns": self.list_runs(flow_id),
            "library_sources": self.library.compositions.catalog()["sources"],
            "existing_proofs": [
                {"origin": origin, **row}
                for origin, store in (
                    ("composition", self.library.legacy),
                    ("library-composition", self.library.compositions),
                )
                for row in store.list_runs()
            ],
            "limits": LIMITS,
        }
