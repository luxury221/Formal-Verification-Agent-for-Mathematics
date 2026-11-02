"""Fixture transports test orchestration; mathematical results require real Lean."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from mra.chat_model import ModelConfig
from mra.datasets import load_dataset
from mra.domain import canonical
from mra.evaluation_targets import reference_formulas, reference_target
from mra.evidence import EvidenceGate
from mra.model_evaluation import (
    model_evaluation_plan,
    parse_proposal,
    proof_request,
    read_model_evaluation,
    run_model_evaluation,
)
from mra.model_service import ModelIdentity, ModelResponse, TokenUsage
from mra.nat_ideas import render_formula
from mra.tasks import TaskStore, WorkerBusy

ROOT = Path(__file__).resolve().parents[1]


def response_for(request, plan=None, **overrides):
    data = json.loads(request.input_json)
    response = ModelResponse(
        request.fingerprint,
        ModelIdentity("test", "authored-protocol-fixture", "v1", "fixture"),
        canonical({"target_fingerprint": data["target_fingerprint"], "plan": plan}).decode(),
        TokenUsage(7, 3),
    )
    return replace(response, **overrides)


class ModelEvaluationContractTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT)
        self.case = self.dataset.cases[0]
        self.target = reference_target(self.case)
        self.request = proof_request(self.target, self.dataset.budget.model, "contract-test")

    def test_all_reference_mappings_preserve_sources_and_explicit_hypotheses(self):
        mappings = reference_formulas()
        self.assertEqual(set(mappings), {case["formal_statement"] for case in self.dataset.cases})
        for case in self.dataset.cases:
            with self.subTest(case=case["id"]):
                target = reference_target(case)
                self.assertEqual(target.source.statement, case["original_statement"])
                self.assertEqual(target.source.id, case["id"])
                rendered = render_formula(json.loads(target.formula_json))
                self.assertEqual(rendered.count("∀"), len(case["annotation"]["variables"]))
                self.assertEqual(rendered.count("→"), len(case["annotation"]["assumptions"]))
        successor = reference_target(self.dataset.cases[2]).formula_json
        self.assertIn('"op":"add"', successor)
        self.assertIn('"value":1', successor)

    def test_unknown_reference_statement_is_rejected_without_general_lean_parser(self):
        with self.assertRaises(ValueError):
            reference_target({**self.case, "formal_statement": "True"})

    def test_plan_uses_existing_fixed_budget_and_no_model_or_checker(self):
        with (
            patch("mra.model_evaluation.ChatModelService", side_effect=AssertionError),
            patch("mra.model_evaluation.NativeLeanBackend", side_effect=AssertionError),
        ):
            plan = model_evaluation_plan(ROOT)
        self.assertEqual(plan["budget"], asdict(self.dataset.budget))
        self.assertEqual(len(plan["targets"]), 6)
        self.assertFalse(plan["reserved_selected"])
        self.assertEqual(plan["model_calls"], 0)
        self.assertIsNone(plan["alignment_accuracy"])
        self.assertEqual(plan, model_evaluation_plan(ROOT))

    def test_reserved_split_and_empty_training_require_correct_explicit_request(self):
        for split in ("all", "reserved", "train"):
            with self.subTest(split=split), self.assertRaises(ValueError):
                model_evaluation_plan(ROOT, split)
        plan = model_evaluation_plan(ROOT, "all", include_reserved=True)
        self.assertEqual(len(plan["targets"]), 18)
        self.assertTrue(plan["reserved_selected"])

    def test_model_inputs_exclude_proofs_labels_and_authored_witnesses(self):
        forbidden = {"proof", "expected_verdict", "direction", "annotation", "witnesses"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)

        for case in self.dataset.cases:
            target = reference_target(case)
            request = proof_request(target, self.dataset.budget.model, uuid.uuid4().hex)
            inspect(json.loads(request.input_json))
            self.assertNotIn(case["proof"], request.input_json)
            self.assertLess(len(request.input_json), request.budget.max_input_chars)

    def test_null_proposal_is_abstention_not_a_counterexample(self):
        self.assertIsNone(
            parse_proposal(response_for(self.request), self.request, self.target, simulation=True)
        )

    def test_response_identity_binding_and_output_limits_are_enforced(self):
        valid = response_for(self.request)
        cases = [
            replace(valid, request_fingerprint="changed"),
            replace(valid, identity=ModelIdentity("test", "m", "v1", "model")),
            replace(valid, usage=TokenUsage(1, self.request.budget.max_output_tokens + 1)),
            replace(valid, output_json="x" * (self.request.budget.max_output_bytes + 1)),
            replace(valid, usage={"input_tokens": 1, "output_tokens": 1}),
        ]
        for value in cases:
            with self.subTest(value=type(value.usage).__name__), self.assertRaises(ValueError):
                parse_proposal(value, self.request, self.target, simulation=True)
        with self.assertRaises(ValueError):
            parse_proposal(valid, self.request, self.target)

    def test_malformed_changed_target_or_unrestricted_tactic_never_becomes_plan(self):
        valid = json.loads(response_for(self.request).output_json)
        for proposal in (
            {**valid, "target_fingerprint": "different"},
            {**valid, "success": True},
            {**valid, "plan": {"direction": "prove", "strategy": "by sorry", "witnesses": []}},
            {
                **valid,
                "plan": {"direction": "refute", "strategy": "counterexample", "witnesses": []},
            },
        ):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                parse_proposal(
                    response_for(self.request, output_json=canonical(proposal).decode()),
                    self.request,
                    self.target,
                    simulation=True,
                )

    def test_missing_model_stops_before_worker_checker_and_artifacts(self):
        with (
            patch.object(ModelConfig, "load", return_value=None),
            patch("mra.model_evaluation.NativeLeanBackend", side_effect=AssertionError),
            patch("mra.model_evaluation.TaskStore", side_effect=AssertionError),
        ):
            with self.assertRaisesRegex(ValueError, "MODEL_NOT_CONFIGURED"):
                run_model_evaluation(ROOT)

    def test_actual_cli_plan_and_reserved_guard_do_not_require_model_config(self):
        result = subprocess.run(
            [sys.executable, "-m", "mra", "model-benchmark", "--plan"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["scope"], "authored-formal-target-proof-plan-only")
        result = subprocess.run(
            [sys.executable, "-m", "mra", "model-benchmark", "--plan", "--split", "reserved"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("include-reserved", result.stderr)


class EvaluationFixture:
    def __init__(self):
        self.requests = []
        self.worker_blocked = []

    def generate(self, request):
        self.requests.append(request)
        try:
            with TaskStore(ROOT).worker_lock():
                self.worker_blocked.append(False)
        except WorkerBusy:
            self.worker_blocked.append(True)
        case_id = json.loads(request.input_json)["target"]["source"]["id"]
        if case_id == "natv1_sub_restore_guarded":
            return response_for(
                request, {"direction": "prove", "strategy": "omega", "witnesses": []}
            )
        if case_id == "natv1_sub_restore_unconditional":
            return response_for(
                request, {"direction": "refute", "strategy": "counterexample", "witnesses": [0, 1]}
            )
        if case_id == "natv1_mul_cancel_unconditional":
            return response_for(
                request, {"direction": "prove", "strategy": "omega", "witnesses": []}
            )
        if case_id == "natv1_mul_add":
            return response_for(request, output_json='{"target_fingerprint":"changed","plan":null}')
        if case_id == "natv1_square_add":
            raise TimeoutError("test-only simulated transport timeout")
        return response_for(request)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit Lean evaluation with simulated proposals"
)
class ModelEvaluationRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = EvaluationFixture()
        cls.report = run_model_evaluation(ROOT, simulation_service=cls.service)
        cls.directory = Path(cls.report["artifact_directory"])

    def test_real_proof_refutation_failure_abstention_and_timeout_have_correct_denominators(self):
        report = self.report
        self.assertEqual(report["state"], "COMPLETED", report)
        self.assertEqual(report["total"], 6)
        self.assertEqual(report["processed"], 6)
        self.assertEqual(report["solved"], 2)
        self.assertEqual(report["lean_checks"], 3)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["simulation_calls"], 5)
        self.assertEqual(report["simulation_request_attempts"], 6)
        self.assertIsNone(report["model_solve_rate"])
        self.assertEqual(report["simulation_checked_rate"], 2 / 6)
        self.assertFalse(report["usage_complete"])
        self.assertEqual(report["known_input_tokens"], 35)
        self.assertIsNone(report["cost"])
        entries = {entry["id"]: entry for entry in report["results"]}
        self.assertEqual(entries["natv1_sub_restore_guarded"]["math_verdict"], "PROVED")
        self.assertEqual(entries["natv1_sub_restore_unconditional"]["math_verdict"], "REFUTED")
        self.assertEqual(entries["natv1_mul_cancel_unconditional"]["math_verdict"], "UNKNOWN")
        self.assertEqual(entries["natv1_add_cancel"]["state"], "ABSTAINED")
        self.assertEqual(entries["natv1_square_add"]["run_status"], "TIMEOUT")
        self.assertTrue((self.directory / "natv1_mul_add/response.json").exists())

    def test_shared_worker_lock_held_and_request_budgets_never_increased_or_retried(self):
        self.assertEqual(self.service.worker_blocked, [True] * 6)
        self.assertEqual(len(self.service.requests), 6)
        self.assertEqual(len({request.id for request in self.service.requests}), 6)
        for request in self.service.requests:
            data = json.loads(request.input_json)
            self.assertEqual(data["maximum_attempts"], 1)
            self.assertEqual(data["feedback"], [])
            self.assertEqual(request.budget, load_dataset(ROOT).budget.model)

    def test_historical_report_checks_all_artifacts_without_new_attestation(self):
        report = read_model_evaluation(ROOT, self.directory)
        self.assertTrue(report["integrity_ok"], report)
        self.assertFalse(report["fresh_attestation"])
        self.assertEqual(report["simulation_checked_rate"], 2 / 6)
        for entry in report["results"][:2]:
            receipt = json.loads((ROOT / entry["lean_directory"] / "receipt.json").read_text())[
                "payload"
            ]
            self.assertNotEqual(receipt["candidate_pid"], receipt["replay"]["pid"])

    def test_changed_request_lean_evidence_or_report_suppresses_rates(self):
        first = self.report["results"][0]
        paths = [
            self.directory / first["id"] / "request.json",
            ROOT / first["lean_directory"] / "input/nat-search.json",
            self.directory / "plan.json",
        ]
        for path in paths:
            original = path.read_bytes()
            try:
                path.write_bytes(original + b" ")
                changed = read_model_evaluation(ROOT, self.directory)
                self.assertFalse(changed["integrity_ok"], path)
                self.assertIsNone(changed["simulation_checked_rate"])
            finally:
                path.write_bytes(original)
        path = self.directory / "report.json"
        original = path.read_bytes()
        try:
            changed_case = json.loads(original)
            changed_case["results"][0]["solved"] = False
            empty_cases = {**json.loads(original), "results": []}
            changed_kind = {**json.loads(original), "simulation": False}
            for changed in (
                b"{broken-json",
                canonical(changed_case),
                canonical(empty_cases),
                canonical(changed_kind),
            ):
                path.write_bytes(changed)
                loaded = read_model_evaluation(ROOT, self.directory)
                self.assertFalse(loaded["integrity_ok"])
                self.assertIsNone(loaded["model_solve_rate"])
        finally:
            path.write_bytes(original)
        self.assertTrue(read_model_evaluation(ROOT, self.directory)["integrity_ok"])

    def test_cancel_after_real_gate_cannot_publish_success_or_partial_score(self):
        cancelled = False
        observed = []
        original = EvidenceGate.assess

        def cancel_after_check(gate, *args, **kwargs):
            nonlocal cancelled
            result = original(gate, *args, **kwargs)
            observed.append(result[0].math_verdict)
            cancelled = True
            return result

        with patch.object(EvidenceGate, "assess", cancel_after_check):
            report = run_model_evaluation(
                ROOT, simulation_service=EvaluationFixture(), cancel_requested=lambda: cancelled
            )
        self.assertEqual(observed, ["PROVED"])
        self.assertEqual(report["state"], "CANCELLED")
        self.assertEqual(report["solved"], 0)
        self.assertIsNone(report["simulation_checked_rate"])
        self.assertEqual(report["results"][0]["math_verdict"], "UNKNOWN")
        self.assertTrue(read_model_evaluation(ROOT, report["artifact_directory"])["integrity_ok"])


if __name__ == "__main__":
    unittest.main()
