"""Inert protocol contracts and separately enabled genuine abstract/inductive checks."""

from copy import deepcopy
import hashlib
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
from mra.mathlib_lab import POLICY
from mra.structured_examples import examples
from mra.structured_proofs import compile_proof
from mra.structured_terms import compile_request
from mra.structures import FILES, StructureStore, catalog
from mra.tasks import TaskStore, WorkerBusy
from mra.typed_reuse import checked
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/structures-20261028-01"


def submit(store, request=None, **changes):
    raw = deepcopy(examples()[2] if request is None else request)
    return store.submit(
        {
            "request_id": "structure-" + uuid.uuid4().hex,
            "request": raw,
            "plan_fingerprint": store.preview(raw)["plan_fingerprint"],
            "reviewer": "MRA059 authored program alignment reviewer",
            "confirmed": True,
            **changes,
        }
    )


class CompilerTests(unittest.TestCase):
    def test_carriers_operations_full_laws_and_primitive_recursion_are_closed(self):
        values = examples()
        for raw in values:
            original = deepcopy(raw)
            value = compile_request(raw)
            self.assertEqual(raw, original)
            self.assertEqual(value["request"], original)
            self.assertEqual(len(value["assumptions"]), len(raw["assumptions"]))
            self.assertTrue(compile_proof(raw["proof"], value).startswith("by"))
            self.assertNotIn("axiom", value["formal_statement"])
        monoid = compile_request(values[2])
        self.assertIn("(mra_t0 : Type)", monoid["formal_statement"])
        self.assertIn("Nat.rec (motive := fun _ => mra_t0)", monoid["formal_statement"])
        self.assertNotIn("Comm", monoid["formal_statement"])
        self.assertEqual(len(monoid["operations"]), 2)
        self.assertEqual(monoid["fold_count"], 1)
        self.assertEqual(compile_request(values[1])["carriers"], {"g": "mra_t0", "h": "mra_t1"})

    def test_wrong_carrier_missing_arguments_and_implicit_arithmetic_are_rejected(self):
        raw = examples()[1]
        for goal in (
            {"eq": [{"var": "x"}, {"call": {"name": "map", "args": [{"var": "x"}]}}]},
            {"eq": [{"call": {"name": "map", "args": []}}, {"var": "x"}]},
            {"eq": [{"add": [{"var": "x"}, {"var": "x"}]}, {"var": "x"}]},
        ):
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                compile_request({**raw, "goal": goal})

    def test_definition_free_capture_forward_calls_and_fold_binder_shadowing_rejected(self):
        raw = examples()[2]
        for body in (
            {"var": "m"},
            {"call": {"name": "power", "args": [{"var": "x"}, {"var": "n"}]}},
            {
                "nat_fold": {
                    "index": {"int": 0},
                    "zero": {"call": {"name": "one", "args": []}},
                    "step": {"index": "k", "value": "acc", "body": {"var": "acc"}},
                }
            },
            {
                "nat_fold": {
                    "index": {"var": "n"},
                    "zero": {"call": {"name": "one", "args": []}},
                    "step": {"index": "x", "value": "acc", "body": {"var": "acc"}},
                }
            },
        ):
            bad = deepcopy(raw)
            bad["definitions"][0]["body"] = body
            with self.subTest(body=body), self.assertRaises(ValueError):
                compile_request(bad)

    def test_fold_step_type_and_quantifier_scope_are_checked(self):
        raw = examples()[2]
        bad = deepcopy(raw)
        bad["definitions"][0]["body"]["nat_fold"]["step"]["body"] = {"nat": 0}
        with self.assertRaises(ValueError):
            compile_request(bad)
        for goal in (
            {
                "forall": {
                    "binders": [{"name": "x", "type": "g"}],
                    "body": {"eq": [{"var": "x"}, {"var": "x"}]},
                }
            },
            {
                "forall": {
                    "binders": [{"name": "q", "type": "missing"}],
                    "body": {"eq": [{"var": "q"}, {"var": "q"}]},
                }
            },
        ):
            with self.assertRaises(ValueError):
                compile_request({**raw, "goal": goal})

    def test_nat_induction_generalization_and_ih_scope_are_explicit(self):
        raw = examples()[6]
        value = compile_request(raw)
        program = compile_proof(raw["proof"], value)
        self.assertIn("induction mra_v2 generalizing mra_v1", program)
        self.assertIn("| succ mra_index mra_ih", program)
        self.assertIn("rw [mra_ih]", program)
        for change in (
            {"variable": "x"},
            {"generalize": ["n"]},
            {"generalize": ["m", "m"]},
            {"generalize": ["missing"]},
            {
                "base": {
                    "method": "rewrite",
                    "steps": [{"rule": "@ih", "reverse": False}],
                    "finish": "rfl",
                }
            },
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_proof({**raw["proof"], **change}, value)

    def test_proof_injection_unknown_rules_and_direction_types_rejected(self):
        value = compile_request(examples()[0])
        for proof in (
            {"method": "run_tac", "source": "sorry"},
            {"method": "rfl", "extra": "sorry"},
            {
                "method": "rewrite",
                "steps": [{"rule": "missing", "reverse": False}],
                "finish": "rfl",
            },
            {"method": "rewrite", "steps": [{"rule": "assoc", "reverse": 1}], "finish": "rfl"},
            {"method": "rewrite", "steps": [], "finish": "grind"},
        ):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                compile_proof(proof, value)

    def test_exact_schema_duplicate_names_universe_and_numeric_cast_boundaries(self):
        raw = examples()[0]
        for change in (
            {"schema": "typed-definitions-v1"},
            {"imports": ["Mathlib"]},
            {"carriers": [{"name": "g", "original": "g"}] * 2},
            {"operations": [raw["operations"][0]] * 2},
            {"variables": [{"name": "x; sorry", "type": "g"}]},
            {"variables": [{"name": "x", "type": "Type 1"}]},
            {"timeout_seconds": True},
            {"timeout_seconds": 31},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_request({**raw, **change})
        bad = deepcopy(raw)
        bad["goal"] = {"eq": [{"cast": {"to": "Nat", "value": {"int": 1}}}, {"nat": 1}]}
        with self.assertRaises(ValueError):
            compile_request(bad)

    def test_input_limits_cycles_and_shared_example_objects_are_bounded(self):
        raw = examples()[0]
        nested = {"var": "x"}
        for _ in range(18):
            nested = {"call": {"name": "mul", "args": [nested, {"var": "x"}]}}
        with self.assertRaises(ValueError):
            compile_request({**raw, "goal": {"eq": [nested, {"var": "x"}]}})
        cycle = {}
        cycle["not"] = cycle
        with self.assertRaises(ValueError):
            compile_request({**raw, "goal": cycle})
        values = examples()
        values[0]["assumptions"].clear()
        self.assertEqual(len(values[2]["assumptions"]), 3)
        self.assertEqual(len(catalog()["examples"][0]["assumptions"]), 3)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_sources(self.root)
        for name in FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.store = StructureStore(self.root)

    def test_prepare_is_unknown_and_owned_policy_cannot_be_mutated(self):
        before = deepcopy(POLICY)
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            plan = self.store.preview(examples()[2])
        self.assertEqual(plan["math_verdict"], "UNKNOWN")
        self.assertEqual(plan["evidence_level"], "E0")
        plan["source_manifest"]["executor"]["policy"]["allowed_axioms"].append("sorryAx")
        self.assertEqual(POLICY, before)
        self.assertNotIn(
            "sorryAx",
            self.store.preview(examples()[2])["source_manifest"]["executor"]["policy"][
                "allowed_axioms"
            ],
        )

    def test_full_named_confirmation_and_idempotent_body_binding(self):
        for change in ({"confirmed": 1}, {"reviewer": " "}, {"plan_fingerprint": "0" * 64}):
            with self.assertRaises(ValueError):
                submit(self.store, **change)
        row = submit(self.store)
        raw = row["plan"]["request"]
        again = submit(self.store, raw, request_id=row["request_id"])
        self.assertEqual(row["run_id"], again["run_id"])
        changed = deepcopy(raw)
        changed["original"] += " "
        with self.assertRaises(ValueError):
            submit(self.store, changed, request_id=row["request_id"])

    def test_implementation_drift_and_restored_bytes_never_revive_evidence(self):
        row = submit(self.store)
        path = self.root / FILES[0]
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        damaged = self.store.report(row["run_id"])
        self.assertTrue(damaged["invalidated"])
        path.write_bytes(original)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")

    def test_inert_artifact_damage_restore_cancel_and_revoke_remain_unknown(self):
        row = submit(self.store)
        area = self.store.directory(row["run_id"])
        area.mkdir(parents=True)
        p = area / "inert-contract.txt"
        p.write_bytes(b"NONEXECUTABLE UNIT CONTRACT, NOT A PROOF")
        with self.store.transaction() as db:
            value = self.store.read(row["run_id"], db)
            value["files"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()}
            self.store.save(db, value)
        original = p.read_bytes()
        p.write_bytes(b"changed")
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        p.write_bytes(original)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        self.assertEqual(self.store.cancel(row["run_id"])["math_verdict"], "UNKNOWN")
        self.assertTrue(
            self.store.revoke(
                row["run_id"], {"reviewer": "QA", "reason": "inert fixture", "confirmed": True}
            )["invalidated"]
        )

    def test_shared_worker_lock_and_restart_never_execute_or_promote(self):
        row = submit(self.store)
        with TaskStore(self.root).worker_lock():
            with self.assertRaises(WorkerBusy):
                self.store.work_once(row["run_id"])
        with self.store.transaction() as db:
            value = self.store.read(row["run_id"], db)
            value["state"] = "RUNNING"
            self.store.save(db, value)
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            self.store.recover()
        self.assertEqual(self.store.report(row["run_id"])["state"], "INTERRUPTED")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")

    def test_outside_and_redirected_storage_are_rejected(self):
        with self.assertRaises(ValueError):
            StructureStore(self.root, base=self.root / "outside")
        link = self.root / "artifacts/redirected"
        link.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            StructureStore(self.root, base=link)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit new real structure and induction verification"
)
class RealTests(unittest.TestCase):
    def store(self, label):
        area = OUT / ("real-" + label + "-" + uuid.uuid4().hex)
        area.mkdir(parents=True)
        return area, StructureStore(ROOT, base=area / "checks")

    def test_abstract_carriers_homomorphism_folds_induction_and_generalization(self):
        area, store = self.store("positive")
        results = []
        for i in (0, 1, 2, 3, 6):
            row = submit(store, examples()[i])
            store.work_once(row["run_id"])
            value = store.report(row["run_id"])
            (area / (str(i) + ".json")).write_bytes(canonical(value) + b"\n")
            checked(value)
            self.assertEqual(value["plan"]["request"], examples()[i])
            self.assertEqual(value["result"]["candidate"]["returncode"], 0)
            results.append(value["run_id"])
        (area / "summary.json").write_bytes(
            canonical({"all_passed": True, "runs": results}) + b"\n"
        )

    def test_wrong_commutativity_and_invalid_base_or_step_are_unknown(self):
        area, store = self.store("negative")
        bad_base = deepcopy(examples()[2])
        bad_base["proof"]["base"] = {"method": "rfl"}
        for i, raw in enumerate((examples()[4], examples()[5], bad_base)):
            row = submit(store, raw)
            store.work_once(row["run_id"])
            value = store.report(row["run_id"])
            (area / (str(i) + ".json")).write_bytes(canonical(value) + b"\n")
            self.assertEqual(
                (value["state"], value["math_verdict"], value["evidence_level"]),
                ("REJECTED", "UNKNOWN", "E0"),
            )
            self.assertNotIn("unexpected", value["result"]["error"])
            self.assertIsNone(value["result"]["replay"])

    def test_real_replay_success_then_cancel_or_supervisor_exit_never_promotes(self):
        area, store = self.store("cancel")
        row = submit(store, examples()[2])
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            outcome = original(backend, *args, **kwargs)
            if kwargs["phase"] == "REPLAY_RUNNING" and outcome["returncode"] == 0:
                store.cancel(row["run_id"])
            return outcome

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            store.work_once(row["run_id"])
        value = store.report(row["run_id"])
        (area / "report.json").write_bytes(canonical(value) + b"\n")
        self.assertEqual((value["state"], value["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        self.assertEqual(value["result"]["replay"]["returncode"], 0)
        area, store = self.store("exit")
        row = submit(store, examples()[2])
        script = """import sys
from pathlib import Path
from unittest.mock import patch
from mra.structures import StructureStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
 result=original(self,*args,**kwargs)
 if kwargs['phase']=='REPLAY_RUNNING' and result['returncode']==0:sys.exit(73)
 return result
store=StructureStore(Path.cwd(),base=Path(sys.argv[1]))
with patch.object(NativeLeanBackend,'_run_worker',stop):store.work_once(sys.argv[2])
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(store.base), row["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        (area / "stdout.log").write_text(process.stdout)
        (area / "stderr.log").write_text(process.stderr)
        self.assertEqual(process.returncode, 73, process.stderr)
        store.recover()
        value = store.report(row["run_id"])
        (area / "report.json").write_bytes(canonical(value) + b"\n")
        self.assertEqual((value["state"], value["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        self.assertIsNone(value["result"])
