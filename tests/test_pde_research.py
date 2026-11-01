"""Contracts use E0-only metadata fixtures; mathematical acceptance is in the real suite."""

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.integrity import IntegrityError
from mra.pde_research import PDEResearchStore, SOURCES
from mra.pde_research_graph import SCHEMA, bind_node_draft, compile_graph
from mra.pde_research_examples import examples
from mra.research import ResearchStore
from mra.research_flow import lease
from mra.tasks import WorkerBusy
from test_http_api import request, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "pde-graph-" + uuid.uuid4().hex


def raw(project, index=0):
    return {
        "project_id": project["project_id"],
        "branch_id": "main",
        "nodes": deepcopy(examples()[index]["nodes"]),
    }


def save(store, value):
    plan = store.preview(value)
    return store.create(
        {"request_id": rid(), "request": value, "plan_fingerprint": plan["plan_fingerprint"]}
    )


def confirmation(store, key, node="problem"):
    preview = store.preview_node(key, {"node_id": node})
    return {
        "request_id": rid(),
        "node_id": node,
        "execution_fingerprint": preview["execution_fingerprint"],
        "reviewer": "E0-only contract author",
        "confirmed": True,
    }


class CompilerTests(unittest.TestCase):
    def graph(self):
        return {"schema": SCHEMA, "nodes": deepcopy(examples()[1]["nodes"])}

    def test_four_node_sources_preserve_all_original_conditions(self):
        raw = self.graph()
        value = compile_graph(raw)
        self.assertEqual(value["order"], ["ibp", "green", "test", "energy"])
        for node in raw["nodes"]:
            plan = value["node_plans"][node["id"]]
            self.assertEqual(len(plan["conditions"]), len(node["target"]["assumptions"]))
            self.assertFalse(plan["source_authorization_checked"])
        self.assertFalse(value["execution_supported"])

    def test_cycles_dangling_orphan_and_multiple_originals_rejected(self):
        for kind in ("cycle", "dangling", "orphan", "problems"):
            raw = self.graph()
            if kind == "cycle":
                raw["nodes"][0]["depends_on"] = ["energy"]
            elif kind == "dangling":
                raw["nodes"][1]["depends_on"] = ["absent"]
            elif kind == "orphan":
                raw["nodes"][-1]["depends_on"] = []
            else:
                raw["nodes"][0]["kind"] = "problem"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                compile_graph(raw)

    def test_symbolic_source_is_distinct_from_scheduling_dependency(self):
        raw = self.graph()
        raw["nodes"][-1]["depends_on"].append("ibp")
        graph = compile_graph(raw)
        self.assertEqual(
            [s["node_id"] for s in graph["node_plans"]["energy"]["source_nodes"]], ["test"]
        )
        self.assertEqual(graph["node_plans"]["energy"]["scheduling_prerequisites"], ["test", "ibp"])

    def test_symbolic_source_must_be_direct_declared_ancestor(self):
        raw = self.graph()
        raw["nodes"][-1]["target"]["sources"][0]["node_id"] = "green"
        with self.assertRaises(ValueError):
            compile_graph(raw)

    def test_binding_preserves_type_and_complete_target(self):
        graph = compile_graph(self.graph())
        fingerprint = graph["node_plans"]["test"]["type_fingerprint"]
        bindings = {"test": {"run_id": "pdelang-" + "a" * 32, "type_fingerprint": fingerprint}}
        value = bind_node_draft(graph, "energy", bindings)
        self.assertEqual(
            value["formal_statement"], graph["node_plans"]["energy"]["formal_statement"]
        )
        self.assertFalse(value["source_authorization_checked"])
        for bad in (
            {},
            {"test": {"run_id": "pdelang-" + "a" * 32, "type_fingerprint": "0" * 64}},
            {"test": {"run_id": {"math_verdict": "PROVED"}, "type_fingerprint": fingerprint}},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bind_node_draft(graph, "energy", bad)

    def test_compiled_graph_cannot_be_rebound_after_original_text_changes(self):
        graph = compile_graph(self.graph())
        graph["request"]["nodes"][0]["target"]["original"] += " changed"
        with self.assertRaises(ValueError):
            bind_node_draft(graph, "ibp", {})

    def test_depth_and_storage_budgets_are_bounded(self):
        for value in (
            {"schema": SCHEMA, "nodes": []},
            {"schema": SCHEMA, "nodes": [{}] * 9},
            {
                "schema": SCHEMA,
                "nodes": [{"deep": [[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]}],
            },
        ):
            with self.assertRaises(ValueError):
                compile_graph(value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_sources(self.root)
        for name in SOURCES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for target in ("mra.pde_language_lab.manifest", "mra.pde_research.provider_manifest"):
            p = patch(
                target, return_value={"contract_fixture": True, "never_validates_mathematics": True}
            )
            p.start()
            self.addCleanup(p.stop)
        self.store = PDEResearchStore(self.root)
        self.project = ResearchStore(self.root).create(
            {
                "request_id": rid(),
                "title": "PDE E0 contract",
                "question": "Complete original targets stay UNKNOWN until actual checks.",
            }
        )
        self.value = save(self.store, raw(self.project))
        self.key = self.value["graph_id"]

    def test_expanded_save_keeps_original_derivation_and_only_one_final_check(self):
        request = raw(self.project, 1)
        plan = self.store.preview_expanded(request)
        data = {
            "request_id": rid(),
            "request": request,
            "plan_fingerprint": plan["plan_fingerprint"],
        }
        row = self.store.create_expanded(data)
        self.assertEqual(row["graph_id"], self.store.create_expanded(data)["graph_id"])
        self.assertEqual(row["plan"]["expansion"]["source_graph"]["nodes"], request["nodes"])
        self.assertEqual(row["plan"]["order"], ["energy"])
        self.assertEqual(row["attempts"], [])
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.create(data)
        changed = deepcopy(data)
        changed["request"]["nodes"][-1]["target"]["original"] += " changed"
        with self.assertRaises(ValueError):
            self.store.create_expanded(changed)

    def test_catalog_lists_metadata_without_reauditing_proofs(self):
        with patch.object(
            self.store, "report", side_effect=AssertionError("No mathematical recheck for listing")
        ):
            listing = self.store.catalog(self.project["project_id"], "main")
        self.assertEqual(listing["graphs"][0]["graph_id"], self.key)
        self.assertFalse(listing["graphs"][0]["evidence_checked"])
        self.assertNotIn("math_verdict", listing["graphs"][0])

    def test_expanded_http_preview_and_save_keep_evidence_unknown(self):
        with running_server(self.root) as server:
            server.pde_research = self.store
            request_data = raw(self.project, 1)
            code, plan, _ = request(
                server, "POST", "/v1/pde-research/expanded-preview", request_data
            )
            self.assertEqual(code, 200)
            code, row, _ = request(
                server,
                "POST",
                "/v1/pde-research/expanded",
                {
                    "request_id": rid(),
                    "request": request_data,
                    "plan_fingerprint": plan["plan_fingerprint"],
                },
            )
            self.assertEqual(code, 201)
            self.assertEqual(len(row["plan"]["expansion"]["steps"]), 4)
            self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_complete_target_saved_with_no_execution_or_evidence(self):
        row = self.store.report(self.key)
        self.assertEqual(row["plan"]["request"], raw(self.project))
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(row["attempts"], [])
        self.assertFalse((self.store.checker(self.key).base / "runs").exists())

    def test_explicit_confirmation_and_current_preview_are_required(self):
        data = confirmation(self.store, self.key)
        for changes in ({"confirmed": 1}, {"reviewer": " "}, {"execution_fingerprint": "stale"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit(self.key, {**data, **changes})
        self.assertEqual(self.store.read(self.key)["attempts"], [])

    def test_submit_is_idempotent_and_reviewer_is_normalized(self):
        data = confirmation(self.store, self.key)
        a = self.store.submit(self.key, {**data, "reviewer": "  " + data["reviewer"] + " "})
        self.assertEqual(self.store.submit(self.key, data)["child_id"], a["child_id"])
        self.assertEqual(len(self.store.read(self.key)["attempts"]), 1)
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**data, "reviewer": "different"})
        self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})
        self.assertEqual(self.store.submit(self.key, data)["state"], "CANCELLED")

    def test_progress_uses_atomic_metadata_without_audit_or_lock(self):
        a = self.store.submit(self.key, confirmation(self.store, self.key))
        before = (self.store.directory(self.key) / "record.json").read_bytes()
        with (
            lease(self.store.directory(self.key)),
            patch.object(
                self.store, "_audit", side_effect=AssertionError("Progress must not audit evidence")
            ),
        ):
            row = self.store.progress(self.key)
        self.assertFalse(row["evidence_checked"])
        self.assertNotIn("math_verdict", row)
        self.assertNotIn("evidence_level", row)
        self.assertEqual(row["attempts"][0]["attempt_id"], a["attempt_id"])
        self.assertEqual(row["attempts"][0]["state"], "QUEUED")
        self.assertEqual((self.store.directory(self.key) / "record.json").read_bytes(), before)
        self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})
        final = self.store.progress(self.key)
        self.assertNotEqual(row["record_fingerprint"], final["record_fingerprint"])
        self.assertEqual(final["attempts"][0]["state"], "CANCELLED")

    def test_worker_waits_for_reader_and_does_not_repeat_yielded_mutation(self):
        acquired, entered = threading.Event(), threading.Event()
        errors = []

        def worker():
            try:
                with self.store.tasks.worker_lock():
                    acquired.set()
                    with self.store.worker_audited(self.key) as (row, _, _, _):
                        entered.set()
                        self.store.event(row, "CONTENTION_TEST")
                        raise ValueError("Failure after one yielded mutation")
            except ValueError as error:
                errors.append(str(error))

        with lease(self.store.directory(self.key)):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(acquired.wait(5))
            self.assertFalse(entered.wait(0.1))
            self.assertEqual(self.store.progress(self.key)["attempts"], [])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(entered.is_set())
        self.assertEqual(errors, ["Failure after one yielded mutation"])
        row = self.store.report(self.key)
        self.assertEqual([e["kind"] for e in row["events"]].count("CONTENTION_TEST"), 1)
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_actual_child_queue_and_cancel_never_publish_proof(self):
        a = self.store.submit(self.key, confirmation(self.store, self.key))
        checker = self.store.checker(self.key)
        child = checker.report(a["child_id"])
        self.assertEqual(
            (child["state"], child["math_verdict"], child["files"], child["result"]),
            ("QUEUED", "UNKNOWN", {}, None),
        )
        row = self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})
        self.assertEqual(row["checks"][a["attempt_id"]]["state"], "CANCELLED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_original_cannot_use_unproved_prerequisites(self):
        row = save(self.store, raw(self.project, 1))
        with self.assertRaises(ValueError):
            self.store.preview_node(row["graph_id"], {"node_id": "energy"})
        with self.assertRaises((IntegrityError, ValueError)):
            self.store.preview_node(row["graph_id"], {"node_id": "ibp"})

    def test_only_one_active_attempt_and_total_budget_includes_cancellation(self):
        a = self.store.submit(self.key, confirmation(self.store, self.key))
        with self.assertRaises(ValueError):
            self.store.submit(self.key, confirmation(self.store, self.key))
        self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})
        for _ in range(11):
            a = self.store.submit(self.key, confirmation(self.store, self.key))
            self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})
        self.assertEqual(self.store.report(self.key)["worker_seconds_reserved"], 480)
        with self.assertRaises(ValueError):
            self.store.submit(self.key, confirmation(self.store, self.key))

    def test_missing_or_foreign_child_database_is_not_recreated(self):
        checker = self.store.checker(self.key)
        checker.path.unlink()
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        self.assertFalse(checker.path.exists())

    def test_plan_damage_is_permanent_after_original_bytes_restore(self):
        p = self.store.directory(self.key) / "plan.json"
        before = p.read_bytes()
        p.write_bytes(b"{}")
        with self.assertRaises((ValueError, KeyError)):
            self.store.report(self.key)
        p.write_bytes(before)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_implementation_change_is_permanent_after_restore(self):
        p = self.root / SOURCES[0]
        before = p.read_bytes()
        p.write_bytes(before + b"\n# changed\n")
        self.assertTrue(self.store.report(self.key)["invalidated"])
        p.write_bytes(before)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_cancel_and_withdrawal_require_existing_named_target(self):
        with self.assertRaises(ValueError):
            self.store.cancel(self.key, {"attempt_id": "absent"})
        for changes in ({"confirmed": 1}, {"reviewer": " "}, {"node_id": "absent"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.withdraw(
                    self.key,
                    {
                        "node_id": "problem",
                        "reviewer": "author",
                        "reason": "withdraw",
                        "confirmed": True,
                        **changes,
                    },
                )

    def test_parent_revision_change_invalidates_saved_complete_graph(self):
        parent = self.store.research.report(self.project["project_id"])
        self.store.research.edit(
            self.project["project_id"],
            {
                "request_id": rid(),
                "expected_version": parent["version"],
                "branch_id": "main",
                "nodes": parent["branches"]["main"]["nodes"],
            },
        )
        self.assertTrue(self.store.report(self.key)["invalidated"])
        with self.assertRaises(ValueError):
            self.store.preview_node(self.key, {"node_id": "problem"})

    def test_graph_lock_and_global_worker_lock_do_not_mutate_queue(self):
        a = self.store.submit(self.key, confirmation(self.store, self.key))
        with lease(self.store.directory(self.key)), self.assertRaises(WorkerBusy):
            self.store.report(self.key)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(self.key)
        self.assertEqual(self.store.read(self.key)["attempts"][0]["state"], "QUEUED")
        self.store.cancel(self.key, {"attempt_id": a["attempt_id"]})

    def test_recovery_cancels_queued_orphans_without_promoting_graph(self):
        checker = self.store.checker(self.key)
        target = raw(self.project)["nodes"][0]["target"]
        p = checker.preview(target)
        orphan = checker.submit(
            {
                "request_id": rid(),
                "request": target,
                "plan_fingerprint": p["plan_fingerprint"],
                "reviewer": "contract",
                "confirmed": True,
            }
        )
        self.store.recover()
        self.assertEqual(checker.report(orphan["run_id"])["state"], "CANCELLED")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")

    def test_http_actual_guards_assets_and_queue_contract(self):
        with running_server(self.root) as server:
            server.pde_research = self.store
            code, listing, _ = request(
                server, "GET", "/v1/pde-research/projects/" + self.project["project_id"] + "/main"
            )
            self.assertEqual(code, 200)
            self.assertEqual(listing["graphs"][0]["nodes"], 1)
            self.assertEqual(listing["graphs"][0]["title"], "scalar-cauchy-schwarz")
            for route in ("/pde", "/pde-research.js", "/pde-research.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(request(server, "GET", "/v1/pde-research/catalog?unused=1")[0], 400)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/pde-research/preview",
                    raw(self.project),
                    headers={"Origin": "http://invalid"},
                )[0],
                403,
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/pde-research/preview",
                    raw(self.project),
                    headers={"X-MRA-CSRF": "invalid"},
                )[0],
                403,
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/pde-research/preview",
                    raw=b'{"project_id":1,"project_id":2}',
                )[0],
                400,
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/pde-research/source-preview",
                    {"run_id": "pdelang-" + "a" * 32},
                )[0],
                400,
            )
            code, p, _ = request(
                server,
                "POST",
                "/v1/pde-research/" + self.key + "/node-preview",
                {"node_id": "problem"},
            )
            self.assertEqual(code, 200)
            data = {
                "request_id": rid(),
                "node_id": "problem",
                "execution_fingerprint": p["execution_fingerprint"],
                "reviewer": "HTTP E0 contract",
                "confirmed": True,
            }
            code, a, _ = request(server, "POST", "/v1/pde-research/" + self.key + "/submit", data)
            self.assertEqual(code, 202)
            code, progress, _ = request(server, "GET", "/v1/pde-research/" + self.key + "/progress")
            self.assertEqual(code, 200)
            self.assertFalse(progress["evidence_checked"])
            self.assertNotIn("math_verdict", progress)
            self.assertEqual(progress["attempts"][0]["state"], "QUEUED")
            code, row, _ = request(
                server,
                "POST",
                "/v1/pde-research/" + self.key + "/cancel",
                {"attempt_id": a["attempt_id"]},
            )
            self.assertEqual(code, 200)
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertEqual(row["checks"][a["attempt_id"]]["files"], {})
            self.assertEqual(
                canonical(row), canonical(request(server, "GET", "/v1/pde-research/" + self.key)[1])
            )


if __name__ == "__main__":
    unittest.main()
