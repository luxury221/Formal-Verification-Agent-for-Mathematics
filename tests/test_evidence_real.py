"""Real Lean integration tests. No mocks; explicitly enabled with MRA_REAL_LEAN=1."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import unittest

from mra.domain import (
    Alignment,
    CheckReceipt,
    Claim,
    EvidenceLevel,
    Formalization,
    RunStatus,
    Verdict,
    digest,
)
from mra.evidence import EvidenceGate
from mra.harness import MinimalResearchEngine
from mra.integrity import IntegrityError
from mra.verifier import NativeLeanBackend
from mra.datasets import load_dataset

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1",
    "real Lean suite requires MRA_REAL_LEAN=1 and pinned runtime",
)
class RealLeanEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = MinimalResearchEngine(ROOT)
        cls.backend = cls.engine.backend
        cls.gate = cls.engine.gate
        cls.case = cls.engine.cases["nat_add_zero"]
        cls.claim = Claim(
            "nat_add_zero", 1, cls.case["original_statement"], cls.case["formal_statement"]
        )
        cls.formalization = Formalization(cls.claim.fingerprint, cls.claim.formal_statement)
        cls.receipt = cls.backend.check(cls.case, cls.claim, cls.formalization)
        cls.alignment = Alignment(
            cls.claim.fingerprint,
            digest(asdict(cls.formalization)),
            "integration-test-fixture-review",
            "Explicitly reviewed finite fixture's original/formal mapping",
        )

    def assess(self, **changes):
        args = dict(
            claim=self.claim,
            formalization=self.formalization,
            receipt=self.receipt,
            alignment=self.alignment,
        )
        args.update(changes)
        return self.gate.assess(**args)

    def test_golden_cases(self):
        for case in self.engine.cases.values():
            with self.subTest(case=case["id"]):
                result = self.engine.run_case(case["id"], "integration-test-fixture-review")
                self.assertEqual(
                    result.run_status.value,
                    case["expected_status"],
                    result.reason + " " + result.artifact_directory,
                )
                self.assertEqual(result.math_verdict.value, case["expected_verdict"])
                self.assertEqual(result.evidence_level.value, case["expected_level"])

    def test_actual_lean_proof_reaches_e4_with_alignment(self):
        result, evidence = self.assess()
        self.assertEqual(result.math_verdict, Verdict.PROVED)
        self.assertEqual(evidence.level, EvidenceLevel.E4)
        snapshot = json.loads(
            (Path(result.artifact_directory) / "runtime-snapshot.json").read_text()
        )
        self.assertEqual(digest(snapshot), self.backend.environment.runtime_digest)

    def test_arithmetic_catalog_cannot_supply_an_edited_fixture_or_drop_its_premises(self):
        case = next(
            case for case in load_dataset(ROOT).cases if case["id"] == "natv1_sub_restore_guarded"
        )
        claim = Claim(
            case["id"],
            1,
            case["original_statement"],
            case["formal_statement"],
            tuple(case["annotation"]["assumptions"]),
        )
        formalization = Formalization(claim.fingerprint, claim.formal_statement)
        with self.assertRaisesRegex(ValueError, "repository-owned"):
            self.backend.check({**case, "proof": "by sorry"}, claim, formalization)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            self.backend.check(case, replace(claim, assumptions=()), formalization)

    def test_proof_without_alignment_remains_e3_unknown(self):
        result, evidence = self.assess(alignment=None)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertEqual(evidence.level, EvidenceLevel.E3)

    def test_forged_pass_cannot_promote(self):
        result, evidence = self.assess(receipt=CheckReceipt({"status": "PASS"}, "fake"))
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_modified_receipt_cannot_promote(self):
        payload = json.loads(json.dumps(self.receipt.payload))
        payload["audit"]["axioms"] = []
        payload["elapsed_seconds"] = -1
        result, evidence = self.assess(receipt=CheckReceipt(payload, self.receipt.signature))
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_new_claim_version_invalidates_evidence(self):
        result, evidence = self.assess(claim=replace(self.claim, version=2))
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertIsNone(evidence)

    def test_changed_environment_invalidates_evidence(self):
        result, evidence = self.assess(
            environment=replace(self.backend.environment, mathlib_commit="0" * 40)
        )
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_changed_policy_invalidates_evidence(self):
        changed = {**self.backend.policy, "policy_id": "future-version"}
        result, evidence = EvidenceGate(ROOT, self.backend, changed).assess(
            self.claim, self.formalization, self.receipt, self.alignment
        )
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_stale_alignment_cannot_promote(self):
        result, evidence = self.assess(alignment=replace(self.alignment, claim_fingerprint="stale"))
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_modified_artifact_cannot_promote(self):
        receipt = self.backend.check(self.case, self.claim, self.formalization)
        path = Path(receipt.payload["artifact_directory"]) / "input/Candidate.lean"
        with path.open("a") as output:
            output.write("\n-- modified after check\n")
        result, evidence = self.assess(receipt=receipt)
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_refutation_direction_cannot_be_substituted(self):
        result, evidence = self.assess(
            formalization=replace(self.formalization, direction="refute")
        )
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_arbitrary_lean_source_is_not_accepted(self):
        with self.assertRaises(ValueError):
            self.backend.check(
                {**self.case, "proof": "by run_tac pure ()"}, self.claim, self.formalization
            )

    def test_fixture_cannot_supply_a_different_claim_or_checked_term(self):
        changed_claim = replace(self.claim, formal_statement="True")
        changed_formalization = Formalization(changed_claim.fingerprint, "True")
        for claim, formalization in (
            (changed_claim, changed_formalization),
            (self.claim, replace(self.formalization, checked_statement="True")),
            (self.claim, replace(self.formalization, direction="refute")),
        ):
            with self.subTest(claim=claim, formalization=formalization):
                with self.assertRaisesRegex(ValueError, "exactly match"):
                    self.backend.check(self.case, claim, formalization)

    def test_real_timeout_is_unknown(self):
        backend = NativeLeanBackend(ROOT, timeout_seconds=0.001)
        receipt = backend.check(self.case, self.claim, self.formalization)
        result, evidence = EvidenceGate(ROOT, backend, backend.policy).assess(
            self.claim, self.formalization, receipt, self.alignment
        )
        self.assertEqual(result.run_status, RunStatus.TIMEOUT)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertIsNone(evidence)

    def test_added_dependency_blocks_old_evidence_and_new_execution(self):
        # Never edit an installed dependency. An extra library is enough to
        # change the exact runtime inventory and exercise both enforcement paths.
        path = ROOT / ".runtime/mathlib/.lake/build/lib/lean/MraUnexpectedLibrary.so"
        with path.open("xb") as output:
            output.write(b"unexpected dependency")
        try:
            result, evidence = self.assess()
            self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
            self.assertIn("Runtime integrity", result.reason)
            self.assertIsNone(evidence)
            receipt = self.backend.check(self.case, self.claim, self.formalization)
            self.assertEqual(receipt.payload["run_status"], "POLICY_BLOCKED")
            self.assertIsNone(receipt.payload["returncode"])
            self.assertIsNone(receipt.payload["audit"])
            with self.assertRaises(IntegrityError):
                NativeLeanBackend(ROOT)
        finally:
            path.unlink()
        self.backend.verify_environment()


if __name__ == "__main__":
    unittest.main()
