"""Association contracts; synthetic reports below never establish real E4 evidence."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.research_evidence import LIMITS, ResearchEvidenceStore
from mra.research_flow import SOURCE_FILES, atomic, lease
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_proof_steps import create as create_scope
from test_proof_steps import submission as step_submission
from test_research_flow import create as create_flow, parent, single
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "bridge-qa-" + uuid.uuid4().hex


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (*SOURCE_FILES, "mra/proof_steps.py", "mra/research_evidence.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.store = ResearchEvidenceStore(self.root)
        self.flow = create_flow(self.store.flows, parent(self.root), single())
        self.key = self.flow["flow_id"]
        self.scope = create_scope(self.store.steps, self.flow)
        self.attempt = "attempt-" + uuid.uuid4().hex
        plan = self.store.steps.preview_step(
            self.scope["scope_id"], {"parent_id": None, "tactic": None}
        )["child_plan"]
        child = {
            "run_id": "synthetic-contract-only",
            "plan": plan,
            "state": "COMPLETED",
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "invalidated": False,
            "alignment": {"confirmed": True, "reviewer": "Contract fixture"},
            "files": {},
            "result": {
                "candidate": {"audit": {"type_matches": True}},
                "replay": {"audit": {"mode": "data-only-kernel-replay-v1"}},
            },
        }
        self.scope.update(
            accepted=[self.attempt],
            checks={self.attempt: child},
            attempts=[
                {
                    "attempt_id": self.attempt,
                    "state": "COMPLETED",
                    "invalidated": False,
                    "cancel_requested": False,
                    "error": None,
                    "observation": {"outcome": "CLOSED"},
                    "source_fingerprint": "contract-fixture-only",
                }
            ],
        )
        self.mock = patch.object(
            self.store.steps, "report", side_effect=lambda _: deepcopy(self.scope)
        )
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.input = {
            "node_id": "problem",
            "scope_id": self.scope["scope_id"],
            "attempt_id": self.attempt,
        }

    def submission(self):
        plan = self.store.preview(self.key, self.input)
        return {
            **self.input,
            "plan_fingerprint": plan["plan_fingerprint"],
            "request_id": rid(),
            "reviewer": "Contract reviewer",
            "confirmed": True,
        }

    def withdrawal(self, row):
        return {
            "binding_id": row["bindings"][-1]["binding_id"],
            "request_id": rid(),
            "reviewer": "Contract reviewer",
            "reason": "Review withdrawn",
            "confirmed": True,
        }

    def test_readonly_preview_has_no_execution_no_ledger_and_no_claim(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            p = self.store.preview(self.key, self.input)
        self.assertEqual((p["math_verdict"], p["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(p["lean_calls"], 0)
        self.assertFalse(self.store.directory(self.key).exists())

    def test_named_confirmation_exact_plan_and_duplicate_request_guards(self):
        raw = self.submission()
        for change in (
            {"reviewer": " "},
            {"confirmed": 1},
            {"confirmed": False},
            {"plan_fingerprint": "stale"},
            {"extra": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.bind(self.key, {**raw, **change})
        row = self.store.bind(self.key, raw)
        self.assertEqual(row, self.store.bind(self.key, raw))
        self.assertEqual(row["math_verdict"], "PROVED")
        self.assertEqual(row["research_task"]["math_verdict"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.bind(self.key, {**raw, "reviewer": "Another reviewer"})
        with self.assertRaises(ValueError):
            self.store.bind(self.key, self.submission())

    def test_withdrawal_is_named_idempotent_and_old_bind_does_not_revive(self):
        raw = self.submission()
        row = self.store.bind(self.key, raw)
        w = self.withdrawal(row)
        for change in ({"reason": ""}, {"reviewer": ""}, {"confirmed": 1}):
            with self.assertRaises(ValueError):
                self.store.withdraw(self.key, {**w, **change})
        row = self.store.withdraw(self.key, w)
        self.assertEqual(row, self.store.withdraw(self.key, w))
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.bind(self.key, raw)["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.bind(self.key, self.submission())["math_verdict"], "PROVED")

    def test_stale_preview_after_revision_change_is_rejected(self):
        old = self.submission()
        row = self.store.bind(self.key, self.submission())
        self.store.withdraw(self.key, self.withdrawal(row))
        with self.assertRaises(ValueError):
            self.store.bind(self.key, old)

    def test_exact_flow_node_target_prerequisite_and_statement_required(self):
        base = deepcopy(self.scope)
        cases = []
        for field, value in (
            ("flow_id", "flow-" + "a" * 32),
            ("node_id", "different"),
            ("prerequisites", {"invented": "binding"}),
            ("target", {}),
            ("flow_plan_fingerprint", "changed"),
        ):
            row = deepcopy(base)
            row["plan"]["context"][field] = value
            cases.append(row)
        row = deepcopy(base)
        row["checks"][self.attempt]["plan"]["formal_statement"] = "True"
        cases.append(row)
        row = deepcopy(base)
        row["checks"][self.attempt]["plan"]["request"]["assumptions"] = []
        cases.append(row)
        for row in cases:
            self.scope = row
            with self.assertRaises(ValueError):
                self.store.preview(self.key, self.input)

    def test_open_cancelled_interrupted_rejected_and_unaccepted_are_not_evidence(self):
        base = deepcopy(self.scope)
        for change in (
            {"state": "CANCELLED"},
            {"state": "INTERRUPTED"},
            {"state": "REJECTED"},
            {"cancel_requested": True},
            {"error": "failed"},
            {"invalidated": True},
            {"observation": {"outcome": "OPEN"}},
        ):
            self.scope = deepcopy(base)
            self.scope["attempts"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.preview(self.key, self.input)
        self.scope = deepcopy(base)
        self.scope["accepted"] = []
        with self.assertRaises(ValueError):
            self.store.preview(self.key, self.input)

    def test_alignment_candidate_and_replay_audits_required(self):
        base = deepcopy(self.scope)
        for kind in ("alignment", "candidate", "replay", "level", "state"):
            self.scope = deepcopy(base)
            c = self.scope["checks"][self.attempt]
            if kind == "alignment":
                c["alignment"]["confirmed"] = 1
            if kind == "candidate":
                c["result"]["candidate"]["audit"]["type_matches"] = False
            if kind == "replay":
                c["result"]["replay"]["audit"]["mode"] = "model-claim"
            if kind == "level":
                c["evidence_level"] = "E3"
            if kind == "state":
                c["state"] = "RUNNING"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.store.preview(self.key, self.input)

    def test_source_drift_restoration_and_old_request_never_revive(self):
        raw = self.submission()
        self.store.bind(self.key, raw)
        original = deepcopy(self.scope)
        self.scope["checks"][self.attempt]["files"] = {"candidate": "changed"}
        self.assertEqual(self.store.report(self.key)["bindings"][0]["state"], "INVALIDATED")
        self.scope = original
        self.assertEqual(self.store.bind(self.key, raw)["math_verdict"], "UNKNOWN")

    def test_program_drift_is_persistent_after_restoration(self):
        self.store.bind(self.key, self.submission())
        path = self.root / "mra/research_evidence.py"
        original = path.read_bytes()
        path.write_bytes(original + b"\n# drift\n")
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")
        path.write_bytes(original)
        self.assertEqual(self.store.report(self.key)["bindings"][0]["state"], "INVALIDATED")

    def test_busy_is_transient_and_never_persistently_invalidates(self):
        self.store.bind(self.key, self.submission())
        path = self.store.directory(self.key) / "record.json"
        before = path.read_bytes()
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.store.steps, "report", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.store.report(self.key)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.report(self.key)["math_verdict"], "PROVED")

    def test_plan_and_record_corruption_never_revive_on_restore(self):
        self.store.bind(self.key, self.submission())
        path = self.store.directory(self.key) / "record.json"
        original = path.read_bytes()
        path.write_text("corrupt")
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        path.write_bytes(original)
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")
        self.assertTrue(self.store.report(self.key)["invalidated"])

    def test_valid_digest_but_malformed_ledger_is_quarantined(self):
        self.store.bind(self.key, self.submission())
        path = self.store.directory(self.key)
        for change in (
            {"bindings": None},
            {"revision": True},
            {"requests": []},
            {"bindings": [{"binding_id": "broken"}]},
        ):
            row = self.store.empty(self.key)
            atomic(path / "record.json", {**row, **change})
            with self.assertRaises(ValueError):
                self.store.report(self.key)
        self.assertTrue((path / "invalidated.json").exists())

    def test_symlink_and_concurrent_publication_fail_closed(self):
        with lease(self.store.base), self.assertRaises(WorkerBusy):
            self.store.report(self.key)
        original = self.store.empty

        def publish(key):
            row = original(key)
            if self.store.directory(key).exists():
                with self.assertRaises(WorkerBusy):
                    ResearchEvidenceStore(self.root).report(key)
            return row

        with patch.object(self.store, "empty", side_effect=publish):
            self.store.bind(self.key, self.submission())
        path = self.store.directory(self.key) / "record.json"
        outside = self.root / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.report(self.key)

    def test_binding_request_and_flow_budgets(self):
        with patch.dict(LIMITS, bindings=0), self.assertRaises(ValueError):
            self.store.bind(self.key, self.submission())
        with patch.dict(LIMITS, requests=0), self.assertRaises(ValueError):
            self.store.bind(self.key, self.submission())
        self.store.bind(self.key, self.submission())
        other = create_flow(self.store.flows, parent(self.root), single())
        with patch.dict(LIMITS, flows=1), self.assertRaises(ValueError):
            with self.store.access(other["flow_id"], create=True):
                pass

    def test_http_csrf_strict_json_readonly_and_static_assets(self):
        with running_server(self.root) as server:
            endpoint = "/v1/research-evidence/" + self.key
            for route in (endpoint, "/research-evidence.js", "/research-evidence.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)
            self.assertEqual(request(server, "GET", endpoint + "?execute=1")[0], 400)
            self.assertEqual(
                request(server, "POST", endpoint + "/bind", {}, headers={"X-MRA-CSRF": "wrong"})[0],
                403,
            )
            self.assertEqual(request(server, "POST", endpoint + "/bind", {})[0], 400)
            self.assertFalse(self.store.directory(self.key).exists())

    def test_cli_strict_json_and_readonly_report(self):
        from mra import __main__ as cli

        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(sys, "argv", ["mra", "research-evidence-report", self.key]),
            patch("sys.stdout", io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["math_verdict"], "UNKNOWN")
        path = self.root / "invalid.json"
        path.write_text('{"x":1,"x":2}')
        with (
            patch.object(cli, "ROOT", self.root),
            patch.object(
                sys, "argv", ["mra", "research-evidence-bind", self.key, "--input", str(path)]
            ),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli.main(), 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1")
class RealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = ResearchEvidenceStore(ROOT)
        cls.out = (
            ROOT / "artifacts/research-evidence-20261027-01" / ("real-suite-" + uuid.uuid4().hex)
        )
        cls.out.mkdir()
        cls.project = parent(ROOT)
        nodes = single()
        nodes[0]["target"].update(
            title="零的自反性",
            original="自然数零等于自身。",
            variables=[],
            assumptions=[],
            goal={"eq": [{"nat": 0}, {"nat": 0}]},
        )
        cls.flow = create_flow(cls.store.flows, cls.project, nodes)

    def save(self, name, row):
        (self.out / (name + ".json")).write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def raw(self, flow, scope):
        return {
            "node_id": "problem",
            "scope_id": scope["scope_id"],
            "attempt_id": scope["attempts"][-1]["attempt_id"],
        }

    def bind(self, flow, scope):
        raw = self.raw(flow, scope)
        plan = self.store.preview(flow, raw)
        return self.store.bind(
            flow,
            {
                **raw,
                "plan_fingerprint": plan["plan_fingerprint"],
                "request_id": rid(),
                "reviewer": "MRA044 real evidence QA",
                "confirmed": True,
            },
        )

    def proved(self):
        scope = create_scope(self.store.steps, self.flow)
        key = scope["scope_id"]
        self.store.steps.submit(key, step_submission(self.store.steps, key))
        self.store.steps.work_once()
        opened = self.store.steps.report(key)
        self.assertEqual(opened["math_verdict"], "UNKNOWN")
        self.assertEqual(opened["attempts"][-1]["observation"]["outcome"], "OPEN")
        with self.assertRaises(ValueError):
            self.store.preview(self.flow["flow_id"], self.raw(self.flow["flow_id"], opened))
        self.store.steps.submit(
            key,
            step_submission(self.store.steps, key, opened["attempts"][-1]["attempt_id"], "omega"),
        )
        self.store.steps.work_once()
        scope = self.store.steps.report(key)
        self.assertEqual((scope["math_verdict"], scope["evidence_level"]), ("PROVED", "E4"))
        return scope

    def test_existing_gcd_replay_can_be_associated_without_new_lean_or_source_rewrite(self):
        flow = "flow-96d6ef6b5a4a4436bf5386ed5d9979a5"
        scope = self.store.steps.report("scope-331b395764e04dfe873b6ad481a1cfbd")
        self.assertEqual(scope["math_verdict"], "PROVED")
        with patch.object(
            NativeLeanBackend,
            "_run_worker",
            side_effect=AssertionError("Association must not run Lean"),
        ):
            row = self.bind(flow, scope)
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("PROVED", "E4"))
        self.assertEqual(row["research_task"]["math_verdict"], "UNKNOWN")
        self.assertEqual(scope, self.store.steps.report(scope["scope_id"]))
        self.save("gcd-linked", row)

    def test_new_real_proof_withdrawal_reassociation_preserve_original(self):
        scope = self.proved()
        flow = self.flow["flow_id"]
        with patch.object(
            NativeLeanBackend, "_run_worker", side_effect=AssertionError("No new Lean")
        ):
            linked = self.bind(flow, scope)
            w = {
                "binding_id": linked["bindings"][-1]["binding_id"],
                "request_id": rid(),
                "reviewer": "MRA044 real evidence QA",
                "reason": "Real withdrawal boundary",
                "confirmed": True,
            }
            withdrawn = self.store.withdraw(flow, w)
            self.assertEqual(withdrawn["math_verdict"], "UNKNOWN")
            self.assertEqual(self.store.steps.report(scope["scope_id"]), scope)
            rebound = self.bind(flow, scope)
            self.assertEqual(rebound["math_verdict"], "PROVED")
        self.save("withdrawn", withdrawn)
        self.save("constant-proved", scope)
        self.save("constant-linked", rebound)

    def test_real_artifact_damage_restore_stays_invalid_without_touching_history(self):
        scope = self.proved()
        flow = self.flow["flow_id"]
        linked = self.bind(flow, scope)
        a = scope["attempts"][-1]
        from mra.proof_steps import PrefixChecks

        checker = PrefixChecks(
            ROOT, self.store.steps.directory(scope["scope_id"]) / a["attempt_id"]
        )
        path = checker.directory(a["run_id"]) / "candidate/stdout.log"
        original = path.read_bytes()
        path.write_bytes(original + b"\nMRA044 deliberate corruption\n")
        try:
            damaged = self.store.report(flow)
        finally:
            path.write_bytes(original)
        key = linked["bindings"][-1]["binding_id"]
        restored = self.store.report(flow)
        for row in (damaged, restored):
            self.assertEqual(
                next(b for b in row["bindings"] if b["binding_id"] == key)["state"], "INVALIDATED"
            )
        self.assertNotIn(key, restored["source_evidence"])
        self.assertEqual(self.store.steps.report(scope["scope_id"])["math_verdict"], "UNKNOWN")
        self.save("damaged", damaged)
        self.save("restored-invalid", restored)
        self.save("damaged-scope", self.store.steps.report(scope["scope_id"]))


if __name__ == "__main__":
    unittest.main()
