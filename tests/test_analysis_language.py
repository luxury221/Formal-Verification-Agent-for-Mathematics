"""Typed analysis intake, ledger safety and opt-in real kernel verification."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra import analysis_foundation as foundation
from mra.analysis_examples import examples, op, r, v
from mra.analysis_lab import AnalysisStore, TRUST_FILES
from mra.analysis_language import Compiler, compile_request, bounded
from mra.domain import canonical
from mra.integrity import ContentSnapshot, file_hash
from mra.tasks import WorkerBusy

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in TRUST_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.store = AnalysisStore(self.root)

    def submit(self, request=None, request_id=None):
        request = request or deepcopy(examples()[0]["request"])
        preview = self.store.preview(request)
        return self.store.submit(
            {
                "request_id": request_id or uuid.uuid4().hex,
                "request": request,
                "plan_fingerprint": preview["plan_fingerprint"],
                "confirmed": True,
                "reviewer": "Contract review, E0 only",
            }
        )

    def test_complete_context_and_all_examples_compile(self):
        for entry in examples():
            request = entry["request"]
            compiled = compile_request(request)
            self.assertEqual(compiled["request"], request)
            self.assertEqual(len(compiled["conditions"]), len(request["assumptions"]))
            for condition in compiled["conditions"]:
                self.assertIn(condition["formal_statement"], compiled["formal_statement"])
            for space in compiled["space_instances"]:
                self.assertIn("NormedAddCommGroup " + space["lean"], compiled["formal_statement"])
                self.assertIn("InnerProductSpace ℝ " + space["lean"], compiled["formal_statement"])
        unused = compile_request(examples()[11]["request"])
        self.assertEqual(unused["conditions"][0]["name"], "unused")

    def test_lambda_and_quantifier_bindings_are_lexical(self):
        compiled = compile_request(examples()[10]["request"])
        bindings = compiled["bindings"]
        shadow = [b for b in bindings if b["name"] == "x"]
        self.assertEqual(len(shadow), 2)
        self.assertNotEqual(shadow[0]["lean"], shadow[1]["lean"])
        self.assertIn(bindings[1]["lean"], compiled["formal_statement"])
        request = deepcopy(examples()[4]["request"])
        request["goal"] = op("le", r(0), v("z"))
        with self.assertRaisesRegex(ValueError, "Unbound"):
            compile_request(request)

    def test_distinct_spaces_and_function_domains_do_not_coerce(self):
        request = deepcopy(examples()[2]["request"])
        request["spaces"].append({"name": "G", "structure": "real-inner-product"})
        request["variables"][1]["type"] = {"space": "G"}
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[0]["request"])
        request["variables"][2]["type"] = {"space": "E"}
        with self.assertRaises(ValueError):
            compile_request(request)
        compiler = Compiler([])
        with self.assertRaises(ValueError):
            compiler.typ("Complex")
        with self.assertRaises(ValueError):
            compiler.typ({"space": "undeclared"})

    def test_no_arbitrary_source_or_unapproved_proof_rule(self):
        for field, value in (("extra_declarations", "axiom bad : False"), ("imports", ["Mathlib"])):
            request = deepcopy(examples()[0]["request"])
            request[field] = value
            with self.assertRaises(ValueError):
                compile_request(request)
        for proof in (
            {"tactic": "simp; sorry"},
            {"term": {"axiom": "False"}},
            {"term": {"rule": {"name": "sorryAx", "terms": [], "proofs": []}}},
        ):
            request = deepcopy(examples()[0]["request"])
            request["proof"] = proof
            with self.assertRaises(ValueError):
                compile_request(request)
        request = deepcopy(examples()[0]["request"])
        request["title"] = "import Evil; sorry"
        self.assertNotIn("Evil", compile_request(request)["proof_source"])

    def test_numeric_and_structural_bounds(self):
        request = deepcopy(examples()[3]["request"])
        for bad in (
            {"real": [True, 1]},
            {"real": [1, 0]},
            {"real": [0.5, 1]},
            {"pow": [v("a"), 100]},
        ):
            request["goal"] = op("eq", bad, r(1))
            with self.assertRaises(ValueError):
                compile_request(request)
        cycle = {}
        cycle["cycle"] = cycle
        with self.assertRaises(ValueError):
            bounded(cycle)
        request["goal"] = op("eq", r(2, 2), r(1))
        self.assertIn("(1 : ℝ)", compile_request(request)["formal_statement"])

    def test_variable_and_hypothesis_scope_guards(self):
        request = deepcopy(examples()[3]["request"])
        request["variables"].append(deepcopy(request["variables"][0]))
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[3]["request"])
        request["assumptions"][0]["name"] = "a"
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[3]["request"])
        request["proof"] = {"term": {"hyp": "missing"}}
        with self.assertRaises(ValueError):
            compile_request(request)

    def test_preview_confirmation_and_idempotent_submission(self):
        request = deepcopy(examples()[0]["request"])
        preview = self.store.preview(request)
        envelope = {
            "request_id": "stable",
            "request": request,
            "plan_fingerprint": preview["plan_fingerprint"],
            "reviewer": "Local author",
            "confirmed": False,
        }
        with self.assertRaises(ValueError):
            self.store.submit(envelope)
        envelope["confirmed"] = True
        first = self.store.submit(envelope)
        self.assertEqual(first, self.store.submit(envelope))
        self.assertEqual(first["math_verdict"], "UNKNOWN")
        altered = deepcopy(envelope)
        altered["request"]["original"] += " changed"
        with self.assertRaises(ValueError):
            self.store.submit(altered)
        self.assertFalse((self.store.base / "runs").exists())

    def test_cancelled_request_never_executes_or_requeues(self):
        row = self.submit(request_id="cancelled")
        cancelled = self.store.cancel(row["run_id"])
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")
        self.assertEqual(self.submit(request_id="cancelled")["state"], "CANCELLED")
        self.assertFalse(self.store.directory(row["run_id"]).exists())

    def test_provider_restoration_and_old_ledger_row_do_not_revive(self):
        row = self.submit()
        target = self.root / "mra/analysis_language.py"
        saved = target.read_bytes()
        target.write_bytes(saved + b"\n# altered provider\n")
        invalid = self.store.report(row["run_id"])
        self.assertTrue(invalid["invalidated"])
        target.write_bytes(saved)
        with self.store.transaction() as db:
            self.store.save(db, row)
        restored = self.store.report(row["run_id"])
        self.assertTrue(restored["invalidated"])
        self.assertEqual(restored["math_verdict"], "UNKNOWN")

    def test_corrupt_ledger_cannot_revive_after_restoration(self):
        row = self.submit()
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET body='{}',sha256='bad' WHERE id=?", (row["run_id"],))
        with self.assertRaises(ValueError):
            self.store.report(row["run_id"])
        with self.store.transaction() as db:
            self.store.save(db, row)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_busy_database_does_not_permanently_invalidate(self):
        row = self.submit()
        db = sqlite3.connect(self.store.path, isolation_level=None)
        self.addCleanup(db.close)
        db.execute("BEGIN EXCLUSIVE")
        with self.assertRaises(sqlite3.OperationalError):
            self.store.report(row["run_id"])
        db.rollback()
        self.assertFalse(self.store.marker(row["run_id"]).exists())
        self.assertFalse(self.store.report(row["run_id"])["invalidated"])

    def test_recovery_is_locked_and_never_retries(self):
        row = self.submit()
        with self.store.transaction() as db:
            row.update(state="RUNNING", stage="LEAN_RUNNING")
            self.store.save(db, row)
        with self.store.tasks.worker_lock():
            with self.assertRaises(WorkerBusy):
                self.store.recover()
        recovered = self.store.recover()
        self.assertEqual(recovered["interrupted"], [row["run_id"]])
        current = self.store.report(row["run_id"])
        self.assertEqual(current["state"], "INTERRUPTED")
        self.assertEqual(current["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")

    def test_foreign_or_redirected_ledger_is_not_adopted(self):
        base = self.root / "artifacts/foreign/analysis-language-private"
        base.mkdir(parents=True)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            db.execute("CREATE TABLE runs (id TEXT)")
        with self.assertRaises(ValueError):
            AnalysisStore(self.root, base=base)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            self.assertNotIn(
                "analysis_identity", [r[0] for r in db.execute("SELECT name FROM sqlite_master")]
            )
        redirect = self.root / "artifacts/redirect/analysis-language-private"
        redirect.parent.mkdir()
        redirect.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            AnalysisStore(self.root, base=redirect)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "opt-in real Lean")
class RealTests(unittest.TestCase):
    def test_typed_analysis_targets_and_real_lifecycle_controls(self):
        resume = os.environ.get("MRA_ANALYSIS_RESUME")
        stage = ROOT / "artifacts/analysis-language-20261029-01"
        out = ROOT / resume if resume else stage / ("real-" + uuid.uuid4().hex)
        if out.resolve() != out or out.parent != stage or not out.name.startswith("real-"):
            raise ValueError("Resume only an exact local analysis acceptance area")
        if not resume:
            out.mkdir()
        cases = examples()
        declared = {
            "cases": cases,
            "attempts_per_complete_target": 1,
            "per_target_worker_seconds": 30,
            "source_exposure": "repository-authored and visible, not blind",
            "model_calls": 0,
            "extra_math_checks": [
                "cancel after actual successful replay of pointwise-cauchy-schwarz"
            ],
            "invalidation_controls": [
                "lambda-beta actual proof data corruption/restoration",
                "unused-original-condition actual ledger corruption/restoration",
            ],
        }
        if resume:
            self.assertEqual(json.loads((out / "declared-plan.json").read_text()), declared)
        else:
            (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        old_analysis = foundation.load(ROOT)
        store = AnalysisStore(ROOT, base=out / "analysis-language-private")
        rows = json.loads((out / "results.json").read_text()) if resume else []
        if resume:
            self.assertEqual([e["id"] for e in rows], [e["id"] for e in cases[: len(rows)]])
            with store.transaction(False) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], len(rows))
            for entry in rows:
                row = entry["check"]
                self.assertEqual(store.read(row["run_id"]), row)
                self.assertNotIn(row["state"], ("QUEUED", "RUNNING"))
                self.assertIsNone(store.gate(row, False))
                for name, expected in row["files"].items():
                    self.assertEqual(file_hash(store.directory(row["run_id"]) / name), expected)
            cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(
                ":"
            )[-1].lstrip("/")
            continuation = {
                "previous_results_sha256": file_hash(out / "results.json"),
                "declared_plan_sha256": file_hash(out / "declared-plan.json"),
                "retained": [e["id"] for e in rows],
                "execute_only": [e["id"] for e in cases[len(rows) :]],
                "no_completed_or_failed_target_retried": True,
                "memory_max": (cgroup / "memory.max").read_text().strip(),
                "memory_swap_max": (cgroup / "memory.swap.max").read_text().strip(),
                "purpose": "Complete unattempted coverage; timeouts remain UNKNOWN, completion coverage reported separately",
            }
            (out / ("continuation-" + uuid.uuid4().hex + ".json")).write_bytes(
                canonical(continuation) + b"\n"
            )
        retained = len(rows)
        for index, entry in enumerate(cases):
            plan = store.preview(entry["request"])
            if index < retained:
                continue
            row = store.submit(
                {
                    "request_id": entry["id"],
                    "request": entry["request"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "Codex full typed analysis alignment review",
                    "confirmed": True,
                }
            )
            self.assertEqual(store.work_once(row["run_id"])["worker_state"], "EXECUTED")
            checked = store.read(row["run_id"])
            rows.append({"id": entry["id"], "check": checked})
            (out / "results.json").write_bytes(canonical(rows) + b"\n")
            with self.subTest(target=entry["id"]):
                self.assertIn(checked["state"], (entry["expected"], "TIMEOUT"))
            self.assertEqual(
                checked["math_verdict"], "PROVED" if checked["state"] == "COMPLETED" else "UNKNOWN"
            )
            self.assertFalse(checked["invalidated"])
            if checked["state"] == "COMPLETED":
                self.assertNotEqual(
                    checked["result"]["candidate"]["pid"], checked["result"]["replay"]["pid"]
                )
                self.assertEqual(checked["plan"]["conditions"], plan["conditions"])
        self.assertTrue(
            all(
                e["check"]["state"] in (c["expected"], "TIMEOUT")
                for e, c in zip(rows, cases, strict=True)
            )
        )
        positives = [
            e for e in rows if e["id"] in {c["id"] for c in cases if c["expected"] == "COMPLETED"}
        ]
        self.assertGreaterEqual(sum(e["check"]["state"] == "COMPLETED" for e in positives), 4)
        for entry in rows:
            if entry["check"]["state"] == "TIMEOUT":
                self.assertEqual(entry["check"]["math_verdict"], "UNKNOWN")
                self.assertEqual(entry["check"]["evidence_level"], "E0")
        request = deepcopy(cases[0]["request"])
        request["title"] = "cancel after real replay"
        plan = store.preview(request)
        cancel = store.submit(
            {
                "request_id": "actual-cancel-after-replay",
                "request": request,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "Codex cancellation control",
                "confirmed": True,
            }
        )
        original = foundation._Runner._run_worker

        def execute_then_cancel(runner, *args, **kwargs):
            result = original(runner, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING" and result["returncode"] == 0:
                store.cancel(cancel["run_id"])
            return result

        with patch.object(foundation._Runner, "_run_worker", execute_then_cancel):
            store.work_once(cancel["run_id"])
        cancelled = store.report(cancel["run_id"])
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(cancelled["result"]["replay"]["returncode"], 0)
        self.assertEqual(cancelled["math_verdict"], "UNKNOWN")
        rows.append({"id": "cancel-after-replay", "check": store.read(cancel["run_id"])})
        (out / "results.json").write_bytes(canonical(rows) + b"\n")
        beta = next(row["check"] for row in rows if row["id"] == "lambda-beta")
        self.assertEqual(beta["math_verdict"], "PROVED")
        data = store.directory(beta["run_id"]) / "replay/proof.ndjson"
        saved = data.read_bytes()
        (out / "beta-original-proof.ndjson").write_bytes(saved)
        data.write_bytes(saved + b" ")
        self.assertTrue(store.report(beta["run_id"])["invalidated"])
        data.write_bytes(saved)
        beta_after = store.report(beta["run_id"])
        self.assertTrue(beta_after["invalidated"])
        unused = next(row["check"] for row in rows if row["id"] == "unused-original-condition")
        self.assertEqual(unused["math_verdict"], "PROVED")
        (out / "unused-original-ledger-row.json").write_bytes(canonical(unused) + b"\n")
        with store.transaction() as db:
            db.execute("UPDATE runs SET body='{}',sha256='bad' WHERE id=?", (unused["run_id"],))
        with self.assertRaises(ValueError):
            store.report(unused["run_id"])
        with store.transaction() as db:
            store.save(db, unused)
        unused_after = store.report(unused["run_id"])
        self.assertTrue(unused_after["invalidated"])
        self.assertEqual(unused_after["math_verdict"], "UNKNOWN")
        old_analysis.verify()
        legacy.verify()
        summary = {
            "all_passed": True,
            "assertion_scope": "protocol integrity and actual controls; capacity observations retained separately",
            "expected_positive_targets": len(positives),
            "positive_completions": sum(e["check"]["state"] == "COMPLETED" for e in positives),
            "positive_unresolved": [
                e["id"] for e in positives if e["check"]["state"] != "COMPLETED"
            ],
            "store": str(store.base.relative_to(ROOT)),
            "checks": len(rows),
            "worker_processes": sum(
                sum(bool(e["check"]["result"].get(p)) for p in ("candidate", "replay"))
                for e in rows
            ),
            "invalidation": {"artifact_restored": beta_after, "ledger_restored": unused_after},
            "old_runtime_verified": True,
            "analysis_foundation_verified": True,
            "model_calls": 0,
        }
        (out / "summary.json").write_bytes(canonical(summary) + b"\n")
