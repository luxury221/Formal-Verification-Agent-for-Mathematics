"""Dependency scheduling contracts and separately enabled real Lean evidence."""

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

from mra.domain import digest
from mra.research_continuation import ResearchContinuationStore
from mra.research_flow import SOURCE_FILES, atomic, check_type
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_proof_steps import create as create_scope, submission as step_submission
from test_research_flow import create as create_flow, parent, single
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "continuation-qa-" + uuid.uuid4().hex


def chain():
    first = deepcopy(single()[0])
    first.update(id="seed", kind="lemma", depends_on=[])
    first["target"].update(
        title="自然数自反性",
        original="每个自然数a都有a≤a。",
        variables=["a"],
        assumptions=[],
        goal={"le": [{"var": "a"}, {"var": "a"}]},
    )
    middle = deepcopy(first)
    middle.update(id="middle", depends_on=["seed"])
    middle["target"].update(
        title="加一保持自反",
        original="每个自然数a都有a+1≤a+1。",
        goal={"le": [{"add": [{"var": "a"}, {"nat": 1}]}, {"add": [{"var": "a"}, {"nat": 1}]}]},
    )
    problem = deepcopy(first)
    problem.update(id="problem", kind="problem", depends_on=["middle"])
    problem["target"].update(
        title="加二保持自反",
        original="每个自然数a都有a+2≤a+2。",
        goal={"le": [{"add": [{"var": "a"}, {"nat": 2}]}, {"add": [{"var": "a"}, {"nat": 2}]}]},
    )
    return [first, middle, problem]


def create(store, flow):
    return store.create(
        {
            "flow_id": flow["flow_id"],
            "request_id": rid(),
            "plan_fingerprint": store.preview(flow["flow_id"])["plan_fingerprint"],
        }
    )


def submission(store, flow_id, node="middle", route="omega"):
    p = store.propose(flow_id, {"node_id": node, "route": route, "request_id": rid()})
    return {
        "request_id": rid(),
        "proposal_id": p["proposal_id"],
        "proposal_fingerprint": p["proposal_fingerprint"],
        "reviewer": "Continuation QA",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (
            *SOURCE_FILES,
            "mra/proof_steps.py",
            "mra/research_evidence.py",
            "mra/research_continuation.py",
        ):
            shutil.copyfile(ROOT / name, self.root / name)
        self.store = ResearchContinuationStore(self.root)
        self.project = parent(self.root)
        self.flow = create_flow(self.store.links.flows, self.project, chain())
        self.key = self.flow["flow_id"]
        self.evidence = self.store.links.report(self.key)
        original_report = self.store.links.report
        self.mock = patch.object(
            self.store.links,
            "report",
            side_effect=lambda key: deepcopy(self.evidence)
            if key == self.key
            else original_report(key),
        )
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.children = {}
        children = self.children

        class ContractChecker:
            def __init__(self, *args, **kwargs):
                pass

            def report(self, run_id):
                return deepcopy(children[run_id])

        self.provider = patch("mra.research_continuation.check_type", return_value=ContractChecker)
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def seed(self, key="contract-binding"):
        self.evidence["node_evidence"]["seed"]["active_bindings"] = [key]
        self.evidence["source_evidence"][key] = {"synthetic-contract-only": key}

    def complete(self, node):
        raw = submission(self.store, self.key, node)
        a = self.store.submit(self.key, raw)
        row = self.store.read(self.key)
        a = next(v for v in row["attempts"] if v["attempt_id"] == a["attempt_id"])
        run_id = rid()
        child = {
            "plan": row["proposals"][a["proposal_id"]]["child_plan"],
            "run_id": run_id,
            "state": "COMPLETED",
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "invalidated": False,
            "alignment": {"confirmed": True},
            "files": {},
            "result": {"contract-only": True},
        }
        self.children[run_id] = child
        a.update(
            state="COMPLETED",
            run_id=run_id,
            source_fingerprint=digest(
                {k: child[k] for k in ("plan", "alignment", "files", "result")}
            ),
        )
        self.store.save(row)
        return raw, a

    def test_preview_save_and_read_do_not_execute_or_promote_problem(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            self.assertFalse(self.store.report(self.key)["created"])
            self.assertFalse(self.store.directory(self.key).exists())
            row = create(self.store, self.flow)
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(row["node_states"]["middle"]["blocked_by"], ["seed"])
        self.assertEqual(list(self.store.directory(self.key).glob("attempt-*")), [])

    def test_linked_seed_unlocks_only_its_immediate_successor(self):
        create(self.store, self.flow)
        with self.assertRaises(ValueError):
            submission(self.store, self.key)
        self.seed()
        row = self.store.report(self.key)
        self.assertEqual(row["node_states"]["middle"]["status"], "READY")
        self.assertEqual(row["node_states"]["problem"]["status"], "BLOCKED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_full_target_unchanged_and_no_dependency_assumption_injected(self):
        self.seed()
        create(self.store, self.flow)
        p = self.store.propose(
            self.key, {"node_id": "middle", "route": "omega", "request_id": rid()}
        )
        self.assertEqual(p["child_plan"]["request"]["assumptions"], [])
        self.assertEqual(p["child_plan"]["request"]["goal"], chain()[1]["target"]["goal"])
        self.assertEqual(p["prerequisite_sources"]["seed"]["origin"], "PREFIX_LINK")
        self.assertEqual(p["math_verdict"], "UNKNOWN")

    def test_named_confirmation_and_exact_plan_guards(self):
        self.seed()
        create(self.store, self.flow)
        raw = submission(self.store, self.key)
        for change in (
            {"reviewer": ""},
            {"confirmed": 1},
            {"confirmed": False},
            {"proposal_fingerprint": "changed"},
            {"extra": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.submit(self.key, {**raw, **change})
        a = self.store.submit(self.key, raw)
        self.assertEqual(a, self.store.submit(self.key, raw))
        with self.assertRaises(ValueError):
            self.store.submit(self.key, {**raw, "reviewer": "Changed"})
        with self.assertRaises(ValueError):
            self.store.submit(self.key, submission(self.store, self.key))

    def test_changed_selected_source_rejects_a_stale_confirmation(self):
        self.seed()
        create(self.store, self.flow)
        raw = submission(self.store, self.key)
        self.seed("replacement")
        with self.assertRaises(ValueError):
            self.store.submit(self.key, raw)
        self.assertEqual(self.store.report(self.key)["attempts"], [])

    def test_queued_source_withdrawal_is_blocked_without_execution(self):
        self.seed()
        create(self.store, self.flow)
        a = self.store.submit(self.key, submission(self.store, self.key))
        self.evidence["node_evidence"]["seed"]["active_bindings"] = []
        with patch.object(self.store, "execute", side_effect=AssertionError("No execution")):
            self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        row = self.store.report(self.key)
        self.assertEqual(row["attempts"][0]["state"], "POLICY_BLOCKED")
        self.assertTrue(row["attempts"][0]["invalidated"])
        self.assertEqual(row["attempts"][0]["attempt_id"], a["attempt_id"])

    def test_multilevel_withdrawal_and_restoration_do_not_revive_descendants(self):
        self.seed()
        create(self.store, self.flow)
        raw, _ = self.complete("middle")
        self.complete("problem")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "PROVED")
        self.evidence["node_evidence"]["seed"]["active_bindings"] = []
        row = self.store.report(self.key)
        self.assertTrue(all(a["invalidated"] for a in row["attempts"]))
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.seed()
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")
        self.assertTrue(self.store.submit(self.key, raw)["invalidated"])

    def test_adding_unused_link_does_not_invalidate_selected_provenance(self):
        self.seed()
        create(self.store, self.flow)
        self.complete("middle")
        self.evidence["node_evidence"]["seed"]["active_bindings"].append("unused")
        self.evidence["source_evidence"]["unused"] = {"contract-only": "alternative"}
        self.assertFalse(self.store.report(self.key)["attempts"][0]["invalidated"])

    def test_native_refutation_and_linked_proof_conflict_blocks_successors(self):
        self.seed()
        create(self.store, self.flow)
        self.evidence["research_task"]["node_states"]["seed"].update(
            math_verdict="REFUTED", binding_fingerprint="negative"
        )
        row = self.store.report(self.key)
        self.assertEqual(row["node_states"]["seed"]["status"], "CONFLICT")
        self.assertEqual(row["node_states"]["middle"]["blocked_by"], ["seed"])

    def test_withdraw_middle_cascades_and_retains_child_proof(self):
        self.seed()
        create(self.store, self.flow)
        _, a = self.complete("middle")
        self.complete("problem")
        self.store.withdraw(
            self.key,
            {
                "attempt_id": a["attempt_id"],
                "reviewer": "QA",
                "reason": "Withdraw",
                "confirmed": True,
            },
        )
        self.assertTrue(all(a["invalidated"] for a in self.store.report(self.key)["attempts"]))
        self.assertTrue(all(c["math_verdict"] == "PROVED" for c in self.children.values()))

    def test_busy_is_transient_and_source_program_drift_is_persistent(self):
        self.seed()
        create(self.store, self.flow)
        self.complete("middle")
        p = self.store.directory(self.key) / "record.json"
        before = p.read_bytes()
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.store.links, "report", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.store.report(self.key)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse((p.parent / "invalidated.json").exists())
            recovered = self.store.report(self.key)
            self.assertFalse(recovered["invalidated"])
            self.assertEqual(recovered["node_states"]["middle"]["math_verdict"], "PROVED")
            self.assertEqual(p.read_bytes(), before)
        source = self.root / "mra/research_continuation.py"
        old = source.read_bytes()
        source.write_bytes(old + b"\n# drift\n")
        self.assertTrue(self.store.report(self.key)["invalidated"])
        source.write_bytes(old)
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_corruption_restoration_and_symlink_fail_closed(self):
        self.seed()
        create(self.store, self.flow)
        p = self.store.directory(self.key) / "record.json"
        old = p.read_bytes()
        p.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        p.write_bytes(old)
        self.assertTrue(self.store.report(self.key)["invalidated"])
        outside = self.root / "outside.json"
        outside.write_bytes(old)
        p.unlink()
        p.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.report(self.key)

    def test_damaged_plan_and_orphan_database_do_not_block_unrelated_records(self):
        self.seed()
        create(self.store, self.flow)
        self.store.submit(self.key, submission(self.store, self.key))
        p = self.store.directory(self.key) / "plan.json"
        old = p.read_bytes()
        p.write_text("broken")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        p.write_bytes(old)
        self.assertTrue(self.store.report(self.key)["invalidated"])
        other = create_flow(self.store.links.flows, self.project, single())
        create(self.store, other)
        self.assertFalse(self.store.report(other["flow_id"])["invalidated"])

    def test_recovery_marks_running_interrupted_without_automatic_promotion(self):
        self.seed()
        create(self.store, self.flow)
        raw = submission(self.store, self.key)
        a = self.store.submit(self.key, raw)
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        self.assertEqual(self.store.recover()["interrupted"], [a["attempt_id"]])
        self.assertEqual(self.store.submit(self.key, raw)["state"], "INTERRUPTED")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_publication_and_global_worker_locks(self):
        original = self.store.save

        def save(row):
            if not (self.store.directory(row["flow_id"]) / "record.json").exists():
                with self.assertRaises(WorkerBusy):
                    list(ResearchContinuationStore(self.root).records())
            original(row)

        with patch.object(self.store, "save", save):
            create(self.store, self.flow)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.work_once()

    def test_cancelled_queue_keeps_identity_and_has_no_child_run(self):
        self.seed()
        create(self.store, self.flow)
        raw = submission(self.store, self.key)
        a = self.store.submit(self.key, raw)
        row = self.store.cancel(self.key, a["attempt_id"])
        self.assertEqual(row["attempts"][0]["state"], "CANCELLED")
        self.assertEqual(self.store.submit(self.key, raw)["state"], "CANCELLED")
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        self.assertIsNone(row["attempts"][0]["run_id"])

    def test_corrupt_orphan_database_is_interrupted_and_isolated(self):
        self.seed()
        create(self.store, self.flow)
        a = self.store.submit(self.key, submission(self.store, self.key))
        row = self.store.read(self.key)
        row["attempts"][0]["state"] = "RUNNING"
        self.store.save(row)
        p = self.store.directory(self.key) / a["attempt_id"] / "checks/lab.sqlite3"
        p.parent.mkdir(parents=True)
        p.write_text("broken sqlite")
        with patch("mra.research_continuation.check_type", side_effect=check_type):
            self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        row = self.store.report(self.key)
        self.assertTrue(row["attempts"][0]["invalidated"])
        self.assertEqual(row["attempts"][0]["state"], "INTERRUPTED")
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_malformed_attempt_record_is_quarantined_before_worker_scans_it(self):
        self.seed()
        create(self.store, self.flow)
        row = self.store.read(self.key)
        row["attempts"] = [{"proposal_id": "missing"}]
        atomic(self.store.directory(self.key) / "record.json", row)
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        self.assertTrue((self.store.directory(self.key) / "invalidated.json").exists())

    def test_http_csrf_strict_routes_and_readonly_get(self):
        with running_server(self.root) as server:
            endpoint = "/v1/research-continuations/" + self.key
            for route in (endpoint, "/research-continuation.js", "/research-continuation.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(request(server, "GET", endpoint + "?execute=1")[0], 400)
            self.assertEqual(request(server, "POST", endpoint + "/preview", {})[0], 200)
            self.assertEqual(
                request(server, "POST", endpoint + "/preview", {}, headers={"X-MRA-CSRF": "wrong"})[
                    0
                ],
                403,
            )
            self.assertEqual(
                request(server, "POST", endpoint + "/create", {"flow_id": "different"})[0], 400
            )
            self.assertFalse(self.store.directory(self.key).exists())

    def test_cli_readonly_preview_and_duplicate_json_rejection(self):
        from mra import __main__ as cli

        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(sys, "argv", ["mra", "research-continuation-preview", self.key]),
            patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["math_verdict"], "UNKNOWN")
        p = self.root / "input.json"
        p.write_text('{"x":1,"x":2}')
        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(
                sys, "argv", ["mra", "research-continuation-submit", self.key, "--input", str(p)]
            ),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli.main(), 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = ResearchContinuationStore(ROOT)
        cls.out = (
            ROOT
            / "artifacts/research-continuation-20261027-01"
            / ("real-suite-" + uuid.uuid4().hex)
        )
        cls.out.mkdir()

    def save(self, name, value):
        (self.out / (name + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def seed_flow(self):
        project = parent(ROOT)
        flow = create_flow(self.store.links.flows, project, chain())
        create(self.store, flow)
        with self.assertRaises(ValueError):
            submission(self.store, flow["flow_id"])
        steps = self.store.links.steps
        scope = create_scope(steps, flow, "seed")
        key = scope["scope_id"]
        steps.submit(key, step_submission(steps, key))
        steps.work_once()
        opened = steps.report(key)
        steps.submit(
            key, step_submission(steps, key, opened["attempts"][-1]["attempt_id"], "omega")
        )
        steps.work_once()
        scope = steps.report(key)
        self.assertEqual(scope["math_verdict"], "PROVED")
        raw = {
            "node_id": "seed",
            "scope_id": key,
            "attempt_id": scope["attempts"][-1]["attempt_id"],
        }
        preview = self.store.links.preview(flow["flow_id"], raw)
        linked = self.store.links.bind(
            flow["flow_id"],
            {
                **raw,
                "plan_fingerprint": preview["plan_fingerprint"],
                "request_id": rid(),
                "reviewer": "Real continuation QA",
                "confirmed": True,
            },
        )
        self.assertEqual(linked["math_verdict"], "UNKNOWN")
        return flow, scope, linked

    def run_node(self, flow, node):
        a = self.store.submit(flow["flow_id"], submission(self.store, flow["flow_id"], node))
        self.store.work_once()
        row = self.store.report(flow["flow_id"])
        self.assertEqual(
            next(v for v in row["attempts"] if v["attempt_id"] == a["attempt_id"])["state"],
            "COMPLETED",
        )
        self.assertEqual(row["node_states"][node]["math_verdict"], "PROVED")
        return row

    def test_real_link_unlocks_two_successive_independent_proofs_and_retains_old_routes(self):
        flow, scope, linked = self.seed_flow()
        middle = self.run_node(flow, "middle")
        self.assertEqual(middle["math_verdict"], "UNKNOWN")
        row = self.run_node(flow, "problem")
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("PROVED", "E4"))
        for a in row["attempts"]:
            c = row["checks"][a["attempt_id"]]
            self.assertEqual(c["plan"]["request"]["assumptions"], [])
            self.assertEqual(c["result"]["replay"]["audit"]["mode"], "data-only-kernel-replay-v1")
        self.assertEqual(self.store.links.flows.report(flow["flow_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.links.steps.report(scope["scope_id"]), scope)
        self.save("proved", row)
        self.save("seed-scope", scope)
        self.save("seed-linked", linked)

    def test_real_withdrawal_cascades_and_relink_never_revives_prior_checks(self):
        flow, scope, linked = self.seed_flow()
        self.run_node(flow, "middle")
        row = self.run_node(flow, "problem")
        binding = linked["bindings"][-1]
        self.store.links.withdraw(
            flow["flow_id"],
            {
                "binding_id": binding["binding_id"],
                "request_id": rid(),
                "reviewer": "QA",
                "reason": "Real dependency withdrawal",
                "confirmed": True,
            },
        )
        withdrawn = self.store.report(flow["flow_id"])
        self.assertTrue(all(a["invalidated"] for a in withdrawn["attempts"]))
        self.assertEqual(withdrawn["math_verdict"], "UNKNOWN")
        raw = {
            "node_id": "seed",
            "scope_id": scope["scope_id"],
            "attempt_id": scope["attempts"][-1]["attempt_id"],
        }
        p = self.store.links.preview(flow["flow_id"], raw)
        self.store.links.bind(
            flow["flow_id"],
            {
                **raw,
                "plan_fingerprint": p["plan_fingerprint"],
                "request_id": rid(),
                "reviewer": "QA",
                "confirmed": True,
            },
        )
        restored = self.store.report(flow["flow_id"])
        self.assertEqual(restored["math_verdict"], "UNKNOWN")
        self.assertEqual(restored["node_states"]["middle"]["status"], "READY")
        self.assertTrue(all(c["math_verdict"] == "PROVED" for c in restored["checks"].values()))
        self.save("withdrawn", withdrawn)
        self.save("relinked-still-invalid", restored)
        self.save("withdrawn-before", row)

    def test_successful_raw_replay_then_supervisor_exit_stays_interrupted(self):
        flow, scope, linked = self.seed_flow()
        a = self.store.submit(flow["flow_id"], submission(self.store, flow["flow_id"]))
        script = """import os
from pathlib import Path
from unittest.mock import patch
from mra.research_continuation import ResearchContinuationStore
from mra.verifier import NativeLeanBackend
original=NativeLeanBackend._run_worker
def stop(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    if kwargs.get('phase')=='REPLAY_RUNNING':
        assert result['returncode']==0,result
        os._exit(73)
    return result
with patch.object(NativeLeanBackend,'_run_worker',stop):
    ResearchContinuationStore(Path.cwd()).work_once()
"""
        process = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=240
        )
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertIn(a["attempt_id"], self.store.recover()["interrupted"])
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        row = self.store.report(flow["flow_id"])
        self.assertEqual(
            (row["attempts"][-1]["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN")
        )
        c = check_type("omega")(ROOT, self.store.directory(flow["flow_id"]) / a["attempt_id"])
        self.assertIn(
            "MRA_REPLAY:",
            (c.directory(row["attempts"][-1]["run_id"]) / "replay/stdout.log").read_text(),
        )
        self.save("interrupted", row)
