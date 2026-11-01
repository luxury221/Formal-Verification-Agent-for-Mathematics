"""Contract controls and opt-in genuine analysis export/kernel replay."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from mra import analysis_foundation as a
from mra.domain import canonical
from mra.integrity import ContentSnapshot, IntegrityError, file_hash
from mra.tasks import TaskStore

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_catalog_is_not_mutable_provider_state(self):
        first = a.catalog()
        first["probes"][0]["proof"] = "by sorry"
        self.assertNotEqual(first["probes"], a.catalog()["probes"])
        first["policy"]["allowed_axioms"].append("evil")
        self.assertNotIn("evil", a.catalog()["policy"]["allowed_axioms"])

    def test_no_arbitrary_lean_or_silent_condition_rewrite(self):
        altered = deepcopy(a.PROBES[1])
        altered["statement"] = "True"
        with self.assertRaises(ValueError):
            a.render(ROOT, altered)
        with self.assertRaises(ValueError):
            a.run(ROOT, "unknown", "reviewer")
        with self.assertRaises(ValueError):
            a.run(ROOT, "real-self", " ")

    def test_full_context_and_isolated_import_in_both_processes(self):
        for probe in a.PROBES:
            for replay in (False, True):
                source = a.render(ROOT, probe, replay=replay)
                self.assertIn(probe["statement"], source)
                self.assertIn("import " + a.IMPORT + "\n", source)
                self.assertNotIn("import Mathlib.Data.Nat.Basic\n", source)
                self.assertIn("MRA_REPLAY:" if replay else "MRA_AUDIT:", source)
        self.assertIn("[InnerProductSpace ℝ F]", a.PROBES[2]["statement"])
        self.assertIn("[NormedAddCommGroup F]", a.PROBES[2]["statement"])

    def test_name_and_redirection_guards(self):
        with self.assertRaises(ValueError):
            a.report(ROOT, "../../result")
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            root = Path(temp)
            (root / "actual").mkdir()
            (root / "redirect").symlink_to(root / "actual", target_is_directory=True)
            with self.assertRaises(IntegrityError):
                a._safe(root, "redirect/test")

    def test_incomplete_snapshot_rejected_without_running_lean(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            root = Path(temp)
            (root / a.BASE).mkdir(parents=True)
            raw = canonical({"schema_version": "1", "trees": {}, "files": {}, "host_files": {}})
            (root / a.SNAPSHOT).write_bytes(raw)
            (root / a.LOCK).write_bytes(canonical({"mode": a.MODE, "sha256": a.digest_bytes(raw)}))
            with self.assertRaises(IntegrityError):
                a.load(root)
            with self.assertRaises(IntegrityError):
                a.seal(root)

    def test_corrupt_receipt_cannot_revive_after_restore(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            root = Path(temp)
            run_id = "analysis-" + "a" * 32
            directory = root / "artifacts/analysis-foundation/runs" / run_id
            directory.mkdir(parents=True)
            result = directory / "result.json"
            result.write_text("{bad")
            self.assertEqual(a.report(root, run_id)["math_verdict"], "UNKNOWN")
            result.write_text("{}")
            self.assertEqual(a.report(root, run_id)["state"], "INVALIDATED")
            self.assertTrue((directory / "invalidated.json").exists())

    def test_observing_running_evidence_never_invalidates_or_promotes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            root = Path(temp)
            run_id = "analysis-" + "b" * 32
            directory = root / "artifacts/analysis-foundation/runs" / run_id
            directory.mkdir(parents=True)
            (directory / "result.json").write_bytes(
                canonical({"state": "RUNNING", "math_verdict": "PROVED"})
            )
            observed = a.report(root, run_id)
            self.assertEqual(observed["state"], "RUNNING")
            self.assertEqual(observed["math_verdict"], "UNKNOWN")
            self.assertFalse((directory / "invalidated.json").exists())

    def test_result_replacement_does_not_leave_partial_receipts(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            directory = Path(temp)
            a._save_result(directory, {"state": "RUNNING"})
            a._save_result(directory, {"state": "INTERRUPTED"})
            self.assertEqual(
                json.loads((directory / "result.json").read_text()), {"state": "INTERRUPTED"}
            )
            self.assertEqual([p.name for p in directory.iterdir()], ["result.json"])

    def test_bounded_diagnostics(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            log = Path(temp) / "output.log"
            log.write_bytes(b"x" * 1048577)
            with self.assertRaises(ValueError):
                a._read_log(log)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "opt-in real Lean")
class RealTests(unittest.TestCase):
    def test_declared_analysis_probes_and_security_controls(self):
        out = ROOT / "artifacts/analysis-foundation-20261029-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir()
        declared = {
            "catalog": a.catalog(),
            "per_probe_worker_seconds": 40,
            "attempts_per_probe": 1,
            "extra_replay_controls": ["wrong-host-target", "malformed-proof-data"],
            "each_replay_control_seconds": 40,
            "source_exposure": "repository-known; not blind",
            "invalidation_control": "mutate real-self proof data, inspect then restore; never revive",
        }
        (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        rows = []
        for probe in a.PROBES:
            value = a.run(ROOT, probe["id"], "Codex repository fixture alignment review")
            rows.append(value)
            (out / "progress.json").write_bytes(canonical(rows) + b"\n")
            self.assertEqual(
                value["state"], probe["expected"], json.dumps(value, ensure_ascii=False)
            )
            self.assertEqual(
                value["math_verdict"], "PROVED" if probe["expected"] == "VERIFIED" else "UNKNOWN"
            )
            if probe["expected"] != "VERIFIED":
                self.assertIsNone(value["replay"])
        first = ROOT / rows[0]["directory"]
        controls = []
        with TaskStore(ROOT).worker_lock() as fd:
            for control in declared["extra_replay_controls"]:
                a.load(ROOT)
                work = out / control
                work.mkdir()
                source = a.render(
                    ROOT,
                    a.PROBES[3] if control == "wrong-host-target" else a.PROBES[0],
                    replay=True,
                )
                (work / "Candidate.lean").write_text(source)
                (work / "policy.json").write_bytes(canonical(a.POLICY) + b"\n")
                raw = (first / "replay/proof.ndjson").read_bytes()
                (work / "proof.ndjson").write_bytes(
                    raw if control == "wrong-host-target" else b'{"bad":true}\n'
                )
                execution = a._Runner(ROOT)._run_worker(
                    work,
                    out / ("sandbox-" + control),
                    work / "stdout.log",
                    work / "stderr.log",
                    40,
                    phase="REPLAY_RUNNING",
                    lifecycle_lock_fd=fd,
                )
                controls.append({"control": control, **execution})
                (out / "controls.json").write_bytes(canonical(controls) + b"\n")
                self.assertEqual(execution["run_status"], "COMPLETED")
                self.assertNotEqual(execution["returncode"], 0)
                if control == "wrong-host-target":
                    self.assertIn("MRA_REPLAY_TARGET_MISMATCH", (work / "stdout.log").read_text())
        saved = (first / "replay/proof.ndjson").read_bytes()
        (out / "invalidation-original-proof.ndjson").write_bytes(saved)
        (first / "replay/proof.ndjson").write_bytes(saved + b" ")
        bad = a.report(ROOT, first.name)
        (first / "replay/proof.ndjson").write_bytes(saved)
        restored = a.report(ROOT, first.name)
        self.assertEqual(bad["state"], "INVALIDATED")
        self.assertEqual(restored["state"], "INVALIDATED")
        self.assertEqual(file_hash(first / "replay/proof.ndjson"), a.digest_bytes(saved))
        legacy.verify()
        current = [a.report(ROOT, Path(row["directory"]).name) for row in rows]
        summary = {
            "all_passed": True,
            "checks": rows,
            "controls": controls,
            "invalidation": {"mutated": bad, "restored": restored},
            "current": current,
            "legacy_runtime_preserved": True,
            "new_models": 0,
        }
        (out / "summary.json").write_bytes(canonical(summary) + b"\n")
