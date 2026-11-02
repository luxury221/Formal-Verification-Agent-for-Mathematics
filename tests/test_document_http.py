"""Real loopback requests and actual source-only extraction; no Lean/model calls."""

from pathlib import Path
import tempfile
import time
import unittest

from mra.document_intake import DocumentPump
from document_fixtures import source_request
from test_http_api import ROOT, copy_web_inputs, request, running_server


class DocumentHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="document-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.context = running_server(self.root)
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def create(self):
        code, row, _ = request(self.server, "POST", "/v1/documents", source_request())
        self.assertEqual(code, 202, row)
        return row["document_id"]

    def complete(self):
        key = self.create()
        pump = DocumentPump(self.server.documents)
        with pump.owner() as fd:
            pump.execute(key, fd)
        return key

    def test_new_workspace_assets_and_existing_html_import_both_available(self):
        for route in ("/documents", "/documents.js", "/documents.css", "/articles"):
            code, raw, headers = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        _, html, _ = request(self.server, "GET", "/articles")
        self.assertIn(b"/documents", html)
        self.assertIn(b'id="ar-import-toggle"', html)
        _, session, _ = request(self.server, "GET", "/v1/session")
        self.assertEqual(session["document_worker"]["state"], "EXTERNAL")

    def test_import_metadata_cancel_and_source_bytes_roundtrip(self):
        key = self.create()
        base = "/v1/documents/" + key
        code, row, _ = request(self.server, "GET", base)
        self.assertEqual(code, 200)
        self.assertEqual(row["state"], "QUEUED")
        code, source, _ = request(self.server, "GET", base + "/sources/0")
        self.assertEqual(code, 200)
        self.assertEqual(source["content"], source_request()["sources"][0]["content"])
        code, row, _ = request(self.server, "POST", base + "/cancel", {})
        self.assertEqual(code, 200)
        self.assertEqual(row["state"], "CANCELLED")
        self.assertEqual(request(self.server, "GET", base + "/draft")[0], 400)

    def test_guards_queries_path_traversal_and_strict_bodies_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"X-MRA-CSRF": "incorrect"}):
            self.assertEqual(
                request(self.server, "POST", "/v1/documents", source_request(), headers=headers)[0],
                403,
            )
        self.assertEqual(request(self.server, "GET", "/v1/documents?query=ignored")[0], 400)
        self.assertEqual(
            request(self.server, "POST", "/v1/documents", source_request(extra=True))[0], 400
        )
        key = self.create()
        for suffix in ("/sources/../inputs.json", "/sources/-1", "/sources/9999", "/pages/0"):
            self.assertNotEqual(
                request(self.server, "GET", "/v1/documents/" + key + suffix)[0], 200
            )
        self.assertEqual(
            request(self.server, "POST", "/v1/documents/" + key + "/cancel", {"unexpected": True})[
                0
            ],
            400,
        )

    def test_completed_draft_edit_and_article_snapshot_never_gain_proof_status(self):
        key = self.complete()
        base = "/v1/documents/" + key
        code, draft, _ = request(self.server, "GET", base + "/draft")
        self.assertEqual(code, 200)
        self.assertFalse(draft["whole_article_verified"])
        b = draft["blocks"][0]
        code, draft, _ = request(
            self.server,
            "POST",
            base + "/review",
            {
                "request_id": "http-review",
                "expected_revision": 0,
                "block_id": b["id"],
                "text": b["text"],
                "kind": "claim",
                "note": "Only extraction was checked",
                "reviewed": True,
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(draft["revision"], 1)
        self.assertEqual(draft["blocks"][0]["evidence_level"], "E0")
        code, snapshot, _ = request(
            self.server,
            "POST",
            base + "/article",
            {"request_id": "http-article", "expected_revision": 1},
        )
        self.assertEqual(code, 201)
        report = self.server.article_audits.report(snapshot["article_id"])
        self.assertFalse(report["whole_article_verified"])
        self.assertEqual(
            self.server.article_audits.read(snapshot["article_id"])[0]["annotations"], {}
        )

    def test_pump_lifecycle_is_responsive_and_close_releases_owner(self):
        pump = DocumentPump(self.server.documents)
        self.server.document_pump = pump
        pump.thread.start()
        key = self.create()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            code, row, _ = request(self.server, "GET", "/v1/documents/" + key)
            self.assertEqual(code, 200)
            if row["state"] == "READY":
                break
            time.sleep(0.03)
        self.assertEqual(row["state"], "READY", row)
        self.server.shutdown()
        self.server.server_close()
        with DocumentPump(self.server.documents).owner():
            pass
