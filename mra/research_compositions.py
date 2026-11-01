"""Exact, named composition evidence in a separate dependency-aware research view."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
import sqlite3
import uuid

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition import LemmaCompositionStore
from .mathlib_lab import now
from .number_theory_terms import MODE as TARGET_MODE
from .research_continuation import ResearchContinuationStore
from .research_evidence import ResearchEvidenceStore, text
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier

SCHEMA = "research-composition-evidence-v1"
LIMITS = {"flows": 50, "bindings": 64, "per_node": 8, "requests": 256}


def manifest(root):
    return {
        "schema": SCHEMA,
        "files": {
            name: file_hash(root / name)
            for name in (
                "mra/research_compositions.py",
                "mra/research_continuation.py",
                "mra/research_evidence.py",
                "mra/research_flow.py",
            )
        },
    }


def complete_target(request):
    return {
        **{k: deepcopy(v) for k, v in request.items() if k not in ("proof", "sources")},
        "schema": TARGET_MODE,
    }


class ResearchCompositionStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-compositions"
        if self.base.resolve() != self.base:
            raise ValueError("Composition research evidence cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.compositions = LemmaCompositionStore(self.root)
        self.continuations = ResearchContinuationStore(self.root)

    def directory(self, flow_id):
        path = self.base / identifier(flow_id, "flow")
        if path.resolve() != path:
            raise ValueError("Composition research evidence path changed")
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
            exact_object(row, set(self.empty(path.name)), "Composition evidence ledger")
            if (
                row["schema"] != SCHEMA
                or row["flow_id"] != path.name
                or type(row["revision"]) is not int
                or row["revision"] < 0
                or type(row["invalidated"]) is not bool
                or not isinstance(row["bindings"], list)
                or not isinstance(row["requests"], dict)
                or not isinstance(row["events"], list)
                or len(row["bindings"]) > LIMITS["bindings"]
                or len(row["requests"]) > LIMITS["requests"]
            ):
                raise ValueError("Invalid composition evidence ledger")
            seen = set()
            for binding in row["bindings"]:
                exact_object(
                    binding,
                    {"binding_id", "plan", "reviewer", "created_at", "state", "reason"},
                    "Composition binding",
                )
                key = identifier(binding["binding_id"], "binding")
                if (
                    key in seen
                    or binding["state"] not in ("ACTIVE", "WITHDRAWN", "INVALIDATED")
                    or not isinstance(binding["plan"], dict)
                    or binding["plan"].get("flow_id") != path.name
                ):
                    raise ValueError("Malformed composition association")
                text(binding["reviewer"], 200, "Named reviewer")
                seen.add(key)
            for key, value in row["requests"].items():
                _identifier(key)
                exact_object(value, {"action", "fingerprint", "binding_id"}, "Composition request")
                if value["action"] not in ("bind", "withdraw") or value["binding_id"] not in seen:
                    raise ValueError("Invalid composition request history")
            return row
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            atomic(path / "invalidated.json", {"reason": str(error)})
            raise

    @contextmanager
    def access(self, flow_id, create=False):
        path = self.directory(flow_id)
        with ExitStack() as stack:
            with lease(self.base):
                if not path.exists() and create:
                    if len(list(self.base.glob("flow-*"))) >= LIMITS["flows"]:
                        raise ValueError("Composition evidence flow budget exhausted")
                    path.mkdir()
                    atomic(path / "record.json", self.empty(flow_id))
                exists = path.exists()
                if exists:
                    stack.enter_context(lease(path))
            yield path, self.read(path) if exists else self.empty(flow_id)

    def baseline(self, flow_id):
        continued = self.continuations.report(flow_id)
        if continued["created"]:
            flow = self.continuations.links.flows.report(flow_id)
            inherited = {
                key: {
                    "math_verdict": state["math_verdict"],
                    "source": state["source"],
                    "binding_fingerprint": state["binding_fingerprint"],
                }
                for key, state in continued["node_states"].items()
            }
        else:
            evidence = self.continuations.links.report(flow_id)
            flow = evidence["research_task"]
            inherited = {}
            for node in flow["plan"]["nodes"]:
                key = node["id"]
                native = flow["node_states"][key]
                choices = []
                if native["math_verdict"] in ("PROVED", "REFUTED"):
                    choices.append(
                        (
                            native["math_verdict"],
                            {
                                "origin": "ORIGINAL_ROUTE",
                                "fingerprint": native["binding_fingerprint"],
                            },
                        )
                    )
                for link in evidence["node_evidence"][key]["active_bindings"]:
                    choices.append(
                        (
                            "PROVED",
                            {
                                "origin": "PREFIX_LINK",
                                "binding_id": link,
                                "fingerprint": digest(evidence["source_evidence"][link]),
                            },
                        )
                    )
                chosen = choices[0] if len({v for v, _ in choices}) == 1 else None
                inherited[key] = {
                    "math_verdict": chosen[0] if chosen else "UNKNOWN",
                    "source": chosen[1] if chosen else None,
                    "binding_fingerprint": digest(chosen[1]) if chosen else None,
                }
        return flow, inherited

    @staticmethod
    def context(flow, node, states):
        return {
            "flow_id": flow["flow_id"],
            "node_id": node["id"],
            "flow_plan_fingerprint": flow["plan_fingerprint"],
            "parent": flow["plan"]["parent"],
            "target": node["target"],
            "prerequisites": {d: states[d]["binding_fingerprint"] for d in node["depends_on"]},
        }

    def source(self, node, run_id):
        row = self.compositions.report(run_id)
        if (
            row["state"],
            row["math_verdict"],
            row["evidence_level"],
            row["invalidated"],
            row["cancel_requested"],
            row["artifact_status"],
        ) != ("COMPLETED", "PROVED", "E4", False, False, "MATCHES_RECORDED_HASHES"):
            raise ValueError("Choose an intact, active, independently replayed composition E4")
        result = row["result"]
        if (
            row["alignment"]["confirmed"] is not True
            or complete_target(row["plan"]["request"]) != node["target"]
            or result["candidate"]["audit"]["type_matches"] is not True
            or result["replay"]["audit"]["mode"] != "data-only-kernel-replay-v1"
            or result["candidate"]["pid"] == result["replay"]["pid"]
        ):
            raise ValueError("Composition must preserve this exact full original target and replay")
        return {
            "run_id": run_id,
            "plan": row["plan"],
            "alignment": row["alignment"],
            "files": row["files"],
            "result": result,
            "submission_fingerprint": row["submission_fingerprint"],
            "fresh_attestation": False,
        }

    def report(self, flow_id):
        flow, inherited = self.baseline(flow_id)
        with self.access(flow_id) as (path, row):
            before = deepcopy(row)
            if (path / "invalidated.json").exists():
                row.update(
                    invalidated=True, reason="Previously detected damaged composition ledger"
                )
            states, sources = {}, {}
            by_id = {n["id"]: n for n in flow["plan"]["nodes"]}
            for key in flow["plan"]["order"]:
                node = by_id[key]
                blocked = [d for d in node["depends_on"] if states[d]["math_verdict"] != "PROVED"]
                context = self.context(flow, node, states)
                candidates = []
                if (
                    not row["invalidated"]
                    and not flow["invalidated"]
                    and not blocked
                    and inherited[key]["source"]
                ):
                    candidates.append(
                        (
                            inherited[key]["math_verdict"],
                            {
                                "origin": "EXISTING_RESEARCH",
                                "source": inherited[key]["source"],
                                "fingerprint": inherited[key]["binding_fingerprint"],
                            },
                        )
                    )
                active_links = []
                for binding in (
                    b
                    for b in row["bindings"]
                    if b["plan"].get("node_id") == key and b["state"] == "ACTIVE"
                ):
                    try:
                        plan = binding["plan"]
                        if (
                            row["invalidated"]
                            or flow["invalidated"]
                            or blocked
                            or plan["context"] != context
                            or plan["source_manifest"] != manifest(self.root)
                            or plan["plan_fingerprint"]
                            != digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
                        ):
                            raise ValueError(
                                "Original context, selected prerequisites or bridge changed"
                            )
                        source = self.source(node, plan["run_id"])
                        if source != plan["source"]:
                            raise ValueError(
                                "Composition source, semantic alignment or proof artifacts changed"
                            )
                        if (
                            source["plan"]["formal_statement"]
                            != flow["node_states"][key]["formal_statement"]
                        ):
                            raise ValueError("Complete theorem type differs from the research node")
                        sources[binding["binding_id"]] = source
                        active_links.append(binding["binding_id"])
                        candidates.append(
                            (
                                "PROVED",
                                {
                                    "origin": "COMPOSITION_LINK",
                                    "binding_id": binding["binding_id"],
                                    "fingerprint": digest(source),
                                },
                            )
                        )
                    except WorkerBusy:
                        raise
                    except sqlite3.Error as error:
                        if storage_busy(error):
                            raise
                        binding.update(state="INVALIDATED", reason=str(error))
                    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                        binding.update(state="INVALIDATED", reason=str(error))
                chosen = candidates[0] if len({v for v, _ in candidates}) == 1 else None
                verdict, source = chosen if chosen else ("UNKNOWN", None)
                states[key] = {
                    "math_verdict": verdict,
                    "evidence_level": "E4" if chosen else "E0",
                    "source": source,
                    "binding_fingerprint": digest(source) if source else None,
                    "blocked_by": blocked,
                    "active_bindings": active_links,
                    "formal_statement": flow["node_states"][key]["formal_statement"],
                    "original_route_verdict": flow["node_states"][key]["math_verdict"],
                    "existing_research_verdict": inherited[key]["math_verdict"],
                }
            # Unknown node IDs in otherwise valid envelopes must never disappear from audit.
            for binding in row["bindings"]:
                if binding["state"] == "ACTIVE" and binding["plan"].get("node_id") not in by_id:
                    binding.update(state="INVALIDATED", reason="Research node no longer exists")
            if row != before:
                row["revision"] += 1
                row["events"].append({"kind": "COMPOSITION_LINKS_INVALIDATED", "at": now()})
                atomic(path / "record.json", row)
        problem = states[flow["plan"]["problem_id"]]
        return {
            **row,
            "research_task": flow,
            "node_evidence": states,
            "source_evidence": sources,
            "math_verdict": problem["math_verdict"],
            "evidence_level": problem["evidence_level"],
            "fresh_attestation": False,
            "model_calls": 0,
            "lean_calls": 0,
            "limits": LIMITS,
            "limitations": [
                "Explicit association of existing full E4 evidence, no new Lean execution.",
                "Original routes, prefix links and continuations retain their own verdicts.",
                "Prerequisites are selected evidence dependencies, never additional Lean assumptions.",
            ],
        }

    def preview(self, flow_id, raw):
        exact_object(raw, {"node_id", "run_id"}, "Exact composition association")
        current = self.report(flow_id)
        flow = current["research_task"]
        node = ResearchEvidenceStore.node(flow, raw["node_id"])
        if (
            current["invalidated"]
            or flow["invalidated"]
            or current["node_evidence"][node["id"]]["blocked_by"]
        ):
            raise ValueError("Original target or selected research prerequisites are unavailable")
        source = self.source(node, raw["run_id"])
        if (
            source["plan"]["formal_statement"]
            != current["node_evidence"][node["id"]]["formal_statement"]
        ):
            raise ValueError("Exact full formal statement required")
        plan = {
            "schema": SCHEMA,
            "flow_id": flow_id,
            **raw,
            "ledger_revision": current["revision"],
            "context": self.context(flow, node, current["node_evidence"]),
            "source": source,
            "source_manifest": manifest(self.root),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "lean_calls": 0,
            "fresh_attestation": False,
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def bind(self, flow_id, raw):
        exact_object(
            raw,
            {"node_id", "run_id", "plan_fingerprint", "request_id", "reviewer", "confirmed"},
            "Named composition association",
        )
        _identifier(raw["request_id"])
        reviewer = text(raw["reviewer"], 200, "Named reviewer")
        if raw["confirmed"] is not True:
            raise ValueError("Confirm the complete target, sources and selected prerequisites")
        current = self.report(flow_id)
        if ResearchEvidenceStore.identity(current, raw, "bind"):
            return current
        if len(current["requests"]) >= LIMITS["requests"]:
            raise ValueError("Composition association request budget exhausted")
        plan = self.preview(flow_id, {k: raw[k] for k in ("node_id", "run_id")})
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Composition evidence or research context changed; preview again")
        with self.access(flow_id, create=True) as (path, row):
            if not ResearchEvidenceStore.identity(row, raw, "bind"):
                if row["invalidated"] or row["revision"] != plan["ledger_revision"]:
                    raise ValueError("Composition evidence ledger changed; preview again")
                if (
                    len(row["bindings"]) >= LIMITS["bindings"]
                    or sum(b["plan"]["node_id"] == raw["node_id"] for b in row["bindings"])
                    >= LIMITS["per_node"]
                ):
                    raise ValueError("Composition association budget exhausted")
                if any(
                    b["state"] == "ACTIVE"
                    and b["plan"]["node_id"] == raw["node_id"]
                    and b["plan"]["run_id"] == raw["run_id"]
                    for b in row["bindings"]
                ):
                    raise ValueError("This exact composition is already linked to this node")
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
                    {"kind": "NAMED_COMPOSITION_LINKED", "binding_id": key, "at": now()}
                )
                atomic(path / "record.json", row)
        return self.report(flow_id)

    def withdraw(self, flow_id, raw):
        exact_object(
            raw,
            {"binding_id", "request_id", "reviewer", "reason", "confirmed"},
            "Withdraw composition association",
        )
        _identifier(raw["request_id"])
        reviewer, reason = (
            text(raw["reviewer"], 200, "Named reviewer"),
            text(raw["reason"], 500, "Reason"),
        )
        if raw["confirmed"] is not True:
            raise ValueError("Explicit named association withdrawal is required")
        with self.access(flow_id) as (path, row):
            if not ResearchEvidenceStore.identity(row, raw, "withdraw"):
                if len(row["requests"]) >= LIMITS["requests"]:
                    raise ValueError("Composition association request budget exhausted")
                binding = next(
                    (b for b in row["bindings"] if b["binding_id"] == raw["binding_id"]), None
                )
                if not binding:
                    raise ValueError("Unknown composition association")
                binding.update(state="WITHDRAWN", reason=reason)
                row["revision"] += 1
                row["requests"][raw["request_id"]] = {
                    "action": "withdraw",
                    "fingerprint": digest(raw),
                    "binding_id": binding["binding_id"],
                }
                row["events"].append(
                    {
                        "kind": "NAMED_COMPOSITION_WITHDRAWN",
                        "reviewer": reviewer,
                        "reason": reason,
                        "at": now(),
                    }
                )
                atomic(path / "record.json", row)
        return self.report(flow_id)

    def catalog(self, flow_id, node_id):
        current = self.report(flow_id)
        node = ResearchEvidenceStore.node(current["research_task"], node_id)
        with self.compositions.transaction(False) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        candidates = []
        for key in ids:
            row = self.compositions.read(key)
            if (
                not row["invalidated"]
                and row["state"] == "COMPLETED"
                and row["math_verdict"] == "PROVED"
                and row["evidence_level"] == "E4"
                and complete_target(row["plan"]["request"]) == node["target"]
            ):
                candidates.append(
                    {
                        "run_id": key,
                        "title": row["plan"]["request"]["title"],
                        "created_at": row["created_at"],
                        "status": "RECORDED_E4_REQUIRES_PREVIEW",
                    }
                )
        return {
            "flow_id": flow_id,
            "node_id": node_id,
            "target": node["target"],
            "candidates": candidates,
            "requires_fresh_source_audit_on_preview": True,
            "model_calls": 0,
            "lean_calls": 0,
        }
