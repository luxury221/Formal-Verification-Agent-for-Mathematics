"""Protocol fixtures are non-evidence; opt-in controls use genuine kernel processes."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import test_obligation_planning as previous
from mra.domain import canonical
from mra.function_dynamics_checks import verified
from mra.lemma_composition import FILES as IMPORT_FILES
from mra.mathlib_lab import audit
from mra.recursion_programs import RecursionStore, prepare
from mra.structure_composition_terms import substitute
from mra.structure_lemma_library import (
    APPLICATION,
    EXPORT,
    FILES,
    ExportApplicationStore,
    LemmaExportStore,
    identity,
    manifest,
    temporary_busy,
)
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structure-lemma-library-20261029-01"


def program(root):
    return previous.proposal(root, previous.cases()[0]["target"])["candidates"][0]["request"]


def export_request(row, slot):
    return {
        "schema": EXPORT,
        "source": {"run_id": row["run_id"], "fingerprint": identity(row)},
        "lemma": row["plan"]["request"]["program"]["lemmas"][slot]["name"],
        "title": "独立导出局部引理 " + str(slot),
        "original": "在全部原载体、函数、显式迭代定义和交换条件下，证明下列完整局部等式；保留原变量 n、x 的量化。"
        + str(slot),
        "timeout_seconds": 20,
    }


def application(export):
    target = deepcopy(export["plan"]["target"])
    target["variables"] = [{"name": "k", "type": "Nat"}, {"name": "y", "type": "a"}]
    goal = substitute(target["goal"], {"n": {"var": "k"}, "x": {"var": "y"}}, {"k", "y"})

    def call(x):
        return {"call": {"name": "h", "args": [x]}}

    target.update(
        title="导出引理跨任务双重观察应用",
        original="在原交换条件下，对重命名的 k、y，证明原核心等式经过两次任意 h 映射后仍成立。",
        goal={"eq": [call(call(t)) for t in goal["eq"]]},
    )
    proof = {
        "lemma": "core",
        "terms": [{"var": "k"}, {"var": "y"}],
        "proofs": [{"hyp": "commutes"}],
    }
    for name in ("z", "w"):
        proof = {
            "congr": {
                "variable": {"name": name, "type": "a"},
                "body": call({"var": name}),
                "proof": proof,
            }
        }
    return {
        "schema": APPLICATION,
        "target": target,
        "sources": [{"alias": "core", "item_id": "export." + export["run_id"]}],
        "proof": proof,
        "timeout_seconds": 20,
    }


def confirm(store, raw):
    return store.submit(
        {
            "request_id": uuid.uuid4().hex,
            "request": raw,
            "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
            "reviewer": "MRA075 complete conditions and new semantic statement reviewer",
            "confirmed": True,
        }
    )


def run(store, raw):
    row = confirm(store, raw)
    store.work_once(row["run_id"])
    return store.report(row["run_id"])


def revoke(store, key):
    return store.revoke(
        key,
        {
            "reviewer": "MRA075 control reviewer",
            "confirmed": True,
            "reason": "Explicit genuine invalidation control",
        },
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        previous.ContractTests.setUp(self)
        for n in (*FILES, *IMPORT_FILES):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / n, p)
        self.exports = LemmaExportStore(self.root)
        self.apps = ExportApplicationStore(self.root, exports=self.exports)
        raw = program(self.root)
        self.fixture = {
            "run_id": "mathlib-" + "0" * 32,
            "plan": prepare(self.root, raw),
            "alignment": {"confirmed": True, "reviewer": "NONEXECUTABLE FIXTURE"},
            "files": {},
            "result": None,
            "submission_fingerprint": "NONEXECUTABLE",
        }

    def preview(self, raw=None):
        with patch.object(self.exports, "parent", return_value=self.fixture):
            return self.exports.preview(raw or export_request(self.fixture, 1))

    def test_export_is_new_complete_statement_with_original_conditions_and_variables(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            p = self.preview()
        old = self.fixture["plan"]["target"]
        for name in ("carriers", "operations", "definitions", "variables", "assumptions"):
            self.assertEqual(p["target"][name], old[name])
        self.assertNotEqual(p["formal_statement"], self.fixture["plan"]["formal_statement"])
        self.assertEqual(len(p["standalone_program"]["program"]["lemmas"]), 1)
        self.assertEqual(p["source_items"], [])
        self.assertEqual((p["math_verdict"], p["evidence_level"]), ("UNKNOWN", "E0"))

    def test_source_schema_fingerprint_lemma_and_budget_rejections(self):
        raw = export_request(self.fixture, 1)
        for update in (
            {"schema": APPLICATION},
            {"source": {"run_id": self.fixture["run_id"], "fingerprint": "bad"}},
            {"lemma": "missing"},
            {"timeout_seconds": True},
            {"original": ""},
            {"path": "/tmp/other"},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.preview({**raw, **update})

    def test_named_queue_cancel_and_manifest_drift_are_persistent(self):
        with patch.object(self.exports, "parent", return_value=self.fixture):
            raw = export_request(self.fixture, 1)
            row = confirm(self.exports, raw)
            self.exports.cancel(row["run_id"])
            self.assertEqual(self.exports.work_once(row["run_id"]), {"worker_state": "IDLE"})
            p = self.root / "mra/structure_lemma_library.py"
            original = p.read_bytes()
            p.write_bytes(original + b"\n")
            self.assertTrue(self.exports.report(row["run_id"])["invalidated"])
            p.write_bytes(original)
            self.assertTrue(self.exports.report(row["run_id"])["invalidated"])
            self.assertTrue(self.exports.list_runs()[0]["invalidated"])

    def test_damage_marker_survives_ledger_byte_restoration(self):
        with patch.object(self.exports, "parent", return_value=self.fixture):
            row = confirm(self.exports, export_request(self.fixture, 0))
            with self.exports.transaction() as db:
                saved = db.execute(
                    "SELECT body,sha256 FROM runs WHERE id=?", (row["run_id"],)
                ).fetchone()
                db.execute("UPDATE runs SET body='{}' WHERE id=?", (row["run_id"],))
            with self.assertRaises(ValueError):
                self.exports.report(row["run_id"])
            with self.exports.transaction() as db:
                db.execute("UPDATE runs SET body=?,sha256=? WHERE id=?", (*saved, row["run_id"]))
            self.assertTrue(self.exports.report(row["run_id"])["invalidated"])

    def test_busy_parent_audit_never_permanently_invalidates_export(self):
        with patch.object(self.exports, "parent", return_value=self.fixture):
            row = confirm(self.exports, export_request(self.fixture, 1))
        for error in (WorkerBusy("temporary"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.exports, "parent", side_effect=error),
                self.assertRaises((WorkerBusy, sqlite3.Error)),
            ):
                self.exports.report(row["run_id"])
            self.assertFalse(self.exports.read(row["run_id"])["invalidated"])
        with self.assertRaises(WorkerBusy):
            temporary_busy(
                lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
            )

    def test_unchecked_parent_and_application_as_source_are_rejected(self):
        row = confirm(self.exports.parents, program(self.root))
        with self.assertRaises(ValueError):
            self.exports.preview(export_request(row, 0))
        for item in ("run.mathlib-" + "1" * 32, "export.mathlib-invalid"):
            with self.assertRaises(ValueError):
                self.apps.load_source({"alias": "core", "item_id": item})

    def test_separate_storage_and_transitive_source_identity(self):
        with self.assertRaises(ValueError):
            LemmaExportStore(
                self.root, parents=self.exports.parents, base=self.exports.parents.base
            )
        with self.assertRaises(ValueError):
            ExportApplicationStore(self.root, exports=self.exports, base=self.exports.base)
        p = manifest(self.root, EXPORT)
        self.assertIn("mra/structure_composition_terms.py", p["files"])
        self.assertIn("mra/recursion_programs.py", p["recursion"]["files"])


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine export/import controls"
)
class RealTests(unittest.TestCase):
    def test_checked_exports_cross_task_import_and_persistent_live_provenance(self):
        area = OUT / ("real-" + uuid.uuid4().hex)
        area.mkdir(parents=True)

        def save(n, v):
            (area / n).write_bytes(canonical(v) + b"\n")

        save(
            "declared-plan.json",
            {
                "program": program(ROOT),
                "runner": "tests/test_structure_lemma_library.py",
                "controls": [
                    "fresh canonical parent",
                    "two independent exports",
                    "renamed double observer application",
                    "wrong condition",
                    "missing condition",
                    "wrong environment",
                    "proof damage and restore",
                    "temporary contention",
                    "parent revocation after genuine replay",
                    "supervisor exit and recovery",
                ],
            },
        )
        records = []

        def checked(store, raw, expected=True):
            row = run(store, raw)
            records.append({"store": str(store.base.relative_to(ROOT)), "check": row})
            save("results.json", records)
            if expected:
                verified(row)
            else:
                self.assertEqual(row["math_verdict"], "UNKNOWN")
            return row

        parents = RecursionStore(ROOT)
        exports = LemmaExportStore(ROOT, parents=parents)
        apps = ExportApplicationStore(ROOT, exports=exports)
        parent = checked(parents, program(ROOT))
        first = checked(exports, export_request(parent, 0))
        core = checked(exports, export_request(parent, 1))
        result = checked(apps, application(core))
        self.assertEqual(len(result["result"]["candidate"]["imports"]), 1)
        self.assertGreater(result["result"]["candidate"]["imports"][0]["replayed_declarations"], 0)
        for update in ("missing", "environment"):
            bad = application(core)
            if update == "missing":
                bad["target"]["assumptions"] = []
            else:
                bad["target"]["operations"][0]["original"] += " changed environment metadata"
            with self.assertRaises(ValueError):
                apps.preview(bad)
        bad = application(core)
        bad["target"]["assumptions"] = [
            {"name": "commutes", "formula": {"eq": [{"var": "y"}, {"var": "y"}]}}
        ]
        checked(apps, bad, False)

        private_parent = RecursionStore(ROOT, base=area / "parent-checks")
        private_export = LemmaExportStore(ROOT, parents=private_parent, base=area / "export-checks")
        private_app = ExportApplicationStore(
            ROOT, exports=private_export, base=area / "application-checks"
        )
        p = checked(private_parent, program(ROOT))
        e1 = checked(private_export, export_request(p, 1))
        a1 = checked(private_app, application(e1))
        proof = private_export.directory(e1["run_id"]) / "replay/proof.ndjson"
        original = proof.read_bytes()
        proof.write_bytes(original + b" ")
        self.assertTrue(private_app.report(a1["run_id"])["invalidated"])
        proof.write_bytes(original)
        self.assertTrue(private_app.report(a1["run_id"])["invalidated"])
        self.assertTrue(private_export.report(e1["run_id"])["invalidated"])
        e2 = checked(private_export, export_request(p, 1))
        a2 = checked(private_app, application(e2))
        with patch.object(
            private_export.parents,
            "gate",
            side_effect=WorkerBusy("temporary genuine parent audit contention"),
        ):
            with self.assertRaises(WorkerBusy):
                private_app.report(a2["run_id"])
        verified(private_app.report(a2["run_id"]))
        pending = confirm(private_app, application(e2))
        original_worker = NativeLeanBackend._run_worker

        def withdraw(backend, *args, **kwargs):
            outcome = original_worker(backend, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING":
                audit(Path(args[2]).read_text(), "MRA_REPLAY:", replay=True)
                revoke(private_parent, p["run_id"])
            return outcome

        with patch.object(NativeLeanBackend, "_run_worker", withdraw):
            private_app.work_once(pending["run_id"])
        blocked = private_app.report(pending["run_id"])
        records.append({"store": str(private_app.base.relative_to(ROOT)), "check": blocked})
        self.assertEqual((blocked["state"], blocked["math_verdict"]), ("POLICY_BLOCKED", "UNKNOWN"))
        self.assertTrue(private_export.report(e2["run_id"])["invalidated"])
        self.assertTrue(private_app.report(a2["run_id"])["invalidated"])

        stopped = confirm(apps, application(core))
        script = """import os,sys
from pathlib import Path
from mra.structure_lemma_library import ExportApplicationStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
 result=original(self,*args,**kwargs)
 if kwargs['phase']=='REPLAY_RUNNING' and result['returncode']==0:os._exit(73)
 return result
NativeLeanBackend._run_worker=stop
ExportApplicationStore(Path.cwd()).work_once(sys.argv[1])
"""
        proc = subprocess.run(
            [sys.executable, "-c", script, stopped["run_id"]],
            cwd=ROOT,
            capture_output=True,
            timeout=180,
        )
        save(
            "supervisor-exit.json",
            {
                "exit_code": proc.returncode,
                "stdout": proc.stdout.decode(),
                "stderr": proc.stderr.decode(),
            },
        )
        self.assertEqual(proc.returncode, 73)
        recovered = apps.recover()
        self.assertIn(stopped["run_id"], recovered["interrupted"])
        interrupted = apps.report(stopped["run_id"])
        self.assertEqual(
            (interrupted["state"], interrupted["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        self.assertEqual(apps.work_once(stopped["run_id"]), {"worker_state": "IDLE"})
        for row in (parent, first, core, result):
            store = parents if row is parent else apps if row is result else exports
            verified(store.report(row["run_id"]))
        save("results.json", records)
        save(
            "controls.json",
            {
                "damage_restoration_stays_invalid": True,
                "temporary_contention_stays_valid": True,
                "parent_revocation_cascades": True,
                "final_commit_withdrawal_blocks_promotion": True,
                "supervisor_exit_recovery": recovered,
                "canonical_positive_runs": [r["run_id"] for r in (parent, first, core, result)],
            },
        )
        save(
            "summary.json",
            {
                "all_passed": True,
                "records": len(records),
                "canonical_export_count": 2,
                "canonical_cross_task_applications": 1,
                "model_calls": 0,
                "blind_evaluation": False,
            },
        )
