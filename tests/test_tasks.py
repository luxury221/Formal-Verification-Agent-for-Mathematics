"""Actual SQLite/process tests; no proof results are invented by queue tests."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import time
import unittest

from mra.datasets import DATASET_ID, load_dataset
from mra.domain import canonical
from mra.integrity import ContentSnapshot, FILES, TREES
from mra.tasks import TaskStore, WorkerBusy

ROOT = Path(__file__).resolve().parents[1]


def copy_inputs(project):
    for name in (
        "evals/golden_cases.json",
        "evals/nat-arithmetic-v1/cases.json",
        "evals/datasets_manifest/nat-arithmetic-v1.json",
        "lean_workspace/environment.json",
        "lean_workspace/evidence-policy.json",
    ):
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    # Deliberately non-executable metadata for queue-only tests. Full backend
    # verification rejects it. These tests do not require an installed runtime.
    (project / ".runtime").mkdir()
    ContentSnapshot(
        project,
        {
            "schema_version": "1",
            "trees": {name: {} for name in TREES},
            "files": {name: {} for name in FILES},
            "host_files": {"/queue-test-only": {}},
            "provenance": {"purpose": "queue-state-test-only-not-a-runtime"},
        },
    ).write()


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_inputs(self.root)
        self.store = TaskStore(self.root)

    def submit(self, request_id="request-1", **options):
        return self.store.submit(request_id, "nat_add_zero", **options)

    def claim(self, task_id):
        with self.store.worker_lock():
            return self.store._claim(task_id)

    def fail(self, task_id, error_code="TIMEOUT"):
        _, attempt_id, _ = self.claim(task_id)
        self.store._finish(task_id, attempt_id, None, error_code, "State-machine test interruption")
        return attempt_id

    def test_queued_request_survives_restart_without_executing_lean(self):
        report = self.submit()
        loaded = TaskStore(self.root).report(report["task_id"])
        self.assertEqual(report, loaded)
        self.assertEqual(report["state"], "QUEUED")
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertEqual(report["evidence_level"], "E0_CANDIDATE")
        self.assertEqual([e["kind"] for e in report["events"]], ["CREATED", "QUEUED"])
        self.assertEqual(report["attempts"], [])
        self.assertFalse((self.root / "artifacts/runs").exists())
        self.assertFalse((self.root / ".runtime/lean-4.35.0-linux").exists())

    def test_request_id_retry_is_idempotent_and_changed_body_is_rejected(self):
        first = self.submit()
        self.assertEqual(self.submit(), first)
        for changes in ({"reviewer": "another reviewer"}, {"timeout_seconds": 1.0}):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(ValueError, "different content"),
            ):
                self.submit(**changes)
        with self.assertRaises(ValueError):
            self.store.submit("request-1", "false_all_nat_zero")

    def test_invalid_keys_budget_reviewer_and_unknown_cases_are_rejected(self):
        for key in ("", "../escape", "a" * 129):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.submit(key)
        for timeout in (0, -1, 121, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.submit(timeout_seconds=timeout)
        with self.assertRaises(ValueError):
            self.submit(reviewer="  ")
        with self.assertRaises(ValueError):
            self.store.submit("unknown", "by sorry")

    def test_reserved_references_cannot_bypass_explicit_evaluation_protocol(self):
        dataset = load_dataset(self.root)
        with self.assertRaisesRegex(ValueError, "Reserved"):
            self.store.submit(
                "reserved", dataset.manifest["splits"]["reserved"][0], suite=DATASET_ID
            )
        case_id = dataset.manifest["splits"]["regression"][0]
        self.assertEqual(
            self.store.submit("regression", case_id, suite=DATASET_ID)["state"], "QUEUED"
        )

    def test_cancel_before_execution_is_terminal_and_duplicate_submit_does_not_requeue(self):
        task_id = self.submit()["task_id"]
        cancelled = self.store.cancel(task_id)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(self.store.cancel(task_id), cancelled)
        self.assertEqual(self.submit(), cancelled)
        outcome = self.store.work_once(task_id)
        self.assertEqual(outcome["worker_state"], "IDLE")
        self.assertEqual(outcome["task"]["attempts"], [])

    def test_running_cancel_is_recorded_once_and_wins_terminal_commit(self):
        task_id = self.submit()["task_id"]
        _, attempt_id, _ = self.claim(task_id)
        self.store.cancel(task_id)
        self.store.cancel(task_id)
        self.store._finish(task_id, attempt_id, None, "EXECUTION_ERROR", "cancelled in test")
        report = self.store.report(task_id)
        self.assertEqual(report["state"], "CANCELLED")
        self.assertIsNone(report["attempts"][0]["result"])
        self.assertEqual(sum(e["kind"] == "CANCEL_REQUESTED" for e in report["events"]), 1)
        with self.assertRaises(ValueError):
            self.store._finish(task_id, attempt_id, None)

    def test_retry_requires_latest_failure_and_has_a_total_attempt_budget(self):
        task_id = self.submit()["task_id"]
        with self.assertRaises(ValueError):
            self.store.retry(task_id, "unknown-attempt", "retry-0")
        for number in range(1, 4):
            attempt_id = self.fail(task_id)
            report = self.store.report(task_id)
            self.assertEqual(report["retryable"], number < 3)
            if number < 3:
                queued = self.store.retry(task_id, attempt_id, f"retry-{number}")
                self.assertEqual(queued["state"], "QUEUED")
                self.assertEqual(self.store.retry(task_id, attempt_id, f"retry-{number}"), queued)
            else:
                with self.assertRaises(ValueError):
                    self.store.retry(task_id, attempt_id, "retry-3")
        self.assertEqual(len(self.store.report(task_id)["attempts"]), 3)

    def test_stale_retry_replay_never_requeues_or_replaces_a_later_attempt(self):
        task_id = self.submit()["task_id"]
        first = self.fail(task_id)
        self.store.retry(task_id, first, "retry-first")
        second = self.fail(task_id)
        self.store.retry(task_id, first, "retry-first")
        self.assertEqual(self.store.report(task_id)["latest_attempt_id"], second)
        self.assertEqual(self.store.report(task_id)["state"], "FAILED")
        with self.assertRaises(ValueError):
            self.store.retry(task_id, second, "retry-first")
        with self.assertRaises(ValueError):
            self.store.retry(task_id, first, "new-stale-retry")

    def test_policy_failure_is_not_retryable(self):
        task_id = self.submit()["task_id"]
        attempt_id = self.fail(task_id, "POLICY_BLOCKED")
        self.assertFalse(self.store.report(task_id)["retryable"])
        with self.assertRaises(ValueError):
            self.store.retry(task_id, attempt_id, "retry-policy")

    def test_worker_lock_prevents_parallel_execution_and_recovery(self):
        task_id = self.submit()["task_id"]
        with self.store.worker_lock():
            self.store._claim(task_id)
            for operation in (self.store.recover, self.store.work_once):
                with self.assertRaises(WorkerBusy):
                    operation()
        self.assertEqual(self.store.recover(), [task_id])
        self.assertEqual(self.store.report(task_id)["state"], "FAILED")
        self.assertEqual(self.store.recover(), [])

    def test_inherited_lock_blocks_recovery_until_child_exits(self):
        script = "import sys; sys.stdin.buffer.read(1)"
        with self.store.worker_lock() as fd:
            child = subprocess.Popen(
                [sys.executable, "-c", script], stdin=subprocess.PIPE, pass_fds=(fd,)
            )
        try:
            with self.assertRaises(WorkerBusy):
                self.store.recover()
        finally:
            child.communicate(b"x", timeout=5)
        self.assertEqual(self.store.recover(), [])

    def test_recovery_of_cancel_requested_attempt_is_cancelled_not_retryable(self):
        task_id = self.submit()["task_id"]
        self.claim(task_id)
        self.store.cancel(task_id)
        self.store.recover()
        report = self.store.report(task_id)
        self.assertEqual(report["state"], "CANCELLED")
        self.assertFalse(report["retryable"])
        self.assertEqual(report["math_verdict"], "UNKNOWN")

    def test_changed_policy_blocks_execution_before_lean(self):
        task_id = self.submit()["task_id"]
        policy = self.root / "lean_workspace/evidence-policy.json"
        data = json.loads(policy.read_text())
        data["policy_id"] = "changed-after-submission"
        policy.write_bytes(canonical(data))
        report = self.store.work_once(task_id)["task"]
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["attempts"][0]["error_code"], "POLICY_BLOCKED")
        self.assertFalse(report["retryable"])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_metadata_only_runtime_cannot_execute_or_publish_evidence(self):
        task_id = self.submit()["task_id"]
        report = self.store.work_once(task_id)["task"]
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["last_error_code"], "POLICY_BLOCKED")
        self.assertEqual(report["evidence_level"], "E0_CANDIDATE")
        self.assertFalse(report["retryable"])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_concurrent_retry_and_cancel_never_revive_cancelled_work(self):
        task_id = self.submit()["task_id"]
        attempt_id = self.fail(task_id)
        self.store.retry(task_id, attempt_id, "retry-once")
        barrier = Barrier(2)

        def replay():
            barrier.wait(timeout=10)
            self.store.retry(task_id, attempt_id, "retry-once")

        def cancel():
            barrier.wait(timeout=10)
            self.store.cancel(task_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(replay), pool.submit(cancel)]
            for future in futures:
                future.result()
        report = self.store.report(task_id)
        self.assertEqual(report["state"], "CANCELLED")
        self.assertEqual(len(report["attempts"]), 1)

    def test_concurrent_identical_submissions_commit_one_task(self):
        barrier = Barrier(5)

        def submit(_):
            barrier.wait(timeout=10)
            return self.submit()

        with ThreadPoolExecutor(max_workers=5) as pool:
            reports = list(pool.map(submit, range(5)))
        self.assertEqual(len({r["task_id"] for r in reports}), 1)
        self.assertTrue(all(len(r["events"]) == 2 for r in reports))

    def test_competing_claims_within_a_transaction_start_only_one_attempt(self):
        task_id = self.submit()["task_id"]
        barrier = Barrier(4)

        def claim(_):
            barrier.wait(timeout=10)
            return self.store._claim(task_id)

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim, range(4)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.assertEqual(len(self.store.report(task_id)["attempts"]), 1)

    def test_transaction_abort_cannot_leave_a_running_task_without_attempt(self):
        task_id = self.submit()["task_id"]
        with sqlite3.connect(self.store.path) as db:
            db.execute(
                "CREATE TRIGGER abort_start BEFORE INSERT ON events WHEN NEW.kind='STARTED' BEGIN SELECT RAISE(ABORT, 'injected interruption'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.claim(task_id)
        report = self.store.report(task_id)
        self.assertEqual(report["state"], "QUEUED")
        self.assertEqual(report["attempts"], [])

    def test_process_kill_rolls_back_uncommitted_cancellation(self):
        task_id = self.submit()["task_id"]
        script = """
import os, signal, sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE tasks SET state='CANCELLED', cancel_requested=1 WHERE id=?", (sys.argv[2],))
os.kill(os.getpid(), signal.SIGKILL)
"""
        killed = subprocess.run(
            [sys.executable, "-c", script, str(self.store.path), task_id],
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        self.assertEqual(TaskStore(self.root).report(task_id)["state"], "QUEUED")

    def test_database_schema_symlink_and_changed_request_fail_closed(self):
        task_id = self.submit()["task_id"]
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE tasks SET request='{}' WHERE id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, "request changed"):
            self.store.report(task_id)
        with sqlite3.connect(self.store.path) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "database version"):
            TaskStore(self.root)
        moved = self.store.path.with_suffix(".copy")
        self.store.path.rename(moved)
        self.store.path.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.report(task_id)


class TaskCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        copy_inputs(self.project)
        shutil.copytree(
            ROOT / "mra", self.project / "mra", ignore=shutil.ignore_patterns("__pycache__")
        )

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mra", *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_submit_report_cancel_and_idle_worker_are_real_cli_without_lean(self):
        args = ("task-submit", "nat_add_zero", "--request-id", "cli-submit")
        submitted = self.cli(*args)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        task = json.loads(submitted.stdout)
        self.assertEqual(json.loads(self.cli(*args).stdout)["task_id"], task["task_id"])
        report = self.cli("task-report", task["task_id"])
        self.assertEqual(json.loads(report.stdout), task)
        self.assertEqual(
            json.loads(self.cli("task-cancel", task["task_id"]).stdout)["state"], "CANCELLED"
        )
        idle = self.cli("task-work", "--task-id", task["task_id"])
        self.assertEqual(idle.returncode, 2, idle.stderr)
        self.assertEqual(json.loads(idle.stdout)["worker_state"], "IDLE")
        self.assertEqual(json.loads(self.cli("task-recover").stdout)["recovered_task_ids"], [])
        self.assertFalse((self.project / "artifacts/runs").exists())

    def test_invalid_task_requests_fail_cleanly(self):
        for args in (
            ("task-report", "unknown"),
            ("task-submit", "nat_add_zero", "--request-id", "../bad"),
            ("task-submit", "nat_add_zero", "--request-id", "bad-budget", "--timeout", "nan"),
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean")
class TaskRealLeanTests(unittest.TestCase):
    def setUp(self):
        self.store = TaskStore(ROOT)
        self.prefix = "test-" + os.urandom(8).hex()

    def submit(self, suffix="", **options):
        return self.store.submit(self.prefix + suffix, "nat_add_zero", **options)

    def test_actual_worker_persists_e3_and_duplicate_work_does_not_execute_again(self):
        task_id = self.submit()["task_id"]
        completed = self.store.work_once(task_id)["task"]
        self.assertEqual(completed["state"], "COMPLETED", completed)
        self.assertEqual(completed["math_verdict"], "UNKNOWN")
        self.assertEqual(completed["evidence_level"], "E3_KERNEL_CHECKED")
        self.assertEqual(completed["artifact_status"], "MATCHES_RECORDED_HASHES")
        self.assertFalse(completed["fresh_attestation"])
        self.assertEqual(self.store.work_once(task_id)["worker_state"], "IDLE")
        self.assertEqual(self.submit(), completed)
        self.assertEqual(len(self.store.report(task_id)["attempts"]), 1)
        directory = Path(completed["attempts"][0]["artifact_directory"])
        with (directory / "input/Candidate.lean").open("a") as output:
            output.write("\n-- modified historical artifact\n")
        changed = self.store.report(task_id)
        self.assertEqual(changed["artifact_status"], "CHANGED_OR_MISSING")
        self.assertEqual(changed["evidence_level"], "E0_CANDIDATE")

    def test_actual_timeout_stays_unknown_and_explicit_retry_gets_new_artifacts(self):
        task_id = self.submit(timeout_seconds=0.001)["task_id"]
        first = self.store.work_once(task_id)["task"]
        self.assertEqual(first["state"], "FAILED", first)
        self.assertEqual(first["run_status"], "TIMEOUT")
        self.assertEqual(first["math_verdict"], "UNKNOWN")
        self.store.retry(task_id, first["latest_attempt_id"], self.prefix + "-retry")
        second = self.store.work_once(task_id)["task"]
        self.assertEqual(second["state"], "FAILED")
        self.assertEqual(len(second["attempts"]), 2)
        self.assertNotEqual(*[a["artifact_directory"] for a in second["attempts"]])

    def test_completed_execution_distinguishes_refutation_invalid_proof_and_policy_rejection(self):
        for case_id, status, verdict, level in (
            ("false_all_nat_zero", "COMPLETED", "REFUTED", "E4_ALIGNED_AND_VERIFIED"),
            ("invalid_proof", "COMPLETED", "UNKNOWN", "E2_FORMALIZED"),
            ("sorry_placeholder", "POLICY_BLOCKED", "UNKNOWN", "E2_FORMALIZED"),
        ):
            with self.subTest(case=case_id):
                task_id = self.store.submit(
                    self.prefix + case_id, case_id, reviewer="repository-task-test-author"
                )["task_id"]
                report = self.store.work_once(task_id)["task"]
                self.assertEqual(report["state"], "COMPLETED", report)
                self.assertEqual(
                    (report["run_status"], report["math_verdict"], report["evidence_level"]),
                    (status, verdict, level),
                )
                self.assertFalse(report["retryable"])
                completed = self.store.cancel(task_id)
                self.assertEqual(completed["state"], "COMPLETED")
                self.assertFalse(completed["cancel_requested"])

    def test_cancellation_after_real_gate_but_before_commit_suppresses_task_result(self):
        class CancelAtCommitStore(TaskStore):
            def _finish(self, task_id, attempt_id, result, *args, **kwargs):
                if result is not None:
                    self.cancel(task_id)
                return super()._finish(task_id, attempt_id, result, *args, **kwargs)

        task_id = self.submit(reviewer="repository-task-cancel-race-test-author")["task_id"]
        report = CancelAtCommitStore(ROOT).work_once(task_id)["task"]
        self.assertEqual(report["state"], "CANCELLED")
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertEqual(report["evidence_level"], "E0_CANDIDATE")
        self.assertIsNone(report["attempts"][0]["result"])
        directory = Path(report["attempts"][0]["artifact_directory"])
        self.assertEqual(
            json.loads((directory / "report.json").read_text())["task"]["math_verdict"], "PROVED"
        )

    def test_checkpoint_failure_terminates_checker_and_releases_inherited_lock(self):
        class FailCheckpointStore(TaskStore):
            def _checkpoint(self, task_id, attempt_id, stage, directory=""):
                super()._checkpoint(task_id, attempt_id, stage, directory)
                if stage == "LEAN_RUNNING":
                    raise OSError("Injected checkpoint storage failure after checker launch")

        task_id = self.submit()["task_id"]
        report = FailCheckpointStore(ROOT).work_once(task_id)["task"]
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["last_error_code"], "EXECUTION_ERROR")
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertTrue(report["retryable"])
        self.assertEqual(self.store.recover(), [])

    def test_running_checker_can_be_cancelled_from_another_process(self):
        task_id = self.submit()["task_id"]
        child = subprocess.Popen(
            [sys.executable, "-m", "mra", "task-work", "--task-id", task_id],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                report = self.store.report(task_id)
                if any(e["payload"].get("stage") == "LEAN_RUNNING" for e in report["events"]):
                    self.store.cancel(task_id)
                    break
                if child.poll() is not None:
                    self.fail("Checker exited before cancellation checkpoint")
                time.sleep(0.02)
            else:
                self.fail("Checker never reached LEAN_RUNNING")
            stdout, stderr = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 2, stderr)
            report = json.loads(stdout)["task"]
            self.assertEqual(report["state"], "CANCELLED")
            self.assertEqual(report["math_verdict"], "UNKNOWN")
            self.assertEqual(report["evidence_level"], "E0_CANDIDATE")
            receipt = json.loads(
                (Path(report["attempts"][0]["artifact_directory"]) / "receipt.json").read_text()
            )
            self.assertEqual(receipt["payload"]["run_status"], "CANCELLED")
            self.assertEqual(self.store.recover(), [])
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_real_proof_report_before_supervisor_crash_is_never_replayed_as_evidence(self):
        task_id = self.submit(reviewer="repository-task-recovery-test-author")["task_id"]
        script = """
import os, signal, sys
from pathlib import Path
from mra.tasks import TaskStore
class CrashStore(TaskStore):
    def _finish(self, task_id, attempt_id, result, *args, **kwargs):
        if result is not None:
            os.kill(os.getpid(), signal.SIGKILL)
        return super()._finish(task_id, attempt_id, result, *args, **kwargs)
CrashStore(Path.cwd()).work_once(sys.argv[1])
"""
        child = subprocess.run(
            [sys.executable, "-c", script, task_id], cwd=ROOT, capture_output=True, timeout=60
        )
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
        interrupted = self.store.report(task_id)
        self.assertEqual(interrupted["state"], "RUNNING")
        old_directory = Path(interrupted["attempts"][0]["artifact_directory"])
        checked = json.loads((old_directory / "report.json").read_text())
        self.assertEqual(checked["task"]["math_verdict"], "PROVED")
        self.assertIn(task_id, self.store.recover())
        recovered = self.store.report(task_id)
        self.assertEqual(recovered["state"], "FAILED")
        self.assertEqual(recovered["math_verdict"], "UNKNOWN")
        self.assertEqual(recovered["evidence_level"], "E0_CANDIDATE")
        self.store.retry(task_id, recovered["latest_attempt_id"], self.prefix + "-retry")
        completed = self.store.work_once(task_id)["task"]
        self.assertEqual(completed["state"], "COMPLETED", completed)
        self.assertEqual(completed["math_verdict"], "PROVED")
        self.assertNotEqual(completed["attempts"][-1]["artifact_directory"], str(old_directory))
        self.assertEqual(len(completed["attempts"]), 2)


if __name__ == "__main__":
    unittest.main()
