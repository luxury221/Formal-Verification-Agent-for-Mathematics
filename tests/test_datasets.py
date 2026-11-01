"""Dataset permission/split/budget gates and actual offline CLI, without model mocks."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mra.datasets import CASES_PATH, MANIFEST_PATH, load_dataset, repository_cases
from mra.evaluation import evaluation_plan, run_reference_evaluation

ROOT = Path(__file__).resolve().parents[1]


class ArithmeticDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "evals", self.root / "evals")
        self.manifest = json.loads((self.root / MANIFEST_PATH).read_text())
        self.data = json.loads((self.root / CASES_PATH).read_text())

    def write(self, *, refresh_hash=True):
        raw = (json.dumps(self.data, ensure_ascii=False, indent=2) + "\n").encode()
        (self.root / CASES_PATH).write_bytes(raw)
        if refresh_hash:
            self.manifest["sha256"] = hashlib.sha256(raw).hexdigest()
        (self.root / MANIFEST_PATH).write_text(json.dumps(self.manifest) + "\n")

    def test_balanced_reference_inventory_has_explicit_permissions_and_no_training(self):
        dataset = load_dataset(self.root)
        self.assertEqual(len(dataset.cases), 18)
        self.assertEqual(
            {name: len(ids) for name, ids in dataset.manifest["splits"].items()},
            {
                "train": 0,
                "development": 6,
                "regression": 6,
                "reserved": 6,
            },
        )
        self.assertEqual(len(set(dataset.manifest["families"].values())), 9)
        for split in ("development", "regression", "reserved"):
            cases = dataset.select(split, include_reserved=True)
            self.assertEqual(sum(case["direction"] == "prove" for case in cases), 3)
            self.assertEqual(sum(case["direction"] == "refute" for case in cases), 3)
        self.assertFalse(dataset.manifest["training_use"])
        self.assertEqual(len(repository_cases(self.root)), 28)

    def test_public_protocol_contains_sources_without_proofs_labels_or_witnesses(self):
        dataset = load_dataset(self.root)
        plan = evaluation_plan(dataset)
        self.assertEqual(plan["split"], "regression")
        self.assertFalse(plan["reserved_selected"])
        for item in plan["public_inputs"]:
            self.assertEqual(set(item), {"id", "version", "statement", "context", "schema_version"})
        self.assertEqual(plan["budget"]["model"]["max_output_tokens"], 2048)
        self.assertEqual(plan["budget"]["lean_timeout_seconds"], 30)
        self.assertEqual(plan["budget"]["max_generation_attempts"], 1)
        self.assertEqual(plan["budget"]["max_lean_checks_per_case"], 1)

    def test_reserved_split_and_all_require_explicit_selection(self):
        dataset = load_dataset(self.root)
        for split in ("reserved", "all"):
            with self.subTest(split=split), self.assertRaisesRegex(ValueError, "include-reserved"):
                evaluation_plan(dataset, split)
            plan = evaluation_plan(dataset, split, include_reserved=True)
            self.assertTrue(plan["reserved_selected"])
            self.assertIn("Not an independently blind", plan["reserved_status"])
        with self.assertRaises(ValueError):
            dataset.select("unknown")

    def test_empty_training_split_cannot_start_a_proof_or_training_run(self):
        self.assertEqual(evaluation_plan(load_dataset(self.root), "train")["public_inputs"], [])
        with self.assertRaisesRegex(ValueError, "Empty split"):
            run_reference_evaluation(self.root, "train")
        self.assertFalse((self.root / "artifacts").exists())

    def test_changed_content_requires_the_exact_manifest_hash(self):
        self.data["cases"][0]["proof"] = "by sorry"
        self.write(refresh_hash=False)
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            load_dataset(self.root)

    def test_missing_or_changed_permissions_never_default_to_training_authorization(self):
        for name, value in (
            ("license", "unknown"),
            ("consent", "public"),
            ("training_use", True),
            ("training_use", 0),
            ("public_benchmark_imported", True),
        ):
            original = self.manifest[name]
            self.manifest[name] = value
            self.write()
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                load_dataset(self.root)
            self.manifest[name] = original
        del self.manifest["license"]
        self.write()
        with self.assertRaises(ValueError):
            load_dataset(self.root)

    def test_family_cannot_cross_a_split_boundary(self):
        moved = self.manifest["splits"]["development"].pop()
        self.manifest["splits"]["regression"].append(moved)
        self.write()
        with self.assertRaisesRegex(ValueError, "family leaks"):
            load_dataset(self.root)

    def test_missing_duplicated_or_training_assigned_cases_are_rejected(self):
        original = json.loads(json.dumps(self.manifest["splits"]))
        case_id = original["regression"][0]
        for changes in ("missing", "duplicate", "training"):
            self.manifest["splits"] = json.loads(json.dumps(original))
            if changes == "missing":
                self.manifest["splits"]["regression"].remove(case_id)
            elif changes == "duplicate":
                self.manifest["splits"]["development"].append(case_id)
            else:
                self.manifest["splits"]["regression"].remove(case_id)
                self.manifest["splits"]["train"].append(case_id)
            self.write()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load_dataset(self.root)

    def test_duplicate_ids_and_normalized_statements_are_rejected_after_rehash(self):
        original = json.loads(json.dumps(self.data))
        for field in ("id", "formal_statement", "original_statement"):
            self.data = json.loads(json.dumps(original))
            self.data["cases"][1][field] = self.data["cases"][0][field] + (
                " \n" if field != "id" else ""
            )
            self.write()
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_dataset(self.root)

    def test_overlap_with_existing_p0_formulas_is_rejected(self):
        golden = json.loads((self.root / "evals/golden_cases.json").read_text())
        self.data["cases"][0]["formal_statement"] = golden[0]["formal_statement"] + " "
        self.write()
        with self.assertRaisesRegex(ValueError, "overlap with P0"):
            load_dataset(self.root)

    def test_refutation_requires_complete_natural_number_witness_annotations(self):
        case = next(case for case in self.data["cases"] if case["direction"] == "refute")
        original = case["annotation"]["witnesses"]
        for witnesses in ({}, {"unknown": "0"}, {"a": "-1", "b": "1"}):
            case["annotation"]["witnesses"] = witnesses
            self.write()
            with self.subTest(witnesses=witnesses), self.assertRaises(ValueError):
                load_dataset(self.root)
        case["annotation"]["witnesses"] = original

    def test_domain_and_quantifier_annotations_cannot_drift(self):
        variable = self.data["cases"][0]["annotation"]["variables"][0]
        for field, value in (("domain", "Int"), ("quantifier", "exists")):
            old = variable[field]
            variable[field] = value
            self.write()
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_dataset(self.root)
            variable[field] = old

    def test_extra_declarations_and_inconsistent_expected_verdicts_are_rejected(self):
        case = self.data["cases"][0]
        for field, value in (
            ("extra_declarations", "axiom injected : False"),
            ("expected_verdict", "REFUTED"),
        ):
            old = case[field]
            case[field] = value
            self.write()
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_dataset(self.root)
            case[field] = old

    def test_budget_rejects_unbounded_retries_boolean_limits_and_invalid_deadlines(self):
        original = json.loads(json.dumps(self.manifest["budget"]))
        for field, value in (
            ("max_generation_attempts", 2),
            ("max_lean_checks_per_case", 0),
            ("lean_timeout_seconds", True),
            ("lean_timeout_seconds", 0),
            ("lean_timeout_seconds", float("nan")),
        ):
            self.manifest["budget"] = {**original, field: value}
            self.write()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                load_dataset(self.root)

    def test_protocol_fingerprint_binds_dataset_split_and_budget(self):
        dataset = load_dataset(self.root)
        first = evaluation_plan(dataset)
        self.assertEqual(first, evaluation_plan(load_dataset(self.root)))
        self.assertEqual(first["verification"]["mode"], "data-only-kernel-replay-v1")
        self.assertEqual(first["verification"]["max_lean_workers_per_attempt"], 2)
        self.assertNotEqual(
            first["protocol_fingerprint"],
            evaluation_plan(dataset, "development")["protocol_fingerprint"],
        )
        self.manifest["budget"]["model"]["max_output_tokens"] += 1
        self.write()
        second = evaluation_plan(load_dataset(self.root))
        self.assertNotEqual(first["protocol_fingerprint"], second["protocol_fingerprint"])

    def test_dataset_paths_and_symlinks_cannot_import_outside_cases(self):
        self.manifest["path"] = "../outside.json"
        self.write()
        with self.assertRaises(ValueError):
            load_dataset(self.root)
        self.manifest["path"] = CASES_PATH
        self.write()
        path = self.root / CASES_PATH
        other = self.root / "alias.json"
        path.rename(other)
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "aliases"):
            load_dataset(self.root)


class ArithmeticDatasetCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "evals", self.root / "evals")
        shutil.copytree(
            ROOT / "mra", self.root / "mra", ignore=shutil.ignore_patterns("__pycache__")
        )

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mra", *args],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(self.root)},
        )

    def test_default_dataset_command_works_without_lean_and_excludes_reserved_references(self):
        result = self.run_cli("dataset")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(len(plan["selected_case_ids"]), 6)
        self.assertFalse(plan["reserved_selected"])
        self.assertFalse((self.root / ".runtime").exists())
        self.assertFalse((self.root / "artifacts").exists())

    def test_explicit_all_selection_records_reserved_exposure(self):
        result = self.run_cli("dataset", "--split", "all", "--include-reserved")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(len(plan["selected_case_ids"]), 18)
        self.assertTrue(plan["reserved_selected"])

    def test_implicit_reserved_selection_fails_cleanly(self):
        for split in ("reserved", "all"):
            result = self.run_cli("dataset", "--split", split)
            self.assertEqual(result.returncode, 1)
            self.assertIn("include-reserved", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_training_is_empty_and_reference_run_is_not_enabled(self):
        result = self.run_cli("dataset", "--split", "train")
        self.assertEqual(json.loads(result.stdout)["public_inputs"], [])
        result = self.run_cli("reference-benchmark", "--split", "train")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Empty split", result.stderr)
        self.assertFalse((self.root / "artifacts").exists())

    def test_corrupt_dataset_does_not_produce_a_fake_score(self):
        (self.root / CASES_PATH).write_text("{}")
        result = self.run_cli("dataset")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_reference_command_requires_a_real_sealed_runtime(self):
        result = self.run_cli("reference-benchmark")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires pinned real Lean runtime")
class ArithmeticReferenceRealTests(unittest.TestCase):
    def test_all_authored_references_with_real_lean_cli(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra",
                "reference-benchmark",
                "--split",
                "all",
                "--include-reserved",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-4000:])
        report = json.loads(result.stdout)
        self.assertEqual((report["total"], report["passed"]), (18, 18))
        self.assertEqual(report["observed_verdicts"], {"PROVED": 9, "REFUTED": 9})
        self.assertEqual(report["run_status_counts"], {"COMPLETED": 18})
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["lean_worker_count"], 36)
        self.assertIsNone(report["model_solve_rate"])
        self.assertTrue(report["plan"]["reserved_selected"])
        self.assertTrue((Path(report["artifact_directory"]) / "report.json").is_file())
        dataset = load_dataset(ROOT)
        for entry in report["results"]:
            self.assertEqual(entry["candidate_attempts"], 1)
            self.assertEqual(entry["lean_worker_count"], 2)
            self.assertEqual(entry["evidence_level"], "E4_ALIGNED_AND_VERIFIED")
            artifact = Path(entry["artifact_directory"])
            checked = json.loads((artifact / "report.json").read_text())
            expected = next(case for case in dataset.cases if case["id"] == entry["id"])
            self.assertEqual(checked["claim"]["formal_statement"], expected["formal_statement"])
            self.assertEqual(checked["claim"]["assumptions"], expected["annotation"]["assumptions"])
            self.assertEqual(checked["formalization"]["direction"], expected["direction"])
            self.assertEqual(
                checked["alignment"]["reviewer"], "repository-nat-arithmetic-v1-reference-author"
            )


if __name__ == "__main__":
    unittest.main()
