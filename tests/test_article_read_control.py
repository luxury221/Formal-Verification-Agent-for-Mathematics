"""Publication races for controlled reads; inert reports are not Lean evidence."""

from copy import deepcopy
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

from mra import article_rechecks
from mra.article_evidence_reader import ArticleEvidenceReadCancelled, ArticleEvidenceReadTimeout
from mra.article_evidence_reader import _run_command
from mra.article_recheck_worker import ArticleRecheckPump
import test_article_rechecks as recheck_contracts


class ArticleReadControlTests(unittest.TestCase):
    setUp = recheck_contracts.ArticleRecheckTests.setUp
    mutation = recheck_contracts.ArticleRecheckTests.mutation
    annotate = recheck_contracts.ArticleRecheckTests.annotate
    bind = recheck_contracts.ArticleRecheckTests.bind
    link = recheck_contracts.ArticleRecheckTests.link
    create = recheck_contracts.ArticleRecheckTests.create

    def prepare(self):
        self.link()
        self.jobs.reporter = None
        return self.create()["job_id"]

    def assert_discarded(self, key, state):
        row = self.jobs.read(key)
        self.assertEqual(row["state"], state)
        self.assertIsNone(row["summary"])
        self.assertEqual(row["observations"], [])
        self.assertFalse((self.jobs.directory(key) / "group-000.json").exists())
        return row

    def test_registered_cancel_interrupts_reader_without_observation_or_summary(self):
        key = self.prepare()

        def read(*, cancel_requested, lifecycle_lock_fd):
            os.fstat(lifecycle_lock_fd)
            self.assertFalse(cancel_requested())
            pending = self.jobs.cancel(self.key, key, {})
            self.assertEqual(pending["state"], "RUNNING")
            self.assertTrue(cancel_requested())
            raise ArticleEvidenceReadCancelled()

        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.side_effect = read
            self.jobs.work_once(key)
        self.assert_discarded(key, "CANCELLED")

    def test_shutdown_interrupts_reader_and_never_restarts_that_job(self):
        key = self.prepare()
        stopped = threading.Event()
        self.jobs.stop_requested = stopped.is_set

        def read(*, cancel_requested, lifecycle_lock_fd):
            stopped.set()
            self.assertTrue(cancel_requested())
            raise ArticleEvidenceReadCancelled()

        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.side_effect = read
            self.jobs.work_once(key)
            row = self.assert_discarded(key, "INTERRUPTED")
            self.assertEqual(row["error"]["code"], "READER_STOPPED")
            stopped.clear()
            self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
            reader.return_value.run.assert_called_once()

    def test_shutdown_after_provider_return_before_commit_discards_full_output(self):
        key = self.prepare()
        stopped = threading.Event()
        self.jobs.stop_requested = stopped.is_set

        def read(**kwargs):
            stopped.set()
            return deepcopy(self.evidence)

        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.side_effect = read
            self.jobs.work_once(key)
        self.assert_discarded(key, "INTERRUPTED")

    def test_shutdown_during_final_snapshot_read_retains_committed_graph_only(self):
        key = self.prepare()
        stopped = threading.Event()
        self.jobs.stop_requested = stopped.is_set

        def snapshot(row):
            stopped.set()
            return {"status": "MATCHES_AT_READ", "revision_at_read": 2, "reason": None}

        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.return_value = deepcopy(self.evidence)
            with patch.object(self.jobs, "_snapshot_state", side_effect=snapshot):
                self.jobs.work_once(key)
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "INTERRUPTED")
        self.assertIsNone(row["summary"])
        self.assertEqual(len(row["observations"]), 1)
        self.assertIsNone(self.jobs.result(self.key, key)["result"])

    def test_implementation_change_during_final_snapshot_cannot_publish_summary(self):
        key = self.prepare()
        original = article_rechecks.implementation()
        changed = False

        def implementation():
            return "f" * 64 if changed else original

        def snapshot(row):
            nonlocal changed
            changed = True
            return {"status": "MATCHES_AT_READ", "revision_at_read": 2, "reason": None}

        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.return_value = deepcopy(self.evidence)
            with patch.object(article_rechecks, "implementation", side_effect=implementation):
                with patch.object(self.jobs, "_snapshot_state", side_effect=snapshot):
                    self.jobs.work_once(key)
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "INTERRUPTED")
        self.assertEqual(row["error"]["code"], "IMPLEMENTATION_CHANGED")
        self.assertIsNone(row["summary"])

    def test_timeout_is_unavailable_without_mathematical_rejection_or_automatic_retry(self):
        key = self.prepare()
        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.side_effect = ArticleEvidenceReadTimeout()
            self.jobs.work_once(key)
            self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
            reader.return_value.run.assert_called_once()
        result = self.jobs.result(self.key, key)
        self.assertEqual(result["job"]["state"], "COMPLETED")
        self.assertEqual(result["result"]["summary"]["unavailable_graphs"], 1)
        link = result["result"]["groups"][0]["links"][0]
        self.assertEqual(link["status"], "UNAVAILABLE")
        self.assertIsNone(link["evidence_reference"])
        self.assertFalse(result["result"]["evidence_authority"])
        self.assertFalse(result["result"]["whole_article_verified"])
        self.assertNotIn("math_verdict", result["job"])
        self.assertEqual(self.jobs.report(self.key, key)["new_lean_executions"], 0)

    def test_pump_close_stops_actual_inert_child_and_releases_owner_lease(self):
        key = self.prepare()
        marker = self.root / "inert-reader.pid"
        command = [
            sys.executable,
            "-c",
            "import os,pathlib,time; "
            "pathlib.Path('inert-reader.pid').write_text(str(os.getpid())); time.sleep(10)",
        ]

        def read(*, cancel_requested, lifecycle_lock_fd):
            return _run_command(
                command,
                {"root": str(self.root)},
                cancel_requested=cancel_requested,
                lifecycle_lock_fd=lifecycle_lock_fd,
                timeout_seconds=10,
            )

        pump = ArticleRecheckPump(self.jobs)
        self.addCleanup(pump.close)
        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.side_effect = read
            pump.thread.start()
            until = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "Inert process actually started")
            pid = int(marker.read_text())
            started = time.monotonic()
            pump.close()
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(pump.snapshot()["state"], "STOPPED")
        self.assert_discarded(key, "INTERRUPTED")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.jobs.worker_session():
            self.assertEqual(self.jobs.recover()["interrupted_jobs"], [])
