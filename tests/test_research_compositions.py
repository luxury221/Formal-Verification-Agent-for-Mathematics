"""Synthetic association contracts, plus explicit real archived-proof integration."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical, digest
from mra.lemma_composition import FILES
from mra.lemma_composition_terms import compile_composition
from mra.mathlib_knowledge import MathlibKnowledgeStore
from mra.research_compositions import ResearchCompositionStore, complete_target
from mra.research_flow import SOURCE_FILES, lease
from mra.tasks import WorkerBusy
from mra.verifier import NativeLeanBackend
from test_http_api import request, running_server
from test_lemma_composition import composition, signatures, real_store, admit
from test_research_flow import create as create_flow, parent
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
BASE = "/v1/research-compositions/"


def rid():
    return "composition-link-" + uuid.uuid4().hex


def graph(requests):
    return [
        {
            "id": "problem" if i == len(requests) - 1 else "seed" if i == 0 else "middle",
            "kind": "problem" if i == len(requests) - 1 else "lemma",
            "target": complete_target(r),
            "depends_on": [] if i == 0 else ["seed" if i == 1 else "middle"],
        }
        for i, r in enumerate(requests)
    ]


def submission(store, key, node, run_id):
    plan = store.preview(key, {"node_id": node, "run_id": run_id})
    return {
        "node_id": node,
        "run_id": run_id,
        "request_id": rid(),
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "MRA049 named association reviewer",
        "confirmed": True,
    }


def withdrawal(row, node="seed"):
    binding = next(
        b for b in row["bindings"] if b["state"] == "ACTIVE" and b["plan"]["node_id"] == node
    )
    return {
        "binding_id": binding["binding_id"],
        "request_id": rid(),
        "reviewer": "MRA049 withdrawal reviewer",
        "reason": "Retire this exact evidence dependency",
        "confirmed": True,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in (
            *FILES,
            *SOURCE_FILES,
            "mra/proof_steps.py",
            "mra/research_evidence.py",
            "mra/research_continuation.py",
            "mra/research_compositions.py",
        ):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, p)
        self.store = ResearchCompositionStore(self.root)
        value = composition()
        sources = [
            {**b, "request": signature} for b, signature in zip(value["sources"], signatures())
        ]
        plan = compile_composition(value, sources)
        self.run_id = "mathlib-" + uuid.uuid4().hex
        self.fake = {
            "run_id": self.run_id,
            "plan": plan,
            "state": "COMPLETED",
            "math_verdict": "PROVED",
            "evidence_level": "E4",
            "invalidated": False,
            "cancel_requested": False,
            "artifact_status": "MATCHES_RECORDED_HASHES",
            "alignment": {"confirmed": True, "reviewer": "NONEXECUTABLE FIXTURE"},
            "files": {"fixture": "not-real"},
            "submission_fingerprint": "not-real",
            "created_at": "2026-10-28T00:00:00+00:00",
            "result": {
                "candidate": {"pid": 101, "audit": {"type_matches": True}},
                "replay": {"pid": 102, "audit": {"mode": "data-only-kernel-replay-v1"}},
            },
        }
        self.flow = create_flow(
            self.store.continuations.links.flows, parent(self.root), graph([value] * 3)
        )
        self.key = self.flow["flow_id"]
        self.mock = patch.object(
            self.store.compositions, "report", side_effect=lambda _: deepcopy(self.fake)
        )
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def bind(self, node="seed"):
        raw = submission(self.store, self.key, node, self.run_id)
        return self.store.bind(self.key, raw), raw

    def test_readonly_preview_preserves_complete_conditions_without_lean_or_ledger(self):
        with patch.object(NativeLeanBackend, "_run_worker", side_effect=AssertionError("No Lean")):
            p = self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})
        self.assertEqual(
            (p["math_verdict"], p["evidence_level"], p["lean_calls"]), ("UNKNOWN", "E0", 0)
        )
        self.assertEqual(p["context"]["target"]["assumptions"], composition()["assumptions"])
        self.assertFalse(self.store.directory(self.key).exists())

    def test_exact_named_idempotency_and_stale_plan_guard(self):
        raw = submission(self.store, self.key, "seed", self.run_id)
        for change in (
            {"confirmed": False},
            {"confirmed": 1},
            {"reviewer": " "},
            {"plan_fingerprint": "old"},
            {"extra": True},
        ):
            with self.assertRaises(ValueError):
                self.store.bind(self.key, {**raw, **change})
        first = self.store.bind(self.key, raw)
        self.assertEqual(self.store.bind(self.key, raw), first)
        with self.assertRaises(ValueError):
            self.store.bind(self.key, {**raw, "reviewer": "Different name"})
        stale = submission(self.store, self.key, "middle", self.run_id)
        self.store.withdraw(self.key, withdrawal(first))
        with self.assertRaises(ValueError):
            self.store.bind(self.key, stale)

    def test_no_field_of_full_target_can_be_weakened_or_reordered(self):
        original = deepcopy(self.fake)
        for field, value in (
            ("original", "Different original"),
            ("title", "Other title"),
            ("variables", ["x", "d", "y"]),
            ("assumptions", []),
            ("goal", {"eq": [{"nat": 0}, {"nat": 0}]}),
            ("timeout_seconds", 29),
        ):
            self.fake = deepcopy(original)
            self.fake["plan"]["request"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})
        self.fake = deepcopy(original)
        self.fake["plan"]["formal_statement"] = "0=0"
        with self.assertRaises(ValueError):
            self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})

    def test_only_intact_completed_aligned_e4_with_independent_replay_qualifies(self):
        original = deepcopy(self.fake)
        for change in (
            {"state": "INTERRUPTED"},
            {"state": "RUNNING"},
            {"math_verdict": "UNKNOWN"},
            {"math_verdict": "REFUTED"},
            {"evidence_level": "E3"},
            {"invalidated": True},
            {"cancel_requested": True},
            {"artifact_status": "UNAVAILABLE"},
            {"alignment": {"confirmed": False}},
        ):
            self.fake = {**deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})
        self.fake = deepcopy(original)
        self.fake["result"]["replay"]["pid"] = 101
        with self.assertRaises(ValueError):
            self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})

    def test_topological_full_problem_requires_its_own_proof_and_cascades_withdrawal(self):
        with self.assertRaises(ValueError):
            submission(self.store, self.key, "problem", self.run_id)
        row, seed = self.bind()
        row, middle = self.bind("middle")
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        row, problem = self.bind("problem")
        self.assertEqual(row["math_verdict"], "PROVED")
        self.assertEqual(row["research_task"]["math_verdict"], "UNKNOWN")
        row = self.store.withdraw(self.key, withdrawal(row))
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(
            [b["state"] for b in row["bindings"]], ["WITHDRAWN", "INVALIDATED", "INVALIDATED"]
        )
        self.assertEqual(self.store.bind(self.key, seed)["math_verdict"], "UNKNOWN")
        self.bind()
        for old in (middle, problem):
            self.assertEqual(self.store.bind(self.key, old)["math_verdict"], "UNKNOWN")

    def test_selected_source_fingerprint_change_invalidates_after_bytes_restore(self):
        self.bind()
        self.bind("middle")
        original = deepcopy(self.fake)
        self.fake["alignment"]["reviewer"] = "Changed"
        row = self.store.report(self.key)
        self.assertEqual([b["state"] for b in row["bindings"]], ["INVALIDATED", "INVALIDATED"])
        self.fake = original
        self.assertTrue(
            all(b["state"] == "INVALIDATED" for b in self.store.report(self.key)["bindings"])
        )

    def test_transient_source_lock_and_own_lease_never_write_damage(self):
        self.bind()
        for error in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
            with (
                patch.object(self.store.compositions, "report", side_effect=error),
                self.assertRaises(type(error)),
            ):
                self.store.report(self.key)
            with self.store.access(self.key) as (_, row):
                self.assertEqual(row["bindings"][0]["state"], "ACTIVE")
        with lease(self.store.directory(self.key)), self.assertRaises(WorkerBusy):
            self.store.report(self.key)
        self.assertFalse((self.store.directory(self.key) / "invalidated.json").exists())

    def test_unselected_alternative_is_stable_but_replacing_selected_prerequisite_cascades(self):
        self.bind()
        row, old_middle = self.bind("middle")
        selected = row["node_evidence"]["seed"]["binding_fingerprint"]
        other_run = "mathlib-" + uuid.uuid4().hex
        row = self.store.bind(self.key, submission(self.store, self.key, "seed", other_run))
        self.assertEqual(row["node_evidence"]["seed"]["binding_fingerprint"], selected)
        self.assertEqual(row["node_evidence"]["middle"]["math_verdict"], "PROVED")
        row = self.store.withdraw(self.key, withdrawal(row))
        self.assertEqual(row["node_evidence"]["seed"]["math_verdict"], "PROVED")
        self.assertNotEqual(row["node_evidence"]["seed"]["binding_fingerprint"], selected)
        self.assertEqual(row["node_evidence"]["middle"]["math_verdict"], "UNKNOWN")
        self.assertEqual(
            self.store.bind(self.key, old_middle)["bindings"][1]["state"], "INVALIDATED"
        )

    def test_changed_parent_context_and_verdict_conflict_fail_closed(self):
        self.bind()
        flow, inherited = self.store.baseline(self.key)
        changed = deepcopy(flow)
        changed["plan"]["parent"]["source_id"] = "changed-parent"
        with patch.object(self.store, "baseline", return_value=(changed, inherited)):
            self.assertEqual(self.store.report(self.key)["bindings"][0]["state"], "INVALIDATED")
        self.assertEqual(self.store.report(self.key)["bindings"][0]["state"], "INVALIDATED")
        self.bind()
        flow, inherited = self.store.baseline(self.key)
        inherited["seed"] = {
            "math_verdict": "REFUTED",
            "source": {"origin": "SYNTHETIC_CONFLICT"},
            "binding_fingerprint": "nonexecutable-fixture",
        }
        with patch.object(self.store, "baseline", return_value=(flow, inherited)):
            row = self.store.report(self.key)
        self.assertEqual(row["node_evidence"]["seed"]["math_verdict"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.bind(self.key, submission(self.store, self.key, "seed", self.run_id))

    def test_corruption_restore_and_cross_flow_plan_never_revive_confirmation(self):
        self.bind()
        path = self.store.directory(self.key) / "record.json"
        data = path.read_bytes()
        path.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.report(self.key)
        path.write_bytes(data)
        row = self.store.report(self.key)
        self.assertTrue(row["invalidated"])
        self.assertEqual(row["math_verdict"], "UNKNOWN")

    def test_other_flow_cannot_reuse_the_first_confirmation(self):
        raw = submission(self.store, self.key, "seed", self.run_id)
        other = create_flow(
            self.store.continuations.links.flows, parent(self.root), graph([composition()] * 3)
        )
        with self.assertRaises(ValueError):
            self.store.bind(other["flow_id"], raw)

    def test_catalog_tags_history_as_pending_reaudit_and_source_preview_remains_authoritative(self):
        with self.store.compositions.transaction() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?)",
                (self.run_id, rid(), canonical(self.fake).decode(), digest(self.fake)),
            )
        c = self.store.catalog(self.key, "seed")
        self.assertEqual(c["candidates"][0]["status"], "RECORDED_E4_REQUIRES_PREVIEW")
        self.fake["invalidated"] = True
        with self.assertRaises(ValueError):
            self.store.preview(self.key, {"node_id": "seed", "run_id": self.run_id})

    def test_http_security_strict_json_bind_export_withdraw_and_busy(self):
        with running_server(self.root) as server:
            server.research_compositions = self.store
            url = BASE + self.key
            raw = submission(self.store, self.key, "seed", self.run_id)
            self.assertEqual(request(server, "GET", url)[0], 200)
            self.assertEqual(request(server, "GET", url + "/nodes/seed")[0], 200)
            self.assertEqual(
                request(
                    server, "POST", url + "/preview", {"node_id": "seed", "run_id": self.run_id}
                )[1]["math_verdict"],
                "UNKNOWN",
            )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    url + "/bind",
                    raw,
                    headers={"Origin": "https://invalid.example"},
                )[0],
                403,
            )
            self.assertEqual(
                request(server, "POST", url + "/bind", raw, headers={"X-MRA-CSRF": "bad"})[0], 403
            )
            self.assertEqual(
                request(server, "POST", url + "/preview", raw=b'{"node_id":"x","node_id":"y"}')[0],
                400,
            )
            self.assertEqual(
                request(server, "POST", url + "/bind", raw)[1]["node_evidence"]["seed"][
                    "math_verdict"
                ],
                "PROVED",
            )
            with patch.object(self.store.compositions, "report", side_effect=WorkerBusy("busy")):
                self.assertEqual(request(server, "GET", url)[0], 503)
            row = request(
                server, "POST", url + "/withdraw", withdrawal(self.store.report(self.key))
            )[1]
            self.assertEqual(row["bindings"][0]["state"], "WITHDRAWN")


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Explicit genuine proof integration only"
)
class RealTests(unittest.TestCase):
    def test_real_private_source_compositions_link_two_nodes_and_persist_cascade(self):
        out = ROOT / "artifacts/research-composition-20261028-01" / ("real-" + uuid.uuid4().hex)
        out.mkdir(parents=True)
        # Copy a genuine existing source into a new private archive. No old source is retired.
        compositions = real_store(
            out / "compositions", source_lab=MathlibKnowledgeStore(ROOT).sources.mathlib
        )
        item = admit(compositions.knowledge, "mathlib-8fd1f2f72a8e4f5e80dedbfe01f63a3d")
        source = compositions.knowledge.report(item["item_id"])["content"]["source"]["request"]
        first = {
            **source,
            "schema": "private-lemma-composition-v1",
            "sources": [{"alias": "boundary", "item_id": item["item_id"]}],
            "proof": {
                "lemma": "boundary",
                "terms": [{"var": v} for v in source["variables"]],
                "proofs": [],
            },
        }
        second = deepcopy(first)
        second.update(
            title="研究图中的两个截断减法实例",
            original="对每个自然数a、b，同时成立两份相同的截断减法等价式。",
            goal={"and": [source["goal"], source["goal"]]},
            proof={"pair": [deepcopy(first["proof"]), deepcopy(first["proof"])]},
        )
        runs = []
        for value in (first, second):
            plan = compositions.preview(value)
            row = compositions.submit(
                {
                    "request_id": rid(),
                    "request": value,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "reviewer": "MRA049 real full-target alignment reviewer",
                    "confirmed": True,
                }
            )
            compositions.work_once(row["run_id"])
            row = compositions.report(row["run_id"])
            self.assertEqual(row["math_verdict"], "PROVED")
            runs.append(row)
        store = ResearchCompositionStore(out / "research")
        store.root = ROOT
        store.compositions = compositions
        store.continuations.root = ROOT
        store.continuations.links.root = ROOT
        store.continuations.links.flows.root = ROOT
        project = parent(out / "research")
        flow = create_flow(store.continuations.links.flows, project, graph([first, second]))
        key = flow["flow_id"]
        before = store.continuations.links.flows.directory(key) / "record.json"
        original_bytes = before.read_bytes()
        first_raw = submission(store, key, "seed", runs[0]["run_id"])
        middle = store.bind(key, first_raw)
        self.assertEqual(middle["math_verdict"], "UNKNOWN")
        last_raw = submission(store, key, "problem", runs[1]["run_id"])
        proved = store.bind(key, last_raw)
        self.assertEqual((proved["math_verdict"], proved["evidence_level"]), ("PROVED", "E4"))
        self.assertEqual(proved["research_task"]["math_verdict"], "UNKNOWN")
        self.assertEqual(original_bytes, before.read_bytes())
        (out / "proved.json").write_text(
            json.dumps(proved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        withdrawn = store.withdraw(key, withdrawal(proved))
        self.assertEqual(withdrawn["bindings"][1]["state"], "INVALIDATED")
        store.bind(key, first_raw)
        self.assertEqual(store.report(key)["math_verdict"], "UNKNOWN")
        seed_again = store.bind(key, submission(store, key, "seed", runs[0]["run_id"]))
        self.assertEqual(seed_again["math_verdict"], "UNKNOWN")
        self.assertEqual(store.bind(key, last_raw)["math_verdict"], "UNKNOWN")
        relinked = store.bind(key, submission(store, key, "problem", runs[1]["run_id"]))
        self.assertEqual(relinked["math_verdict"], "PROVED")
        artifact = compositions.directory(runs[1]["run_id"]) / next(iter(runs[1]["files"]))
        original_artifact = artifact.read_bytes()
        artifact.write_bytes(original_artifact + b"\nMRA049 intentional corruption\n")
        damaged = store.report(key)
        self.assertEqual(damaged["math_verdict"], "UNKNOWN")
        artifact.write_bytes(original_artifact)
        self.assertEqual(store.report(key)["math_verdict"], "UNKNOWN")
        self.assertTrue(compositions.report(runs[1]["run_id"])["invalidated"])
        (out / "damaged-restored.json").write_text(
            json.dumps(store.report(key), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Revoke only this isolated private source. Both runs and all active associations stop.
        compositions.knowledge.retire(
            item["item_id"],
            {
                "reviewer": "MRA049 source retirement reviewer",
                "confirmed": True,
                "reason": "Isolated cascade acceptance",
                "request_id": rid(),
            },
            remove=False,
        )
        retired = store.report(key)
        self.assertEqual(retired["math_verdict"], "UNKNOWN")
        self.assertTrue(all(b["state"] != "ACTIVE" for b in retired["bindings"]))
        (out / "retired.json").write_text(
            json.dumps(retired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out / "fixture.json").write_text(
            json.dumps(
                {
                    "flow_id": key,
                    "project_id": project["project_id"],
                    "runs": [r["run_id"] for r in runs],
                    "data": str(out.relative_to(ROOT)),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
