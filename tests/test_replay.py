"""Proof-data protocol tests and actual isolated Lean kernel replay attacks."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import unittest
import uuid

from mra.domain import (
    canonical,
    Claim,
    CheckReceipt,
    Formalization,
    Verdict,
    EvidenceLevel,
    RunStatus,
)
from mra.harness import MinimalResearchEngine
from mra.replay import extract_export, render_replay, MAX_EXPORT_BYTES, REPLAY_FILES

ROOT = Path(__file__).resolve().parents[1]
META = {"meta": {"format": {"version": "3.1.0"}, "lean": {"version": "4.35.0"}}}


def frame(*rows):
    return "MRA_EXPORT_BEGIN\n" + "\n".join(json.dumps(row) for row in rows) + "\nMRA_EXPORT_END\n"


class ReplayProtocolTests(unittest.TestCase):
    def test_export_requires_one_pair_of_ordered_boundaries(self):
        for output in (
            "",
            "MRA_EXPORT_BEGIN\nMRA_EXPORT_END",
            "MRA_EXPORT_END\nMRA_EXPORT_BEGIN",
            frame(META) + frame(META),
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                extract_export(output)

    def test_strict_json_rows_reject_duplicates_and_nonobjects(self):
        for output in (
            frame(META, []),
            frame(META, {}),
            'MRA_EXPORT_BEGIN\n{"meta":{},"meta":{}}\nMRA_EXPORT_END\n',
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                extract_export(output)

    def test_export_metadata_must_match_pinned_format_and_lean_version(self):
        for value in (
            None,
            [],
            {},
            {"format": []},
            {"format": {"version": "3.0.0"}, "lean": {"version": "4.35.0"}},
            {"format": {"version": "3.1.0"}, "lean": {"version": "4.34.0"}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_export(frame({"meta": value}))

    def test_export_has_a_hard_byte_limit(self):
        with self.assertRaisesRegex(ValueError, "oversized"):
            extract_export(frame(META, {"strVal": "x" * MAX_EXPORT_BYTES, "ie": 0}))

    def test_logs_outside_export_are_not_treated_as_proof_data(self):
        raw = extract_export(
            "arbitrary diagnostic\n" + frame(META, {"ie": 0, "bvar": 0}) + "MRA_AUDIT:{}\n"
        )
        self.assertEqual(len(raw.splitlines()), 2)
        self.assertNotIn(b"arbitrary", raw)

    def test_trusted_template_has_no_candidate_module_import(self):
        rendered = render_replay(ROOT, "∀ n : Nat, n + 0 = n")
        self.assertNotIn("__MRA_EXPECTED_STATEMENT__", rendered)
        self.assertNotIn("import Candidate", rendered)
        self.assertIn("base.toKernelEnv.replay locals", rendered)
        self.assertIn("kernel.addDeclCore", rendered)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean")
class ReplayRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = MinimalResearchEngine(ROOT)
        cls.backend = cls.engine.backend
        result = cls.engine.run_case("nat_add_zero")
        if result.evidence_level != EvidenceLevel.E3:
            raise AssertionError(f"Real replay fixture failed: {result.reason}")
        cls.directory = Path(result.artifact_directory)
        cls.receipt = json.loads((cls.directory / "receipt.json").read_text())
        cls.original = (cls.directory / "replay/proof.ndjson").read_bytes()
        cls.report = json.loads((cls.directory / "report.json").read_text())

    def rows(self):
        return [json.loads(row) for row in self.original.splitlines()]

    @staticmethod
    def names(rows):
        names = {0: ""}
        for row in rows:
            if "in" in row and "str" in row:
                data = row["str"]
                names[row["in"]] = ".".join(filter(None, (names[data["pre"]], data["str"])))
            elif "in" in row and "num" in row:
                data = row["num"]
                names[row["in"]] = names[data["pre"]] + "." + str(data["i"])
        return names

    def declaration(self, rows, name="MraCandidate.result"):
        names = self.names(rows)
        return next(
            (row, key)
            for row in rows
            for key in ("thm", "def", "opaque")
            if key in row and names[row[key]["name"]] == name
        )

    def replay(self, rows, statement="∀ n : Nat, n + 0 = n", budget=30):
        directory = ROOT / "artifacts/runs" / ("replay-attack-" + uuid.uuid4().hex)
        work = directory / "replay"
        work.mkdir(parents=True)
        data = b"\n".join(canonical(row) for row in rows) + b"\n"
        (work / "proof.ndjson").write_bytes(data)
        (work / "Candidate.lean").write_text(render_replay(ROOT, statement), encoding="utf-8")
        (work / "policy.json").write_bytes(canonical(self.backend.policy))
        self.backend.verify_environment()
        result = self.backend._run_worker(
            work,
            directory / "sandbox",
            work / "stdout.log",
            work / "stderr.log",
            budget,
            phase="REPLAY_RUNNING",
        )
        (directory / "attack-result.json").write_bytes(
            canonical({"kind": "actual-data-only-replay-test", **result})
        )
        output = (work / "stdout.log").read_text(encoding="utf-8")
        return result, output

    def test_second_process_is_required_and_bound_into_real_receipt(self):
        payload = self.receipt["payload"]
        self.assertEqual(payload["run_status"], "COMPLETED")
        self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertEqual(payload["replay"]["audit"]["replayed_declarations"], 1)
        self.assertFalse(payload["replay"]["audit"]["candidate_module_imported"])
        self.assertTrue(REPLAY_FILES.issubset(payload["files"]))
        self.assertEqual(self.report["task"]["evidence_level"], "E3_KERNEL_CHECKED")

    def test_forged_proof_expression_is_rejected_by_actual_kernel(self):
        rows = self.rows()
        row, key = self.declaration(rows)
        row[key]["value"] = row[key]["type"]
        result, output = self.replay(rows)
        self.assertNotEqual(result["returncode"], 0, output)
        self.assertIn("while replaying declaration", output)

    def test_valid_proof_of_a_different_goal_fails_kernel_bridge(self):
        result, output = self.replay(self.rows(), "∀ n : Nat, n = 0")
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("MRA_REPLAY_TARGET_MISMATCH", output)

    def test_missing_target_cannot_reuse_an_audit_string(self):
        rows = self.rows()
        row, _ = self.declaration(rows)
        rows.remove(row)
        result, output = self.replay(rows)
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("MRA_REPLAY_MISSING_THEOREM", output)

    def test_unused_new_axiom_is_rejected_before_replay(self):
        rows = self.rows()
        names = self.names(rows)
        prefix = next(index for index, name in names.items() if name == "MraCandidate")
        fresh = max(names) + 1
        target, key = self.declaration(rows)
        rows.extend(
            [
                {"in": fresh, "str": {"pre": prefix, "str": "unusedAxiom"}},
                {
                    "axiom": {
                        "name": fresh,
                        "levelParams": [],
                        "type": target[key]["type"],
                        "isUnsafe": False,
                    }
                },
            ]
        )
        result, output = self.replay(rows)
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("MRA_REPLAY_UNAPPROVED_DECLARATION", output)

    def test_unsafe_declaration_cannot_be_silently_skipped_by_replay(self):
        rows = self.rows()
        row, key = self.declaration(rows)
        data = row.pop(key)
        row["def"] = {**data, "safety": "unsafe", "hints": "opaque"}
        result, output = self.replay(rows)
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("MRA_REPLAY_UNSAFE_DECLARATION", output)

    def test_serialized_override_of_trusted_import_is_ignored(self):
        rows = self.rows()
        row, key = self.declaration(rows, "Nat.add_zero")
        row[key]["value"] = row[key]["type"]
        result, output = self.replay(rows)
        self.assertEqual(result["returncode"], 0, output)
        self.assertIn("MRA_REPLAY:", output)

    def test_duplicate_expression_index_is_rejected_by_data_parser(self):
        rows = self.rows()
        rows.append(next(row for row in rows if "ie" in row))
        result, output = self.replay(rows)
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("bound twice", output)

    def test_replay_timeout_is_an_execution_failure(self):
        result, _ = self.replay(self.rows(), budget=0.001)
        self.assertEqual(result["run_status"], "TIMEOUT")

    def test_changed_export_between_processes_blocks_pipeline_evidence(self):
        def corrupt(stage, directory):
            if stage == "REPLAY_PREPARING":
                path = Path(directory) / "replay/proof.ndjson"
                rows = [json.loads(row) for row in path.read_bytes().splitlines()]
                row, key = self.declaration(rows)
                row[key]["value"] = row[key]["type"]
                path.write_bytes(b"\n".join(canonical(row) for row in rows) + b"\n")

        result = self.engine.run_case(
            "nat_add_zero", "repository-replay-test-author", checkpoint=corrupt
        )
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertEqual(result.evidence_level, EvidenceLevel.E2)

    def test_cancellation_during_replay_publishes_no_evidence(self):
        cancelled = False

        def cancel(stage, directory):
            nonlocal cancelled
            if stage == "REPLAY_RUNNING":
                cancelled = True

        result = self.engine.run_case(
            "nat_add_zero", cancel_requested=lambda: cancelled, checkpoint=cancel
        )
        self.assertEqual(result.run_status, RunStatus.CANCELLED)
        self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
        self.assertEqual(result.evidence_level, EvidenceLevel.E0)

    def test_modified_replay_log_blocks_existing_authentic_receipt(self):
        case = self.engine.cases["nat_add_zero"]
        from mra.domain import Claim, Formalization

        claim = Claim(case["id"], 1, case["original_statement"], case["formal_statement"])
        formalization = Formalization(claim.fingerprint, claim.formal_statement)
        receipt = self.backend.check(case, claim, formalization)
        path = Path(receipt.payload["artifact_directory"]) / "replay/stdout.log"
        path.write_text("MRA_REPLAY:FAKE_PASS\n")
        result, evidence = self.engine.gate.assess(claim, formalization, receipt)
        self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
        self.assertIsNone(evidence)

    def test_gate_rejects_authenticated_incomplete_or_inconsistent_replay(self):
        # Internal negative metadata fixtures exercise the gate after HMAC verification.
        case = self.engine.cases["nat_add_zero"]
        claim = Claim(case["id"], 1, case["original_statement"], case["formal_statement"])
        formalization = Formalization(claim.fingerprint, claim.formal_statement)
        for field, value in (
            ("audit", None),
            ("pid", self.receipt["payload"]["candidate_pid"]),
            ("pid", None),
            ("replayed_declarations", 0),
            ("axioms", ["Classical.choice"]),
            ("local_dependency_axioms", ["sorryAx"]),
            ("local_axioms", ["MraCandidate.fake"]),
        ):
            payload = json.loads(json.dumps(self.receipt["payload"]))
            target = payload["replay"] if field in ("audit", "pid") else payload["replay"]["audit"]
            target[field] = value
            signature = hmac.new(self.backend._key, canonical(payload), hashlib.sha256).hexdigest()
            receipt = CheckReceipt(payload, signature)
            self.assertTrue(self.backend.authentic(receipt))
            with self.subTest(field=field, value=value):
                result, evidence = self.engine.gate.assess(claim, formalization, receipt)
                self.assertEqual(result.run_status, RunStatus.POLICY_BLOCKED)
                self.assertEqual(result.math_verdict, Verdict.UNKNOWN)
                self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
