"""Automatic orchestration tests; model outputs below are explicit software fixtures."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from mra.document_intake import DocumentPump, DocumentStore
from mra.domain import digest
from mra.paper_automation import PaperAutomationStore
from mra.paper_pipeline import PaperPump
from mra.paper_protocol import DEFAULT_BUDGET
from mra.paper_protocol import chunks, scan_input
from mra.research_flow import atomic
from mra.tasks import WorkerBusy
from document_fixtures import source_request
from paper_fixtures import CONFIG, HTML, FixtureService, unavailable_kernel

ROOT = Path(__file__).resolve().parents[1]


class AutomaticPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="paper-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = DocumentStore(self.root)
        self.store = PaperAutomationStore(self.root, documents=self.documents)
        FixtureService.seen = []
        self.pump = PaperPump(
            self.store, service_factory=FixtureService, kernel_executor=unavailable_kernel
        )

    def document(self, extract=True):
        doc = self.documents.create(
            source_request(sources=[{"name": "owned.html", "content": HTML}])
        )
        if extract:
            p = DocumentPump(self.documents)
            with p.owner() as fd:
                p.execute(doc["document_id"], fd)
        return doc["document_id"]

    def job(self, extract=True, **limits):
        doc = self.document(extract)
        return self.store.create(
            {
                "request_id": "auto-test",
                "document_id": doc,
                "expected_document_revision": 0,
                "budget": {**DEFAULT_BUDGET, **limits},
            }
        )

    def configure(self):
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config/model.local.json").write_text(json.dumps(CONFIG))

    def execute(self, key):
        with self.pump.owner() as fd:
            self.pump.execute(key, fd)
        return self.store.report(key)

    def test_source_wait_and_model_wait_never_invent_readings(self):
        row = self.job(extract=False)
        key = row["job_id"]
        self.assertEqual(self.execute(key)["state"], "WAITING_FOR_SOURCE")
        p = DocumentPump(self.documents)
        with p.owner() as fd:
            p.execute(row["document_id"], fd)
        result = self.execute(key)
        self.assertEqual(result["state"], "WAITING_FOR_MODEL")
        self.assertEqual(result["used"]["model_calls"], 0)
        self.assertTrue(result["coverage"])
        self.assertTrue(all(not c["reading_accepted"] for c in result["coverage"]))
        self.assertFalse(result["whole_article_verified"])

    def test_creation_waits_for_extractor_metadata_commit(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from mra.research_flow import lease

        doc = self.document()
        raw = {
            "request_id": "concurrent-create",
            "document_id": doc,
            "expected_document_revision": 0,
            "budget": DEFAULT_BUDGET,
        }
        started = threading.Event()

        def create():
            started.set()
            return self.store.create(raw)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with lease(self.documents.directory(doc)):
                future = pool.submit(create)
                self.assertTrue(started.wait(2))
                time.sleep(0.1)
                self.assertFalse(
                    future.done(), "A short extraction commit must not make creation fail"
                )
            self.assertEqual(future.result(timeout=5)["state"], "QUEUED")

    def test_restarted_worker_waits_for_old_child_lease_then_takes_over(self):
        row = self.job()
        replacement = PaperPump(self.store)
        try:
            with self.pump.owner():
                replacement.thread.start()
                end = time.monotonic() + 3
                while replacement.state != "WAITING_FOR_OWNER" and time.monotonic() < end:
                    time.sleep(0.01)
                self.assertEqual(replacement.state, "WAITING_FOR_OWNER")
                self.assertTrue(replacement.thread.is_alive())
            end = time.monotonic() + 5
            while (
                self.store.read(row["job_id"])["state"] != "WAITING_FOR_MODEL"
                and time.monotonic() < end
            ):
                time.sleep(0.02)
            self.assertEqual(self.store.read(row["job_id"])["state"], "WAITING_FOR_MODEL")
            self.assertEqual(self.store.read(row["job_id"])["used"]["model_calls"], 0)
            self.assertIsNone(replacement.error)
        finally:
            replacement.close()

    def test_complete_fixture_orchestration_keeps_kernel_failure_unknown(self):
        self.configure()
        row = self.job(repair_attempts=1)
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS", result["error"])
        self.assertGreaterEqual(result["summary"]["obligations"], 2)
        self.assertEqual(
            result["summary"]["reading_units"], result["summary"]["reading_units_accepted"]
        )
        self.assertEqual(result["summary"]["kernel_candidates_checked"], 0)
        self.assertTrue(all(i["status"] == "GAP" for i in result["items"]))
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertIsNone(result["human_confirmation"])
        self.assertFalse(list((self.root / "artifacts/document-obligations").glob("*")))
        self.assertTrue(any(t == "paper_formalize" for t, _ in FixtureService.seen))
        # Original full target is identical throughout all repair attempts.
        originals = [
            p["obligation"]["statement"] for t, p in FixtureService.seen if t == "paper_formalize"
        ]
        self.assertEqual(len(set(originals)), 1)
        for name in result["artifacts"]:
            if name.endswith("-response.json"):
                response = self.store.artifact(self.store.read(row["job_id"]), name)
                self.assertEqual(response["identity"]["kind"], "fixture")

    def test_budget_reserved_before_call_stops_without_hidden_overrun(self):
        self.configure()
        row = self.job(model_calls=1)
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["used"]["model_calls"], 1)
        self.assertEqual(len(FixtureService.seen), 1)
        self.assertEqual(result["used"]["output_tokens_reserved"], 8192)
        self.assertEqual(result["summary"]["kernel_candidates_checked"], 0)

    def test_strict_bad_quote_is_repaired_using_real_protocol_feedback(self):
        class BadQuoteOnce(FixtureService):
            def generate(inner, request):
                response = super(BadQuoteOnce, inner).generate(request)
                output = json.loads(response.output_json)
                if request.task == "paper_scan" and "read-0-1" in request.id:
                    output["obligations"][0]["quotes"][0]["quote"] = "fabricated"
                    from dataclasses import replace

                    response = replace(response, output_json=json.dumps(output))
                return response

        self.configure()
        row = self.job(repair_attempts=1)
        self.pump.service_factory = BadQuoteOnce
        result = self.execute(row["job_id"])
        self.assertEqual(result["chunks"][0]["attempts"], 2)
        self.assertEqual(result["chunks"][0]["status"], "ACCEPTED")
        feedback = next(
            p["feedback"] for t, p in BadQuoteOnce.seen if t == "paper_scan" and p.get("feedback")
        )
        self.assertIn("Quotation", feedback)

    def test_cancel_is_idempotent_and_stale_action_rejected(self):
        row = self.job()
        raw = {"request_id": "cancel", "expected_revision": row["revision"]}
        first = self.store.action(row["job_id"], "cancel", raw)
        self.assertEqual(first["state"], "CANCELLED")
        self.assertEqual(self.store.action(row["job_id"], "cancel", raw)["state"], "CANCELLED")
        with self.assertRaises(ValueError):
            self.store.action(
                row["job_id"], "cancel", {"request_id": "other", "expected_revision": 0}
            )
        self.assertEqual(self.execute(row["job_id"])["used"]["model_calls"], 0)

    def test_document_change_and_artifact_damage_suppress_current_results(self):
        row = self.job()
        key = row["job_id"]
        source = self.store.source(self.store.read(key))
        source["blocks"][0]["text"] += "damaged"
        atomic(self.store.directory(key) / "source.json", source)
        self.assertTrue(self.store.report(key)["stale"])
        # The envelope is internally consistent, but the pinned artifact bytes differ.
        self.assertEqual(self.store.report(key)["summary"]["kernel_candidates_checked"], 0)

    def test_config_drift_blocks_resume_and_no_automatic_call(self):
        self.configure()
        row = self.job(model_calls=1)
        self.execute(row["job_id"])
        with self.store.edit(row["job_id"]) as current:
            current["state"] = "MODEL_ERROR"
        altered = {**CONFIG, "revision": "fixture-2"}
        (self.root / "config/model.local.json").write_text(json.dumps(altered))
        current = self.store.read(row["job_id"])
        self.store.action(
            row["job_id"],
            "resume",
            {"request_id": "resume", "expected_revision": current["revision"]},
        )
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.execute(row["job_id"])
        self.assertEqual(len(FixtureService.seen), 1)

    def test_queue_lease_and_recovery_keep_uncertain_charge(self):
        row = self.job()
        key = row["job_id"]
        with self.store.edit(key) as current:
            current.update(
                state="RUNNING", active={"kind": "MODEL", "step": "read-0-1", "chunk": 0}
            )
            current["chunks"][0].update(status="WORKING", attempts=1)
            current["used"].update(model_calls=1, output_tokens_reserved=8192)
            current["calls"]["read-0-1"] = {
                "status": "STARTED",
                "request_fingerprint": digest("uncertain"),
            }
        with self.pump.owner():
            with self.assertRaises(WorkerBusy):
                with PaperPump(self.store).owner():
                    pass
            self.pump.recover()
        result = self.store.report(key)
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertEqual(result["calls"]["read-0-1"]["status"], "INTERRUPTED_COST_UNKNOWN")
        self.assertEqual(result["used"]["output_tokens_reserved"], 8192)
        self.assertEqual(len(FixtureService.seen), 0)

    def test_transport_failure_pauses_without_automatic_provider_retry(self):
        class Broken(FixtureService):
            def generate(inner, request):
                raise OSError("network unavailable")

        self.configure()
        row = self.job()
        self.pump.service_factory = Broken
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "MODEL_ERROR")
        self.assertEqual(result["used"]["model_calls"], 1)
        self.assertEqual(result["calls"]["read-0-1"]["status"], "INTERRUPTED_COST_UNKNOWN")
        self.execute(row["job_id"])
        self.assertEqual(self.store.read(row["job_id"])["used"]["model_calls"], 1)

    def test_completed_call_checkpoint_is_reused_after_explicit_resume(self):
        class Unsupported(FixtureService):
            unsupported = True

        self.configure()
        row = self.job()
        key = row["job_id"]
        self.pump.service_factory = Unsupported
        with self.store.edit(key) as current:
            from mra.chat_model import ModelConfig

            current.update(state="RUNNING", model=ModelConfig.load(self.root).public())
            current["chunks"][0].update(status="WORKING", attempts=1)
        self.pump.deadline = time.monotonic() + 30
        source = self.store.source(self.store.read(key))
        packet = {
            "source_fingerprint": row["source_fingerprint"],
            **scan_input(source, chunks(source)[0]),
            "feedback": None,
        }
        with self.pump.owner() as fd:
            self.pump.call(key, "read-0-1", "paper_scan", packet, fd, {"chunk": 0})
            self.pump.recover()
        pending = self.store.read(key)
        self.store.action(
            key, "resume", {"request_id": "resume-cached", "expected_revision": pending["revision"]}
        )
        result = self.execute(key)
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS")
        self.assertEqual(sum(t == "paper_scan" for t, _ in Unsupported.seen), 1)
        self.assertEqual(result["chunks"][0]["attempts"], 1)

    def test_actual_document_revision_change_invalidates_pinned_source(self):
        row = self.job()
        block = self.documents.draft(row["document_id"])["blocks"][0]
        self.documents.review(
            row["document_id"],
            {
                "request_id": "source-edit",
                "expected_revision": 0,
                "block_id": block["id"],
                "text": block["text"] + " corrected",
                "kind": block["kind"],
                "note": "Owned test edit",
                "reviewed": False,
            },
        )
        result = self.store.report(row["job_id"])
        self.assertTrue(result["stale"])
        self.assertIsNone(result["source"])
        self.assertEqual(result["used"]["model_calls"], 0)

    def test_kernel_interrupt_retains_budget_and_requires_explicit_resume(self):
        self.configure()
        row = self.job()

        def interrupted(*_args, **_kwargs):
            raise OSError("Owned checker crash fixture")

        self.pump.kernel_executor = interrupted
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "EXECUTION_ERROR")
        self.assertEqual(result["used"]["kernel_seconds_reserved"], 30)
        self.assertEqual(result["summary"]["kernel_candidates_checked"], 0)

    def test_context_limit_stops_instead_of_spending_another_formal_call(self):
        from dataclasses import replace

        self.configure()
        html = HTML.replace(
            "</article>",
            "".join("<p>Additional context " + str(i) + "</p>" for i in range(30)) + "</article>",
        )
        doc = self.documents.create(
            source_request(sources=[{"name": "owned.html", "content": html}])
        )
        p = DocumentPump(self.documents)
        with p.owner() as fd:
            p.execute(doc["document_id"], fd)
        row = self.store.create(
            {
                "request_id": "context-budget",
                "document_id": doc["document_id"],
                "expected_document_revision": 0,
                "budget": DEFAULT_BUDGET,
            }
        )
        ids = [b["id"] for b in row["source"]["blocks"]]
        self.assertGreater(len(ids), 26, "Context fixtures must be inside the extracted article")

        class NeedsContext(FixtureService):
            def generate(inner, request):
                response = super(NeedsContext, inner).generate(request)
                if request.task == "paper_formalize":
                    output = json.loads(response.output_json)
                    output.update(
                        status="NEEDS_CONTEXT",
                        profile=None,
                        request=None,
                        questions=["Complete context requested"],
                        required_context=ids[:24] if request.id.endswith("-1") else ids[-2:],
                    )
                    return replace(response, output_json=json.dumps(output))
                return response

        self.pump.service_factory = NeedsContext
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS")
        self.assertEqual(sum(task == "paper_formalize" for task, _ in NeedsContext.seen), 4)
        self.assertTrue(all(i["status"] == "GAP" and i["attempts"] == 2 for i in result["items"]))

    def test_model_audit_rejection_does_not_reach_kernel(self):
        class Reject(FixtureService):
            def generate(inner, request):
                response = super(Reject, inner).generate(request)
                if request.task == "paper_audit":
                    from dataclasses import replace

                    output = json.loads(response.output_json)
                    output.update(decision="REJECT", issues=["Original scope is missing"])
                    output["checks"]["quantifiers"] = False
                    response = replace(response, output_json=json.dumps(output))
                return response

        self.configure()
        row = self.job(repair_attempts=1)
        self.pump.service_factory = Reject
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS")
        self.assertEqual(result["summary"]["obligations"], 0)
        self.assertEqual(result["used"]["kernel_seconds_reserved"], 0)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1",
    "Requires actual sealed Lean and namespace sandbox; fixtures are not proof evidence",
)
class PaperRealLeanTests(unittest.TestCase):
    def test_actual_sandbox_cancellation_reaps_owned_processes(self):
        import uuid
        from mra import paper_kernel
        from paper_fixtures import candidate, content

        directory = ROOT / "artifacts" / ("paper-cancel-real-" + uuid.uuid4().hex)
        directory.mkdir()
        store = PaperAutomationStore(directory, runtime_root=ROOT)
        pump = PaperPump(store)
        checked = directory / "check"
        value = paper_kernel.compile_candidate("typed", candidate(), content())
        pids = []

        def cancel():
            control = checked / "worker-pids.json"
            if control.exists():
                pids.extend(json.loads(control.read_text()))
                return True
            return False

        with pump.owner() as fd:
            with self.assertRaises(InterruptedError):
                paper_kernel.execute(
                    ROOT, checked, value, cancel_requested=cancel, lifecycle_lock_fd=fd
                )
        self.assertTrue(pids, "Cancellation must happen after a real sandbox was launched")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            paper_kernel._identity(p["pid"]) == p["start"] for p in pids
        ):
            time.sleep(0.02)
        self.assertTrue(all(paper_kernel._identity(p["pid"]) != p["start"] for p in pids))
        from mra.tasks import TaskStore

        with TaskStore(ROOT).worker_lock():
            pass
        atomic(
            directory / "cancellation.json",
            {
                "cancelled_after_real_launch": True,
                "owned_processes": pids,
                "all_reaped": True,
                "mathematical_result": "UNKNOWN",
            },
        )

    def test_owned_full_pipeline_with_scripted_model_and_actual_kernel(self):
        import uuid

        directory = ROOT / "artifacts" / ("paper-real-" + uuid.uuid4().hex)
        directory.mkdir()
        (directory / "config").mkdir()
        (directory / "config/model.local.json").write_text(json.dumps(CONFIG))
        docs = DocumentStore(directory, runtime_root=ROOT)
        doc = docs.create(source_request(sources=[{"name": "owned.html", "content": HTML}]))
        p = DocumentPump(docs)
        with p.owner() as fd:
            p.execute(doc["document_id"], fd)
        store = PaperAutomationStore(directory, documents=docs, runtime_root=ROOT)
        result = store.create(
            {
                "request_id": "real-owned-pipeline",
                "document_id": doc["document_id"],
                "expected_document_revision": 0,
                "budget": DEFAULT_BUDGET,
            }
        )
        pump = PaperPump(store, service_factory=FixtureService)
        with pump.owner() as fd:
            pump.execute(result["job_id"], fd)
        report = store.report(result["job_id"])
        atomic(directory / "acceptance.json", report)
        print("REAL_PAPER_REPORT=" + str(directory / "acceptance.json"), flush=True)
        self.assertEqual(report["state"], "COMPLETED", report["error"])
        self.assertGreaterEqual(report["summary"]["kernel_candidates_checked"], 2, report["items"])
        for item in report["items"]:
            phases = item["kernel_receipt"]["phases"]
            self.assertNotEqual(phases["candidate"]["pid"], phases["replay"]["pid"])
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertFalse(report["whole_article_verified"])
        self.assertIsNone(report["human_confirmation"])
