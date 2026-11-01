"""Finite semantics are untrusted; refutations require actual candidate/replay evidence."""

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

from mra.counterexamples import (
    SEARCH_SCHEMA,
    CounterexampleChecks,
    Evaluator,
    _directory,
    catalog,
    prepare,
    preview,
    report,
    run,
    search,
)
from mra.search_evaluation import _atomic, _envelope
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import request, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/counterexample-20261027-01"


def cases():
    return json.loads((ROOT / "evals/number-theory-search-v1/cases.json").read_text())["cases"]


def experiment(value, root=ROOT, bound=4, count=1000):
    return search(
        {
            "schema": SEARCH_SCHEMA,
            "target": value,
            "budget": {"max_value": bound, "max_assignments": count},
        },
        root,
    )


def execute(root, value):
    return run(
        root,
        value["counterexample_id"],
        plan_fingerprint=value["plan"]["plan_fingerprint"],
        reviewer="Counterexample QA",
        confirmed=True,
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        shutil.copyfile(ROOT / "mra/counterexamples.py", self.root / "mra/counterexamples.py")

    def candidate(self):
        return experiment(cases()[-4]["target"], self.root)["candidate"]

    def test_authored_false_cases_have_complete_untrusted_witnesses(self):
        for case in cases()[-4:-1]:
            result = experiment(case["target"], self.root)
            self.assertEqual((result["math_verdict"], result["evidence_level"]), ("UNKNOWN", "E0"))
            self.assertEqual((result["lean_calls"], result["model_calls"]), (0, 0))
            self.assertEqual(set(result["witness"]), set(case["target"]["variables"]))
            self.assertTrue(all(result["instance"]["assumptions"]))
            self.assertFalse(result["instance"]["goal"])
            self.assertEqual(
                {k: v for k, v in result["candidate"].items() if k not in ("schema", "witness")},
                {k: v for k, v in case["target"].items() if k != "schema"},
            )

    def test_true_targets_stay_unknown_after_finite_exhaustion(self):
        for case in (cases()[4], cases()[-1]):
            result = experiment(case["target"], self.root)
            self.assertIsNone(result["candidate"])
            self.assertEqual(result["math_verdict"], "UNKNOWN")
            self.assertEqual(result["stop_reason"], "finite_domain_exhausted")

    def test_zero_divisibility_coprime_and_truncated_subtraction_semantics(self):
        evaluator = Evaluator()

        def n(x):
            return {"nat": x}

        self.assertTrue(evaluator.formula({"dvd": [n(0), n(0)]}, {}))
        self.assertFalse(evaluator.formula({"dvd": [n(0), n(1)]}, {}))
        self.assertTrue(evaluator.formula({"dvd": [n(3), n(0)]}, {}))
        self.assertFalse(evaluator.formula({"coprime": [n(0), n(0)]}, {}))
        self.assertTrue(evaluator.formula({"coprime": [n(0), n(1)]}, {}))
        self.assertEqual(evaluator.nat({"sub": [n(1), n(2)]}, {}), 0)
        self.assertTrue(
            evaluator.formula({"iff": [{"eq": [n(0), n(1)]}, {"eq": [n(1), n(2)]}]}, {})
        )

    def test_assignment_bound_and_empty_variable_tuple_are_explicit(self):
        value = deepcopy(cases()[-2]["target"])
        result = experiment(value, self.root, bound=0)
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["stats"]["assignments"], 1)
        result = experiment(value, self.root, bound=4, count=1)
        self.assertEqual(result["stop_reason"], "assignment_limit")
        value = deepcopy(cases()[-3]["target"])
        value["variables"] = []
        result = experiment(value, self.root, bound=0)
        self.assertEqual(result["witness"], {})
        self.assertEqual(result["stats"]["assignments"], 1)

    def test_preview_preserves_original_and_checks_its_exact_negation(self):
        value = preview(self.root, self.candidate())
        self.assertEqual(value["direction"], "refute")
        self.assertEqual(
            value["formal_statement"], "¬ (" + value["original_formal_statement"] + ")"
        )
        self.assertEqual(value["observations"]["assumptions"], [True])
        self.assertEqual(value["budget"]["candidate_processes"], 1)
        self.assertIn("by decide", value["proof_source"])

    def test_overflow_and_experiment_time_or_operation_limits_remain_unknown(self):
        value = deepcopy(cases()[-3]["target"])
        value["variables"] = []
        expression = {"nat": 1000000}
        for _ in range(13):
            expression = {"mul": [expression, {"nat": 1000000}]}
        value["goal"] = {"eq": [expression, {"nat": 0}]}
        result = experiment(value, self.root)
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["stats"]["overflow_assignments"], 1)
        for limit in ("WALL_SECONDS", "MAX_OPERATIONS"):
            with patch("mra.counterexamples." + limit, 0):
                result = experiment(cases()[-1]["target"], self.root)
            self.assertEqual(result["math_verdict"], "UNKNOWN")
            self.assertIsNone(result["candidate"])
            self.assertIn(result["stop_reason"], ("wall_time_limit", "operation_limit"))

    def test_wrong_witness_missing_premise_bool_and_proof_input_are_rejected(self):
        candidate = self.candidate()
        for changes in (
            {"witness": {}},
            {"witness": {"d": 1, "a": 1, "b": 1}},
            {"witness": {"d": 0, "a": 1, "b": 1}},
            {"witness": {"d": False, "a": 0, "b": 1}},
            {"proof": "by sorry"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                preview(self.root, {**candidate, **changes})

    def test_budget_types_unsupported_input_and_proof_in_target_are_rejected(self):
        raw = {
            "schema": SEARCH_SCHEMA,
            "target": cases()[0]["target"],
            "budget": {"max_value": 4, "max_assignments": 1000},
        }
        for budget in (
            {"max_value": True, "max_assignments": 10},
            {"max_value": 33, "max_assignments": 10},
            {"max_value": 1, "max_assignments": 0},
        ):
            with self.assertRaises(ValueError):
                search({**raw, "budget": budget}, self.root)
        with self.assertRaises(ValueError):
            search({**raw, "target": {**raw["target"], "proof": {"hyp": "h"}}}, self.root)

    def test_prepare_never_executes_and_confirmation_errors_do_not_create_checks(self):
        with patch.object(CounterexampleChecks, "execute", side_effect=AssertionError("No Lean")):
            value = prepare(self.root, self.candidate())
        self.assertEqual((value["state"], value["math_verdict"]), ("PREPARED", "UNKNOWN"))
        for changes in (
            {"confirmed": False},
            {"confirmed": 1},
            {"reviewer": " "},
            {"plan_fingerprint": "stale"},
        ):
            kwargs = {
                "plan_fingerprint": value["plan"]["plan_fingerprint"],
                "reviewer": "QA",
                "confirmed": True,
                **changes,
            }
            with self.assertRaises(ValueError):
                run(self.root, value["counterexample_id"], **kwargs)
        self.assertFalse((_directory(self.root, value["counterexample_id"]) / "checks").exists())

    def test_worker_lock_rejects_start_and_creation_fault_does_not_retry(self):
        value = prepare(self.root, self.candidate())
        with TaskStore(self.root).worker_lock(), self.assertRaises(WorkerBusy):
            execute(self.root, value)
        self.assertEqual(report(self.root, value["counterexample_id"])["state"], "PREPARED")
        with patch.object(
            CounterexampleChecks, "__init__", side_effect=RuntimeError("Create fault")
        ):
            result = execute(self.root, value)
        self.assertEqual((result["state"], result["math_verdict"]), ("EXECUTION_ERROR", "UNKNOWN"))
        with patch.object(CounterexampleChecks, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(execute(self.root, value)["state"], "EXECUTION_ERROR")

    def test_execution_fault_cleans_running_child_without_promoting_it(self):
        value = prepare(self.root, self.candidate())
        with patch.object(
            CounterexampleChecks, "execute", side_effect=RuntimeError("Execution fault")
        ):
            result = execute(self.root, value)
        self.assertEqual((result["state"], result["math_verdict"]), ("EXECUTION_ERROR", "UNKNOWN"))
        self.assertEqual(result["check"]["state"], "INTERRUPTED")

    def test_plan_report_or_source_drift_stays_invalid_after_restoration(self):
        for name in ("plan.json", "report.json", "mra/counterexamples.py"):
            value = prepare(self.root, self.candidate())
            path = (
                self.root
                if name.startswith("mra/")
                else _directory(self.root, value["counterexample_id"])
            ) / name
            before = path.read_bytes()
            path.write_bytes(before + b" " if name.startswith("mra/") else b"{}")
            self.assertFalse(report(self.root, value["counterexample_id"])["integrity_ok"])
            path.write_bytes(before)
            self.assertEqual(report(self.root, value["counterexample_id"])["state"], "UNAVAILABLE")

    def test_forged_completion_without_child_and_redirected_paths_fail_closed(self):
        value = prepare(self.root, self.candidate())
        path = _directory(self.root, value["counterexample_id"]) / "report.json"
        raw = _envelope(path)
        raw.update(state="COMPLETED", reviewer="QA")
        _atomic(path, raw)
        self.assertEqual(report(self.root, value["counterexample_id"])["math_verdict"], "UNKNOWN")
        for bad in ("../outside", "cex-bad"):
            with self.assertRaises(ValueError):
                report(self.root, bad)
        base = _directory(self.root)
        redirect = base / ("cex-" + "f" * 32)
        redirect.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            report(self.root, redirect.name)

    def test_http_search_csrf_and_history_are_separate_from_execution(self):
        raw = {
            "schema": SEARCH_SCHEMA,
            "target": cases()[-4]["target"],
            "budget": {"max_value": 4, "max_assignments": 1000},
        }
        with running_server(self.root) as server:
            self.assertEqual(
                request(server, "POST", "/v1/number-theory/counterexamples/search", raw)[0], 200
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/number-theory/counterexamples/search",
                    raw,
                    headers={"X-MRA-CSRF": "wrong"},
                )[0],
                403,
            )
            self.assertEqual(request(server, "GET", "/v1/counterexamples")[1]["runs"], [])
            self.assertEqual(request(server, "POST", "/v1/counterexamples", {})[0], 404)
            self.assertEqual(request(server, "GET", "/v1/counterexamples?run=1")[0], 404)
            self.assertEqual(request(server, "GET", "/counterexamples.js")[0], 200)
        self.assertEqual(catalog(self.root)["runs"], [])


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    def test_three_false_authored_targets_are_actually_refuted_and_cli_is_idempotent(self):
        results = []
        for case in cases()[-4:-1]:
            proposal = experiment(case["target"])
            value = prepare(ROOT, proposal["candidate"])
            args = [
                sys.executable,
                "-m",
                "mra",
                "counterexample-run",
                value["counterexample_id"],
                "--plan-fingerprint",
                value["plan"]["plan_fingerprint"],
                "--reviewer",
                "Counterexample QA",
                "--confirmed",
            ]
            completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=240)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            checked = json.loads(completed.stdout)
            results.append(
                {"case_id": case["id"], "search": proposal, "prepared": value, "result": checked}
            )
            (OUT / "real-refutations.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n"
            )
            self.assertEqual(
                (checked["state"], checked["math_verdict"], checked["evidence_level"]),
                ("COMPLETED", "REFUTED", "E4"),
                checked,
            )
            self.assertEqual(checked["check"]["math_verdict"], "PROVED")
            self.assertEqual(
                checked["check"]["result"]["replay"]["audit"]["mode"], "data-only-kernel-replay-v1"
            )
            with patch.object(
                CounterexampleChecks, "execute", side_effect=AssertionError("No retry")
            ):
                self.assertEqual(execute(ROOT, value), checked)
            with self.assertRaises(ValueError):
                run(
                    ROOT,
                    value["counterexample_id"],
                    plan_fingerprint=value["plan"]["plan_fingerprint"],
                    reviewer="Other",
                    confirmed=True,
                )

    def test_actual_supervisor_exit_after_negation_check_keeps_original_unknown(self):
        value = prepare(ROOT, experiment(cases()[-3]["target"])["candidate"])
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.counterexamples import CounterexampleChecks,run
original=CounterexampleChecks.execute
def stop(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    assert result['math_verdict']=='PROVED',result
    os._exit(73)
with patch.object(CounterexampleChecks,'execute',stop):
    run(Path.cwd(),sys.argv[1],plan_fingerprint=sys.argv[2],reviewer='Counterexample QA',confirmed=True)
"""
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                value["counterexample_id"],
                value["plan"]["plan_fingerprint"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertEqual(child.returncode, 73, child.stderr)
        checked = execute(ROOT, value)
        self.assertEqual((checked["state"], checked["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        self.assertEqual(checked["check"]["math_verdict"], "PROVED")
        (OUT / "real-interruption.json").write_text(
            json.dumps(checked, ensure_ascii=False, indent=2) + "\n"
        )
        directory = _directory(ROOT, value["counterexample_id"])
        destination = ROOT / "artifacts/counterexample-tests" / uuid.uuid4().hex
        destination.parent.mkdir(exist_ok=True)
        assert directory.resolve().is_relative_to(ROOT / "artifacts/nat-counterexamples")
        shutil.move(str(directory), destination)
        (OUT / "interruption-location.json").write_text(
            json.dumps({"path": str(destination.relative_to(ROOT))}) + "\n"
        )
