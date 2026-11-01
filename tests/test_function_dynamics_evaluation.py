"""Protocol fixtures never run as mathematics; real evaluation is separately enabled."""

import hashlib
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

from mra.domain import canonical, digest
from mra.research_flow import atomic
from mra.search_evaluation import _envelope
from mra.function_dynamics_evaluation import (
    DATASET,
    FILES,
    SOURCE,
    LICENSE,
    directory,
    prepare,
    report,
    run,
)
from mra.function_dynamics_checks import Checks
from mra.tasks import TaskStore, WorkerBusy
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/function-dynamics-evaluation-20261029-01"


def execute(root, value, **changes):
    return run(
        root,
        value["evaluation_id"],
        **{
            "plan_fingerprint": value["plan"]["plan_fingerprint"],
            "reviewer": "MRA071 authored diagnostic alignment reviewer",
            "confirmed": True,
            **changes,
        },
    )


def fixture(store, key, _fd, success=False):
    """NONEXECUTABLE protocol metadata, never a real check or a solve-rate observation."""
    row = store.read(key)
    area = store.directory(key)
    (area / "replay").mkdir(parents=True)
    raw = b"NONEXECUTABLE protocol fixture; not a Lean proof\n"
    (area / "replay/proof.ndjson").write_bytes(raw)
    phase = {
        "pid": 101,
        "returncode": 0,
        "run_status": "COMPLETED",
        "audit": {
            "type_matches": True,
            "local_axioms": [],
            "axioms": [],
            "local_dependency_axioms": [],
        },
    }
    row.update(
        state="COMPLETED" if success else "REJECTED",
        stage="NONEXECUTABLE",
        math_verdict="PROVED" if success else "UNKNOWN",
        evidence_level="E4" if success else "E0",
        files={"replay/proof.ndjson": hashlib.sha256(raw).hexdigest()},
        result={
            "worker_seconds": 1,
            "candidate": phase,
            "replay": {**phase, "pid": 102} if success else None,
        },
    )
    with store.transaction() as db:
        store.save(db, row)
    return store.report(key)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for folder, pattern in (("mra", "*.py"), ("lean_workspace", "*.in")):
            for source in (ROOT / folder).rglob(pattern):
                target = self.root / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        target = self.root / DATASET
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / DATASET, target)
        for name in (SOURCE, LICENSE):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def simulated(self, success=False):
        with (
            patch.object(Checks, "execute", lambda s, k, fd: fixture(s, k, fd, success)),
            patch.object(
                NativeLeanBackend,
                "_run_worker",
                side_effect=AssertionError("No Lean in protocol fixture"),
            ),
        ):
            value = prepare(self.root)
            return value, execute(self.root, value)

    def test_preparation_freezes_generated_programs_without_lean_or_search_on_report(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = prepare(self.root)
        self.assertEqual(value["state"], "PREPARED")
        self.assertIsNone(value["summary"])
        self.assertFalse(list(directory(self.root).rglob("*.sqlite3")))
        self.assertEqual(value["plan"]["definition"]["budget"]["combined_worker_seconds"], 640)
        for case in value["plan"]["cases"]:
            self.assertLessEqual(len(case["arms"]["current"]), 2)
            for arm in case["arms"].values():
                for request in arm:
                    self.assertEqual(
                        {k: v for k, v in request.items() if k != "proof"}, case["target"]
                    )
        with patch(
            "mra.function_dynamics_evaluation.current.propose",
            side_effect=AssertionError("No regeneration"),
        ):
            self.assertTrue(report(self.root, value["evaluation_id"])["integrity_ok"])

    def test_fixed_full_denominator_failures_and_reserved_ceiling(self):
        prepared, value = self.simulated()
        self.assertEqual(value["state"], "COMPLETED")
        self.assertEqual(len(value["results"]), 32)
        for arm in value["summary"]["arms"].values():
            self.assertEqual(
                (
                    arm["proved"],
                    arm["unknown"],
                    arm["actual_checks"],
                    arm["worker_reserved_seconds"],
                ),
                (0, 8, 16, 320),
            )
        self.assertEqual(len(value["summary"]["correlated_families"]), 6)
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        with patch.object(Checks, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(execute(self.root, prepared)["summary"], value["summary"])

    def test_success_skips_unused_slots_without_removing_cases(self):
        _, value = self.simulated(True)
        self.assertEqual(len(value["results"]), 32)
        self.assertEqual(sum(r["status"] == "SKIPPED_AFTER_SUCCESS" for r in value["results"]), 16)
        for arm in value["summary"]["arms"].values():
            self.assertEqual((arm["proved"], arm["denominator"], arm["actual_checks"]), (8, 8, 8))

    def test_named_confirmation_and_busy_worker_never_start_or_create_checks(self):
        value = prepare(self.root)
        for change in ({"confirmed": False}, {"plan_fingerprint": "0" * 64}):
            with self.assertRaises(ValueError):
                execute(self.root, value, **change)
        with TaskStore(self.root).worker_lock():
            with self.assertRaises(WorkerBusy):
                execute(self.root, value)
        self.assertFalse(list(directory(self.root).rglob("*.sqlite3")))

    def test_dataset_dropped_reordered_reference_proof_or_exposure_claim_rejected(self):
        original = (self.root / DATASET).read_bytes()
        for change in (
            lambda v: v["cases"].pop(),
            lambda v: v["cases"].reverse(),
            lambda v: v["cases"][0]["target"].update(proof={"method": "rfl"}),
            lambda v: v["cases"][0]["provenance"].update(exposure="blind"),
        ):
            v = json.loads(original)
            change(v)
            (self.root / DATASET).write_bytes(canonical(v))
            with self.assertRaises(ValueError):
                prepare(self.root)
        (self.root / DATASET).write_bytes(original)

    def test_public_source_anchor_license_and_content_are_verified(self):
        original = (self.root / DATASET).read_bytes()
        for mutate in (
            lambda v: v["source"].update(sha256="0" * 64),
            lambda v: v["source"].update(license="unverified"),
            lambda v: v["source"].update(commit="0" * 40),
            lambda v: v["cases"][0]["provenance"].update(lines=[1, 1]),
            lambda v: v["cases"][0]["provenance"].update(generator_reference_proof_supplied=True),
        ):
            value = json.loads(original)
            mutate(value)
            (self.root / DATASET).write_bytes(canonical(value))
            with self.assertRaises(ValueError):
                prepare(self.root)
        (self.root / DATASET).write_bytes(original)

    def test_proposal_budget_and_source_manifest_cannot_be_relabelled(self):
        for mutate in (
            lambda p: p["budget"].update(max_candidates=8),
            lambda p: p.update(source_manifest={}),
            lambda p: p.update(schema="structure-strategy-proposals-v2"),
        ):
            value = prepare(self.root)
            area = directory(self.root, value["evaluation_id"])
            plan = _envelope(area / "plan.json")
            p = plan["cases"][0]["proposals"]["current"]
            mutate(p)
            p["plan_fingerprint"] = digest(
                {k: v for k, v in p.items() if k not in ("plan_fingerprint", "stats")}
            )
            plan["plan_fingerprint"] = digest(
                {k: v for k, v in plan.items() if k != "plan_fingerprint"}
            )
            atomic(area / "plan.json", plan)
            self.assertFalse(report(self.root, value["evaluation_id"])["integrity_ok"])

    def test_changed_complete_program_or_budget_snapshot_is_not_accepted(self):
        value = prepare(self.root)
        area = directory(self.root, value["evaluation_id"])
        plan = _envelope(area / "plan.json")
        plan["cases"][0]["arms"]["legacy"][0]["goal"] = {"eq": [{"nat": 0}, {"nat": 0}]}
        plan["plan_fingerprint"] = digest(
            {k: v for k, v in plan.items() if k != "plan_fingerprint"}
        )
        atomic(area / "plan.json", plan)
        self.assertFalse(report(self.root, value["evaluation_id"])["integrity_ok"])

    def test_source_drift_and_restored_bytes_do_not_revive_plan(self):
        value = prepare(self.root)
        p = self.root / FILES[1]
        old = p.read_bytes()
        p.write_bytes(old + b"\n")
        self.assertFalse(report(self.root, value["evaluation_id"])["integrity_ok"])
        p.write_bytes(old)
        self.assertEqual(report(self.root, value["evaluation_id"])["state"], "UNAVAILABLE")

    def test_completed_entries_cannot_omit_reorder_or_duplicate_slots(self):
        for mutate in (lambda e: e.pop(), lambda e: e.reverse(), lambda e: e.__setitem__(1, e[0])):
            _, value = self.simulated()
            area = directory(self.root, value["evaluation_id"])
            row = _envelope(area / "record.json")
            mutate(row["entries"])
            atomic(area / "record.json", row)
            self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])

    def test_damage_or_withdrawal_suppresses_comparison_permanently(self):
        for revoke in (False, True):
            _, value = self.simulated(True)
            area = directory(self.root, value["evaluation_id"])
            lab = Checks(self.root, area)
            key = value["results"][0]["run_id"]
            p = lab.directory(key) / "replay/proof.ndjson"
            old = p.read_bytes()
            if revoke:
                lab.revoke(
                    key,
                    {
                        "reviewer": "NONEXECUTABLE fixture",
                        "reason": "Protocol withdrawal",
                        "confirmed": True,
                    },
                )
            else:
                p.write_bytes(old + b" ")
            self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])
            p.write_bytes(old)
            self.assertEqual(report(self.root, value["evaluation_id"])["state"], "UNAVAILABLE")

    def test_phase_audit_or_process_identity_cannot_be_replaced_by_e4_label(self):
        for bad in ("pid", "axiom"):
            _, value = self.simulated(True)
            area = directory(self.root, value["evaluation_id"])
            lab = Checks(self.root, area)
            key = value["results"][0]["run_id"]
            with lab.transaction() as db:
                row = lab.read(key, db)
                if bad == "pid":
                    row["result"]["replay"]["pid"] = row["result"]["candidate"]["pid"]
                else:
                    row["result"]["replay"]["audit"]["axioms"] = ["sorryAx"]
                lab.save(db, row)
            self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])

    def test_supervisor_exit_after_child_is_not_published_or_resumed(self):
        value = prepare(self.root)

        def crash(store, key, fd):
            fixture(store, key, fd, True)
            raise SystemExit(73)

        with patch.object(Checks, "execute", crash):
            with self.assertRaises(SystemExit):
                execute(self.root, value)
        current = report(self.root, value["evaluation_id"])
        self.assertEqual(current["state"], "INTERRUPTED")
        self.assertIsNone(current["summary"])
        self.assertEqual(current["results"], [])
        with patch.object(Checks, "execute", side_effect=AssertionError("No resume")):
            self.assertEqual(execute(self.root, value)["state"], "INTERRUPTED")


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine fixed-budget strategy diagnostics"
)
class RealTests(unittest.TestCase):
    def test_all_eight_pairs_use_real_complete_checks_and_replay(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        prepared = prepare(ROOT)
        (out / "prepared.json").write_bytes(canonical(prepared) + b"\n")
        value = execute(ROOT, prepared)
        (out / "report.json").write_bytes(canonical(value) + b"\n")
        self.assertEqual(value["state"], "COMPLETED", value.get("reason", value.get("error")))
        self.assertEqual(len(value["results"]), 32)
        self.assertEqual(value["summary"]["denominator"], 8)
        for arm in value["summary"]["arms"].values():
            self.assertEqual(arm["proved"] + arm["unknown"], 8)
            self.assertLessEqual(arm["worker_reserved_seconds"], 320)
        for entry in value["results"]:
            child = entry["check"]
            if child and child["math_verdict"] == "PROVED":
                self.assertNotEqual(
                    child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"]
                )
                self.assertTrue(child["result"]["replay"]["audit"]["type_matches"])
        for arm in ("legacy", "current"):
            self.assertEqual(
                value["summary"]["arms"][arm]["case_outcomes"]["missing-semiconjugacy"], "UNKNOWN"
            )
        with patch.object(Checks, "execute", side_effect=AssertionError("No rerun")):
            self.assertEqual(execute(ROOT, prepared)["summary"], value["summary"])

    def test_real_success_then_supervisor_exit_never_publishes_statistics(self):
        out = OUT / ("real-interruption-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        prepared = prepare(ROOT)
        (out / "prepared.json").write_bytes(canonical(prepared) + b"\n")
        script = """import sys
from pathlib import Path
from unittest.mock import patch
from mra.function_dynamics_evaluation import run
from mra.function_dynamics_checks import Checks
original=Checks.execute
count=0
def crash(self,key,fd):
    global count
    result=original(self,key,fd);count+=1
    if result['math_verdict']=='PROVED':sys.exit(73)
    return result
with patch.object(Checks,'execute',crash):run(Path.cwd(),sys.argv[1],plan_fingerprint=sys.argv[2],reviewer='MRA071 interruption reviewer',confirmed=True)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                prepared["evaluation_id"],
                prepared["plan"]["plan_fingerprint"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (out / "stdout.log").write_text(result.stdout)
        (out / "stderr.log").write_text(result.stderr)
        self.assertEqual(result.returncode, 73, result.stderr)
        current = report(ROOT, prepared["evaluation_id"])
        (out / "report.json").write_bytes(canonical(current) + b"\n")
        self.assertEqual(current["state"], "INTERRUPTED")
        self.assertIsNone(current["summary"])
        child = Checks(ROOT, directory(ROOT, prepared["evaluation_id"])).report(
            current["inflight"]["run_id"]
        )
        self.assertEqual(child["math_verdict"], "PROVED")
        self.assertNotEqual(child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"])
        with patch.object(Checks, "execute", side_effect=AssertionError("No resume")):
            self.assertEqual(
                run(
                    ROOT,
                    prepared["evaluation_id"],
                    plan_fingerprint=prepared["plan"]["plan_fingerprint"],
                    reviewer="MRA071 interruption reviewer",
                    confirmed=True,
                )["state"],
                "INTERRUPTED",
            )
