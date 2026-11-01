"""Compact failure diagnostics: original targets and genuinely executed controls."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_recursion_programs as core
from mra.compact_recursion import (
    MODE,
    FILE,
    CompactRecursionStore,
    compact,
    diagnostics,
    manifest,
    prepare,
)
from mra.domain import canonical
from mra.function_dynamics_checks import verified
from mra.recursion_programs import RecursionStore, prepare as legacy_prepare
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/compact-recursion-diagnostics-20261029-01"


def upgrade(raw):
    return {**deepcopy(raw), "schema": MODE}


def run(store, raw, label):
    row = store.submit(
        {
            "request_id": label + "-" + uuid.uuid4().hex,
            "request": raw,
            "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
            "reviewer": "MRA078 original statement, full premises and diagnostic profile reviewer",
            "confirmed": True,
        }
    )
    store.work_once(row["run_id"])
    return store.report(row["run_id"])


class ContractTests(unittest.TestCase):
    def setUp(self):
        core.ContractTests.setUp(self)
        p = self.root / FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / FILE, p)
        self.raw = upgrade(core.request(core.cases()[0]["target"], {"method": "grind"}))
        self.store = CompactRecursionStore(self.root)

    def test_only_fixed_generated_grind_lines_change_and_complete_target_is_identical(self):
        old_raw = {**deepcopy(self.raw), "schema": "structure-recursion-program-v1"}
        old = legacy_prepare(self.root, old_raw)
        new = self.store.preview(self.raw)
        self.assertEqual(new["formal_statement"], old["formal_statement"])
        self.assertEqual(new["target"], old["target"])
        self.assertEqual(
            new["proof_source"].replace("all_goals (grind -verbose)", "all_goals grind"),
            old["proof_source"],
        )
        self.assertEqual(new["request"], self.raw)
        self.assertEqual(
            compact("have grind := 1\n  all_goals grind\nall_goals grind_extra"),
            "have grind := 1\n  all_goals (grind -verbose)\nall_goals grind_extra",
        )

    def test_compacts_local_helpers_and_main_program_without_new_assumptions(self):
        original = core.proposal(self.root, core.cases()[1]["target"])["candidates"][0]["request"]
        before = deepcopy(original)
        new = prepare(self.root, upgrade(original))
        old = legacy_prepare(self.root, original)
        self.assertEqual(new["assumptions"], old["assumptions"])
        self.assertEqual(new["proof_source"], compact(old["proof_source"]))
        self.assertEqual(
            new["local_lemmas"][0]["proof_source"], compact(old["local_lemmas"][0]["proof_source"])
        )
        self.assertEqual(original, before)

    def test_explicit_opt_in_strict_schema_budget_and_no_raw_tactics(self):
        for change in (
            {"schema": "structure-recursion-program-v1"},
            {"profile": "verbose"},
            {"timeout_seconds": True},
            {"timeout_seconds": 19},
            {"program": {"lemmas": [], "proof": {"method": "grind", "verbose": True}}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare(self.root, {**self.raw, **change})
        with self.assertRaises(ValueError):
            legacy_prepare(self.root, self.raw)

    def test_preview_has_no_lean_call_and_keeps_original_limits(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            p = prepare(self.root, self.raw)
        self.assertEqual(
            (p["math_verdict"], p["evidence_level"], p["model_calls"]), ("UNKNOWN", "E0", 0)
        )
        self.assertEqual(
            p["budget"],
            legacy_prepare(self.root, {**self.raw, "schema": "structure-recursion-program-v1"})[
                "budget"
            ],
        )
        self.assertEqual(p["diagnostic_profile"]["sandbox_file_limit_bytes"], 1048576)
        self.assertNotEqual(self.store.path, RecursionStore(self.root).path)
        with self.assertRaises(ValueError):
            CompactRecursionStore(self.root, base=self.root / "artifacts/structure-recursion")

    def test_profile_change_isolated_from_legacy_and_restoration_does_not_revive(self):
        before = core.manifest(self.root)
        p = self.store.preview(self.raw)
        row = self.store.submit(
            {
                "request_id": "NONEXECUTABLE-drift",
                "request": self.raw,
                "plan_fingerprint": p["plan_fingerprint"],
                "reviewer": "NONEXECUTABLE local fixture",
                "confirmed": True,
            }
        )
        source = self.root / FILE
        data = source.read_bytes()
        source.write_bytes(data + b"\n")
        self.assertEqual(core.manifest(self.root), before)
        self.assertNotEqual(manifest(self.root), p["source_manifest"])
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        source.write_bytes(data)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            self.store.work_once(row["run_id"])
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")

    def test_named_confirmation_idempotence_and_queued_cancellation(self):
        p = self.store.preview(self.raw)
        env = {
            "request_id": "NONEXECUTABLE-confirm",
            "request": self.raw,
            "plan_fingerprint": p["plan_fingerprint"],
            "reviewer": "NONEXECUTABLE protocol fixture",
            "confirmed": True,
        }
        with self.assertRaises(ValueError):
            self.store.submit({**env, "confirmed": False})
        row = self.store.submit(env)
        self.assertEqual(self.store.submit(env)["run_id"], row["run_id"])
        cancelled = self.store.cancel(row["run_id"])
        self.assertEqual((cancelled["state"], cancelled["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        self.assertEqual(self.store.catalog()["mode"], MODE)

    def test_invalidated_diagnostics_never_read_or_reuse_stale_logs(self):
        with patch.object(Path, "open", side_effect=AssertionError("No stale log read")):
            d = diagnostics(
                {"invalidated": True, "files": {"candidate/stdout.log": "fake"}},
                self.root / "missing",
            )
        self.assertEqual(d["status"], "NOT_AVAILABLE")
        self.assertIsNone(d["first_error"])

    def test_diagnostic_prefix_is_hash_bound_and_late_changes_are_hidden(self):
        directory = self.root / "artifacts/log-fixture"
        folder = directory / "candidate"
        folder.mkdir(parents=True)
        data = b"Candidate.lean:1:1: error: grind failed\n" + b"x" * 7000
        (folder / "stdout.log").write_bytes(data)
        (folder / "stderr.log").write_bytes(b"")
        import hashlib

        row = {
            "invalidated": False,
            "state": "REJECTED",
            "result": {"candidate": {}, "replay": None},
            "files": {
                "candidate/stdout.log": hashlib.sha256(data).hexdigest(),
                "candidate/stderr.log": hashlib.sha256(b"").hexdigest(),
            },
        }
        self.assertIn("grind", diagnostics(row, directory)["first_error"])
        (folder / "stdout.log").write_bytes(b"changed")
        self.assertIsNone(diagnostics(row, directory)["first_error"])

    def test_report_busy_propagates_without_invalidating_a_queued_task(self):
        p = self.store.preview(self.raw)
        row = self.store.submit(
            {
                "request_id": "NONEXECUTABLE-busy",
                "request": self.raw,
                "plan_fingerprint": p["plan_fingerprint"],
                "reviewer": "NONEXECUTABLE protocol fixture",
                "confirmed": True,
            }
        )
        with (
            patch.object(self.store, "gate", side_effect=WorkerBusy("held")),
            self.assertRaises(WorkerBusy),
        ):
            self.store.report(row["run_id"])
        self.assertFalse(self.store.read(row["run_id"])["invalidated"])


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine compact-diagnostic comparison"
)
class RealTests(unittest.TestCase):
    def test_fixed_programs_preserve_proof_outcomes_and_complete_failed_diagnostics(self):
        area = OUT / ("real-" + uuid.uuid4().hex)
        area.mkdir()
        old = RecursionStore(ROOT, base=area / "verbose")
        new = CompactRecursionStore(ROOT, base=area / "compact")
        prior = json.loads(
            (
                ROOT
                / "artifacts/export-lemma-matching-20261029-01/real-560dd371b5f4479e999162f5dbfaf4b4/declared-plan.json"
            ).read_text()
        )
        positive = json.loads(
            (
                ROOT
                / "artifacts/structure-recursion/runs/mathlib-51884beea8114bb1b5c2e8fe3d043608/plan.json"
            ).read_text()
        )["request"]
        bad = core.request(core.cases()[0]["target"])
        bad["target"]["goal"] = {"eq": [{"var": "x"}, {"var": "x"}]}
        bad["program"]["lemmas"] = [
            {
                "name": "unused_false",
                "goal": {"eq": [{"nat": 0}, {"nat": 1}]},
                "proof": {"method": "rfl"},
            }
        ]
        requests = [
            ("two-lemmas-failure", prior["items"][0]["without_import"]["candidates"][0]["request"]),
            ("two-layer-failure", prior["items"][2]["without_import"]["candidates"][0]["request"]),
            ("positive-two-helper", positive),
            ("unused-false-helper", bad),
        ]
        declared = [{"id": key, "old": raw, "new": upgrade(raw)} for key, raw in requests]

        def save(n, v):
            (area / n).write_bytes(canonical(v) + b"\n")

        save(
            "declared-plan.json",
            {
                "items": declared,
                "author_visible": True,
                "purpose": "Diagnostic completeness at fixed file limits, not a mathematical solve-rate comparison",
                "source_tasks_seen_before": True,
                "worker_seconds_per_complete_check": 20,
                "checks_per_case_and_arm": 1,
                "replaces_prior_results": False,
            },
        )
        results = []
        for item in declared:
            for arm, store in (("old", old), ("new", new)):
                row = run(store, item[arm], item["id"])
                results.append(
                    {
                        "case_id": item["id"],
                        "arm": arm,
                        "store": str(store.base.relative_to(ROOT)),
                        "check": row,
                    }
                )
                save("results.json", results)
                if item["id"] == "positive-two-helper":
                    verified(row)
                else:
                    self.assertEqual(row["math_verdict"], "UNKNOWN")
                if arm == "new" and item["id"].endswith("failure"):
                    self.assertEqual(row["state"], "REJECTED")
                    self.assertIn("grind", row["execution_diagnostics"]["first_error"])
                    self.assertEqual(row["execution_diagnostics"]["status"], "CANDIDATE_REJECTED")
                    self.assertNotIn("file too large", row["result"]["error"])
                    self.assertLess(
                        (store.directory(row["run_id"]) / "candidate/stdout.log").stat().st_size,
                        1048576,
                    )
        # Genuine replay can complete while the user cancels before promotion.
        with_original = NativeLeanBackend._run_worker
        raw = upgrade(positive)
        q = new.submit(
            {
                "request_id": "cancel-after-actual-replay",
                "request": raw,
                "plan_fingerprint": new.preview(raw)["plan_fingerprint"],
                "reviewer": "MRA078 real cancellation reviewer",
                "confirmed": True,
            }
        )

        def worker(backend, *args, **kw):
            result = with_original(backend, *args, **kw)
            if kw["phase"] == "REPLAY_RUNNING":
                self.assertEqual(result["returncode"], 0)
                new.cancel(q["run_id"])
            return result

        with patch.object(NativeLeanBackend, "_run_worker", worker):
            new.work_once(q["run_id"])
        cancelled = new.report(q["run_id"])
        self.assertEqual((cancelled["state"], cancelled["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        results.append(
            {
                "case_id": "cancel-after-replay",
                "arm": "new",
                "store": str(new.base.relative_to(ROOT)),
                "check": cancelled,
            }
        )
        save("results.json", results)
        save(
            "summary.json",
            {
                "all_passed": True,
                "complete_checks": len(results),
                "comparison_cases": len(declared),
                "mathematical_capability_claim": False,
                "model_calls": 0,
            },
        )
