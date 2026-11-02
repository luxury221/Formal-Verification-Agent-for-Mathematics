"""Real HTTP guards and persisted automatic jobs; no model or Lean proof fixtures."""

from pathlib import Path
import tempfile
import unittest

from mra.document_intake import DocumentPump
from document_fixtures import source_request
from mra.paper_protocol import DEFAULT_BUDGET
from test_http_api import ROOT, copy_web_inputs, request, running_server


class PaperHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="paper-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.context = running_server(self.root)
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def body(self):
        code, doc, _ = request(self.server, "POST", "/v1/documents", source_request())
        self.assertEqual(code, 202)
        return {
            "request_id": "paper-http-job",
            "document_id": doc["document_id"],
            "expected_document_revision": doc["revision"],
            "budget": DEFAULT_BUDGET,
        }

    def test_upload_to_pending_automation_and_source_navigation_assets(self):
        body = self.body()
        code, job, _ = request(self.server, "POST", "/v1/paper-automation", body)
        self.assertEqual(code, 202, job)
        self.assertEqual(job["state"], "WAITING_FOR_SOURCE")
        for route in ("/automation", "/automation.js", "/automation.css"):
            code, _, headers = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(
            request(self.server, "GET", "/v1/paper-automation/capabilities")[1]["model"][
                "configured"
            ],
            False,
        )
        self.assertEqual(
            request(self.server, "POST", "/v1/paper-automation", body)[1]["job_id"], job["job_id"]
        )
        pump = DocumentPump(self.server.documents)
        with pump.owner() as fd:
            pump.execute(body["document_id"], fd)
        key = job["job_id"]
        code, report, _ = request(self.server, "GET", "/v1/paper-automation/" + key + "/export")
        self.assertEqual(code, 200)
        self.assertFalse(report["whole_article_verified"])
        self.assertEqual(report["evidence_level"], "E0")

    def test_shared_guards_strict_schema_and_revision_actions(self):
        body = self.body()
        for headers in ({"Origin": "https://evil.example"}, {"X-MRA-CSRF": "invalid"}):
            self.assertEqual(
                request(self.server, "POST", "/v1/paper-automation", body, headers=headers)[0], 403
            )
        self.assertEqual(request(self.server, "GET", "/v1/paper-automation?ignored=1")[0], 400)
        self.assertEqual(
            request(self.server, "POST", "/v1/paper-automation", {**body, "confirmed": True})[0],
            400,
        )
        _, job, _ = request(self.server, "POST", "/v1/paper-automation", body)
        path = "/v1/paper-automation/" + job["job_id"] + "/cancel"
        self.assertEqual(
            request(self.server, "POST", path, {"request_id": "cancel", "expected_revision": 50})[
                0
            ],
            400,
        )
        self.assertEqual(
            request(
                self.server,
                "POST",
                path,
                {"request_id": "cancel", "expected_revision": job["revision"]},
            )[1]["state"],
            "CANCELLED",
        )
        self.assertNotEqual(
            request(self.server, "GET", "/v1/paper-automation/../../config/model.local.json")[0],
            200,
        )

    def test_only_registered_hash_matched_json_artifacts_are_readable(self):
        from urllib.parse import quote

        body = self.body()
        pump = DocumentPump(self.server.documents)
        with pump.owner() as fd:
            pump.execute(body["document_id"], fd)
        code, job, _ = request(self.server, "POST", "/v1/paper-automation", body)
        self.assertEqual(code, 202)
        base = "/v1/paper-automation/" + job["job_id"] + "/artifacts/"
        code, source, _ = request(self.server, "GET", base + quote("source.json", safe=""))
        self.assertEqual(code, 200)
        self.assertEqual(source["document_id"], body["document_id"])
        self.assertEqual(
            request(self.server, "GET", base + quote("../inputs.json", safe=""))[0], 400
        )
        self.assertEqual(
            request(self.server, "GET", base + quote("unregistered.json", safe=""))[0], 400
        )
