"""The source gate must not hide failures or execute deferred runtime fixtures."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from ops.scripts import run_tests as runner


def fixture_cases():
    calls = []

    class SourceCase(unittest.TestCase):
        def test_source(self):
            calls.append("source")

    class RuntimeCase(unittest.TestCase):
        def setUp(self):
            calls.append("runtime fixture")
            raise AssertionError("Deferred fixture executed")

        def test_runtime(self):
            raise AssertionError("Deferred method executed")

    cases = [SourceCase("test_source"), RuntimeCase("test_runtime")]
    manifest = {
        "schema": "mra-test-suites-v1",
        "requirements": {
            "prepared": {"files": [".runtime/seal.json"], "description": "Actual profile"}
        },
        "runtime_contracts": [
            {"test": cases[1].id(), "requires": ["prepared"], "reason": "Reads actual seal"}
        ],
        "lean_smoke": [cases[0].id()],
    }
    return calls, cases, manifest


class TestScopeTests(unittest.TestCase):
    def temporary_root(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "tests").mkdir()
        self.addCleanup(os.chdir, Path.cwd())
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        return root

    def test_deferred_fixture_is_never_executed_even_if_environment_exists(self):
        root = self.temporary_root()
        (root / ".runtime").mkdir()
        (root / ".runtime/seal.json").write_text("{}")
        calls, cases, manifest = fixture_cases()
        requirements = runner.classify(cases, manifest)
        chosen, deferred = runner.select(cases, requirements, manifest, "source")
        runner.preflight(root, "source", chosen, requirements, manifest, {})
        success, result = runner.execute(chosen, "source", io.StringIO(), 0)
        self.assertTrue(success)
        self.assertEqual(calls, ["source"])
        self.assertEqual(result["passed"], 1)
        self.assertEqual(deferred, [{"test": cases[1].id(), **requirements[cases[1].id()]}])

    def test_unclassified_assertion_failure_is_not_converted_to_a_skip(self):
        class BrokenCase(unittest.TestCase):
            def test_broken(self):
                self.fail("Regression must fail the source gate")

        case = BrokenCase("test_broken")
        success, result = runner.execute([case], "source", io.StringIO(), 0)
        self.assertFalse(success)
        self.assertEqual(result["failures"], [case.id()])
        self.assertEqual(result["skipped"], [])

    def test_stale_overlapping_and_unknown_classifications_fail_closed(self):
        _, cases, manifest = fixture_cases()
        stale = deepcopy(manifest)
        stale["runtime_contracts"][0]["test"] += "_deleted"
        with self.assertRaisesRegex(ValueError, "Stale"):
            runner.classify(cases, stale)
        overlap = deepcopy(manifest)
        overlap["runtime_contracts"].append(deepcopy(overlap["runtime_contracts"][0]))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            runner.classify(cases, overlap)
        unknown = deepcopy(manifest)
        unknown["runtime_contracts"][0]["requires"] = ["undeclared"]
        with self.assertRaisesRegex(ValueError, "Unknown"):
            runner.classify(cases, unknown)

    def test_deleted_duplicate_or_empty_smoke_selection_is_an_error(self):
        _, cases, manifest = fixture_cases()
        for smoke in ([], ["deleted.test"], manifest["lean_smoke"] * 2):
            with self.subTest(smoke=smoke), self.assertRaisesRegex(ValueError, "smoke selectors"):
                runner.classify(cases, {**manifest, "lean_smoke": smoke})

    def test_import_error_during_discovery_is_not_omitted(self):
        root = self.temporary_root()
        name = "test_import_failure_" + uuid.uuid4().hex
        (root / "tests" / (name + ".py")).write_text("raise RuntimeError('broken test import')\n")
        self.addCleanup(sys.modules.pop, name, None)
        with self.assertRaisesRegex(ValueError, "broken test import"):
            runner.discover(root)

    def test_imported_duplicate_is_omitted_and_distinct_subclass_is_retained(self):
        root = self.temporary_root()
        name = "test_base_" + uuid.uuid4().hex
        other = "test_import_" + uuid.uuid4().hex
        (root / "tests" / (name + ".py")).write_text(
            "import unittest\nclass Base(unittest.TestCase):\n    def test_case(self): pass\n"
        )
        (root / "tests" / (other + ".py")).write_text(
            f"from {name} import Base\nclass Child(Base): pass\n"
        )
        self.addCleanup(sys.modules.pop, name, None)
        self.addCleanup(sys.modules.pop, other, None)
        cases, duplicates = runner.discover(root)
        self.assertEqual(
            {c.id() for c in cases}, {name + ".Base.test_case", other + ".Child.test_case"}
        )
        self.assertEqual(duplicates, {name + ".Base.test_case": 1})

    def test_source_integration_flag_is_rejected_before_import_side_effects(self):
        root = self.temporary_root()
        marker = root / "imported"
        (root / "tests/test_side_effect.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        )
        for flag in runner.OPT_INS:
            path = Path("artifacts") / (flag + ".json")
            with (
                self.subTest(flag=flag),
                patch.object(runner, "ROOT", root),
                patch.dict(os.environ, {flag: "1"}),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(runner.main(["--report", str(path)]), 2)
                report = json.loads((root / path).read_text())
                self.assertEqual(report["status"], "PREFLIGHT_FAILED")
                self.assertNotIn("result", report)
        self.assertFalse(marker.exists())

    def test_smoke_requires_optin_and_real_prerequisites_and_cannot_pass_if_skipped(self):
        root = self.temporary_root()
        _, cases, manifest = fixture_cases()
        manifest["requirements"]["base-runtime"] = {
            "files": [".runtime/real-seal.json"],
            "description": "Base",
        }
        with self.assertRaisesRegex(ValueError, "explicit MRA_REAL_LEAN"):
            runner.preflight(root, "lean-smoke", cases[:1], {}, manifest, {})
        with self.assertRaisesRegex(ValueError, "real-seal.json"):
            runner.preflight(root, "lean-smoke", cases[:1], {}, manifest, {"MRA_REAL_LEAN": "1"})

        class SkippedCase(unittest.TestCase):
            def test_runtime(self):
                self.skipTest("Runtime unavailable")

        success, result = runner.execute(
            cases[:1] + [SkippedCase("test_runtime")], "lean-smoke", io.StringIO(), 0
        )
        self.assertFalse(success)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(len(result["skipped"]), 1)

    def test_prepared_scope_lists_missing_requirements_instead_of_skipping(self):
        root = self.temporary_root()
        _, cases, manifest = fixture_cases()
        requirements = runner.classify(cases, manifest)
        chosen, deferred = runner.select(cases, requirements, manifest, "prepared")
        self.assertEqual(deferred, [])
        with self.assertRaisesRegex(ValueError, "Missing environment prerequisites.*seal.json"):
            runner.preflight(root, "prepared", chosen, requirements, manifest, {})

    def test_manifest_cannot_redirect_a_requirement_outside_the_project(self):
        root = self.temporary_root()
        _, _, manifest = fixture_cases()
        for path in ("../secret", "/tmp/secret", "runtime/../secret", "runtime\\secret"):
            manifest["requirements"]["prepared"]["files"] = [path]
            (root / "tests/suites.json").write_text(json.dumps(manifest))
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ValueError, "Invalid requirement path"),
            ):
                runner.load_manifest(root)
        (root / "tests/suites.json").write_text('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            runner.load_manifest(root)

    def test_present_prerequisite_is_never_labeled_integrity_verified(self):
        root = self.temporary_root()
        _, _, manifest = fixture_cases()
        (root / ".runtime").mkdir()
        (root / ".runtime/seal.json").write_text("not even a valid seal")
        inventory = runner.prerequisite_inventory(root, manifest)
        self.assertEqual(inventory["prepared"]["missing_files"], [])
        self.assertTrue(inventory["prepared"]["presence_only_not_integrity_verified"])

    def test_interruption_report_cannot_be_mistaken_for_a_success(self):
        root = self.temporary_root()
        _, _, manifest = fixture_cases()
        (root / "tests/suites.json").write_text(json.dumps(manifest))
        with (
            patch.object(runner, "ROOT", root),
            patch.object(runner, "discover", side_effect=KeyboardInterrupt),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(runner.main(["--report", "artifacts/interrupted.json"]), 130)
        report = json.loads((root / "artifacts/interrupted.json").read_text())
        self.assertEqual(report["status"], "INTERRUPTED")
        self.assertNotIn("result", report)

    def test_failure_report_is_retained_and_existing_report_cannot_be_overwritten(self):
        root = self.temporary_root()
        name = "test_failing_gate_" + uuid.uuid4().hex
        (root / "tests" / (name + ".py")).write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            "    def test_fault(self): self.fail('real software failure')\n"
        )
        self.addCleanup(sys.modules.pop, name, None)
        manifest = {
            "schema": "mra-test-suites-v1",
            "requirements": {},
            "runtime_contracts": [],
            "lean_smoke": [name + ".Check.test_fault"],
        }
        (root / "tests/suites.json").write_text(json.dumps(manifest))
        path = Path("artifacts/result.json")
        with (
            patch.object(runner, "ROOT", root),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(runner.main(["--report", str(path)]), 1)
            before = (root / path).read_bytes()
            report = json.loads(before)
            self.assertEqual(report["status"], "FAILED")
            self.assertEqual(report["result"]["passed"], 0)
            self.assertEqual(report["result"]["failures"], [name + ".Check.test_fault"])
            self.assertFalse(report["mathematical_evidence"])
            with self.assertRaises(SystemExit):
                runner.main(["--report", str(path)])
        self.assertEqual((root / path).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
