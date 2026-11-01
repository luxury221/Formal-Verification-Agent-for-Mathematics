"""Gcd/coprime input gates and real frozen Lean/replay execution."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.mathlib_lab import MathlibLabStore
from mra.mathlib_terms import compile_request as legacy_compile
from mra.number_theory_lab import NumberTheoryStore, manifest
from mra.number_theory_terms import MODE, REFERENCES, compile_request, examples
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def isolated_store():
    root = ROOT / "artifacts/number-theory-tests" / uuid.uuid4().hex
    store = NumberTheoryStore(root)
    store.root = ROOT
    store.tasks = TaskStore(ROOT)
    return store


def submit(store, value=None, **changes):
    value = examples()[0] if value is None else value
    plan = store.preview(value)
    return store.submit(
        {
            "request_id": uuid.uuid4().hex,
            "request": value,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "Authored gcd integration reviewer",
            "confirmed": True,
            **changes,
        }
    )


class TermTests(unittest.TestCase):
    def test_six_authored_inputs_preserve_all_conditions_and_explicit_types(self):
        for value in examples():
            compiled = compile_request(value)
            self.assertEqual(compiled["request"]["assumptions"], value["assumptions"])
            self.assertIn("Nat", compiled["formal_statement"])
        self.assertEqual(compile_request(examples()[2])["formal_statement"].count(" → "), 2)
        self.assertIn("Nat.gcd", compile_request(examples()[0])["formal_statement"])
        self.assertIn("Nat.Coprime", compile_request(examples()[3])["formal_statement"])

    def test_legacy_schema_and_old_gcd_block_are_preserved(self):
        value = examples()[0]
        with self.assertRaises(ValueError):
            legacy_compile(value)
        value["schema"] = "mathlib-nat-divisibility-v1"
        with self.assertRaises(ValueError):
            legacy_compile(value)
        with self.assertRaises(ValueError):
            compile_request(value)

    def test_type_confusion_unknown_definitions_and_arbitrary_source_are_rejected(self):
        variants = [
            {"goal": {"eq": [{"coprime": [{"var": "a"}, {"var": "a"}]}, {"nat": 1}]}},
            {"goal": {"gcd": [{"var": "a"}, {"var": "a"}]}},
            {"goal": {"prime": [{"var": "a"}]}},
            {"proof": {"source": "by sorry"}},
            {"proof": {"ref": "sorryAx", "terms": [], "proofs": []}},
            {"variables": ["a) := by sorry"]},
            {"goal": {"eq": [{"nat": -1}, {"var": "a"}]}},
            {"goal": {"eq": [{"nat": True}, {"var": "a"}]}},
        ]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compile_request({**examples()[0], **changes})

    def test_gcd_arity_missing_hypotheses_and_reference_parameters_are_not_inferred(self):
        for changes in (
            {"goal": {"eq": [{"gcd": [{"var": "a"}]}, {"var": "a"}]}},
            {"proof": {"ref": "Nat.gcd_self", "terms": [], "proofs": []}},
            {"proof": {"hyp": "missing"}},
            {"drop_premises": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compile_request({**examples()[0], **changes})

    def test_expression_node_depth_and_worker_budgets_are_bounded(self):
        for budget in (True, 0, 31, float("nan"), float("inf")):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                compile_request({**examples()[0], "timeout_seconds": budget})
        expression = {"var": "a"}
        for _ in range(16):
            expression = {"gcd": [expression, {"var": "a"}]}
        with self.assertRaises(ValueError):
            compile_request({**examples()[0], "goal": {"eq": [expression, {"var": "a"}]}})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.store = isolated_store()

    def test_preview_and_submission_bind_new_and_frozen_protocols_without_lean(self):
        value = self.store.preview(examples()[0])
        self.assertEqual(value["source_manifest"]["mode"], MODE)
        self.assertEqual(value["source_manifest"], manifest(ROOT))
        self.assertEqual(value["budget"]["shared_worker_seconds"], 30)
        row = submit(self.store, request_id="idempotent")
        self.assertEqual(submit(self.store, request_id="idempotent"), row)
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]), ("QUEUED", "UNKNOWN", "E0")
        )
        self.assertFalse(self.store.directory(row["run_id"]).exists())

    def test_confirmation_and_stale_plan_do_not_create_runs(self):
        for changes in (
            {"confirmed": False},
            {"confirmed": 1},
            {"reviewer": " "},
            {"plan_fingerprint": "stale"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                submit(self.store, **changes)
        self.assertEqual(self.store.list_runs(), [])

    def test_cancel_revoke_and_restart_leave_unknown_without_retry(self):
        row = submit(self.store)
        self.assertEqual(self.store.cancel(row["run_id"])["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")
        other = submit(self.store)
        with self.store.transaction() as db:
            value = self.store.read(other["run_id"], db)
            value.update(state="RUNNING", stage="REPLAY_RUNNING")
            self.store.save(db, value)
        self.assertEqual(self.store.recover()["interrupted"], [other["run_id"]])
        self.assertEqual(self.store.report(other["run_id"])["math_verdict"], "UNKNOWN")
        revoked = self.store.revoke(
            row["run_id"], {"reviewer": "Author", "reason": "Recheck original", "confirmed": True}
        )
        self.assertTrue(revoked["invalidated"])

    def test_global_worker_lock_blocks_execution(self):
        row = submit(self.store)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(row["run_id"])

    def test_protocol_drift_is_persistent_and_paths_and_ledger_tampering_are_rejected(self):
        row = submit(self.store)
        with patch("mra.number_theory_lab.manifest", return_value={"changed": True}):
            self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        with self.assertRaises(ValueError):
            self.store.report("../outside")
        other = submit(self.store)
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET sha256='bad' WHERE id=?", (other["run_id"],))
        with self.assertRaises(ValueError):
            self.store.report(other["run_id"])

    def test_storage_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "artifacts").mkdir()
            (root / "target").mkdir()
            (root / "artifacts/number-theory").symlink_to(root / "target", target_is_directory=True)
            with self.assertRaises(ValueError):
                NumberTheoryStore(root)

    def test_cli_preview_and_old_dataset_cli_remain_distinct(self):
        path = self.store.base / "request.json"
        path.write_text(json.dumps(examples()[0]))
        for args in (
            ["number-theory-preview", "--input", str(path)],
            ["dataset"],
            ["mathlib-benchmark", "--plan"],
        ):
            result = subprocess.run(
                [sys.executable, "-m", "mra", *args],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.context = running_server(self.root)
        self.server = self.context.__enter__()
        self.server.number_theory = isolated_store()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_catalog_page_and_new_assets_keep_local_guards(self):
        for route in ("/number-theory", "/number-theory.js", "/v1/number-theory/catalog"):
            self.assertEqual(request(self.server, "GET", route)[0], 200)
        self.assertEqual(
            request(self.server, "GET", "/number-theory", headers={"Host": "evil.example"})[0], 403
        )
        catalog = request(self.server, "GET", "/v1/number-theory/catalog")[1]
        self.assertEqual(catalog["mode"], MODE)
        self.assertEqual(len(catalog["references"]), len(REFERENCES))

    def test_http_preview_confirm_cancel_and_revoke_use_independent_ledger(self):
        status, plan, _ = request(self.server, "POST", "/v1/number-theory/preview", examples()[2])
        self.assertEqual(status, 200)
        data = {
            "request_id": "http-gcd",
            "request": examples()[2],
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "HTTP author",
            "confirmed": True,
        }
        status, row, _ = request(self.server, "POST", "/v1/number-theory/runs", data)
        self.assertEqual(status, 201)
        endpoint = "/v1/number-theory/runs/" + row["run_id"]
        self.assertEqual(request(self.server, "GET", endpoint)[1]["math_verdict"], "UNKNOWN")
        self.assertEqual(
            request(self.server, "POST", endpoint + "/cancel", {})[1]["state"], "CANCELLED"
        )
        self.assertTrue(
            request(
                self.server,
                "POST",
                endpoint + "/revoke",
                {"reviewer": "Author", "reason": "Correct source", "confirmed": True},
            )[1]["invalidated"]
        )
        self.assertEqual(request(self.server, "GET", "/v1/mathlib/runs")[1]["runs"], [])

    def test_unsupported_input_and_csrf_cannot_queue_checks(self):
        self.assertEqual(
            request(
                self.server,
                "POST",
                "/v1/number-theory/preview",
                {**examples()[0], "proof": {"source": "by sorry"}},
            )[0],
            400,
        )
        self.assertEqual(
            request(
                self.server,
                "POST",
                "/v1/number-theory/preview",
                examples()[0],
                headers={"X-MRA-CSRF": "bad"},
            )[0],
            403,
        )
        self.assertEqual(request(self.server, "GET", "/v1/number-theory/runs")[1]["runs"], [])


def all_new_references_request():
    def v(name):
        return {"var": name}

    def f(op, a, b):
        return {op: [a, b]}

    def ref(name, args, proofs=None):
        return {"ref": name, "terms": args, "proofs": proofs or []}

    a, b, d = v("a"), v("b"), v("d")
    zero, one = {"nat": 0}, {"nat": 1}
    g = f("gcd", a, b)
    items = [
        (f("eq", f("gcd", a, a), a), ref("Nat.gcd_self", [a])),
        (f("eq", f("gcd", zero, a), a), ref("Nat.gcd_zero_left", [a])),
        (f("eq", f("gcd", a, zero), a), ref("Nat.gcd_zero_right", [a])),
        (f("dvd", g, a), ref("Nat.gcd_dvd_left", [a, b])),
        (f("dvd", g, b), ref("Nat.gcd_dvd_right", [a, b])),
        (f("dvd", d, g), ref("Nat.dvd_gcd", [d, a, b], [{"hyp": "ha"}, {"hyp": "hb"}])),
        (f("eq", g, f("gcd", b, a)), ref("Nat.gcd_comm", [a, b])),
        (f("iff", f("coprime", a, b), f("eq", g, one)), ref("Nat.coprime_iff_gcd_eq_one", [a, b])),
        (f("eq", g, one), ref("Nat.Coprime.gcd_eq_one", [a, b], [{"hyp": "hc"}])),
        (f("coprime", one, a), ref("Nat.coprime_one_left", [a])),
        (f("coprime", a, one), ref("Nat.coprime_one_right", [a])),
        (f("iff", f("coprime", zero, a), f("eq", a, one)), ref("Nat.coprime_zero_left", [a])),
        (f("iff", f("coprime", a, zero), f("eq", a, one)), ref("Nat.coprime_zero_right", [a])),
    ]

    def join(rows):
        if len(rows) == 1:
            return rows[0]
        middle = len(rows) // 2
        left, right = join(rows[:middle]), join(rows[middle:])
        return f("and", left[0], right[0]), {"pair": [left[1], right[1]]}

    goal, proof = join(items)
    return {
        **deepcopy(examples()[0]),
        "title": "All thirteen new library references with explicit parameters",
        "original": "作者功能检查：保留d整除a、d整除b以及a和b互素这三个完整前提，验证目录十三条新增引用的显式实参次序与完整结论。",
        "variables": ["a", "b", "d"],
        "assumptions": [
            {"name": "ha", "formula": f("dvd", d, a)},
            {"name": "hb", "formula": f("dvd", d, b)},
            {"name": "hc", "formula": f("coprime", a, b)},
        ],
        "goal": goal,
        "proof": proof,
    }


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "requires actual sealed Lean and independent replay"
)
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = isolated_store()
        cls.rows = []
        for value in examples():
            row = submit(cls.store, value)
            cls.store.work_once(row["run_id"])
            cls.rows.append(cls.store.report(row["run_id"]))
        reference = submit(cls.store, all_new_references_request())
        cls.store.work_once(reference["run_id"])
        cls.reference_row = cls.store.report(reference["run_id"])
        (ROOT / "artifacts/number-theory-real-examples.json").write_text(
            json.dumps(
                {
                    "source": "actual-Lean-and-independent-replay",
                    "store": str(cls.store.base.relative_to(ROOT)),
                    "results": cls.rows,
                    "all_new_references": cls.reference_row,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    def test_gcd_self_and_zero_boundary_are_real_e4(self):
        self.assertEqual(
            (self.rows[0]["state"], self.rows[0]["math_verdict"], self.rows[0]["evidence_level"]),
            ("COMPLETED", "PROVED", "E4"),
            self.rows[0]["result"],
        )
        self.assertEqual(self.rows[4]["math_verdict"], "PROVED", self.rows[4]["result"])

    def test_conjunction_and_both_premises_have_independent_actual_replay(self):
        for row in self.rows[1:3]:
            self.assertEqual(row["math_verdict"], "PROVED", row["result"])
            self.assertNotEqual(row["result"]["candidate"]["pid"], row["result"]["replay"]["pid"])
            self.assertTrue(row["result"]["candidate"]["audit"]["type_matches"])
            self.assertFalse(row["result"]["replay"]["audit"]["candidate_module_imported"])

    def test_coprime_definition_is_actual_kernel_checked(self):
        self.assertEqual(self.rows[3]["math_verdict"], "PROVED", self.rows[3]["result"])

    def test_all_thirteen_new_reference_types_and_argument_orders_are_real_e4(self):
        self.assertEqual(self.reference_row["math_verdict"], "PROVED", self.reference_row["result"])
        self.assertEqual(len(self.reference_row["plan"]["references"]), 13)

    def test_omitted_premise_is_real_rejection_and_unknown(self):
        row = self.rows[5]
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]),
            ("REJECTED", "UNKNOWN", "E0"),
            row["result"],
        )
        self.assertIsNotNone(row["result"]["candidate"])
        self.assertIsNone(row["result"]["replay"])
        self.assertIn("mra_h0", row["result"]["error"])

    def test_tampered_artifact_suppresses_real_proof_and_restoration_never_revives(self):
        row = self.rows[0]
        path = self.store.directory(row["run_id"]) / "candidate/stdout.log"
        raw = path.read_bytes()
        path.write_bytes(raw + b"changed")
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])
        path.write_bytes(raw)
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")

    def test_revocation_keeps_actual_artifacts_but_disables_label(self):
        row = self.rows[4]
        result = self.store.revoke(
            row["run_id"], {"reviewer": "Author", "reason": "Review withdrawn", "confirmed": True}
        )
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertTrue(self.store.directory(row["run_id"]).is_dir())

    def test_gcd_results_do_not_enter_legacy_ledger(self):
        legacy = MathlibLabStore(ROOT)
        for row in self.rows:
            with self.assertRaises(ValueError):
                legacy.read(row["run_id"])


if __name__ == "__main__":
    unittest.main()
