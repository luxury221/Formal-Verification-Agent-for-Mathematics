"""Capture-free substitution and an isolated adapter over real sealed Lean evidence."""

import copy
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

from mra.evidence import EvidenceGate
from mra.knowledge_instances import KnowledgeInstanceStore, specialize, storage_busy
from mra.nat_ideas import render_formula
from mra.paper_review import PaperReviewStore
from mra.research import ResearchStore
from mra.research_runs import ControlledReview
from mra.tasks import TaskStore, WorkerBusy
from test_http_api import copy_web_inputs, request, running_server
from test_integer_arithmetic import example, PROVE
from test_knowledge import admission
from test_paper_review import submission

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "instance-test-" + uuid.uuid4().hex


def num(n):
    return {"op": "num", "value": n}


def var(name):
    return {"op": "var", "name": name}


def binary(op, left, right):
    return {"op": op, "left": left, "right": right}


def quantified(op, name, body):
    return {"op": op, "name": name, "body": body}


def substitution(name="n", term=None):
    return {"variable": name, "term": num(3) if term is None else term}


def formula():
    return quantified("forall", "n", binary("lt", binary("sub", var("n"), num(1)), var("n")))


class SpecializationTests(unittest.TestCase):
    def test_constant_and_symbolic_instances_keep_explicit_domain(self):
        result = specialize(formula(), "Int", [], [substitution(term=num(-2))])
        self.assertEqual(result, binary("lt", binary("sub", num(-2), num(1)), num(-2)))
        term = binary("add", var("x"), num(2))
        symbolic = specialize(formula(), "Int", ["x"], [substitution(term=term)])
        self.assertEqual(symbolic["name"], "x")
        self.assertIn("Int", render_formula(symbolic, "Int"))
        with self.assertRaises(ValueError):
            specialize(formula(), "Nat", [], [substitution(term=num(-2))])

    def test_partial_prefix_preserves_uninstantiated_quantifiers_implication_and_exists(self):
        body = binary(
            "imp",
            binary("lt", var("n"), var("m")),
            quantified("exists", "z", binary("eq", var("z"), var("n"))),
        )
        source = quantified("forall", "n", quantified("forall", "m", body))
        result = specialize(source, "Nat", ["x"], [substitution(term=var("x"))])
        self.assertEqual(result["body"]["name"], "m")
        condition = result["body"]["body"]
        self.assertEqual(condition["op"], "imp")
        self.assertEqual(condition["right"]["op"], "exists")
        self.assertEqual(condition["left"], binary("lt", var("x"), var("m")))
        self.assertEqual(condition["right"]["body"]["right"], var("x"))
        self.assertEqual(source["name"], "n")

    def test_capture_unused_duplicate_reserved_or_unbound_parameters_are_rejected(self):
        source = quantified(
            "forall", "n", quantified("exists", "z", binary("eq", var("z"), var("n")))
        )
        for parameters, term in [
            (["z"], var("z")),
            (["n"], var("n")),
            (["x"], num(1)),
            (["x", "x"], var("x")),
            (["by"], num(1)),
            ([], var("x")),
            (["x"], var("y")),
        ]:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                specialize(source, "Int", parameters, [substitution(term=term)])

    def test_only_exact_leading_universal_prefix_and_terms_are_accepted(self):
        for substitutions in [
            [],
            [substitution("m")],
            [substitution(), substitution()],
            [{"variable": "n", "term": num(1), "drop_condition": True}],
            [substitution(term=binary("eq", num(1), num(1)))],
            [substitution(term={**num(1), "source": "by trivial"})],
        ]:
            with self.subTest(substitutions=substitutions), self.assertRaises(ValueError):
                specialize(formula(), "Int", [], substitutions)
        exists = quantified("exists", "n", binary("eq", var("n"), num(1)))
        with self.assertRaises(ValueError):
            specialize(exists, "Int", [], [substitution()])

    def test_substitution_expansion_and_combined_quantifier_budget_are_bounded(self):
        leaf = var("n")
        for _ in range(5):
            leaf = binary("add", leaf, copy.deepcopy(leaf))
        source = quantified("forall", "n", binary("eq", leaf, copy.deepcopy(leaf)))
        render_formula(source, "Int")
        term = binary("add", var("x"), binary("add", var("x"), var("x")))
        with self.assertRaises(ValueError):
            specialize(source, "Int", ["x"], [substitution(term=term)])
        body = binary("eq", var("n"), var("n"))
        for name in reversed(["n", "a", "b", "c", "d", "e", "f", "g"]):
            body = quantified("forall", name, body)
        with self.assertRaises(ValueError):
            specialize(
                body, "Int", ["x", "y"], [substitution(term=binary("add", var("x"), var("y")))]
            )


class InstanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = KnowledgeInstanceStore(self.root)

    def test_empty_store_and_unverified_sources_never_create_positive_records(self):
        self.assertEqual(self.store.list_instances(), [])
        with self.assertRaises(ValueError):
            self.store.preview(
                {
                    "knowledge_id": "missing",
                    "title": "t",
                    "statement": "s",
                    "parameters": [],
                    "substitutions": [substitution()],
                }
            )
        self.assertFalse((self.root / "artifacts/runs").exists())
        with self.assertRaises(ValueError):
            self.store.create({"math_verdict": "PROVED"})

    def test_native_busy_error_is_identified_on_python310_without_masking_other_failures(self):
        owner = sqlite3.connect(self.root / "busy.sqlite3", timeout=0)
        other = sqlite3.connect(self.root / "busy.sqlite3", timeout=0)
        try:
            owner.execute("CREATE TABLE rows (id INTEGER)")
            owner.commit()
            owner.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(sqlite3.OperationalError) as caught:
                other.execute("SELECT * FROM rows").fetchall()
            self.assertTrue(storage_busy(caught.exception))
        finally:
            other.close()
            owner.rollback()
            owner.close()
        self.assertFalse(storage_busy(sqlite3.OperationalError("no such table: missing")))

    def test_http_assets_csrf_origin_strict_json_and_unknown_routes(self):
        with running_server(self.root) as server:
            for asset in ("/knowledge-instances.js", "/knowledge-instances.css"):
                self.assertEqual(request(server, "GET", asset)[0], 200)
            self.assertEqual(request(server, "GET", "/v1/knowledge-instances")[1]["instances"], [])
            for headers in ({"Origin": "https://outside.test"}, {"X-MRA-CSRF": "stale"}):
                self.assertEqual(
                    request(server, "POST", "/v1/knowledge-instances/preview", {}, headers=headers)[
                        0
                    ],
                    403,
                )
            self.assertEqual(
                request(server, "POST", "/v1/knowledge-instances/preview", raw=b'{"a":1,"a":2}')[0],
                400,
            )
            for route in ("/v1/knowledge-instances/missing", "/v1/instance-runs/missing"):
                self.assertEqual(request(server, "GET", route)[0], 400)
            self.assertEqual(
                request(server, "POST", "/v1/instance-runs/missing/cancel", {"extra": True})[0], 400
            )


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "requires actual sealed Lean and replay"
)
class InstanceRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.papers = PaperReviewStore(ROOT)
        bundle = example()
        bundle["document"]["id"] = rid()
        bundle["document"]["text"] = "对任意整数 n，n - 1 < n。"
        bundle["steps"] = []
        queued = cls.papers.submit(submission(bundle))
        cls.review = cls.papers.work_once(queued["review_id"])["review"]
        if cls.review["claim_verdict"] != "PROVED":
            raise AssertionError(cls.review)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = KnowledgeInstanceStore(self.root)
        # Isolated instance/archive ledgers, authoritative paper store and sealed checker.
        self.store.root = ROOT
        self.store.tasks = TaskStore(ROOT)
        self.store.papers = self.papers
        self.store.research.underlying = ResearchStore(ROOT)
        self.store.knowledge.papers = self.papers
        self.item = self.store.knowledge.admit(
            admission(self.store.knowledge.prepare(self.review["review_id"]))
        )["items"][0]
        self.ids = []
        self.addCleanup(self.cleanup_runs)

    def cleanup_runs(self):
        for run_id in self.ids:
            if self.store.raw(run_id)["state"] in ("QUEUED", "RUNNING"):
                self.store.cancel(run_id)

    def preview(self):
        return self.store.preview(
            {
                "knowledge_id": self.item["item_id"],
                "title": "整数引理的符号实例",
                "statement": "对任意整数 x，(x + 2) - 1 < x + 2。",
                "parameters": ["x"],
                "substitutions": [substitution(term=binary("add", var("x"), num(2)))],
            }
        )

    def draft(self):
        preview = self.preview()
        return self.store.create(
            {
                "request_id": rid(),
                "input": preview["input"],
                "preview_fingerprint": preview["preview_fingerprint"],
            }
        )

    def execution(self, strategy="omega", wall=120):
        return {
            "branch_id": "main",
            "items": [{"node_id": "instance", "plan": {**PROVE, "strategy": strategy}}],
            "budget": {"max_checks": 1, "worker_seconds": 30, "wall_seconds": wall},
        }

    def queue(self, strategy="omega", wall=120):
        draft = self.draft()
        plan = self.store.prepare(draft["instance_id"], self.execution(strategy, wall))
        data = {
            "request_id": rid(),
            "input": plan["input"],
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "repository-instance-acceptance",
            "confirmed_nodes": ["instance"],
            "confirmed": True,
        }
        run = self.store.submit(draft["instance_id"], data)
        self.ids.append(run["run_id"])
        return run, data

    def work(self, run):
        return self.store.work_once(run["run_id"])["run"]

    def withdraw(self, delete=False):
        self.store.knowledge.action(
            self.item["item_id"],
            {"reviewer": "test", "reason": "withdraw cited source", "confirmed": True},
            delete=delete,
        )

    def hidden(self, run):
        self.assertEqual(run["nodes"][0]["math_verdict"], "UNKNOWN")
        self.assertFalse(run["nodes"][0]["results_available"])

    def test_preview_draft_and_prepare_are_offline_exact_idempotent_and_do_not_write_research(self):
        before = set((ROOT / "artifacts/runs").iterdir())
        projects = self.store.research.underlying.list_projects()
        p = self.preview()
        data = {
            "request_id": rid(),
            "input": p["input"],
            "preview_fingerprint": p["preview_fingerprint"],
        }
        d = self.store.create(data)
        self.assertEqual(d, self.store.create(data))
        self.assertEqual(d["math_verdict"], "UNKNOWN")
        self.assertEqual(d["runs"], [])
        plan = self.store.prepare(d["instance_id"], self.execution())
        self.assertEqual(plan["citation"]["content"]["formula"], formula())
        self.assertIn("x +", plan["items"][0]["review_plan"]["obligations"][0]["formal_statement"])
        self.assertEqual(projects, self.store.research.underlying.list_projects())
        self.assertEqual(before, set((ROOT / "artifacts/runs").iterdir()))
        for changed in (
            {**data, "preview_fingerprint": "changed"},
            {**data, "input": {**data["input"], "title": "changed"}},
        ):
            with self.assertRaises(ValueError):
                self.store.create(changed)
        for changed in (
            {**self.execution(), "extra": True},
            {
                **self.execution(),
                "budget": {"max_checks": True, "worker_seconds": 30, "wall_seconds": 120},
            },
            {**self.execution(), "items": [None]},
        ):
            with self.assertRaises((ValueError, TypeError)):
                self.store.prepare(d["instance_id"], changed)

    def test_real_symbolic_instance_has_distinct_candidate_replay_and_new_alignment(self):
        queued, _ = self.queue()
        run = self.work(queued)
        self.assertEqual(run["state"], "COMPLETED", run)
        self.assertEqual(run["nodes"][0]["math_verdict"], "PROVED", run)
        self.assertEqual(run["used"], {"checks": 1, "worker_seconds_reserved": 30})
        review = self.papers.report(run["nodes"][0]["review_id"])
        self.assertEqual(review["author_proof_verdict"], "UNKNOWN")
        receipt = json.loads(
            (Path(review["checks"][0]["result"]["artifact_directory"]) / "receipt.json").read_text()
        )["payload"]
        self.assertNotEqual(receipt["candidate_pid"], receipt["replay"]["pid"])
        self.assertTrue(receipt["replay"]["audit"]["type_matches"])
        result = self.store.report_instance(run["plan"]["project_id"])
        self.assertEqual(result["math_verdict"], "PROVED")
        self.assertFalse(result["fresh_attestation"])

    def test_failed_decide_strategy_is_unknown_and_never_refuted(self):
        queued, _ = self.queue("decide")
        run = self.work(queued)
        self.assertEqual(run["state"], "COMPLETED", run)
        self.assertEqual(run["nodes"][0]["math_verdict"], "UNKNOWN")

    def test_readonly_citation_does_not_request_the_knowledge_writer_lock(self):
        with self.store.knowledge.transaction():
            preview = self.preview()
            draft = self.store.create(
                {
                    "request_id": rid(),
                    "input": preview["input"],
                    "preview_fingerprint": preview["preview_fingerprint"],
                }
            )
            self.assertEqual(self.store.report_instance(draft["instance_id"])["state"], "DRAFT")
        self.assertEqual(draft["math_verdict"], "UNKNOWN")

    def test_transient_database_busy_does_not_permanently_invalidate_the_draft_or_run(self):
        queued, _ = self.queue()
        error = sqlite3.OperationalError("simulated temporary writer contention")
        error.sqlite_errorcode = 5
        with patch.object(self.store, "knowledge_rows", side_effect=error):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.audit_instance(queued["plan"]["project_id"])
            with self.assertRaises(sqlite3.OperationalError):
                self.store.invalid_reason(queued)
        self.assertEqual(self.store.draft(queued["plan"]["project_id"])["state"], "DRAFT")
        self.assertEqual(self.store.report(queued["run_id"])["state"], "QUEUED")

    def test_real_instance_publishes_with_a_concurrent_knowledge_writer_reservation(self):
        queued, _ = self.queue()
        with self.store.knowledge.transaction():
            run = self.work(queued)
        self.assertEqual(run["state"], "COMPLETED", run)
        self.assertEqual(run["nodes"][0]["math_verdict"], "PROVED")
        self.assertTrue(run["nodes"][0]["results_available"])

    def test_targeted_dependency_audit_persists_real_damage_and_preserves_independent_claim(self):
        bundle = example()
        bundle["document"]["id"] = rid()
        queued = self.papers.submit(submission(bundle))
        review = self.papers.work_once(queued["review_id"])["review"]
        self.assertEqual(review["claim_verdict"], "PROVED")
        admitted = self.store.knowledge.admit(
            admission(self.store.knowledge.prepare(review["review_id"]))
        )
        items = {i["source_ref"]["key"]: i for i in admitted["items"]}
        self.item = items["s2"]
        draft = self.draft()
        path = self.store.knowledge.proofs / items["s1"]["item_id"] / "replay/proof.ndjson"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        try:
            self.assertEqual(
                self.store.report_instance(draft["instance_id"])["state"], "INVALIDATED"
            )
        finally:
            path.write_bytes(original)
        for key in ("s1", "s2"):
            self.assertEqual(
                self.store.knowledge.report(items[key]["item_id"])["state"], "INVALIDATED"
            )
        self.assertEqual(self.store.knowledge.report(items["claim"]["item_id"])["state"], "ACTIVE")
        with self.assertRaises(ValueError):
            self.preview()

    def test_actual_process_exit_after_check_requires_reconfirmation_and_never_promotes_artifacts(
        self,
    ):
        queued, _ = self.queue()
        code = """from pathlib import Path
import os, sys
from mra.knowledge_instances import KnowledgeInstanceStore
from mra.paper_review import PaperReviewStore
from mra.research import ResearchStore
from mra.research_runs import ControlledReview
from mra.tasks import TaskStore
s = KnowledgeInstanceStore(Path(sys.argv[1]))
s.root = Path.cwd()
s.tasks = TaskStore(s.root)
s.papers = PaperReviewStore(s.root)
s.knowledge.papers = s.papers
s.research.underlying = ResearchStore(s.root)
original = ControlledReview.execute
def crash(self, *args, **kwargs):
    original(self, *args, **kwargs)
    os._exit(73)
ControlledReview.execute = crash
s.work_once(sys.argv[2])
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.root), queued["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        child_id = self.store.raw(queued["run_id"])["nodes"][0]["review_id"]
        child = self.papers.raw(child_id)
        self.assertEqual(child["checks"][0]["result"]["math_verdict"], "PROVED")
        self.assertEqual(child["state"], "RUNNING")
        self.assertFalse(self.papers.report(child_id)["results_available"])
        self.assertIn(queued["run_id"], self.store.recover()["interrupted"])
        run = self.store.report(queued["run_id"])
        self.assertEqual(run["state"], "INTERRUPTED")
        self.hidden(run)
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")
        self.assertFalse(self.papers.report(child_id)["results_available"])

    def test_cancel_is_idempotent_and_recovery_preserves_budget_without_retry(self):
        queued, data = self.queue()
        self.store.cancel(queued["run_id"])
        repeated = self.store.submit(queued["plan"]["project_id"], data)
        self.assertEqual(repeated["state"], "CANCELLED")
        self.hidden(repeated)
        queued, _ = self.queue()
        self.store.mutate(
            queued["run_id"],
            lambda b: b.update(state="RUNNING", used={"checks": 1, "worker_seconds_reserved": 30}),
        )
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.recover()
        self.assertEqual(self.store.recover()["interrupted"], [queued["run_id"]])
        self.hidden(self.store.report(queued["run_id"]))
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_withdrawal_and_deletion_invalidate_queued_instances_and_keep_original_proof(self):
        queued, _ = self.queue()
        self.withdraw(delete=True)
        self.store.reconcile()
        report = self.store.report_instance(queued["plan"]["project_id"])
        self.assertEqual(report["state"], "INVALIDATED")
        self.assertEqual(report["run_state"], "INVALIDATED")
        self.assertEqual(report["math_verdict"], "UNKNOWN")
        self.assertEqual(self.papers.report(self.review["review_id"])["claim_verdict"], "PROVED")

    def test_archive_change_is_permanent_and_retries_cleanup_after_crash(self):
        queued, _ = self.queue()
        path = self.store.knowledge.proofs / self.item["item_id"] / "replay/proof.ndjson"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with patch.object(
            self.store, "stop", side_effect=RuntimeError("simulated crash after invalidation")
        ):
            with self.assertRaises(RuntimeError):
                self.store.audit_instance(queued["plan"]["project_id"])
        path.write_bytes(original)
        self.store.reconcile()
        self.assertEqual(self.store.raw(queued["run_id"])["state"], "INVALIDATED")
        self.hidden(self.store.report(queued["run_id"]))

    def test_completed_result_is_revoked_when_citation_withdrawn_and_cannot_recur(self):
        queued, _ = self.queue()
        run = self.work(queued)
        child_id = run["nodes"][0]["review_id"]
        derived = self.store.knowledge.admit(admission(self.store.knowledge.prepare(child_id)))[
            "items"
        ][0]
        with self.assertRaises(ValueError):
            self.store.preview(
                {
                    **self.preview()["input"],
                    "knowledge_id": derived["item_id"],
                    "substitutions": [substitution("x")],
                    "parameters": [],
                }
            )
        self.withdraw()
        self.store.reconcile()
        self.hidden(self.store.report(run["run_id"]))
        self.assertFalse(self.papers.report(child_id)["results_available"])
        self.assertEqual(self.store.knowledge.report(derived["item_id"])["state"], "INVALIDATED")

    def test_source_withdrawn_after_actual_e4_gate_is_never_published(self):
        queued, _ = self.queue()
        original = EvidenceGate.assess
        verdicts = []

        def withdraw_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            verdicts.append(result[0].math_verdict.value)
            self.withdraw()
            return result

        with patch.object(EvidenceGate, "assess", withdraw_after_gate):
            run = self.work(queued)
        self.assertEqual(verdicts, ["PROVED"])
        self.assertEqual(run["state"], "INVALIDATED")
        self.hidden(run)
        self.assertFalse(self.papers.report(run["nodes"][0]["review_id"])["results_available"])

    def test_actual_gate_result_after_wall_deadline_is_hidden(self):
        queued, _ = self.queue()
        original = EvidenceGate.assess

        def expire_after_gate(gate, *args, **kwargs):
            result = original(gate, *args, **kwargs)
            self.assertEqual(result[0].math_verdict.value, "PROVED")
            self.store.mutate(queued["run_id"], lambda b: b.update(deadline_monotonic=0))
            return result

        with patch.object(EvidenceGate, "assess", expire_after_gate):
            run = self.work(queued)
        self.assertEqual(run["state"], "BUDGET_EXHAUSTED")
        self.hidden(run)

    def test_cancel_during_real_replay_releases_shared_lock(self):
        queued, _ = self.queue()
        original = ControlledReview.checkpoint

        def cancel_at_replay(child, review_id, key, stage, directory=None):
            original(child, review_id, key, stage, directory)
            if stage == "REPLAY_RUNNING":
                self.store.cancel(queued["run_id"])

        with patch.object(ControlledReview, "checkpoint", cancel_at_replay):
            run = self.work(queued)
        self.assertEqual(run["state"], "CANCELLED")
        self.hidden(run)
        with self.store.tasks.worker_lock():
            pass

    def test_http_preview_create_plan_run_cancel_and_stale_source(self):
        with running_server(self.root) as server:
            server.instances = self.store
            data = self.preview()["input"]
            status, preview, _ = request(server, "POST", "/v1/knowledge-instances/preview", data)
            self.assertEqual(status, 200)
            status, draft, _ = request(
                server,
                "POST",
                "/v1/knowledge-instances",
                {
                    "request_id": rid(),
                    "input": data,
                    "preview_fingerprint": preview["preview_fingerprint"],
                },
            )
            self.assertEqual(status, 201)
            base = "/v1/knowledge-instances/" + draft["instance_id"]
            status, plan, _ = request(server, "POST", base + "/run-plan", self.execution())
            self.assertEqual(status, 200)
            payload = {
                "request_id": rid(),
                "input": plan["input"],
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "test",
                "confirmed_nodes": [],
                "confirmed": True,
            }
            self.assertEqual(request(server, "POST", base + "/runs", payload)[0], 400)
            payload["confirmed_nodes"] = ["instance"]
            status, run, _ = request(server, "POST", base + "/runs", payload)
            self.assertEqual(status, 201)
            self.ids.append(run["run_id"])
            route = "/v1/instance-runs/" + run["run_id"]
            self.assertEqual(request(server, "GET", route)[1]["state"], "QUEUED")
            self.assertEqual(
                request(server, "POST", route + "/cancel", {})[1]["state"], "CANCELLED"
            )
            self.withdraw()
            self.assertEqual(request(server, "GET", base)[1]["state"], "INVALIDATED")

    def test_ledger_mutation_and_symlink_cannot_produce_available_results(self):
        self.draft()
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE instances SET body='{}'")
        with self.assertRaises((ValueError, KeyError)):
            self.store.list_instances()
        moved = self.root / "moved.sqlite3"
        self.store.path.rename(moved)
        self.store.path.symlink_to(moved)
        with self.assertRaises(ValueError):
            self.store.list_instances()

    def test_cli_roundtrip_stays_offline_until_explicit_worker(self):
        knowledge = KnowledgeInstanceStore(ROOT).knowledge
        item = knowledge.admit(admission(knowledge.prepare(self.review["review_id"])))["items"][0]
        path = self.root / "cli.json"

        def cli(*args):
            result = subprocess.run(
                [sys.executable, "-m", "mra", *args],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        path.write_text(json.dumps({**self.preview()["input"], "knowledge_id": item["item_id"]}))
        p = cli("knowledge-instance-preview", "--input", str(path))
        path.write_text(
            json.dumps(
                {
                    "request_id": rid(),
                    "input": p["input"],
                    "preview_fingerprint": p["preview_fingerprint"],
                }
            )
        )
        d = cli("knowledge-instance-create", "--input", str(path))
        path.write_text(json.dumps(self.execution()))
        p = cli("knowledge-instance-plan", d["instance_id"], "--input", str(path))
        path.write_text(
            json.dumps(
                {
                    "request_id": rid(),
                    "input": p["input"],
                    "plan_fingerprint": p["plan_fingerprint"],
                    "reviewer": "CLI acceptance",
                    "confirmed_nodes": ["instance"],
                    "confirmed": True,
                }
            )
        )
        r = cli("knowledge-instance-submit", d["instance_id"], "--input", str(path))
        try:
            self.assertEqual(cli("knowledge-instance-run-report", r["run_id"])["state"], "QUEUED")
        finally:
            self.assertEqual(cli("knowledge-instance-cancel", r["run_id"])["state"], "CANCELLED")
        self.assertEqual(
            cli("knowledge-instance-report", d["instance_id"])["math_verdict"], "UNKNOWN"
        )
