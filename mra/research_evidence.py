"""Named links from exact, independently replayed proof prefixes to research nodes."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
import sqlite3
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_lab import now
from .proof_steps import ProofStepStore
from .research_flow import ResearchFlowStore, atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier

SCHEMA = "research-prefix-evidence-links-v1"
LIMITS = {"flows": 50, "bindings": 128, "bindings_per_node": 16, "requests": 512}


def manifest(root):
    return {"schema": SCHEMA, "source": file_hash(root / "mra/research_evidence.py")}


def text(value, maximum, label):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= maximum:
        raise ValueError(label + " is required")
    return value.strip()


class ResearchEvidenceStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-evidence"
        if self.base.resolve() != self.base:
            raise ValueError("Research evidence storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.flows = ResearchFlowStore(self.root)
        self.steps = ProofStepStore(self.root)

    def directory(self, flow_id):
        path = self.base / identifier(flow_id, "flow")
        if path.resolve() != path:
            raise ValueError("Evidence link directory cannot be redirected")
        return path

    @staticmethod
    def empty(flow_id):
        return {
            "schema": SCHEMA,
            "flow_id": flow_id,
            "revision": 0,
            "invalidated": False,
            "reason": None,
            "bindings": [],
            "requests": {},
            "events": [],
        }

    def read(self, path):
        try:
            row = _envelope(path / "record.json")
            exact_object(row, set(self.empty(path.name)), "Evidence ledger")
            if row["schema"] != SCHEMA or row["flow_id"] != path.name:
                raise ValueError("Evidence link record identity changed")
            if (
                type(row["revision"]) is not int
                or row["revision"] < 0
                or type(row["invalidated"]) is not bool
                or not isinstance(row["bindings"], list)
                or not isinstance(row["requests"], dict)
                or not isinstance(row["events"], list)
                or len(row["bindings"]) > LIMITS["bindings"]
                or len(row["requests"]) > LIMITS["requests"]
            ):
                raise ValueError("Malformed evidence ledger")
            seen = set()
            for binding in row["bindings"]:
                exact_object(
                    binding,
                    {"binding_id", "plan", "reviewer", "created_at", "state", "reason"},
                    "Evidence binding",
                )
                key = identifier(binding["binding_id"], "binding")
                if (
                    key in seen
                    or binding["state"] not in ("ACTIVE", "WITHDRAWN", "INVALIDATED")
                    or not isinstance(binding["plan"], dict)
                    or binding["plan"].get("flow_id") != path.name
                    or not isinstance(binding["plan"].get("node_id"), str)
                ):
                    raise ValueError("Malformed or duplicate evidence binding")
                text(binding["reviewer"], 200, "Named reviewer")
                seen.add(key)
            for key, request in row["requests"].items():
                _identifier(key)
                exact_object(request, {"action", "fingerprint", "binding_id"}, "Evidence request")
                if (
                    request["action"] not in ("bind", "withdraw")
                    or request["binding_id"] not in seen
                ):
                    raise ValueError("Malformed evidence request")
            return row
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            atomic(path / "invalidated.json", {"reason": str(error)})
            raise

    @contextmanager
    def access(self, flow_id, create=False):
        path = self.directory(flow_id)
        with ExitStack() as stack:
            # The base lease prevents readers from seeing partially published records.
            with lease(self.base):
                if not path.exists() and create:
                    if len(list(self.base.glob("flow-*"))) >= LIMITS["flows"]:
                        raise ValueError("Research evidence flow limit exceeded")
                    path.mkdir()
                    atomic(path / "record.json", self.empty(flow_id))
                exists = path.exists()
                if exists:
                    stack.enter_context(lease(path))
            yield path, self.read(path) if exists else self.empty(flow_id)

    @staticmethod
    def node(flow, node_id):
        value = next((n for n in flow["plan"]["nodes"] if n["id"] == node_id), None)
        if value is None:
            raise ValueError("Unknown research node")
        return value

    def source(self, flow, node_id, scope_id, attempt_id):
        node = self.node(flow, node_id)
        if flow["invalidated"] or flow["node_states"][node_id]["blocked_by"]:
            raise ValueError("Research target or its original prerequisites are unavailable")
        scope = self.steps.report(scope_id)
        context = {
            "flow_id": flow["flow_id"],
            "node_id": node_id,
            "target": node["target"],
            "flow_plan_fingerprint": flow["plan_fingerprint"],
            "prerequisites": {
                d: flow["node_states"][d]["binding_fingerprint"] for d in node["depends_on"]
            },
        }
        if scope["invalidated"] or scope["plan"]["context"] != context:
            raise ValueError("Proof prefix belongs to a different exact research context")
        attempt = next((a for a in scope["attempts"] if a["attempt_id"] == attempt_id), None)
        if (
            not attempt
            or attempt_id not in scope["accepted"]
            or attempt["state"] != "COMPLETED"
            or attempt["invalidated"]
            or attempt["cancel_requested"]
            or attempt["error"]
            or attempt["observation"]["outcome"] != "CLOSED"
        ):
            raise ValueError("Choose a completed, accepted CLOSED prefix with E4 evidence")
        child = scope["checks"][attempt_id]
        if (
            child["state"],
            child["math_verdict"],
            child["evidence_level"],
            child["invalidated"],
        ) != ("COMPLETED", "PROVED", "E4", False) or child["alignment"]["confirmed"] is not True:
            raise ValueError("Exact candidate audit, semantic alignment and replay are required")
        request = {k: v for k, v in child["plan"]["request"].items() if k != "steps"}
        request["schema"] = node["target"]["schema"]
        if (
            request != node["target"]
            or child["plan"]["formal_statement"] != flow["node_states"][node_id]["formal_statement"]
            or child["result"]["replay"]["audit"]["mode"] != "data-only-kernel-replay-v1"
            or child["result"]["candidate"]["audit"]["type_matches"] is not True
        ):
            raise ValueError("The complete original theorem and independent replay must match")
        return {
            "scope_id": scope_id,
            "attempt_id": attempt_id,
            "run_id": child["run_id"],
            "context": context,
            "source_fingerprint": attempt["source_fingerprint"],
            "child_plan": child["plan"],
            "alignment": child["alignment"],
            "files": child["files"],
            "candidate_audit": child["result"]["candidate"]["audit"],
            "replay_audit": child["result"]["replay"]["audit"],
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "fresh_attestation": False,
        }

    def report(self, flow_id):
        flow = self.flows.report(flow_id)
        with self.access(flow_id) as (path, row):
            before = deepcopy(row)
            if (path / "invalidated.json").exists():
                row.update(invalidated=True, reason="Previously detected damaged evidence ledger")
            sources = {}
            for binding in row["bindings"]:
                if binding["state"] != "ACTIVE":
                    continue
                try:
                    plan = binding["plan"]
                    if (
                        row["invalidated"]
                        or plan["source_manifest"] != manifest(self.root)
                        or plan["plan_fingerprint"]
                        != digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
                    ):
                        raise ValueError("Evidence ledger, plan or bridge implementation changed")
                    actual = self.source(
                        flow, plan["node_id"], plan["scope_id"], plan["attempt_id"]
                    )
                    if actual != plan["source"]:
                        raise ValueError(
                            "The linked proof, target, prerequisites or artifacts changed"
                        )
                    sources[binding["binding_id"]] = actual
                except WorkerBusy:
                    raise
                except sqlite3.Error as error:
                    if storage_busy(error):
                        raise
                    binding.update(state="INVALIDATED", reason=str(error))
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    binding.update(state="INVALIDATED", reason=str(error))
            if row != before:
                row["revision"] += 1
                row["events"].append({"kind": "AUDIT_INVALIDATED_LINKS", "at": now()})
                atomic(path / "record.json", row)
        nodes = {}
        for node in flow["plan"]["nodes"]:
            links = [
                b["binding_id"]
                for b in row["bindings"]
                if b["binding_id"] in sources and b["plan"]["node_id"] == node["id"]
            ]
            nodes[node["id"]] = {
                "math_verdict": "PROVED" if links else "UNKNOWN",
                "evidence_level": "E4" if links else "E0",
                "active_bindings": links,
                "route_math_verdict": flow["node_states"][node["id"]]["math_verdict"],
            }
        problem = nodes[flow["plan"]["problem_id"]]
        return {
            **row,
            "research_task": flow,
            "node_evidence": nodes,
            "source_evidence": sources,
            "math_verdict": problem["math_verdict"],
            "evidence_level": problem["evidence_level"],
            "fresh_attestation": False,
            "model_calls": 0,
            "lean_calls": 0,
            "limits": LIMITS,
            "limitations": [
                "Named association of existing exact E4 evidence; no new Lean execution or replay.",
                "The original route ledger and its execution/verdict history are preserved.",
                "These links do not satisfy native route prerequisite gates or inject Lean assumptions.",
                "No automatic knowledge admission, theorem generalization or model planning.",
            ],
        }

    def preview(self, flow_id, raw):
        exact_object(raw, {"node_id", "scope_id", "attempt_id"}, "Exact prefix evidence link")
        current = self.report(flow_id)
        if current["invalidated"]:
            raise ValueError("Evidence ledger is invalidated")
        source = self.source(current["research_task"], **raw)
        value = {
            "schema": SCHEMA,
            "flow_id": flow_id,
            **raw,
            "ledger_revision": current["revision"],
            "source_manifest": manifest(self.root),
            "source": source,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "lean_calls": 0,
            "model_calls": 0,
            "fresh_attestation": False,
        }
        value["plan_fingerprint"] = digest(value)
        return value

    @staticmethod
    def identity(row, raw, action):
        previous = row["requests"].get(raw["request_id"])
        if previous:
            if previous["action"] != action or previous["fingerprint"] != digest(raw):
                raise ValueError("Request ID belongs to different evidence input")
            return True
        if len(row["requests"]) >= LIMITS["requests"]:
            raise ValueError("Evidence request budget exhausted")
        return False

    def bind(self, flow_id, raw):
        exact_object(
            raw,
            {
                "node_id",
                "scope_id",
                "attempt_id",
                "plan_fingerprint",
                "request_id",
                "reviewer",
                "confirmed",
            },
            "Named evidence association",
        )
        _identifier(raw["request_id"])
        reviewer = text(raw["reviewer"], 200, "Named reviewer")
        if raw["confirmed"] is not True:
            raise ValueError("Confirm the full original theorem and exact E4 source")
        current = self.report(flow_id)
        if self.identity(current, raw, "bind"):
            return current  # An old request never revives a withdrawn or invalidated link.
        plan = self.preview(flow_id, {k: raw[k] for k in ("node_id", "scope_id", "attempt_id")})
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Exact proof or evidence ledger changed; preview again")
        with self.access(flow_id, create=True) as (path, row):
            if not self.identity(row, raw, "bind"):
                if row["invalidated"] or row["revision"] != plan["ledger_revision"]:
                    raise ValueError("Evidence ledger changed; preview again")
                if (
                    len(row["bindings"]) >= LIMITS["bindings"]
                    or sum(b["plan"]["node_id"] == raw["node_id"] for b in row["bindings"])
                    >= LIMITS["bindings_per_node"]
                ):
                    raise ValueError("Evidence binding budget exhausted")
                if any(
                    b["state"] == "ACTIVE"
                    and b["plan"]["node_id"] == raw["node_id"]
                    and b["plan"]["source"] == plan["source"]
                    for b in row["bindings"]
                ):
                    raise ValueError("This exact source is already linked")
                key = "binding-" + uuid.uuid4().hex
                row["bindings"].append(
                    {
                        "binding_id": key,
                        "plan": plan,
                        "reviewer": reviewer,
                        "created_at": now(),
                        "state": "ACTIVE",
                        "reason": None,
                    }
                )
                row["revision"] += 1
                row["requests"][raw["request_id"]] = {
                    "action": "bind",
                    "fingerprint": digest(raw),
                    "binding_id": key,
                }
                row["events"].append(
                    {"kind": "NAMED_EXISTING_E4_LINKED", "binding_id": key, "at": now()}
                )
                atomic(path / "record.json", row)
        return self.report(flow_id)

    def withdraw(self, flow_id, raw):
        exact_object(
            raw,
            {"binding_id", "request_id", "reviewer", "reason", "confirmed"},
            "Named link withdrawal",
        )
        _identifier(raw["request_id"])
        reviewer = text(raw["reviewer"], 200, "Named reviewer")
        reason = text(raw["reason"], 500, "Withdrawal reason")
        if raw["confirmed"] is not True:
            raise ValueError("Confirm withdrawal of this research evidence link")
        with self.access(flow_id) as (path, row):
            if not self.identity(row, raw, "withdraw"):
                binding = next(
                    (b for b in row["bindings"] if b["binding_id"] == raw["binding_id"]), None
                )
                if binding is None:
                    raise ValueError("Unknown evidence binding")
                binding.update(state="WITHDRAWN", reason=reason)
                row["revision"] += 1
                row["requests"][raw["request_id"]] = {
                    "action": "withdraw",
                    "fingerprint": digest(raw),
                    "binding_id": binding["binding_id"],
                }
                row["events"].append(
                    {
                        "kind": "NAMED_LINK_WITHDRAWN",
                        "reviewer": reviewer,
                        "reason": reason,
                        "binding_id": binding["binding_id"],
                        "at": now(),
                    }
                )
                atomic(path / "record.json", row)
        return self.report(flow_id)
