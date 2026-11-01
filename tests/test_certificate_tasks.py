"""Frozen certificate queue inputs, SQLite recovery, and actual two-stage Lean runs."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mra.certificates import CERTIFICATE_FILE, MAX_BYTES, MODE
from mra.domain import canonical, digest
from mra.tasks import TaskStore
from test_tasks import copy_inputs

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "evals/certificates/add-zero.json").read_bytes()
LAMBDA = (ROOT / "evals/certificates/add-zero-lambda.json").read_bytes()


class CertificateTaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_inputs(self.root)
        self.store = TaskStore(self.root)

    def submit(self, request_id="certificate-1", raw=RAW, **options):
        return self.store.submit_certificate(request_id, "nat_add_zero", raw, **options)

    def test_canonical_body_and_digest_survive_restart_without_lean(self):
        first = self.submit()
        request = first["request"]
        body = canonical(json.loads(RAW)) + b"\n"
        self.assertEqual(request["kind"], "kernel-certificate-check")
        self.assertEqual(request["input_mode"], MODE)
        self.assertEqual(request["certificate"].encode(), body)
        self.assertEqual(request["certificate_digest"], hashlib.sha256(body).hexdigest())
        self.assertEqual(TaskStore(self.root).report(first["task_id"]), first)
        self.assertEqual(
            (first["math_verdict"], first["evidence_level"]), ("UNKNOWN", "E0_CANDIDATE")
        )
        self.assertFalse((self.root / "artifacts/runs").exists())
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_formatting_is_idempotent_but_proof_reviewer_and_budget_changes_conflict(self):
        first = self.submit()
        reordered = dict(reversed(list(json.loads(RAW).items())))
        self.assertEqual(self.submit(raw=json.dumps(reordered, indent=4).encode()), first)
        for options in ({"raw": LAMBDA}, {"reviewer": "other"}, {"timeout_seconds": 1}):
            with (
                self.subTest(options=options),
                self.assertRaisesRegex(ValueError, "different content"),
            ):
                self.submit(**options)

    def test_reference_and_certificate_request_ids_share_one_namespace(self):
        self.submit()
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.submit("certificate-1", "nat_add_zero")
        self.store.submit("reference-1", "nat_add_zero")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.submit("reference-1")

    def test_source_oversized_invalid_and_misbound_inputs_never_queue(self):
        source = {**json.loads(RAW), "proof": {"op": "source", "code": "by rfl"}}
        misbound = {**json.loads(RAW), "claim_fingerprint": "0" * 64}
        for raw in (
            None,
            "{}",
            b"",
            b" " * (MAX_BYTES + 1),
            b'{"proof":1,"proof":2}',
            canonical(source),
            canonical(misbound),
        ):
            with self.subTest(raw_type=type(raw)), self.assertRaises(ValueError):
                self.submit(raw=raw)
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_disabled_targets_and_suite_override_are_rejected(self):
        for target in ("sandbox_boundary", "unknown", "natv1_add_comm"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.store.submit_certificate("disabled", target, RAW)
        with self.assertRaises(TypeError):
            self.submit(suite="nat-arithmetic-v1")

    def test_queued_cancel_is_terminal_even_on_duplicate_submission(self):
        task_id = self.submit()["task_id"]
        cancelled = self.store.cancel(task_id)
        self.assertEqual(self.submit(), cancelled)
        self.assertEqual(self.store.work_once(task_id)["worker_state"], "IDLE")
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(cancelled["attempts"], [])

    def test_worker_revalidates_kind_suite_digest_body_and_budget_before_lean(self):
        for index, change in enumerate(
            (
                {"kind": "unrecognized-check"},
                {"kind": "repository-reference-check"},
                {"suite": "nat-arithmetic-v1"},
                {"certificate_digest": "0" * 64},
                {"certificate": LAMBDA.decode()},
                {"certificate": None},
                {"timeout_seconds": 0},
            )
        ):
            task_id = self.submit(f"changed-{index}")["task_id"]
            request = {**self.store.report(task_id)["request"], **change}
            # Even a self-consistent DB digest cannot skip request reconstruction.
            # The owner remains trusted; this is a malformed-state regression.
            with sqlite3.connect(self.store.path) as db:
                db.execute(
                    "UPDATE tasks SET request=?, fingerprint=? WHERE id=?",
                    (canonical(request).decode(), digest(request), task_id),
                )
            report = self.store.work_once(task_id)["task"]
            with self.subTest(change=change):
                self.assertEqual(report["state"], "FAILED")
                self.assertEqual(report["last_error_code"], "POLICY_BLOCKED")
                self.assertFalse(report["retryable"])
                self.assertEqual(report["evidence_level"], "E0_CANDIDATE")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_target_drift_after_submission_cannot_reuse_certificate(self):
        task_id = self.submit()["task_id"]
        path = self.root / "evals/golden_cases.json"
        cases = json.loads(path.read_text())
        next(case for case in cases if case["id"] == "nat_add_zero").setdefault("annotation", {})[
            "assumptions"
        ] = ["changed assumption"]
        path.write_bytes(canonical(cases))
        report = self.store.work_once(task_id)["task"]
        self.assertEqual(report["last_error_code"], "POLICY_BLOCKED")
        self.assertFalse((self.root / "artifacts/runs").exists())


class CertificateTaskCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_inputs(self.root)
        shutil.copytree(
            ROOT / "mra", self.root / "mra", ignore=shutil.ignore_patterns("__pycache__")
        )
        (self.root / "proof.json").write_bytes(RAW)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mra", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_cli_freezes_input_before_file_removal_and_restart(self):
        args = (
            "task-submit-certificate",
            "nat_add_zero",
            "--input",
            "proof.json",
            "--request-id",
            "cli-cert",
        )
        first = self.cli(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        task = json.loads(first.stdout)
        self.assertEqual(json.loads(self.cli(*args).stdout), task)
        (self.root / "proof.json").unlink()
        self.assertEqual(json.loads(self.cli("task-report", task["task_id"]).stdout), task)
        cancelled = json.loads(self.cli("task-cancel", task["task_id"]).stdout)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_cli_rejects_invalid_certificate_without_traceback_or_execution(self):
        for raw in (b'{"source":"by rfl"}', b" " * (MAX_BYTES + 1)):
            (self.root / "proof.json").write_bytes(raw)
            result = self.cli(
                "task-submit-certificate",
                "nat_add_zero",
                "--input",
                "proof.json",
                "--request-id",
                "cli-invalid",
            )
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "artifacts/runs").exists())


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean")
class CertificateTaskRealLeanTests(unittest.TestCase):
    def setUp(self):
        self.store = TaskStore(ROOT)
        self.prefix = "certificate-task-" + os.urandom(8).hex()

    def submit(self, suffix="", raw=RAW, **options):
        return self.store.submit_certificate(self.prefix + suffix, "nat_add_zero", raw, **options)

    def receipt(self, attempt):
        return json.loads((Path(attempt["artifact_directory"]) / "receipt.json").read_text())[
            "payload"
        ]

    def assert_checked(self, task, attempt):
        payload = self.receipt(attempt)
        self.assertEqual(payload["input_mode"], MODE)
        self.assertEqual(payload["certificate_digest"], task["request"]["certificate_digest"])
        self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertEqual(payload["replay"]["run_status"], "COMPLETED")
        self.assertIn(CERTIFICATE_FILE, attempt["file_hashes"])
        self.assertEqual(
            (Path(attempt["artifact_directory"]) / CERTIFICATE_FILE).read_text(),
            task["request"]["certificate"],
        )

    def test_frozen_certificate_runs_after_input_deletion_and_tampering_suppresses_history(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            path = Path(directory) / "input.json"
            path.write_bytes(RAW)
            task_id = self.submit(raw=path.read_bytes())["task_id"]
            path.unlink()
        task = TaskStore(ROOT).work_once(task_id)["task"]
        self.assertEqual(task["state"], "COMPLETED", task)
        self.assertEqual(
            (task["math_verdict"], task["evidence_level"]), ("UNKNOWN", "E3_KERNEL_CHECKED")
        )
        self.assert_checked(task, task["attempts"][0])
        self.assertEqual(self.submit(), task)
        self.assertEqual(self.store.work_once(task_id)["worker_state"], "IDLE")
        path = Path(task["attempts"][0]["artifact_directory"]) / CERTIFICATE_FILE
        path.write_bytes(LAMBDA)
        changed = self.store.report(task_id)
        self.assertEqual(changed["artifact_status"], "CHANGED_OR_MISSING")
        self.assertEqual(
            (changed["math_verdict"], changed["evidence_level"]), ("UNKNOWN", "E0_CANDIDATE")
        )

    def test_named_alignment_proves_original_or_complete_negation(self):
        for name, case_id, raw, verdict in (
            ("prove", "nat_add_zero", LAMBDA, "PROVED"),
            (
                "refute",
                "false_all_nat_zero",
                (ROOT / "evals/certificates/not-all-zero.json").read_bytes(),
                "REFUTED",
            ),
        ):
            task_id = self.store.submit_certificate(
                self.prefix + name, case_id, raw, reviewer="repository-certificate-task-test-author"
            )["task_id"]
            task = self.store.work_once(task_id)["task"]
            with self.subTest(case_id=case_id):
                self.assertEqual(task["state"], "COMPLETED", task)
                self.assertEqual(
                    (task["math_verdict"], task["evidence_level"]),
                    (verdict, "E4_ALIGNED_AND_VERIFIED"),
                )
                self.assert_checked(task, task["attempts"][0])
                self.assertEqual(self.store.cancel(task_id), task)

    def test_wrong_type_and_unsafe_constant_never_refute_or_retry(self):
        for name, proof, status in (
            ("type", {"op": "nat", "value": 0}, "COMPLETED"),
            ("unsafe", {"op": "const", "name": "unsafeCast", "levels": [0, 0]}, "POLICY_BLOCKED"),
        ):
            task_id = self.submit(
                name,
                raw=canonical({**json.loads(RAW), "proof": proof}),
                reviewer="repository-certificate-task-test-author",
            )["task_id"]
            task = self.store.work_once(task_id)["task"]
            with self.subTest(name=name):
                self.assertEqual(task["state"], "COMPLETED", task)
                self.assertEqual(
                    (task["run_status"], task["math_verdict"], task["evidence_level"]),
                    (status, "UNKNOWN", "E2_FORMALIZED"),
                )
                self.assertFalse(task["retryable"])

    def test_cancellation_during_real_replay_publishes_no_task_evidence(self):
        class CancelReplayStore(TaskStore):
            def _checkpoint(self, task_id, attempt_id, stage, directory=""):
                super()._checkpoint(task_id, attempt_id, stage, directory)
                if stage == "REPLAY_RUNNING":
                    self.cancel(task_id)

        task_id = self.submit()["task_id"]
        task = CancelReplayStore(ROOT).work_once(task_id)["task"]
        self.assertEqual(task["state"], "CANCELLED", task)
        self.assertEqual(
            (task["math_verdict"], task["evidence_level"]), ("UNKNOWN", "E0_CANDIDATE")
        )
        self.assertIsNone(task["attempts"][0]["result"])
        self.assertEqual(self.receipt(task["attempts"][0])["run_status"], "CANCELLED")
        self.assertEqual(self.store.recover(), [])

    def test_cancellation_wins_even_after_real_e4_before_terminal_commit(self):
        class CancelCommitStore(TaskStore):
            def _finish(self, task_id, attempt_id, result, *args, **kwargs):
                if result is not None:
                    self.cancel(task_id)
                return super()._finish(task_id, attempt_id, result, *args, **kwargs)

        task_id = self.submit(reviewer="repository-certificate-task-test-author")["task_id"]
        task = CancelCommitStore(ROOT).work_once(task_id)["task"]
        self.assertEqual(task["state"], "CANCELLED")
        self.assertEqual(task["evidence_level"], "E0_CANDIDATE")
        directory = Path(task["attempts"][0]["artifact_directory"])
        self.assertEqual(
            json.loads((directory / "report.json").read_text())["task"]["math_verdict"], "PROVED"
        )

    def test_timeout_retry_keeps_frozen_proof_and_budget_with_fresh_artifacts(self):
        task_id = self.submit(timeout_seconds=0.001)["task_id"]
        first = self.store.work_once(task_id)["task"]
        self.assertEqual(first["run_status"], "TIMEOUT", first)
        self.store.retry(task_id, first["latest_attempt_id"], self.prefix + "-retry")
        task = self.store.work_once(task_id)["task"]
        self.assertEqual(task["run_status"], "TIMEOUT", task)
        self.assertEqual(task["math_verdict"], "UNKNOWN")
        self.assertEqual(first["request"], task["request"])
        self.assertEqual(len(task["attempts"]), 2)
        self.assertNotEqual(*[a["artifact_directory"] for a in task["attempts"]])
        for attempt in task["attempts"]:
            self.assertEqual(
                self.receipt(attempt)["certificate_digest"], task["request"]["certificate_digest"]
            )

    def test_crash_after_real_check_requires_fresh_two_process_retry(self):
        task_id = self.submit(reviewer="repository-certificate-task-test-author")["task_id"]
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
            [sys.executable, "-c", script, task_id], cwd=ROOT, capture_output=True, timeout=90
        )
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
        interrupted = self.store.report(task_id)
        self.assertEqual(interrupted["state"], "RUNNING")
        old = interrupted["attempts"][0]
        self.assertEqual(
            json.loads((Path(old["artifact_directory"]) / "report.json").read_text())["task"][
                "math_verdict"
            ],
            "PROVED",
        )
        self.assertIn(task_id, self.store.recover())
        recovered = self.store.report(task_id)
        self.assertEqual(
            (recovered["math_verdict"], recovered["evidence_level"]), ("UNKNOWN", "E0_CANDIDATE")
        )
        self.store.retry(task_id, recovered["latest_attempt_id"], self.prefix + "-retry")
        task = self.store.work_once(task_id)["task"]
        self.assertEqual((task["state"], task["math_verdict"]), ("COMPLETED", "PROVED"), task)
        self.assertEqual(task["request"], recovered["request"])
        self.assertNotEqual(task["attempts"][1]["artifact_directory"], old["artifact_directory"])
        self.assert_checked(task, task["attempts"][1])
        old_payload, new_payload = self.receipt(old), self.receipt(task["attempts"][1])
        self.assertTrue(
            {old_payload["candidate_pid"], old_payload["replay"]["pid"]}.isdisjoint(
                {new_payload["candidate_pid"], new_payload["replay"]["pid"]}
            )
        )


if __name__ == "__main__":
    unittest.main()
