"""Real loopback transport and worker boundaries; inert reporter fixtures, no Lean."""

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest

from mra.article_recheck_worker import ArticleRecheckPump
from test_article_audit import HTML, FORMAL, fixture_report
from test_http_api import ROOT, copy_web_inputs, request, running_server


class ArticleRecheckHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="recheck-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        context = running_server(self.root)
        self.server = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.evidence = fixture_report()
        self.server.article_audits.reporter = lambda *args: deepcopy(self.evidence)
        self.server.article_rechecks.reporter = lambda *args: deepcopy(self.evidence)
        code, report, _ = request(
            self.server,
            "POST",
            "/v1/article-audits",
            {
                "request_id": "http-recheck-import",
                "title": "Inert transport fixture",
                "url": "https://example.org/recheck/v1",
                "html": HTML,
            },
        )
        self.assertEqual(code, 201)
        self.article = report["article_id"]
        self.base = "/v1/article-audits/" + self.article
        self.revision = report["revision"]

    def bind(self):
        code, report, _ = request(
            self.server,
            "POST",
            self.base + "/annotate",
            {
                "request_id": "http-recheck-annotate",
                "expected_revision": self.revision,
                "block_id": "step1",
                "classification": "author_step",
                "statement": "Exact step",
                "assumptions": [],
                "depends_on": [],
                "note": "Fixture, not mathematical evidence",
            },
        )
        self.assertEqual(code, 200)
        code, report, _ = request(
            self.server,
            "POST",
            self.base + "/bind",
            {
                "request_id": "http-recheck-bind",
                "expected_revision": report["revision"],
                "block_id": "step1",
                "backend": "calculus",
                "graph_id": "fixture-graph",
                "node_id": "root",
                "formal_statement": FORMAL,
                "relation": "equivalent",
                "scope": "Inert local-store HTTP fixture only",
                "reviewer": "fixture reviewer",
                "confirmed": True,
            },
        )
        self.assertEqual(code, 200)
        self.revision = report["revision"]

    def create(self, **changes):
        return request(
            self.server,
            "POST",
            self.base + "/rechecks",
            {
                "request_id": "http-background-recheck",
                "expected_revision": self.revision,
                **changes,
            },
        )

    def wait_terminal(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            code, value, _ = request(self.server, "GET", self.base + "/rechecks/" + job_id)
            self.assertEqual(code, 200)
            if value["state"] not in ("QUEUED", "RUNNING"):
                return value
            time.sleep(0.02)
        self.fail("The inert background job did not reach a terminal state")

    def test_create_is_durable_idempotent_and_cancelled_without_a_worker(self):
        code, job, headers = self.create()
        self.assertEqual(code, 202)
        self.assertEqual(job["state"], "QUEUED")
        self.assertTrue(job["as_of_only"])
        self.assertFalse(job["fresh_attestation"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.create()[1]["job_id"], job["job_id"])
        self.assertEqual(self.create(expected_revision=self.revision + 1)[0], 400)
        code, listing, _ = request(self.server, "GET", self.base + "/rechecks")
        self.assertEqual(code, 200)
        self.assertEqual(len(listing["jobs"]), 1)
        path = self.base + "/rechecks/" + job["job_id"]
        self.assertIsNone(request(self.server, "GET", path + "/result")[1]["result"])
        cancelled = request(self.server, "POST", path + "/cancel", {})
        self.assertEqual(cancelled[0], 200)
        self.assertEqual(cancelled[1]["state"], "CANCELLED")
        self.assertIsNone(cancelled[1]["summary"])
        self.assertEqual(
            request(self.server, "POST", path + "/cancel", {})[1]["state"], "CANCELLED"
        )
        self.assertEqual(
            request(self.server, "POST", path + "/cancel", {"unexpected": True})[0], 400
        )

    def test_guards_unknown_fields_queries_and_article_job_mismatch_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"X-MRA-CSRF": "wrong"}):
            self.assertEqual(
                request(
                    self.server,
                    "POST",
                    self.base + "/rechecks",
                    {
                        "request_id": "forbidden",
                        "expected_revision": self.revision,
                    },
                    headers=headers,
                )[0],
                403,
            )
        self.assertEqual(self.create(extra="forbidden")[0], 400)
        job = self.create()[1]
        self.assertEqual(request(self.server, "GET", self.base + "/rechecks?ignored=yes")[0], 400)
        other = "/v1/article-audits/article-" + "0" * 32 + "/rechecks/" + job["job_id"]
        self.assertNotEqual(request(self.server, "GET", other)[0], 200)

    def test_http_remains_responsive_and_cancel_discards_inflight_provider_result(self):
        self.bind()
        entered, release = threading.Event(), threading.Event()

        def reporter(*args):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Fixture provider release deadline")
            return deepcopy(self.evidence)

        self.server.article_rechecks.reporter = reporter
        pump = ArticleRecheckPump(self.server.article_rechecks)
        self.server.article_recheck_pump = pump
        pump.thread.start()
        self.addCleanup(release.set)
        code, job, _ = self.create()
        self.assertEqual(code, 202)
        self.assertTrue(entered.wait(2))
        path = self.base + "/rechecks/" + job["job_id"]
        self.assertEqual(request(self.server, "GET", path)[1]["state"], "RUNNING")
        self.assertIsNone(request(self.server, "GET", path + "/result")[1]["result"])
        code, pending, _ = request(self.server, "POST", path + "/cancel", {})
        self.assertEqual(code, 200)
        self.assertTrue(pending["cancel_requested"])
        release.set()
        final = self.wait_terminal(job["job_id"])
        self.assertEqual(final["state"], "CANCELLED")
        self.assertIsNone(final["summary"])
        self.assertIsNone(request(self.server, "GET", path + "/result")[1]["result"])

    def test_shutdown_stops_both_queues_before_waiting_for_inflight_read(self):
        self.bind()
        entered, release, closing = threading.Event(), threading.Event(), threading.Event()

        def reporter(*args):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Fixture provider release deadline")
            return deepcopy(self.evidence)

        class IdleMathPump:
            # Lifecycle fixture only: no mathematical worker or Lean execution.
            def __init__(self):
                self.stopped = threading.Event()
                self.wake = threading.Event()
                self.closed = threading.Event()

            def close(self):
                self.stopped.set()
                self.wake.set()
                self.closed.set()

        self.server.article_rechecks.reporter = reporter
        code, job, _ = self.create()
        self.assertEqual(code, 202)
        pump = ArticleRecheckPump(self.server.article_rechecks)
        self.server.article_recheck_pump = pump
        self.server.pump = math_pump = IdleMathPump()
        original_close = pump.close

        def observed_close():
            closing.set()
            original_close()

        pump.close = observed_close
        pump.thread.start()
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        self.server.shutdown()
        errors = []

        def close_server():
            try:
                self.server.server_close()
            except BaseException as error:
                errors.append(error)

        closer = threading.Thread(target=close_server)
        closer.start()
        try:
            self.assertTrue(closing.wait(2))
            self.assertTrue(closer.is_alive(), "Injected providers retain their own stop contract")
            self.assertTrue(pump.stopped.is_set())
            self.assertTrue(
                math_pump.stopped.is_set(),
                "The other queue must stop taking new tasks before a provider join blocks",
            )
            self.assertTrue(math_pump.wake.is_set())
        finally:
            release.set()
            closer.join(timeout=3)
        self.assertFalse(closer.is_alive())
        self.assertFalse(errors)
        self.assertEqual(pump.snapshot()["state"], "STOPPED")
        self.assertTrue(math_pump.closed.is_set())
        # A finished graceful shutdown must release the independent owner lease.
        with self.server.article_rechecks.worker_session():
            self.server.article_rechecks.recover()
        final = self.server.article_rechecks.report(self.article, job["job_id"])
        self.assertEqual(final["state"], "INTERRUPTED")
        self.assertEqual(final["error"]["code"], "READER_STOPPED")
        self.assertIsNone(final["summary"])
        self.assertEqual(final["progress"]["completed_graphs"], 0)
        self.assertFalse(final["result_available"])
        self.assertFalse(final["fresh_attestation"])

    def test_completed_job_is_historical_and_article_edits_hide_its_summary(self):
        self.bind()
        job = self.create()[1]
        with self.server.article_rechecks.worker_session():
            self.server.article_rechecks.recover()
            for _ in range(4):
                self.server.article_rechecks.work_once()
        path = self.base + "/rechecks/" + job["job_id"]
        final = request(self.server, "GET", path)[1]
        self.assertEqual(final["state"], "COMPLETED")
        self.assertIsNotNone(final["summary"])
        self.assertTrue(final["as_of_only"])
        self.assertFalse(final["fresh_attestation"])
        result = request(self.server, "GET", path + "/result")[1]
        self.assertIsNotNone(result["result"])
        code, original, _ = request(self.server, "GET", self.base)
        self.assertEqual(code, 200)
        self.assertFalse(original["evidence_rechecked"])
        self.assertEqual(original["claim_verdict"], "UNKNOWN")
        code, _, _ = request(
            self.server,
            "POST",
            self.base + "/annotate",
            {
                "request_id": "edit-after-check",
                "expected_revision": self.revision,
                "block_id": "step1",
                "classification": "author_step",
                "statement": "Exact step",
                "assumptions": [],
                "depends_on": [],
                "note": "New interpretation requires a new check",
            },
        )
        self.assertEqual(code, 200)
        stale = request(self.server, "GET", path)[1]
        self.assertEqual(stale["snapshot_status"], "CHANGED")
        self.assertIsNone(stale["summary"])
        history = request(self.server, "GET", path + "/result")[1]["result"]
        self.assertIsNotNone(history)
        self.assertFalse(history["applicable_to_snapshot_at_read"])
        self.assertEqual(history["snapshot_status"], "CHANGED")
        self.assertTrue(history["as_of_only"])

    def test_local_math_assets_have_fixed_routes_binary_mime_and_narrow_csp(self):
        for route in ("/articles", "/proof", "/analysis", "/studio"):
            code, _, headers = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            csp = headers["Content-Security-Policy"]
            self.assertIn("script-src 'self'", csp)
            self.assertIn("style-src-attr 'unsafe-inline'", csp)
            self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertNotIn(
            "style-src-attr", request(self.server, "GET", "/")[2]["Content-Security-Policy"]
        )
        for route in (
            "/math-display.js",
            "/math-display.css",
            "/vendor/katex/katex.min.js",
            "/vendor/katex/katex.min.css",
        ):
            code, body, _ = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            self.assertEqual(body, (self.root / "mra/web" / route.lstrip("/")).read_bytes())
        code, body, headers = request(
            self.server, "GET", "/vendor/katex/fonts/KaTeX_Main-Regular.woff2"
        )
        self.assertEqual(code, 200)
        self.assertTrue(body)
        self.assertEqual(headers["Content-Type"], "font/woff2")
        for route in (
            "/vendor/katex/../LICENSE",
            "/vendor/katex/package.json",
            "/vendor/katex/katex.min.js?x=1",
        ):
            self.assertEqual(request(self.server, "GET", route)[0], 404)


if __name__ == "__main__":
    unittest.main()
