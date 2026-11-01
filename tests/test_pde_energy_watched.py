"""Contract controls for fixed smooth Poisson identities and exact proof-data reuse."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from mra import pde_energy_watched as p
from mra.domain import canonical
from mra.integrity import IntegrityError

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_catalog_copy_and_fixed_decreasing_source_edges(self):
        rows = p.catalog()
        rows["probes"][0]["statement"] = "False"
        self.assertNotEqual(rows["probes"], p.catalog()["probes"])
        seen = set()
        for probe in p.PROBES:
            if "source_probe" in probe:
                self.assertIn(probe["source_probe"], seen)
            self.assertNotIn(probe["id"], seen)
            seen.add(probe["id"])

    def test_no_arbitrary_theorems_or_unnamed_alignment(self):
        forged = deepcopy(p.PROBES[0])
        forged["statement"] = "True"
        with self.assertRaises(ValueError):
            p.render(ROOT, forged)
        with self.assertRaises(ValueError):
            p.run(ROOT, "unknown", "reviewer")
        with self.assertRaises(ValueError):
            p.run(ROOT, p.PROBES[0]["id"], " ")

    def test_full_types_and_all_imports_precede_commands(self):
        for probe in p.PROBES:
            for replay in (False, True):
                source = p.render(ROOT, probe, replay=replay)
                self.assertIn(probe["statement"], source)
                self.assertIn("import " + p.IMPORTS[0] + "\n", source)
                lines = source.splitlines()
                self.assertLess(
                    max(i for i, s in enumerate(lines) if s.startswith("import ")),
                    min(i for i, s in enumerate(lines) if s.startswith("open ")),
                )
                self.assertNotIn("import Mathlib.Data.Nat.Basic\n", source)
            if probe.get("source_probe"):
                source = p.render(ROOT, probe)
                parent = next(r for r in p.PROBES if r["id"] == probe["source_probe"])
                self.assertIn(parent["statement"], source)
                self.assertIn("/work/source-0.ndjson", source)
                self.assertIn("MRA_IMPORT:", source)
                self.assertIn("MraCandidate.Reused.s0.checked", source)
        self.assertIn("u a = 0 → u b = 0", p.PROBES[3]["statement"])
        self.assertIn("a ≤ b", p.PROBES[4]["statement"])

    def test_source_selection_and_unsafe_paths_rejected(self):
        with self.assertRaises(ValueError):
            p._source(ROOT, p.PROBES[0], "pdewatched-" + "a" * 32)
        self.assertEqual(p._source(ROOT, p.PROBES[0], None), (None, None))
        for name in ("../result", "pdewatched-" + "g" * 32, None):
            with self.assertRaises(ValueError):
                p._source(ROOT, p.PROBES[1], name)
        with self.assertRaises(ValueError):
            p.report(ROOT, "../../outside")

    def test_import_audit_rejects_counterfeit_success(self):
        good = {
            "slot": 0,
            "type_matches": True,
            "candidate_module_imported": False,
            "replayed_declarations": 1,
            "axioms": ["propext"],
        }
        self.assertEqual(p._import_audit("MRA_IMPORT:" + json.dumps(good)), good)
        for key, value in (
            ("slot", True),
            ("type_matches", False),
            ("candidate_module_imported", True),
            ("replayed_declarations", 0),
            ("axioms", ["sorryAx"]),
        ):
            bad = {**good, key: value}
            with self.assertRaises(ValueError):
                p._import_audit("MRA_IMPORT:" + json.dumps(bad))
        with self.assertRaises(ValueError):
            p._import_audit("MRA_IMPORT:" + json.dumps(good) + "\nMRA_IMPORT:" + json.dumps(good))

    def test_corruption_stays_invalidated_after_restoration(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            root = Path(d)
            run_id = "pdewatched-" + "a" * 32
            directory = root / "artifacts/pde-energy-watched-v2/runs" / run_id
            directory.mkdir(parents=True)
            (directory / "result.json").write_text("{bad")
            self.assertEqual(p.report(root, run_id)["state"], "INVALIDATED")
            (directory / "result.json").write_text("{}")
            self.assertEqual(p.report(root, run_id)["state"], "INVALIDATED")

    def test_running_never_promotes_or_scans_unfinished_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            root = Path(d)
            run_id = "pdewatched-" + "b" * 32
            directory = root / "artifacts/pde-energy-watched-v2/runs" / run_id
            directory.mkdir(parents=True)
            p._save_result(directory, {"state": "RUNNING", "math_verdict": "PROVED"})
            observed = p.report(root, run_id)
            self.assertEqual(observed["math_verdict"], "UNKNOWN")
            self.assertFalse((directory / "invalidated.json").exists())

    def test_unsealed_or_redirected_environment_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            root = Path(d)
            (root / "actual").mkdir()
            (root / "redirect").symlink_to(root / "actual", target_is_directory=True)
            with self.assertRaises(IntegrityError):
                p._safe(root, "redirect/entry")
            with self.assertRaises(IntegrityError):
                p.load(root)

    def test_atomic_receipts_and_bounded_logs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            directory = Path(d)
            p._save_result(directory, {"state": "RUNNING"})
            p._save_result(directory, {"state": "INTERRUPTED"})
            self.assertEqual(
                json.loads((directory / "result.json").read_text())["state"], "INTERRUPTED"
            )
            log = directory / "stdout.log"
            log.write_bytes(b"x" * 1048577)
            with self.assertRaises(ValueError):
                p._read_log(log)

    def test_source_type_checked_before_recursive_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            root = Path(d)
            run_id = "pdewatched-" + "c" * 32
            directory = root / "artifacts/pde-energy-watched-v2/runs" / run_id
            directory.mkdir(parents=True)
            (directory / "request.json").write_bytes(canonical({"probe": p.PROBES[1]}))
            with self.assertRaises(IntegrityError):
                p._source(root, p.PROBES[1], run_id)
            self.assertFalse((directory / "invalidated.json").exists())

    def test_direct_proof_use_is_required(self):
        valid = 'MRA_SOURCE_USE:["MraCandidate.Reused.s0.checked","mul_comm"]'
        self.assertIn("MraCandidate.Reused.s0.checked", p._source_use(valid))
        for text in ("MRA_SOURCE_USE:[]", 'MRA_SOURCE_USE:{"bad":true}', valid + "\n" + valid):
            with self.assertRaises(ValueError):
                p._source_use(text)

    def test_recovery_never_retries_or_promotes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as d:
            root = Path(d)
            run_id = "pdewatched-" + "d" * 32
            directory = root / "artifacts/pde-energy-watched-v2/runs" / run_id
            directory.mkdir(parents=True)
            p._save_result(directory, {"state": "RUNNING", "math_verdict": "PROVED"})
            value = p.recover(root, run_id)
            self.assertEqual(value["state"], "INTERRUPTED")
            self.assertEqual(value["math_verdict"], "UNKNOWN")
            self.assertEqual(p.recover(root, run_id), value)
            self.assertFalse((directory / "candidate").exists())

    def test_profile_is_independent_and_preserves_mathematical_scope(self):
        from mra import pde_energy as prior
        from mra.streaming_integrity_v2 import SCAN_POLICY

        self.assertNotEqual(p.MODE, prior.MODE)
        self.assertEqual(p.TREES, prior.TREES)
        self.assertEqual(p.PROBES, prior.PROBES)
        self.assertIn("mra/streaming_integrity_v2.py", p.FILES)
        self.assertIn(prior.SNAPSHOT, p.FILES)
        self.assertIn(prior.LOCK, p.FILES)
        self.assertEqual(p.catalog()["scan_policy"], SCAN_POLICY)
        copy = p.catalog()
        copy["scan_policy"]["monitor_required"] = False
        self.assertTrue(p.catalog()["scan_policy"]["monitor_required"])
        with self.assertRaises(ValueError):
            p.report(ROOT, "pde-" + "a" * 32)

    def test_context_requires_actual_native_dependencies_and_kernel(self):
        from unittest.mock import patch

        provenance, hosts = p._context(ROOT)
        self.assertEqual(provenance["mode"], p.MODE)
        self.assertTrue(set(p.host_dependencies()).issubset(hosts))
        with patch.object(p, "host_dependencies", return_value=("/untrusted/native",)):
            with self.assertRaises(IntegrityError):
                p._context(ROOT)
        with patch("platform.release", return_value="different-kernel"):
            with self.assertRaises(IntegrityError):
                p._context(ROOT)

    def test_native_dependencies_include_builtin_interpreter_and_ctypes(self):
        import ctypes
        import sys

        hosts = p.host_dependencies()
        self.assertEqual(len(hosts), len(set(hosts)))
        self.assertIn(str(Path(sys.executable).resolve()), hosts)
        self.assertIn(str(Path(ctypes.__file__).resolve()), hosts)
        self.assertTrue(any(Path(name).name.startswith("libffi.so.") for name in hosts))
        self.assertTrue(all(Path(name).is_file() for name in hosts))
