"""Nonexecutable protocol fixtures; one separately enabled genuine paired run."""

from copy import deepcopy
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

from mra.domain import canonical
from mra.research_flow import atomic
from mra.reuse_evaluation import DATASET, FILES, directory, prepare, report, run
from mra.reuse_evaluation_checks import DirectChecks, ImportChecks
from mra.reuse_evaluation_terms import compile_case, expand
from mra.search_evaluation import _envelope
from mra.tasks import TaskStore, WorkerBusy
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/reuse-evaluation-20261028-01"
NONEXECUTABLE = (
    canonical({"meta": {"format": {"version": "3.1.0"}, "lean": {"version": "4.35.0"}}}) + b"\n"
)


def execute(root, prepared, **changes):
    return run(
        root,
        prepared["evaluation_id"],
        **{
            "plan_fingerprint": prepared["plan"]["plan_fingerprint"],
            "reviewer": "MRA052 authored program alignment reviewer",
            "confirmed": True,
            **changes,
        },
    )


def synthetic(store, key, _fd):
    """Fake metadata only; must never be executed by Lean or counted as math evidence."""
    row = store.read(key)
    area = store.directory(key)
    (area / "replay").mkdir(parents=True)
    (area / "replay/proof.ndjson").write_bytes(NONEXECUTABLE)
    rejected = row["plan"]["request"]["title"].startswith("待检")
    row.update(
        state="REJECTED" if rejected else "COMPLETED",
        stage="NONEXECUTABLE",
        math_verdict="UNKNOWN" if rejected else "PROVED",
        evidence_level="E0" if rejected else "E4",
        files={"replay/proof.ndjson": hashlib.sha256(NONEXECUTABLE).hexdigest()},
        result={
            "worker_seconds": 1,
            "candidate": {"pid": 101},
            "replay": None if rejected else {"pid": 102},
        },
    )
    with store.transaction() as db:
        store.save(db, row)
    return store.report(key)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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

    def simulated(self):
        with (
            patch.object(DirectChecks, "execute", synthetic),
            patch.object(ImportChecks, "execute", synthetic),
        ):
            value = prepare(self.root)
            return value, execute(self.root, value)

    def test_fixed_complete_dataset_equal_ceilings_and_no_execution_on_prepare(self):
        with patch.object(DirectChecks, "execute", side_effect=AssertionError("No Lean")):
            value = prepare(self.root)
        self.assertIsNone(value["summary"])
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertFalse(value["plan"]["independent_blind_evaluation"])
        self.assertEqual(value["plan"]["budget"]["per_case_per_arm_worker_seconds"], 60)
        self.assertEqual(len(value["plan"]["cases"]), 5)
        self.assertFalse(list(directory(self.root).rglob("*.sqlite3")))

    def test_simultaneous_binder_and_premise_substitution_does_not_revisit_actuals(self):
        source = {
            "variables": ["x", "y"],
            "assumptions": [{"name": "h"}],
            "proof": {
                "ref": "Nat.dvd_trans",
                "terms": [{"var": "x"}, {"var": "y"}, {"var": "x"}],
                "proofs": [{"hyp": "h"}, {"hyp": "h"}],
            },
        }
        actual = {"lemma": "prior", "terms": [{"var": "y"}, {"var": "x"}], "proofs": [{"hyp": "h"}]}
        original = deepcopy((source, actual))
        result = expand(actual, source)
        self.assertEqual(result["terms"], [{"var": "y"}, {"var": "x"}, {"var": "y"}])
        self.assertEqual(result["proofs"], [{"hyp": "h"}, {"hyp": "h"}])
        self.assertEqual((source, actual), original)

    def test_nested_actual_proofs_expand_without_recursively_substituting_terms(self):
        source = {"variables": ["a"], "assumptions": [{"name": "p"}], "proof": {"hyp": "p"}}
        inner = {"lemma": "prior", "terms": [{"var": "x"}], "proofs": [{"hyp": "original"}]}
        self.assertEqual(
            expand({"lemma": "prior", "terms": [{"var": "y"}], "proofs": [inner]}, source),
            {"hyp": "original"},
        )

    def test_missing_premises_target_drift_and_nonuniform_budget_are_rejected(self):
        case = json.loads((self.root / DATASET).read_text())["cases"][1]
        for mutate in (
            lambda c: c["proof"].update(proofs=[]),
            lambda c: c["target"].update(timeout_seconds=29),
            lambda c: c["source"].update(timeout_seconds=True),
        ):
            bad = deepcopy(case)
            mutate(bad)
            with self.assertRaises(ValueError):
                compile_case(bad)

    def test_all_denominators_failures_blocks_costs_and_idempotent_run(self):
        prepared, value = self.simulated()
        self.assertEqual(value["state"], "COMPLETED")
        self.assertEqual(
            [value["summary"]["arms"][a]["proved"] for a in ("import", "inline")], [3, 3]
        )
        self.assertEqual(
            [value["summary"]["arms"][a]["unknown"] for a in ("import", "inline")], [2, 2]
        )
        self.assertEqual(value["summary"]["arms"]["import"]["cold_cost"]["checks"], 8)
        self.assertEqual(value["summary"]["arms"]["inline"]["cold_cost"]["checks"], 4)
        with patch.object(DirectChecks, "execute", side_effect=AssertionError("No rerun")):
            self.assertEqual(execute(self.root, prepared)["summary"], value["summary"])

    def test_confirmation_budget_and_reviewer_cannot_change(self):
        value = prepare(self.root)
        for changes in ({"confirmed": False}, {"plan_fingerprint": "0" * 64}):
            with self.assertRaises(ValueError):
                execute(self.root, value, **changes)
        with (
            patch.object(DirectChecks, "execute", synthetic),
            patch.object(ImportChecks, "execute", synthetic),
        ):
            execute(self.root, value)
        with self.assertRaises(ValueError):
            execute(self.root, value, reviewer="another reviewer")

    def test_busy_worker_does_not_start_or_consume_budget(self):
        value = prepare(self.root)
        with TaskStore(self.root).worker_lock():
            with self.assertRaises(WorkerBusy):
                execute(self.root, value)
        self.assertEqual(report(self.root, value["evaluation_id"])["state"], "PREPARED")

    def test_implementation_drift_permanently_suppresses_summary(self):
        value = prepare(self.root)
        path = self.root / FILES[1]
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        self.assertFalse(report(self.root, value["evaluation_id"])["integrity_ok"])
        path.write_bytes(original)
        self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])

    def test_damaged_child_and_restored_bytes_do_not_revive_statistics(self):
        _, value = self.simulated()
        child = value["results"][0]["run_id"]
        path = (
            DirectChecks(self.root, directory(self.root, value["evaluation_id"])).directory(child)
            / "replay/proof.ndjson"
        )
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        damaged = report(self.root, value["evaluation_id"])
        self.assertFalse(damaged["integrity_ok"])
        self.assertIsNone(damaged["summary"])
        path.write_bytes(original)
        self.assertEqual(report(self.root, value["evaluation_id"])["state"], "UNAVAILABLE")

    def test_withdrawn_source_suppresses_whole_comparison(self):
        _, value = self.simulated()
        direct = DirectChecks(self.root, directory(self.root, value["evaluation_id"]))
        direct.revoke(
            value["results"][0]["run_id"],
            {
                "reviewer": "NONEXECUTABLE fixture",
                "reason": "Contract revocation",
                "confirmed": True,
            },
        )
        self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])

    def test_completed_report_cannot_drop_duplicate_or_reorder_entries(self):
        for change in (lambda e: e.pop(), lambda e: e.__setitem__(1, e[0]), lambda e: e.reverse()):
            _, value = self.simulated()
            area = directory(self.root, value["evaluation_id"])
            row = _envelope(area / "record.json")
            change(row["entries"])
            atomic(area / "record.json", row)
            self.assertIsNone(report(self.root, value["evaluation_id"])["summary"])

    def test_supervisor_exit_after_child_success_does_not_publish_or_resume(self):
        value = prepare(self.root)

        def crash(store, key, fd):
            synthetic(store, key, fd)
            raise SystemExit(73)

        with patch.object(DirectChecks, "execute", crash):
            with self.assertRaises(SystemExit):
                execute(self.root, value)
        result = report(self.root, value["evaluation_id"])
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertIsNone(result["summary"])
        self.assertEqual(result["results"], [])
        with patch.object(DirectChecks, "execute", side_effect=AssertionError("No resume")):
            self.assertEqual(execute(self.root, value)["state"], "INTERRUPTED")


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine paired evaluation")
class RealTests(unittest.TestCase):
    def test_real_source_success_followed_by_supervisor_exit_is_not_published(self):
        out = OUT / ("real-interruption-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        value = prepare(ROOT)
        (out / "prepared.json").write_bytes(canonical(value) + b"\n")
        script = """import sys
from unittest.mock import patch
from pathlib import Path
from mra.reuse_evaluation import run
from mra.reuse_evaluation_checks import DirectChecks
original = DirectChecks.execute
def interrupted(self, key, fd):
    result = original(self, key, fd)
    assert result['math_verdict'] == 'PROVED', result
    sys.exit(73)
with patch.object(DirectChecks, 'execute', interrupted):
    run(Path.cwd(), sys.argv[1], plan_fingerprint=sys.argv[2], reviewer='MRA052 interruption reviewer', confirmed=True)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                value["evaluation_id"],
                value["plan"]["plan_fingerprint"],
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        (out / "stdout.log").write_text(result.stdout)
        (out / "stderr.log").write_text(result.stderr)
        self.assertEqual(result.returncode, 73, result.stderr)
        current = report(ROOT, value["evaluation_id"])
        (out / "report.json").write_bytes(canonical(current) + b"\n")
        self.assertEqual(current["state"], "INTERRUPTED")
        self.assertIsNone(current["summary"])
        self.assertEqual(current["results"], [])
        child = DirectChecks(ROOT, directory(ROOT, value["evaluation_id"])).report(
            current["inflight"]["run_id"]
        )
        self.assertEqual(child["math_verdict"], "PROVED")
        self.assertNotEqual(child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"])
        with TaskStore(ROOT).worker_lock():
            pass

    def test_all_five_pairs_with_real_prerequisites_and_independent_replay(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        prepared = prepare(ROOT)
        (out / "prepared.json").write_bytes(canonical(prepared) + b"\n")
        value = execute(ROOT, prepared)
        (out / "report.json").write_bytes(canonical(value) + b"\n")
        self.assertEqual(value["state"], "COMPLETED", value.get("reason", value.get("error")))
        self.assertEqual(
            [value["summary"]["arms"][a]["proved"] for a in ("import", "inline")], [3, 3]
        )
        self.assertEqual(
            [value["summary"]["arms"][a]["unknown"] for a in ("import", "inline")], [2, 2]
        )
        wrong = [
            r for r in value["results"] if r["case_id"] == "wrong_premise" and r["kind"] != "source"
        ]
        self.assertEqual([r["status"] for r in wrong], ["REJECTED", "REJECTED"])
        for entry in value["results"]:
            child = entry["check"]
            if child and child["state"] == "COMPLETED":
                result = child["result"]
                self.assertNotEqual(result["candidate"]["pid"], result["replay"]["pid"])
                self.assertTrue(result["candidate"]["audit"]["type_matches"])
        self.assertEqual(value["summary"]["arms"]["import"]["cold_cost"]["checks"], 8)
        self.assertEqual(value["summary"]["arms"]["inline"]["cold_cost"]["checks"], 4)
        self.assertEqual(report(ROOT, prepared["evaluation_id"])["summary"], value["summary"])
