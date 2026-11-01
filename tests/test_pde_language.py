"""Syntax/ledger contracts only; no mocked checker result is treated as mathematics."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mra import pde_language_runtime as runtime
from mra.domain import digest
from mra.analysis_examples import op, r, rule, v
from mra.calculus_examples import binding, integral
from mra.pde_language import MODE, catalog, compile_request
from mra.pde_language_examples import EXAMPLES, example
from mra.pde_language_lab import (
    PDELanguageStore,
    import_audits,
    render,
    run_identifier,
    source_use,
)
from mra.integrity import IntegrityError

ROOT = Path(__file__).resolve().parents[1]


def simple():
    return {
        "schema": MODE,
        "title": "explicit scalar identity",
        "original": "每个实数等于自身。",
        "spaces": [],
        "variables": [binding("x")],
        "assumptions": [],
        "goal": op("eq", v("x"), v("x")),
        "sources": [],
        "proof": {"term": rule("eq_refl", [v("x")])},
        "timeout_seconds": 40,
    }


class CompilerTests(unittest.TestCase):
    def test_known_examples_compile_only_as_candidates(self):
        for name in EXAMPLES:
            with self.subTest(name=name):
                raw = example(name)
                plan = compile_request(raw)
                self.assertEqual(plan["request"], raw)
                self.assertEqual((plan["math_verdict"], plan["evidence_level"]), ("UNKNOWN", "E0"))
                self.assertIs(plan["source_authorization_checked"], False)
                if raw["sources"]:
                    self.assertIn("Set.uIcc", plan["formal_statement"])
                    self.assertIn("MraCandidate.Reused.s0.checked", plan["proof_source"])
                self.assertEqual(len(plan["conditions"]), len(raw["assumptions"]))

    def test_unused_original_conditions_are_not_removed(self):
        raw = simple()
        raw["assumptions"] = [{"name": "unused", "formula": op("lt", r(0), v("x"))}]
        self.assertEqual(len(compile_request(raw)["conditions"]), 1)

    def test_interval_bounds_use_outer_scope_and_distinct_binder(self):
        raw = simple()
        term = integral(v("x"), v("x"), r(2), name="x")
        raw["goal"] = op("eq", term, term)
        raw["proof"] = {"tactic": "rfl"}
        plan = compile_request(raw)
        self.assertIn("in (mraBound0)..", plan["formal_statement"])
        names = [b["lean"] for b in plan["bindings"]]
        self.assertEqual(len(names), len(set(names)))

    def test_missing_condition_is_not_inserted(self):
        raw = example("integration-by-parts")
        raw["assumptions"].pop()
        raw["proof"]["term"]["source_apply"]["proofs"].pop()
        self.assertEqual(len(compile_request(raw)["conditions"]), 3)

    def test_measure_remains_distinct(self):
        raw = example("integration-by-parts")
        raw["variables"].append(binding("mu", "MeasureReal"))
        raw["goal"]["eq"][0]["interval_integral"]["measure"] = v("mu")
        plan = compile_request(raw)
        self.assertIn("Measure ℝ", plan["formal_statement"])
        self.assertEqual(plan["request"]["goal"], raw["goal"])

    def test_wrong_shapes_raw_lean_and_unbound_sources_are_rejected(self):
        edits = [
            lambda q: q.update(schema="calculus-language-v1"),
            lambda q: q.update(axioms=["False"]),
            lambda q: q.update(proof={"term": {"lean": "by sorry"}}),
            lambda q: q["sources"][0].update(run_id="pde-" + "a" * 32),
            lambda q: q["sources"][0].update(type_fingerprint="True"),
            lambda q: q["sources"][0].update(statement="True"),
            lambda q: q["sources"].append(deepcopy(q["sources"][0])),
            lambda q: q.update(proof={"tactic": "rfl"}),
            lambda q: q["proof"]["term"]["source_apply"].update(source="missing"),
            lambda q: q["proof"]["term"]["source_apply"]["terms"].append(v("missing")),
            lambda q: q["proof"]["term"]["source_apply"]["proofs"].append({"hyp": "missing"}),
            lambda q: q.update(spaces=[{"name": "E", "structure": "real-inner-product"}]),
            lambda q: q.update(timeout_seconds=True),
        ]
        for index, edit in enumerate(edits):
            with self.subTest(index=index):
                raw = example("integration-by-parts")
                edit(raw)
                with self.assertRaises(ValueError):
                    compile_request(raw)

    def test_rewrite_has_explicit_direction_and_no_source_text(self):
        raw = example("zero-boundary-green")
        raw["proof"]["term"]["rewrite"]["equalities"][0]["reverse"] = 1
        with self.assertRaises(ValueError):
            compile_request(raw)
        raw["proof"]["term"]["rewrite"]["equalities"][0]["reverse"] = False
        raw["proof"]["term"]["rewrite"]["equalities"][0]["proof"] = {"lean": "False.elim ?_"}
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_closed_membership_requires_real_operand(self):
        raw = example("integration-by-parts")
        raw["assumptions"][0]["formula"]["forall"]["body"]["imp"][0]["in_uIcc"]["term"] = v("u")
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_congruence_membership_name_cannot_capture_a_variable(self):
        raw = simple()
        raw["proof"] = {
            "term": {
                "interval_congr": {
                    "name": "t",
                    "membership": "x",
                    "lower": r(0),
                    "upper": r(1),
                    "measure": {"volume": True},
                    "proof": rule("eq_refl", [v("t")]),
                }
            }
        }
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_source_ids_and_proof_budgets_are_bounded(self):
        raw = example("integration-by-parts")
        raw["sources"] = [deepcopy(raw["sources"][0]) for _ in range(5)]
        with self.assertRaises(ValueError):
            compile_request(raw)
        raw = simple()
        for _ in range(40):
            raw["proof"]["term"] = {
                "rule": {"name": "eq_symm", "terms": [], "proofs": [raw["proof"]["term"]]}
            }
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_catalog_and_unbound_example_do_not_claim_authorization(self):
        self.assertEqual(catalog()["mode"], MODE)
        self.assertIs(catalog()["source_authorization_checked"], False)
        self.assertEqual(catalog()["limits"]["spaces"], 0)

    def test_render_has_imports_before_scopes_and_exact_source_types(self):
        plan = compile_request(example("zero-boundary-green"))
        plan["resolved_sources"] = [{**plan["sources"][0], "statement": "∀ x : ℝ, x = x"}]
        for replay in (False, True):
            source = render(ROOT, plan, replay=replay)
            lines = source.splitlines()
            self.assertLess(
                max(i for i, line in enumerate(lines) if line.startswith("import ")),
                min(i for i, line in enumerate(lines) if line.startswith("open ")),
            )
            self.assertIn(plan["formal_statement"], source)
            if not replay:
                self.assertIn("MRA_IMPORT_TARGET_MISMATCH", source)
                self.assertIn("MRA_SOURCE_NOT_USED", source)


class AuditTests(unittest.TestCase):
    def test_every_exact_slot_must_be_checked_once(self):
        sources = [{"slot": 0}, {"slot": 1}]
        rows = [
            {
                "slot": i,
                "type_matches": True,
                "candidate_module_imported": False,
                "replayed_declarations": 1,
                "axioms": ["propext"],
            }
            for i in range(2)
        ]
        output = "\n".join("MRA_IMPORT:" + json.dumps(v) for v in reversed(rows))
        self.assertEqual(import_audits(output, sources), rows)
        for bad in (
            [rows[0]],
            rows + [rows[0]],
            [{**rows[0], "slot": True}, rows[1]],
            [{**rows[0], "axioms": ["sorryAx"]}, rows[1]],
        ):
            with self.subTest(bad=bad), self.assertRaises(IntegrityError):
                import_audits("\n".join("MRA_IMPORT:" + json.dumps(v) for v in bad), sources)

    def test_all_claimed_sources_must_occur_in_final_kernel_term(self):
        sources = [{"slot": 0}, {"slot": 1}]
        used = ["MraCandidate.Reused.s0.checked", "MraCandidate.Reused.s1.checked"]
        self.assertEqual(source_use("MRA_SOURCE_USE:" + json.dumps(used), sources), used)
        with self.assertRaises(IntegrityError):
            source_use("MRA_SOURCE_USE:" + json.dumps(used[:1]), sources)
        with self.assertRaises(IntegrityError):
            source_use("MRA_SOURCE_USE:" + json.dumps(used), [])

    def test_legacy_mathlib_and_malformed_run_ids_are_rejected(self):
        for raw in ("mathlib-" + "a" * 32, "pdelang-../", "pdelang-" + "0" * 31, None):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                run_identifier(raw)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        area = ROOT / "artifacts/pde-language-20261029-01"
        self.temp = tempfile.TemporaryDirectory(prefix="contract-", dir=area)
        self.base = Path(self.temp.name) / "pde-language-private"
        self.manifest = patch(
            "mra.pde_language_lab.manifest", return_value={"mode": MODE, "contract_fixture": True}
        )
        self.manifest.start()
        self.store = PDELanguageStore(ROOT, base=self.base)

    def tearDown(self):
        self.manifest.stop()
        self.temp.cleanup()

    def submit(self, request_id="contract"):
        raw = simple()
        plan = self.store.preview(raw)
        return self.store.submit(
            {
                "request_id": request_id,
                "request": raw,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "protocol fixture",
                "confirmed": True,
            }
        )

    def test_preview_and_submission_stay_unknown_without_running_a_checker(self):
        row = self.submit()
        self.assertTrue(row["run_id"].startswith("pdelang-"))
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]), ("QUEUED", "UNKNOWN", "E0")
        )
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")
        self.assertFalse(self.store.directory(row["run_id"]).exists())

    def test_idempotence_and_changed_confirmation(self):
        first = self.submit()
        self.assertEqual(self.submit()["run_id"], first["run_id"])
        raw = simple()
        with self.assertRaises(ValueError):
            self.store.submit(
                {
                    "request_id": "contract",
                    "request": raw,
                    "plan_fingerprint": self.store.preview(raw)["plan_fingerprint"],
                    "reviewer": "another reviewer",
                    "confirmed": True,
                }
            )

    def test_cancel_queued_run_never_promotes(self):
        row = self.submit()
        value = self.store.cancel(row["run_id"])
        self.assertEqual((value["state"], value["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")

    def test_revoke_and_restoration_cannot_revive_confirmation(self):
        row = self.submit()
        self.store.revoke(
            row["run_id"], {"reviewer": "contract", "reason": "test", "confirmed": True}
        )
        with self.store.transaction() as db:
            self.store.save(db, row)
        value = self.store.report(row["run_id"])
        self.assertTrue(value["invalidated"])
        self.assertEqual(value["evidence_level"], "E0")

    def test_changed_full_target_is_permanently_invalidated(self):
        row = self.submit()
        before = deepcopy(row)
        row["plan"]["request"]["goal"] = op("eq", v("x"), r(0))
        with self.store.transaction() as db:
            self.store.save(db, row)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        with self.store.transaction() as db:
            self.store.save(db, before)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_interrupted_run_is_not_automatically_retried(self):
        row = self.submit()
        row["state"] = "RUNNING"
        with self.store.transaction() as db:
            self.store.save(db, row)
        result = self.store.recover()
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(result["interrupted"], [row["run_id"]])
        self.assertEqual(self.store.report(row["run_id"])["state"], "INTERRUPTED")
        self.assertFalse(self.store.directory(row["run_id"]).exists())

    def test_foreign_ledger_is_not_adopted(self):
        wrong = Path(self.temp.name) / "other" / "pde-language-private"
        wrong.mkdir(parents=True)
        with sqlite3.connect(wrong / "lab.sqlite3") as db:
            db.execute("CREATE TABLE foreign_table (v TEXT)")
        with self.assertRaises(ValueError):
            PDELanguageStore(ROOT, base=wrong)

    def test_corrupt_row_digest_is_rejected_and_gets_permanent_marker(self):
        row = self.submit()
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET sha256='bad' WHERE id=?", (row["run_id"],))
        with self.assertRaises(ValueError):
            self.store.report(row["run_id"])
        self.assertTrue(self.store.marker(row["run_id"]).exists())

    def test_run_with_missing_proof_files_cannot_claim_completion(self):
        row = self.submit()
        row.update(state="COMPLETED", math_verdict="PROVED", evidence_level="E4")
        with self.store.transaction() as db:
            self.store.save(db, row)
        value = self.store.report(row["run_id"])
        self.assertTrue(value["invalidated"])
        self.assertEqual(value["math_verdict"], "UNKNOWN")

    def test_queued_source_stamp_cannot_authorize_a_new_result(self):
        row = self.submit()
        with self.store.transaction(False) as db:
            self.assertFalse(
                self.store.source_stamps_match(
                    [
                        {
                            "provider": MODE,
                            "run_id": row["run_id"],
                            "stamp": {"row": digest(row)},
                        }
                    ],
                    db,
                )
            )

    def test_corrupt_cyclic_source_does_not_recurse_without_bound(self):
        row = self.submit()
        raw = simple()
        raw["sources"] = [
            {
                "name": "self",
                "run_id": row["run_id"],
                "type_fingerprint": digest(row["plan"]["formal_statement"]),
            }
        ]
        raw["proof"] = {
            "term": {
                "source_apply": {
                    "source": "self",
                    "terms": [v("x")],
                    "proofs": [],
                }
            }
        }
        row["plan"]["request"] = raw
        row.update(state="COMPLETED", math_verdict="PROVED", evidence_level="E4")
        with self.store.transaction() as db:
            self.store.save(db, row)
        value = self.store.report(row["run_id"])
        self.assertTrue(value["invalidated"])
        self.assertEqual(value["math_verdict"], "UNKNOWN")


class RuntimeTests(unittest.TestCase):
    def test_missing_or_malformed_metadata_fails_as_integrity_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts/pde-language-20261029-01") as temp:
            root = Path(temp)
            for name, value in (
                (runtime.BASE + "/adoption.json", []),
                (runtime.BASE + "/acquisition.json", {}),
                (runtime.prior.SNAPSHOT, {"host_files": {}}),
                (runtime.prior.LOCK, {}),
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            with self.assertRaises(IntegrityError):
                runtime.context(root)
            with self.assertRaises(IntegrityError):
                runtime.identity(root)

    def test_project_escape_and_symlink_paths_are_rejected(self):
        with self.assertRaises(IntegrityError):
            runtime.safe(ROOT, "../escape")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts/pde-language-20261029-01") as temp:
            root = Path(temp)
            (root / "target").write_text("same content")
            (root / "alias").symlink_to(root / "target")
            with self.assertRaises(IntegrityError):
                runtime.safe(root, "alias")


if __name__ == "__main__":
    unittest.main()
