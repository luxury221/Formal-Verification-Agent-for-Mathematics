"""Local workbench transport; all execution and verdicts come from TaskStore."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit, parse_qs

from .certificates import MODE, REFERENCE_MODE, TARGETS, target_descriptor
from .domain import canonical
from .formalizer import exact_object, strict_json
from .tasks import TaskStore, WorkerBusy
from .ideas import IdeaStore
from .chat_model import model_status
from .paper_review import PaperReviewStore, prepare_review
from .knowledge import KnowledgeStore
from .research_capacity import ManagedResearchStore as ResearchStore
from . import research_capacity_web
from .research_runs import ResearchRunStore
from .knowledge_instances import KnowledgeInstanceStore
from .mathlib_lab import MathlibLabStore
from .research_mathlib import ResearchMathlibStore
from .mathlib_knowledge import MathlibKnowledgeStore
from .mathlib_reuse import MathlibReuseStore
from .number_theory_lab import NumberTheoryStore
from .number_theory_search import search as search_number_theory
from .lean_strategies import LeanStrategyStore
from .research_flow import ResearchFlowStore
from .proof_steps import ProofStepStore
from .research_evidence import ResearchEvidenceStore
from .research_continuation import ResearchContinuationStore
from .lemma_composition import LemmaCompositionStore
from . import composition_web
from .research_compositions import ResearchCompositionStore
from .proof_library import ProofLibraryStore
from . import proof_library_web
from .research_campaign import ResearchCampaignStore
from . import campaign_web
from .typed_research import TypedResearchStore
from . import typed_research_web
from .typed_reuse import TypedReuseStore
from . import typed_reuse_web
from .structure_graph_publication import PublishedStructureResearchStore as StructureResearchStore
from . import structure_research_web
from .structure_reuse import StructureReuseStore
from . import structure_reuse_web
from .recursion_programs import RecursionStore
from .structure_lemma_library import LemmaExportStore, ExportApplicationStore
from . import lemma_research_web
from .calculus_research import CalculusResearchStore
from . import calculus_research_web
from .proof_router import ProofResearchRouter
from . import proof_research_web
from .pde_research import PDEResearchStore
from . import pde_research_web
from .research_studio import StudioStore
from . import studio_web
from .article_audit import ArticleAuditStore
from . import article_audit_web
from . import article_recheck_web
from .article_rechecks import ArticleRecheckStore
from .article_recheck_worker import ArticleRecheckPump
from .math_assets import MATH_ASSETS
from .document_intake import DocumentStore, DocumentPump
from . import document_web
from .document_obligations import ObligationStore
from . import obligation_web
from .paper_automation import PaperAutomationStore
from .paper_pipeline import PaperPump
from . import paper_web

MAX_BODY = 196608
ASSETS = {
    "/automation": ("automation.html", "text/html"),
    "/automation.js": ("automation.js", "text/javascript"),
    "/automation.css": ("automation.css", "text/css"),
    "/documents": ("documents.html", "text/html"),
    "/documents.js": ("documents.js", "text/javascript"),
    "/documents.css": ("documents.css", "text/css"),
    "/obligations": ("obligations.html", "text/html"),
    "/obligations.js": ("obligations.js", "text/javascript"),
    "/obligations.css": ("obligations.css", "text/css"),
    "/articles": ("articles.html", "text/html"),
    "/articles.js": ("articles.js", "text/javascript"),
    "/articles.css": ("articles.css", "text/css"),
    "/studio": ("studio.html", "text/html"),
    "/studio.js": ("studio.js", "text/javascript"),
    "/studio.css": ("studio.css", "text/css"),
    "/pde": ("pde-research.html", "text/html"),
    "/pde-research.js": ("pde-research.js", "text/javascript"),
    "/pde-research.css": ("pde-research.css", "text/css"),
    "/analysis": ("analysis-research.html", "text/html"),
    "/proof": ("proof-research.html", "text/html"),
    "/analysis-research.js": ("analysis-research.js", "text/javascript"),
    "/analysis-research.css": ("analysis-research.css", "text/css"),
    "/typed-research/lemmas": ("typed-research.html", "text/html"),
    "/lemma-research.js": ("lemma-research.js", "text/javascript"),
    "/lemma-research.css": ("lemma-research.css", "text/css"),
    "/structure-notation.js": ("structure-notation.js", "text/javascript"),
    "/typed-research": ("typed-research.html", "text/html"),
    "/typed-research.js": ("typed-research.js", "text/javascript"),
    "/typed-research.css": ("typed-research.css", "text/css"),
    "/typed-transport.js": ("typed-transport.js", "text/javascript"),
    "/typed-reuse.js": ("typed-reuse.js", "text/javascript"),
    "/typed-reuse.css": ("typed-reuse.css", "text/css"),
    "/campaigns": ("workspace.html", "text/html"),
    "/campaigns.js": ("campaigns.js", "text/javascript"),
    "/campaigns.css": ("campaigns.css", "text/css"),
    "/proof-library": ("workspace.html", "text/html"),
    "/library-compositions": ("workspace.html", "text/html"),
    "/proof-library.js": ("proof-library.js", "text/javascript"),
    "/proof-library.css": ("proof-library.css", "text/css"),
    "/compositions": ("workspace.html", "text/html"),
    "/compositions.js": ("compositions.js", "text/javascript"),
    "/composition-notation.js": ("composition-notation.js", "text/javascript"),
    "/compositions.css": ("compositions.css", "text/css"),
    "/research-compositions.js": ("research-compositions.js", "text/javascript"),
    "/research-compositions.css": ("research-compositions.css", "text/css"),
    "/": ("workspace.html", "text/html"),
    "/verify": ("index.html", "text/html"),
    "/papers": ("workspace.html", "text/html"),
    "/research": ("workspace.html", "text/html"),
    "/library": ("workspace.html", "text/html"),
    "/mathlib": ("workspace.html", "text/html"),
    "/mathlib.js": ("mathlib.js", "text/javascript"),
    "/mathlib.css": ("mathlib.css", "text/css"),
    "/research-mathlib.js": ("research-mathlib.js", "text/javascript"),
    "/research-mathlib.css": ("research-mathlib.css", "text/css"),
    "/mathlib-knowledge.js": ("mathlib-knowledge.js", "text/javascript"),
    "/mathlib-knowledge.css": ("mathlib-knowledge.css", "text/css"),
    "/mathlib-reuse.js": ("mathlib-reuse.js", "text/javascript"),
    "/mathlib-reuse.css": ("mathlib-reuse.css", "text/css"),
    "/mathlib-evaluation.js": ("mathlib-evaluation.js", "text/javascript"),
    "/mathlib-evaluation.css": ("mathlib-evaluation.css", "text/css"),
    "/number-theory": ("workspace.html", "text/html"),
    "/number-theory.js": ("number-theory.js", "text/javascript"),
    "/number-theory-search.js": ("number-theory-search.js", "text/javascript"),
    "/number-theory-search.css": ("number-theory-search.css", "text/css"),
    "/search-evaluation.js": ("search-evaluation.js", "text/javascript"),
    "/search-evaluation.css": ("search-evaluation.css", "text/css"),
    "/counterexamples.js": ("counterexamples.js", "text/javascript"),
    "/counterexamples.css": ("counterexamples.css", "text/css"),
    "/strategies": ("workspace.html", "text/html"),
    "/lean-strategies.js": ("lean-strategies.js", "text/javascript"),
    "/lean-strategies.css": ("lean-strategies.css", "text/css"),
    "/research-flow.js": ("research-flow.js", "text/javascript"),
    "/research-loading.js": ("research-loading.js", "text/javascript"),
    "/research-flow.css": ("research-flow.css", "text/css"),
    "/proof-steps.js": ("proof-steps.js", "text/javascript"),
    "/proof-steps.css": ("proof-steps.css", "text/css"),
    "/research-evidence.js": ("research-evidence.js", "text/javascript"),
    "/research-evidence.css": ("research-evidence.css", "text/css"),
    "/research-continuation.js": ("research-continuation.js", "text/javascript"),
    "/research-continuation.css": ("research-continuation.css", "text/css"),
    "/evaluations": ("workspace.html", "text/html"),
    "/activity": ("workspace.html", "text/html"),
    "/workflow": ("workspace.html", "text/html"),
    "/settings": ("workspace.html", "text/html"),
    "/shell.js": ("shell.js", "text/javascript"),
    "/workspace.js": ("workspace.js", "text/javascript"),
    "/workspace.css": ("workspace.css", "text/css"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/papers.js": ("papers.js", "text/javascript"),
    "/library.js": ("library.js", "text/javascript"),
    "/research.js": ("research.js", "text/javascript"),
    "/research-capacity.js": ("research-capacity.js", "text/javascript"),
    "/research-capacity.css": ("research-capacity.css", "text/css"),
    "/research.css": ("research.css", "text/css"),
    "/research-runs.js": ("research-runs.js", "text/javascript"),
    "/research-runs.css": ("research-runs.css", "text/css"),
    "/knowledge-instances.js": ("knowledge-instances.js", "text/javascript"),
    "/knowledge-instances.css": ("knowledge-instances.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/ideas": ("ideas.html", "text/html"),
    "/ideas.js": ("ideas.js", "text/javascript"),
}

ASSETS.update(MATH_ASSETS)


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class TaskPump:
    """One local worker; restart recovers interrupted state but never retries it."""

    def __init__(
        self,
        store: TaskStore,
        ideas=None,
        papers=None,
        research_runs=None,
        instances=None,
        mathlib=None,
        research_mathlib=None,
        mathlib_knowledge=None,
        mathlib_reuse=None,
        number_theory=None,
        strategies=None,
        research_flow=None,
        proof_steps=None,
        continuations=None,
        compositions=None,
        proof_library=None,
        campaigns=None,
        typed_research=None,
        typed_reuse=None,
        structure_research=None,
        structure_reuse=None,
        recursion=None,
        lemma_exports=None,
        export_applications=None,
        calculus_research=None,
        pde_research=None,
        proof_research=None,
    ):
        self.store = store
        self.ideas = ideas
        self.papers = papers
        self.research_runs = research_runs
        self.instances = instances
        self.mathlib = mathlib
        self.research_mathlib = research_mathlib
        self.last_mathlib_reconcile = 0
        self.mathlib_knowledge = mathlib_knowledge
        self.last_knowledge_reconcile = 0
        self.mathlib_reuse = mathlib_reuse
        self.number_theory = number_theory
        self.strategies = strategies
        self.research_flow = research_flow
        self.proof_steps = proof_steps
        self.continuations = continuations
        self.compositions = compositions
        self.proof_library = proof_library
        self.campaigns = campaigns
        self.typed_research = typed_research
        self.typed_reuse = typed_reuse
        self.structure_research = structure_research
        self.structure_reuse = structure_reuse
        self.recursion = recursion
        self.lemma_exports = lemma_exports
        self.export_applications = export_applications
        self.calculus_research = calculus_research
        self.pde_research = pde_research
        self.proof_research = proof_research
        self.last_library_reconcile = 0
        self.last_reuse_reconcile = 0
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.status = {"state": "STARTING", "error": None}
        self.thread = threading.Thread(target=self.run, name="mra-task-worker")

    def set_status(self, state, error=None):
        with self.lock:
            self.status = {"state": state, "error": error}

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                self.set_status("CHECKING_QUEUE")
                result = self.store.work_once()
                if result["worker_state"] == "EXECUTED":
                    continue
                if self.ideas and self.ideas.work_once()["worker_state"] == "EXECUTED":
                    continue
                if self.papers and self.papers.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.research_runs
                    and self.research_runs.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if self.instances and self.instances.work_once()["worker_state"] == "EXECUTED":
                    continue
                if self.mathlib and self.mathlib.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.number_theory
                    and self.number_theory.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if self.research_mathlib and time.monotonic() - self.last_mathlib_reconcile >= 10:
                    self.last_mathlib_reconcile = time.monotonic()
                    self.research_mathlib.reconcile()
                if self.strategies and self.strategies.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.research_flow
                    and self.research_flow.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if self.proof_steps and self.proof_steps.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.continuations
                    and self.continuations.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if (
                    self.compositions
                    and self.compositions.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if self.proof_library:
                    if self.proof_library.compositions.work_once()["worker_state"] == "EXECUTED":
                        continue
                    if time.monotonic() - self.last_library_reconcile >= 10:
                        self.last_library_reconcile = time.monotonic()
                        self.proof_library.reconcile()
                if (
                    self.mathlib_knowledge
                    and time.monotonic() - self.last_knowledge_reconcile >= 10
                ):
                    self.last_knowledge_reconcile = time.monotonic()
                    self.mathlib_knowledge.reconcile()
                if self.mathlib_reuse and time.monotonic() - self.last_reuse_reconcile >= 10:
                    self.last_reuse_reconcile = time.monotonic()
                    self.mathlib_reuse.reconcile()
                if self.campaigns and self.campaigns.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.typed_research
                    and self.typed_research.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if self.typed_reuse and self.typed_reuse.work_once()["worker_state"] == "EXECUTED":
                    continue
                if (
                    self.structure_research
                    and self.structure_research.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                if (
                    self.structure_reuse
                    and self.structure_reuse.work_once()["worker_state"] == "EXECUTED"
                ):
                    continue
                for queue in (
                    self.recursion,
                    self.lemma_exports,
                    self.export_applications,
                    self.calculus_research,
                    self.pde_research,
                    self.proof_research,
                ):
                    if queue and queue.work_once()["worker_state"] == "EXECUTED":
                        break
                else:
                    self.set_status("IDLE")
                    self.wake.wait(0.5)
                continue
            except WorkerBusy:
                self.set_status("BUSY", "Another local worker or checker owns the queue")
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.set_status("BLOCKED", str(error))
            self.wake.wait(0.5)
        self.set_status("STOPPED")

    def close(self):
        self.stopped.set()
        self.wake.set()
        # Finish the current bounded attempt. Never abandon a checker by
        # treating a daemon thread's exit as a successful terminal commit.
        if self.thread.ident is not None:
            self.thread.join()


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root: Path, port: int = 8765, *, start_worker: bool = True):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be an integer in [0, 65535]")
        self.root = root.resolve()
        self.store = TaskStore(self.root)
        self.ideas = IdeaStore(self.root)
        self.papers = PaperReviewStore(self.root)
        self.knowledge = KnowledgeStore(self.root)
        self.research = ResearchStore(self.root)
        self.research_runs = ResearchRunStore(self.root)
        self.instances = KnowledgeInstanceStore(self.root)
        self.mathlib = MathlibLabStore(self.root)
        self.research_mathlib = ResearchMathlibStore(self.root)
        self.mathlib_knowledge = MathlibKnowledgeStore(self.root)
        self.mathlib_reuse = MathlibReuseStore(self.root)
        self.number_theory = NumberTheoryStore(self.root)
        self.strategies = LeanStrategyStore(self.root)
        self.research_flow = ResearchFlowStore(self.root)
        self.proof_steps = ProofStepStore(self.root)
        self.research_evidence = ResearchEvidenceStore(self.root)
        self.continuations = ResearchContinuationStore(self.root)
        self.compositions = LemmaCompositionStore(self.root)
        self.research_compositions = ResearchCompositionStore(self.root)
        self.proof_library = ProofLibraryStore(self.root)
        self.campaigns = ResearchCampaignStore(self.root, self.proof_library)
        self.typed_research = TypedResearchStore(self.root)
        self.typed_reuse = TypedReuseStore(self.root, graphs=self.typed_research)
        self.structure_research = StructureResearchStore(self.root)
        self.structure_reuse = StructureReuseStore(self.root, graphs=self.structure_research)
        self.recursion = RecursionStore(self.root)
        self.lemma_exports = LemmaExportStore(self.root, parents=self.recursion)
        self.export_applications = ExportApplicationStore(self.root, exports=self.lemma_exports)
        self.calculus_research = CalculusResearchStore(self.root)
        self.pde_research = PDEResearchStore(self.root)
        self.proof_research = ProofResearchRouter(self.root)
        self.studio = StudioStore(self.root)
        self.article_audits = ArticleAuditStore(self.root)
        self.article_rechecks = ArticleRecheckStore(self.root, articles=self.article_audits)
        self.documents = DocumentStore(self.root)
        self.obligations = ObligationStore(self.root, documents=self.documents, studio=self.studio)
        self.document_pump = DocumentPump(self.documents) if start_worker else None
        self.paper_automation = PaperAutomationStore(self.root, documents=self.documents)
        self.paper_pump = PaperPump(self.paper_automation) if start_worker else None
        self.csrf = secrets.token_urlsafe(32)
        self.assets = {
            route: ((self.root / "mra/web" / filename).read_bytes(), mime)
            for route, (filename, mime) in ASSETS.items()
        }
        self.pump = (
            TaskPump(
                self.store,
                self.ideas,
                self.papers,
                self.research_runs,
                self.instances,
                self.mathlib,
                self.research_mathlib,
                self.mathlib_knowledge,
                self.mathlib_reuse,
                self.number_theory,
                self.strategies,
                self.research_flow,
                self.proof_steps,
                self.continuations,
                self.compositions,
                self.proof_library,
                self.campaigns,
                self.typed_research,
                self.typed_reuse,
                self.structure_research,
                self.structure_reuse,
                self.recursion,
                self.lemma_exports,
                self.export_applications,
                self.calculus_research,
                self.pde_research,
                self.proof_research,
            )
            if start_worker
            else None
        )
        self.article_recheck_pump = (
            ArticleRecheckPump(self.article_rechecks) if start_worker else None
        )
        super().__init__(("127.0.0.1", port), WorkbenchHandler)
        self.authority = f"127.0.0.1:{self.server_address[1]}"
        self.origin = f"http://{self.authority}"
        if self.pump:
            self.pump.thread.start()
        if self.article_recheck_pump:
            self.article_recheck_pump.thread.start()
        if self.document_pump:
            self.document_pump.thread.start()
        if self.paper_pump:
            self.paper_pump.thread.start()

    def server_close(self):
        # Stop both queues before either join can wait for a provider read or a
        # bounded checker attempt. Construction also uses this path when bind() fails.
        for pump in (self.paper_pump, self.document_pump, self.article_recheck_pump, self.pump):
            if pump:
                pump.stopped.set()
                pump.wake.set()
        super().server_close()
        if self.article_recheck_pump:
            self.article_recheck_pump.close()
        if self.pump:
            self.pump.close()
        if self.document_pump:
            self.document_pump.close()
        if self.paper_pump:
            self.paper_pump.close()

    def catalog(self):
        cases = json.loads((self.root / "evals/golden_cases.json").read_text(encoding="utf-8"))
        examples = {
            "nat_add_zero": ("add-zero.json", "add-zero-lambda.json"),
            "false_all_nat_zero": ("not-all-zero.json",),
        }
        return [
            {
                **target_descriptor(case),
                "case_id": case["id"],
                "samples": [
                    {
                        "name": name,
                        "certificate": json.loads(
                            (self.root / "evals/certificates" / name).read_text(encoding="utf-8")
                        ),
                        "provenance": "repository-author-example-not-model-output",
                    }
                    for name in examples[case["id"]]
                ],
            }
            for case in cases
            if case["id"] in TARGETS
        ]

    def submit(self, data):
        exact_object(
            data,
            {
                "request_id",
                "case_id",
                "input_mode",
                "certificate",
                "timeout_seconds",
                "claim_fingerprint",
                "formalization_fingerprint",
                "alignment",
            },
            "Task submission",
        )
        target = next(
            (target for target in self.catalog() if target["case_id"] == data["case_id"]), None
        )
        if target is None:
            raise ValueError("HTTP target is not enabled")
        for key in ("claim_fingerprint", "formalization_fingerprint"):
            if data[key] != target["certificate_header"][key]:
                raise ValueError("Displayed target changed; reload it before submitting")
        reviewer = None
        if data["alignment"] is not None:
            alignment = exact_object(data["alignment"], {"reviewer", "confirmed"}, "Alignment")
            reviewer = alignment["reviewer"]
            if (
                alignment["confirmed"] is not True
                or not isinstance(reviewer, str)
                or not reviewer.strip()
                or len(reviewer) > 200
            ):
                raise ValueError(
                    "Alignment requires explicit confirmation and a local reviewer name"
                )
        options = {"timeout_seconds": data["timeout_seconds"], "reviewer": reviewer}
        if data["input_mode"] == MODE:
            if not isinstance(data["certificate"], str):
                raise ValueError("Certificate must be raw JSON text")
            task = self.store.submit_certificate(
                data["request_id"], data["case_id"], data["certificate"].encode("utf-8"), **options
            )
        elif data["input_mode"] == REFERENCE_MODE and data["certificate"] is None:
            task = self.store.submit(data["request_id"], data["case_id"], **options)
        else:
            raise ValueError("Unknown input mode or unexpected certificate")
        if self.pump:
            self.pump.wake.set()
        return task

    def evidence(self, task_id):
        task = self.store.report(task_id)
        if task["artifact_status"] != "MATCHES_RECORDED_HASHES":
            return {
                "available": False,
                "artifact_status": task["artifact_status"],
                "fresh_attestation": False,
            }
        attempt = task["attempts"][-1]
        directory = Path(attempt["artifact_directory"])
        result = {
            "available": True,
            "fresh_attestation": False,
            "artifact_status": task["artifact_status"],
        }
        for name, key in (("report.json", "report"), ("receipt.json", "receipt")):
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(self.root / "artifacts/runs"):
                raise HttpError(409, "Artifact path changed; reload task status")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != attempt["file_hashes"][name]:
                raise HttpError(409, "Artifact changed; reload task status")
            result[key] = json.loads(raw)
        return result

    def evaluation_catalog(self):
        from .datasets import load_dataset
        from .model_evaluation import model_evaluation_plan

        dataset = load_dataset(self.root)
        base = self.root / "artifacts/model-evaluations"
        runs = []
        if base.exists() and not base.is_symlink():
            directories = sorted(
                (
                    path
                    for path in base.iterdir()
                    if re.fullmatch(r"nat-plans-[a-f0-9]{32}", path.name)
                    and path.is_dir()
                    and not path.is_symlink()
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:20]
            for path in directories:
                report = path / "report.json"
                if report.is_symlink() or not report.is_file() or report.stat().st_size > 1048576:
                    continue
                try:
                    data = strict_json(report.read_text(encoding="utf-8"))
                    if (
                        data["state"] not in ("RUNNING", "COMPLETED", "CANCELLED", "INTERRUPTED")
                        or type(data["simulation"]) is not bool
                        or type(data["total"]) is not int
                        or not 1 <= data["total"] <= 1000
                        or type(data["processed"]) is not int
                        or not 0 <= data["processed"] <= data["total"]
                        or not isinstance(data["recorded_at"], str)
                        or len(data["recorded_at"]) > 128
                        or (
                            data.get("configured_model") is not None
                            and not isinstance(data["configured_model"], dict)
                        )
                    ):
                        continue
                    runs.append(
                        {
                            "id": path.name,
                            "state": data["state"],
                            "recorded_at": data["recorded_at"],
                            "simulation": data["simulation"],
                            "total": data["total"],
                            "processed": data["processed"],
                            "model": (data.get("configured_model") or {}).get("model"),
                        }
                    )
                except (ValueError, KeyError, TypeError):
                    continue
        return {
            "plan": model_evaluation_plan(self.root),
            "split_counts": {name: len(ids) for name, ids in dataset.manifest["splits"].items()},
            "runs": runs,
            "history_scope": "metadata-only; open a run to check artifact hashes",
        }


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "MRA-Local/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Do not write certificate bodies, query data or tokens to access logs.
        pass

    def reply(self, status, data, mime="application/json"):
        raw = canonical(data) if mime == "application/json" else data
        self.send_response(status)
        content_type = (
            mime + "; charset=utf-8"
            if mime.startswith("text/") or mime in ("application/json", "image/svg+xml")
            else mime
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if status == 503 and mime == "application/json" and data.get("code") == "WORKER_BUSY":
            self.send_header("Retry-After", "1")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # Only trusted local KaTeX layout uses generated inline style attributes.
        # Script execution and style elements remain restricted to local files.
        style_attributes = (
            " style-src-attr 'unsafe-inline';"
            if self.path
            in ("/proof", "/analysis", "/articles", "/studio", "/documents", "/obligations")
            else ""
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self';"
            + style_attributes
            + " connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def guard(self, write=False):
        if self.headers.get_all("Host") != [self.server.authority]:
            raise HttpError(403, "Invalid local Host")
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [self.server.origin]:
            raise HttpError(403, "Cross-origin access is disabled")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HttpError(403, "Cross-site access is disabled")
        if write and (
            origins != [self.server.origin]
            or self.headers.get_all("X-MRA-CSRF") != [self.server.csrf]
        ):
            raise HttpError(403, "Same-origin session token required")

    def body(self, maximum=MAX_BODY):
        if self.headers.get("Transfer-Encoding") is not None:
            raise HttpError(400, "Transfer encoding is unsupported")
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdigit():
            raise HttpError(411, "One Content-Length is required")
        length = int(sizes[0])
        if not 0 < length <= maximum:
            raise HttpError(413, "Request exceeds the JSON byte budget")
        if self.headers.get_all("Content-Type") != ["application/json"]:
            raise HttpError(415, "Content-Type must be application/json")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HttpError(400, "Incomplete JSON body")
        return strict_json(raw.decode("utf-8"))

    def handle_route(self, write=False):
        try:
            self.guard(write)
            if not write and self.path in self.server.assets:
                raw, mime = self.server.assets[self.path]
                return self.reply(200, raw, mime)
            if self.path.split("?", 1)[0] == "/v1/capabilities":
                if write or self.path != "/v1/capabilities":
                    raise ValueError("Capabilities use GET without query parameters")
                from .workflows import capabilities

                value = capabilities(self.server.root)
                for name in ("document", "paper"):
                    pump = getattr(self.server, name + "_pump")
                    value["workers"][name] = (
                        pump.snapshot() if pump else {"state": "EXTERNAL", "error": None}
                    )
                return self.reply(200, value)
            if not write and self.path == "/v1/session":
                return self.reply(
                    200,
                    {
                        "csrf_token": self.server.csrf,
                        "targets": self.server.catalog(),
                        "worker": self.server.pump.snapshot()
                        if self.server.pump
                        else {"state": "EXTERNAL", "error": None},
                        "model_calls": 0,
                        "article_recheck_worker": self.server.article_recheck_pump.snapshot()
                        if self.server.article_recheck_pump
                        else {"state": "EXTERNAL", "error": None},
                        "idea_model": model_status(self.server.root),
                        "document_worker": self.server.document_pump.snapshot()
                        if self.server.document_pump
                        else {"state": "EXTERNAL", "error": None},
                        "paper_worker": self.server.paper_pump.snapshot()
                        if self.server.paper_pump
                        else {"state": "EXTERNAL", "error": None},
                    },
                )
            if self.path == "/v1/tasks":
                if write:
                    return self.reply(202, self.server.submit(self.body()))
                return self.reply(200, {"tasks": self.server.store.list_tasks()})
            if self.path == "/v1/ideas":
                if write:
                    result = self.server.ideas.create(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(202, result)
                return self.reply(200, {"ideas": self.server.ideas.list_ideas()})
            if self.path == "/v1/research":
                if write:
                    return self.reply(201, self.server.research.create(self.body()))
                catalog = self.server.research.capacity_catalog()
                return self.reply(
                    200,
                    {
                        "projects": [
                            p for p in catalog["projects"] if p["lifecycle"]["state"] == "ACTIVE"
                        ],
                        "capacity": {k: catalog[k] for k in ("schema", "counts", "limits")},
                    },
                )
            research_parts = self.path.split("/")
            if document_web.dispatch(self, write):
                return
            if paper_web.dispatch(self, write):
                return
            if obligation_web.dispatch(self, write):
                return
            if research_capacity_web.dispatch(self, write):
                return
            if article_recheck_web.dispatch(self, write):
                return
            if article_audit_web.dispatch(self, write):
                return
            if studio_web.dispatch(self, write):
                return
            if pde_research_web.dispatch(self, write):
                return
            if calculus_research_web.dispatch(self, write):
                return
            if proof_research_web.dispatch(self, write):
                return
            if campaign_web.dispatch(self, write):
                return
            if not write and urlsplit(self.path).path == "/v1/proof-library":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if (
                    set(query) - {"q", "include_inactive"}
                    or any(len(v) != 1 for v in query.values())
                    or query.get("include_inactive", ["false"])[0] not in ("true", "false")
                ):
                    raise ValueError("Invalid proof library query")
                return self.reply(
                    200,
                    self.server.proof_library.search(
                        query.get("q", [""])[0],
                        query.get("include_inactive", ["false"])[0] == "true",
                    ),
                )
            if write and self.path in ("/v1/proof-library/preview", "/v1/proof-library/admit"):
                action = "prepare" if self.path.endswith("preview") else "admit"
                return self.reply(200, getattr(self.server.proof_library, action)(self.body()))
            if research_parts[:4] == ["", "v1", "proof-library", "items"] and len(
                research_parts
            ) in (5, 6):
                library, item_id = self.server.proof_library, research_parts[4]
                if not write and len(research_parts) == 5:
                    return self.reply(200, library.report(item_id))
                if (
                    write
                    and len(research_parts) == 6
                    and research_parts[5] in ("withdraw", "remove")
                ):
                    return self.reply(
                        200,
                        library.retire(item_id, self.body(), remove=research_parts[5] == "remove"),
                    )
            if research_parts[:3] == ["", "v1", "library-compositions"]:
                store = self.server.proof_library.compositions
                if not write and self.path == "/v1/library-compositions/catalog":
                    return self.reply(200, composition_web.catalog(store))
                if not write and len(research_parts) == 5 and research_parts[3] == "sources":
                    return self.reply(
                        200,
                        proof_library_web.source_detail(
                            self.server.proof_library, research_parts[4]
                        ),
                    )
                if write and self.path == "/v1/library-compositions/preview":
                    return self.reply(200, store.preview(self.body()))
                if self.path == "/v1/library-compositions/runs":
                    if write:
                        value = store.submit(self.body())
                        if self.server.pump:
                            self.server.pump.wake.set()
                        return self.reply(201, value)
                    return self.reply(200, {"runs": store.list_runs()})
                if len(research_parts) in (5, 6) and research_parts[3] == "runs":
                    run_id = research_parts[4]
                    if not write and (len(research_parts) == 5 or research_parts[5] == "export"):
                        return self.reply(200, store.report(run_id))
                    if write and len(research_parts) == 6:
                        if research_parts[5] == "cancel":
                            exact_object(self.body(), set(), "Library composition cancellation")
                            return self.reply(200, store.cancel(run_id))
                        if research_parts[5] == "revoke":
                            return self.reply(200, store.revoke(run_id, self.body()))
            if research_parts[:3] == ["", "v1", "research-compositions"]:
                store = self.server.research_compositions
                if not write and len(research_parts) == 4:
                    return self.reply(200, store.report(research_parts[3]))
                if not write and len(research_parts) == 6 and research_parts[4] == "nodes":
                    return self.reply(200, store.catalog(research_parts[3], research_parts[5]))
                if (
                    write
                    and len(research_parts) == 5
                    and research_parts[4] in ("preview", "bind", "withdraw")
                ):
                    return self.reply(
                        200, getattr(store, research_parts[4])(research_parts[3], self.body())
                    )
            if research_parts[:3] == ["", "v1", "research-continuations"]:
                store = self.server.continuations
                if not write and len(research_parts) == 4:
                    return self.reply(200, store.report(research_parts[3]))
                if write and len(research_parts) == 5:
                    flow_id, action = research_parts[3:]
                    raw = self.body()
                    if action == "preview":
                        exact_object(raw, set(), "Continuation preview")
                        return self.reply(200, store.preview(flow_id))
                    if action == "create":
                        exact_object(raw, {"request_id", "plan_fingerprint"}, "Save continuation")
                        return self.reply(201, store.create({**raw, "flow_id": flow_id}))
                    if action in ("propose", "submit", "cancel", "withdraw"):
                        if action == "cancel":
                            exact_object(raw, {"attempt_id"}, "Cancel continuation")
                            result = store.cancel(flow_id, raw["attempt_id"])
                        else:
                            result = getattr(store, action)(flow_id, raw)
                        if action == "submit" and self.server.pump:
                            self.server.pump.wake.set()
                        return self.reply(202 if action == "submit" else 200, result)
            if research_parts[:3] == ["", "v1", "research-evidence"]:
                store = self.server.research_evidence
                if not write and len(research_parts) == 4:
                    return self.reply(200, store.report(research_parts[3]))
                if write and len(research_parts) == 5:
                    flow_id, action = research_parts[3:]
                    if action in ("preview", "bind", "withdraw"):
                        return self.reply(200, getattr(store, action)(flow_id, self.body()))
            if research_parts[:3] == ["", "v1", "proof-steps"]:
                store = self.server.proof_steps
                if self.path == "/v1/proof-steps/preview" and write:
                    return self.reply(200, store.preview(self.body()))
                if self.path == "/v1/proof-steps" and write:
                    return self.reply(201, store.create(self.body()))
                if not write and len(research_parts) == 6 and research_parts[3] == "nodes":
                    return self.reply(200, store.catalog(research_parts[4], research_parts[5]))
                if not write and len(research_parts) == 4:
                    return self.reply(200, store.report(research_parts[3]))
                if write and len(research_parts) == 5:
                    key, action = research_parts[3:]
                    if action == "preview":
                        return self.reply(200, store.preview_step(key, self.body()))
                    if action == "submit":
                        value = store.submit(key, self.body())
                        if self.server.pump:
                            self.server.pump.wake.set()
                        return self.reply(202, value)
                    if action == "cancel":
                        data = self.body()
                        exact_object(data, {"attempt_id"}, "Cancel prefix attempt")
                        return self.reply(200, store.cancel(key, data["attempt_id"]))
            if lemma_research_web.dispatch(self, write):
                return
            if structure_research_web.dispatch(self, write):
                return
            if typed_research_web.dispatch(self, write):
                return
            if structure_reuse_web.dispatch(self, write):
                return
            if typed_reuse_web.dispatch(self, write):
                return
            if write and self.path == "/v1/research-flows/preview":
                return self.reply(200, self.server.research_flow.preview(self.body()))
            if write and self.path == "/v1/research-flows":
                return self.reply(201, self.server.research_flow.create(self.body()))
            if research_parts[:3] == ["", "v1", "research-flows"]:
                if not write and len(research_parts) == 6 and research_parts[3] == "projects":
                    return self.reply(
                        200, self.server.research_flow.catalog(research_parts[4], research_parts[5])
                    )
                if len(research_parts) == 4 and not write:
                    return self.reply(200, self.server.research_flow.report(research_parts[3]))
                if len(research_parts) == 5 and write:
                    flow_id, action = research_parts[3:]
                    if action == "propose":
                        return self.reply(
                            200, self.server.research_flow.propose(flow_id, self.body())
                        )
                    if action == "submit":
                        value = self.server.research_flow.submit(flow_id, self.body())
                        if self.server.pump:
                            self.server.pump.wake.set()
                        return self.reply(202, value)
                    if action == "cancel":
                        data = self.body()
                        exact_object(data, {"attempt_id"}, "Cancel research check")
                        return self.reply(
                            200, self.server.research_flow.cancel(flow_id, data["attempt_id"])
                        )
                    if action == "withdraw":
                        return self.reply(
                            200, self.server.research_flow.withdraw(flow_id, self.body())
                        )
            if not write and urlsplit(self.path).path == "/v1/mathlib-reuse/search":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if (
                    set(query) - {"q", "include_inactive"}
                    or any(len(v) != 1 for v in query.values())
                    or query.get("include_inactive", ["false"])[0] not in ("true", "false")
                ):
                    raise ValueError("Invalid reuse search query")
                return self.reply(
                    200,
                    self.server.mathlib_reuse.search(
                        query.get("q", [""])[0],
                        query.get("include_inactive", ["false"])[0] == "true",
                    ),
                )
            if self.path == "/v1/mathlib-reuse":
                return (
                    self.reply(201, self.server.mathlib_reuse.create(self.body()))
                    if write
                    else self.reply(200, self.server.mathlib_reuse.search())
                )
            if write and self.path == "/v1/mathlib-reuse/preview":
                return self.reply(200, self.server.mathlib_reuse.preview(self.body()))
            if research_parts[:3] == ["", "v1", "mathlib-reuse"] and len(research_parts) in (4, 5):
                item_id = research_parts[3]
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.mathlib_reuse.report(item_id))
                if (
                    not write
                    and len(research_parts) == 5
                    and research_parts[4] in ("request", "export")
                ):
                    method = (
                        self.server.mathlib_reuse.request
                        if research_parts[4] == "request"
                        else self.server.mathlib_reuse.report
                    )
                    return self.reply(200, method(item_id))
                if write and len(research_parts) == 5:
                    if research_parts[4] in ("withdraw", "remove"):
                        return self.reply(
                            200,
                            self.server.mathlib_reuse.retire(
                                item_id, self.body(), remove=research_parts[4] == "remove"
                            ),
                        )
                    if research_parts[4] in ("preview-attachment", "attach"):
                        method = (
                            self.server.mathlib_reuse.preview_attachment
                            if research_parts[4] == "preview-attachment"
                            else self.server.mathlib_reuse.attach
                        )
                        return self.reply(200, method(item_id, self.body()))
            if not write and urlsplit(self.path).path == "/v1/mathlib-knowledge/search":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if (
                    set(query) - {"q", "include_inactive"}
                    or any(len(v) != 1 for v in query.values())
                    or query.get("include_inactive", ["false"])[0] not in ("true", "false")
                ):
                    raise ValueError("Invalid Mathlib knowledge search query")
                return self.reply(
                    200,
                    self.server.mathlib_knowledge.search(
                        query.get("q", [""])[0],
                        query.get("include_inactive", ["false"])[0] == "true",
                    ),
                )
            if self.path == "/v1/mathlib-knowledge":
                return (
                    self.reply(201, self.server.mathlib_knowledge.admit(self.body()))
                    if write
                    else self.reply(200, self.server.mathlib_knowledge.search())
                )
            if write and self.path == "/v1/mathlib-knowledge/prepare":
                return self.reply(200, self.server.mathlib_knowledge.prepare(self.body()))
            if research_parts[:3] == ["", "v1", "mathlib-knowledge"] and len(research_parts) in (
                4,
                5,
            ):
                item_id = research_parts[3]
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.mathlib_knowledge.report(item_id))
                if (
                    not write
                    and len(research_parts) == 5
                    and research_parts[4] in ("request", "export")
                ):
                    method = (
                        self.server.mathlib_knowledge.recheck_request
                        if research_parts[4] == "request"
                        else self.server.mathlib_knowledge.report
                    )
                    return self.reply(200, method(item_id))
                if (
                    write
                    and len(research_parts) == 5
                    and research_parts[4] in ("withdraw", "remove")
                ):
                    result = self.server.mathlib_knowledge.retire(
                        item_id, self.body(), remove=research_parts[4] == "remove"
                    )
                    try:
                        self.server.mathlib_reuse.reconcile(item_id=item_id)
                    except (OSError, RuntimeError, ValueError, sqlite3.Error):
                        result["reuse_reconciliation"] = "DEFERRED_UNTIL_READ_OR_SWEEP"
                    return self.reply(200, result)
            if not write and self.path == "/v1/number-theory/catalog":
                return self.reply(200, self.server.number_theory.catalog())
            if not write and self.path == "/v1/lemma-compositions/catalog":
                return self.reply(200, composition_web.catalog(self.server.compositions))
            if (
                research_parts[:4] == ["", "v1", "lemma-compositions", "sources"]
                and len(research_parts) == 5
                and not write
            ):
                return self.reply(
                    200,
                    composition_web.source_detail(self.server.compositions, research_parts[4]),
                )
            if write and self.path == "/v1/lemma-compositions/preview":
                return self.reply(200, self.server.compositions.preview(self.body()))
            if self.path == "/v1/lemma-compositions/runs":
                if write:
                    value = self.server.compositions.submit(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(201, value)
                return self.reply(200, {"runs": self.server.compositions.list_runs()})
            if research_parts[:4] == ["", "v1", "lemma-compositions", "runs"] and len(
                research_parts
            ) in (5, 6):
                identifier = research_parts[4]
                if not write and (len(research_parts) == 5 or research_parts[5] == "export"):
                    return self.reply(200, self.server.compositions.report(identifier))
                if write and len(research_parts) == 6:
                    if research_parts[5] == "cancel":
                        exact_object(self.body(), set(), "Composition cancellation")
                        return self.reply(200, self.server.compositions.cancel(identifier))
                    if research_parts[5] == "revoke":
                        return self.reply(
                            200, self.server.compositions.revoke(identifier, self.body())
                        )
            if not write and self.path == "/v1/lean-strategies/catalog":
                return self.reply(200, self.server.strategies.catalog())
            if write and self.path == "/v1/lean-strategies/preview":
                return self.reply(200, self.server.strategies.preview(self.body()))
            if self.path == "/v1/lean-strategies/runs":
                if write:
                    value = self.server.strategies.submit(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(201, value)
                return self.reply(200, {"runs": self.server.strategies.list_runs()})
            if research_parts[:4] == ["", "v1", "lean-strategies", "runs"] and len(
                research_parts
            ) in (5, 6):
                identifier = research_parts[4]
                if not write and len(research_parts) == 5:
                    return self.reply(200, self.server.strategies.report(identifier))
                if write and len(research_parts) == 6:
                    if research_parts[5] == "cancel":
                        exact_object(self.body(), set(), "Strategy cancellation")
                        return self.reply(200, self.server.strategies.cancel(identifier))
                    if research_parts[5] == "revoke":
                        return self.reply(
                            200, self.server.strategies.revoke(identifier, self.body())
                        )
            if write and self.path == "/v1/number-theory/preview":
                return self.reply(200, self.server.number_theory.preview(self.body()))
            if write and self.path == "/v1/number-theory/counterexamples/search":
                from .counterexamples import search as counterexample_search

                return self.reply(200, counterexample_search(self.body(), self.server.root))
            if not write and urlsplit(self.path).path.startswith("/v1/counterexamples"):
                from .counterexamples import (
                    catalog as counterexample_catalog,
                    report as counterexample_report,
                )

                url = urlsplit(self.path)
                if url.query:
                    raise HttpError(404, "Unknown counterexample route")
                if url.path == "/v1/counterexamples":
                    return self.reply(200, counterexample_catalog(self.server.root))
                match = re.fullmatch(r"/v1/counterexamples/(cex-[a-f0-9]{32})", url.path)
                if not match:
                    raise HttpError(404, "Unknown counterexample route")
                return self.reply(200, counterexample_report(self.server.root, match[1]))
            if write and self.path == "/v1/number-theory/search":
                return self.reply(
                    200, search_number_theory(self.body(), self.server.number_theory.root)
                )
            if self.path == "/v1/number-theory/runs":
                if write:
                    result = self.server.number_theory.submit(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(201, result)
                return self.reply(200, {"runs": self.server.number_theory.list_runs()})
            if research_parts[:4] == ["", "v1", "number-theory", "runs"] and len(
                research_parts
            ) in (5, 6):
                run_id = research_parts[4]
                if not write and len(research_parts) == 5:
                    return self.reply(200, self.server.number_theory.report(run_id))
                if write and len(research_parts) == 6:
                    if research_parts[5] == "cancel":
                        exact_object(self.body(), set(), "Cancellation")
                        return self.reply(200, self.server.number_theory.cancel(run_id))
                    if research_parts[5] == "revoke":
                        return self.reply(
                            200, self.server.number_theory.revoke(run_id, self.body())
                        )
            if not write and self.path == "/v1/mathlib/catalog":
                return self.reply(200, self.server.mathlib.catalog())
            if write and self.path == "/v1/mathlib/preview":
                return self.reply(200, self.server.mathlib.preview(self.body()))
            if self.path == "/v1/mathlib/runs":
                if write:
                    result = self.server.mathlib.submit(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(202, result)
                return self.reply(200, {"runs": self.server.mathlib.list_runs()})
            if research_parts[:4] == ["", "v1", "mathlib", "runs"] and len(research_parts) in (
                5,
                6,
            ):
                run_id = research_parts[4]
                if not write and len(research_parts) == 5:
                    return self.reply(200, self.server.mathlib.report(run_id))
                if write and len(research_parts) == 6:
                    if research_parts[5] == "cancel":
                        exact_object(self.body(), set(), "Cancel")
                        return self.reply(200, self.server.mathlib.cancel(run_id))
                    if research_parts[5] == "revoke":
                        result = self.server.mathlib.revoke(run_id, self.body())
                        try:
                            self.server.research_mathlib.reconcile(run_id)
                        except (OSError, RuntimeError, ValueError, sqlite3.Error):
                            result["research_reconciliation"] = "DEFERRED_UNTIL_READ_OR_SWEEP"
                        try:
                            self.server.mathlib_knowledge.reconcile(run_id)
                        except (OSError, RuntimeError, ValueError, sqlite3.Error):
                            result["knowledge_reconciliation"] = "DEFERRED_UNTIL_READ_OR_SWEEP"
                        try:
                            self.server.mathlib_reuse.reconcile(run_id=run_id)
                        except (OSError, RuntimeError, ValueError, sqlite3.Error):
                            result["reuse_reconciliation"] = "DEFERRED_UNTIL_READ_OR_SWEEP"
                        return self.reply(200, result)
            if self.path == "/v1/knowledge-instances":
                if write:
                    return self.reply(201, self.server.instances.create(self.body()))
                return self.reply(200, {"instances": self.server.instances.list_instances()})
            if write and self.path == "/v1/knowledge-instances/preview":
                return self.reply(200, self.server.instances.preview(self.body()))
            if research_parts[:3] == ["", "v1", "knowledge-instances"] and len(research_parts) in (
                4,
                5,
            ):
                instance_id = research_parts[3]
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.instances.report_instance(instance_id))
                if write and len(research_parts) == 5 and research_parts[4] == "run-plan":
                    return self.reply(200, self.server.instances.prepare(instance_id, self.body()))
                if write and len(research_parts) == 5 and research_parts[4] == "runs":
                    result = self.server.instances.submit(instance_id, self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(201, result)
            if research_parts[:3] == ["", "v1", "instance-runs"] and len(research_parts) in (4, 5):
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.instances.report(research_parts[3]))
                if write and len(research_parts) == 5 and research_parts[4] == "cancel":
                    exact_object(self.body(), set(), "Cancel instance verification")
                    return self.reply(200, self.server.instances.cancel(research_parts[3]))
            if len(research_parts) >= 4 and research_parts[:3] == ["", "v1", "research-runs"]:
                run_id = research_parts[3]
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.research_runs.report(run_id))
                if write and len(research_parts) == 5 and research_parts[4] == "cancel":
                    exact_object(self.body(), set(), "Cancel research execution")
                    return self.reply(200, self.server.research_runs.cancel(run_id))
            if len(research_parts) >= 4 and research_parts[:3] == ["", "v1", "research"]:
                project_id = research_parts[3]
                if (
                    not write
                    and len(research_parts) == 5
                    and research_parts[4] == "mathlib-branches"
                ):
                    return self.reply(200, self.server.research_mathlib.branches(project_id))
                if len(research_parts) >= 6 and research_parts[4] == "mathlib":
                    action = research_parts[5]
                    if not write and len(research_parts) == 6:
                        return self.reply(
                            200, self.server.research_mathlib.report(project_id, action)
                        )
                    if (
                        not write
                        and len(research_parts) == 7
                        and research_parts[6] in ("history", "export")
                    ):
                        method = getattr(self.server.research_mathlib, research_parts[6])
                        return self.reply(200, method(project_id, action))
                    methods = {
                        "edit": "edit",
                        "preview-attachment": "preview_attachment",
                        "attach": "attach",
                        "withdraw": "withdraw",
                    }
                    if write and len(research_parts) == 6 and action in methods:
                        method = getattr(self.server.research_mathlib, methods[action])
                        return self.reply(200, method(project_id, self.body()))
                if len(research_parts) == 5:
                    if write and research_parts[4] == "run-plan":
                        return self.reply(
                            200, self.server.research_runs.prepare(project_id, self.body())
                        )
                    if research_parts[4] == "runs":
                        if write:
                            result = self.server.research_runs.submit(project_id, self.body())
                            if self.server.pump:
                                self.server.pump.wake.set()
                            return self.reply(201, result)
                        return self.reply(
                            200, {"runs": self.server.research_runs.list_runs(project_id)}
                        )
                if not write and len(research_parts) == 4:
                    return self.reply(200, self.server.research.report(project_id))
                actions = {
                    "edit": self.server.research.edit,
                    "fork": self.server.research.fork,
                    "prepare-binding": self.server.research.prepare_binding,
                    "bind": self.server.research.bind,
                    "withdraw": self.server.research.withdraw,
                }
                if write and len(research_parts) == 5 and research_parts[4] in actions:
                    return self.reply(200, actions[research_parts[4]](project_id, self.body()))
                if not write and len(research_parts) == 6 and research_parts[4] == "history":
                    return self.reply(
                        200, self.server.research.history(project_id, research_parts[5])
                    )
                if not write and len(research_parts) == 7 and research_parts[4] == "export":
                    return self.reply(
                        200,
                        self.server.research.export_node(
                            project_id, research_parts[5], research_parts[6]
                        ),
                    )
            evaluation_url = urlsplit(self.path)
            if not write and evaluation_url.path.startswith("/v1/number-theory-search-evaluations"):
                from .search_evaluation import evaluation_catalog, read_evaluation

                if evaluation_url.query:
                    raise HttpError(404, "Unknown search evaluation query")
                if evaluation_url.path == "/v1/number-theory-search-evaluations":
                    return self.reply(200, evaluation_catalog(self.server.root))
                match = re.fullmatch(
                    r"/v1/number-theory-search-evaluations/(ns-eval-[a-f0-9]{32})",
                    evaluation_url.path,
                )
                if match:
                    return self.reply(200, read_evaluation(self.server.root, match[1]))
                raise HttpError(404, "Unknown search evaluation route")
            if not write and evaluation_url.path.startswith("/v1/mathlib-evaluations"):
                from .mathlib_evaluation import evaluation_catalog, evaluation_plan, read_evaluation

                if evaluation_url.path == "/v1/mathlib-evaluations" and not evaluation_url.query:
                    return self.reply(200, evaluation_catalog(self.server.root))
                if evaluation_url.path == "/v1/mathlib-evaluations/plan":
                    query = parse_qs(evaluation_url.query, keep_blank_values=True)
                    if set(query) - {"split"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError("Invalid Mathlib evaluation query")
                    return self.reply(
                        200, evaluation_plan(self.server.root, query.get("split", ["all"])[0])
                    )
                match = re.fullmatch(
                    r"/v1/mathlib-evaluations/(ml-bench-[a-f0-9]{32})", evaluation_url.path
                )
                if match and not evaluation_url.query:
                    return self.reply(200, read_evaluation(self.server.root, match[1]))
                raise HttpError(404, "Unknown Mathlib evaluation route")
            if not write and self.path == "/v1/evaluations":
                return self.reply(200, self.server.evaluation_catalog())
            if not write and urlsplit(self.path).path == "/v1/knowledge/search":
                self.server.instances.reconcile()
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if (
                    set(query) - {"q", "include_inactive"}
                    or any(len(v) != 1 for v in query.values())
                    or query.get("include_inactive", ["false"])[0] not in ("true", "false")
                ):
                    raise ValueError("Invalid knowledge search query")
                return self.reply(
                    200,
                    self.server.knowledge.search(
                        query.get("q", [""])[0],
                        query.get("include_inactive", ["false"])[0] == "true",
                    ),
                )
            if write and self.path == "/v1/knowledge/prepare":
                self.server.instances.reconcile()
                data = exact_object(self.body(), {"review_id"}, "Knowledge source")
                return self.reply(200, self.server.knowledge.prepare(data["review_id"]))
            if write and self.path == "/v1/knowledge":
                self.server.instances.reconcile()
                return self.reply(201, self.server.knowledge.admit(self.body()))
            if not write and self.path in ("/v1/paper-examples", "/v1/paper-examples/integers"):
                example_folder = (
                    "evals/integer-review-v1"
                    if self.path.endswith("/integers")
                    else "evals/paper-review-v1"
                )
                return self.reply(
                    200,
                    {
                        "examples": [
                            {
                                "id": name,
                                "bundle": json.loads(
                                    (
                                        self.server.root / example_folder / (name + ".json")
                                    ).read_text(encoding="utf-8")
                                ),
                                "provenance": "repository-author-annotations-not-model-output",
                            }
                            for name in ("valid-proof", "true-claim-bad-proof", "missing-condition")
                        ]
                    },
                )
            if write and self.path == "/v1/paper-reviews/prepare":
                data = exact_object(self.body(), {"bundle"}, "Paper preview")
                return self.reply(200, prepare_review(data["bundle"]))
            if self.path == "/v1/paper-reviews":
                if write:
                    result = self.server.papers.submit(self.body())
                    if self.server.pump:
                        self.server.pump.wake.set()
                    return self.reply(202, result)
                return self.reply(200, {"reviews": self.server.papers.list_reviews()})
            parts = self.path.split("/")
            if len(parts) in (4, 5) and parts[:3] == ["", "v1", "knowledge"]:
                self.server.instances.reconcile()
                if not write and len(parts) == 4:
                    return self.reply(200, self.server.knowledge.report(parts[3]))
                if write and len(parts) == 5 and parts[4] in ("withdraw", "remove"):
                    result = self.server.knowledge.action(
                        parts[3], self.body(), delete=parts[4] == "remove"
                    )
                    self.server.instances.reconcile()
                    return self.reply(200, result)
            if len(parts) in (4, 5, 6) and parts[:3] == ["", "v1", "paper-reviews"]:
                review_id = parts[3]
                if not write and len(parts) == 4:
                    return self.reply(200, self.server.papers.report(review_id))
                if write and len(parts) == 5 and parts[4] in ("cancel", "revoke"):
                    exact_object(self.body(), set(), "Paper review action")
                    return self.reply(
                        200, self.server.papers.cancel(review_id, revoke=parts[4] == "revoke")
                    )
                if not write and len(parts) == 6 and parts[4] == "evidence":
                    return self.reply(200, self.server.papers.evidence(review_id, parts[5]))
            if not write and len(parts) == 4 and parts[:3] == ["", "v1", "evaluations"]:
                from .model_evaluation import read_model_evaluation

                if re.fullmatch(r"nat-plans-[a-f0-9]{32}", parts[3]) is None:
                    raise HttpError(404, "Unknown evaluation")
                directory = self.server.root / "artifacts/model-evaluations" / parts[3]
                report = directory / "report.json"
                if (
                    directory.parent.is_symlink()
                    or not directory.resolve().is_relative_to(
                        self.server.root / "artifacts/model-evaluations"
                    )
                    or directory.is_symlink()
                    or report.is_symlink()
                    or not report.is_file()
                    or report.stat().st_size > 1048576
                ):
                    raise HttpError(404, "Evaluation is unavailable")
                return self.reply(200, read_model_evaluation(self.server.root, directory))
            if len(parts) in (4, 5) and parts[:3] == ["", "v1", "ideas"]:
                idea_id = parts[3]
                if len(parts) == 4 and not write:
                    return self.reply(200, self.server.ideas.report(idea_id))
                if len(parts) == 5:
                    action = parts[4]
                    if not write and action == "evidence":
                        return self.reply(200, self.server.ideas.evidence(idea_id))
                    if write and action in ("generate", "confirm", "revise", "cancel", "revoke"):
                        data = self.body()
                        if action in ("generate", "cancel", "revoke"):
                            exact_object(data, set(), "Idea action")
                        if action == "generate":
                            result = self.server.ideas.generate(idea_id)
                        elif action == "confirm":
                            result = self.server.ideas.confirm(idea_id, data)
                        elif action == "revise":
                            result = self.server.ideas.revise(idea_id, data)
                        else:
                            result = self.server.ideas.cancel(idea_id, revoke=action == "revoke")
                        if self.server.pump:
                            self.server.pump.wake.set()
                        return self.reply(200, result)
            if len(parts) in (4, 5) and parts[:3] == ["", "v1", "tasks"]:
                task_id = parts[3]
                if len(parts) == 4 and not write:
                    return self.reply(200, self.server.store.report(task_id))
                if len(parts) == 5 and parts[4] == "evidence" and not write:
                    return self.reply(200, self.server.evidence(task_id))
                if len(parts) == 5 and write and parts[4] in ("cancel", "retry"):
                    data = self.body()
                    if parts[4] == "cancel":
                        exact_object(data, set(), "Cancel")
                        task = self.server.store.cancel(task_id)
                    else:
                        exact_object(data, {"attempt_id", "request_id"}, "Retry")
                        task = self.server.store.retry(
                            task_id, data["attempt_id"], data["request_id"]
                        )
                        if self.server.pump:
                            self.server.pump.wake.set()
                    return self.reply(200, task)
            raise HttpError(404, "Unknown workbench route")
        except HttpError as error:
            self.reply(error.status, {"error": error.message})
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            self.reply(400, {"error": str(error)})
        except WorkerBusy:
            self.reply(
                503,
                {
                    "code": "WORKER_BUSY",
                    "error": "本地证据正在核对，请稍后重新读取。",
                    "retryable_read": not write,
                },
            )
        except (OSError, RuntimeError, sqlite3.Error):
            self.reply(
                503,
                {
                    "error": "Local storage or worker unavailable; inspect the task or retry this request"
                },
            )

    def do_GET(self):
        self.handle_route()

    def do_POST(self):
        self.handle_route(write=True)


def serve(root: Path, port: int = 8765):
    server = WorkbenchServer(root, port)
    print(f"MRA local workbench: {server.origin}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
