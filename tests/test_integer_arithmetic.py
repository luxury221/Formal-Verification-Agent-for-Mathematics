"""Explicit integer types; positive verdicts require actual Lean and kernel replay."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from mra.domain import Alignment, canonical, digest
from mra.evidence import EvidenceGate
from mra.formalizer import SourceClaim
from mra.knowledge import KnowledgeStore
from mra.nat_ideas import (
    INT_MODE,
    INT_BUNDLE_FILE,
    IntTarget,
    NatTarget,
    render_formula,
    parse_bundle,
    proof_source,
    search_bundle,
    target_from_dict,
    validate_plan,
)
from mra.paper_review import PaperReviewStore, prepare_review
from mra.verifier import NativeLeanBackend
from test_http_api import copy_web_inputs, request, running_server
from test_paper_review import submission
from test_knowledge import admission

ROOT = Path(__file__).resolve().parents[1]
PROVE = {"direction": "prove", "strategy": "omega", "witnesses": []}
REFUTE = {"direction": "refute", "strategy": "counterexample", "witnesses": [-1]}


def num(value):
    return {"op": "num", "value": value}


def var():
    return {"op": "var", "name": "n"}


def binary(op, left, right):
    return {"op": op, "left": left, "right": right}


def forall(body):
    return {"op": "forall", "name": "n", "body": body}


def example(name="valid-proof"):
    return json.loads((ROOT / "evals/integer-review-v1" / (name + ".json")).read_text())


def target(formula, cls=IntTarget):
    return cls.create(
        SourceClaim("typed-integer-test", 1, "Repository authored typed arithmetic"),
        formula,
        "Explicit domain test",
    )


class IntegerProtocolTests(unittest.TestCase):
    def test_domain_changes_target_and_alignment_even_when_formula_ast_is_identical(self):
        formula = forall(binary("lt", binary("sub", var(), num(1)), var()))
        natural, integer = target(formula, NatTarget), target(formula)
        self.assertNotEqual(natural.fingerprint, integer.fingerprint)
        self.assertIn(": Nat", natural.contract("prove")[0].formal_statement)
        self.assertIn(": Int", integer.contract("prove")[0].formal_statement)
        with self.assertRaises(ValueError):
            NatTarget.from_dict(integer.to_dict())
        with self.assertRaises(ValueError):
            IntTarget.from_dict(natural.to_dict())

    def test_negative_constants_and_unary_negation_are_explicitly_integer_only(self):
        for expression in (num(-1), {"op": "neg", "body": var()}):
            formula = forall(binary("eq", expression, expression))
            self.assertIn("Int", render_formula(formula, "Int"))
            with self.assertRaises(ValueError):
                render_formula(formula)
        self.assertEqual(
            target_from_dict(target(forall(binary("eq", var(), var()))).to_dict()).DOMAIN, "Int"
        )

    def test_signed_witnesses_are_parenthesized_typed_and_not_accepted_as_naturals(self):
        formula = forall(binary("le", num(0), var()))
        self.assertIn("(-1 : Int)", proof_source(REFUTE, target(formula)))
        with self.assertRaises(ValueError):
            validate_plan(REFUTE, target(formula, NatTarget))
        for value in (-1000001, 1000001, True, 1.2, "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_plan({**REFUTE, "witnesses": [value]}, target(formula))

    def test_mode_schema_mismatch_extra_ast_fields_and_arbitrary_source_are_rejected(self):
        value = target(forall(binary("eq", var(), var())))
        bundle = search_bundle(value, PROVE)
        self.assertEqual(parse_bundle(canonical(bundle))[0].DOMAIN, "Int")
        with self.assertRaises(ValueError):
            parse_bundle(canonical({**bundle, "mode": "nat-bounded-search-v1"}))
        for formula in [
            forall(binary("eq", num(True), num(0))),
            forall(binary("eq", num(-1000001), num(0))),
            {"op": "lean", "source": "by sorry"},
        ]:
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                target(formula)
        with self.assertRaises(ValueError):
            render_formula(forall(binary("eq", var(), var())), "Real")

    def test_review_domain_and_source_version_are_bound_to_confirmation(self):
        bundle = example()
        plan = prepare_review(bundle)
        self.assertEqual(plan["domain"], "Int")
        self.assertTrue(
            all(item["target"]["schema"] == "int-formula-v1" for item in plan["obligations"])
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_web_inputs(root)
            store = PaperReviewStore(root)
            request_data = submission(bundle)
            for change in (
                {"schema": "nat-paper-review-v1"},
                {"document": {**bundle["document"], "version": 2}},
            ):
                with self.assertRaises(ValueError):
                    store.submit({**request_data, "bundle": {**bundle, **change}})

    def test_integer_http_examples_and_prepare_need_no_model_and_retain_origin_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_web_inputs(root)
            shutil.copytree(ROOT / "evals/integer-review-v1", root / "evals/integer-review-v1")
            with running_server(root) as server:
                status, data, _ = request(server, "GET", "/v1/paper-examples/integers")
                self.assertEqual(status, 200)
                self.assertEqual(len(data["examples"]), 3)
                status, plan, _ = request(
                    server,
                    "POST",
                    "/v1/paper-reviews/prepare",
                    {"bundle": example("missing-condition")},
                )
                self.assertEqual(
                    (status, plan["domain"], plan["budget"]["model_calls"]), (200, "Int", 0)
                )
                self.assertEqual(
                    request(
                        server,
                        "GET",
                        "/v1/paper-examples/integers",
                        headers={"Origin": "https://outside.example"},
                    )[0],
                    403,
                )
                self.assertFalse((root / "artifacts/runs").exists())


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1 for actual workers"
)
class IntegerRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = NativeLeanBackend(ROOT, 30)
        cls.gate = EvidenceGate(ROOT, cls.backend, cls.backend.policy)

    def check(self, value, plan, aligned=True):
        claim, formalization = value.contract(plan["direction"])
        checker = (
            self.backend.check_int_plan if value.DOMAIN == "Int" else self.backend.check_nat_plan
        )
        receipt = checker(value, plan)
        alignment = (
            Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                "repository-integer-test-author",
                "Exact explicitly typed authored target",
            )
            if aligned
            else None
        )
        result, evidence = self.gate.assess(claim, formalization, receipt, alignment)
        return result, receipt, claim, formalization, alignment

    def test_integer_subtraction_proves_while_same_natural_formula_has_zero_counterexample(self):
        formula = forall(binary("lt", binary("sub", var(), num(1)), var()))
        integer = self.check(target(formula), PROVE)
        natural = self.check(target(formula, NatTarget), {**REFUTE, "witnesses": [0]})
        self.assertEqual(integer[0].math_verdict, "PROVED")
        self.assertEqual(natural[0].math_verdict, "REFUTED")
        self.assertNotEqual(integer[2].fingerprint, natural[2].fingerprint)

    def test_unary_negation_uses_real_independent_replay_and_needs_alignment_for_e4(self):
        formula = forall(binary("eq", binary("add", {"op": "neg", "body": var()}, var()), num(0)))
        result, receipt, claim, formalization, _ = self.check(target(formula), PROVE, False)
        self.assertEqual(
            (result.math_verdict, result.evidence_level), ("UNKNOWN", "E3_KERNEL_CHECKED")
        )
        self.assertNotEqual(receipt.payload["candidate_pid"], receipt.payload["replay"]["pid"])
        aligned, _ = self.gate.assess(
            claim,
            formalization,
            receipt,
            Alignment(
                claim.fingerprint,
                digest(asdict(formalization)),
                "integer-test-author",
                "Exact integer negation",
            ),
        )
        self.assertEqual(aligned.math_verdict, "PROVED")

    def test_negative_counterexample_is_checked_and_changed_sidecar_cannot_be_reused(self):
        result, receipt, claim, formalization, alignment = self.check(
            target(forall(binary("le", num(0), var()))), REFUTE
        )
        self.assertEqual(result.math_verdict, "REFUTED")
        self.assertEqual(receipt.payload["input_mode"], INT_MODE)
        path = Path(result.artifact_directory) / INT_BUNDLE_FILE
        original = path.read_bytes()
        try:
            bundle = json.loads(original)
            bundle["target"]["schema"] = "nat-formula-v1"
            path.write_bytes(canonical(bundle))
            rejected, _ = self.gate.assess(claim, formalization, receipt, alignment)
            self.assertEqual(
                (rejected.math_verdict, rejected.run_status), ("UNKNOWN", "POLICY_BLOCKED")
            )
        finally:
            path.write_bytes(original)

    def test_rejected_integer_proof_candidate_never_becomes_a_refutation(self):
        result, *_ = self.check(target(forall(binary("le", num(0), var()))), PROVE)
        self.assertEqual(result.math_verdict, "UNKNOWN")

    def run_review(self, name):
        store = PaperReviewStore(ROOT)
        queued = store.submit(submission(example(name)))
        report = store.work_once(queued["review_id"])["review"]
        self.assertEqual(report["state"], "COMPLETED", report)
        return store, report

    def test_integer_true_claim_bad_original_proof_and_unverified_dependency_remain_separate(self):
        _, report = self.run_review("true-claim-bad-proof")
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("PROVED", "GAP_FOUND")
        )
        self.assertEqual([item["key"] for item in report["checks"]], ["claim", "s1"])
        self.assertEqual(report["diagnostics"][0]["span"], [2, 2])
        plan = KnowledgeStore(ROOT).prepare(report["review_id"])
        self.assertEqual([item["source"]["key"] for item in plan["items"]], ["claim"])

    def test_integer_proof_archive_carries_domain_and_withdrawal_invalidates_only_dependents(self):
        _, report = self.run_review("valid-proof")
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("PROVED", "PROVED")
        )
        knowledge = KnowledgeStore(ROOT)
        plan = knowledge.prepare(report["review_id"])
        self.assertTrue(all(item["domain"] == "Int" for item in plan["items"]))
        for item in plan["items"]:
            self.assertEqual(
                item["proposition_fingerprint"],
                digest({"domain": "Int", "formula": item["formula"]}),
            )
        admitted = knowledge.admit(admission(plan))
        by_key = {item["source_ref"]["key"]: item for item in admitted["items"]}
        knowledge.action(
            by_key["s1"]["item_id"],
            {
                "reviewer": "integer-test-author",
                "reason": "Test typed dependency invalidation",
                "confirmed": True,
            },
        )
        self.assertEqual(knowledge.report(by_key["s2"]["item_id"])["state"], "INVALIDATED")
        self.assertEqual(knowledge.report(by_key["claim"]["item_id"])["state"], "ACTIVE")
