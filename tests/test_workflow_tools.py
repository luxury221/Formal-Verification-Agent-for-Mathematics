"""Real store/process/HTTP workflow contracts; no model outputs or Lean results."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mra import core_tools, paper_automation, workflows
from mra.core_tools import CoreToolAPI, SCHEMA
from mra.document_intake import DocumentPump, DocumentStore
from mra.paper_automation import PaperAutomationStore
from mra.paper_pipeline import PaperPump
from mra.paper_protocol import DEFAULT_BUDGET, SCHEMA as LEGACY_SCHEMA
from mra.research_flow import atomic
from mra.verifier import NativeLeanBackend
from document_fixtures import source_request
from test_http_api import ROOT, copy_web_inputs, request, running_server


def call(tool, **arguments):
    return {"schema": SCHEMA, "call_id": "workflow-contract", "tool": tool, "arguments": arguments}


class WorkflowToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="workflow-tools-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reader = CoreToolAPI(self.root)
        self.writer = CoreToolAPI(self.root, allow_mutations=True)
        guard = patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")
        )
        guard.start()
        self.addCleanup(guard.stop)

    def invoke(self, tool, *, write=False, **arguments):
        reply = (self.writer if write else self.reader).call(call(tool, **arguments))
        self.assertEqual(reply["call_status"], "RETURNED", reply)
        return reply

    def document(self, extract=False):
        reply = self.invoke("document.import", write=True, request=source_request())
        key = reply["receipt"]["document_id"]
        if extract:
            pump = DocumentPump(DocumentStore(self.root))
            with pump.owner() as fd:
                pump.execute(key, fd)
            self.assertEqual(
                self.invoke("document.status", document_id=key)["result"]["state"], "READY"
            )
        return key

    def paper(self, extract=False):
        doc = self.document(extract)
        body = {
            "request_id": "automatic-workflow",
            "document_id": doc,
            "expected_document_revision": 0,
            "budget": deepcopy(DEFAULT_BUDGET),
        }
        return self.invoke("paper.create", write=True, request=body), body

    def wait_for_model(self, key):
        pump = PaperPump(PaperAutomationStore(self.root))
        with pump.owner() as fd:
            pump.execute(key, fd)
        result = self.invoke("paper.status", job_id=key)["result"]
        self.assertEqual(result["state"], "WAITING_FOR_MODEL")
        self.assertEqual(result["used"]["model_calls"], 0)
        return result

    def test_discovery_never_creates_storage_and_reports_implemented_limits(self):
        for tool in (
            "catalog",
            "workflow.capabilities",
            "document.capabilities",
            "paper.capabilities",
        ):
            self.invoke(tool)
            self.assertEqual(list(self.root.iterdir()), [], tool)
        value = self.invoke("workflow.capabilities")["result"]
        self.assertEqual(value["schema"], "product-capabilities-v1")
        self.assertFalse(value["documents"]["capabilities"]["pdf"])
        self.assertFalse(value["workflows"]["paper"]["capabilities"]["model"]["configured"])
        self.assertEqual(value["workers"]["paper"]["state"], "NOT_OBSERVED")
        names = set(value["documents"]["tools"])
        for workflow in value["workflows"].values():
            names.update(workflow["tools"])
        self.assertEqual(names | {"catalog", "workflow.capabilities"}, set(core_tools.TOOLS))
        self.assertFalse(value["authority"]["tool_call_success_is_proof"])
        value["workflows"]["paper"]["capabilities"]["default_budget"]["model_calls"] = 0
        self.assertEqual(
            self.invoke("paper.capabilities")["result"]["default_budget"], DEFAULT_BUDGET
        )

    def test_presence_hints_and_invalid_model_configuration_are_not_attestation(self):
        executable = self.root / ".runtime/document-ingestion-v1/python/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text("inert file, not an executable")
        config = self.root / "config/model.local.json"
        config.parent.mkdir()
        config.write_text("invalid json")
        value = self.invoke("workflow.capabilities")["result"]
        self.assertTrue(value["documents"]["capabilities"]["pdf"])
        self.assertEqual(value["runtime_assurance"], "CONFIGURATION_AND_FILE_PRESENCE_ONLY")
        self.assertEqual(
            value["workflows"]["paper"]["capabilities"]["model"]["reason"], "INVALID_MODEL_CONFIG"
        )

    def test_all_mutations_reject_default_access_before_dispatch(self):
        with patch.object(self.reader, "_dispatch", side_effect=AssertionError("No dispatch")):
            for tool, (fields, mutates) in core_tools.TOOLS.items():
                if mutates:
                    reply = self.reader.call(
                        call(tool, **{field: "placeholder" for field in fields})
                    )
                    self.assertEqual(
                        (reply["call_status"], reply["operation_outcome"]),
                        ("REJECTED", "NOT_STARTED"),
                        tool,
                    )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_import_is_idempotent_enqueues_only_and_cancellation_is_repeatable(self):
        key = self.document()
        self.assertEqual(self.document(), key)
        docs = self.invoke("document.list")["result"]["documents"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["state"], "QUEUED")
        changed = source_request(title="different input under same request")
        reply = self.writer.call(call("document.import", request=changed))
        self.assertEqual(reply["operation_outcome"], "UNKNOWN")
        self.assertEqual(reply["receipt"]["request_id"], changed["request_id"])
        for _ in range(2):
            value = self.invoke("document.cancel", write=True, document_id=key)["result"]
            self.assertEqual(value["state"], "CANCELLED")
            self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(len(self.invoke("document.list")["result"]["documents"]), 1)

    def test_input_size_is_not_silently_truncated_or_relaxed_for_import(self):
        raw = source_request(
            sources=[{"name": "large.html", "content": "x" * core_tools.MAX_INPUT_BYTES}]
        )
        reply = self.writer.call(call("document.import", request=raw))
        self.assertEqual(reply["operation_outcome"], "NOT_STARTED")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_real_source_extraction_then_draft_shares_the_original_store(self):
        key = self.document(extract=True)
        draft = self.invoke("document.draft", document_id=key)["result"]
        self.assertEqual(draft, DocumentStore(self.root).draft(key))
        self.assertTrue(draft["blocks"])
        self.assertTrue(all(b["evidence_level"] == "E0" for b in draft["blocks"]))

    def test_paper_queue_waits_for_source_then_external_worker_waits_for_model(self):
        first, body = self.paper()
        key = first["receipt"]["job_id"]
        self.assertEqual(first["result"]["state"], "WAITING_FOR_SOURCE")
        again = self.invoke("paper.create", write=True, request=body)
        self.assertEqual(again["receipt"]["job_id"], key)
        self.assertEqual(first["receipt"]["document_id"], body["document_id"])
        self.assertEqual(len(self.invoke("paper.list")["result"]["jobs"]), 1)
        pump = DocumentPump(DocumentStore(self.root))
        with pump.owner() as fd:
            pump.execute(body["document_id"], fd)
        row = self.wait_for_model(key)
        self.assertFalse(row["whole_article_verified"])
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))

    def test_cancel_preserves_action_receipt_revision_and_original_budget(self):
        first, body = self.paper()
        key, revision = first["receipt"]["job_id"], first["result"]["revision"]
        stale = self.writer.call(
            call(
                "paper.cancel",
                job_id=key,
                request={"request_id": "cancel", "expected_revision": revision + 1},
            )
        )
        self.assertEqual(stale["call_status"], "FAILED")
        action = {"request_id": "cancel", "expected_revision": revision}
        result = self.invoke("paper.cancel", write=True, job_id=key, request=action)
        replay = self.invoke("paper.cancel", write=True, job_id=key, request=action)
        self.assertEqual(result["receipt"]["request_id"], "cancel")
        self.assertEqual(replay["result"]["revision"], result["result"]["revision"])
        self.assertEqual(result["result"]["state"], "CANCELLED")
        self.assertEqual(result["result"]["budget"], body["budget"])
        action = {
            "request_id": "resume-cancelled",
            "expected_revision": result["result"]["revision"],
        }
        self.assertEqual(
            self.writer.call(call("paper.resume", job_id=key, request=action))["call_status"],
            "FAILED",
        )

    def test_resume_reuses_job_and_budget_but_does_not_execute_work(self):
        first, body = self.paper(extract=True)
        key = first["receipt"]["job_id"]
        row = self.wait_for_model(key)
        action = {"request_id": "resume-model", "expected_revision": row["revision"]}
        resumed = self.invoke("paper.resume", write=True, job_id=key, request=action)
        replay = self.invoke("paper.resume", write=True, job_id=key, request=action)
        self.assertEqual(resumed["receipt"]["request_id"], "resume-model")
        self.assertEqual(resumed["result"]["state"], "QUEUED")
        self.assertEqual(replay["result"], resumed["result"])
        self.assertEqual(resumed["result"]["used"], row["used"])
        self.assertEqual(resumed["result"]["budget"], body["budget"])
        bad = {**action, "expected_revision": row["revision"] + 1}
        self.assertEqual(
            self.writer.call(call("paper.resume", job_id=key, request=bad))["call_status"], "FAILED"
        )

    def test_changed_implementation_or_legacy_job_cannot_be_resumed(self):
        first, _ = self.paper(extract=True)
        key = first["receipt"]["job_id"]
        row = self.wait_for_model(key)
        action = {"request_id": "resume-stale", "expected_revision": row["revision"]}
        current = paper_automation.implementation()
        with patch.object(
            paper_automation, "implementation", return_value={**current, "new-source": "changed"}
        ):
            self.assertTrue(self.invoke("paper.status", job_id=key)["result"]["stale"])
            self.assertEqual(
                self.writer.call(call("paper.resume", job_id=key, request=action))["call_status"],
                "FAILED",
            )
        store = PaperAutomationStore(self.root)
        historical = store.read(key)
        self.assertNotIn(action["request_id"], historical["actions"])
        historical["schema"] = LEGACY_SCHEMA
        atomic(store.directory(key) / "record.json", historical)
        self.assertTrue(self.invoke("paper.status", job_id=key)["result"]["legacy_job"])
        self.assertEqual(
            self.writer.call(call("paper.resume", job_id=key, request=action))["call_status"],
            "FAILED",
        )

    def test_source_revision_change_invalidates_resume_without_rebinding_job(self):
        first, body = self.paper(extract=True)
        key = first["receipt"]["job_id"]
        row = self.wait_for_model(key)
        documents = DocumentStore(self.root)
        block = documents.draft(body["document_id"])["blocks"][0]
        documents.review(
            body["document_id"],
            {
                "request_id": "change-extraction",
                "expected_revision": 0,
                "block_id": block["id"],
                "text": block["text"] + " Changed source.",
                "kind": block["kind"],
                "note": "Source edit contract",
                "reviewed": False,
            },
        )
        result = self.invoke("paper.status", job_id=key)["result"]
        self.assertTrue(result["stale"])
        action = {"request_id": "resume-revised", "expected_revision": row["revision"]}
        self.assertEqual(
            self.writer.call(call("paper.resume", job_id=key, request=action))["call_status"],
            "FAILED",
        )
        self.assertEqual(PaperAutomationStore(self.root).read(key)["document_revision"], 0)

    def test_artifacts_require_registered_names_and_matching_bytes(self):
        first, _ = self.paper(extract=True)
        key = first["receipt"]["job_id"]
        value = self.invoke("paper.artifact", job_id=key, name="source.json")["result"]
        self.assertTrue(value["blocks"])
        for name in ("../inputs.json", "unregistered.json", ["source.json"]):
            self.assertEqual(
                self.reader.call(call("paper.artifact", job_id=key, name=name))["call_status"],
                "FAILED",
            )
        target = PaperAutomationStore(self.root).directory(key) / "source.json"
        target.write_bytes(target.read_bytes() + b" ")
        self.assertEqual(
            self.reader.call(call("paper.artifact", job_id=key, name="source.json"))["call_status"],
            "FAILED",
        )
        self.assertTrue(self.invoke("paper.status", job_id=key)["result"]["stale"])

    def test_result_limit_keeps_document_and_job_receipts_and_replay_does_not_duplicate(self):
        for tool in ("document.import", "paper.create"):
            if tool == "document.import":
                body, identity = source_request(), "document_id"
            else:
                doc = self.invoke("document.list")["result"]["documents"][0]
                body, identity = (
                    {
                        "request_id": "limited-job",
                        "document_id": doc["document_id"],
                        "expected_document_revision": 0,
                        "budget": deepcopy(DEFAULT_BUDGET),
                    },
                    "job_id",
                )
            with patch.object(core_tools, "MAX_OUTPUT_BYTES", 256):
                limited = self.writer.call(call(tool, request=body))
            self.assertEqual(limited["error"]["code"], "RESULT_LIMIT")
            self.assertEqual(limited["operation_outcome"], "RETURNED")
            self.assertIsNone(limited["result"])
            replay = self.invoke(tool, write=True, request=body)
            self.assertEqual(replay["receipt"][identity], limited["receipt"][identity])
            self.assertEqual(limited["receipt"]["request_id"], body["request_id"])
        self.assertEqual(len(self.invoke("document.list")["result"]["documents"]), 1)
        self.assertEqual(len(self.invoke("paper.list")["result"]["jobs"]), 1)

    def test_failure_after_import_commit_has_uncertain_outcome_and_exact_replay_recovers(self):
        original = self.writer._dispatch

        def commit_then_fail(tool, arguments):
            original(tool, arguments)
            raise OSError("Simulated output failure after actual store commit")

        with patch.object(self.writer, "_dispatch", side_effect=commit_then_fail):
            reply = self.writer.call(call("document.import", request=source_request()))
        self.assertEqual(reply["operation_outcome"], "UNKNOWN")
        self.assertEqual(reply["receipt"]["request_id"], source_request()["request_id"])
        self.document()
        self.assertEqual(len(self.invoke("document.list")["result"]["documents"]), 1)

    def test_new_tools_reject_redirected_storage_and_path_identifiers(self):
        for tool, field in (("document.status", "document_id"), ("paper.status", "job_id")):
            reply = self.reader.call(call(tool, **{field: "../config/model.local.json"}))
            self.assertEqual(reply["call_status"], "FAILED")
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "artifacts/document-intake").rmdir()
        (self.root / "artifacts/document-intake").symlink_to(outside, target_is_directory=True)
        self.assertEqual(
            self.writer.call(call("document.import", request=source_request()))["call_status"],
            "FAILED",
        )
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_restart_observes_the_same_queued_document(self):
        def cli(data, write=False):
            command = [
                sys.executable,
                "-c",
                "import sys; from mra.core_tools import main; raise SystemExit(main(sys.argv.pop(1)))",
                str(self.root),
            ]
            if write:
                command.append("--allow-mutations")
            process = subprocess.run(
                command, input=core_tools.encode(data), capture_output=True, cwd=ROOT, timeout=15
            )
            self.assertEqual(process.returncode, 0, process.stderr.decode())
            return json.loads(process.stdout)

        first = cli(call("document.import", request=source_request()), write=True)
        key = first["receipt"]["document_id"]
        status = cli(call("document.status", document_id=key))
        self.assertEqual(status["result"]["document_id"], key)
        self.assertEqual(status["result"]["state"], "QUEUED")


class WorkflowHttpTests(unittest.TestCase):
    def test_shared_discovery_uses_real_http_guards_and_domain_capabilities(self):
        with tempfile.TemporaryDirectory(
            dir=ROOT / "artifacts", prefix="workflow-http-"
        ) as directory:
            root = Path(directory)
            copy_web_inputs(root)
            with running_server(root) as server:
                code, value, headers = request(server, "GET", "/v1/capabilities")
                self.assertEqual(code, 200)
                expected = workflows.capabilities(root)
                expected["workers"] = {
                    name: {"state": "EXTERNAL", "error": None} for name in ("document", "paper")
                }
                self.assertEqual(value, expected)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(
                    request(server, "GET", "/v1/documents/capabilities")[1],
                    value["documents"]["capabilities"],
                )
                paper = request(server, "GET", "/v1/paper-automation/capabilities")[1]
                paper.pop("worker")
                self.assertEqual(paper, value["workflows"]["paper"]["capabilities"])
                self.assertEqual(
                    request(
                        server,
                        "GET",
                        "/v1/capabilities",
                        headers={"Origin": "https://evil.example"},
                    )[0],
                    403,
                )
                self.assertEqual(
                    request(server, "GET", "/v1/capabilities", headers={"Host": "evil.example"})[0],
                    403,
                )
                self.assertEqual(request(server, "GET", "/v1/capabilities?ignored=1")[0], 400)
                self.assertEqual(request(server, "POST", "/v1/capabilities", {})[0], 400)
