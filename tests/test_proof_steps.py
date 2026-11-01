"""Controlled prefix contracts and actual Lean open/failure/closed/crash boundaries."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.proof_steps import MODE, PrefixChecks, ProofStepStore, prepare
from mra.research_flow import SOURCE_FILES
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_research_flow import create as create_flow, parent, single
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "steps-qa-" + uuid.uuid4().hex


def create(store, flow, node="problem"):
    raw = {"flow_id": flow["flow_id"], "node_id": node}
    plan = store.preview(raw)
    return store.create({**raw, "request_id": rid(), "plan_fingerprint": plan["plan_fingerprint"]})


def submission(store, key, parent_id=None, tactic=None):
    raw = {"parent_id": parent_id, "tactic": tactic}
    plan = store.preview_step(key, raw)
    return {
        **raw,
        "request_id": rid(),
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "Controlled prefix QA",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*SOURCE_FILES, "mra/proof_steps.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.project = parent(self.root)
        self.store = ProofStepStore(self.root)
        self.flow = create_flow(self.store.flows, self.project, single())
        self.scope = create(self.store, self.flow)
        self.key = self.scope["scope_id"]

    def test_save_and_preview_are_unknown_and_never_execute_or_create_child_ledgers(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            p = self.store.preview_step(self.key, {"parent_id": None, "tactic": None})
        self.assertEqual(p["child_plan"]["formal_statement"].count("→"), 3)
        self.assertEqual(p["child_plan"]["request"]["steps"], [])
        self.assertEqual(self.scope["math_verdict"], "UNKNOWN")
        self.assertEqual(list(self.store.directory(self.key).glob("attempt-*")), [])

    def test_host_commands_proofs_imports_wrong_domains_and_excessive_depth_rejected(self):
        raw = {**single()[0]["target"], "schema": MODE, "steps": []}
        for change in (
            {"steps": ["sorry"]},
            {"steps": ["intros\nrun_tac IO.println 1"]},
            {"steps": [True]},
            {"steps": ["intros"] * 13},
            {"proof": "by sorry"},
            {"imports": ["Mathlib"]},
            {"schema": "Int"},
            {"timeout_seconds": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare(self.root, {**raw, **change})

    def test_step_compiler_preserves_full_original_target_and_only_appends_fixed_source(self):
        raw = {**single()[0]["target"], "schema": MODE, "steps": ["intros", "dvd-gcd"]}
        before = deepcopy(raw)
        p = prepare(self.root, raw)
        self.assertEqual(raw, before)
        self.assertEqual(p["request"]["assumptions"], before["assumptions"])
        self.assertIn("apply Nat.dvd_gcd", p["proof_source"])
        self.assertNotIn("sorry", p["proof_source"])
        self.assertEqual(p["budget"]["automatic_retries"], 0)

    def test_unobserved_wrong_or_arbitrary_branch_parent_is_rejected(self):
        for raw in (
            {"parent_id": "missing", "tactic": "intros"},
            {"parent_id": None, "tactic": "intros"},
            {"parent_id": None, "tactic": None, "goals": []},
        ):
            with self.assertRaises(ValueError):
                self.store.preview_step(self.key, raw)

    def test_named_exact_confirmation_idempotency_and_single_active_attempt(self):
        raw = submission(self.store, self.key)
        for change in (
            {"reviewer": " "},
            {"confirmed": False},
            {"confirmed": 1},
            {"plan_fingerprint": "stale"},
        ):
            with self.assertRaises(ValueError):
                self.store.submit(self.key, {**raw, **change})
        a = self.store.submit(self.key, raw)
        self.assertEqual(a, self.store.submit(self.key, raw))
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "request_id": rid()})
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "reviewer": "Changed"})
        row = self.store.cancel(self.key, a["attempt_id"])
        self.assertEqual(row["attempts"][0]["state"], "CANCELLED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_prerequisites_cannot_be_bypassed_by_new_workspace(self):
        flow = create_flow(self.store.flows, self.project)
        with self.assertRaises(ValueError):
            create(self.store, flow)

    def test_source_drift_persists_after_restoration_and_blocks_claim(self):
        self.store.submit(self.key, submission(self.store, self.key))
        path = self.root / "mra/proof_steps.py"
        before = path.read_bytes()
        path.write_bytes(before + b"\n# drift\n")
        with patch.object(self.store, "execute", side_effect=AssertionError("No execution")):
            self.store.work_once()
        path.write_bytes(before)
        row = self.store.report(self.key)
        self.assertTrue(row["invalidated"])
        self.assertEqual(row["attempts"][0]["state"], "POLICY_BLOCKED")

    def test_busy_parent_lock_or_database_is_transient_not_invalidating(self):
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.store.flows, "report", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.store.report(self.key)
            self.assertFalse(self.store.report(self.key)["invalidated"])
            self.assertFalse((self.store.directory(self.key) / "invalidated.json").exists())

    def test_record_and_plan_corruption_stay_invalid_after_original_restored(self):
        for name in ("record.json", "plan.json"):
            scope = create(self.store, self.flow)
            path = self.store.directory(scope["scope_id"]) / name
            original = path.read_bytes()
            path.write_text("corrupt")
            with self.assertRaises(ValueError):
                self.store.report(scope["scope_id"])
            path.write_bytes(original)
            self.assertTrue(self.store.report(scope["scope_id"])["invalidated"])

    def test_corrupt_record_does_not_block_unrelated_workspaces(self):
        (self.store.directory(self.key) / "record.json").write_text("corrupt")
        other = create(self.store, self.flow)
        self.assertFalse(other["invalidated"])
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_corrupt_queued_plan_is_quarantined_without_blocking_catalog_or_worker(self):
        self.store.submit(self.key, submission(self.store, self.key))
        path = self.store.directory(self.key) / "plan.json"
        original = path.read_bytes()
        path.write_text("corrupt")
        other = create(self.store, self.flow)
        catalog = self.store.catalog(self.flow["flow_id"], "problem")
        self.assertEqual([w["scope_id"] for w in catalog["workspaces"]], [other["scope_id"]])
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        path.write_bytes(original)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_corrupt_orphan_ledger_is_interrupted_without_blocking_recovery(self):
        a = self.store.submit(self.key, submission(self.store, self.key))
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        path = self.store.directory(self.key) / a["attempt_id"] / "checks/lab.sqlite3"
        path.parent.mkdir(parents=True)
        path.write_text("corrupt")
        self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        row = self.store.report(self.key)
        self.assertEqual(row["attempts"][0]["state"], "INTERRUPTED")
        self.assertTrue(row["attempts"][0]["invalidated"])
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_global_lock_and_redirected_record_fail_closed(self):
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once()
        path = self.store.directory(self.key) / "record.json"
        outside = self.root / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.report(self.key)

    def test_scanner_cannot_quarantine_an_unpublished_workspace_during_creation(self):
        original = self.store.save
        observed = []

        def publish(row):
            directory = self.store.directory(row["scope_id"])
            if not (directory / "record.json").exists():
                scanner = ProofStepStore(self.root)
                with self.assertRaises(WorkerBusy):
                    list(scanner.records())
                self.assertFalse((directory / "invalidated.json").exists())
                observed.append(row["scope_id"])
            original(row)

        with patch.object(self.store, "save", publish):
            scope = create(self.store, self.flow)
        self.assertEqual(observed, [scope["scope_id"]])
        self.assertFalse(self.store.report(scope["scope_id"])["invalidated"])
        self.assertIn(scope["scope_id"], [path.name for path, _ in self.store.records()])

    def test_recovery_cancels_orphan_child_without_retry_or_promotion(self):
        raw = submission(self.store, self.key)
        a = self.store.submit(self.key, raw)
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        directory = self.store.directory(self.key) / a["attempt_id"]
        directory.mkdir()
        checker = PrefixChecks(self.root, directory, create=True)
        p = a["plan"]["child_plan"]
        child = checker.submit(
            {
                "request_id": "orphan",
                "request": p["request"],
                "plan_fingerprint": p["plan_fingerprint"],
                "reviewer": "QA",
                "confirmed": True,
            }
        )
        self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        self.assertEqual(checker.report(child["run_id"])["state"], "CANCELLED")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")
        with patch.object(self.store, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_http_csrf_preview_confirmation_get_and_cancel_guards(self):
        with running_server(self.root) as server:
            endpoint = "/v1/proof-steps/" + self.key
            for route in ("/proof-steps.js", "/proof-steps.css", endpoint):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    endpoint + "/preview",
                    {"parent_id": None, "tactic": None},
                    headers={"X-MRA-CSRF": "wrong"},
                )[0],
                403,
            )
            raw = submission(self.store, self.key)
            status, a, _ = request(server, "POST", endpoint + "/submit", raw)
            self.assertEqual(status, 202)
            self.assertEqual(request(server, "GET", endpoint + "?execute=1")[0], 400)
            self.assertEqual(
                request(server, "POST", endpoint + "/cancel", {"attempt_id": a["attempt_id"]})[1][
                    "attempts"
                ][0]["state"],
                "CANCELLED",
            )

    def test_cli_strict_input_and_readonly_report(self):
        from mra import __main__ as cli

        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(sys, "argv", ["mra", "proof-steps-report", self.key]),
            patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["math_verdict"], "UNKNOWN")
        path = self.root / "invalid.json"
        path.write_text('{"x":1,"x":2}')
        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(sys, "argv", ["mra", "proof-steps-preview", "--input", str(path)]),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli.main(), 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = ProofStepStore(ROOT)
        cls.out = ROOT / "artifacts/proof-steps-20261027-01" / ("real-suite-" + uuid.uuid4().hex)
        cls.out.mkdir()
        cls.project = parent(ROOT)
        cls.flow = create_flow(cls.store.flows, cls.project, single())
        cls.constant = deepcopy(single())
        cls.constant[0]["target"].update(
            title="零的自反性",
            original="自然数零等于自身。",
            variables=[],
            assumptions=[],
            goal={"eq": [{"nat": 0}, {"nat": 0}]},
        )
        cls.constant_flow = create_flow(cls.store.flows, cls.project, cls.constant)

    def save(self, name, row):
        (self.out / (name + ".json")).write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n"
        )

    def step(self, key, parent_id=None, tactic=None):
        self.store.submit(key, submission(self.store, key, parent_id, tactic))
        self.store.work_once()
        return self.store.report(key)

    def test_actual_open_goals_failed_tactic_branch_and_complete_independent_proof(self):
        scope = create(self.store, self.flow)
        key = scope["scope_id"]
        row = self.step(key)
        self.assertEqual(row["attempts"][-1]["observation"]["outcome"], "OPEN")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        initial = row["attempts"][-1]["attempt_id"]
        row = self.step(key, initial, "intros")
        introduced = row["attempts"][-1]["attempt_id"]
        row = self.step(key, introduced, "omega")
        self.assertEqual(row["attempts"][-1]["state"], "REJECTED")
        self.assertNotIn(row["attempts"][-1]["attempt_id"], row["accepted"])
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.save("failed-branch", row)
        row = self.step(key, introduced, "dvd-gcd")
        self.assertEqual(len(row["attempts"][-1]["observation"]["checkpoints"][-1]["goals"]), 2)
        row = self.step(key, row["attempts"][-1]["attempt_id"], "dvd-add")
        self.assertEqual(len(row["attempts"][-1]["observation"]["checkpoints"][-1]["goals"]), 3)
        row = self.step(key, row["attempts"][-1]["attempt_id"], "all-assumptions")
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("PROVED", "E4"))
        self.assertEqual(
            [len(c["goals"]) for c in row["attempts"][-1]["observation"]["checkpoints"]],
            [1, 1, 2, 3, 0],
        )
        child = row["checks"][row["attempts"][-1]["attempt_id"]]
        self.assertEqual(child["result"]["replay"]["audit"]["mode"], "data-only-kernel-replay-v1")
        self.assertTrue(child["alignment"]["confirmed"])
        self.assertEqual(self.store.flows.report(self.flow["flow_id"])["math_verdict"], "UNKNOWN")
        self.save("proved", row)

    def test_cancel_after_actual_closed_candidate_and_restored_open_artifact_stay_unknown(self):
        scope = create(self.store, self.constant_flow)
        key = scope["scope_id"]
        row = self.step(key)
        initial = row["attempts"][-1]
        a = self.store.submit(key, submission(self.store, key, initial["attempt_id"], "omega"))
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            result = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "LEAN_RUNNING":
                self.assertEqual(result["returncode"], 0)
                self.store.cancel(key, a["attempt_id"])
            return result

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once()
        row = self.store.report(key)
        self.assertEqual(
            (row["attempts"][-1]["state"], row["math_verdict"]), ("CANCELLED", "UNKNOWN")
        )
        self.save("cancelled", row)
        checker = PrefixChecks(ROOT, self.store.directory(key) / initial["attempt_id"])
        path = checker.directory(initial["run_id"]) / "candidate/stdout.log"
        before = path.read_bytes()
        path.write_bytes(before + b"\nchanged\n")
        self.assertNotIn(initial["attempt_id"], self.store.report(key)["accepted"])
        path.write_bytes(before)
        row = self.store.report(key)
        self.assertNotIn(initial["attempt_id"], row["accepted"])
        self.save("restored-still-invalid", row)

    def test_supervisor_exit_after_actual_replay_does_not_promote_or_retry(self):
        scope = create(self.store, self.constant_flow)
        key = scope["scope_id"]
        row = self.step(key)
        a = self.store.submit(
            key, submission(self.store, key, row["attempts"][-1]["attempt_id"], "omega")
        )
        script = """import os
from pathlib import Path
from unittest.mock import patch
from mra.proof_steps import ProofStepStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    if kwargs.get('phase')=='REPLAY_RUNNING':
        assert result['returncode']==0,result
        os._exit(73)
    return result
with patch.object(NativeLeanBackend,'_run_worker',stop):
    ProofStepStore(Path.cwd()).work_once()
"""
        process = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=240
        )
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        with patch.object(self.store, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        row = self.store.report(key)
        self.assertEqual(
            (row["attempts"][-1]["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        checker = PrefixChecks(ROOT, self.store.directory(key) / a["attempt_id"])
        self.assertIn(
            "MRA_REPLAY:",
            (checker.directory(row["attempts"][-1]["run_id"]) / "replay/stdout.log").read_text(),
        )
        self.save("interrupted", row)
