"""Fixed tactics, unchanged mathematical target, real failures and lifecycle boundaries."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.lean_strategies import STRATEGIES, LeanStrategyStore, examples
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/lean-strategies-20261027-01"


def submit(store, value=None):
    value = value or examples()[0]
    plan = store.preview(value)
    return store.submit(
        {
            "request_id": "strategy-" + uuid.uuid4().hex,
            "request": value,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "Lean strategy QA",
            "confirmed": True,
        }
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        shutil.copyfile(ROOT / "mra/lean_strategies.py", self.root / "mra/lean_strategies.py")
        self.store = LeanStrategyStore(self.root)

    def test_preview_is_complete_fixed_and_never_executes(self):
        with patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean on preview")
        ):
            plan = self.store.preview(examples()[1])
        self.assertEqual(plan["request"], examples()[1])
        self.assertEqual(len(plan["assumptions"]), 2)
        self.assertEqual(plan["formal_statement"].count("→"), 2)
        self.assertEqual((plan["math_verdict"], plan["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(plan["budget"]["automatic_retries"], 0)
        self.assertEqual(plan["proof_source"], "by\n  intros\n  omega")
        self.assertEqual(self.store.list_runs(), [])

    def test_arbitrary_source_import_proof_unknown_strategy_and_wrong_domain_rejected(self):
        for changes in (
            {"strategy": "omega; sorry"},
            {"strategy": True},
            {"proof": "by exact 1"},
            {"imports": ["Mathlib"]},
            {"schema": "int"},
            {"timeout_seconds": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.preview({**examples()[0], **changes})

    def test_simp_is_only_the_versioned_rules_and_preview_cannot_mutate_catalog(self):
        value = self.store.preview(examples()[3])
        self.assertIn("simp_all only [", value["proof_source"])
        before = deepcopy(STRATEGIES)
        value["strategy"]["proof_source"] = "by sorry"
        value["source_manifest"]["strategies"]["omega"]["proof_source"] = "changed"
        self.assertEqual(STRATEGIES, before)
        self.assertEqual(set(self.store.catalog()["strategies"]), {"omega", "simp-gcd"})

    def test_unnamed_unconfirmed_stale_and_changed_request_never_queue(self):
        value = examples()[1]
        plan = self.store.preview(value)
        raw = {
            "request_id": "contract",
            "request": value,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "QA",
            "confirmed": True,
        }
        for changes in (
            {"reviewer": " "},
            {"confirmed": False},
            {"confirmed": 1},
            {"plan_fingerprint": "stale"},
            {"request": {**value, "assumptions": []}},
        ):
            with self.assertRaises(ValueError):
                self.store.submit({**raw, **changes})
        self.assertEqual(self.store.list_runs(), [])

    def test_named_confirmation_is_idempotent_but_changed_identity_is_rejected(self):
        plan = self.store.preview(examples()[0])
        data = {
            "request_id": "same",
            "request": examples()[0],
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "QA",
            "confirmed": True,
        }
        first = self.store.submit(data)
        self.assertEqual(self.store.submit(data)["run_id"], first["run_id"])
        with self.assertRaises(ValueError):
            self.store.submit({**data, "reviewer": "Other"})

    def test_cancelled_queued_run_never_executes_or_gets_a_verdict(self):
        value = submit(self.store)
        cancelled = self.store.cancel(value["run_id"])
        with patch.object(
            LeanStrategyStore, "execute", side_effect=AssertionError("No cancelled work")
        ):
            self.assertEqual(self.store.work_once(value["run_id"])["worker_state"], "IDLE")
        self.assertEqual((cancelled["state"], cancelled["math_verdict"]), ("CANCELLED", "UNKNOWN"))

    def test_global_worker_lock_prevents_parallel_check(self):
        value = submit(self.store)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(value["run_id"])
        self.assertEqual(self.store.report(value["run_id"])["state"], "QUEUED")

    def test_recovery_never_executes_or_promotes_unfinished_work(self):
        value = submit(self.store)
        with self.store.transaction() as db:
            row = self.store.read(value["run_id"], db)
            row.update(state="RUNNING", stage="REPLAY_RUNNING")
            self.store.save(db, row)
        with patch.object(LeanStrategyStore, "execute", side_effect=AssertionError("No resume")):
            result = self.store.recover()
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        self.assertEqual(result["interrupted"], [value["run_id"]])
        self.assertEqual(self.store.report(value["run_id"])["math_verdict"], "UNKNOWN")

    def test_adapter_drift_is_persistent_even_after_source_restoration(self):
        value = submit(self.store)
        path = self.root / "mra/lean_strategies.py"
        before = path.read_bytes()
        path.write_bytes(before + b"\n")
        self.assertTrue(self.store.report(value["run_id"])["invalidated"])
        path.write_bytes(before)
        self.assertTrue(self.store.report(value["run_id"])["invalidated"])

    def test_missing_premise_is_preserved_and_inconsistent_premises_are_not_silently_removed(self):
        value = deepcopy(examples()[2])
        value["assumptions"] = []
        self.assertEqual(self.store.preview(value)["assumptions"], [])
        a = {"var": "a"}
        value["assumptions"] = [
            {"name": "impossible", "formula": {"le": [{"add": [a, {"nat": 1}]}, a]}}
        ]
        plan = self.store.preview(value)
        self.assertEqual(plan["request"]["assumptions"], value["assumptions"])
        self.assertTrue(any("consistency" in s for s in plan["limitations"]))

    def test_storage_redirect_and_empty_or_unsupported_formula_are_rejected(self):
        path = self.root / "artifacts/lean-strategies/lab.sqlite3"
        path.unlink()
        target = self.root / "outside.sqlite3"
        target.write_text("not a ledger")
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.list_runs()
        for goal in ({}, {"not": [{"eq": [{"nat": 0}, {"nat": 1}]}]}):
            with self.assertRaises(ValueError):
                self.store.preview({**examples()[0], "goal": goal})

    def test_http_requires_csrf_and_confirmation_and_returns_full_feedback_records(self):
        with running_server(self.root) as server:
            for route in (
                "/strategies",
                "/lean-strategies.js",
                "/lean-strategies.css",
                "/v1/lean-strategies/catalog",
            ):
                self.assertEqual(request(server, "GET", route)[0], 200)
            status, plan, _ = request(server, "POST", "/v1/lean-strategies/preview", examples()[1])
            self.assertEqual(status, 200)
            data = {
                "request_id": "http",
                "request": plan["request"],
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "QA",
                "confirmed": True,
            }
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/lean-strategies/runs",
                    data,
                    headers={"X-MRA-CSRF": "wrong"},
                )[0],
                403,
            )
            self.assertEqual(
                request(server, "POST", "/v1/lean-strategies/runs", {**data, "confirmed": False})[
                    0
                ],
                400,
            )
            status, row, _ = request(server, "POST", "/v1/lean-strategies/runs", data)
            self.assertEqual(status, 201)
            self.assertEqual(
                request(
                    server, "POST", "/v1/lean-strategies/runs/" + row["run_id"] + "/cancel", {}
                )[1]["state"],
                "CANCELLED",
            )
            self.assertEqual(request(server, "GET", "/v1/lean-strategies/runs?execute=1")[0], 404)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    def test_all_authored_examples_real_proofs_and_false_claim_remain_unknown(self):
        store = LeanStrategyStore(ROOT)
        runs = []
        for i, value in enumerate(examples()):
            row = submit(store, value)
            store.work_once(row["run_id"])
            result = store.report(row["run_id"])
            runs.append(result)
            (OUT / "real-runs.json").write_text(
                json.dumps(runs, ensure_ascii=False, indent=2) + "\n"
            )
            expected = ("COMPLETED", "PROVED", "E4") if i < 5 else ("REJECTED", "UNKNOWN", "E0")
            self.assertEqual(
                (result["state"], result["math_verdict"], result["evidence_level"]),
                expected,
                result["result"],
            )
            if i < 5:
                self.assertTrue(result["result"]["candidate"]["audit"]["type_matches"])
                self.assertEqual(
                    result["result"]["replay"]["audit"]["mode"], "data-only-kernel-replay-v1"
                )
            else:
                self.assertIn("omega", result["result"]["error"])
        revoked = store.revoke(
            runs[4]["run_id"],
            {
                "reviewer": "Lean strategy QA",
                "reason": "Actual revocation boundary check",
                "confirmed": True,
            },
        )
        self.assertEqual(revoked["math_verdict"], "UNKNOWN")
        self.assertTrue(revoked["invalidated"])
        (OUT / "real-revocation.json").write_text(
            json.dumps(revoked, ensure_ascii=False, indent=2) + "\n"
        )

    def test_cancel_after_actual_candidate_never_promotes_partial_proof(self):
        store = LeanStrategyStore(ROOT)
        row = submit(store)
        original = NativeLeanBackend._run_worker

        def cancel_after_candidate(backend, *args, **kwargs):
            result = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "LEAN_RUNNING":
                self.assertEqual(result["returncode"], 0)
                store.cancel(row["run_id"])
            return result

        with patch.object(NativeLeanBackend, "_run_worker", cancel_after_candidate):
            store.work_once(row["run_id"])
        result = store.report(row["run_id"])
        self.assertEqual((result["state"], result["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        self.assertIsNone(result["result"]["replay"])
        (OUT / "real-cancellation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )

    def test_supervisor_exit_after_actual_replay_is_interrupted_without_retry(self):
        store = LeanStrategyStore(ROOT)
        row = submit(store)
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.lean_strategies import LeanStrategyStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    if kwargs.get('phase')=='REPLAY_RUNNING':
        assert result['returncode']==0,result
        os._exit(73)
    return result
with patch.object(NativeLeanBackend,'_run_worker',stop):
    LeanStrategyStore(Path.cwd()).work_once(sys.argv[1])
"""
        result = subprocess.run(
            [sys.executable, "-c", script, row["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        recovered = store.recover()
        self.assertIn(row["run_id"], recovered["interrupted"])
        with patch.object(LeanStrategyStore, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(store.work_once(row["run_id"])["worker_state"], "IDLE")
        checked = store.report(row["run_id"])
        self.assertEqual((checked["state"], checked["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        self.assertTrue((store.directory(row["run_id"]) / "replay/stdout.log").is_file())
        (OUT / "real-interruption.json").write_text(
            json.dumps(checked, ensure_ascii=False, indent=2) + "\n"
        )
