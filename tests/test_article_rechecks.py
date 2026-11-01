"""Background evidence-read lifecycle contracts; reporter fixtures are not Lean evidence."""

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from mra import article_rechecks
from mra.article_audit import ArticleAuditStore
from mra.article_rechecks import ArticleRecheckStore
from mra.integrity import file_hash
from mra.research_flow import atomic
from mra.tasks import WorkerBusy
from test_article_audit import ArticleAuditTests, HTML, fixture_report


class ArticleRecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = fixture_report()
        self.articles = ArticleAuditStore(self.root, reporter=lambda *_: deepcopy(self.evidence))
        self.store = self.articles
        self.key = self.articles.create(
            {
                "request_id": "import",
                "title": "Inert fixture article",
                "url": "https://example.org/paper/v1",
                "html": HTML,
            }
        )["article_id"]
        self.provider_calls = []

        def provider(backend, graph_id):
            self.provider_calls.append((backend, graph_id))
            return deepcopy(self.evidence)

        self.provider = provider
        self.jobs = ArticleRecheckStore(self.root, articles=self.articles, reporter=provider)

    mutation = ArticleAuditTests.mutation
    annotate = ArticleAuditTests.annotate
    bind = ArticleAuditTests.bind

    def link(self, block_id="step1", graph_id="fixture-graph"):
        self.annotate(block_id=block_id)
        self.bind(block_id=block_id, graph_id=graph_id)

    def create(self, request_id="read-job"):
        return self.jobs.create(
            self.key,
            {
                "request_id": request_id,
                "expected_revision": self.articles.read(self.key)[0]["revision"],
            },
        )

    def finish(self, job_id):
        with self.jobs.worker_session():
            for _ in range(66):
                result = self.jobs.work_once(job_id)
                self.assertEqual(result["worker_state"], "EXECUTED", result)
                row = self.jobs.report(self.key, job_id)
                if row["state"] not in article_rechecks.ACTIVE:
                    return row
        raise AssertionError("Fixture job did not finish")

    def test_create_is_durable_idempotent_and_does_not_read_provider_or_modify_article(self):
        self.link()
        article_path = self.articles.directory(self.key) / "record.json"
        before = file_hash(article_path)
        first = self.create()
        second = self.create()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["state"], "QUEUED")
        self.assertEqual(first["snapshot_revision"], self.articles.read(self.key)[0]["revision"])
        self.assertEqual(
            first["progress"],
            {"completed_graphs": 0, "total_graphs": 1, "checked_links": 0, "total_links": 1},
        )
        self.assertEqual(file_hash(article_path), before)
        self.assertEqual(self.provider_calls, [])
        self.assertFalse(first["fresh_attestation"])
        self.assertIsNone(self.jobs.result(self.key, first["job_id"])["result"])

    def test_changed_request_or_revision_and_cross_article_access_are_rejected(self):
        self.link()
        job = self.create()
        for data in (
            {"request_id": "different", "expected_revision": 0},
            {"request_id": "different", "expected_revision": True},
            {"request_id": "read-job", "expected_revision": 0},
            {"request_id": "different", "expected_revision": 2, "extra": True},
        ):
            with self.assertRaises(ValueError):
                self.jobs.create(self.key, data)
        other = self.articles.create(
            {
                "request_id": "other",
                "title": "Other",
                "url": "https://example.org/paper/v2",
                "html": HTML,
            }
        )["article_id"]
        with self.assertRaises(ValueError):
            self.jobs.report(other, job["job_id"])
        with self.assertRaises(ValueError):
            self.jobs.result(other, job["job_id"])
        with self.assertRaises(ValueError):
            self.jobs.cancel(other, job["job_id"], {})

    def test_shared_graph_is_read_once_and_results_remain_historical_references(self):
        self.link()
        self.link("step2")
        key = self.create()["job_id"]
        status = self.finish(key)
        self.assertEqual(status["state"], "COMPLETED")
        self.assertEqual(self.provider_calls, [("calculus", "fixture-graph")])
        self.assertEqual(status["summary"]["matched_links"], 2)
        result = self.jobs.result(self.key, key)
        self.assertTrue(result["result"]["as_of_only"])
        self.assertFalse(result["result"]["fresh_attestation"])
        self.assertFalse(result["result"]["whole_article_verified"])
        group = result["result"]["groups"][0]
        self.assertEqual([x["status"] for x in group["links"]], ["MATCHED_AS_OF_CHECK"] * 2)
        self.assertIn("provider_report_sha256", group)
        self.assertEqual(group["links"][0]["evidence_reference"]["attempt_id"], "fixture-attempt")
        self.assertNotIn("math_verdict", status)
        self.assertNotIn("claim_verdict", result["result"])
        self.assertEqual(
            self.articles.report(self.key)["counts"]["current_verified_obligations"], 0
        )

    def test_empty_job_does_not_turn_no_links_into_whole_article_verification(self):
        key = self.create()["job_id"]
        row = self.finish(key)
        self.assertEqual(row["state"], "COMPLETED")
        self.assertEqual(row["summary"]["total_links"], 0)
        self.assertEqual(self.provider_calls, [])
        self.assertFalse(self.jobs.result(self.key, key)["result"]["whole_article_verified"])

    def test_default_provider_uses_the_controlled_original_provider_reader(self):
        self.link()
        self.jobs = ArticleRecheckStore(self.root, articles=self.articles)
        key = self.create()["job_id"]
        with patch("mra.article_rechecks.ArticleEvidenceReader") as reader:
            reader.return_value.run.return_value = deepcopy(self.evidence)
            self.finish(key)
        reader.assert_called_once_with(self.root, "calculus", "fixture-graph")
        reader.return_value.run.assert_called_once()
        self.assertTrue(callable(reader.return_value.run.call_args.kwargs["cancel_requested"]))
        self.assertIsInstance(reader.return_value.run.call_args.kwargs["lifecycle_lock_fd"], int)

    def test_source_annotation_or_link_change_before_execution_stales_job_without_provider(self):
        self.link()
        key = self.create()["job_id"]
        self.annotate(statement="Changed obligation")
        result = self.jobs.work_once(key)
        self.assertEqual(result["worker_state"], "EXECUTED")
        status = self.jobs.report(self.key, key)
        self.assertEqual(status["state"], "STALE")
        self.assertEqual(status["snapshot_status"], "CHANGED")
        self.assertIsNone(status["summary"])
        self.assertEqual(self.provider_calls, [])
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(
            self.jobs.create(
                self.key,
                {"request_id": "read-job", "expected_revision": status["snapshot_revision"]},
            )["job_id"],
            key,
        )

    def test_new_binding_after_snapshot_blocks_execution_even_when_source_is_identical(self):
        self.link()
        key = self.create()["job_id"]
        source_before = self.articles.read(self.key)[0]["source_fingerprint"]
        self.bind()
        self.jobs.work_once(key)
        self.assertEqual(self.jobs.report(self.key, key)["state"], "STALE")
        self.assertEqual(self.articles.read(self.key)[0]["source_fingerprint"], source_before)
        self.assertEqual(self.provider_calls, [])

    def test_article_change_during_provider_read_prevents_final_summary(self):
        self.link()
        key = self.create()["job_id"]
        entered, release = threading.Event(), threading.Event()
        errors = []

        def provider(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timeout")
            return self.provider(*args)

        self.jobs.reporter = provider

        def worker():
            try:
                self.jobs.work_once(key)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(5))
        try:
            self.annotate(statement="Changed while the original provider was reading")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        row = self.jobs.report(self.key, key)
        self.assertEqual(row["state"], "STALE")
        self.assertIsNone(row["summary"])
        self.assertIsNone(self.jobs.result(self.key, key)["result"])

    def test_completed_job_remains_history_and_hides_status_summary_after_article_change(self):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        self.annotate(statement="New meaning after completed historical read")
        status = self.jobs.report(self.key, key)
        archived = self.jobs.result(self.key, key)["result"]
        self.assertEqual(status["state"], "COMPLETED")
        self.assertEqual(status["snapshot_status"], "CHANGED")
        self.assertIsNone(status["summary"])
        self.assertFalse(archived["applicable_to_snapshot_at_read"])
        self.assertEqual(archived["summary"]["matched_links"], 1)
        self.assertEqual(len(self.provider_calls), 1)

    def test_polls_do_not_recheck_providers_or_convert_historical_success_to_current_authority(
        self,
    ):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        self.evidence["invalidated"] = True
        for _ in range(3):
            status = self.jobs.report(self.key, key)
            self.assertTrue(status["as_of_only"])
            self.assertFalse(status["fresh_attestation"])
            self.assertEqual(status["summary"]["matched_links"], 1)
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(
            self.articles.report(self.key, recheck=True)["counts"]["current_verified_obligations"],
            0,
        )

    def test_queued_cancel_is_immediate_idempotent_and_never_reads_provider(self):
        self.link()
        key = self.create()["job_id"]
        first = self.jobs.cancel(self.key, key, {})
        again = self.jobs.cancel(self.key, key, {})
        self.assertEqual(first, again)
        self.assertEqual(first["state"], "CANCELLED")
        self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
        self.assertEqual(self.provider_calls, [])

    def test_cancellation_during_read_wins_before_observation_commit(self):
        self.link()
        key = self.create()["job_id"]
        entered, release = threading.Event(), threading.Event()
        errors = []

        def provider(*args):
            entered.set()
            release.wait(5)
            return self.provider(*args)

        self.jobs.reporter = provider

        def worker():
            try:
                self.jobs.work_once(key)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(5))
        try:
            requested = self.jobs.cancel(self.key, key, {})
            self.assertEqual(requested["state"], "RUNNING")
            self.assertTrue(requested["cancel_requested"])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "CANCELLED")
        self.assertEqual(row["observations"], [])
        self.assertIsNone(row["summary"])

    def test_cancel_after_completed_commit_does_not_rewrite_history(self):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        before = self.jobs.read(key)
        self.jobs.cancel(self.key, key, {})
        self.assertEqual(self.jobs.read(key), before)

    def test_graph_boundary_cancellation_stops_remaining_provider_calls(self):
        self.link()
        self.link("step2", "second-graph")
        key = self.create()["job_id"]
        with self.jobs.worker_session():
            self.jobs.work_once(key)
            row = self.jobs.report(self.key, key)
            self.assertEqual(row["state"], "RUNNING")
            self.assertIsNone(row["active_graph"])
            self.assertEqual(self.jobs.cancel(self.key, key, {})["state"], "CANCELLED")
            self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
        self.assertEqual(len(self.provider_calls), 1)

    def test_owner_lifetime_lease_protects_idle_and_graph_boundary_from_other_pumps(self):
        self.link()
        self.link("step2", "second-graph")
        key = self.create()["job_id"]
        other = ArticleRecheckStore(self.root, articles=self.articles, reporter=self.provider)
        with self.jobs.worker_session():
            with self.assertRaises(WorkerBusy):
                other.recover()
            self.jobs.work_once(key)
            self.assertIsNone(self.jobs.read(key)["active_group"])
            with self.assertRaises(WorkerBusy):
                other.recover()
            self.assertEqual(other.work_once(key)["worker_state"], "BUSY")
            self.jobs.work_once(key)
        self.assertEqual(self.jobs.read(key)["state"], "COMPLETED")
        self.assertEqual(len(self.provider_calls), 2)

    def test_restart_marks_partial_job_interrupted_and_never_reexecutes(self):
        self.link()
        self.link("step2", "second-graph")
        key = self.create()["job_id"]
        with self.jobs.worker_session():
            self.jobs.work_once(key)
        restarted = ArticleRecheckStore(self.root, articles=self.articles, reporter=self.provider)
        recovered = restarted.recover()
        self.assertEqual(recovered["interrupted_jobs"], [key])
        self.assertFalse(recovered["automatic_retry"])
        row = restarted.report(self.key, key)
        self.assertEqual(row["state"], "INTERRUPTED")
        self.assertEqual(row["progress"]["completed_graphs"], 1)
        self.assertEqual(restarted.work_once(key)["worker_state"], "IDLE")
        self.assertEqual(len(self.provider_calls), 1)

    def test_interrupted_active_step_is_not_replayed_or_published(self):
        self.link()
        key = self.create()["job_id"]
        with patch.object(self.jobs, "_read_group", side_effect=SystemExit("simulated crash")):
            with self.assertRaises(SystemExit):
                self.jobs.work_once(key)
        self.assertEqual(self.jobs.read(key)["active_group"], 0)
        self.jobs.recover()
        status = self.jobs.report(self.key, key)
        self.assertEqual(status["state"], "INTERRUPTED")
        self.assertEqual(status["error_graph"]["graph_id"], "fixture-graph")
        self.assertIsNone(status["summary"])
        self.assertEqual(self.provider_calls, [])

    def test_uncommitted_observation_after_crash_never_becomes_completed_evidence(self):
        self.link()
        key = self.create()["job_id"]
        original = self.jobs._save

        def fail_commit(row):
            if row["next_group"] == 1:
                raise OSError("simulated failure after historical report write")
            original(row)

        with patch.object(self.jobs, "_save", side_effect=fail_commit):
            with self.assertRaises(OSError):
                self.jobs.work_once(key)
        self.assertTrue((self.jobs.directory(key) / "group-000.json").exists())
        self.jobs.recover()
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "INTERRUPTED")
        self.assertEqual(row["observations"], [])
        self.assertIsNone(self.jobs.result(self.key, key)["result"])
        self.assertEqual(len(self.provider_calls), 1)

    def test_provider_busy_and_failure_are_localized_without_automatic_retry(self):
        self.link()
        self.link("step2", "second-graph")
        self.link("eq1", "third-graph")
        key = self.create()["job_id"]

        def provider(backend, graph_id):
            self.provider_calls.append((backend, graph_id))
            if graph_id == "fixture-graph":
                raise WorkerBusy("fixture provider is updating")
            if graph_id == "second-graph":
                raise OSError("fixture report missing")
            return deepcopy(self.evidence)

        self.jobs.reporter = provider
        status = self.finish(key)
        self.assertEqual(status["state"], "COMPLETED")
        self.assertEqual(status["summary"]["busy_graphs"], 1)
        self.assertEqual(status["summary"]["unavailable_graphs"], 1)
        self.assertEqual(status["summary"]["issue_links"], 2)
        self.assertEqual(status["summary"]["matched_links"], 1)
        self.assertEqual(len(self.provider_calls), 3)

    def test_changed_exact_evidence_and_invalidated_provider_are_not_matches(self):
        self.link()
        key = self.create()["job_id"]
        self.evidence["checks"]["fixture-attempt"]["files"]["fixture/Candidate.lean"] = "changed"
        status = self.finish(key)
        self.assertEqual(status["summary"]["matched_links"], 0)
        self.assertEqual(
            self.jobs.result(self.key, key)["result"]["groups"][0]["links"][0]["status"],
            "DIFFERENT_EVIDENCE",
        )
        self.evidence["invalidated"] = True
        second = self.create("read-invalidated")["job_id"]
        self.finish(second)
        link = self.jobs.result(self.key, second)["result"]["groups"][0]["links"][0]
        self.assertEqual(link["status"], "UNAVAILABLE")
        self.assertIsNone(link["evidence_reference"])

    def test_already_stale_binding_is_recorded_without_calling_provider(self):
        self.link()
        self.annotate(statement="Changed before job creation")
        key = self.create()["job_id"]
        status = self.finish(key)
        self.assertEqual(status["summary"]["matched_links"], 0)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(
            self.jobs.result(self.key, key)["result"]["groups"][0]["status"], "SKIPPED_STALE"
        )

    def test_final_article_lock_contention_does_not_repeat_completed_provider(self):
        self.link()
        key = self.create()["job_id"]
        original, calls = self.jobs._complete, []

        def initially_busy(job_id):
            calls.append(job_id)
            if len(calls) == 1:
                raise WorkerBusy("fixture article mutation is finishing")
            original(job_id)

        with (
            self.jobs.worker_session(),
            patch.object(self.jobs, "_complete", side_effect=initially_busy),
        ):
            self.jobs.work_once(key)
            self.assertEqual(self.jobs.read(key)["state"], "RUNNING")
            self.assertEqual(self.jobs.read(key)["next_group"], 1)
            self.jobs.work_once(key)
        self.assertEqual(self.jobs.read(key)["state"], "COMPLETED")
        self.assertEqual(len(self.provider_calls), 1)

    def test_cancellation_wins_after_graph_commit_before_final_summary(self):
        self.link()
        key = self.create()["job_id"]
        original = self.jobs._complete

        def cancel_then_complete(job_id):
            self.jobs.cancel(self.key, job_id, {})
            original(job_id)

        with patch.object(self.jobs, "_complete", side_effect=cancel_then_complete):
            self.jobs.work_once(key)
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "CANCELLED")
        self.assertEqual(row["next_group"], 1)
        self.assertIsNone(row["summary"])

    def test_malformed_job_state_hashes_paths_and_observations_fail_closed(self):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        path = self.jobs.directory(key) / "record.json"
        original = path.read_bytes()
        mutations = (
            lambda row: row.update(state="COMPLETED", next_group=0),
            lambda row: row.update(state="RUNNING", owner_id=None, completed_at=None),
            lambda row: row["observations"][0].update(file="../other.json"),
            lambda row: row.update(summary={"matched_links": 99}),
            lambda row: row["summary"].update(matched_links=True),
            lambda row: row.update(request_id="another-request"),
            lambda row: row.update(request_fingerprint="f" * 64),
        )
        for change in mutations:
            row = self.jobs.envelope(path)
            change(row)
            atomic(path, row)
            with self.assertRaises((ValueError, TypeError)):
                self.jobs.report(self.key, key)
            path.write_bytes(original)
        report_path = self.jobs.directory(key) / "group-000.json"
        report_path.write_bytes(report_path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.jobs.result(self.key, key)

    def test_upgrade_and_rollback_keep_completed_history_readable_and_new_jobs_creatable(self):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        original = self.jobs.read(key)
        with patch.object(article_rechecks, "implementation", return_value="f" * 64):
            archived = self.jobs.result(self.key, key)
            self.assertEqual(archived["job"]["implementation_status"], "CHANGED")
            self.assertEqual(
                archived["job"]["implementation_fingerprint"],
                original["implementation_fingerprint"],
            )
            self.assertIsNone(archived["job"]["summary"])
            self.assertFalse(archived["result"]["applicable_to_current_implementation"])
            self.assertTrue(archived["result"]["as_of_only"])
            self.assertEqual(archived["result"]["summary"]["matched_links"], 1)
            self.assertEqual(self.jobs.listing(self.key)["jobs"][0]["job_id"], key)
            second = self.create("upgraded-job")["job_id"]
            self.finish(second)
            self.assertEqual(self.jobs.read(key), original)
        self.assertEqual(
            self.jobs.report(self.key, key)["implementation_status"], "MATCHES_AT_READ"
        )
        self.assertEqual(self.jobs.report(self.key, second)["implementation_status"], "CHANGED")
        self.assertEqual(
            self.jobs.result(self.key, second)["result"]["summary"]["matched_links"], 1
        )
        self.assertEqual(self.jobs.read(key), original)
        self.assertEqual(len(self.provider_calls), 2)

    def test_upgraded_active_jobs_are_interrupted_without_using_active_capacity_or_replaying(self):
        self.link()
        key = self.create()["job_id"]
        with patch.object(article_rechecks, "implementation", return_value="f" * 64):
            with patch.dict(article_rechecks.LIMITS, active=1):
                new_key = self.create("new-version-job")["job_id"]
            self.assertEqual(self.jobs.recover()["interrupted_jobs"], [key])
            old = self.jobs.report(self.key, key)
            self.assertEqual(old["state"], "INTERRUPTED")
            self.assertEqual(old["error"]["code"], "IMPLEMENTATION_CHANGED")
            self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
            self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.jobs.work_once(new_key)["worker_state"], "EXECUTED")
        self.assertEqual(self.jobs.read(new_key)["state"], "INTERRUPTED")
        self.assertEqual(self.jobs.read(new_key)["error"]["code"], "IMPLEMENTATION_CHANGED")
        self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")
        self.assertEqual(self.provider_calls, [])

    def test_upgrade_at_graph_boundary_interrupts_partial_job_and_preserves_committed_history(self):
        self.link()
        self.link("step2", "second-graph")
        key = self.create()["job_id"]
        with self.jobs.worker_session():
            self.jobs.work_once(key)
            with patch.object(article_rechecks, "implementation", return_value="f" * 64):
                self.jobs.work_once(key)
                row = self.jobs.read(key)
                self.assertEqual(row["state"], "INTERRUPTED")
                self.assertEqual(row["next_group"], 1)
                self.assertIsNone(row["summary"])
                self.assertEqual(row["error"]["code"], "IMPLEMENTATION_CHANGED")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")

    def test_implementation_change_during_provider_prevents_observation_commit(self):
        self.link()
        key = self.create()["job_id"]
        changed = False
        original = article_rechecks.implementation()

        def provider(*args):
            nonlocal changed
            changed = True
            return self.provider(*args)

        self.jobs.reporter = provider
        with patch.object(
            article_rechecks,
            "implementation",
            side_effect=lambda: "f" * 64 if changed else original,
        ):
            self.jobs.work_once(key)
            row = self.jobs.read(key)
            self.assertEqual(row["state"], "INTERRUPTED")
            self.assertEqual(row["observations"], [])
            self.assertIsNone(row["summary"])
            self.assertEqual(row["error"]["code"], "IMPLEMENTATION_CHANGED")
        self.assertEqual(len(self.provider_calls), 1)

    def test_upgrade_cannot_steal_live_owner_even_at_graph_boundaries(self):
        self.link()
        self.link("step2", "second-graph")
        key = self.create()["job_id"]
        other = ArticleRecheckStore(self.root, articles=self.articles, reporter=self.provider)
        with self.jobs.worker_session():
            self.jobs.work_once(key)
            with patch.object(article_rechecks, "implementation", return_value="f" * 64):
                with self.assertRaises(WorkerBusy):
                    other.recover()
                self.assertEqual(other.work_once(key)["worker_state"], "BUSY")
                self.assertEqual(self.jobs.read(key)["state"], "RUNNING")
        with patch.object(article_rechecks, "implementation", return_value="f" * 64):
            self.assertEqual(other.recover()["interrupted_jobs"], [key])
        self.assertEqual(len(self.provider_calls), 1)

    def test_upgrade_recovery_preserves_prior_cancel_request(self):
        self.link()
        key = self.create()["job_id"]

        def cancelled_provider(*args):
            self.jobs.cancel(self.key, key, {})
            raise SystemExit("simulated crash after durable cancellation request")

        self.jobs.reporter = cancelled_provider
        with self.assertRaises(SystemExit):
            self.jobs.work_once(key)
        self.assertTrue(self.jobs.read(key)["cancel_requested"])
        with patch.object(article_rechecks, "implementation", return_value="f" * 64):
            self.jobs.recover()
            row = self.jobs.read(key)
            self.assertEqual(row["state"], "CANCELLED")
            self.assertEqual(row["error"]["code"], "IMPLEMENTATION_CHANGED")
            self.assertIsNone(row["summary"])
        self.assertEqual(self.provider_calls, [])

    def test_upgrade_after_graph_commit_blocks_final_publication(self):
        self.link()
        key = self.create()["job_id"]
        original = self.jobs._complete

        def upgraded_finalizer(job_id):
            with patch.object(article_rechecks, "implementation", return_value="f" * 64):
                original(job_id)

        with patch.object(self.jobs, "_complete", side_effect=upgraded_finalizer):
            self.jobs.work_once(key)
        row = self.jobs.read(key)
        self.assertEqual(row["state"], "INTERRUPTED")
        self.assertEqual(row["next_group"], 1)
        self.assertIsNone(row["summary"])
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.jobs.work_once(key)["worker_state"], "IDLE")

    def test_changed_implementation_does_not_relax_old_storage_integrity(self):
        self.link()
        key = self.create()["job_id"]
        self.finish(key)
        path = self.jobs.directory(key) / "record.json"
        row = self.jobs.read(key)
        row["summary"]["matched_links"] = True
        atomic(path, row)
        with patch.object(article_rechecks, "implementation", return_value="f" * 64):
            with self.assertRaises(ValueError):
                self.jobs.result(self.key, key)
            with self.assertRaises(ValueError):
                self.create("new-job-beside-corruption")

    def test_redirected_storage_oversized_job_and_path_injection_are_rejected(self):
        key = self.create()["job_id"]
        with self.assertRaises(ValueError):
            self.jobs.report(self.key, "../../record")
        path = self.jobs.directory(key) / "record.json"
        original = path.read_bytes()
        path.write_bytes(b" " * (article_rechecks.LIMITS["bytes"] + 1))
        with self.assertRaises(ValueError):
            self.jobs.report(self.key, key)
        path.write_bytes(original)
        moved = path.with_name("other-record.json")
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaises(ValueError):
            self.jobs.report(self.key, key)

    def test_capacity_limit_and_bad_job_prevent_silent_skips(self):
        first = self.create()["job_id"]
        with patch.dict(article_rechecks.LIMITS, active=1):
            with self.assertRaises(ValueError):
                self.create("another-job")
        path = self.jobs.directory(first) / "record.json"
        path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.jobs.listing(self.key)
        with self.assertRaises(ValueError):
            self.create("not-skipped")


if __name__ == "__main__":
    unittest.main()
