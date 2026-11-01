"""Version-isolated streaming profile and opt-in matched real Lean comparison."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import test_analysis_language as language_tests
from mra import analysis_foundation as foundation
from mra import analysis_lab as legacy
from mra import analysis_streaming as streaming
from mra import streaming_integrity
from mra.analysis_examples import examples
from mra.domain import canonical
from mra.integrity import ContentSnapshot

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(language_tests.ContractTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in streaming.TRUST_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.store = streaming.AnalysisStore(self.root)

    def test_foreign_or_redirected_ledger_is_not_adopted(self):
        base = self.root / "artifacts/foreign/analysis-streaming-private"
        base.mkdir(parents=True)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            db.execute("CREATE TABLE runs (id TEXT)")
        with self.assertRaises(ValueError):
            streaming.AnalysisStore(self.root, base=base)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            self.assertNotIn(
                "analysis_streaming_identity",
                [r[0] for r in db.execute("SELECT name FROM sqlite_master")],
            )
        redirect = self.root / "artifacts/redirect/analysis-streaming-private"
        redirect.parent.mkdir()
        redirect.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            streaming.AnalysisStore(self.root, base=redirect)

    def test_same_full_target_and_candidate_and_replay_source(self):
        for entry in examples():
            old = legacy.prepare(self.root, entry["request"])
            new = self.store.preview(entry["request"])
            for key in ("request", "formal_statement", "proof_source", "conditions", "budget"):
                self.assertEqual(old[key], new[key])
            for replay in (False, True):
                self.assertEqual(
                    legacy.render(self.root, old, replay=replay),
                    streaming.render(self.root, new, replay=replay),
                )
            self.assertNotEqual(old["plan_fingerprint"], new["plan_fingerprint"])
            self.assertEqual(new["request"]["schema"], "analysis-language-v1")
            self.assertEqual(new["mode"], streaming.MODE)

    def test_new_scanner_source_restoration_does_not_revive(self):
        row = self.submit()
        target = self.root / "mra/streaming_integrity.py"
        saved = target.read_bytes()
        target.write_bytes(saved + b"\n# changed scanner\n")
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        target.write_bytes(saved)
        with self.store.transaction() as db:
            self.store.save(db, row)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_old_ledger_is_separate_and_cannot_adopt_streaming_identity(self):
        old = legacy.AnalysisStore(self.root)
        self.assertNotEqual(old.path, self.store.path)
        newbase = self.root / "artifacts/identity/analysis-streaming-private"
        newbase.mkdir(parents=True)
        shutil.copyfile(old.path, newbase / "lab.sqlite3")
        with self.assertRaises(ValueError):
            streaming.AnalysisStore(self.root, base=newbase)
        with self.store.transaction(False) as db:
            self.assertEqual(
                db.execute("SELECT mode FROM analysis_streaming_identity").fetchone()[0],
                streaming.MODE,
            )

    def test_catalog_policy_return_is_a_copy(self):
        value = self.store.catalog()
        value["scan_policy"]["success_cache"] = True
        self.assertFalse(self.store.catalog()["scan_policy"]["success_cache"])
        self.assertTrue(self.store.catalog()["scan_policy"]["checks_actual_bytes"])


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "opt-in real Lean")
class RealTests(unittest.TestCase):
    def test_matched_targets_and_real_lifecycle_controls(self):
        stage = ROOT / "artifacts/analysis-streaming-20261029-01"
        out = stage / ("real-" + uuid.uuid4().hex)
        out.mkdir()
        cases = {c["id"]: c for c in examples()}
        order = [
            ("pointwise-hypothesis", "old"),
            ("pointwise-hypothesis", "streaming"),
            ("lambda-scaled-energy", "streaming"),
            ("lambda-scaled-energy", "old"),
            ("missing-nonzero", "streaming"),
            ("wrong-energy-sign", "streaming"),
            ("cancel-after-replay", "streaming"),
        ]
        cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {
            "memory_max": (cgroup / "memory.max").read_text().strip(),
            "memory_swap_max": (cgroup / "memory.swap.max").read_text().strip(),
        }
        self.assertEqual(resources["memory_max"], str(6 * 1024**3))
        self.assertEqual(resources["memory_swap_max"], str(512 * 1024**2))
        declared = {
            "cases": [
                {"id": n, "profile": p, "request": cases[n]["request"]}
                for n, p in order
                if n != "cancel-after-replay"
            ],
            "order": order,
            "resources": resources,
            "attempts_per_profile_target": 1,
            "worker_seconds_per_check": 30,
            "integrity_seconds_separate": True,
            "cancel_request": cases["lambda-scaled-energy"]["request"],
            "invalidation_control": "successful streaming proof corrupted then restored, stays invalid",
            "timing": "work_once external wall time; every actual verify invocation timed",
            "limitations": "two known targets, one sample each, cache/order confounding; not blind",
            "model_calls": 0,
        }
        (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        stores = {
            "old": legacy.AnalysisStore(ROOT, base=out / "analysis-language-private"),
            "streaming": streaming.AnalysisStore(ROOT, base=out / "analysis-streaming-private"),
        }
        rows = []
        for name, profile in order:
            store = stores[profile]
            request = deepcopy(
                cases["lambda-scaled-energy" if name == "cancel-after-replay" else name]["request"]
            )
            plan = store.preview(request)
            row = store.submit(
                {
                    "request_id": name,
                    "request": request,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "Codex complete typed target and unchanged proof review",
                    "confirmed": True,
                }
            )
            scans = []
            snapshot_class = (
                ContentSnapshot if profile == "old" else streaming_integrity.StreamingSnapshot
            )
            verify = snapshot_class.verify

            def timed(snapshot):
                started = time.monotonic()
                try:
                    return verify(snapshot)
                finally:
                    scans.append(
                        {
                            "seconds": time.monotonic() - started,
                            "metrics": deepcopy(getattr(snapshot, "last_scan", None)),
                        }
                    )

            runner = foundation._Runner._run_worker

            def cancel_after_actual_replay(instance, *args, **kwargs):
                result = runner(instance, *args, **kwargs)
                if kwargs["phase"] == "REPLAY_RUNNING" and result["returncode"] == 0:
                    store.cancel(row["run_id"])
                return result

            started = time.monotonic()
            with patch.object(snapshot_class, "verify", timed):
                if name == "cancel-after-replay":
                    with patch.object(
                        foundation._Runner, "_run_worker", cancel_after_actual_replay
                    ):
                        work = store.work_once(row["run_id"])
                else:
                    work = store.work_once(row["run_id"])
            elapsed = time.monotonic() - started
            checked = store.read(row["run_id"])
            rows.append(
                {
                    "id": name,
                    "profile": profile,
                    "check": checked,
                    "whole_job_seconds": elapsed,
                    "integrity_scans": scans,
                }
            )
            (out / "results.json").write_bytes(canonical(rows) + b"\n")
            self.assertEqual(work["worker_state"], "EXECUTED")
            with self.subTest(target=name, profile=profile):
                expected = "CANCELLED" if name == "cancel-after-replay" else cases[name]["expected"]
                self.assertIn(checked["state"], (expected, "TIMEOUT"))
                self.assertFalse(checked["invalidated"])
                self.assertEqual(
                    checked["math_verdict"],
                    "PROVED" if checked["state"] == "COMPLETED" else "UNKNOWN",
                )
                self.assertEqual(
                    checked["evidence_level"], "E4" if checked["state"] == "COMPLETED" else "E0"
                )
                if checked["state"] in ("COMPLETED", "CANCELLED"):
                    self.assertNotEqual(
                        checked["result"]["candidate"]["pid"], checked["result"]["replay"]["pid"]
                    )
                    self.assertEqual(checked["result"]["replay"]["returncode"], 0)
        invalidation = None
        positive = next(
            (e for e in rows if e["profile"] == "streaming" and e["check"]["state"] == "COMPLETED"),
            None,
        )
        if positive is not None:
            store = stores["streaming"]
            run_id = positive["check"]["run_id"]
            data = store.directory(run_id) / "replay/proof.ndjson"
            saved = data.read_bytes()
            (out / "original-proof.ndjson").write_bytes(saved)
            data.write_bytes(saved + b" ")
            self.assertTrue(store.report(run_id)["invalidated"])
            data.write_bytes(saved)
            restored = store.report(run_id)
            self.assertTrue(restored["invalidated"])
            self.assertEqual(restored["math_verdict"], "UNKNOWN")
            invalidation = {"run_id": run_id, "restored": restored}
        summary = {
            "all_passed": all(
                e["check"]["state"]
                in (
                    ("CANCELLED", "TIMEOUT")
                    if e["id"] == "cancel-after-replay"
                    else (cases[e["id"]]["expected"], "TIMEOUT")
                )
                for e in rows
            )
            and invalidation is not None,
            "negative_controls_completed": all(
                e["check"]["state"] == "REJECTED"
                for e in rows
                if e["id"] in ("missing-nonzero", "wrong-energy-sign")
            ),
            "cancellation_control_completed": rows[-1]["check"]["state"] == "CANCELLED",
            "scope": "protocol safety; timeout capacity reported separately",
            "stores": {p: str(s.base.relative_to(ROOT)) for p, s in stores.items()},
            "checks": len(rows),
            "worker_processes": sum(
                sum(bool(e["check"]["result"].get(p)) for p in ("candidate", "replay"))
                for e in rows
            ),
            "unresolved": [
                {"id": e["id"], "profile": e["profile"], "state": e["check"]["state"]}
                for e in rows
                if e["check"]["state"] == "TIMEOUT"
            ],
            "invalidation": invalidation,
            "model_calls": 0,
        }
        (out / "summary.json").write_bytes(canonical(summary) + b"\n")
        self.assertIsNotNone(
            invalidation, "No successful streaming proof available for real restoration control"
        )
