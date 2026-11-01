"""Search comparison contracts plus an actual full benchmark and crash boundary."""

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

from mra.domain import digest
from mra.mathlib_lab import TRUST_FILES
from mra.search_evaluation import (
    DATASET,
    VARIANTS,
    EvaluationChecks,
    _atomic,
    _directory,
    _envelope,
    _load_plan,
    definition,
    evaluation_catalog,
    prepare_evaluation,
    read_evaluation,
    run_evaluation,
)
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/search-evaluation-20261027-01"


def copy_sources(root):
    copy_web_inputs(root)  # Deliberately nonexecutable runtime metadata for unit contracts.
    for name in (
        *TRUST_FILES,
        DATASET,
        "mra/number_theory_terms.py",
        "mra/number_theory_lab.py",
        "mra/number_theory_search.py",
        "mra/search_evaluation.py",
    ):
        if name == "lean_workspace/runtime-lock.json":
            continue  # Keep the copied nonexecutable fixture snapshot and its matching lock.
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)


def execute(root, value, **changes):
    return run_evaluation(
        root,
        value["evaluation_id"],
        plan_fingerprint=value["plan"]["plan_fingerprint"],
        reviewer="Search comparison QA",
        confirmed=True,
        **changes,
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)

    def unknown_dataset(self):
        value = json.loads((self.root / DATASET).read_text())
        value["cases"] = value["cases"][-4:]
        (self.root / DATASET).write_text(json.dumps(value))

    def test_definition_has_proof_free_targets_exposure_and_equal_resource_ceilings(self):
        value = definition(self.root)
        self.assertEqual(len(value["cases"]), 16)
        self.assertEqual(
            [len(definition(self.root, s)["cases"]) for s in ("development", "regression")], [4, 12]
        )
        self.assertTrue(all("proof" not in c["target"] for c in value["cases"]))
        self.assertFalse(value["independent_blind_evaluation"])
        self.assertFalse(value["training_use"])
        self.assertEqual({v["max_expansions"] for v in VARIANTS.values()}, {3000})
        self.assertEqual(value["limits"]["maximum_lean_worker_seconds"], 960)

    def test_prepare_preserves_all_targets_and_grants_no_math_evidence(self):
        with patch.object(
            EvaluationChecks, "execute", side_effect=AssertionError("No Lean on preparation")
        ):
            value = prepare_evaluation(self.root)
        self.assertEqual(
            (value["state"], value["summary"], value["reviewer"]), ("PREPARED", None, None)
        )
        self.assertTrue(value["integrity_ok"])
        self.assertEqual(value["results"], [])
        self.assertFalse((_directory(self.root, value["evaluation_id"]) / "checks").exists())
        for variant, count in (("direct", 4), ("composed", 12)):
            self.assertEqual(
                sum(bool(c["searches"][variant]["candidate"]) for c in value["plan"]["cases"]),
                count,
            )
            for case in value["plan"]["cases"]:
                proposal = case["searches"][variant]
                self.assertEqual(proposal["target"], case["target"])
                self.assertEqual(
                    (proposal["math_verdict"], proposal["evidence_level"]), ("UNKNOWN", "E0")
                )

    def test_unconfirmed_unnamed_stale_or_wrong_typed_confirmation_creates_no_check(self):
        value = prepare_evaluation(self.root, "development")
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
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_evaluation(self.root, value["evaluation_id"], **kwargs)
        self.assertEqual(read_evaluation(self.root, value["evaluation_id"])["state"], "PREPARED")
        self.assertFalse((_directory(self.root, value["evaluation_id"]) / "checks").exists())

    def test_no_candidate_denominator_completion_and_idempotent_no_retry(self):
        self.unknown_dataset()
        value = prepare_evaluation(self.root)
        with patch.object(
            EvaluationChecks, "execute", side_effect=AssertionError("No candidates to check")
        ):
            result = execute(self.root, value)
            self.assertEqual(execute(self.root, value), result)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(len(result["results"]), 8)
        for variant in VARIANTS:
            self.assertEqual(
                (
                    result["summary"][variant]["denominator"],
                    result["summary"][variant]["proved"],
                    result["summary"][variant]["unknown"],
                ),
                (4, 0, 4),
            )
        with self.assertRaises(ValueError):
            run_evaluation(
                self.root,
                value["evaluation_id"],
                plan_fingerprint=value["plan"]["plan_fingerprint"],
                reviewer="Different reviewer",
                confirmed=True,
            )

    def test_global_worker_lock_rejects_start_before_state_change(self):
        value = prepare_evaluation(self.root, "development")
        with TaskStore(self.root).worker_lock(), self.assertRaises(WorkerBusy):
            execute(self.root, value)
        self.assertEqual(read_evaluation(self.root, value["evaluation_id"])["state"], "PREPARED")

    def test_fault_during_creation_or_execution_is_terminal_without_summary(self):
        for target in ("__init__", "execute"):
            value = prepare_evaluation(self.root, "development")
            with patch.object(
                EvaluationChecks, target, side_effect=RuntimeError("Injected lifecycle fault")
            ):
                result = execute(self.root, value)
            self.assertEqual(result["state"], "EXECUTION_ERROR")
            self.assertIsNone(result["summary"])
            with patch.object(EvaluationChecks, "execute", side_effect=AssertionError("No retry")):
                self.assertEqual(execute(self.root, value)["state"], "EXECUTION_ERROR")

    def test_report_or_plan_tampering_stays_invalid_after_file_restoration(self):
        for filename in ("report.json", "plan.json"):
            value = prepare_evaluation(self.root, "development")
            path = _directory(self.root, value["evaluation_id"]) / filename
            before = path.read_bytes()
            path.write_text("{}")
            self.assertFalse(read_evaluation(self.root, value["evaluation_id"])["integrity_ok"])
            path.write_bytes(before)
            self.assertEqual(
                read_evaluation(self.root, value["evaluation_id"])["state"], "UNAVAILABLE"
            )

    def test_generator_or_dataset_drift_stays_invalid_after_restoration(self):
        for filename in ("mra/number_theory_search.py", DATASET):
            value = prepare_evaluation(self.root, "development")
            path = self.root / filename
            before = path.read_bytes()
            path.write_bytes(before + b"\n")
            self.assertFalse(read_evaluation(self.root, value["evaluation_id"])["integrity_ok"])
            path.write_bytes(before)
            self.assertFalse(read_evaluation(self.root, value["evaluation_id"])["integrity_ok"])

    def test_completed_report_cannot_drop_reorder_or_invent_case_results(self):
        self.unknown_dataset()
        for operation in ("drop", "reverse", "invent"):
            value = prepare_evaluation(self.root)
            execute(self.root, value)
            path = _directory(self.root, value["evaluation_id"]) / "report.json"
            raw = _envelope(path)
            if operation == "drop":
                raw["results"].pop()
            elif operation == "reverse":
                raw["results"].reverse()
            else:
                raw["results"][0]["run_id"] = "mathlib-" + ("0" * 32)
            _atomic(path, raw)
            self.assertIsNone(read_evaluation(self.root, value["evaluation_id"])["summary"])
            self.assertFalse(read_evaluation(self.root, value["evaluation_id"])["integrity_ok"])

    def test_candidate_goal_cannot_be_resealed_as_different_target(self):
        value = prepare_evaluation(self.root, "development")
        directory = _directory(self.root, value["evaluation_id"])
        plan = _envelope(directory / "plan.json")
        proposal = plan["cases"][0]["searches"]["direct"]
        proposal["candidate"]["goal"] = {"eq": [{"nat": 0}, {"nat": 1}]}
        proposal["record_sha256"] = digest(
            {k: v for k, v in proposal.items() if k != "record_sha256"}
        )
        plan["plan_fingerprint"] = digest(
            {k: v for k, v in plan.items() if k != "plan_fingerprint"}
        )
        _atomic(directory / "plan.json", plan)
        with self.assertRaises(ValueError):
            _load_plan(self.root, directory)

    def test_invalid_split_duplicate_cases_and_proof_input_are_rejected(self):
        original = json.loads((self.root / DATASET).read_text())
        for operation in ("duplicate", "proof", "budget"):
            value = deepcopy(original)
            if operation == "duplicate":
                value["cases"][1] = value["cases"][0]
            elif operation == "proof":
                value["cases"][0]["target"]["proof"] = {"hyp": "h"}
            else:
                value["cases"][0]["target"]["timeout_seconds"] = True
            (self.root / DATASET).write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                definition(self.root)
        with self.assertRaises(ValueError):
            definition(self.root, "blind")

    def test_redirected_storage_and_identifiers_fail_closed(self):
        for identifier in ("../outside", "ns-eval-wrong", None):
            if identifier is not None:
                with self.assertRaises(ValueError):
                    read_evaluation(self.root, identifier)
        base = _directory(self.root)
        base.parent.mkdir(exist_ok=True)
        target = self.root / "redirect"
        target.mkdir()
        base.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare_evaluation(self.root)

    def test_http_is_read_only_and_exposes_full_prepared_plan_and_assets(self):
        value = prepare_evaluation(self.root, "development")
        with running_server(self.root) as server:
            for route in (
                "/search-evaluation.js",
                "/search-evaluation.css",
                "/v1/number-theory-search-evaluations",
                "/v1/number-theory-search-evaluations/" + value["evaluation_id"],
            ):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(
                request(server, "POST", "/v1/number-theory-search-evaluations", {})[0], 404
            )
            self.assertEqual(
                request(server, "GET", "/v1/number-theory-search-evaluations?run=1")[0], 404
            )
        self.assertEqual(evaluation_catalog(self.root)["runs"][0]["state"], "PREPARED")


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    def test_full_visible_dataset_real_comparison_and_idempotent_cli(self):
        value = prepare_evaluation(ROOT)
        # Save the actual complete generated proof plans before explicitly confirming.
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "reviewed-plan.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
        args = [
            sys.executable,
            "-m",
            "mra",
            "search-evaluation-run",
            value["evaluation_id"],
            "--plan-fingerprint",
            value["plan"]["plan_fingerprint"],
            "--reviewer",
            "Search comparison QA",
            "--confirmed",
        ]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=1200)
        (OUT / "real-cli.stderr.log").write_text(result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(result.stdout)
        (OUT / "real-comparison.json").write_text(
            json.dumps(checked, ensure_ascii=False, indent=2) + "\n"
        )
        self.assertTrue(checked["integrity_ok"])
        self.assertEqual(checked["state"], "COMPLETED")
        self.assertEqual([checked["summary"][v]["proved"] for v in VARIANTS], [4, 12])
        self.assertEqual([checked["summary"][v]["denominator"] for v in VARIANTS], [16, 16])
        self.assertEqual(len(checked["summary"]["composed_only_proved"]), 8)
        self.assertEqual(len(checked["results"]), 32)
        self.assertTrue(all(r["math_verdict"] == "UNKNOWN" for r in checked["results"][-8:]))
        with patch.object(
            EvaluationChecks,
            "execute",
            side_effect=AssertionError("Idempotency must not check again"),
        ):
            self.assertEqual(execute(ROOT, value)["summary"], checked["summary"])

    def test_actual_supervisor_exit_after_checked_child_stays_interrupted(self):
        value = prepare_evaluation(ROOT, "development")
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.search_evaluation import EvaluationChecks,run_evaluation
original=EvaluationChecks.execute
def exit_after_actual_check(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    assert result['math_verdict']=='PROVED',result
    os._exit(73)
with patch.object(EvaluationChecks,'execute',exit_after_actual_check):
    run_evaluation(Path.cwd(),sys.argv[1],plan_fingerprint=sys.argv[2],reviewer='Search comparison QA',confirmed=True)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                value["evaluation_id"],
                value["plan"]["plan_fingerprint"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        checked = read_evaluation(ROOT, value["evaluation_id"])
        self.assertEqual(checked["state"], "INTERRUPTED")
        self.assertIsNone(checked["summary"])
        with patch.object(EvaluationChecks, "execute", side_effect=AssertionError("Do not retry")):
            self.assertEqual(execute(ROOT, value)["state"], "INTERRUPTED")
        (OUT / "real-interruption.json").write_text(
            json.dumps(checked, ensure_ascii=False, indent=2) + "\n"
        )
        # Keep fault-injection history outside the product evaluation list.
        directory = _directory(ROOT, value["evaluation_id"])
        destination = ROOT / "artifacts/search-evaluation-tests" / uuid.uuid4().hex
        destination.parent.mkdir(exist_ok=True)
        assert directory.resolve().is_relative_to(
            ROOT / "artifacts/number-theory-search-evaluations"
        )
        shutil.move(str(directory), destination)
        (OUT / "interruption-location.json").write_text(
            json.dumps({"path": str(destination.relative_to(ROOT))}) + "\n"
        )
