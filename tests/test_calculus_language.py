"""Typed calculus contracts and explicit opt-in genuine kernel/lifecycle tests."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra import calculus_foundation_v2 as foundation
from mra import calculus_lab as lab
from mra.calculus_lab import CalculusStore, TRUST_FILES
from mra.calculus_language import Compiler, compile_request, bounded, catalog
from mra.calculus_examples import examples, integral, lam
from mra.analysis_examples import op, r, v
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
        self.store = CalculusStore(self.root)

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
        request = deepcopy(examples()[2]["request"])
        request["variables"].append(deepcopy(request["variables"][0]))
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[2]["request"])
        request["assumptions"][0]["name"] = "f"
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[2]["request"])
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
        target = self.root / "mra/calculus_language.py"
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
        base = self.root / "artifacts/foreign/calculus-language-private"
        base.mkdir(parents=True)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            db.execute("CREATE TABLE runs (id TEXT)")
        with self.assertRaises(ValueError):
            CalculusStore(self.root, base=base)
        with sqlite3.connect(base / "lab.sqlite3") as db:
            self.assertNotIn(
                "calculus_identity", [r[0] for r in db.execute("SELECT name FROM sqlite_master")]
            )
        redirect = self.root / "artifacts/redirect/calculus-language-private"
        redirect.parent.mkdir()
        redirect.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            CalculusStore(self.root, base=redirect)

    def test_full_original_conditions_and_measures_survive(self):
        for entry in examples():
            request = entry["request"]
            plan = compile_request(request)
            self.assertEqual(plan["request"], request)
            self.assertEqual(len(plan["conditions"]), len(request["assumptions"]))
            for condition in plan["conditions"]:
                self.assertIn(condition["formal_statement"], plan["formal_statement"])
            self.assertEqual(
                plan["math_verdict"] if "math_verdict" in plan else "UNKNOWN", "UNKNOWN"
            )
        measured = compile_request(examples()[7]["request"])
        self.assertIn("Measure ℝ", measured["formal_statement"])
        self.assertIn(".real (Set.Ioc", measured["formal_statement"])
        self.assertEqual(len(compile_request(examples()[2]["request"])["conditions"]), 2)
        self.assertEqual(len(compile_request(examples()[4]["request"])["conditions"]), 2)

    def test_interval_binder_does_not_capture_endpoints(self):
        plan = compile_request(examples()[9]["request"])
        outer = plan["variables"][0]["lean"]
        bound = next(b["lean"] for b in plan["bindings"] if b["scope"] == "interval-integral")
        self.assertNotEqual(outer, bound)
        self.assertIn("in (" + outer + ")..", plan["formal_statement"])
        self.assertIn("∫ (" + bound + " : ℝ)", plan["formal_statement"])
        request = deepcopy(examples()[3]["request"])
        request["goal"] = op("eq", integral(v("t"), v("a"), v("b")), v("t"))
        with self.assertRaisesRegex(ValueError, "Unbound"):
            compile_request(request)
        request["goal"] = op("eq", integral(v("t"), v("t"), v("b")), r(0))
        with self.assertRaisesRegex(ValueError, "Unbound"):
            compile_request(request)

    def test_ae_scope_and_pointwise_quantification_are_distinct(self):
        plan = compile_request(examples()[-1]["request"])
        self.assertIn("∀ᵐ", plan["conditions"][0]["formal_statement"])
        self.assertIn("(∀ (", plan["formal_statement"])
        request = deepcopy(examples()[-1]["request"])
        request["goal"] = op("eq", v("t"), r(0))
        with self.assertRaisesRegex(ValueError, "Unbound"):
            compile_request(request)

    def test_calculus_rejects_type_coercions_and_hidden_measure(self):
        for operand in (r(0), {"volume": 1}, {"zero": "Real"}):
            request = deepcopy(examples()[3]["request"])
            request["goal"]["eq"][0]["interval_integral"]["measure"] = operand
            with self.assertRaises(ValueError):
                compile_request(request)
        request = deepcopy(examples()[2]["request"])
        request["spaces"] = [{"name": "E", "structure": "real-inner-product"}]
        request["variables"][0]["type"] = {"function": ["Real", {"space": "E"}]}
        with self.assertRaises(ValueError):
            compile_request(request)
        request = deepcopy(examples()[3]["request"])
        del request["goal"]["eq"][0]["interval_integral"]["measure"]
        with self.assertRaises(ValueError):
            compile_request(request)

    def test_total_operators_never_add_existence_assumptions(self):
        request = deepcopy(examples()[11]["request"])
        request["assumptions"] = []
        request["proof"] = {"tactic": "simp"}
        plan = compile_request(request)
        self.assertEqual(plan["conditions"], [])
        self.assertIn("deriv", plan["formal_statement"])
        self.assertNotIn("HasDerivAt", plan["formal_statement"])
        request = deepcopy(examples()[4]["request"])
        request["assumptions"] = []
        request["proof"] = {"tactic": "simp"}
        plan = compile_request(request)
        self.assertEqual(plan["conditions"], [])
        self.assertNotIn("IntervalIntegrable", plan["formal_statement"])

    def test_unused_assumption_and_nested_lambda_stay_explicit(self):
        request = deepcopy(examples()[9]["request"])
        request["assumptions"] = [{"name": "unused", "formula": op("le", v("t"), r(0))}]
        request["goal"]["eq"][0]["interval_integral"]["body"] = op(
            "apply", lam("t", v("t")), v("c")
        )
        plan = compile_request(request)
        self.assertEqual(plan["conditions"][0]["name"], "unused")
        self.assertEqual(len({b["lean"] for b in plan["bindings"]}), len(plan["bindings"]))
        self.assertIn("mraHyp0", plan["formal_statement"])

    def test_full_candidate_and_replay_templates_keep_imports_first(self):
        for entry in examples():
            plan = self.store.preview(entry["request"])
            for replay in (False, True):
                source = lab.render(self.root, plan, replay=replay)
                self.assertIn(plan["formal_statement"], source)
                lines = source.splitlines()
                self.assertLess(
                    max(i for i, line in enumerate(lines) if line.startswith("import ")),
                    min(i for i, line in enumerate(lines) if line.startswith("open ")),
                )
                for name in foundation.IMPORTS:
                    self.assertIn("import " + name, source)

    def test_diagnostics_keep_initial_errors_before_large_export(self):
        value = lab.diagnostic(
            "Candidate.lean:10:2: error: unsolved goals\n⊢ HasDerivAt f df x\nMRA_AUDIT:{}\nMRA_EXPORT_BEGIN\n"
            + "x" * 20000,
            "",
            "candidate",
        )
        self.assertEqual(value["kind"], "UNSOLVED_GOALS")
        self.assertIn("HasDerivAt", value["text"])
        self.assertNotIn("MRA_EXPORT", value["text"])
        self.assertFalse(value["mathematical_counterexample"])
        application = lab.diagnostic(
            "error: Application type mismatch: hypothesis type differs", "", "candidate"
        )
        self.assertEqual(application["kind"], "LEAN_DIAGNOSTIC")
        self.assertIn("Application type mismatch:", application["text"])
        self.assertFalse(application["mathematical_counterexample"])

    def test_catalog_mutation_does_not_change_compiler_or_old_language(self):
        from mra import analysis_language

        first = catalog()
        first["proof_rules"]["has_deriv_add"]["proofs"] = 0
        first["terms"].append("evil")
        self.assertEqual(catalog()["proof_rules"]["has_deriv_add"]["proofs"], 2)
        self.assertNotIn("evil", catalog()["terms"])
        self.assertNotIn("interval_integral", analysis_language.catalog()["terms"])
        compiler = Compiler([])
        with self.assertRaises(ValueError):
            compiler.typ("Complex")
        for value in (True, 0, 41):
            request = deepcopy(examples()[0]["request"])
            request["timeout_seconds"] = value
            with self.assertRaises(ValueError):
                compile_request(request)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "opt-in real Lean")
class RealTests(unittest.TestCase):
    def test_typed_calculus_and_actual_lifecycle_controls(self):
        resume = os.environ.get("MRA_CALCULUS_RESUME")
        out = ROOT / (
            resume or ("artifacts/calculus-language-20261029-01/real-" + uuid.uuid4().hex)
        )
        if not resume:
            out.mkdir()
        cases = examples()
        declared = {
            "cases": cases,
            "attempts_per_case": 1,
            "shared_worker_seconds": 40,
            "extra_case": "cancel-after-real-replay",
            "extra_replay_controls": ["wrong-host-target", "malformed-proof-data"],
            "restoration_controls": [
                "actual proof bytes",
                "actual worker accounting ledger row",
                "actual original integrability condition ledger row",
            ],
            "source_exposure": "repository-known; not blind",
            "scope": "structured calculus language and execution, not PDE or novelty",
        }
        if resume:
            self.assertEqual((out / "declared-plan.json").read_bytes(), canonical(declared) + b"\n")
        else:
            (out / "declared-plan.json").write_bytes(canonical(declared) + b"\n")
        cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[
            -1
        ].lstrip("/")
        resources = {n: (cgroup / n).read_text().strip() for n in ("memory.max", "memory.swap.max")}
        self.assertEqual(
            resources, {"memory.max": str(6 * 1024**3), "memory.swap.max": str(512 * 1024**2)}
        )
        if resume:
            self.assertEqual((out / "resources.json").read_bytes(), canonical(resources) + b"\n")
        else:
            (out / "resources.json").write_bytes(canonical(resources) + b"\n")
        store = CalculusStore(ROOT, base=out / "calculus-language-private")
        legacy = ContentSnapshot.load_runtime(ROOT)
        legacy.verify()
        foundation.load(ROOT)

        def submit(request, suffix):
            plan = store.preview(request)
            return store.submit(
                {
                    "request_id": "real-" + suffix,
                    "request": request,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "Codex local full calculus statement review",
                    "confirmed": True,
                }
            )

        records = []
        interrupted = []
        pending = cases
        if resume:
            import json

            expected_area = (
                "artifacts/calculus-language-20261029-01/real-aebc69b293c74bc89c880f3c7f553d09"
            )
            self.assertEqual(resume, expected_area)
            preserved = out.parent / "interruption-before-continuation"
            observation = json.loads((preserved / "observation.json").read_text())
            self.assertFalse((out / "summary.json").exists())
            self.assertFalse((out / "controls.json").exists())
            self.assertFalse((out / "restoration-controls.json").exists())
            for name, expected_hash in observation["sources"].items():
                if name in TRUST_FILES or name == "mra/calculus_examples.py":
                    self.assertEqual(file_hash(ROOT / name), expected_hash)
            original_bytes = (out / "results-before-continuation.json").read_bytes()
            self.assertEqual((out / "results.json").read_bytes(), original_bytes)
            self.assertEqual((preserved / "results.json").read_bytes(), original_bytes)
            records = json.loads(original_bytes)
            self.assertEqual(len(records), 11)
            self.assertEqual([e["id"] for e in records], [e["id"] for e in cases[:11]])
            for entry, case in zip(records, cases[:11], strict=True):
                row = entry["check"]
                self.assertEqual(entry["expected"], case["expected"])
                self.assertEqual(row, store.read(row["run_id"]))
                self.assertEqual(row["plan"], store.preview(case["request"]))
                self.assertIsNone(store.gate(row))
            self.assertEqual(records[-1]["check"]["state"], "TIMEOUT")
            self.assertEqual(records[-1]["check"]["math_verdict"], "UNKNOWN")
            attempt = json.loads((out / "interrupted-attempt.json").read_text())
            row = attempt["check"]
            self.assertEqual(attempt["id"], cases[11]["id"])
            self.assertEqual(row, store.read(row["run_id"]))
            self.assertEqual(row["plan"], store.preview(cases[11]["request"]))
            self.assertEqual(row["state"], "INTERRUPTED")
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertIsNone(row["result"])
            self.assertEqual(
                sorted(p.name for p in store.directory(row["run_id"]).iterdir()), ["plan.json"]
            )
            self.assertEqual(
                (store.directory(row["run_id"]) / "plan.json").read_bytes(),
                canonical(row["plan"]) + b"\n",
            )
            with store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 12)
            for name, expected_hash in observation["artifacts"].items():
                self.assertEqual(file_hash(out / name), expected_hash)
            interrupted = [attempt]
            pending = cases[12:]
        for entry in pending:
            queued = submit(entry["request"], entry["id"])
            store.work_once(queued["run_id"])
            row = store.read(queued["run_id"])
            records.append({"id": entry["id"], "expected": entry["expected"], "check": row})
            (out / "results.json").write_bytes(canonical(records) + b"\n")
            with self.subTest(case=entry["id"]):
                self.assertIn(row["state"], (entry["expected"], "TIMEOUT"))
                self.assertEqual(
                    row["math_verdict"], "PROVED" if row["state"] == "COMPLETED" else "UNKNOWN"
                )
                if entry["expected"] == "REJECTED":
                    self.assertIsNone(row["result"]["replay"])
                    if row["state"] == "REJECTED":
                        self.assertIn(
                            row["result"]["diagnostic"]["kind"],
                            ("TYPE_MISMATCH", "UNSOLVED_GOALS", "LEAN_DIAGNOSTIC"),
                        )
                        if row["result"]["diagnostic"]["kind"] == "LEAN_DIAGNOSTIC":
                            self.assertIn(
                                entry["id"],
                                ("missing-derivative-condition", "missing-integrability"),
                            )
                            self.assertIn(
                                "Application type mismatch:", row["result"]["diagnostic"]["text"]
                            )
                        self.assertTrue(row["result"]["diagnostic"]["text"])
                        self.assertNotIn("MRA_EXPORT_BEGIN", row["result"]["diagnostic"]["text"])
        cancelled = submit(cases[3]["request"], "cancel-after-real-replay")
        original_worker = foundation._Runner._run_worker

        def cancel_after_real_replay(runner, *args, **kwargs):
            outcome = original_worker(runner, *args, **kwargs)
            if (
                kwargs["phase"] == "REPLAY_RUNNING"
                and outcome["run_status"] == "COMPLETED"
                and outcome["returncode"] == 0
            ):
                store.cancel(cancelled["run_id"])
            return outcome

        with patch.object(foundation._Runner, "_run_worker", cancel_after_real_replay):
            store.work_once(cancelled["run_id"])
            cancelled_row = store.read(cancelled["run_id"])
        records.append(
            {"id": "cancel-after-real-replay", "expected": "CANCELLED", "check": cancelled_row}
        )
        (out / "results.json").write_bytes(canonical(records) + b"\n")
        self.assertEqual(cancelled_row["state"], "CANCELLED")
        self.assertEqual(cancelled_row["math_verdict"], "UNKNOWN")
        self.assertEqual(cancelled_row["result"]["replay"]["returncode"], 0)
        first = records[0]["check"]
        first_dir = store.directory(first["run_id"])
        controls = []
        with store.tasks.worker_lock() as fd:
            for control in declared["extra_replay_controls"]:
                work = out / control
                work.mkdir()
                plan = (
                    records[3]["check"]["plan"] if control == "wrong-host-target" else first["plan"]
                )
                (work / "Candidate.lean").write_text(lab.render(ROOT, plan, replay=True))
                (work / "policy.json").write_bytes(canonical(lab.POLICY) + b"\n")
                (work / "proof.ndjson").write_bytes(
                    (first_dir / "replay/proof.ndjson").read_bytes()
                    if control == "wrong-host-target"
                    else b'{"bad":true}\n'
                )
                execution = foundation._Runner(ROOT)._run_worker(
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
        restorations = []
        proof_file = first_dir / "replay/proof.ndjson"
        saved = proof_file.read_bytes()
        (out / "proof-original.ndjson").write_bytes(saved)
        proof_file.write_bytes(saved + b" ")
        bad = store.report(first["run_id"])
        proof_file.write_bytes(saved)
        restored = store.report(first["run_id"])
        self.assertTrue(bad["invalidated"] and restored["invalidated"])
        self.assertEqual(restored["math_verdict"], "UNKNOWN")
        self.assertEqual(file_hash(proof_file), foundation.digest_bytes(saved))
        restorations.append(
            {"control": "proof", "run_id": first["run_id"], "mutated": bad, "restored": restored}
        )
        for index, control in ((2, "worker-accounting"), (4, "original-integrability-condition")):
            original = deepcopy(records[index]["check"])
            self.assertEqual(original["state"], "COMPLETED")
            (out / (control + "-original-row.json")).write_bytes(canonical(original) + b"\n")
            changed = deepcopy(original)
            if control == "worker-accounting":
                changed["result"]["worker_seconds"] += 1
            else:
                changed["plan"]["request"]["assumptions"] = changed["plan"]["request"][
                    "assumptions"
                ][:1]
            with store.transaction() as db:
                store.save(db, changed)
            bad = store.report(original["run_id"])
            self.assertTrue(bad["invalidated"])
            self.assertEqual(bad["math_verdict"], "UNKNOWN")
            with store.transaction() as db:
                store.save(db, original)
            restored = store.report(original["run_id"])
            self.assertTrue(restored["invalidated"])
            self.assertEqual(restored["math_verdict"], "UNKNOWN")
            restorations.append(
                {
                    "control": control,
                    "run_id": original["run_id"],
                    "mutated": bad,
                    "restored": restored,
                }
            )
        (out / "restoration-controls.json").write_bytes(canonical(restorations) + b"\n")
        current = [store.report(e["check"]["run_id"]) for e in records]
        legacy.verify()
        foundation.load(ROOT)
        summary = {
            "all_passed": all(e["check"]["state"] in (e["expected"], "TIMEOUT") for e in records),
            "store": str(store.base.relative_to(ROOT)),
            "checks": len(records),
            "declared_cases": len(cases),
            "positive_expected": sum(e["expected"] == "COMPLETED" for e in cases),
            "interrupted_attempts": interrupted,
            "resumed": bool(resume),
            "original_exit_receipt_available": not bool(resume),
            "positive_completions": sum(e["check"]["state"] == "COMPLETED" for e in records),
            "current_e4": sum(e["math_verdict"] == "PROVED" for e in current),
            "unresolved": [e["id"] for e in records if e["check"]["state"] == "TIMEOUT"]
            + [e["id"] for e in interrupted],
            "controls": controls,
            "restorations": restorations,
            "current": current,
            "model_calls": 0,
            "legacy_runtime_preserved": True,
        }
        (out / "summary.json").write_bytes(canonical(summary) + b"\n")
