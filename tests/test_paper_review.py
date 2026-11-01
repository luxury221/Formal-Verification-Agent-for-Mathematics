"""Paper review verdicts are exercised with actual Lean, never fabricated receipts."""

import copy
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
from mra.evidence import EvidenceGate
from mra.paper_review import PaperReviewStore, prepare_review
from mra.tasks import WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def example(name="true-claim-bad-proof"):
    return json.loads((ROOT / "evals/paper-review-v1" / (name + ".json")).read_text())


def submission(bundle, request_id=None):
    return {
        "request_id": request_id or "paper-test-" + uuid.uuid4().hex,
        "bundle": bundle,
        "plan_fingerprint": prepare_review(bundle)["plan_fingerprint"],
        "reviewer": "repository-paper-review-test-author",
        "confirmed": True,
    }


class PaperReviewProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        shutil.copytree(ROOT / "evals/paper-review-v1", self.root / "evals/paper-review-v1")
        self.store = PaperReviewStore(self.root)

    def test_plan_is_offline_and_binds_original_lines_assumptions_and_exact_obligations(self):
        bundle = example("missing-condition")
        plan = prepare_review(bundle)
        self.assertEqual(plan, prepare_review(copy.deepcopy(bundle)))
        self.assertEqual(
            plan["budget"], {"maximum_checks": 2, "worker_seconds_per_check": 30, "model_calls": 0}
        )
        self.assertEqual(plan["obligations"][1]["span"], [2, 2])
        self.assertIn("共同因子", plan["obligations"][1]["quote"])
        self.assertIn("→", plan["obligations"][0]["formal_statement"])
        self.assertIn("*", plan["obligations"][0]["formal_statement"])
        self.assertNotIn("≠", plan["obligations"][0]["formal_statement"])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_steps_use_explicit_earlier_dependencies_and_no_unverified_external_lemma(self):
        for dependencies in (["s2"], ["Mathlib.unchecked"], ["claim"], ["s1", "s1"]):
            bundle = example("valid-proof")
            bundle["steps"][0 if dependencies != ["s1", "s1"] else 1]["depends_on"] = dependencies
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                prepare_review(bundle)

    def test_source_spans_scope_unsupported_code_and_structural_limits_are_rejected(self):
        variants = []
        bundle = example()
        bundle["steps"][0]["span"] = [0, 2]
        variants.append(bundle)
        bundle = example()
        bundle["steps"][0]["span"] = [3, 2]
        variants.append(bundle)
        bundle = example()
        bundle["variables"] = ["n", "n"]
        variants.append(bundle)
        bundle = example()
        bundle["variables"] = ["n) := by sorry"]
        variants.append(bundle)
        bundle = example()
        bundle["conclusion"] = {"op": "lean", "source": "by sorry"}
        variants.append(bundle)
        bundle = example()
        bundle["steps"][0]["plan"]["strategy"] = "run_cmd"
        variants.append(bundle)
        bundle = example()
        bundle["steps"] *= 3
        variants.append(bundle)
        for bundle in variants:
            with self.subTest(bundle=bundle), self.assertRaises((ValueError, TypeError)):
                prepare_review(bundle)

    def test_any_source_version_formula_premise_or_plan_change_invalidates_confirmation(self):
        original = example()
        data = submission(original)
        changes = [
            lambda b: b["document"].update(version=2),
            lambda b: b["document"].update(text=b["document"]["text"] + "\n附注"),
            lambda b: b["steps"][1].update(depends_on=[]),
            lambda b: b.update(claim_plan=None),
            lambda b: b.update(
                assumptions=[
                    {
                        "op": "eq",
                        "left": {"op": "var", "name": "n"},
                        "right": {"op": "num", "value": 0},
                    }
                ]
            ),
        ]
        for change in changes:
            altered = copy.deepcopy(data)
            change(altered["bundle"])
            with self.subTest(bundle=altered["bundle"]), self.assertRaises(ValueError):
                self.store.submit(altered)
        self.assertEqual(self.store.list_reviews(), [])

    def test_idempotent_submission_survives_restart_and_cannot_change_reviewer_or_content(self):
        data = submission(example())
        first = self.store.submit(data)
        self.assertEqual(PaperReviewStore(self.root).submit(data), first)
        altered = {**data, "reviewer": "another-name"}
        with self.assertRaises(ValueError):
            self.store.submit(altered)
        self.assertEqual(first["claim_verdict"], "UNKNOWN")
        self.assertEqual(first["author_proof_verdict"], "UNKNOWN")
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_empty_or_missing_explicit_confirmation_cannot_queue_a_review(self):
        for changes in ({"confirmed": False}, {"reviewer": "   "}, {"plan_fingerprint": "stale"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit({**submission(example()), **changes})
        self.assertEqual(self.store.list_reviews(), [])

    def test_cancel_revoke_recovery_and_shared_lock_do_not_promote_saved_metadata(self):
        first = self.store.submit(submission(example()))
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once()
        cancelled = self.store.cancel(first["review_id"])
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        revoked = self.store.cancel(first["review_id"], revoke=True)
        self.assertEqual(revoked["author_proof_verdict"], "UNKNOWN")
        second = self.store.submit(submission(example()))
        self.store.mutate(second["review_id"], lambda b: b.update(state="RUNNING"))
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        recovered = self.store.report(second["review_id"])
        self.assertEqual((recovered["state"], recovered["error"]), ("FAILED", "INTERRUPTED"))
        self.assertFalse(self.store.evidence(second["review_id"], "claim")["available"])

    def test_changed_database_body_is_rejected(self):
        report = self.store.submit(submission(example()))
        with self.store.transaction() as db:
            db.execute(
                "UPDATE reviews SET body=? WHERE id=?",
                (canonical({"forged": "PROVED"}).decode(), report["review_id"]),
            )
        with self.assertRaises(ValueError):
            self.store.report(report["review_id"])

    def test_new_http_resources_preview_guards_submission_and_no_model_requirement(self):
        with running_server(self.root) as server:
            self.assertEqual(request(server, "GET", "/papers.js")[0], 200)
            status, samples, _ = request(server, "GET", "/v1/paper-examples")
            self.assertEqual(status, 200)
            self.assertEqual(len(samples["examples"]), 3)
            status, plan, _ = request(
                server, "POST", "/v1/paper-reviews/prepare", {"bundle": example()}
            )
            self.assertEqual(status, 200)
            self.assertEqual(plan["budget"]["model_calls"], 0)
            self.assertFalse((self.root / "artifacts/runs").exists())
            data = submission(example())
            status, report, _ = request(server, "POST", "/v1/paper-reviews", data)
            self.assertEqual(status, 202)
            self.assertEqual(report["state"], "QUEUED")
            path = "/v1/paper-reviews/" + report["review_id"]
            self.assertEqual(request(server, "GET", path)[0], 200)
            self.assertEqual(
                request(server, "GET", "/v1/paper-reviews")[1]["reviews"][0]["review_id"],
                report["review_id"],
            )
            self.assertEqual(request(server, "POST", path + "/cancel", {})[1]["state"], "CANCELLED")
            for route in ("/v1/paper-reviews", path, "/v1/paper-examples"):
                self.assertEqual(
                    request(server, "GET", route, headers={"Origin": "https://outside.example"})[0],
                    403,
                )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/paper-reviews/prepare",
                    {"bundle": example()},
                    headers={"X-MRA-CSRF": "stale"},
                )[0],
                403,
            )
            self.assertEqual(
                request(server, "GET", path + "/evidence/claim")[1]["available"], False
            )

    def test_actual_cli_plan_only_and_bad_annotations_do_not_execute_lean(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra",
                "paper-review-plan",
                "--input",
                "evals/paper-review-v1/true-claim-bad-proof.json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["budget"]["maximum_checks"], 3)
        path = self.root / "bad.json"
        path.write_text('{"schema":"bad"}')
        result = subprocess.run(
            [sys.executable, "-m", "mra", "paper-review-plan", "--input", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1 for actual workers"
)
class PaperReviewRealLeanTests(unittest.TestCase):
    def run_review(self, bundle):
        store = PaperReviewStore(ROOT)
        queued = store.submit(submission(bundle))
        result = store.work_once(queued["review_id"])
        self.assertEqual(result["worker_state"], "EXECUTED")
        report = result["review"]
        self.assertEqual(report["state"], "COMPLETED", report)
        self.assertEqual(report["model_calls"], 0)
        return store, report

    def test_true_claim_and_refuted_author_step_are_separate_and_unverified_dependency_is_skipped(
        self,
    ):
        store, report = self.run_review(example())
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("PROVED", "GAP_FOUND")
        )
        self.assertEqual([item["key"] for item in report["checks"]], ["claim", "s1"])
        self.assertEqual(report["diagnostics"][0]["kind"], "INFERENCE_REFUTED")
        self.assertEqual(report["diagnostics"][0]["span"], [2, 2])
        evidence = store.evidence(report["review_id"], "s1")
        payload = evidence["receipt"]["payload"]
        self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertEqual(payload["replay"]["returncode"], 0)

    def test_complete_authored_steps_pass_and_tampering_or_revocation_suppresses_both_channels(
        self,
    ):
        store, report = self.run_review(example("valid-proof"))
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("PROVED", "PROVED")
        )
        self.assertEqual(len(report["checks"]), 3)
        path = Path(report["checks"][-1]["result"]["artifact_directory"]) / "report.json"
        raw = path.read_bytes()
        path.write_bytes(raw + b"\n")
        try:
            changed = store.report(report["review_id"])
            self.assertEqual(changed["artifact_status"], "CHANGED_OR_MISSING")
            self.assertEqual(
                (changed["claim_verdict"], changed["author_proof_verdict"]), ("UNKNOWN", "UNKNOWN")
            )
            self.assertFalse(store.evidence(report["review_id"], "claim")["available"])
        finally:
            path.write_bytes(raw)
        revoked = store.cancel(report["review_id"], revoke=True)
        self.assertEqual(
            (revoked["claim_verdict"], revoked["author_proof_verdict"]), ("UNKNOWN", "UNKNOWN")
        )

    def test_missing_multiplicative_condition_has_a_real_counterexample(self):
        _, report = self.run_review(example("missing-condition"))
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("REFUTED", "GAP_FOUND")
        )
        self.assertTrue(
            all(
                item["result"]["evidence_level"] == "E4_ALIGNED_AND_VERIFIED"
                for item in report["checks"]
            )
        )

    def test_failed_strategy_is_unknown_and_never_labels_the_author_wrong(self):
        bundle = example("valid-proof")
        bundle["claim_plan"] = {"direction": "prove", "strategy": "decide", "witnesses": []}
        bundle["steps"][0]["plan"] = None
        _, report = self.run_review(bundle)
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("UNKNOWN", "UNKNOWN")
        )
        self.assertEqual([item["key"] for item in report["checks"]], ["claim"])
        self.assertTrue(
            all(item["kind"] == "UNRESOLVED_OBLIGATION" for item in report["diagnostics"])
        )

    def test_revocation_after_real_gate_cannot_publish_either_channel(self):
        store = PaperReviewStore(ROOT)
        queued = store.submit(submission(example("valid-proof")))
        original = EvidenceGate.assess

        def revoke_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            store.cancel(queued["review_id"], revoke=True)
            return result

        with patch.object(EvidenceGate, "assess", revoke_after_gate):
            report = store.work_once(queued["review_id"])["review"]
        self.assertEqual(report["state"], "CANCELLED")
        self.assertEqual(
            (report["claim_verdict"], report["author_proof_verdict"]), ("UNKNOWN", "UNKNOWN")
        )
        self.assertEqual(len(report["checks"]), 1)


if __name__ == "__main__":
    unittest.main()
