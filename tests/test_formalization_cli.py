"""Run the actual offline CLI in a disposable project with no installed Lean."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FormalizationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        shutil.copytree(
            ROOT / "mra", self.project / "mra", ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copytree(ROOT / "evals/formalization", self.project / "evals/formalization")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mra", *args],
            cwd=self.project,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(self.project)},
        )

    def create(self, file="clear-nat.json"):
        result = self.run_cli("formalize", "--input", "evals/formalization/" + file)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def review_args(self, draft):
        return [
            "review-formalization",
            draft["artifact_directory"],
            "--draft-fingerprint",
            draft["draft_fingerprint"],
            "--candidate",
            draft["candidate_fingerprints"][0],
            "--reviewer",
            "local-cli-test-reviewer",
            "--decision",
            "accept",
            "--note",
            "Checked explicit domain, quantifier, assumptions and conclusion",
        ]

    def test_clear_input_and_saved_report_work_without_lean(self):
        draft = self.create()
        self.assertEqual(draft["status"], "CANDIDATES_READY")
        self.assertEqual(draft["producer"]["kind"], "fixture")
        self.assertEqual(draft["math_verdict"], "UNKNOWN")
        self.assertEqual(draft["evidence_level"], "E0_CANDIDATE")
        self.assertFalse(draft["can_execute_lean"])
        result = self.run_cli("formalization-report", draft["artifact_directory"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["draft_fingerprint"], draft["draft_fingerprint"])
        self.assertFalse((self.project / ".runtime").exists())

    def test_ambiguous_and_unsupported_input_are_distinct(self):
        ambiguous = self.create("ambiguous.json")
        self.assertEqual(ambiguous["math_verdict"], "NEEDS_CLARIFICATION")
        self.assertTrue(ambiguous["questions"])
        unknown = self.create("unsupported.json")
        self.assertEqual(unknown["status"], "UNSUPPORTED")
        self.assertEqual(unknown["math_verdict"], "UNKNOWN")
        self.assertFalse(unknown["candidates"])

    def test_acceptance_is_recorded_without_proof_or_artifact_overwrite(self):
        draft = self.create()
        original = (Path(draft["artifact_directory"]) / "draft.json").read_bytes()
        result = self.run_cli(*self.review_args(draft))
        self.assertEqual(result.returncode, 0, result.stderr)
        review = json.loads(result.stdout)
        self.assertEqual(review["review_status"], "ACCEPTED")
        self.assertEqual(review["math_verdict"], "UNKNOWN")
        self.assertFalse(review["can_execute_lean"])
        self.assertTrue(Path(review["artifact_file"]).is_file())
        self.assertEqual((Path(draft["artifact_directory"]) / "draft.json").read_bytes(), original)
        self.assertFalse((self.project / "artifacts/runs").exists())

    def test_stale_review_and_ambiguous_acceptance_fail(self):
        draft = self.create()
        args = self.review_args(draft)
        args[args.index("--draft-fingerprint") + 1] = "stale-version"
        self.assertEqual(self.run_cli(*args).returncode, 1)
        ambiguous = self.create("ambiguous.json")
        args = self.review_args(draft)
        args[1] = ambiguous["artifact_directory"]
        self.assertEqual(self.run_cli(*args).returncode, 1)

    def test_clarification_revision_preserves_id_and_invalidates_old_draft(self):
        previous = self.create("ambiguous.json")
        source = {**previous["source"], "version": 2, "statement": "对任意自然数 n，有 n + 0 = n。"}
        (self.project / "revision.json").write_text(json.dumps(source), encoding="utf-8")
        result = self.run_cli(
            "formalize", "--input", "revision.json", "--revise", previous["artifact_directory"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        draft = json.loads(result.stdout)
        self.assertEqual(draft["source"]["version"], 2)
        self.assertEqual(draft["status"], "CANDIDATES_READY")
        self.assertNotEqual(draft["draft_fingerprint"], previous["draft_fingerprint"])
        source["version"] = 1
        (self.project / "revision.json").write_text(json.dumps(source), encoding="utf-8")
        self.assertEqual(
            self.run_cli(
                "formalize", "--input", "revision.json", "--revise", previous["artifact_directory"]
            ).returncode,
            1,
        )

    def test_deadline_failure_is_unknown_and_returns_nonzero(self):
        result = self.run_cli(
            "formalize", "--input", "evals/formalization/clear-nat.json", "--timeout", "0.000000001"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        draft = json.loads(result.stdout)
        self.assertEqual(draft["run_status"], "TIMEOUT")
        self.assertEqual(draft["math_verdict"], "UNKNOWN")

    def test_invalid_json_unknown_fields_and_modified_bundle_fail_cleanly(self):
        for text in ('{"broken"', '{"id":"x","math_verdict":"PROVED"}'):
            (self.project / "invalid.json").write_text(text, encoding="utf-8")
            result = self.run_cli("formalize", "--input", "invalid.json")
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)
        draft = self.create()
        (Path(draft["artifact_directory"]) / "draft.json").write_text("{}\n")
        result = self.run_cli(*self.review_args(draft))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_review_revocation_retry_and_report_show_current_state(self):
        draft = self.create()
        args = [*self.review_args(draft), "--event-id", "cli-review-request"]
        first = self.run_cli(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        event = json.loads(first.stdout)
        self.assertEqual(event["review_state"]["candidates"][0]["status"], "ACCEPTED")
        repeated = self.run_cli(*args)
        self.assertEqual(json.loads(repeated.stdout)["sequence"], event["sequence"])
        revoke = [
            "revoke-formalization",
            draft["artifact_directory"],
            "--draft-fingerprint",
            draft["draft_fingerprint"],
            "--candidate",
            draft["candidate_fingerprints"][0],
            "--review-id",
            event["event_id"],
            "--reviewer",
            "local-cli-test-reviewer",
            "--note",
            "Withdrawn explicitly",
            "--event-id",
            "cli-revoke-request",
        ]
        result = self.run_cli(*revoke)
        self.assertEqual(result.returncode, 0, result.stderr)
        withdrawn = json.loads(result.stdout)
        self.assertEqual(withdrawn["review_state"]["candidates"][0]["status"], "REVOKED")
        self.assertFalse(withdrawn["can_execute_lean"])
        self.assertEqual(self.run_cli(*revoke).returncode, 0)
        report = json.loads(
            self.run_cli("formalization-report", draft["artifact_directory"]).stdout
        )
        self.assertEqual(len(report["review_state"]["history"]), 2)
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertFalse((self.project / ".runtime").exists())

    def test_source_revision_persists_parent_and_blocks_old_review(self):
        draft = self.create()
        self.assertEqual(self.run_cli(*self.review_args(draft)).returncode, 0)
        revision = {**draft["source"], "version": 2, "statement": "所有自然数都等于零。"}
        (self.project / "revision.json").write_text(json.dumps(revision), encoding="utf-8")
        result = self.run_cli(
            "formalize", "--input", "revision.json", "--revise", draft["artifact_directory"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = json.loads(result.stdout)
        self.assertEqual(
            updated["review_state"]["revision_parent"], Path(draft["artifact_directory"]).name
        )
        old = json.loads(self.run_cli("formalization-report", draft["artifact_directory"]).stdout)
        self.assertEqual(old["review_state"]["candidates"][0]["status"], "SUPERSEDED")
        result = self.run_cli(*self.review_args(draft))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_legacy_registration_never_imports_old_review_files_as_approval(self):
        draft = self.create()
        original = Path(draft["artifact_directory"])
        legacy = original.parent / "legacy-copy"
        shutil.copytree(original, legacy)
        (legacy / "reviews").mkdir()
        (legacy / "reviews/old.json").write_text('{"review_status":"ACCEPTED"}')
        result = self.run_cli("formalization-report", str(legacy))
        self.assertEqual(
            json.loads(result.stdout)["review_state"]["candidates"][0]["status"], "UNREGISTERED"
        )
        args = self.review_args(draft)
        args[1] = str(legacy)
        self.assertEqual(self.run_cli(*args).returncode, 1)
        result = self.run_cli("register-formalization", str(legacy))
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)["review_state"]
        self.assertEqual(state["candidates"][0]["status"], "UNREVIEWED")
        self.assertEqual(state["history"], [])

    def test_conflicting_event_id_and_corrupt_database_fail_cleanly(self):
        draft = self.create()
        args = [*self.review_args(draft), "--event-id", "one-request"]
        self.assertEqual(self.run_cli(*args).returncode, 0)
        args[args.index("--decision") + 1] = "reject"
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        (self.project / "artifacts/formalization/review-state.sqlite3").write_bytes(
            b"broken database"
        )
        result = self.run_cli("formalization-report", draft["artifact_directory"])
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
