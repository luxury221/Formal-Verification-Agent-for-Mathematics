"""Strict certificate capability boundary and actual two-stage Lean verification."""

from dataclasses import asdict, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mra.certificates import (
    CERTIFICATE_FILE,
    MAX_BYTES,
    MODE,
    parse_certificate,
    read_certificate,
    target_contract,
    target_descriptor,
)
from mra.domain import Alignment, EvidenceLevel, RunStatus, Verdict, canonical, digest
from mra.evidence import EvidenceGate
from mra.harness import MinimalResearchEngine
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
CASES = {case["id"]: case for case in json.loads((ROOT / "evals/golden_cases.json").read_text())}


def const(name, levels=()):
    return {"op": "const", "name": name, "levels": list(levels)}


def app(fn, *args):
    for arg in args:
        fn = {"op": "app", "fn": fn, "arg": arg}
    return fn


def certificate(proof=None, case_id="nat_add_zero"):
    return {
        **target_descriptor(CASES[case_id])["certificate_header"],
        "proof": const("Nat.add_zero") if proof is None else proof,
    }


def eq(left, right):
    return app(const("Eq", [1]), const("Nat"), left, right)


class CertificateProtocolTests(unittest.TestCase):
    def parse(self, data):
        return parse_certificate(canonical(data), *target_contract(CASES["nat_add_zero"]))

    def test_exact_target_and_canonical_certificate_are_bound(self):
        data = certificate()
        parsed = self.parse(data)
        raw = json.dumps(data, indent=4).encode()
        self.assertEqual(parsed, parse_certificate(raw, *target_contract(CASES["nat_add_zero"])))
        self.assertEqual(parsed.nodes, 1)
        for field in ("claim_fingerprint", "formalization_fingerprint", "schema_version", "format"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.parse({**data, field: "changed"})

    def test_source_tactics_and_unrecognized_fields_cannot_cross_boundary(self):
        for proof in (
            "by rfl",
            {"op": "run_cmd", "source": 'IO.FS.writeFile "marker" "bad"'},
            {"op": "mvar", "name": "x"},
            {"op": "const", "name": "Nat.add_zero", "levels": [], "code": "run_tac"},
        ):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.parse(certificate(proof))
        with self.assertRaises(ValueError):
            self.parse({**certificate(), "statement": "True"})

    def test_duplicate_json_keys_invalid_unicode_and_nonobjects_fail(self):
        claim, formalization = target_contract(CASES["nat_add_zero"])
        for raw in (b'{"proof":1,"proof":2}', b"[]", b"\xff", b'{"proof":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_certificate(raw, claim, formalization)

    def test_deep_json_and_expression_depth_fail_with_bounded_error(self):
        claim, formalization = target_contract(CASES["nat_add_zero"])
        with self.assertRaises(ValueError):
            parse_certificate(
                b'{"proof":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}", claim, formalization
            )
        proof = const("Nat.add_zero")
        for _ in range(65):
            proof = {"op": "lam", "type": const("Nat"), "body": proof}
        with self.assertRaisesRegex(ValueError, "depth"):
            self.parse(certificate(proof))

    def test_balanced_large_expression_hits_node_budget(self):
        proof = {"op": "bvar", "index": 0}
        for _ in range(10):
            proof = app(proof, proof)
        data = certificate({"op": "lam", "type": const("Nat"), "body": proof})
        self.assertLess(len(canonical(data)), MAX_BYTES)
        with self.assertRaisesRegex(ValueError, "node budget"):
            self.parse(data)

    def test_raw_input_reading_stops_at_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "certificate.json"
            path.write_bytes(b" " * (MAX_BYTES + 500))
            raw = read_certificate(path)
            self.assertEqual(len(raw), MAX_BYTES + 1)
            with self.assertRaises(ValueError):
                parse_certificate(raw, *target_contract(CASES["nat_add_zero"]))

    def test_names_and_universe_levels_cannot_encode_source(self):
        for name in (
            "Nat.add_zero\nrun_cmd",
            "«Nat.add_zero»",
            "../Nat",
            "Nat..add_zero",
            "Nat.0",
            "x" * 193,
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.parse(certificate(const(name)))
        for levels in ([True], [-1], [9], [0] * 9, "0"):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                self.parse(certificate({"op": "const", "name": "Eq.refl", "levels": levels}))

    def test_unbound_variables_and_noninteger_atoms_are_rejected(self):
        for proof in (
            {"op": "bvar", "index": 0},
            {"op": "nat", "value": True},
            {"op": "nat", "value": 2**32},
            {"op": "sort", "level": -1},
        ):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.parse(certificate(proof))

    def test_other_reference_targets_are_not_implicitly_enabled(self):
        with self.assertRaisesRegex(ValueError, "not enabled"):
            target_descriptor(CASES["sandbox_boundary"])
        claim, formalization = target_contract(CASES["nat_add_zero"])
        with self.assertRaises(ValueError):
            parse_certificate(
                canonical(certificate()), replace(claim, id="natv1_mul_one"), formalization
            )

    def test_version_assumption_and_direction_changes_invalidate_binding(self):
        claim, formalization = target_contract(CASES["nat_add_zero"])
        for changed in (replace(claim, version=2), replace(claim, assumptions=("False",))):
            with self.subTest(claim=changed), self.assertRaises(ValueError):
                parse_certificate(canonical(certificate()), changed, formalization)
        with self.assertRaises(ValueError):
            parse_certificate(
                canonical(certificate()), claim, replace(formalization, direction="refute")
            )


class CertificateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(
            ROOT / "mra", self.root / "mra", ignore=shutil.ignore_patterns("__pycache__")
        )
        (self.root / "evals").mkdir()
        shutil.copy2(ROOT / "evals/golden_cases.json", self.root / "evals/golden_cases.json")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mra", *args],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(self.root)},
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_target_inspection_requires_no_lean_and_provides_no_proof(self):
        result = self.cli("certificate-target", "nat_add_zero")
        self.assertEqual(result.returncode, 0, result.stderr)
        target = json.loads(result.stdout)
        self.assertEqual(target["certificate_header"]["format"], MODE)
        self.assertEqual(target["math_verdict"], "UNKNOWN")
        self.assertNotIn("proof", target["certificate_header"])
        self.assertFalse((self.root / "artifacts").exists())

    def test_source_payload_is_rejected_before_runtime_or_artifact_creation(self):
        data = certificate({"op": "run_cmd", "source": 'IO.println "FAKE_PASS"'})
        (self.root / "attack.json").write_bytes(canonical(data))
        result = self.cli("check-certificate", "nat_add_zero", "--input", "attack.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported certificate constructor", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "artifacts").exists())

    def test_valid_certificate_still_requires_real_sealed_runtime(self):
        (self.root / "valid.json").write_bytes(canonical(certificate()))
        result = self.cli("check-certificate", "nat_add_zero", "--input", "valid.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Runtime snapshot", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_and_disabled_targets_fail_cleanly(self):
        for case_id in ("unknown", "sandbox_boundary"):
            result = self.cli("certificate-target", case_id)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean")
class CertificateRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = MinimalResearchEngine(ROOT)
        cls.backend = cls.engine.backend
        cls.claim, cls.formalization = target_contract(CASES["nat_add_zero"])
        cls.result = cls.engine.run_case("nat_add_zero", certificate_raw=canonical(certificate()))
        if cls.result.evidence_level != EvidenceLevel.E3:
            raise AssertionError(cls.result)
        cls.directory = Path(cls.result.artifact_directory)
        cls.receipt = json.loads((cls.directory / "receipt.json").read_text())

    def run_proof(self, proof, case_id="nat_add_zero", **kwargs):
        return self.engine.run_case(
            case_id, certificate_raw=canonical(certificate(proof, case_id)), **kwargs
        )

    def test_data_only_certificate_is_bound_to_two_real_processes(self):
        payload = self.receipt["payload"]
        self.assertEqual(payload["input_mode"], MODE)
        self.assertFalse(payload["audit"]["candidate_source_executed"])
        self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertIn(CERTIFICATE_FILE, payload["files"])
        self.assertEqual(self.result.math_verdict, Verdict.UNKNOWN)
        source = (self.directory / "input/Candidate.lean").read_text()
        self.assertIn("kernel", source)
        self.assertNotIn("by intro n; exact Nat.add_zero n", source)

    def test_real_certificate_with_named_alignment_can_reach_e4(self):
        from mra.domain import CheckReceipt

        alignment = Alignment(
            self.claim.fingerprint,
            digest(asdict(self.formalization)),
            "certificate-test-author",
            "Reviewed exact Nat target and original statement",
        )
        result, evidence = self.engine.gate.assess(
            self.claim, self.formalization, CheckReceipt(**self.receipt), alignment
        )
        self.assertEqual(result.math_verdict, Verdict.PROVED)
        self.assertEqual(evidence.level, EvidenceLevel.E4)

    def test_alternate_lambda_proof_is_actually_checked(self):
        result = self.run_proof(
            {
                "op": "lam",
                "type": const("Nat"),
                "body": app(const("Nat.add_zero"), {"op": "bvar", "index": 0}),
            }
        )
        self.assertEqual(result.evidence_level, EvidenceLevel.E3, result.reason)

    def test_counterexample_certificate_proves_complete_negation(self):
        raw = (ROOT / "evals/certificates/not-all-zero.json").read_bytes()
        result = self.engine.run_case(
            "false_all_nat_zero", "certificate-test-author", certificate_raw=raw
        )
        self.assertEqual(result.math_verdict, Verdict.REFUTED, result.reason)
        self.assertEqual(result.evidence_level, EvidenceLevel.E4)

    def test_valid_proof_of_wrong_proposition_is_not_promoted(self):
        result = self.run_proof(app(const("Eq.refl", [1]), const("Nat"), {"op": "nat", "value": 0}))
        self.assertEqual(result.run_status, RunStatus.COMPLETED)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertEqual(result.evidence_level, EvidenceLevel.E2)
        self.assertIn(
            "MRA_CERTIFICATE_KERNEL_REJECTED",
            (Path(result.artifact_directory) / "stdout.log").read_text(),
        )

    def test_sorry_axiom_cannot_issue_certificate_evidence(self):
        var, zero = {"op": "bvar", "index": 0}, {"op": "nat", "value": 0}
        proposition = {
            "op": "forall",
            "type": const("Nat"),
            "body": eq(app(const("Nat.add"), var, zero), var),
        }
        result = self.run_proof(
            app(const("sorryAx", [0]), proposition, const("Bool.false")),
            reviewer="certificate-test-author",
        )
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED, result.reason)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)

    def test_unsafe_constant_is_rejected_by_trusted_interpreter(self):
        result = self.run_proof(const("unsafeCast", [0, 0]))
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIn(
            "MRA_CERTIFICATE_POLICY_UNSAFE_CONSTANT",
            (Path(result.artifact_directory) / "stdout.log").read_text(),
        )

    def test_io_constant_is_data_and_cannot_act_as_a_proof(self):
        result = self.run_proof(const("IO.println", [0]))
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertEqual(result.evidence_level, EvidenceLevel.E2)
        self.assertNotEqual(result.run_status, RunStatus.EXECUTION_ERROR)

    def test_unknown_constant_is_not_resolved_through_import_or_source(self):
        result = self.run_proof(const("Untrusted.result"))
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIn(
            "MRA_CERTIFICATE_POLICY_UNKNOWN_CONSTANT",
            (Path(result.artifact_directory) / "stdout.log").read_text(),
        )

    def test_certificate_file_change_invalidates_authentic_receipt(self):
        from mra.domain import CheckReceipt

        path = self.directory / CERTIFICATE_FILE
        original = path.read_bytes()
        try:
            path.write_bytes(canonical(certificate(const("Nat.zero_add"))))
            result, evidence = self.engine.gate.assess(
                self.claim, self.formalization, CheckReceipt(**self.receipt)
            )
            self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
            self.assertIsNone(evidence)
        finally:
            path.write_bytes(original)

    def test_real_timeout_never_becomes_a_refutation(self):
        backend = NativeLeanBackend(ROOT, timeout_seconds=0.001)
        receipt = backend.check_certificate(
            CASES["nat_add_zero"], self.claim, self.formalization, canonical(certificate())
        )
        result, evidence = EvidenceGate(ROOT, backend, backend.policy).assess(
            self.claim, self.formalization, receipt
        )
        self.assertEqual(result.run_status, RunStatus.TIMEOUT)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertIsNone(evidence)

    def test_replay_cancellation_suppresses_certificate_evidence(self):
        cancelled = False

        def cancel(stage, directory):
            nonlocal cancelled
            if stage == "REPLAY_RUNNING":
                cancelled = True

        result = self.run_proof(
            const("Nat.add_zero"), cancel_requested=lambda: cancelled, checkpoint=cancel
        )
        self.assertEqual(result.run_status, RunStatus.CANCELLED)
        self.assertEqual(result.evidence_level, EvidenceLevel.E0)

    def test_reference_allowlist_and_target_contract_cannot_be_bypassed(self):
        for case, claim, formalization, raw in (
            (
                {**CASES["nat_add_zero"], "proof": "rfl"},
                self.claim,
                self.formalization,
                canonical(certificate()),
            ),
            (
                CASES["nat_add_zero"],
                replace(self.claim, formal_statement="True"),
                self.formalization,
                canonical(certificate()),
            ),
            (CASES["nat_add_zero"], self.claim, self.formalization, None),
        ):
            with self.subTest(claim=claim, raw=raw), self.assertRaises(ValueError):
                self.backend.check_certificate(case, claim, formalization, raw)

    def test_gate_rejects_authenticated_wrong_mode_digest_and_source_audit(self):
        from mra.domain import CheckReceipt

        for field, value in (
            ("input_mode", "untrusted-source"),
            ("certificate_digest", "0" * 64),
            ("candidate_source_executed", True),
        ):
            payload = json.loads(json.dumps(self.receipt["payload"]))
            target = payload["audit"] if field == "candidate_source_executed" else payload
            target[field] = value
            signature = hmac.new(self.backend._key, canonical(payload), hashlib.sha256).hexdigest()
            receipt = CheckReceipt(payload, signature)
            self.assertTrue(self.backend.authentic(receipt))
            with self.subTest(field=field):
                result, evidence = self.engine.gate.assess(self.claim, self.formalization, receipt)
                self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
                self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
