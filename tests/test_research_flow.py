"""Research bindings, real goal observations and incomplete execution boundaries."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.lean_goal_trace import LABELS, MODE, PROGRAM, prepare, read_trace
from mra.research import ResearchStore
from mra.research_flow import (
    SOURCE_FILES,
    FlowStrategyChecks,
    ResearchFlowStore,
    atomic,
    example,
    graph,
    lease,
)
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_research import graph as parent_graph
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "flow-qa-" + uuid.uuid4().hex


def parent(root):
    return ResearchStore(root).create(
        {
            "request_id": rid(),
            "title": "研究路线与真实 Lean 状态验收",
            "question": "在完整前提下，独立检查每条引理与原问题；失败保持未知。",
        }
    )


def create(store, project, nodes=None):
    value = {"project_id": project["project_id"], "branch_id": "main", "nodes": nodes or example()}
    plan = store.preview(value)
    return store.create(
        {**value, "request_id": rid(), "plan_fingerprint": plan["plan_fingerprint"]}
    )


def single(false=False):
    node = deepcopy(example()[-1])
    node["depends_on"] = []
    if false:
        node["target"].update(
            title="错误猜想：所有自然数相等",
            original="对任意自然数 a、b，都有 a=b。",
            variables=["a", "b"],
            assumptions=[],
            goal={"eq": [{"var": "a"}, {"var": "b"}]},
        )
    return [node]


def proposal(store, flow, node="problem", route="goal-trace"):
    return store.propose(flow["flow_id"], {"request_id": rid(), "node_id": node, "route": route})


def confirmation(p):
    return {
        "request_id": rid(),
        "proposal_id": p["proposal_id"],
        "proposal_fingerprint": p["proposal_fingerprint"],
        "reviewer": "Research flow QA",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in SOURCE_FILES:
            shutil.copyfile(ROOT / name, self.root / name)
        self.project = parent(self.root)
        self.store = ResearchFlowStore(self.root)
        self.flow = create(self.store, self.project)
        self.key = self.flow["flow_id"]

    def test_full_graph_is_immutable_unknown_and_has_no_child_ledger(self):
        self.assertEqual(self.flow["plan"]["nodes"], example())
        self.assertEqual(self.flow["math_verdict"], "UNKNOWN")
        self.assertEqual(self.flow["node_states"]["problem"]["blocked_by"], ["sum", "gcd"])
        self.assertEqual(list(self.store.directory(self.key).glob("attempt-*")), [])
        self.assertEqual(self.flow["plan"]["budget"]["maximum_total_worker_seconds"], 480)

    def test_graph_rejects_cycles_missing_unrelated_duplicate_and_multiple_problems(self):
        cases = []
        n = example()
        n[0]["depends_on"] = ["problem"]
        cases.append(n)
        n = example()
        n[-1]["depends_on"] = ["missing"]
        cases.append(n)
        n = example()
        n[-1]["depends_on"] = ["sum"]
        cases.append(n)
        n = example()
        n[1]["id"] = "sum"
        cases.append(n)
        n = example()
        n[0]["kind"] = "problem"
        cases.append(n)
        cases.extend([[], example() * 3])
        for nodes in cases:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                graph(nodes)

    def test_arbitrary_tactic_domain_proof_or_program_is_rejected(self):
        target = {**single()[0]["target"], "schema": MODE, "program": PROGRAM}
        for change in (
            {"proof": "by sorry"},
            {"imports": ["Mathlib"]},
            {"program": "intros; sorry"},
            {"schema": "Int"},
            {"timeout_seconds": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare(self.root, {**target, **change})

    def test_goal_parser_ignores_quoted_source_but_rejects_missing_duplicate_or_fake_data(self):
        records = [
            {"label": label, "goals": [{"mvar": str(i), "pretty": "⊢ Nat"} for i in range(count)]}
            for label, count in zip(LABELS, (1, 1, 2, 0))
        ]
        log = "\n".join("MRA_GOALS:" + json.dumps(r) for r in records)
        value = read_trace(
            log + '\n    Lean.logInfo m! "MRA_GOALS:{payload.compress}"` does nothing.'
        )
        self.assertEqual(value["checkpoints"], records)
        for bad in (
            log.splitlines()[0],
            log + "\n" + log,
            'MRA_GOALS:{"label":"before","goals":"fake"}',
        ):
            with self.assertRaises(ValueError):
                read_trace(bad)

    def test_proposals_do_not_execute_or_create_child_ledgers_and_preserve_original_premises(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            for route in ("proof-search", "omega", "simp-gcd", "counterexample", "goal-trace"):
                p = proposal(self.store, self.flow, route=route)
                self.assertEqual((p["math_verdict"], p["evidence_level"]), ("UNKNOWN", "E0"))
                if p["child_plan"] and route != "counterexample":
                    self.assertEqual(
                        p["child_plan"]["request"]["assumptions"],
                        example()[-1]["target"]["assumptions"],
                    )
                    self.assertEqual(p["child_plan"]["formal_statement"].count("→"), 3)
        self.assertEqual(list(self.store.directory(self.key).glob("attempt-*")), [])

    def test_numeric_witness_plan_has_negated_original_direction_and_stays_unknown(self):
        flow = create(self.store, self.project, single(True))
        p = proposal(self.store, flow, route="counterexample")
        self.assertIsNotNone(p["child_plan"])
        self.assertEqual(p["child_plan"]["math_verdict"], "UNKNOWN")
        self.assertIn("¬", p["child_plan"]["formal_statement"])
        self.assertEqual(self.store.report(flow["flow_id"])["math_verdict"], "UNKNOWN")

    def test_named_confirmation_exact_fingerprint_and_prerequisites_are_required(self):
        p = proposal(self.store, self.flow, node="sum", route="proof-search")
        raw = confirmation(p)
        for change in (
            {"confirmed": False},
            {"confirmed": 1},
            {"reviewer": " "},
            {"proposal_fingerprint": "stale"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.submit(self.key, {**raw, **change})
        p = proposal(self.store, self.flow)
        with self.assertRaises(ValueError):
            self.store.submit(self.key, confirmation(p))
        self.assertEqual(self.store.read(self.key)["attempts"], [])

    def test_submission_idempotency_one_active_and_queued_cancellation(self):
        p = proposal(self.store, self.flow, "sum", "omega")
        raw = confirmation(p)
        a = self.store.submit(self.key, raw)
        self.assertEqual(a, self.store.submit(self.key, raw))
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "reviewer": "Different"})
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "request_id": rid()})
        checked = self.store.cancel(self.key, a["attempt_id"])
        self.assertEqual(checked["attempts"][0]["state"], "CANCELLED")
        self.assertFalse((self.store.directory(self.key) / a["attempt_id"]).exists())
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_proposal_idempotency_and_conflicting_input(self):
        raw = {"request_id": rid(), "node_id": "sum", "route": "proof-search"}
        first = self.store.propose(self.key, raw)
        self.assertEqual(first, self.store.propose(self.key, raw))
        with self.assertRaises(ValueError):
            self.store.propose(self.key, {**raw, "route": "omega"})

    def test_record_and_plan_corruption_remain_invalid_after_original_bytes_restored(self):
        for name in ("record.json", "plan.json"):
            flow = create(self.store, self.project)
            path = self.store.directory(flow["flow_id"]) / name
            before = path.read_bytes()
            path.write_text('{"broken":true}')
            with self.assertRaises((ValueError, KeyError)):
                self.store.report(flow["flow_id"])
            path.write_bytes(before)
            checked = self.store.report(flow["flow_id"])
            self.assertTrue(checked["invalidated"])
            self.assertEqual(checked["math_verdict"], "UNKNOWN")

    def test_source_drift_is_persistent_and_prevents_claim_even_after_restoration(self):
        p = proposal(self.store, self.flow, "sum", "omega")
        self.store.submit(self.key, confirmation(p))
        path = self.root / "mra/lean_goal_trace.py"
        before = path.read_bytes()
        path.write_bytes(before + b"\n# drift\n")
        with patch.object(self.store, "execute", side_effect=AssertionError("No execution")):
            self.store.work_once()
        path.write_bytes(before)
        checked = self.store.report(self.key)
        self.assertTrue(checked["invalidated"])
        self.assertEqual(checked["attempts"][0]["state"], "POLICY_BLOCKED")

    def test_parent_edit_before_claim_blocks_execution(self):
        self.store.submit(self.key, confirmation(proposal(self.store, self.flow, "sum", "omega")))
        ResearchStore(self.root).edit(
            self.project["project_id"],
            {
                "request_id": rid(),
                "expected_version": self.project["version"],
                "branch_id": "main",
                "nodes": parent_graph(),
            },
        )
        with patch.object(self.store, "execute", side_effect=AssertionError("No execution")):
            self.store.work_once()
        checked = self.store.report(self.key)
        self.assertTrue(checked["invalidated"])
        self.assertEqual(checked["attempts"][0]["state"], "POLICY_BLOCKED")

    def test_transient_parent_busy_does_not_invalidate(self):
        with patch.object(
            self.store, "parent", side_effect=sqlite3.OperationalError("database is locked")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.report(self.key)
        self.assertFalse(self.store.report(self.key)["invalidated"])
        self.assertFalse((self.store.directory(self.key) / "invalidated.json").exists())

    def test_corrupt_record_does_not_prevent_other_tasks_from_being_saved_or_worker_idle(self):
        path = self.store.directory(self.key) / "record.json"
        path.write_text("broken")
        other = create(self.store, self.project, single())
        self.assertFalse(other["invalidated"])
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        catalog = self.store.catalog(self.project["project_id"], "main")
        self.assertEqual([r["flow_id"] for r in catalog["flows"]], [other["flow_id"]])

    def test_locks_and_redirected_storage_fail_closed(self):
        with lease(self.store.directory(self.key)), self.assertRaises(WorkerBusy):
            self.store.report(self.key)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once()
        path = self.store.directory(self.key) / "record.json"
        other = self.root / "outside.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.store.report(self.key)

    def test_recovery_cancels_orphan_queued_child_and_never_promotes(self):
        p = proposal(self.store, self.flow, "sum", "omega")
        a = self.store.submit(self.key, confirmation(p))
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        directory = self.store.directory(self.key) / a["attempt_id"]
        directory.mkdir()
        checker = FlowStrategyChecks(self.root, directory, create=True)
        child = checker.submit(
            {
                "request_id": a["attempt_id"],
                "request": p["child_plan"]["request"],
                "plan_fingerprint": p["child_plan"]["plan_fingerprint"],
                "reviewer": "QA",
                "confirmed": True,
            }
        )
        recovered = self.store.recover()
        self.assertIn(a["attempt_id"], recovered["interrupted"])
        self.assertEqual(checker.report(child["run_id"])["state"], "CANCELLED")
        self.assertEqual(self.store.report(self.key)["attempts"][0]["state"], "INTERRUPTED")
        with patch.object(self.store, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_save_failure_does_not_publish_partial_record(self):
        path = self.store.directory(self.key) / "record.json"
        before = path.read_bytes()
        with (
            patch("mra.research_flow.os.fsync", side_effect=OSError("disk fault")),
            self.assertRaises(OSError),
        ):
            atomic(path, {"changed": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".pending-*")), [])

    def test_http_static_routes_csrf_exact_input_no_get_execution_and_cancellation(self):
        with running_server(self.root) as server:
            for route in (
                "/research",
                "/research-flow.js",
                "/research-flow.css",
                "/v1/research-flows/projects/" + self.project["project_id"] + "/main",
                "/v1/research-flows/" + self.key,
            ):
                self.assertEqual(request(server, "GET", route)[0], 200)
            endpoint = "/v1/research-flows/" + self.key
            raw = {"request_id": rid(), "node_id": "sum", "route": "omega"}
            self.assertEqual(
                request(
                    server, "POST", endpoint + "/propose", raw, headers={"X-MRA-CSRF": "wrong"}
                )[0],
                403,
            )
            status, p, _ = request(server, "POST", endpoint + "/propose", raw)
            self.assertEqual(status, 200)
            self.assertEqual(request(server, "GET", endpoint + "?execute=1")[0], 400)
            status, a, _ = request(server, "POST", endpoint + "/submit", confirmation(p))
            self.assertEqual(status, 202)
            status, row, _ = request(
                server, "POST", endpoint + "/cancel", {"attempt_id": a["attempt_id"]}
            )
            self.assertEqual(status, 200)
            self.assertEqual(row["attempts"][0]["state"], "CANCELLED")

    def test_cli_reads_strict_json_and_report_without_execution(self):
        from mra import __main__ as cli

        for arguments in (
            ["research-flow-report", self.key],
            ["research-flow-work"],
            ["research-flow-recover"],
        ):
            out = io.StringIO()
            with (
                patch.object(cli, "ROOT", self.root),
                patch.object(sys, "argv", ["mra", *arguments]),
                patch("sys.stdout", out),
            ):
                self.assertEqual(cli.main(), 0)
            self.assertIsInstance(json.loads(out.getvalue()), dict)
        path = self.root / "bad.json"
        path.write_text('{"a":1,"a":2}')
        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(sys, "argv", ["mra", "research-flow-preview", "--input", str(path)]),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli.main(), 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ROOT / "artifacts/research-flow-20261027-01" / ("real-suite-" + uuid.uuid4().hex)
        cls.out.mkdir()
        cls.project = parent(ROOT)
        cls.store = ResearchFlowStore(ROOT)
        (cls.out / "project.json").write_text(
            json.dumps(cls.project, ensure_ascii=False, indent=2) + "\n"
        )

    def save(self, name, row):
        (self.out / (name + ".json")).write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n"
        )

    def run_route(self, flow, node="problem", route="goal-trace"):
        p = proposal(self.store, flow, node, route)
        a = self.store.submit(flow["flow_id"], confirmation(p))
        self.store.work_once()
        row = self.store.report(flow["flow_id"])
        self.assertEqual(row["attempts"][-1]["attempt_id"], a["attempt_id"])
        return row

    def test_chain_failed_route_actual_subgoals_and_independent_root_proof(self):
        flow = create(self.store, self.project)
        for node in ("sum", "gcd"):
            flow = self.run_route(flow, node, "proof-search")
            self.assertEqual(flow["node_states"][node]["math_verdict"], "PROVED")
            self.assertEqual(flow["math_verdict"], "UNKNOWN")
            self.save("after-" + node, flow)
        flow = self.run_route(flow, route="omega")
        self.assertEqual(
            (flow["attempts"][-1]["state"], flow["math_verdict"]), ("REJECTED", "UNKNOWN")
        )
        self.save("failed-root-route", flow)
        flow = self.run_route(flow)
        self.assertEqual((flow["math_verdict"], flow["evidence_level"]), ("PROVED", "E4"))
        self.assertEqual(
            [len(c["goals"]) for c in flow["attempts"][-1]["goal_trace"]["checkpoints"]],
            [1, 1, 2, 0],
        )
        for child in flow["checks"].values():
            if child["math_verdict"] == "PROVED":
                self.assertTrue(child["alignment"]["confirmed"])
                self.assertTrue(child["result"]["candidate"]["audit"]["type_matches"])
                self.assertEqual(
                    child["result"]["replay"]["audit"]["mode"], "data-only-kernel-replay-v1"
                )
        self.save("chain-proved", flow)

    def test_numeric_witness_needs_real_negation_proof_to_refute_original(self):
        flow = create(self.store, self.project, single(True))
        row = self.run_route(flow, route="counterexample")
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("REFUTED", "E4"))
        child = row["checks"][row["attempts"][-1]["attempt_id"]]
        self.assertEqual(child["math_verdict"], "PROVED")
        self.assertIn("¬", child["plan"]["formal_statement"])
        self.save("refuted", row)

    def test_cancel_after_actual_candidate_keeps_unknown(self):
        flow = create(self.store, self.project, single())
        a = self.store.submit(flow["flow_id"], confirmation(proposal(self.store, flow)))
        original = NativeLeanBackend._run_worker

        def cancel(backend, *args, **kwargs):
            result = original(backend, *args, **kwargs)
            if kwargs.get("phase") == "LEAN_RUNNING":
                self.assertEqual(result["returncode"], 0)
                self.store.cancel(flow["flow_id"], a["attempt_id"])
            return result

        with patch.object(NativeLeanBackend, "_run_worker", cancel):
            self.store.work_once()
        row = self.store.report(flow["flow_id"])
        self.assertEqual(
            (row["attempts"][-1]["state"], row["math_verdict"]), ("CANCELLED", "UNKNOWN")
        )
        self.assertIsNone(row["checks"][a["attempt_id"]]["result"]["replay"])
        self.save("cancelled", row)

    def test_supervisor_exit_after_actual_replay_never_promotes_or_retries(self):
        flow = create(self.store, self.project, single())
        a = self.store.submit(flow["flow_id"], confirmation(proposal(self.store, flow)))
        script = """import os
from pathlib import Path
from unittest.mock import patch
from mra.research_flow import ResearchFlowStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    if kwargs.get('phase')=='REPLAY_RUNNING':
        assert result['returncode']==0,result
        os._exit(73)
    return result
with patch.object(NativeLeanBackend,'_run_worker',stop):
    ResearchFlowStore(Path.cwd()).work_once()
"""
        process = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=240
        )
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        with patch.object(self.store, "execute", side_effect=AssertionError("No retry")):
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        row = self.store.report(flow["flow_id"])
        self.assertEqual(
            (row["attempts"][-1]["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        child_path = (
            self.store.directory(flow["flow_id"])
            / a["attempt_id"]
            / "checks/runs"
            / row["attempts"][-1]["run_id"]
        )
        self.assertIn("MRA_REPLAY:", (child_path / "replay/stdout.log").read_text())
        self.save("interrupted-after-replay", row)

    def test_withdrawal_and_artifact_restoration_do_not_revive_binding(self):
        flow = create(self.store, self.project, single())
        row = self.run_route(flow)
        a = row["attempts"][-1]
        self.assertEqual(row["math_verdict"], "PROVED")
        self.save("before-artifact-drift", row)
        path = (
            self.store.directory(flow["flow_id"])
            / a["attempt_id"]
            / "checks/runs"
            / a["run_id"]
            / "candidate/stdout.log"
        )
        before = path.read_bytes()
        path.write_bytes(before + b"\nchanged\n")
        self.assertEqual(self.store.report(flow["flow_id"])["math_verdict"], "UNKNOWN")
        path.write_bytes(before)
        row = self.store.report(flow["flow_id"])
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertTrue(row["attempts"][-1]["invalidated"])
        self.save("artifact-restored-still-invalid", row)

    def test_withdrawn_prerequisite_invalidates_dependent_research_binding_only(self):
        flow = create(self.store, self.project)
        for node in ("sum", "gcd"):
            flow = self.run_route(flow, node, "proof-search")
        flow = self.run_route(flow)
        self.assertEqual(flow["math_verdict"], "PROVED")
        self.save("before-withdrawal", flow)
        row = self.store.withdraw(
            flow["flow_id"],
            {
                "attempt_id": flow["attempts"][0]["attempt_id"],
                "reviewer": "Research flow QA",
                "reason": "Exercise prerequisite withdrawal",
                "confirmed": True,
            },
        )
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertTrue(row["attempts"][-1]["invalidated"])
        self.assertEqual(row["checks"][row["attempts"][-1]["attempt_id"]]["math_verdict"], "PROVED")
        self.save("withdrawn", row)
