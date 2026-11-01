"""Graph/HTTP contracts and opt-in actual calculus evidence; fixtures are E0 only."""

from copy import deepcopy
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.proof_lab import ProofStore, TRUST_FILES
from mra.proof_research import ProofResearchStore, SOURCES
from mra.calculus_research import examples
from mra.research import ResearchStore
from mra.tasks import WorkerBusy
from test_http_api import request, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "analysis-test-" + uuid.uuid4().hex


def parent(root):
    return ResearchStore(root).create(
        {
            "request_id": rid(),
            "title": "分析研究协议验收",
            "question": "保存完整函数、测度、条件与原目标；前置不代替原问题。",
        }
    )


def raw(project, index=1):
    return {
        "project_id": project["project_id"],
        "branch_id": "main",
        "nodes": [
            {**node, "target": {**deepcopy(node["target"]), "schema": "proof-language-v1"}}
            for node in examples()[index]["nodes"]
        ],
    }


def save(store, value):
    plan = store.preview(value)
    return store.create(
        {"request_id": rid(), "request": value, "plan_fingerprint": plan["plan_fingerprint"]}
    )


def confirm(store, key, node="lemma"):
    p = store.preview_node(key, {"node_id": node})
    return {
        "request_id": rid(),
        "node_id": node,
        "execution_fingerprint": p["execution_fingerprint"],
        "reviewer": "Local calculus graph test author",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*TRUST_FILES, *SOURCES):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.project = parent(self.root)
        self.store = ProofResearchStore(self.root)
        self.request = raw(self.project)
        self.value = save(self.store, self.request)
        self.key = self.value["graph_id"]

    def test_complete_conditions_types_measure_and_original_survive_each_plan(self):
        p = self.value["plan"]
        self.assertEqual(p["request"], self.request)
        self.assertEqual(p["order"], ["lemma", "problem"])
        for node in self.request["nodes"]:
            child = p["node_plans"][node["id"]]
            self.assertEqual(child["request"], node["target"])
            self.assertEqual(len(child["conditions"]), len(node["target"]["assumptions"]))
            self.assertEqual(child["math_verdict"], "UNKNOWN")
        self.assertIn("Measure ℝ", p["node_plans"]["problem"]["formal_statement"])
        self.assertEqual(self.value["node_states"]["problem"]["blocked_by"], ["lemma"])
        self.assertEqual(self.value["math_verdict"], "UNKNOWN")

    def test_cycles_dangling_orphan_nodes_and_multiple_problems_rejected(self):
        for mutation in ("cycle", "dangling", "orphan", "problems", "source"):
            value = deepcopy(self.request)
            if mutation == "cycle":
                value["nodes"][0]["depends_on"] = ["problem"]
            elif mutation == "dangling":
                value["nodes"][1]["depends_on"] = ["missing"]
            elif mutation == "orphan":
                value["nodes"][1]["depends_on"] = []
            elif mutation == "problems":
                value["nodes"][0]["kind"] = "problem"
            else:
                value["nodes"][0]["target"]["extra_declarations"] = "axiom bad : False"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.store.preview(value)

    def test_blocked_original_stale_fingerprint_and_nonliteral_confirmation_never_queue(self):
        with self.assertRaises(ValueError):
            confirm(self.store, self.key, "problem")
        value = confirm(self.store, self.key)
        for change in ({"confirmed": 1}, {"reviewer": " "}, {"execution_fingerprint": "old"}):
            with self.assertRaises(ValueError):
                self.store.submit(self.key, {**value, **change})
        self.assertEqual(self.store.read(self.key)["attempts"], [])

    def test_idempotent_save_submit_and_cancel_do_not_requeue(self):
        original = self.value
        envelope = {
            "request_id": original["request_id"],
            "request": self.request,
            "plan_fingerprint": original["plan_fingerprint"],
        }
        self.assertEqual(self.store.create(envelope)["graph_id"], self.key)
        value = confirm(self.store, self.key)
        attempt = self.store.submit(self.key, value)
        self.assertEqual(attempt, self.store.submit(self.key, value))
        self.store.cancel(self.key, {"attempt_id": attempt["attempt_id"]})
        self.assertEqual(self.store.submit(self.key, value)["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**value, "reviewer": "other"})

    def test_shared_graph_budget_480_seconds_also_counts_cancelled_attempts(self):
        for _ in range(12):
            attempt = self.store.submit(self.key, confirm(self.store, self.key))
            self.store.cancel(self.key, {"attempt_id": attempt["attempt_id"]})
        self.assertEqual(self.store.report(self.key)["worker_seconds_reserved"], 480)
        with self.assertRaisesRegex(ValueError, "worker budget"):
            self.store.submit(self.key, confirm(self.store, self.key))
        self.assertEqual(len(self.store.read(self.key)["attempts"]), 12)

    def test_graph_provider_and_restored_old_record_do_not_revive(self):
        record = self.store.directory(self.key) / "record.json"
        original_record = record.read_bytes()
        source = self.root / "mra/proof_research.py"
        original_source = source.read_bytes()
        source.write_bytes(original_source + b"\n# provider drift\n")
        self.assertTrue(self.store.report(self.key)["invalidated"])
        source.write_bytes(original_source)
        record.write_bytes(original_record)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_parent_context_restoration_cannot_revive_old_graph_row(self):
        record = self.store.directory(self.key) / "record.json"
        original_record = record.read_bytes()
        with self.store.research.transaction() as db:
            body = ResearchStore.read(db, self.project["project_id"])
            before = deepcopy(body)
            body["question"] += " changed"
            self.store.research.save(db, body)
        self.assertTrue(self.store.report(self.key)["invalidated"])
        with self.store.research.transaction() as db:
            self.store.research.save(db, before)
        record.write_bytes(original_record)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_missing_child_ledger_is_quarantined_and_not_recreated(self):
        database = self.store.checker(self.key).path
        before = database.read_bytes()
        database.unlink()
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        self.assertFalse(database.exists())
        database.write_bytes(before)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_empty_existing_child_ledger_is_not_adopted_or_repaired(self):
        database = self.store.checker(self.key).path
        database.unlink()
        with sqlite3.connect(database):
            pass
        with self.assertRaises(sqlite3.OperationalError):
            self.store.report(self.key)
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_global_lock_recovery_demotes_and_never_retries(self):
        attempt = self.store.submit(self.key, confirm(self.store, self.key))
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.recover()
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        self.assertIn(attempt["attempt_id"], self.store.recover()["interrupted"])
        current = self.store.report(self.key)
        self.assertEqual(current["attempts"][0]["state"], "INTERRUPTED")
        self.assertEqual(current["checks"][attempt["attempt_id"]]["state"], "CANCELLED")
        self.assertEqual(current["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")

    def test_missing_and_damaged_records_restoration_stays_invalidated(self):
        record = self.store.directory(self.key) / "record.json"
        before = record.read_bytes()
        record.write_text("bad")
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        record.write_bytes(before)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_atomic_publication_hides_partial_graph_from_worker_and_readers(self):
        seen = []
        original = ProofStore.__init__

        def make(instance, *args, **kwargs):
            base = kwargs["base"]
            if base.parent.name.startswith(".creating-"):
                self.assertNotIn(base.parent, self.store.paths())
                self.assertFalse(
                    (self.store.base / base.parent.name.removeprefix(".creating-")).exists()
                )
                seen.append(True)
            return original(instance, *args, **kwargs)

        with patch.object(ProofStore, "__init__", make):
            result = save(self.store, self.request)
        self.assertEqual(seen, [True])
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertEqual(len(self.store.paths()), 2)

    def test_private_store_path_identity_and_symlinks_fail_closed(self):
        with self.assertRaises(ValueError):
            ProofResearchStore(self.root, base=self.root / "artifacts/other")
        link = self.root / "artifacts/redirect/proof-research-private"
        link.parent.mkdir()
        link.symlink_to(self.store.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            ProofResearchStore(self.root, base=link)

    def test_actual_http_assets_csrf_preview_save_cancel_and_queries(self):
        with running_server(self.root) as server:
            for route in ("/proof", "/analysis-research.js", "/analysis-research.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(request(server, "GET", "/v1/proof-research/catalog?execute=1")[0], 400)
            route = "/v1/proof-research"
            self.assertEqual(
                request(
                    server, "POST", route + "/preview", self.request, headers={"X-MRA-CSRF": "bad"}
                )[0],
                403,
            )
            code, plan, _ = request(server, "POST", route + "/preview", self.request)
            self.assertEqual(code, 200)
            code, value, _ = request(
                server,
                "POST",
                route,
                {
                    "request_id": rid(),
                    "request": self.request,
                    "plan_fingerprint": plan["plan_fingerprint"],
                },
            )
            self.assertEqual(code, 201)
            key = value["graph_id"]
            data = confirm(server.proof_research, key)
            code, attempt, _ = request(server, "POST", route + "/" + key + "/submit", data)
            self.assertEqual(code, 202)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    route + "/" + key + "/cancel",
                    {"attempt_id": attempt["attempt_id"]},
                )[0],
                200,
            )
            current = request(server, "GET", route + "/" + key)[1]
            self.assertEqual(current["math_verdict"], "UNKNOWN")
            self.assertEqual(current["attempts"][0]["state"], "CANCELLED")
