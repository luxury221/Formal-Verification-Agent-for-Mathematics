"""Private cross-domain research notebook; only existing verifiers can publish formal evidence."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
import os
from pathlib import Path
import uuid
from .chat_model import ChatModelService, ModelConfig, model_status
from .domain import canonical, digest
from .formalizer import exact_object
from .model_service import text_field
from .integrity import file_hash
from .research_capacity import ManagedResearchStore as ResearchStore, LIMITS as PROJECT_LIMITS
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier, _now
from . import studio_premises
from . import research_tools
from . import premise_application
from .premise_search import PremiseIndex
from .research_protocol import (
    AREAS,
    BACKENDS,
    scaffold,
    source_request,
    validate_plan,
    validate_round,
)

SCHEMA = "mathematical-research-studio-v1"


def backend_store(root, name):
    if name == "typed":
        from .typed_research import TypedResearchStore

        return TypedResearchStore(root)
    if name == "calculus":
        from .calculus_research import CalculusResearchStore

        return CalculusResearchStore(root)
    if name == "pde":
        from .pde_research import PDEResearchStore

        return PDEResearchStore(root)
    if name == "proof":
        from .proof_router import ProofResearchRouter

        return ProofResearchRouter(root)
    raise ValueError("Unknown formal verification backend")


def backend_context(name):
    if name == "typed":
        from .typed_research import example

        return {"scope": BACKENDS[name]["scope"], "example_request": example()}
    if name == "calculus":
        from .calculus_research import examples
        from .calculus_language import catalog
    elif name == "pde":
        from .pde_research_examples import examples
        from .pde_language import catalog
    elif name == "proof":
        from .proof_router import catalog as proof_catalog

        value = proof_catalog()
        return {
            "scope": BACKENDS[name]["scope"],
            "language": value["language"],
            "supported_languages": value["supported_languages"],
            "example_request": {"nodes": value["examples"][0]["nodes"]},
        }
    else:
        return None
    return {
        "scope": BACKENDS[name]["scope"],
        "language": catalog(),
        "example_request": {"nodes": examples()[0]["nodes"]},
    }


class StudioStore:
    def __init__(self, root, *, service_factory=None):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-studio"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Research notebook storage was redirected")
        self.service_factory = service_factory

    def directory(self, key):
        p = self.base / identifier(key, "study")
        if self.base.resolve() != self.base or p.resolve() != p:
            raise ValueError("Research notebook storage was redirected")
        return p

    def read(self, key):
        row = _envelope(self.directory(key) / "record.json")
        if (
            row["schema"] != SCHEMA
            or row["study_id"] != key
            or digest(source_request(row["source"])) != row["source_fingerprint"]
        ):
            raise ValueError("Research problem identity changed")
        plans = {}
        for item in row["plans"]:
            p = validate_plan(item["plan"], row["source"], row["source_fingerprint"])
            if digest(p) != item["fingerprint"]:
                raise ValueError("Research plan changed")
            plans[item["fingerprint"]] = p
        for item in row["rounds"]:
            proposal = item["proposal"]
            plan = plans[proposal["plan_fingerprint"]]
            node = next(n for n in plan["steps"] if n["id"] == proposal["node_id"])
            validate_round(proposal, row["source_fingerprint"], digest(plan), node)
            if (item["math_verdict"], item["evidence_level"]) != ("UNKNOWN", "E0"):
                raise ValueError("Research prose cannot grant mathematical evidence")
        studio_premises.validate_citations(row)
        for operation in row["operations"]:
            research_tools.validate_operation(operation)
        premise_application.validate_references(row)
        return row

    def view(self, row):
        plan = row["plans"][-1] if row["plans"] else None
        return {
            **deepcopy(row),
            "plan": plan["plan"] if plan else None,
            "plan_fingerprint": plan["fingerprint"] if plan else None,
            "active_operation": next(
                (x for x in reversed(row["operations"]) if x["state"] == "RUNNING"), None
            ),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "citation_states": studio_premises.states(self.root, row),
            "application_states": premise_application.states(self, row),
            "model": model_status(self.root),
            "model_calls": sum(
                research_tools.calls(x) for x in row["operations"] if x["producer_kind"] == "model"
            ),
            "simulation_calls": sum(
                research_tools.calls(x)
                for x in row["operations"]
                if x["producer_kind"] == "fixture"
            ),
        }

    def report(self, key):
        return self.view(self.read(key))

    def listing(self):
        return [
            {
                "study_id": p.name,
                "title": r["source"]["title"],
                "areas": r["source"]["areas"],
                "revision": r["revision"],
                "rounds": len(r["rounds"]),
                "created_at": r["created_at"],
            }
            for p in self.paths()
            for r in [self.read(p.name)]
        ]

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Research storage changed")
        paths = sorted(self.base.glob("study-*"))
        if len(paths) > PROJECT_LIMITS["retained_projects"]:
            raise ValueError("Retained research notebook budget exceeded")
        return paths

    @staticmethod
    def remember(row, data):
        _identifier(data["request_id"])
        fingerprint = digest(data)
        old = next((x for x in row["requests"] if x["id"] == data["request_id"]), None)
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("Request identifier belongs to different content")
            return False
        if (
            type(data["expected_revision"]) is not int
            or data["expected_revision"] != row["revision"]
        ):
            raise ValueError(
                "Notebook changed; reload before changing the plan or research attempt"
            )
        if len(row["requests"]) >= 64:
            raise ValueError("Notebook action budget exhausted; export before continuing")
        row["requests"].append({"id": data["request_id"], "fingerprint": fingerprint})
        return True

    def save(self, row, kind):
        if len(row["events"]) >= 128:
            raise ValueError("Notebook history budget exhausted")
        row["revision"] += 1
        row["events"].append({"at": _now(), "kind": kind, "revision": row["revision"]})
        atomic(self.directory(row["study_id"]) / "record.json", row)

    def change(self, key, data, kind, action):
        with lease(self.directory(key)):
            row = self.read(key)
            if self.remember(row, data):
                if any(x["state"] == "RUNNING" for x in row["operations"]):
                    raise ValueError("Finish or cancel the current model operation first")
                action(row)
                self.save(row, kind)
        return self.view(row)

    def create(self, data):
        exact_object(data, {"request_id", "source"}, "Create research problem")
        _identifier(data["request_id"])
        source = source_request(data["source"])
        with lease(self.base):
            for path in self.paths():
                old = self.read(path.name)
                if old["request_id"] == data["request_id"]:
                    if old["source_fingerprint"] != digest(source):
                        raise ValueError("Problem request identifier already has different content")
                    return self.view(old)
            if len(self.paths()) >= PROJECT_LIMITS["retained_projects"]:
                raise ValueError("Retained research notebook budget exhausted")
            parent = ResearchStore(self.root).create(
                {
                    "request_id": "studio-parent-" + digest(data),
                    "title": source["title"],
                    "question": source["question"],
                }
            )
            key = "study-" + uuid.uuid4().hex
            row = {
                "schema": SCHEMA,
                "study_id": key,
                "request_id": data["request_id"],
                "source": source,
                "source_fingerprint": digest(source),
                "project_id": parent["project_id"],
                "created_at": _now(),
                "revision": 0,
                "plans": [],
                "rounds": [],
                "transfers": [],
                "feedback": [],
                "operations": [],
                "requests": [],
                "events": [],
            }
            staging = self.base / (".creating-" + key)
            staging.mkdir()
            atomic(staging / "record.json", row)
            staging.rename(self.directory(key))
        return self.view(row)

    @staticmethod
    def node(row, key):
        if not row["plans"]:
            raise ValueError("Save a research plan first")
        node = next((n for n in row["plans"][-1]["plan"]["steps"] if n["id"] == key), None)
        if not node:
            raise ValueError("Select a step from the current plan")
        return node

    @staticmethod
    def add_plan(row, plan, producer):
        if len(row["plans"]) >= 8:
            raise ValueError("Plan revision budget exhausted")
        plan = validate_plan(plan, row["source"], row["source_fingerprint"])
        row["plans"].append(
            {"fingerprint": digest(plan), "plan": plan, "producer": producer, "at": _now()}
        )

    def plan(self, key, data):
        exact_object(data, {"request_id", "expected_revision", "plan"}, "Save research plan")
        return self.change(
            key, data, "PLAN_SAVED", lambda row: self.add_plan(row, data["plan"], {"kind": "human"})
        )

    def scaffold(self, key, data):
        exact_object(data, {"request_id", "expected_revision"}, "Start from original question")
        return self.change(
            key,
            data,
            "ORIGINAL_QUESTION_SCAFFOLD",
            lambda row: self.add_plan(
                row,
                scaffold(row["source"], row["source_fingerprint"]),
                {"kind": "original-question-scaffold", "model_calls": 0},
            ),
        )

    @staticmethod
    def add_round(row, proposal, producer):
        if len(row["rounds"]) >= 24:
            raise ValueError("Research attempt budget exhausted")
        node = StudioStore.node(row, proposal["node_id"])
        proposal = validate_round(
            proposal, row["source_fingerprint"], row["plans"][-1]["fingerprint"], node
        )
        row["rounds"].append(
            {
                "round_id": "round-" + uuid.uuid4().hex,
                "proposal": proposal,
                "producer": producer,
                "at": _now(),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
        )

    def add_manual_round(self, key, data):
        exact_object(
            data, {"request_id", "expected_revision", "proposal"}, "Save mathematical attempt"
        )
        return self.change(
            key,
            data,
            "RESEARCH_ATTEMPT_SAVED",
            lambda row: self.add_round(row, data["proposal"], {"kind": "human"}),
        )

    @contextmanager
    def model_lock(self, key):
        fd = os.open(
            self.directory(key) / ".model.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerBusy("A live model operation still owns this notebook") from error
            yield fd
        finally:
            os.close(fd)

    def generate(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "operation", "node_id", "backend"},
            "Bounded research model request",
        )
        if data["operation"] not in ("plan", "step") or data["backend"] not in ("none", *BACKENDS):
            raise ValueError("Select plan/step and a known backend")
        with self.model_lock(key) as fd:
            with lease(self.directory(key)):
                row = self.read(key)
                if not self.remember(row, data):
                    return self.view(row)
                if any(x["state"] == "RUNNING" for x in row["operations"]):
                    raise ValueError("Recover or finish the previous model operation first")
                config = ModelConfig.load(self.root)
                if config is None:
                    raise ValueError(
                        "MODEL_NOT_CONFIGURED: the problem and manual attempts remain available"
                    )
                if len(row["operations"]) >= 16:
                    raise ValueError(
                        "Sixteen model requests per notebook; export before continuing"
                    )
                remaining_calls = 16 - sum(research_tools.calls(x) for x in row["operations"])
                if remaining_calls <= 0:
                    raise ValueError("Sixteen model calls per notebook exhausted")
                call_limit = min(
                    research_tools.MAX_CALLS if data["operation"] == "step" else 1, remaining_calls
                )
                if len(row["events"]) + 3 * call_limit + 3 > 128:
                    raise ValueError(
                        "Export notebook before starting another complete research operation"
                    )
                if len(canonical(row)) + 262144 > 2 * 1024 * 1024:
                    raise ValueError(
                        "Export notebook before starting a model operation: storage headroom exhausted"
                    )
                context = {
                    "source": row["source"],
                    "source_fingerprint": row["source_fingerprint"],
                    "project_id": row["project_id"],
                }
                if data["operation"] == "step":
                    node = self.node(row, data["node_id"])
                    context.update(
                        plan=row["plans"][-1]["plan"],
                        plan_fingerprint=row["plans"][-1]["fingerprint"],
                        node=node,
                        backend=data["backend"],
                        backend_context=backend_context(data["backend"]),
                        retrieved_premises=studio_premises.context(self.root, row, node["id"]),
                        theorem_applications=premise_application.context(self, row, node["id"]),
                        verifier_feedback=[
                            x
                            for x in row["feedback"]
                            if x["node_id"] == node["id"]
                            and x["plan_fingerprint"] == row["plans"][-1]["fingerprint"]
                        ][-2:],
                        previous_attempts=[
                            r
                            for r in row["rounds"]
                            if r["proposal"]["node_id"] == node["id"]
                            and r["proposal"]["plan_fingerprint"] == row["plans"][-1]["fingerprint"]
                        ][-2:],
                    )
                elif data["node_id"] or data["backend"] != "none":
                    raise ValueError("Planning has no selected node or verification backend")
                if len(canonical(context).decode()) > 100000:
                    raise ValueError(
                        "Complete context exceeds model input budget; shorten the plan, never drop hypotheses"
                    )
                operation_id = "studio-" + uuid.uuid4().hex
                op = {
                    "id": operation_id,
                    "request_id": data["request_id"],
                    "kind": data["operation"],
                    "node_id": data["node_id"],
                    "state": "RUNNING",
                    "cancel_requested": False,
                    "request_fingerprint": None,
                    "context_fingerprint": digest(context),
                    "config_fingerprint": config.fingerprint,
                    "budget": {
                        "timeout_seconds": 90,
                        "max_output_tokens": 8192,
                        "max_output_bytes": 131072,
                        "max_input_chars": 100000,
                        "max_model_calls": call_limit,
                        "max_tools": min(3, call_limit - 1),
                    },
                    "loop_version": research_tools.VERSION,
                    "plan_fingerprint": row["plans"][-1]["fingerprint"] if row["plans"] else None,
                    "calls": [],
                    "tools": [],
                    "trace_files": [],
                    "producer_kind": "fixture" if self.service_factory else "model",
                    "error": None,
                }
                row["operations"].append(op)
                self.save(row, "MODEL_REQUEST_STARTED")

            def journal(kind, payload):
                with lease(self.directory(key)):
                    latest = self.read(key)
                    active = next(x for x in latest["operations"] if x["id"] == operation_id)
                    directory = self.directory(key) / operation_id
                    if directory.resolve() != directory:
                        raise ValueError("Research operation storage was redirected")
                    directory.mkdir(exist_ok=True)
                    if kind == "CALL_STARTED":
                        ordinal = len(active["calls"]) + 1
                        request_fp = digest(payload)
                        active["calls"].append(
                            {
                                "id": payload["id"],
                                "request_fingerprint": request_fp,
                                "budget": payload["budget"],
                                "state": "STARTED",
                            }
                        )
                        if ordinal == 1:
                            active["request_fingerprint"] = request_fp
                        filename = "call-" + str(ordinal) + "-request.json"
                    elif kind == "CALL_FINISHED":
                        ordinal = len(active["calls"])
                        call = active["calls"][-1]
                        call.update(
                            state="COMPLETED", usage=payload["usage"], producer=payload["identity"]
                        )
                        filename = "call-" + str(ordinal) + "-response.json"
                    else:
                        active["tools"].append(research_tools.summary(payload))
                        filename = "tool-" + str(len(active["tools"])) + ".json"
                    path = directory / filename
                    atomic(
                        path,
                        {
                            "event": kind,
                            "at": _now(),
                            "data": payload,
                            "math_verdict": "UNKNOWN",
                            "evidence_level": "E0",
                        },
                    )
                    active["trace_files"].append({"name": filename, "sha256": file_hash(path)})
                    self.save(latest, kind)

            response = None
            error = None
            usage = None
            try:
                factory = self.service_factory or ChatModelService
                response, output, usage = research_tools.run(
                    self.root,
                    config,
                    factory,
                    context,
                    operation_id,
                    data["operation"],
                    call_limit,
                    cancel_requested=lambda: self.read(key)["operations"][-1]["cancel_requested"],
                    lifecycle_lock_fd=fd,
                    journal=journal,
                    fixture=bool(self.service_factory),
                )
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                error = type(exc).__name__ + ": " + str(exc)[:500]
            with lease(self.directory(key)):
                row = self.read(key)
                op = next(x for x in row["operations"] if x["id"] == operation_id)
                if response is not None:
                    op.update(producer=asdict(response.identity), usage=usage)
                op["usage"] = {
                    name: sum(call.get("usage", {}).get(name, 0) for call in op["calls"])
                    for name in ("input_tokens", "output_tokens")
                }
                if op["cancel_requested"]:
                    op["state"] = "CANCELLED"
                elif error:
                    op.update(state="FAILED", error=error)
                else:
                    try:
                        producer = asdict(response.identity)
                        if data["operation"] == "plan":
                            self.add_plan(row, output, producer)
                        else:
                            if (
                                output.get("formalization")
                                and output["formalization"]["backend"] != data["backend"]
                            ):
                                raise ValueError(
                                    "Model selected an unrequested verification backend"
                                )
                            self.add_round(row, output, producer)
                            row["rounds"][-1]["operation_id"] = operation_id
                        op["state"] = "COMPLETED"
                    except (ValueError, TypeError, KeyError) as exc:
                        op.update(state="FAILED", error=type(exc).__name__ + ": " + str(exc)[:500])
                for call in op["calls"]:
                    if call["state"] == "STARTED":
                        call["state"] = "CANCELLED" if op["state"] == "CANCELLED" else "FAILED"
                self.save(row, "MODEL_REQUEST_FINISHED")
            return self.view(row)

    def cancel(self, key, data):
        exact_object(data, {"operation_id"}, "Cancel model operation")
        with lease(self.directory(key)):
            row = self.read(key)
            op = next((x for x in row["operations"] if x["id"] == data["operation_id"]), None)
            if op is None:
                raise ValueError("Unknown model operation")
            if op["state"] == "RUNNING" and not op["cancel_requested"]:
                op["cancel_requested"] = True
                self.save(row, "MODEL_CANCELLATION_REQUESTED")
        return self.view(row)

    def recover(self, key, data):
        exact_object(data, {"operation_id"}, "Recover interrupted model operation")
        with self.model_lock(key), lease(self.directory(key)):
            row = self.read(key)
            op = next((x for x in row["operations"] if x["id"] == data["operation_id"]), None)
            if op is None:
                raise ValueError("Unknown operation")
            if op["state"] == "RUNNING":
                op.update(
                    state="INTERRUPTED",
                    error="Supervisor stopped; explicit new request required, no automatic retry",
                )
                for call in op.get("calls", []):
                    if call["state"] == "STARTED":
                        call["state"] = "INTERRUPTED"
                self.save(row, "INTERRUPTED_OPERATION_RECOVERED")
        return self.view(row)

    def round(self, row, round_id):
        item = next((x for x in row["rounds"] if x["round_id"] == round_id), None)
        if item is None or item["proposal"]["plan_fingerprint"] != row["plans"][-1]["fingerprint"]:
            raise ValueError("Select an attempt from the current plan")
        if item["proposal"]["formalization"] is None:
            raise ValueError("This attempt has no formalization proposal")
        return item

    def preview_transfer(self, key, data):
        exact_object(data, {"round_id"}, "Preview complete formalization")
        row = self.read(key)
        item = self.round(row, data["round_id"])
        formal = item["proposal"]["formalization"]
        raw = {"project_id": row["project_id"], "branch_id": "main", **formal["request"]}
        plan = backend_store(self.root, formal["backend"]).preview(raw)
        value = {
            "study_id": key,
            "revision": row["revision"],
            "round_id": data["round_id"],
            "source": row["source"],
            "research_node": self.node(row, item["proposal"]["node_id"]),
            "backend": formal["backend"],
            "formal_plan": plan,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        value["preview_fingerprint"] = digest(value)
        return value

    def transfer(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "round_id",
                "preview_fingerprint",
                "reviewer",
                "confirmed",
            },
            "Save complete verifier task",
        )
        text_field(data["reviewer"], "Semantic reviewer", 200)
        if data["confirmed"] is not True:
            raise ValueError(
                "Confirm correspondence of original definitions, all hypotheses and formal target"
            )

        def action(row):
            if len(row["transfers"]) >= 16:
                raise ValueError("Verifier transfer budget exhausted")
            preview = self.preview_transfer(key, {"round_id": data["round_id"]})
            if preview["preview_fingerprint"] != data["preview_fingerprint"]:
                raise ValueError("Full formal preview or original context changed")
            name, plan = preview["backend"], preview["formal_plan"]
            graph = backend_store(self.root, name).create(
                {
                    "request_id": "studio-bridge-" + digest(data),
                    "request": plan["request"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                }
            )
            href = BACKENDS[name]["href"] + "#" + row["project_id"] + "/main/" + graph["graph_id"]
            row["transfers"].append(
                {
                    "round_id": data["round_id"],
                    "backend": name,
                    "graph_id": graph["graph_id"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "href": href,
                    "reviewer": data["reviewer"],
                    "at": _now(),
                }
            )

        return self.change(key, data, "FORMAL_TASK_SAVED_WITHOUT_EXECUTION", action)

    def evidence(self, key, data):
        exact_object(data, {"graph_id"}, "Read current verifier evidence")
        row = self.read(key)
        transfer = next((x for x in row["transfers"] if x["graph_id"] == data["graph_id"]), None)
        if transfer is None:
            raise ValueError("Graph is not bound to this research notebook")
        report = backend_store(self.root, transfer["backend"]).report(transfer["graph_id"])
        if report["plan"]["plan_fingerprint"] != transfer["plan_fingerprint"]:
            raise ValueError("Saved formal task identity changed")
        return {
            "formal_report": report,
            "scope": "This exact formal candidate; research prose and other steps remain unverified",
            "original_research_verdict": "UNKNOWN",
            "evidence_checked": True,
        }

    def feedback(self, key, data):
        exact_object(
            data, {"request_id", "expected_revision", "graph_id"}, "Collect verifier feedback"
        )

        def action(row):
            if len(row["feedback"]) >= 16:
                raise ValueError("Verifier feedback budget exhausted")
            checked = self.evidence(key, {"graph_id": data["graph_id"]})["formal_report"]
            transfer = next(x for x in row["transfers"] if x["graph_id"] == data["graph_id"])
            attempt = self.round(row, transfer["round_id"])
            diagnostics = []
            for child in checked["checks"].values():
                result = child.get("result") or {}
                diagnostic = result.get("diagnostic") or {}
                if diagnostic.get("text"):
                    diagnostics.append(diagnostic["text"][:12000])
                if result.get("error"):
                    diagnostics.append(str(result["error"])[:2000])
            row["feedback"].append(
                {
                    "graph_id": data["graph_id"],
                    "node_id": attempt["proposal"]["node_id"],
                    "plan_fingerprint": attempt["proposal"]["plan_fingerprint"],
                    "at": _now(),
                    "formal_math_verdict_at_read": checked["math_verdict"],
                    "formal_evidence_at_read": checked["evidence_level"],
                    "invalidated_at_read": checked["invalidated"],
                    "diagnostics": diagnostics,
                    "execution_states": [x["state"] for x in checked["attempts"]],
                    "scope": "Historical feedback for this exact formal candidate, not original research certification",
                }
            )

        return self.change(key, data, "VERIFIER_FEEDBACK_COLLECTED", action)

    def catalog(self):
        return {
            "schema": SCHEMA,
            "areas": AREAS,
            "backends": BACKENDS,
            "model": model_status(self.root),
            "premise_index": PremiseIndex(self.root).status(),
            "limits": {
                "steps": 12,
                "plans": 8,
                "rounds": 24,
                "model_requests": 16,
                "model_calls_per_notebook": 16,
                "model_timeout_seconds": 90,
                "output_tokens": 8192,
                "model_calls_per_operation": research_tools.MAX_CALLS,
                "tools_per_operation": research_tools.MAX_TOOLS,
            },
            "capabilities": {
                "cross_domain_problem_input": True,
                "cross_domain_model_reasoning": "requires configured model",
                "model_library_search": "requires configured model and ready source index",
                "model_representation_preview": "structure only; never executes Lean",
                "arbitrary_lean_execution": False,
                "all_mathematics_solved": False,
            },
        }
