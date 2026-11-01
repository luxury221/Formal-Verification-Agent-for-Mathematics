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
from mra.structure_reuse_evaluation import DATASET, FILES, directory, prepare, report, run
from mra.structure_evaluation_checks import DirectChecks, ImportChecks
from mra.structure_evaluation_terms import compile_case, compile_program, INLINE_MODE
from mra.search_evaluation import _envelope
from mra.tasks import TaskStore, WorkerBusy
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-reuse-eval-20261029-01"
NONEXECUTABLE = (
    canonical({"meta": {"format": {"version": "3.1.0"}, "lean": {"version": "4.35.0"}}}) + b"\n"
)


def execute(root, prepared, **changes):
    return run(
        root,
        prepared["evaluation_id"],
        **{
            "plan_fingerprint": prepared["plan"]["plan_fingerprint"],
            "reviewer": "MRA064 authored program alignment reviewer",
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
    request = row["plan"]["request"]
    rejected = request.get("target", request)["title"].startswith("待检")
    row.update(
        state="REJECTED" if rejected else "COMPLETED",
        stage="NONEXECUTABLE",
        math_verdict="UNKNOWN" if rejected else "PROVED",
        evidence_level="E0" if rejected else "E4",
        files={"replay/proof.ndjson": hashlib.sha256(NONEXECUTABLE).hexdigest()},
        result={
            "worker_seconds": 1,
            "candidate": {
                "pid": 101,
                "run_status": "COMPLETED",
                "returncode": 0,
                "audit": {"type_matches": True},
            },
            "replay": None
            if rejected
            else {
                "pid": 102,
                "run_status": "COMPLETED",
                "returncode": 0,
                "audit": {"type_matches": True},
            },
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
        self.assertEqual(len(value["plan"]["cases"]), 6)
        self.assertFalse(list(directory(self.root).rglob("*.sqlite3")))
        self.assertEqual(value["plan"]["budget"]["maximum_checks"], 16)
        self.assertEqual(value["plan"]["budget"]["combined_worker_seconds"], 480)

    def test_inline_uses_same_closed_source_program_and_complete_target(self):
        cases = json.loads((self.root / DATASET).read_text())["cases"]
        for case in cases:
            original = deepcopy(case)
            compiled = compile_case(case)
            self.assertEqual(case, original)
            self.assertIn(
                compiled["programs"]["source"].splitlines()[-1].strip(),
                compiled["programs"]["inline"],
            )
            self.assertNotIn("MraCandidate.Reused", compiled["programs"]["inline"])
            self.assertIn("@MraCandidate.Reused.s0.checked", compiled["programs"]["import"])
            self.assertEqual(
                compile_program(compiled["inline_request"])["formal_statement"],
                compiled["formal_statement"],
            )
        self.assertTrue(
            all("try clear mra_h0" in compile_case(c)["programs"]["source"] for c in cases)
        )
        self.assertEqual(compile_case(cases[1])["application_count"], 2)
        self.assertIn("∀", compile_case(cases[-1])["formal_statement"])

    def test_schema_shared_definitions_and_arbitrary_tactics_are_rejected(self):
        case = json.loads((self.root / DATASET).read_text())["cases"][0]
        for alter in (
            lambda c: c["source"].update(proof={"method": "sorry"}),
            lambda c: c["target"]["carriers"][0].update(original="changed"),
            lambda c: c.update(source_available=1),
        ):
            bad = deepcopy(case)
            alter(bad)
            with self.assertRaises(ValueError):
                compile_case(bad)
        request = compile_case(case)["inline_request"]
        with self.assertRaises(ValueError):
            compile_program({**request, "schema": "typed-proof-composition-v1"})
        self.assertEqual(request["schema"], INLINE_MODE)

    def test_missing_premises_target_drift_and_nonuniform_budget_are_rejected(self):
        case = json.loads((self.root / DATASET).read_text())["cases"][2]
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
            [value["summary"]["arms"][a]["proved"] for a in ("import", "inline")], [3, 4]
        )
        self.assertEqual(
            [value["summary"]["arms"][a]["unknown"] for a in ("import", "inline")], [3, 2]
        )
        self.assertEqual(value["summary"]["arms"]["import"]["cold_cost"]["checks"], 9)
        self.assertEqual(value["summary"]["arms"]["inline"]["cold_cost"]["checks"], 6)
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

    def test_dataset_drift_drops_reorders_and_duplicates_are_rejected(self):
        original = (self.root / DATASET).read_bytes()
        for change in (lambda c: c.pop(), lambda c: c.reverse(), lambda c: c.__setitem__(1, c[0])):
            data = json.loads(original)
            change(data["cases"])
            (self.root / DATASET).write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                prepare(self.root)
        (self.root / DATASET).write_bytes(original)

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
from mra.structure_reuse_evaluation import run
from mra.structure_evaluation_checks import DirectChecks
original = DirectChecks.execute
def interrupted(self, key, fd):
    result = original(self, key, fd)
    assert result['math_verdict'] == 'PROVED', result
    sys.exit(73)
with patch.object(DirectChecks, 'execute', interrupted):
    run(Path.cwd(), sys.argv[1], plan_fingerprint=sys.argv[2], reviewer='MRA064 interruption reviewer', confirmed=True)
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

    def test_inline_source_tactic_cannot_use_false_outer_assumption(self):
        from mra.structure_evaluation_terms import INLINE_MODE

        out = OUT / ("real-isolation-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        direct = DirectChecks(ROOT, out, create=True)
        case = json.loads((ROOT / DATASET).read_text())["cases"][5]
        source = deepcopy(case["source"])
        target = deepcopy(source)
        target["assumptions"].append(
            {"name": "impossible", "formula": {"not": {"eq": [{"var": "x"}, {"var": "x"}]}}}
        )
        request = {
            "schema": INLINE_MODE,
            "source": source,
            "target": target,
            "proof": case["proof"],
            "timeout_seconds": 30,
        }
        plan = direct.preview(request)
        child = direct.submit(
            {
                "request_id": "isolation-" + uuid.uuid4().hex,
                "request": request,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "MRA064 closed source isolation reviewer",
                "confirmed": True,
            }
        )
        with TaskStore(ROOT).worker_lock() as fd:
            with direct.transaction() as db:
                row = direct.read(child["run_id"], db)
                row.update(state="RUNNING", stage="CHECKING")
                direct.save(db, row)
            result = direct.execute(child["run_id"], fd)
        (out / "report.json").write_bytes(canonical(result) + b"\n")
        self.assertEqual(result["state"], "REJECTED", result.get("result"))
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertIsNone(result["result"]["replay"])
        self.assertNotIn("unexpected", result["result"]["error"])
        self.assertTrue(result["result"]["error"])

    def test_all_six_pairs_with_real_prerequisites_and_independent_replay(self):
        out = OUT / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        prepared = prepare(ROOT)
        (out / "prepared.json").write_bytes(canonical(prepared) + b"\n")
        value = execute(ROOT, prepared)
        (out / "report.json").write_bytes(canonical(value) + b"\n")
        self.assertEqual(value["state"], "COMPLETED", value.get("reason", value.get("error")))
        self.assertEqual(
            [value["summary"]["arms"][a]["proved"] for a in ("import", "inline")], [3, 4]
        )
        self.assertEqual(
            [value["summary"]["arms"][a]["unknown"] for a in ("import", "inline")], [3, 2]
        )
        wrong = [
            r
            for r in value["results"]
            if r["case_id"] == "wrong-identity-premise" and r["kind"] != "source"
        ]
        self.assertEqual([r["status"] for r in wrong], ["REJECTED", "REJECTED"])
        for entry in value["results"]:
            child = entry["check"]
            if child and child["state"] == "COMPLETED":
                result = child["result"]
                self.assertNotEqual(result["candidate"]["pid"], result["replay"]["pid"])
                self.assertTrue(result["candidate"]["audit"]["type_matches"])
        self.assertEqual(value["summary"]["arms"]["import"]["cold_cost"]["checks"], 9)
        self.assertEqual(value["summary"]["arms"]["inline"]["cold_cost"]["checks"], 6)
        self.assertEqual(report(ROOT, prepared["evaluation_id"])["summary"], value["summary"])
