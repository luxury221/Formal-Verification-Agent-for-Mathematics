"""Type/scope boundaries, immutable confirmations, and actual closed-target kernel replay."""

from copy import deepcopy
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

from mra.tasks import TaskStore, WorkerBusy
from mra.typed_definitions import TypedDefinitionStore, catalog, examples
from mra.typed_terms import compile_request
from mra.verifier import NativeLeanBackend
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/typed-definitions-20261028-01"


def submit(store, value=None, request_id=None):
    value = deepcopy(examples()[1] if value is None else value)
    plan = store.preview(value)
    return store.submit(
        {
            "request_id": request_id or "typed-" + uuid.uuid4().hex,
            "request": value,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "Typed definition QA",
            "confirmed": True,
        }
    )


def isolated_store():
    """Real frozen executor, isolated ledger; no old production run or data is reused."""
    store = object.__new__(TypedDefinitionStore)
    store.root = ROOT
    store.base = OUT / ("checks-" + uuid.uuid4().hex)
    store.base.mkdir(parents=True)
    store.path = store.base / "lab.sqlite3"
    store.tasks = TaskStore(ROOT)
    with store.transaction() as db:
        db.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
        )
    return store


class CompilerTests(unittest.TestCase):
    def test_complete_context_types_original_and_all_conditions_survive(self):
        raw = examples()[0]
        raw["original"] += "  "
        value = compile_request(raw)
        self.assertEqual(value["request"], raw)
        self.assertTrue(value["formal_statement"].startswith("let mra_d0 : Nat → Nat → Nat"))
        self.assertIn("∀ (mra_v0 : Nat) (mra_v1 : Nat)", value["formal_statement"])
        self.assertEqual(len(value["assumptions"]), 1)
        self.assertIn("→", value["formal_statement"].split("∀")[1])
        self.assertEqual(compile_request(examples()[1])["variable_mapping"]["a"]["type"], "Int")

    def test_mixed_operands_and_narrowing_need_explicit_casts(self):
        n = {"var": "n"}
        base = {**examples()[3], "variables": [{"name": "n", "type": "Nat"}]}
        for expr in (
            {"add": [n, {"int": 0}]},
            {"cast": {"to": "Nat", "value": {"int": -1}}},
            {"cast": {"to": "Int", "value": {"rat": [1, 2]}}},
            {"neg": n},
            {"div": [n, {"nat": 2}]},
        ):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                compile_request({**base, "goal": {"eq": [expr, expr]}})
        for target in ("Int", "Rat"):
            expr = {"cast": {"to": target, "value": n}}
            self.assertIn(
                target, compile_request({**base, "goal": {"eq": [expr, expr]}})["formal_statement"]
            )

    def test_forward_self_mutual_recursion_global_capture_and_unknown_calls_blocked(self):
        raw = examples()[4]
        for definitions in (
            list(reversed(raw["definitions"])),
            [
                {
                    **raw["definitions"][0],
                    "body": {"call": {"name": "embed", "args": [{"var": "n"}]}},
                }
            ],
            [{**raw["definitions"][0], "body": {"var": "z"}}],
        ):
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                compile_request({**raw, "definitions": definitions})
        self.assertEqual(compile_request(raw)["definitions"][1]["depends_on"], ["embed"])

    def test_every_call_argument_is_explicit_and_typed(self):
        raw = examples()[1]
        for args in ([], [{"int": 0}], [{"int": 0}, {"nat": 1}], [{"int": 0}] * 3):
            value = deepcopy(raw)
            value["goal"] = {"eq": [{"call": {"name": "gap", "args": args}}, {"int": 0}]}
            with self.subTest(args=args), self.assertRaises(ValueError):
                compile_request(value)

    def test_locally_bound_parameters_do_not_capture_global_names(self):
        value = compile_request(examples()[1])
        self.assertIn("mra_p0_0", value["definitions"][0]["body"])
        self.assertNotIn("mra_v", value["definitions"][0]["body"])
        raw = examples()[1]
        raw["definitions"][0]["parameters"][1]["name"] = "a"
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_predicates_numeric_results_and_logical_positions_are_checked(self):
        value = compile_request(examples()[5])
        self.assertTrue(value["definitions"][0]["signature"].endswith("Prop"))
        for goal in (
            {"int": 1},
            {"and": [{"int": 1}, {"int": 1}]},
            {"eq": [{"eq": [{"int": 0}, {"int": 0}]}] * 2},
        ):
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                compile_request({**examples()[3], "goal": goal})
        raw = examples()[5]
        raw["definitions"][0]["result_type"] = "Rat"
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_quantifiers_are_hygienic_and_reject_shadowing_and_free_variables(self):
        body = {"eq": [{"var": "q"}, {"var": "q"}]}
        quantified = {"forall": {"binders": [{"name": "q", "type": "Rat"}], "body": body}}
        raw = {**examples()[3], "goal": quantified}
        self.assertIn("∀ (mra_q0 : Rat)", compile_request(raw)["formal_statement"])
        with self.assertRaises(ValueError):
            compile_request({**raw, "variables": [{"name": "q", "type": "Rat"}]})
        quantified["forall"]["body"] = {"eq": [{"var": "missing"}, {"var": "q"}]}
        with self.assertRaises(ValueError):
            compile_request(raw)

    def test_fraction_boundaries_zero_denominators_and_bool_literals_rejected(self):
        for fraction in ([1, 0], [1, -2], [True, 2], [1, True], [1000001, 1], [1, 1000001]):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                compile_request({**examples()[3], "goal": {"eq": [{"rat": fraction}] * 2}})
        for literal in ({"nat": -1}, {"int": True}, {"int": 1000001}):
            with self.assertRaises(ValueError):
                compile_request({**examples()[3], "goal": {"eq": [literal] * 2}})
        self.assertIn(
            "(-1 : Int)",
            compile_request({**examples()[3], "goal": {"eq": [{"rat": [-1, 2]}] * 2}})[
                "formal_statement"
            ],
        )

    def test_injection_missing_fields_duplicate_names_and_limits(self):
        for changes in (
            {"schema": "mathlib-nat-gcd-v1"},
            {"imports": ["Mathlib"]},
            {"variables": [{"name": "x; sorry", "type": "Rat"}]},
            {"variables": [{"name": "x", "type": "Real"}]},
            {"timeout_seconds": True},
            {"timeout_seconds": 31},
            {"assumptions": [{"name": "h", "formula": {"int": 1}}]},
            {"definitions": examples()[1]["definitions"] * 2},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compile_request({**examples()[3], **changes})
        expr = {"int": 0}
        for _ in range(18):
            expr = {"neg": expr}
        with self.assertRaises(ValueError):
            compile_request({**examples()[3], "goal": {"eq": [expr, {"int": 0}]}})
        cycle = {}
        cycle["cycle"] = cycle
        with self.assertRaises(ValueError):
            compile_request(cycle)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in ("typed_terms.py", "typed_definitions.py"):
            shutil.copyfile(ROOT / "mra" / name, self.root / "mra" / name)
        self.store = TypedDefinitionStore(self.root)

    def test_preview_never_executes_and_fingerprint_binds_exact_full_input(self):
        with patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No execution")
        ):
            raw = examples()[0]
            value = self.store.preview(raw)
            for mutate in (
                lambda r: r.update(original=r["original"] + " "),
                lambda r: r.update(assumptions=[]),
                lambda r: r["definitions"][0].update(original="更改定义说明"),
                lambda r: r.update(timeout_seconds=29),
            ):
                other = deepcopy(raw)
                mutate(other)
                self.assertNotEqual(
                    value["plan_fingerprint"], self.store.preview(other)["plan_fingerprint"]
                )
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(self.store.list_runs(), [])

    def test_only_fixed_strategies_and_catalog_is_not_mutable_authority(self):
        for value in ("native_decide", "grind; sorry", True, ["grind"]):
            with self.assertRaises(ValueError):
                self.store.preview({**examples()[3], "strategy": value})
        a = catalog()
        a["strategies"]["grind"]["proof_source"] = "by sorry"
        self.assertNotIn("sorry", catalog()["strategies"]["grind"]["proof_source"])

    def test_confirmation_is_named_idempotent_and_request_bound(self):
        value = examples()[1]
        plan = self.store.preview(value)
        data = {
            "request_id": "same",
            "request": value,
            "reviewer": "QA",
            "confirmed": True,
            "plan_fingerprint": plan["plan_fingerprint"],
        }
        for changes in ({"confirmed": 1}, {"reviewer": " "}, {"plan_fingerprint": "stale"}):
            with self.assertRaises(ValueError):
                self.store.submit({**data, **changes})
        first = self.store.submit(data)
        self.assertEqual(first, self.store.submit(data))
        with self.assertRaises(ValueError):
            self.store.submit({**data, "reviewer": "other"})

    def test_cancel_lock_recovery_and_revocation_do_not_run_or_promote(self):
        row = submit(self.store)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(row["run_id"])
        self.assertEqual(self.store.cancel(row["run_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")
        row = submit(self.store)
        with self.store.transaction() as db:
            value = self.store.read(row["run_id"], db)
            value.update(state="RUNNING", stage="REPLAY_RUNNING")
            self.store.save(db, value)
        self.assertIn(row["run_id"], self.store.recover()["interrupted"])
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")
        revoked = self.store.revoke(
            row["run_id"], {"reviewer": "QA", "reason": "withdraw", "confirmed": True}
        )
        self.assertTrue(revoked["invalidated"])

    def test_compiler_drift_is_persistent_after_bytes_restored(self):
        row = submit(self.store)
        path = self.root / "mra/typed_terms.py"
        before = path.read_bytes()
        path.write_bytes(before + b"\n")
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        path.write_bytes(before)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_storage_redirect_rejected(self):
        self.store.path.unlink()
        outside = self.root / "redirect"
        outside.touch()
        self.store.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.list_runs()


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit real sealed Lean")
class RealTests(unittest.TestCase):
    def setUp(self):
        self.store = isolated_store()

    def run_check(self, request):
        row = submit(self.store, request)
        self.store.work_once(row["run_id"])
        value = self.store.report(row["run_id"])
        (self.store.base / (row["run_id"] + "-report.json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
        return value

    def test_all_authored_examples_and_missing_condition_failures(self):
        for i, value in enumerate(examples()):
            with self.subTest(example=i):
                row = self.run_check(value)
                expected = ("COMPLETED", "PROVED", "E4") if i < 7 else ("REJECTED", "UNKNOWN", "E0")
                self.assertEqual(
                    (row["state"], row["math_verdict"], row["evidence_level"]),
                    expected,
                    row["result"],
                )
                self.assertEqual(row["plan"]["request"], value)
                if i < 7:
                    a, b = row["result"]["candidate"], row["result"]["replay"]
                    self.assertNotEqual(a["pid"], b["pid"])
                    self.assertTrue(a["audit"]["type_matches"])
                    self.assertTrue(b["audit"]["type_matches"])
                    self.assertFalse(b["audit"]["candidate_module_imported"])
                    self.assertNotIn("sorryAx", b["audit"]["axioms"])
                else:
                    self.assertIn(value["strategy"], row["result"]["error"])
                    self.assertNotIn("`dsimp` made no progress", row["result"]["error"])

    def test_negative_fraction_zero_division_quantifiers_and_parameter_hygiene(self):
        def rat(n, d=1):
            return {"rat": [n, d]}

        def eq(a, b):
            return {"eq": [a, b]}

        v = {"var": "q"}
        goal = {
            "and": [
                {
                    "forall": {
                        "binders": [{"name": "q", "type": "Rat"}],
                        "body": {
                            "exists": {
                                "binders": [{"name": "y", "type": "Rat"}],
                                "body": eq({"var": "y"}, v),
                            }
                        },
                    }
                },
                {
                    "and": [
                        eq({"add": [rat(-1, 2), rat(1, 2)]}, rat(0)),
                        eq({"div": [rat(3), rat(0)]}, rat(0)),
                    ]
                },
            ]
        }
        row = self.run_check(
            {
                **examples()[3],
                "goal": goal,
                "original": "对任意有理数q存在y=q；-1/2+1/2=0且3/0=0（Lean总除法）。",
            }
        )
        self.assertEqual(
            (row["math_verdict"], row["evidence_level"]), ("PROVED", "E4"), row["result"]
        )

    def test_rfl_checks_closed_function_definition_and_old_history_stays_unknown_after_damage(self):
        raw = examples()[4]
        call = {"call": {"name": "lift", "args": [{"var": "n"}]}}
        raw.update(strategy="rfl", goal={"eq": [call, call]}, original="lift(n)=lift(n)。")
        row = self.run_check(raw)
        self.assertEqual(row["math_verdict"], "PROVED", row["result"])
        path = self.store.directory(row["run_id"]) / "replay/proof.ndjson"
        before = path.read_bytes()
        path.write_bytes(before + b"\n")
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")
        path.write_bytes(before)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        revoked = self.run_check(examples()[1])
        result = self.store.revoke(
            revoked["run_id"], {"reviewer": "QA", "reason": "actual withdrawal", "confirmed": True}
        )
        self.assertEqual((result["math_verdict"], result["evidence_level"]), ("UNKNOWN", "E0"))
        (self.store.base / "invalidation.json").write_text(
            json.dumps({"damaged": self.store.report(row["run_id"]), "revoked": result}) + "\n"
        )

    def test_actual_replay_then_cancellation_cannot_publish_e4(self):
        row = submit(self.store)
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            result = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "REPLAY_RUNNING":
                self.assertEqual(result["returncode"], 0)
                self.store.cancel(row["run_id"])
            return result

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once(row["run_id"])
        result = self.store.report(row["run_id"])
        self.assertEqual((result["state"], result["math_verdict"]), ("CANCELLED", "UNKNOWN"))
        self.assertEqual(result["result"]["replay"]["returncode"], 0)
        (self.store.base / "cancelled.json").write_text(json.dumps(result) + "\n")

    def test_supervisor_exit_after_actual_replay_does_not_promote_or_retry(self):
        row = submit(self.store)
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.typed_definitions import TypedDefinitionStore
from mra.tasks import TaskStore
from mra.verifier import NativeLeanBackend
s=object.__new__(TypedDefinitionStore);s.root=Path.cwd();s.base=Path(sys.argv[1]);s.path=s.base/'lab.sqlite3';s.tasks=TaskStore(s.root)
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
 r=original(self,*args,**kwargs)
 if kwargs.get('phase')=='REPLAY_RUNNING':
  assert r['returncode']==0,r
  os._exit(73)
 return r
with patch.object(NativeLeanBackend,'_run_worker',stop):s.work_once(sys.argv[2])
"""
        value = subprocess.run(
            [sys.executable, "-c", script, str(self.store.base), row["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(value.returncode, 73, value.stderr)
        self.assertIn(row["run_id"], self.store.recover()["interrupted"])
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")
        result = self.store.report(row["run_id"])
        self.assertEqual((result["state"], result["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        self.assertTrue((self.store.directory(row["run_id"]) / "replay/stdout.log").is_file())
        (self.store.base / "interrupted.json").write_text(json.dumps(result) + "\n")
