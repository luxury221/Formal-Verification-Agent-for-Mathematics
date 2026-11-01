"""Complete typed graph context, prerequisite identities, lifecycle, and real Lean."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.research import ResearchStore
from mra.research_flow import lease
from mra.tasks import WorkerBusy
from mra.typed_research import (
    SOURCES,
    TypedGraphChecks,
    TypedResearchStore,
    example,
    source_identity,
)
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/typed-research-20261028-01"


def rid():
    return "typed-qa-" + uuid.uuid4().hex


def parent(root):
    return ResearchStore(root).create(
        {
            "request_id": rid(),
            "title": "类型定义与项目研究验收",
            "question": "在同一项目中保存完整定义，独立核验引理和原问题。",
        }
    )


def create(store, project, value=None):
    raw = {"project_id": project["project_id"], "branch_id": "main", **deepcopy(value or example())}
    plan = store.preview(raw)
    return store.create(
        {"request_id": rid(), "request": raw, "plan_fingerprint": plan["plan_fingerprint"]}
    )


def confirmation(store, graph_id, node="expansion"):
    p = store.preview_node(graph_id, {"node_id": node})
    return {
        "request_id": rid(),
        "node_id": node,
        "execution_fingerprint": p["execution_fingerprint"],
        "reviewer": "Typed research QA",
        "confirmed": True,
    }


def inert_success(store, graph_id, node="expansion"):
    """Unit-only ledger fixture, deliberately not claimed as mathematical evidence."""
    attempt = store.submit(graph_id, confirmation(store, graph_id, node))
    checks = store.checker(graph_id)
    with checks.transaction() as db:
        child = checks.read(attempt["child_id"], db)
        child.update(
            state="COMPLETED",
            stage="COMPLETED",
            math_verdict="PROVED",
            evidence_level="E4",
            result={"fixture": "INERT UNIT CONTRACT, NO LEAN OR MATHEMATICAL EVIDENCE"},
        )
        checks.save(db, child)
    row = store.read(graph_id)
    current = next(a for a in row["attempts"] if a["attempt_id"] == attempt["attempt_id"])
    current.update(state="COMPLETED", source_fingerprint=source_identity(child))
    store.save(row)
    return store.report(graph_id)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for n in (*SOURCES, "mra/typed_terms.py", "mra/typed_definitions.py"):
            shutil.copyfile(ROOT / n, self.root / n)
        self.project = parent(self.root)
        self.store = TypedResearchStore(self.root)
        self.value = create(self.store, self.project)
        self.key = self.value["graph_id"]

    def test_preview_and_graph_store_are_unknown_and_preserve_every_definition_and_condition(self):
        raw = self.value["plan"]["request"]
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            value = self.store.preview(raw)
        self.assertEqual(value["request"], raw)
        self.assertEqual(value["order"], ["expansion", "problem"])
        for key, plan in value["node_plans"].items():
            self.assertEqual(plan["request"]["definitions"], raw["definitions"])
            node = next(n for n in raw["nodes"] if n["id"] == key)
            self.assertEqual(plan["request"]["assumptions"], node["target"]["assumptions"])
            self.assertIn("let mra_d0", plan["formal_statement"])
            self.assertEqual(plan["math_verdict"], "UNKNOWN")
        self.assertEqual(self.value["math_verdict"], "UNKNOWN")
        self.assertEqual(self.value["node_states"]["problem"]["blocked_by"], ["expansion"])

    def test_graph_rejects_cycles_orphans_missing_problem_types_and_extra_target_definitions(self):
        cases = []
        x = example()
        x["nodes"][0]["depends_on"] = ["problem"]
        cases.append(x)
        x = example()
        x["nodes"][1]["depends_on"] = ["missing"]
        cases.append(x)
        x = example()
        x["nodes"][1]["depends_on"] = []
        cases.append(x)
        x = example()
        x["nodes"][0]["kind"] = "problem"
        cases.append(x)
        x = example()
        x["nodes"][0]["target"]["definitions"] = []
        cases.append(x)
        x = example()
        x["nodes"][1]["target"]["variables"][0]["type"] = "Int"
        cases.append(x)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.preview(
                    {"project_id": self.project["project_id"], "branch_id": "main", **value}
                )

    def test_blocked_node_cannot_be_previewed_or_queued_and_names_fingerprints_are_strict(self):
        with self.assertRaises(ValueError):
            confirmation(self.store, self.key, "problem")
        raw = confirmation(self.store, self.key)
        for changes in (
            {"confirmed": 1},
            {"reviewer": " "},
            {"execution_fingerprint": "stale"},
            {"node_id": "problem"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit(self.key, {**raw, **changes})
        self.assertEqual(self.store.read(self.key)["attempts"], [])

    def test_save_and_submit_are_idempotent_and_cancelled_retry_never_requeues(self):
        raw = confirmation(self.store, self.key)
        first = self.store.submit(self.key, raw)
        self.assertEqual(self.store.submit(self.key, raw), first)
        self.store.cancel(self.key, {"attempt_id": first["attempt_id"]})
        self.assertEqual(self.store.submit(self.key, raw)["state"], "CANCELLED")
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "reviewer": "other"})
        plan = self.value["plan"]
        data = {
            "request_id": self.value["request_id"],
            "request": plan["request"],
            "plan_fingerprint": plan["plan_fingerprint"],
        }
        self.assertEqual(self.store.create(data)["graph_id"], self.key)
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")

    def test_precursor_alone_cannot_prove_original_problem_and_replacement_cannot_revive_successor(
        self,
    ):
        current = inert_success(self.store, self.key)
        self.assertEqual(current["math_verdict"], "UNKNOWN")
        stale = confirmation(self.store, self.key, "problem")
        inert_success(self.store, self.key, "problem")
        self.store.withdraw(
            self.key,
            {"node_id": "expansion", "reviewer": "QA", "reason": "withdraw", "confirmed": True},
        )
        result = self.store.report(self.key)
        self.assertEqual(result["node_states"]["problem"]["math_verdict"], "UNKNOWN")
        self.assertTrue(result["attempts"][1]["invalidated"])
        inert_success(self.store, self.key)
        with self.assertRaises(ValueError):
            self.store.submit(self.key, stale)
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")

    def test_child_target_and_provider_drift_are_persistent_after_restoration(self):
        row = inert_success(self.store, self.key)
        checker = self.store.checker(self.key)
        with checker.transaction() as db:
            child = checker.read(row["attempts"][0]["child_id"], db)
            before = deepcopy(child)
            child["plan"]["request"]["definitions"] = []
            checker.save(db, child)
        self.assertEqual(
            self.store.report(self.key)["node_states"]["expansion"]["math_verdict"], "UNKNOWN"
        )
        with checker.transaction() as db:
            checker.save(db, before)
        self.assertTrue(self.store.report(self.key)["attempts"][0]["invalidated"])
        path = self.root / "mra/typed_terms.py"
        source = path.read_bytes()
        path.write_bytes(source + b"\n")
        self.assertTrue(self.store.report(self.key)["invalidated"])
        path.write_bytes(source)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_project_branch_context_changes_invalidate_without_rewriting_legacy_nodes(self):
        source = self.store.parent(self.project["project_id"], "main")
        with self.store.research.transaction() as db:
            row = ResearchStore.read(db, self.project["project_id"])
            row["question"] += "修改原问题"
            self.store.research.save(db, row)
        self.assertNotEqual(source, self.store.parent(self.project["project_id"], "main"))
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_global_worker_lock_recovery_and_orphan_checks_cannot_promote_or_retry(self):
        raw = self.store.submit(self.key, confirmation(self.store, self.key))
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once(self.key)
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        orphan = self.store.checker(self.key).submit(
            {
                "request_id": rid(),
                "request": self.value["plan"]["node_plans"]["expansion"]["request"],
                "plan_fingerprint": self.value["plan"]["node_plans"]["expansion"][
                    "plan_fingerprint"
                ],
                "reviewer": "orphan fixture",
                "confirmed": True,
            }
        )
        self.assertIn(raw["attempt_id"], self.store.recover()["interrupted"])
        self.assertEqual(
            self.store.checker(self.key).report(orphan["run_id"])["state"], "CANCELLED"
        )
        self.assertEqual(self.store.checker(self.key).report(raw["child_id"])["state"], "CANCELLED")
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")

    def test_graph_record_damage_is_quarantined_and_restored_bytes_do_not_revive(self):
        path = self.store.directory(self.key) / "record.json"
        before = path.read_bytes()
        path.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        path.write_bytes(before)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_concurrent_submissions_allow_one_active_check(self):
        values, errors = [], []
        barrier = threading.Barrier(2)
        inputs = [confirmation(self.store, self.key) for _ in range(2)]

        def submit(data):
            barrier.wait(timeout=10)
            try:
                values.append(self.store.submit(self.key, data))
            except (ValueError, WorkerBusy) as error:
                errors.append(str(error))

        threads = [threading.Thread(target=submit, args=(data,)) for data in inputs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual((len(values), len(errors)), (1, 1))
        self.assertEqual(len(self.store.read(self.key)["attempts"]), 1)
        self.store.cancel(self.key, {"attempt_id": values[0]["attempt_id"]})

    def test_idle_recovery_does_not_reaudit_completed_targets_or_rewrite_parent(self):
        path = self.store.directory(self.key) / "record.json"
        before = path.stat().st_mtime_ns
        with patch.object(self.store, "_audit", side_effect=AssertionError("No idle math audit")):
            self.assertEqual(self.store.recover()["interrupted"], [])
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_report_reader_during_child_completion_does_not_lose_parent_commit(self):
        attempt = self.store.submit(self.key, confirmation(self.store, self.key))
        held = threading.Event()

        def reader():
            with lease(self.store.directory(self.key)):
                held.set()
                threading.Event().wait(0.35)

        thread = threading.Thread(target=reader)

        def finish(checker, child_id, lock_fd):
            with checker.transaction() as db:
                child = checker.read(child_id, db)
                child.update(state="REJECTED", stage="REJECTED")
                checker.save(db, child)
            thread.start()
            self.assertTrue(held.wait(5))
            return checker.report(child_id)

        try:
            with patch.object(TypedGraphChecks, "execute", finish):
                self.store.work_once(self.key)
        finally:
            if thread.ident:
                thread.join(timeout=5)
        value = self.store.report(self.key)
        self.assertEqual(value["attempts"][0]["state"], "REJECTED")
        self.assertEqual(self.store.recover()["interrupted"], [])
        self.assertEqual(value["attempts"][0]["attempt_id"], attempt["attempt_id"])

    def test_damaged_queued_graph_does_not_block_a_different_graph(self):
        self.store.submit(self.key, confirmation(self.store, self.key))
        (self.store.directory(self.key) / "plan.json").write_text("broken")
        other = create(self.store, self.project)
        second = self.store.submit(other["graph_id"], confirmation(self.store, other["graph_id"]))
        with patch.object(self.store, "execute") as execute:
            result = self.store.work_once()
        self.assertEqual(result["attempt_id"], second["attempt_id"])
        self.assertEqual(execute.call_count, 1)
        self.store.recover()

    def test_actual_http_csrf_strict_input_and_cancel_are_wired_without_running_lean(self):
        with running_server(self.root) as server:
            for route in ("/typed-research", "/typed-research.js", "/typed-research.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(
                request(server, "GET", "/v1/typed-research/" + self.key + "?execute=1")[0], 400
            )
            p = request(
                server,
                "POST",
                "/v1/typed-research/" + self.key + "/node-preview",
                {"node_id": "expansion"},
            )[1]
            data = {
                "request_id": rid(),
                "node_id": "expansion",
                "execution_fingerprint": p["execution_fingerprint"],
                "reviewer": "HTTP QA",
                "confirmed": True,
            }
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/typed-research/" + self.key + "/submit",
                    data,
                    headers={"X-MRA-CSRF": "wrong"},
                )[0],
                403,
            )
            status, value, _ = request(
                server, "POST", "/v1/typed-research/" + self.key + "/submit", data
            )
            self.assertEqual(status, 202)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/typed-research/" + self.key + "/cancel",
                    {"attempt_id": value["attempt_id"]},
                )[0],
                200,
            )
            self.assertEqual(
                request(server, "GET", "/v1/typed-research/" + self.key)[1]["math_verdict"],
                "UNKNOWN",
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/typed-research/preview",
                    raw=b'{"project_id":1,"project_id":2}',
                )[0],
                400,
            )


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit sealed real Lean")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = parent(ROOT)
        OUT.mkdir(exist_ok=True)
        (OUT / ("real-project-" + uuid.uuid4().hex + ".json")).write_bytes(
            canonical(cls.project) + b"\n"
        )

    def setUp(self):
        self.store = TypedResearchStore(ROOT)
        self.store.base = OUT / ("real-" + uuid.uuid4().hex)
        self.store.base.mkdir()
        self.value = create(self.store, self.project)
        self.key = self.value["graph_id"]

    def run_node(self, node):
        attempt = self.store.submit(self.key, confirmation(self.store, self.key, node))
        self.store.work_once(self.key)
        value = self.store.report(self.key)
        (self.store.base / (node + "-report.json")).write_bytes(canonical(value) + b"\n")
        return value, attempt

    def test_shared_definitions_really_enter_both_checks_and_precursor_withdrawal_propagates(self):
        row, _ = self.run_node("expansion")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(row["node_states"]["expansion"]["math_verdict"], "PROVED", row)
        row, _ = self.run_node("problem")
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("PROVED", "E4"), row)
        for child in row["checks"].values():
            self.assertEqual(
                child["plan"]["request"]["definitions"], row["plan"]["request"]["definitions"]
            )
            self.assertNotEqual(
                child["result"]["candidate"]["pid"], child["result"]["replay"]["pid"]
            )
            self.assertTrue(child["result"]["replay"]["audit"]["type_matches"])
        row = self.store.withdraw(
            self.key,
            {
                "node_id": "expansion",
                "reviewer": "QA",
                "reason": "actual withdrawal",
                "confirmed": True,
            },
        )
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertTrue(row["attempts"][1]["invalidated"])
        (self.store.base / "withdrawn.json").write_bytes(canonical(row) + b"\n")

    def test_true_precursor_cannot_strengthen_false_original_and_damaged_proof_stays_invalid(self):
        row, _ = self.run_node("expansion")
        original = example()
        original["nodes"][1]["target"]["goal"] = {"eq": [{"var": "q"}, {"rat": [0, 1]}]}
        other = create(self.store, self.project, original)
        self.key = other["graph_id"]
        self.run_node("expansion")
        result, _ = self.run_node("problem")
        self.assertEqual(result["math_verdict"], "UNKNOWN")
        self.assertEqual(result["attempts"][-1]["state"], "REJECTED")
        child = row["checks"][row["attempts"][0]["attempt_id"]]
        path = (
            self.store.checker(row["graph_id"]).directory(child["run_id"]) / "replay/proof.ndjson"
        )
        before = path.read_bytes()
        path.write_bytes(before + b"\n")
        self.assertEqual(
            self.store.report(row["graph_id"])["node_states"]["expansion"]["math_verdict"],
            "UNKNOWN",
        )
        path.write_bytes(before)
        self.assertTrue(self.store.report(row["graph_id"])["attempts"][0]["invalidated"])

    def test_cancellation_after_actual_replay_does_not_publish_graph_evidence(self):
        data = self.store.submit(self.key, confirmation(self.store, self.key))
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            value = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "REPLAY_RUNNING":
                self.assertEqual(value["returncode"], 0)
                self.store.cancel(self.key, {"attempt_id": data["attempt_id"]})
            return value

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once(self.key)
        row = self.store.report(self.key)
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(row["attempts"][0]["state"], "CANCELLED")
        self.assertEqual(row["checks"][data["attempt_id"]]["result"]["replay"]["returncode"], 0)
        (self.store.base / "cancelled.json").write_bytes(canonical(row) + b"\n")

    def test_actual_completed_replay_survives_parent_report_lock_contention(self):
        data = self.store.submit(self.key, confirmation(self.store, self.key))
        original = TypedGraphChecks.execute
        held = threading.Event()

        def reader():
            with lease(self.store.directory(self.key)):
                held.set()
                threading.Event().wait(0.35)

        thread = threading.Thread(target=reader)

        def hold(checker, *args, **kwargs):
            value = original(checker, *args, **kwargs)
            self.assertEqual(value["math_verdict"], "PROVED", value)
            thread.start()
            self.assertTrue(held.wait(5))
            return value

        try:
            with patch.object(TypedGraphChecks, "execute", hold):
                self.store.work_once(self.key)
        finally:
            if thread.ident:
                thread.join(timeout=5)
        row = self.store.report(self.key)
        self.assertEqual(row["node_states"]["expansion"]["math_verdict"], "PROVED", row)
        self.assertEqual(row["attempts"][0]["state"], "COMPLETED")
        self.assertEqual(row["attempts"][0]["attempt_id"], data["attempt_id"])
        self.assertEqual(self.store.recover()["interrupted"], [])
        (self.store.base / "reader-contention.json").write_bytes(canonical(row) + b"\n")

    def test_supervisor_exit_after_actual_replay_never_promotes_child_or_resumes(self):
        self.store.submit(self.key, confirmation(self.store, self.key))
        script = """import os,sys
from pathlib import Path
from unittest.mock import patch
from mra.typed_research import TypedResearchStore
from mra.verifier import NativeLeanBackend
s=TypedResearchStore(Path.cwd());s.base=Path(sys.argv[1])
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
 r=original(self,*args,**kwargs)
 if kwargs.get('phase')=='REPLAY_RUNNING':
  assert r['returncode']==0,r
  os._exit(73)
 return r
with patch.object(NativeLeanBackend,'_run_worker',stop):s.work_once(sys.argv[2])
"""
        p = subprocess.run(
            [sys.executable, "-c", script, str(self.store.base), self.key],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(p.returncode, 73, p.stderr)
        self.assertEqual(len(self.store.recover()["interrupted"]), 1)
        self.assertEqual(self.store.work_once(self.key)["worker_state"], "IDLE")
        row = self.store.report(self.key)
        self.assertEqual(row["attempts"][0]["state"], "INTERRUPTED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        child = self.store.checker(self.key)
        self.assertTrue(
            (child.directory(row["attempts"][0]["child_id"]) / "replay/stdout.log").is_file()
        )
        (self.store.base / "interrupted.json").write_bytes(canonical(row) + b"\n")
