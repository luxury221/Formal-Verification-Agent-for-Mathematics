"""Explicitly nonexecutable contracts and separately enabled genuine graph proofs."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

from mra.campaign_checks import CampaignChecks
from mra.domain import canonical
from mra.research_campaign import ResearchCampaignStore
from mra.research_compositions import complete_target
from mra.research_flow import SOURCE_FILES
from mra.tasks import WorkerBusy
from test_proof_library import (
    ContractTests as LibraryFixtures,
    NONEXECUTABLE,
    admission,
    application,
    real_library,
    retirement,
)
from test_research_flow import create, parent

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/research-campaign-20261028-01"


def rid():
    return "campaign-qa-" + uuid.uuid4().hex


def graph(requests):
    names = ["seed", "middle", "problem"][-len(requests) :]
    return [
        {
            "id": k,
            "kind": "problem" if i == len(requests) - 1 else "lemma",
            "target": complete_target(requests[i]),
            "depends_on": [] if i == 0 else [names[i - 1]],
        }
        for i, k in enumerate(names)
    ]


def reviewed(store, value):
    return {
        "request_id": rid(),
        "input": value,
        "plan_fingerprint": store.preview(value)["plan_fingerprint"],
        "reviewer": "NONEXECUTABLE contract reviewer",
        "confirmed": True,
    }


def campaign_input(flow, actions, **budget):
    return {
        "flow_id": flow["flow_id"],
        "actions": actions,
        "budget": {"max_checks": 8, "worker_seconds": 240, "wall_seconds": 600, **budget},
    }


def synthetic(checker, run_id, _lock_fd):
    """Never execute this metadata-only NDJSON or count it as mathematical evidence."""
    row = checker.read(run_id)
    directory = checker.directory(run_id)
    (directory / "replay").mkdir(parents=True)
    (directory / "replay/proof.ndjson").write_bytes(NONEXECUTABLE)
    row.update(
        state="COMPLETED",
        stage="COMPLETED",
        math_verdict="PROVED",
        evidence_level="E4",
        files={"replay/proof.ndjson": hashlib.sha256(NONEXECUTABLE).hexdigest()},
        result={
            "candidate": {"pid": 11, "audit": {"type_matches": True}},
            "replay": {"pid": 12, "audit": {"mode": "data-only-kernel-replay-v1"}},
            "worker_seconds": 0.1,
        },
    )
    with checker.transaction() as db:
        checker.save(db, row)
    return checker.report(run_id)


class ContractTests(unittest.TestCase):
    def setUp(self):
        LibraryFixtures.setUp(self)
        for name in (
            *SOURCE_FILES,
            "mra/research_compositions.py",
            "mra/research_campaign.py",
            "mra/campaign_checks.py",
        ):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, p)
        self.store = ResearchCampaignStore(self.root, self.library)
        self.requests = [deepcopy(self.fake["plan"]["request"]) for _ in range(3)]
        for request, k in zip(self.requests, ("seed", "middle", "problem")):
            request.update(title=k, original="NONEXECUTABLE " + k)
        # Existing proof must match the original node's full wording exactly.
        self.requests[0] = deepcopy(self.fake["plan"]["request"])
        self.flow = create(self.store.flows, parent(self.root), graph(self.requests))
        self.value = campaign_input(
            self.flow,
            {
                "seed": {
                    "kind": "existing",
                    "source": {"origin": "composition", "run_id": self.run_id},
                },
                "middle": {
                    "kind": "compose",
                    "request": application(
                        "node.seed", self.requests[1], title=self.requests[1]["title"]
                    ),
                },
                "problem": {
                    "kind": "compose",
                    "request": application(
                        "node.middle", self.requests[2], title=self.requests[2]["title"]
                    ),
                },
            },
        )

    def run_fake(self, value=None):
        row = self.store.submit(reviewed(self.store, value or self.value))
        with patch.object(CampaignChecks, "execute", synthetic):
            self.store.work_once(row["campaign_id"])
        return self.store.report(row["campaign_id"])

    def test_preview_keeps_whole_graph_and_future_types_without_lean_or_children(self):
        plan = self.store.preview(self.value)
        self.assertEqual((plan["math_verdict"], plan["new_lean_calls"]), ("UNKNOWN", 0))
        self.assertEqual(plan["flow_plan"]["nodes"], self.flow["plan"]["nodes"])
        self.assertEqual(
            plan["actions"]["middle"]["inputs"][0]["signature"]["request"]["assumptions"],
            self.requests[0]["assumptions"],
        )
        self.assertFalse(self.store.paths())

    def test_every_node_full_target_and_explicit_direct_dependency_are_required(self):
        for mutate in (
            lambda v: v["actions"].pop("seed"),
            lambda v: v["actions"]["middle"]["request"].update(original="silently changed"),
            lambda v: v["actions"]["problem"]["request"]["sources"][0].update(item_id="node.seed"),
            lambda v: v["actions"]["middle"]["request"]["proof"].update(proofs=[]),
        ):
            v = deepcopy(self.value)
            mutate(v)
            with self.assertRaises(ValueError):
                self.store.preview(v)

    def test_named_stale_confirmation_identity_and_cancelled_retry_are_strict(self):
        body = reviewed(self.store, self.value)
        for change in ({"confirmed": False}, {"reviewer": ""}, {"plan_fingerprint": "stale"}):
            with self.assertRaises(ValueError):
                self.store.submit({**body, **change})
        row = self.store.submit(body)
        self.store.cancel(row["campaign_id"])
        self.assertEqual(self.store.submit(body)["state"], "CANCELLED")
        with self.assertRaises(ValueError):
            self.store.submit({**body, "reviewer": "different"})
        self.assertEqual(self.store.work_once()["worker_state"], "IDLE")

    def test_full_graph_executes_ordered_node_data_imports_under_one_review(self):
        row = self.run_fake()
        self.assertEqual((row["state"], row["math_verdict"]), ("COMPLETED", "PROVED"))
        self.assertEqual([n["state"] for n in row["nodes"]], ["VERIFIED"] * 3)
        self.assertEqual(row["used"]["checks"], 2)
        self.assertEqual(row["used"]["worker_seconds_reserved"], 60)
        last = next(n for n in row["nodes"] if n["node_id"] == "problem")
        child = CampaignChecks(self.store, row["campaign_id"]).report(last["child_id"])
        self.assertEqual(child["plan"]["source_items"][0]["origin"], "campaign-node")
        self.assertEqual(
            child["plan"]["source_items"][0]["request"]["goal"], self.requests[1]["goal"]
        )

    def test_check_and_full_worker_reservations_bound_partial_success(self):
        for budget in ({"max_checks": 1}, {"worker_seconds": 30}):
            row = self.run_fake({**self.value, "budget": {**self.value["budget"], **budget}})
            self.assertEqual((row["state"], row["math_verdict"]), ("BUDGET_EXHAUSTED", "UNKNOWN"))
            self.assertEqual(row["node_states"]["middle"]["math_verdict"], "PROVED")
            self.assertEqual(row["node_states"]["problem"]["math_verdict"], "UNKNOWN")
            self.assertEqual(row["used"]["checks"], 1)

    def test_withdrawal_is_persistent_and_old_submission_does_not_rerun(self):
        body = reviewed(self.store, self.value)
        row = self.store.submit(body)
        with patch.object(CampaignChecks, "execute", synthetic):
            self.store.work_once(row["campaign_id"])
        raw = {
            "request_id": rid(),
            "node_id": "middle",
            "reviewer": "NONEXECUTABLE",
            "reason": "retire",
            "confirmed": True,
        }
        row = self.store.withdraw(row["campaign_id"], raw)
        self.assertEqual(row["node_states"]["problem"]["math_verdict"], "UNKNOWN")
        self.assertTrue(next(n for n in row["nodes"] if n["node_id"] == "problem")["invalidated"])
        self.assertEqual(self.store.withdraw(row["campaign_id"], raw)["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.submit(body)["math_verdict"], "UNKNOWN")

    def test_source_alignment_and_restored_child_bytes_do_not_revive_descendants(self):
        row = self.run_fake()
        key = row["campaign_id"]
        node = next(n for n in row["nodes"] if n["node_id"] == "middle")
        p = CampaignChecks(self.store, key).directory(node["child_id"]) / "replay/proof.ndjson"
        original = p.read_bytes()
        p.write_bytes(b"broken")
        self.assertEqual(self.store.report(key)["math_verdict"], "UNKNOWN")
        p.write_bytes(original)
        self.assertEqual(self.store.report(key)["math_verdict"], "UNKNOWN")
        other = self.run_fake()
        self.fake["alignment"]["reviewer"] = "changed"
        self.assertEqual(self.store.report(other["campaign_id"])["math_verdict"], "UNKNOWN")

    def test_implementation_and_original_context_drift_stay_invalid_after_restoration(self):
        row = self.run_fake()
        with patch.object(self.store, "manifest", return_value={"changed": True}):
            self.assertEqual(self.store.report(row["campaign_id"])["math_verdict"], "UNKNOWN")
        self.assertTrue(self.store.report(row["campaign_id"])["invalidated"])
        other = self.run_fake()
        original = self.store.flows.parent

        def changed(*args):
            return {**original(*args), "context_fingerprint": "changed"}

        with patch.object(self.store.flows, "parent", side_effect=changed):
            self.assertEqual(self.store.report(other["campaign_id"])["math_verdict"], "UNKNOWN")
        self.assertTrue(self.store.report(other["campaign_id"])["invalidated"])
        self.assertEqual(self.store.report(other["campaign_id"])["math_verdict"], "UNKNOWN")

    def test_temporary_locks_leave_healthy_campaign_unchanged(self):
        row = self.run_fake()
        key = row["campaign_id"]
        before = self.store.read(key)
        with patch.object(self.store, "existing", side_effect=WorkerBusy("busy")):
            with self.assertRaises(WorkerBusy):
                self.store.report(key)
        with patch.object(
            self.store, "existing", side_effect=sqlite3.OperationalError("database is locked")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.report(key)
        self.assertEqual(self.store.read(key), before)
        with self.store.tasks.worker_lock(), self.assertRaises(WorkerBusy):
            self.store.recover()

    def test_concurrent_status_reader_cannot_interrupt_a_live_node_commit(self):
        entered, release = threading.Event(), threading.Event()
        original = self.store.existing
        errors, readers = [], []

        def slow_read(ref, node):
            if threading.current_thread().name == "campaign-status-reader":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Reader release timed out")
            return original(ref, node)

        def inspect(key):
            try:
                self.store.report(key)
            except Exception as error:
                errors.append(error)

        def finish(checker, run_id, fd):
            result = synthetic(checker, run_id, fd)
            if not readers:
                thread = threading.Thread(
                    target=inspect, args=(checker.key,), name="campaign-status-reader"
                )
                readers.append(thread)
                thread.start()
                self.assertTrue(entered.wait(5))
                threading.Timer(0.15, release.set).start()
            return result

        row = self.store.submit(reviewed(self.store, self.value))
        try:
            with (
                patch.object(self.store, "existing", side_effect=slow_read),
                patch.object(CampaignChecks, "execute", finish),
            ):
                self.store.work_once(row["campaign_id"])
        finally:
            release.set()
            for thread in readers:
                thread.join(5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in readers))
        self.assertEqual(self.store.report(row["campaign_id"])["math_verdict"], "PROVED")

    def test_record_damage_marker_survives_restoration_and_restart_never_promotes(self):
        row = self.run_fake()
        key = row["campaign_id"]
        p = self.store.directory(key) / "record.json"
        original = p.read_bytes()
        p.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.store.report(key)
        p.write_bytes(original)
        self.assertEqual(self.store.report(key)["math_verdict"], "UNKNOWN")
        row = self.store.submit(reviewed(self.store, self.value))
        key = row["campaign_id"]
        current = self.store.read(key)
        current["state"] = "RUNNING"
        current["nodes"][0]["state"] = "RUNNING"
        self.store.save(current)
        self.store.recover()
        self.assertEqual(
            (self.store.report(key)["state"], self.store.report(key)["math_verdict"]),
            ("INTERRUPTED", "UNKNOWN"),
        )

    def test_cancel_during_child_and_wall_expiry_cannot_publish_raw_success(self):
        for stop in ("cancel", "deadline"):

            def stopping(checker, run_id, fd):
                result = synthetic(checker, run_id, fd)
                if stop == "cancel":
                    self.store.cancel(checker.key)
                else:
                    row = self.store.read(checker.key)
                    row["deadline"] = 0
                    self.store.save(row)
                return result

            row = self.store.submit(reviewed(self.store, self.value))
            with patch.object(CampaignChecks, "execute", stopping):
                self.store.work_once(row["campaign_id"])
            row = self.store.report(row["campaign_id"])
            self.assertEqual(row["state"], "CANCELLED" if stop == "cancel" else "BUDGET_EXHAUSTED")
            self.assertEqual(row["math_verdict"], "UNKNOWN")
            self.assertEqual(row["node_states"]["middle"]["math_verdict"], "UNKNOWN")


def real_store(base):
    library = real_library(base / "private-library")
    store = ResearchCampaignStore(base / "campaign-data", library)
    store.root = ROOT
    store.flows.root = ROOT
    return store


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine Lean graph campaign integration"
)
class RealTests(unittest.TestCase):
    def test_three_new_nodes_import_actual_predecessor_proofs_and_damage_restore_stays_unknown(
        self,
    ):
        out = OUT / ("real-chain-" + uuid.uuid4().hex)
        out.mkdir()
        store = real_store(out)
        library = store.library
        first = library.admit(admission(library, "mathlib-c2d3df2f5585479b9c4bad13f1270db9"))
        seed = application(
            first["item_id"], first["content"]["source"]["request"], title="研究批次种子完整合取"
        )
        middle = application("node.seed", seed, title="研究批次加入自然数自身gcd")
        a = {"var": middle["variables"][0]}
        middle["goal"] = {"and": [middle["goal"], {"eq": [{"gcd": [a, a]}, a]}]}
        middle["proof"] = {
            "pair": [middle["proof"], {"ref": "Nat.gcd_self", "terms": [a], "proofs": []}]
        }
        problem = application("node.middle", middle, title="研究批次原问题的完整组合")
        flow = create(
            store.flows,
            parent(out / "campaign-data"),
            graph([seed, middle, problem]),
        )
        value = campaign_input(
            flow,
            {
                k: {"kind": "compose", "request": r}
                for k, r in zip(("seed", "middle", "problem"), (seed, middle, problem))
            },
        )
        body = reviewed(store, value)
        body["reviewer"] = "MRA051 genuine full-program and graph reviewer"
        row = store.submit(body)
        store.work_once(row["campaign_id"])
        row = store.report(row["campaign_id"])
        self.assertEqual(row["math_verdict"], "PROVED", row)
        self.assertEqual(row["used"]["checks"], 3)
        (out / "proved.json").write_bytes(canonical(row) + b"\n")
        child = CampaignChecks(store, row["campaign_id"])
        seed_row = row["nodes"][0]
        artifact = child.directory(seed_row["child_id"]) / "replay/proof.ndjson"
        original = artifact.read_bytes()
        artifact.write_bytes(b"changed")
        self.assertEqual(store.report(row["campaign_id"])["math_verdict"], "UNKNOWN")
        artifact.write_bytes(original)
        restored = store.report(row["campaign_id"])
        self.assertEqual(restored["math_verdict"], "UNKNOWN")
        (out / "damaged-restored.json").write_bytes(canonical(restored) + b"\n")
        library.retire(first["item_id"], retirement())

    def test_genuine_conditional_node_rejects_wrong_premise_and_budget_stops_before_next_node(self):
        out = OUT / ("real-boundaries-" + uuid.uuid4().hex)
        out.mkdir()
        store = real_store(out)
        source = store.library.legacy.report("mathlib-c2d3df2f5585479b9c4bad13f1270db9")
        seed = source["plan"]["request"]
        middle = application("node.seed", seed, title="研究批次保留互素条件")
        a, b = [{"var": v} for v in middle["variables"][:2]]
        middle["assumptions"] = [{"name": "hcoprime", "formula": {"coprime": [a, b]}}]
        middle["goal"] = {"and": [middle["goal"], {"eq": [{"gcd": [a, b]}, {"nat": 1}]}]}
        middle["proof"] = {
            "pair": [
                middle["proof"],
                {"ref": "Nat.Coprime.gcd_eq_one", "terms": [a, b], "proofs": [{"hyp": "hcoprime"}]},
            ]
        }
        problem = application("node.middle", middle, title="研究批次错误条件证明")
        problem["proof"]["proofs"] = [{"ref": "Nat.dvd_refl", "terms": [a], "proofs": []}]
        flow = create(store.flows, parent(out / "campaign-data"), graph([seed, middle, problem]))
        value = campaign_input(
            flow,
            {
                "seed": {
                    "kind": "existing",
                    "source": {"origin": "composition", "run_id": source["run_id"]},
                },
                "middle": {"kind": "compose", "request": middle},
                "problem": {"kind": "compose", "request": problem},
            },
        )
        row = store.submit(reviewed(store, value))
        store.work_once(row["campaign_id"])
        row = store.report(row["campaign_id"])
        self.assertEqual((row["state"], row["math_verdict"]), ("FAILED", "UNKNOWN"), row)
        self.assertEqual(row["node_states"]["middle"]["math_verdict"], "PROVED", row)
        rejected = CampaignChecks(store, row["campaign_id"]).report(row["nodes"][-1]["child_id"])
        self.assertEqual(rejected["state"], "REJECTED", rejected)
        limited = store.submit(
            reviewed(store, {**value, "budget": {**value["budget"], "max_checks": 1}})
        )
        store.work_once(limited["campaign_id"])
        limited = store.report(limited["campaign_id"])
        self.assertEqual(
            (limited["state"], limited["used"]["checks"]), ("BUDGET_EXHAUSTED", 1), limited
        )
        (out / "results.json").write_bytes(canonical({"rejected": row, "limited": limited}) + b"\n")

    def test_supervisor_exit_after_completed_child_never_commits_graph_and_queued_cancel_never_runs(
        self,
    ):
        out = OUT / ("real-interrupted-" + uuid.uuid4().hex)
        out.mkdir()
        store = real_store(out)
        first = store.library.admit(
            admission(store.library, "mathlib-c2d3df2f5585479b9c4bad13f1270db9")
        )
        req = application(
            first["item_id"], first["content"]["source"]["request"], title="监督退出不发布研究结果"
        )
        flow = create(store.flows, parent(out / "campaign-data"), graph([req]))
        value = campaign_input(flow, {"problem": {"kind": "compose", "request": req}})
        body = reviewed(store, value)
        row = store.submit(body)
        script = "\n".join(
            [
                "import os,sys",
                "from pathlib import Path",
                "from test_research_campaign import real_store",
                "from mra.campaign_checks import CampaignChecks",
                "original=CampaignChecks.execute",
                "def stop(self,*args):",
                "    result=original(self,*args)",
                "    os._exit(73)",
                "CampaignChecks.execute=stop",
                "real_store(Path(sys.argv[1])).work_once(sys.argv[2])",
            ]
        )
        process = subprocess.run(
            [sys.executable, "-c", script, str(out), row["campaign_id"]],
            capture_output=True,
            text=True,
            timeout=240,
        )
        (out / "stdout.log").write_text(process.stdout)
        (out / "stderr.log").write_text(process.stderr)
        self.assertEqual(process.returncode, 73, process.stderr)
        raw = store.read(row["campaign_id"])
        child = CampaignChecks(store, row["campaign_id"]).report(raw["nodes"][0]["child_id"])
        self.assertEqual(child["state"], "COMPLETED", child)
        store.recover()
        row = store.report(row["campaign_id"])
        self.assertEqual((row["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))
        self.assertEqual(store.submit(body)["state"], "INTERRUPTED")
        cancelled = store.submit(reviewed(store, value))
        store.cancel(cancelled["campaign_id"])
        self.assertEqual(store.work_once(cancelled["campaign_id"])["worker_state"], "IDLE")
        (out / "results.json").write_bytes(
            canonical(
                {
                    "raw_child": child,
                    "recovered": row,
                    "cancelled": store.report(cancelled["campaign_id"]),
                }
            )
            + b"\n"
        )
        store.library.retire(first["item_id"], retirement())


if __name__ == "__main__":
    unittest.main()
