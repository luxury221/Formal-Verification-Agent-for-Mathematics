"""Authored evaluation gates and actual candidate/replay diagnostics, without model claims."""

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

from mra.domain import canonical
from mra.mathlib_evaluation import (
    DATASET,
    EvaluationLab,
    _directory,
    _json,
    _lease,
    _write_report,
    evaluation_catalog,
    evaluation_plan,
    read_evaluation,
    run_evaluation,
)
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def archive_test_directory(path):
    destination = ROOT / "artifacts/mathlib-evaluation-tests" / uuid.uuid4().hex
    destination.parent.mkdir(exist_ok=True)
    assert path.parent == ROOT / "artifacts/mathlib-evaluations"
    shutil.move(str(path), destination)


class PlanTests(unittest.TestCase):
    def test_all_inputs_budget_and_visible_authorship_are_explicit(self):
        plan = evaluation_plan(ROOT)
        self.assertEqual(len(plan["cases"]), 8)
        self.assertEqual(sum(c["preflight"]["supported"] for c in plan["cases"]), 6)
        self.assertEqual(plan["budget"]["maximum_worker_seconds"], 240)
        self.assertEqual(plan["budget"]["automatic_retries"], 0)
        self.assertIsNone(plan["model_solve_rate"])
        self.assertIsNone(plan["alignment_accuracy"])
        self.assertFalse(plan["independent_blind_evaluation"])
        self.assertFalse(plan["training_use"])
        self.assertTrue(all(c["original"] and c["request"]["proof"] for c in plan["cases"]))

    def test_splits_do_not_overlap_families_and_keep_failure_cases(self):
        development = evaluation_plan(ROOT, "development")
        regression = evaluation_plan(ROOT, "regression")
        self.assertEqual((len(development["cases"]), len(regression["cases"])), (2, 6))
        self.assertFalse(
            {c["family"] for c in development["cases"]} & {c["family"] for c in regression["cases"]}
        )
        self.assertEqual(
            sum(c["expected_run_status"] != "COMPLETED" for c in regression["cases"]), 5
        )
        with self.assertRaises(ValueError):
            evaluation_plan(ROOT, "independent-blind")

    def test_nonuniform_budgets_duplicate_families_and_unknown_fields_are_rejected(self):
        dataset = _json(ROOT / DATASET)
        variants = []
        for budget in (True, 0, 31, "30"):
            value = deepcopy(dataset)
            value["cases"][0]["request"]["timeout_seconds"] = budget
            variants.append(value)
        duplicate = deepcopy(dataset)
        duplicate["cases"][1]["family"] = duplicate["cases"][0]["family"]
        variants.append(duplicate)
        variants.append({**dataset, "drop_failed_cases": True})
        for value in variants:
            with (
                self.subTest(value=value),
                patch("mra.mathlib_evaluation._json", return_value=value),
                self.assertRaises(ValueError),
            ):
                evaluation_plan(ROOT)

    def test_stale_unnamed_or_unconfirmed_execution_never_creates_a_run(self):
        plan = evaluation_plan(ROOT)
        for changes in ({"confirmed": False}, {"reviewer": " "}, {"plan_fingerprint": "stale"}):
            arguments = {
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "Gate test",
                "confirmed": True,
                **changes,
            }
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_evaluation(ROOT, **arguments)

    def test_global_worker_lease_blocks_evaluation_before_directory_creation(self):
        plan = evaluation_plan(ROOT)
        with TaskStore(ROOT).worker_lock(), self.assertRaises(WorkerBusy):
            run_evaluation(
                ROOT,
                plan_fingerprint=plan["plan_fingerprint"],
                reviewer="Lease test",
                confirmed=True,
            )

    def test_invalid_path_and_redirected_storage_or_oversized_json_are_rejected(self):
        for name in ("../x", "ml-bench-" + "A" * 32, "mathlib-" + "0" * 32):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _directory(ROOT, name)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as raw:
            base = Path(raw)
            file = base / "large.json"
            file.write_bytes(b" " * 100)
            with self.assertRaises(ValueError):
                _json(file, 10)
            alias = base / "alias.json"
            alias.symlink_to(file)
            with self.assertRaises(ValueError):
                _json(alias)
            (base / "artifacts").symlink_to(ROOT / "artifacts", target_is_directory=True)
            with self.assertRaises(ValueError):
                _directory(base)

    def test_cli_plan_keeps_existing_arithmetic_dataset_command_working(self):
        for arguments in (["mathlib-benchmark", "--plan"], ["dataset"]):
            result = subprocess.run(
                [sys.executable, "-m", "mra", *arguments],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=25,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
        rejected = subprocess.run(
            [sys.executable, "-m", "mra", "mathlib-benchmark"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(rejected.returncode, 2)

    def test_http_plan_assets_read_only_routes_and_strict_queries(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as raw:
            temporary = Path(raw)
            copy_web_inputs(temporary)
            with running_server(temporary) as server:
                server.root = ROOT
                status, plan, _ = request(
                    server, "GET", "/v1/mathlib-evaluations/plan?split=regression"
                )
                self.assertEqual(status, 200)
                self.assertEqual(len(plan["cases"]), 6)
                for route in (
                    "/v1/mathlib-evaluations/plan?split=all&split=regression",
                    "/v1/mathlib-evaluations/plan?unknown=1",
                ):
                    self.assertEqual(request(server, "GET", route)[0], 400)
                self.assertEqual(
                    request(server, "GET", "/v1/mathlib-evaluations/not-a-record")[0], 404
                )
                self.assertEqual(request(server, "POST", "/v1/mathlib-evaluations", {})[0], 404)
                for route in ("/mathlib-evaluation.js", "/mathlib-evaluation.css"):
                    self.assertEqual(request(server, "GET", route)[0], 200)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Requires actual sealed Lean runtime")
class RealEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = evaluation_plan(ROOT)
        argv = [
            sys.executable,
            "-m",
            "mra",
            "mathlib-benchmark",
            "--plan-fingerprint",
            cls.plan["plan_fingerprint"],
            "--reviewer",
            "repository-benchmark-acceptance-author",
            "--confirmed",
        ]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=1800)
        evidence = {
            "command": argv,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        (ROOT / "artifacts/mathlib-benchmark-real-cli.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
        )
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout)
        cls.report = json.loads(result.stdout)
        cls.evaluation_id = cls.report["evaluation_id"]
        cls.directory = _directory(ROOT, cls.evaluation_id)
        (ROOT / "artifacts/mathlib-benchmark-real-check.json").write_bytes(
            canonical(cls.report) + b"\n"
        )

    def clone(self):
        key = "ml-bench-" + uuid.uuid4().hex
        directory = _directory(ROOT, key)
        shutil.copytree(self.directory, directory)
        report = _json(directory / "report.json")
        report["evaluation_id"] = key
        _write_report(directory, report)
        self.addCleanup(archive_test_directory, directory)
        return key, directory

    def test_real_suite_includes_all_unknowns_in_the_denominator(self):
        result = read_evaluation(ROOT, self.evaluation_id)
        self.assertTrue(result["integrity_ok"])
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["summary"]["total"], 8)
        self.assertEqual(result["summary"]["proved"], 3)
        self.assertEqual(result["summary"]["unknown"], 5)
        self.assertEqual(
            result["summary"]["run_status_counts"],
            {"COMPLETED": 3, "REJECTED": 3, "PRECHECK_REJECTED": 2},
        )
        self.assertEqual(result["authored_plan_pass_rate"], 3 / 8)
        self.assertIsNone(result["model_solve_rate"])
        self.assertTrue(all(row["expected_status_matches"] for row in result["results"]))

    def test_every_positive_has_independent_real_candidate_and_replay(self):
        positives = [row for row in self.report["results"] if row["math_verdict"] == "PROVED"]
        self.assertEqual(len(positives), 3)
        for row in positives:
            result = row["checked"]["result"]
            self.assertNotEqual(result["candidate"]["pid"], result["replay"]["pid"])
            self.assertTrue(result["candidate"]["audit"]["type_matches"])
            self.assertTrue(result["replay"]["audit"]["type_matches"])
            self.assertFalse(result["replay"]["audit"]["candidate_module_imported"])
            self.assertEqual(row["evidence_level"], "E4")
            self.assertLessEqual(result["worker_seconds"], 30)

    def test_failed_proofs_are_unknown_and_do_not_claim_counterexamples(self):
        negatives = [row for row in self.report["results"] if row["run_status"] == "REJECTED"]
        self.assertEqual(len(negatives), 3)
        self.assertEqual({row["math_verdict"] for row in negatives}, {"UNKNOWN"})
        self.assertTrue(
            all(
                row["candidate_processes"] == 1 and row["replay_processes"] == 0
                for row in negatives
            )
        )
        self.assertTrue(all(row["evidence_level"] == "E0" for row in negatives))

    def test_unavailable_definitions_remain_original_and_have_zero_processes(self):
        blocked = [
            row for row in self.report["results"] if row["run_status"] == "PRECHECK_REJECTED"
        ]
        self.assertEqual(len(blocked), 2)
        self.assertTrue(
            all(
                row["run_id"] is None and row["candidate_processes"] == row["replay_processes"] == 0
                for row in blocked
            )
        )
        self.assertTrue(
            all(
                row["math_verdict"] == "UNKNOWN" and not row["preflight"]["formal_statement"]
                for row in blocked
            )
        )
        self.assertIn("gcd", blocked[0]["original"])

    def test_artifact_damage_suppresses_score_and_restore_does_not_revive_child(self):
        key, directory = self.clone()
        first = self.report["results"][0]["run_id"]
        proof = directory / "checks/runs" / first / "replay/proof.ndjson"
        original = proof.read_bytes()
        proof.write_bytes(original + b"\n")
        damaged = read_evaluation(ROOT, key)
        self.assertFalse(damaged["integrity_ok"])
        self.assertIsNone(damaged["authored_plan_pass_rate"])
        self.assertEqual(damaged["results"][0]["math_verdict"], "UNKNOWN")
        proof.write_bytes(original)
        self.assertFalse(read_evaluation(ROOT, key)["integrity_ok"])

    def test_report_plan_or_source_tampering_never_preserves_a_score(self):
        for name in ("report.json", "plan.json"):
            key, directory = self.clone()
            p = directory / name
            p.write_bytes(p.read_bytes() + b"\n")
            result = read_evaluation(ROOT, key)
            self.assertFalse(result["integrity_ok"])
            self.assertIsNone(result["authored_plan_pass_rate"])
        with patch("mra.mathlib_evaluation.evaluation_plan", return_value={}):
            self.assertIsNone(read_evaluation(ROOT, self.evaluation_id)["authored_plan_pass_rate"])

    def test_missing_replay_and_duplicate_or_missing_cases_are_unavailable(self):
        key, directory = self.clone()
        first = self.report["results"][0]["run_id"]
        (directory / "checks/runs" / first / "replay/proof.ndjson").unlink()
        self.assertFalse(read_evaluation(ROOT, key)["integrity_ok"])
        key, directory = self.clone()
        report = _json(directory / "report.json")
        report["results"].pop()
        _write_report(directory, report)
        self.assertFalse(read_evaluation(ROOT, key)["integrity_ok"])
        key, directory = self.clone()
        report = _json(directory / "report.json")
        report["results"][1] = report["results"][0]
        _write_report(directory, report)
        self.assertFalse(read_evaluation(ROOT, key)["integrity_ok"])

    def test_orphaned_execution_never_reports_a_completed_rate(self):
        key, directory = self.clone()
        report = _json(directory / "report.json")
        report["state"] = "RUNNING"
        _write_report(directory, report)
        result = read_evaluation(ROOT, key)
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertIsNone(result["authored_plan_pass_rate"])
        with _lease(directory):
            self.assertEqual(read_evaluation(ROOT, key)["state"], "RUNNING")
        rows = evaluation_catalog(ROOT)["runs"]
        self.assertEqual(
            next(row["state"] for row in rows if row["evaluation_id"] == key), "INTERRUPTED"
        )

    def test_interrupt_cleans_running_child_and_does_not_retry(self):
        plan = evaluation_plan(ROOT, "development")
        with patch.object(EvaluationLab, "execute", side_effect=KeyboardInterrupt):
            result = run_evaluation(
                ROOT,
                split="development",
                plan_fingerprint=plan["plan_fingerprint"],
                reviewer="Interruption fault injection",
                confirmed=True,
            )
        directory = _directory(ROOT, result["evaluation_id"])
        self.addCleanup(archive_test_directory, directory)
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertIsNone(result["authored_plan_pass_rate"])
        lab = EvaluationLab(ROOT, directory)
        self.assertEqual(lab.read(result["inflight_run_id"])["state"], "INTERRUPTED")

    def test_cli_report_and_http_detail_match_the_same_real_suite(self):
        result = subprocess.run(
            [sys.executable, "-m", "mra", "mathlib-benchmark-report", self.evaluation_id],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["summary"]["proved"], 3)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as raw:
            temporary = Path(raw)
            copy_web_inputs(temporary)
            with running_server(temporary) as server:
                server.root = ROOT
                status, body, _ = request(
                    server, "GET", "/v1/mathlib-evaluations/" + self.evaluation_id
                )
                self.assertEqual(status, 200)
                self.assertTrue(body["integrity_ok"])
                self.assertEqual(body["summary"]["total"], 8)
                status, catalog, _ = request(server, "GET", "/v1/mathlib-evaluations")
                self.assertEqual(status, 200)
                self.assertIn(self.evaluation_id, [row["evaluation_id"] for row in catalog["runs"]])
