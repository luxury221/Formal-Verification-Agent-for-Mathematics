"""Graph scheduling contracts; positive math results always use real Lean and replay."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.evidence import EvidenceGate
from mra.research_runs import ControlledReview, ResearchRunStore
from mra.tasks import WorkerBusy
from test_http_api import copy_web_inputs, request, running_server
from test_research import formula, graph as original_graph, node

ROOT = Path(__file__).resolve().parents[1]
PLAN = {"direction": "prove", "strategy": "omega", "witnesses": []}


def rid():
    return "graph-test-" + uuid.uuid4().hex


def graph():
    nodes = original_graph()
    nodes[2]["statement"] = "对任意整数n，n减3小于n。"
    return nodes


class RunFixture(unittest.TestCase):
    def edit(self, nodes):
        current = self.store.research.report(self.pid)
        return self.store.research.edit(
            self.pid,
            {
                "request_id": rid(),
                "expected_version": current["version"],
                "branch_id": "main",
                "nodes": nodes,
            },
        )

    def plan(self, keys=("l1", "l2", "main"), *, plans=None, **budget):
        return self.store.prepare(
            self.pid,
            {
                "branch_id": "main",
                "items": [{"node_id": key, "plan": (plans or {}).get(key, PLAN)} for key in keys],
                "budget": {"max_checks": 8, "worker_seconds": 240, "wall_seconds": 300, **budget},
            },
        )

    @staticmethod
    def submission(plan):
        return {
            "request_id": rid(),
            "input": plan["input"],
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "repository-graph-execution-test-author",
            "confirmed_nodes": [item["node_id"] for item in plan["items"]],
            "confirmed": True,
        }

    def queue(self, *args, **kwargs):
        result = self.store.submit(self.pid, self.submission(self.plan(*args, **kwargs)))
        self.run_ids.append(result["run_id"])
        return result

    def work(self, run):
        return self.store.work_once(run["run_id"])["run"]

    def assert_hidden(self, report):
        self.assertTrue(
            all(
                n["math_verdict"] == "UNKNOWN" and not n["results_available"]
                for n in report["nodes"]
            ),
            report,
        )


class ResearchRunProtocolTests(RunFixture):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = ResearchRunStore(self.root)
        project = self.store.research.create(
            {
                "request_id": rid(),
                "title": "Graph scheduling contract tests",
                "question": "Author-created protocol fixture; no proof claims",
            }
        )
        self.pid, self.run_ids = project["project_id"], []
        self.edit(graph())

    def test_offline_preview_reorders_topologically_and_preserves_full_independent_targets(self):
        plan = self.plan(("main", "l2", "l1"))
        self.assertEqual([i["node_id"] for i in plan["items"]], ["l1", "l2", "main"])
        self.assertEqual(plan, self.plan())
        for item in plan["items"]:
            self.assertEqual(item["bundle"]["assumptions"], [])
            self.assertEqual(item["bundle"]["variables"], [])
            self.assertEqual(item["bundle"]["steps"], [])
            self.assertEqual(item["bundle"]["conclusion"], item["node"]["formula"])
        self.assertFalse((self.root / "artifacts/runs").exists())
        self.assertEqual(self.store.papers.list_reviews(), [])

    def test_selection_rejects_unresolved_external_prerequisites_definitions_and_duplicates(self):
        for keys in (("l2",), ("l1", "l1"), (), ("missing",)):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.plan(keys)
        definition = {**node("d1"), "kind": "definition", "domain": None, "formula": None}
        self.edit([definition, {**node(), "context": ["d1"]}])
        with self.assertRaises(ValueError):
            self.plan(("d1",))
        self.assertEqual(
            self.plan(("l1",))["graph"]["nodes"][0]["statement"], definition["statement"]
        )

    def test_total_budget_enforces_integer_types_and_bounds_but_allows_partial_round(self):
        for change in (
            {"max_checks": True},
            {"max_checks": 0},
            {"max_checks": 9},
            {"worker_seconds": 29},
            {"worker_seconds": 241},
            {"wall_seconds": 0},
            {"wall_seconds": 601},
            {"wall_seconds": 1.5},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.plan(**change)
        self.assertEqual(self.plan(max_checks=1, worker_seconds=30)["budget"]["max_checks"], 1)
        with self.assertRaises(ValueError):
            self.plan(plans={"l1": {**PLAN, "strategy": "run_cmd"}})

    def test_each_node_name_and_exact_fingerprint_require_explicit_confirmation(self):
        data = self.submission(self.plan())
        for change in (
            {"confirmed": False},
            {"confirmed_nodes": ["l1"]},
            {"confirmed_nodes": ["main", "l2", "l1"]},
            {"reviewer": " "},
            {"plan_fingerprint": "stale"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.submit(self.pid, {**data, **change})
        self.assertEqual(self.store.list_runs(self.pid), [])

    def test_stale_graph_and_changed_context_cannot_use_previous_confirmation(self):
        data = self.submission(self.plan())
        changed = graph()
        changed[0]["notes"] = "Changed meaning/context"
        self.edit(changed)
        with self.assertRaises(ValueError):
            self.store.submit(self.pid, data)
        self.assertNotEqual(self.plan()["plan_fingerprint"], data["plan_fingerprint"])

    def test_idempotency_restart_cancellation_and_conflicting_request_content(self):
        data = self.submission(self.plan())
        first = self.store.submit(self.pid, data)
        self.assertEqual(first, ResearchRunStore(self.root).submit(self.pid, data))
        self.store.cancel(first["run_id"])
        replay = self.store.submit(self.pid, data)
        self.assertEqual(replay["state"], "CANCELLED")
        self.assert_hidden(replay)
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        with self.assertRaises(ValueError):
            self.store.submit(self.pid, {**data, "reviewer": "changed"})

    def test_parallel_submissions_reserve_only_one_active_run_per_branch(self):
        plan = self.plan()

        def submit(_):
            try:
                return self.store.submit(self.pid, self.submission(plan))
            except ValueError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_graph_change_invalidates_queued_run_permanently_even_if_restored(self):
        run = self.queue()
        self.edit([node()])
        self.assertEqual(self.store.report(run["run_id"])["state"], "INVALIDATED")
        self.edit(graph())
        self.assertEqual(self.store.report(run["run_id"])["state"], "INVALIDATED")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_recovery_requires_worker_lock_retains_reservation_and_never_resumes(self):
        run = self.queue()
        self.store.mutate(
            run["run_id"],
            lambda b: b.update(state="RUNNING", used={"checks": 1, "worker_seconds_reserved": 30}),
        )
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.recover()
        self.assertEqual(self.store.recover()["interrupted"], [run["run_id"]])
        report = self.store.report(run["run_id"])
        self.assertEqual(report["state"], "INTERRUPTED")
        self.assertEqual(report["used"]["worker_seconds_reserved"], 30)
        self.assert_hidden(report)
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_changed_ledger_and_storage_redirection_are_rejected(self):
        self.queue()
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE runs SET body='{}'")
        with self.assertRaises(ValueError):
            self.store.list_runs(self.pid)
        moved = self.root / "moved.sqlite3"
        self.store.path.rename(moved)
        self.store.path.symlink_to(moved)
        with self.assertRaises(ValueError):
            ResearchRunStore(self.root)

    def test_http_plan_submit_report_cancel_and_origin_csrf_strict_json_guards(self):
        with running_server(self.root) as server:
            path = "/v1/research/" + self.pid
            data = self.plan()["input"]
            status, plan, _ = request(server, "POST", path + "/run-plan", data)
            self.assertEqual(status, 200)
            for headers in ({"Origin": "http://other.test"}, {"X-MRA-CSRF": "bad"}):
                self.assertEqual(
                    request(server, "POST", path + "/runs", self.submission(plan), headers=headers)[
                        0
                    ],
                    403,
                )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    path + "/run-plan",
                    raw=b'{"branch_id":"main","branch_id":"main"}',
                )[0],
                400,
            )
            status, run, _ = request(server, "POST", path + "/runs", self.submission(plan))
            self.assertEqual(status, 201)
            detail = "/v1/research-runs/" + run["run_id"]
            self.assertEqual(request(server, "GET", detail)[1]["state"], "QUEUED")
            self.assertEqual(
                request(server, "GET", path + "/runs")[1]["runs"][0]["run_id"], run["run_id"]
            )
            self.assertEqual(request(server, "POST", detail + "/cancel", {"extra": True})[0], 400)
            self.assertEqual(
                request(server, "POST", detail + "/cancel", {})[1]["state"], "CANCELLED"
            )
            for asset in ("/research-runs.js", "/research-runs.css"):
                self.assertEqual(request(server, "GET", asset)[0], 200)

    def test_cli_plan_submit_report_and_cancel_share_the_durable_store(self):
        path = self.root / "request.json"
        code = "import sys;from pathlib import Path;import mra.__main__ as m;m.ROOT=Path(sys.argv[1]);sys.argv=['mra',*sys.argv[2:]];raise SystemExit(m.main())"

        def cli(*args):
            result = subprocess.run(
                [sys.executable, "-c", code, str(self.root), *args],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        path.write_bytes(canonical(self.plan()["input"]))
        plan = cli("research-run-plan", self.pid, "--input", str(path))
        path.write_bytes(canonical(self.submission(plan)))
        run = cli("research-run-submit", self.pid, "--input", str(path))
        self.assertEqual(cli("research-run-report", run["run_id"])["state"], "QUEUED")
        self.assertEqual(cli("research-run-cancel", run["run_id"])["state"], "CANCELLED")


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Requires pinned real Lean and independent replay"
)
class ResearchRunRealLeanTests(RunFixture):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT
        cls.store = ResearchRunStore(ROOT)
        project = cls.store.research.create(
            {
                "request_id": rid(),
                "title": "真实 Lean 多节点调度验收",
                "question": "作者构造的调度验收；不是模型生成或原创数学成果。",
            }
        )
        cls.pid = project["project_id"]

    def setUp(self):
        self.run_ids = []
        self.edit(graph())

    def tearDown(self):
        for run_id in self.run_ids:
            if self.store.raw(run_id)["state"] in ("QUEUED", "RUNNING"):
                self.store.cancel(run_id)

    def test_real_three_nodes_run_in_dependency_order_with_replay_and_no_automatic_binding(self):
        run = self.work(self.queue())
        self.assertEqual(run["state"], "COMPLETED", run)
        self.assertEqual([n["math_verdict"] for n in run["nodes"]], ["PROVED"] * 3)
        self.assertEqual(run["used"], {"checks": 3, "worker_seconds_reserved": 90})
        self.assertEqual(
            [e["node_id"] for e in run["events"] if e["kind"] == "NODE_FINISHED"],
            ["l1", "l2", "main"],
        )
        for checked in run["nodes"]:
            source = self.store.research.source(checked["review_id"], "claim")
            self.assertEqual(source["math_verdict"], "PROVED")
            review = self.store.papers.report(checked["review_id"])
            self.assertEqual(review["author_proof_verdict"], "UNKNOWN")
        project = self.store.research.report(self.pid)
        self.assertFalse(
            any(
                binding["state"] == "ACTIVE"
                for binding in project["branches"]["main"]["bindings"].values()
            )
        )
        self.assertEqual(
            project["branches"]["main"]["node_states"]["main"]["math_verdict"], "UNKNOWN"
        )

    def test_real_refuted_prerequisite_skips_successor_and_continues_independent_node(self):
        false_formula = {
            "op": "forall",
            "name": "n",
            "body": {
                "op": "eq",
                "left": {"op": "var", "name": "n"},
                "right": {"op": "num", "value": 0},
            },
        }
        self.edit(
            [
                {**node(expression=false_formula), "statement": "对任意整数n，n等于0。"},
                node("l2", ["l1"]),
                node("main"),
            ]
        )
        run = self.work(
            self.queue(
                plans={
                    "l1": {"direction": "refute", "strategy": "counterexample", "witnesses": [-1]}
                }
            )
        )
        self.assertEqual(
            [n["math_verdict"] for n in run["nodes"]], ["REFUTED", "UNKNOWN", "PROVED"], run
        )
        self.assertEqual(run["nodes"][1]["state"], "SKIPPED")
        self.assertEqual(run["used"]["checks"], 2)

    def test_real_unresolved_strategy_is_unknown_and_cannot_enable_a_successor(self):
        self.edit(
            [
                {**node(expression=formula(0)), "statement": "对任意整数n，n减0小于n。"},
                node("l2", ["l1"]),
            ]
        )
        run = self.work(self.queue(("l1", "l2"), plans={"l1": {**PLAN, "strategy": "simp"}}))
        self.assertEqual(run["nodes"][0]["math_verdict"], "UNKNOWN", run)
        self.assertEqual(run["nodes"][1]["state"], "SKIPPED")

    def test_real_check_and_worker_limits_stop_new_checks_but_keep_finished_evidence(self):
        for budget in ({"max_checks": 1}, {"worker_seconds": 30}):
            with self.subTest(budget=budget):
                run = self.work(self.queue(**budget))
                self.assertEqual(run["state"], "BUDGET_EXHAUSTED", run)
                self.assertEqual(run["used"], {"checks": 1, "worker_seconds_reserved": 30})
                self.assertEqual(run["nodes"][0]["math_verdict"], "PROVED")
                self.assertEqual(run["nodes"][1]["math_verdict"], "UNKNOWN")
                self.assertEqual(run["nodes"][1]["state"], "BUDGET_EXHAUSTED")

    def test_real_wall_deadline_stops_without_publishing_late_math_result(self):
        run = self.work(self.queue(wall_seconds=1))
        self.assertEqual(run["state"], "BUDGET_EXHAUSTED", run)
        self.assert_hidden(run)

    def test_real_gate_result_cancelled_before_publication_is_withdrawn(self):
        queued = self.queue(("l1",))
        original = EvidenceGate.assess
        actual_verdicts = []

        def cancel_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            actual_verdicts.append(result[0].math_verdict.value)
            self.store.cancel(queued["run_id"])
            return result

        with patch.object(EvidenceGate, "assess", cancel_after_gate):
            run = self.work(queued)
        self.assertEqual(actual_verdicts, ["PROVED"])
        self.assertEqual(run["state"], "CANCELLED")
        self.assert_hidden(run)
        self.assertFalse(
            self.store.papers.report(run["nodes"][0]["review_id"])["results_available"]
        )

    def test_real_running_replay_cancel_stops_successors_and_releases_worker_lock(self):
        queued = self.queue()
        original = ControlledReview.checkpoint
        phases = []

        def cancel_replay(child, review_id, key, stage, directory=None):
            original(child, review_id, key, stage, directory)
            phases.append(stage)
            if stage == "REPLAY_RUNNING":
                self.store.cancel(queued["run_id"])

        with patch.object(ControlledReview, "checkpoint", cancel_replay):
            run = self.work(queued)
        self.assertIn("REPLAY_RUNNING", phases)
        self.assertEqual(run["state"], "CANCELLED")
        self.assertEqual(run["used"]["checks"], 1)
        self.assert_hidden(run)
        with self.store.tasks.worker_lock():
            pass

    def test_real_gate_result_after_wall_deadline_is_not_published(self):
        queued = self.queue(("l1",))
        original = EvidenceGate.assess

        def expire_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            self.assertEqual(result[0].math_verdict.value, "PROVED")
            self.store.mutate(queued["run_id"], lambda b: b.update(deadline_monotonic=0))
            return result

        with patch.object(EvidenceGate, "assess", expire_after_gate):
            run = self.work(queued)
        self.assertEqual(run["state"], "BUDGET_EXHAUSTED")
        self.assert_hidden(run)
        self.assertFalse(
            self.store.papers.report(run["nodes"][0]["review_id"])["results_available"]
        )

    def test_real_gate_result_cannot_publish_after_graph_change(self):
        queued = self.queue(("l1",))
        original = EvidenceGate.assess

        def edit_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            self.assertEqual(result[0].math_verdict.value, "PROVED")
            self.edit([{**node(), "notes": "Edited during verification"}])
            return result

        with patch.object(EvidenceGate, "assess", edit_after_gate):
            run = self.work(queued)
        self.assertEqual(run["state"], "INVALIDATED", run)
        self.assert_hidden(run)

    def test_real_completed_artifact_tampering_is_persistently_invalid_even_after_restore(self):
        run = self.work(self.queue(("l1",)))
        review = self.store.papers.report(run["nodes"][0]["review_id"])
        path = Path(review["checks"][0]["result"]["artifact_directory"]) / "report.json"
        original = path.read_bytes()
        try:
            path.write_bytes(b"changed")
            invalid = self.store.report(run["run_id"])
        finally:
            path.write_bytes(original)
        self.assertEqual(invalid["state"], "INVALIDATED")
        self.assert_hidden(self.store.report(run["run_id"]))
        self.assertFalse(
            self.store.papers.report(run["nodes"][0]["review_id"])["results_available"]
        )

    def test_real_external_prerequisite_is_pinned_and_withdrawal_revokes_generated_source(self):
        self.edit([node(), node("l2", ["l1"])])
        first = self.work(self.queue(("l1",)))
        project = self.store.research.report(self.pid)
        data = {
            "expected_version": project["version"],
            "branch_id": "main",
            "node_id": "l1",
            "review_id": first["nodes"][0]["review_id"],
            "key": "claim",
        }
        binding = self.store.research.prepare_binding(self.pid, data)
        self.store.research.bind(
            self.pid,
            {
                **data,
                "request_id": rid(),
                "binding_fingerprint": binding["binding_fingerprint"],
                "reviewer": "repository-graph-execution-test-author",
                "confirmed": True,
            },
        )
        second = self.work(self.queue(("l2",)))
        self.assertEqual(second["nodes"][0]["math_verdict"], "PROVED", second)
        self.store.cancel(first["run_id"])
        self.assertEqual(self.store.report(second["run_id"])["state"], "INVALIDATED")
        self.assert_hidden(self.store.report(second["run_id"]))

    def test_real_process_exit_after_checker_never_republishes_retained_evidence(self):
        queued = self.queue(("l1",))
        code = "from mra.research_runs import ResearchRunStore,ControlledReview;from pathlib import Path;import os,sys;original=ControlledReview.execute\ndef crash(self,*a,**k):\n original(self,*a,**k)\n os._exit(73)\nControlledReview.execute=crash\nResearchRunStore(Path.cwd()).work_once(sys.argv[1])"
        result = subprocess.run(
            [sys.executable, "-c", code, queued["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        child_id = self.store.raw(queued["run_id"])["nodes"][0]["review_id"]
        child = self.store.papers.raw(child_id)
        self.assertEqual(child["checks"][0]["result"]["math_verdict"], "PROVED")
        self.assertEqual(child["state"], "RUNNING")
        self.assertFalse(self.store.papers.report(child_id)["results_available"])
        self.assertEqual(self.store.papers.work_once()["worker_state"], "IDLE")
        self.assertIn(queued["run_id"], self.store.recover()["interrupted"])
        run = self.store.report(queued["run_id"])
        self.assertEqual(run["state"], "INTERRUPTED")
        self.assertEqual(run["used"]["checks"], 1)
        self.assert_hidden(run)
        self.assertFalse(self.store.papers.report(child_id)["results_available"])
