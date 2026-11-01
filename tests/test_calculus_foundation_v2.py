"""Contract controls and opt-in genuine analysis export/kernel replay."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from mra import calculus_foundation_v2 as a
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
        first["imports"].append("Evil")
        self.assertNotIn("Evil", a.catalog()["imports"])

    def test_no_arbitrary_lean_or_silent_condition_rewrite(self):
        altered = deepcopy(a.PROBES[1])
        altered["statement"] = "True"
        with self.assertRaises(ValueError):
            a.render(ROOT, altered)
        with self.assertRaises(ValueError):
            a.run(ROOT, "unknown", "reviewer")
        with self.assertRaises(ValueError):
            a.run(ROOT, "derivative-id", " ")

    def test_full_context_and_isolated_import_in_both_processes(self):
        for probe in a.PROBES:
            for replay in (False, True):
                source = a.render(ROOT, probe, replay=replay)
                self.assertIn(probe["statement"], source)
                for name in a.IMPORTS:
                    self.assertIn("import " + name + "\n", source)
                self.assertNotIn("import Mathlib.Data.Nat.Basic\n", source)
                self.assertIn("MRA_REPLAY:" if replay else "MRA_AUDIT:", source)
                lines = source.splitlines()
                imports = [i for i, line in enumerate(lines) if line.startswith("import ")]
                opens = [i for i, line in enumerate(lines) if line.startswith("open ")]
                self.assertLess(max(imports), min(opens))
        self.assertEqual(a.PROBES[2]["statement"].count("HasDerivAt"), 3)
        self.assertEqual(a.PROBES[4]["statement"].count("IntervalIntegrable"), 2)

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
            run_id = "calculus-" + "a" * 32
            directory = root / "artifacts/calculus-foundation-v2/runs" / run_id
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
            run_id = "calculus-" + "b" * 32
            directory = root / "artifacts/calculus-foundation-v2/runs" / run_id
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
        out = ROOT / "artifacts/calculus-foundation-20261029-01" / ("real-v2-" + uuid.uuid4().hex)
        out.mkdir()
        declared = {
            "catalog": a.catalog(),
            "scope": "capacity of explicit corrected-adapter cohort; one attempt per probe in this cohort; prior failed cohort retained",
            "per_probe_worker_seconds": 40,
            "attempts_per_probe": 1,
            "extra_replay_controls": ["wrong-host-target", "malformed-proof-data"],
            "each_replay_control_seconds": 40,
            "source_exposure": "repository-known; not blind",
            "invalidation_controls": [
                "derivative-id proof data corruption/restoration",
                "derivative-add worker accounting corruption/restoration",
            ],
        }
        (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (cgroup / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        (out / "resources.json").write_bytes(canonical(resources) + b"\n")
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        rows = []
        for probe in a.PROBES:
            value = a.run(ROOT, probe["id"], "Codex repository fixture alignment review")
            rows.append(value)
            (out / "progress.json").write_bytes(canonical(rows) + b"\n")
            with self.subTest(probe=probe["id"]):
                self.assertIn(value["state"], (probe["expected"], "TIMEOUT"))
            self.assertEqual(
                value["math_verdict"], "PROVED" if value["state"] == "VERIFIED" else "UNKNOWN"
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
        budget_row = rows[2]
        self.assertEqual(budget_row["state"], "VERIFIED")
        budget_directory = ROOT / budget_row["directory"]
        budget_file = budget_directory / "result.json"
        budget_saved = budget_file.read_bytes()
        (out / "budget-original-result.json").write_bytes(budget_saved)
        changed = json.loads(budget_saved)
        changed["worker_seconds"] += 1
        a._save_result(budget_directory, changed)
        budget_bad = a.report(ROOT, budget_directory.name)
        self.assertEqual(budget_bad["state"], "INVALIDATED")
        self.assertIn("accounting", budget_bad["error"])
        budget_file.write_bytes(budget_saved)
        budget_restored = a.report(ROOT, budget_directory.name)
        self.assertEqual(budget_restored["state"], "INVALIDATED")
        legacy.verify()
        current = [a.report(ROOT, Path(row["directory"]).name) for row in rows]
        summary = {
            "all_passed": all(
                v["state"] in (p["expected"], "TIMEOUT")
                for p, v in zip(a.PROBES, rows, strict=True)
            ),
            "expected_positive_probes": sum(p["expected"] == "VERIFIED" for p in a.PROBES),
            "positive_completions": sum(v["state"] == "VERIFIED" for v in rows),
            "unresolved": [
                p["id"] for p, v in zip(a.PROBES, rows, strict=True) if v["state"] == "TIMEOUT"
            ],
            "checks": rows,
            "controls": controls,
            "invalidation": {"mutated": bad, "restored": restored},
            "budget_invalidation": {"mutated": budget_bad, "restored": budget_restored},
            "current": current,
            "legacy_runtime_preserved": True,
            "new_models": 0,
        }
        (out / "summary.json").write_bytes(canonical(summary) + b"\n")
