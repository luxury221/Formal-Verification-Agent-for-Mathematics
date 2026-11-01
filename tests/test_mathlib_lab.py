"""Typed library terms, durable gates, and actual Lean/replay integration."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from mra.mathlib_lab import MathlibLabStore, audit
from mra.mathlib_terms import compile_request, examples
from mra.tasks import TaskStore, WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def isolated_store():
    root = ROOT / "artifacts/mathlib-lab-tests" / uuid.uuid4().hex
    store = MathlibLabStore(root)
    store.root = ROOT
    store.tasks = TaskStore(ROOT)
    return store


def submit(store, value=None, **changes):
    value = examples()[0] if value is None else value
    plan = store.preview(value)
    data = {
        "request_id": uuid.uuid4().hex,
        "request": value,
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "Mathlib integration reviewer",
        "confirmed": True,
    }
    return store.submit({**data, **changes})


class TermTests(unittest.TestCase):
    def test_all_examples_compile_without_running_or_discarding_assumptions(self):
        for value in examples():
            compiled = compile_request(value)
            self.assertEqual(compiled["request"]["assumptions"], value["assumptions"])
            self.assertEqual(len(compiled["assumptions"]), len(value["assumptions"]))
            self.assertIn("Nat", compiled["formal_statement"])
        chain = compile_request(examples()[0])
        self.assertEqual(chain["references"], ["Nat.dvd_add", "Nat.dvd_trans"])
        self.assertEqual(chain["formal_statement"].count(" → "), 2)

    def test_exact_schemas_reject_unknown_definitions_source_and_implicit_parameters(self):
        cases = []
        for path, value in [
            ("schema", "arbitrary-lean"),
            ("goal", {"prime": [{"var": "a"}]}),
            ("proof", {"source": "by sorry"}),
            ("proof", {"ref": "sorryAx", "terms": [], "proofs": []}),
            ("proof", {"ref": "Nat.dvd_add", "terms": [], "proofs": []}),
            ("proof", {"hyp": "missing"}),
        ]:
            case = examples()[0]
            case[path] = value
            cases.append(case)
        cases.append({**examples()[0], "drop_premises": True})
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                compile_request(case)

    def test_names_cannot_inject_shadow_or_reference_unbound_variables(self):
        for names in (["a", "a"], ["a; run_cmd"], ["a\nend"], ["a", "b", "x"]):
            case = examples()[0]
            case["variables"] = names
            with self.subTest(names=names), self.assertRaises(ValueError):
                compile_request(case)
        case = examples()[0]
        case["assumptions"][0]["name"] = "a"
        with self.assertRaises(ValueError):
            compile_request(case)

    def test_negative_bool_float_constants_and_bad_budgets_rejected(self):
        for number in (-1, True, 1.2, 1000001):
            case = examples()[0]
            case["goal"] = {"dvd": [{"nat": number}, {"nat": 0}]}
            with self.subTest(number=number), self.assertRaises(ValueError):
                compile_request(case)
        for timeout in (True, 0, 31, float("nan"), float("inf")):
            case = {**examples()[0], "timeout_seconds": timeout}
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                compile_request(case)

    def test_node_depth_and_parameter_budgets(self):
        case = examples()[0]
        deep = {"nat": 0}
        for _ in range(16):
            deep = {"add": [deep, {"nat": 0}]}
        case["goal"] = {"eq": [deep, {"nat": 0}]}
        with self.assertRaises(ValueError):
            compile_request(case)
        for key, value in [
            ("variables", list("abcdefg")),
            ("assumptions", examples()[0]["assumptions"] * 4),
        ]:
            case = {**examples()[0], key: value}
            with self.assertRaises(ValueError):
                compile_request(case)

    def test_audit_does_not_trust_type_success_or_hidden_axioms(self):
        base = {
            "target": "MraCandidate.result",
            "declaration_kind": "theorem",
            "type_matches": True,
            "local_axioms": [],
            "axioms": [],
            "local_dependency_axioms": [],
        }
        self.assertEqual(audit("MRA_AUDIT:" + json.dumps(base), "MRA_AUDIT:"), base)
        for changes in (
            {"target": "Other"},
            {"type_matches": False},
            {"axioms": ["sorryAx"]},
            {"local_dependency_axioms": ["Lean.ofReduceBool"]},
            {"local_axioms": ["backdoor"]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                audit("MRA_AUDIT:" + json.dumps({**base, **changes}), "MRA_AUDIT:")
        with self.assertRaises(ValueError):
            audit("MRA_REPLAY:" + json.dumps(base), "MRA_REPLAY:", replay=True)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.store = isolated_store()

    def test_preview_is_unproved_and_idempotency_binds_named_confirmation(self):
        plan = self.store.preview(examples()[0])
        self.assertEqual(plan["math_verdict"], "UNKNOWN")
        self.assertFalse(self.store.list_runs())
        first = submit(self.store, request_id="same")
        second = submit(self.store, request_id="same")
        self.assertEqual(first["run_id"], second["run_id"])
        with self.assertRaises(ValueError):
            submit(self.store, request_id="same", reviewer="Another person")

    def test_no_unnamed_false_or_stale_confirmation(self):
        for changes in (
            {"reviewer": " "},
            {"confirmed": False},
            {"confirmed": 1},
            {"plan_fingerprint": "stale"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                submit(self.store, **changes)

    def test_cancel_queue_and_revoke_keep_evidence_unknown(self):
        queued = submit(self.store)
        row = self.store.cancel(queued["run_id"])
        self.assertEqual(row["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(row["run_id"])["worker_state"], "IDLE")
        revoked = self.store.revoke(
            row["run_id"],
            {"reviewer": "Reviewer", "reason": "Semantic correction", "confirmed": True},
        )
        self.assertTrue(revoked["invalidated"])
        self.assertEqual(revoked["math_verdict"], "UNKNOWN")

    def test_shared_lock_blocks_recovery_or_execution(self):
        queued = submit(self.store)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(queued["run_id"])

    def test_restart_never_promotes_residual_artifacts(self):
        queued = submit(self.store)
        with self.store.transaction() as db:
            row = self.store.read(queued["run_id"], db)
            row.update(state="RUNNING", stage="REPLAY_RUNNING")
            self.store.save(db, row)
        result = self.store.recover()
        self.assertEqual(result["interrupted"], [queued["run_id"]])
        row = self.store.report(queued["run_id"])
        self.assertEqual((row["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))

    def test_corrupt_ledger_and_path_traversal_rejected(self):
        queued = submit(self.store)
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET sha256='corrupt' WHERE id=?", (queued["run_id"],))
        with self.assertRaises(ValueError):
            self.store.report(queued["run_id"])
        with self.assertRaises(ValueError):
            self.store.report("../../outside")

    def test_changed_catalog_or_source_permanently_suppresses_plan(self):
        queued = submit(self.store)
        with patch("mra.mathlib_lab.source_manifest", return_value={"changed": True}):
            self.assertTrue(self.store.report(queued["run_id"])["invalidated"])
        self.assertTrue(self.store.report(queued["run_id"])["invalidated"])

    def test_new_cli_previews_exact_request_and_rejects_invalid_names(self):
        path = self.store.base / "cli-request.json"
        path.write_text(json.dumps(examples()[0]), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "mra", "mathlib-preview", "--input", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["math_verdict"], "UNKNOWN")
        result = subprocess.run(
            [sys.executable, "-m", "mra", "mathlib-report", "../../outside"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)


class HttpTests(unittest.TestCase):
    def test_routes_guard_full_preview_history_and_cancel(self):
        store = isolated_store()
        # Keep the transport fixture isolated; use the real root for sealed manifests.
        root = store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib = store
            for route in (
                "/mathlib",
                "/mathlib.js",
                "/mathlib.css",
                "/v1/mathlib/catalog",
                "/v1/mathlib/runs",
            ):
                self.assertEqual(request(server, "GET", route)[0], 200)
            for headers in ({"Origin": "https://elsewhere.test"}, {"X-MRA-CSRF": "wrong"}):
                self.assertEqual(
                    request(server, "POST", "/v1/mathlib/preview", examples()[0], headers=headers)[
                        0
                    ],
                    403,
                )
            self.assertEqual(
                request(server, "POST", "/v1/mathlib/preview", raw=b'{"x":1,"x":2}')[0], 400
            )
            status, plan, _ = request(server, "POST", "/v1/mathlib/preview", examples()[0])
            self.assertEqual(status, 200)
            status, row, _ = request(
                server,
                "POST",
                "/v1/mathlib/runs",
                {
                    "request_id": "http",
                    "request": plan["request"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "HTTP reviewer",
                    "confirmed": True,
                },
            )
            self.assertEqual(status, 202)
            route = "/v1/mathlib/runs/" + row["run_id"]
            self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(request(server, "POST", route + "/cancel", {"extra": True})[0], 400)
            self.assertEqual(
                request(server, "POST", route + "/cancel", {})[1]["state"], "CANCELLED"
            )


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "requires sealed actual Lean and replay"
)
class RealLeanTests(unittest.TestCase):
    def setUp(self):
        self.store = isolated_store()

    def execute(self, value=None):
        row = submit(self.store, value)
        self.store.work_once(row["run_id"])
        return self.store.report(row["run_id"])

    def test_real_composition_and_two_mathlib_definitional_equivalences(self):
        for value in examples()[:3]:
            with self.subTest(value=value["title"]):
                row = self.execute(value)
                self.assertEqual(
                    (row["state"], row["math_verdict"], row["evidence_level"]),
                    ("COMPLETED", "PROVED", "E4"),
                    row["result"],
                )
                self.assertFalse(row["result"]["replay"]["audit"]["candidate_module_imported"])

    def test_missing_premise_is_rejected_without_claiming_refutation(self):
        row = self.execute(examples()[3])
        self.assertEqual((row["state"], row["math_verdict"]), ("REJECTED", "UNKNOWN"))
        self.assertIsNone(row["result"]["replay"])

    def test_actual_timeout_does_not_restart_budget_for_replay(self):
        value = {**examples()[0], "timeout_seconds": 1}
        row = self.execute(value)
        self.assertEqual(row["state"], "TIMEOUT")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertIsNone(row["result"]["replay"])

    def test_fresh_kernel_rejects_a_different_expected_target(self):
        from mra.replay import render_replay

        with patch(
            "mra.mathlib_lab.render_replay",
            side_effect=lambda root, _: render_replay(root, "False"),
        ):
            row = self.execute()
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertNotEqual(row["result"]["replay"]["returncode"], 0)
        self.assertIn("MRA_REPLAY_TARGET_MISMATCH", row["result"]["error"])

    def test_all_nine_references_typecheck_with_explicit_arguments(self):
        values = examples()
        a, b, c = [{"var": name} for name in ("a", "b", "c")]
        for name, goal, terms, assumptions, proofs in [
            ("Nat.dvd_refl", {"dvd": [a, a]}, [a], [], []),
            (
                "Nat.dvd_trans",
                {"dvd": [a, c]},
                [a, b, c],
                values[0]["assumptions"],
                [{"hyp": "hab"}, {"hyp": "hbc"}],
            ),
            (
                "Nat.dvd_antisymm",
                {"eq": [a, b]},
                [a, b],
                [
                    {"name": "hab", "formula": {"dvd": [a, b]}},
                    {"name": "hba", "formula": {"dvd": [b, a]}},
                ],
                [{"hyp": "hab"}, {"hyp": "hba"}],
            ),
            ("Nat.dvd_mul_right", {"dvd": [a, {"mul": [a, b]}]}, [a, b], [], []),
            ("Nat.dvd_mul_left", {"dvd": [a, {"mul": [b, a]}]}, [a, b], [], []),
            (
                "Nat.dvd_sub_self_right",
                {"iff": [{"dvd": [a, {"sub": [b, a]}]}, {"or": [{"dvd": [a, b]}, {"le": [b, a]}]}]},
                [a, b],
                [],
                [],
            ),
        ]:
            value = {
                **values[0],
                "title": name,
                "goal": goal,
                "assumptions": assumptions,
                "proof": {"ref": name, "terms": terms, "proofs": proofs},
            }
            with self.subTest(reference=name):
                self.assertEqual(self.execute(value)["math_verdict"], "PROVED")

    def test_zero_divisibility_and_pair_equivalence_application(self):
        case = examples()[1]
        a, b = {"var": "a"}, {"var": "b"}
        case["assumptions"] = [
            {"name": "hab", "formula": {"dvd": [a, b]}},
            {"name": "hba", "formula": {"dvd": [b, a]}},
        ]
        case["goal"] = {"eq": [a, b]}
        case["proof"] = {"mpr": [case["proof"], {"pair": [{"hyp": "hab"}, {"hyp": "hba"}]}]}
        self.assertEqual(self.execute(case)["math_verdict"], "PROVED")
        case = {
            **case,
            "variables": [],
            "assumptions": [],
            "goal": {"dvd": [{"nat": 0}, {"nat": 0}]},
            "proof": {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []},
        }
        self.assertEqual(self.execute(case)["math_verdict"], "PROVED")

    def test_artifact_corruption_permanently_suppresses_proved_label(self):
        row = self.execute()
        self.assertEqual(row["math_verdict"], "PROVED")
        path = self.store.directory(row["run_id"]) / "replay/proof.ndjson"
        original = path.read_bytes()
        path.write_bytes(original + b"{}\n")
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")
        path.write_bytes(original)
        self.assertTrue(self.store.report(row["run_id"])["invalidated"])

    def test_cancel_and_revoke_during_actual_replay_never_publish(self):
        original = NativeLeanBackend._run_worker
        for revoke in (False, True):
            queued = submit(self.store)

            def wrapper(backend, *args, **options):
                if options["phase"] == "REPLAY_RUNNING":
                    checkpoint = options["checkpoint"]

                    def stop(phase, path):
                        checkpoint(phase, path)
                        if revoke:
                            self.store.revoke(
                                queued["run_id"],
                                {
                                    "reviewer": "Review",
                                    "reason": "Revoked during real replay",
                                    "confirmed": True,
                                },
                            )
                        else:
                            self.store.cancel(queued["run_id"])

                    options["checkpoint"] = stop
                return original(backend, *args, **options)

            with patch.object(NativeLeanBackend, "_run_worker", wrapper):
                self.store.work_once(queued["run_id"])
            row = self.store.report(queued["run_id"])
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertIsNotNone(row["result"]["replay"]["pid"])

    def test_real_process_exit_after_candidate_never_promotes_on_recovery(self):
        row = submit(self.store)
        script = "\n".join(
            [
                "import os",
                "from pathlib import Path",
                "from mra.mathlib_lab import MathlibLabStore",
                "from mra.tasks import TaskStore",
                "from mra.verifier import NativeLeanBackend",
                f"store = MathlibLabStore(Path({str(self.store.base.parents[1])!r}))",
                f"store.root = Path({str(ROOT)!r})",
                "store.tasks = TaskStore(store.root)",
                "original = NativeLeanBackend._run_worker",
                "def exit_after(backend, *args, **kwargs):",
                "    result = original(backend, *args, **kwargs)",
                "    os._exit(73)",
                "NativeLeanBackend._run_worker = exit_after",
                f"store.work_once({row['run_id']!r})",
            ]
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, timeout=120)
        self.assertEqual(result.returncode, 73)
        self.assertEqual(self.store.recover()["interrupted"], [row["run_id"]])
        self.assertEqual(self.store.report(row["run_id"])["math_verdict"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
