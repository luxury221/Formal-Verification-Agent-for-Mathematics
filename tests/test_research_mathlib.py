"""Research integration gates; positive proof evidence always comes from real Lean."""

import copy
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

from mra.mathlib_terms import compile_request, examples
from mra.research_mathlib import ResearchMathlibStore, validate_nodes
from test_http_api import copy_web_inputs, request, running_server
from test_mathlib_lab import isolated_store, submit

ROOT = Path(__file__).resolve().parents[1]


def make_store(mathlib=None):
    root = ROOT / "artifacts/research-mathlib-tests" / uuid.uuid4().hex
    store = ResearchMathlibStore(root)
    store.root = ROOT
    store.mathlib = mathlib or isolated_store()
    project = store.research.create(
        {
            "request_id": uuid.uuid4().hex,
            "title": "Mathlib research test",
            "question": "Track complete divisibility statements and real evidence",
        }
    )
    return store, project["project_id"]


def context(store, project, branch="main"):
    row = store.report(project, branch)
    return {
        "branch_id": branch,
        "expected_revision": row["revision"],
        "parent_context_fingerprint": row["parent_context_fingerprint"],
    }


def node(key="lemma", deps=None, value=None, kind="lemma"):
    return {
        "id": key,
        "kind": kind,
        "request": compile_request(value or examples()[0])["request"],
        "depends_on": deps or [],
        "notes": "",
    }


def edit(store, project, nodes, branch="main", **changes):
    return store.edit(
        project,
        {
            **context(store, project, branch),
            "request_id": uuid.uuid4().hex,
            "nodes": nodes,
            **changes,
        },
    )


def attachment(store, project, run_id, key="lemma", deps=None, kind="lemma", branch="main"):
    data = {
        **context(store, project, branch),
        "node_id": key,
        "kind": kind,
        "depends_on": deps or [],
        "notes": "",
        "run_id": run_id,
    }
    preview = store.preview_attachment(project, data)
    return {
        **data,
        "request_id": uuid.uuid4().hex,
        "reviewer": "Real Lean research reviewer",
        "confirmed": True,
        "attachment_fingerprint": preview["attachment_fingerprint"],
    }


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.store, self.project = make_store()

    def test_read_only_empty_scope_and_strict_graph_limits(self):
        before = self.store.path.read_bytes()
        row = self.store.report(self.project, "main")
        self.assertEqual((row["revision"], row["nodes"]), (0, []))
        self.assertEqual(before, self.store.path.read_bytes())
        with self.assertRaises(ValueError):
            validate_nodes([node(str(i)) for i in range(17)])
        for nodes in (
            [node("x", ["missing"])],
            [node("x", ["y"]), node("y", ["x"])],
            [node("x"), node("x")],
            [node("x", ["x", "x"])],
            [{**node(), "extra": True}],
        ):
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                validate_nodes(nodes)

    def test_normalized_domain_and_complete_request_required(self):
        for change in (
            {"schema": "int-paper-review-v1"},
            {"variables": ["a", "a"]},
            {"goal": {"prime": [{"var": "a"}]}},
            {"timeout_seconds": True},
        ):
            value = node()
            value["request"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                edit(self.store, self.project, [value])

    def test_versions_optimistic_concurrency_and_idempotency(self):
        data = {
            **context(self.store, self.project),
            "request_id": uuid.uuid4().hex,
            "nodes": [node()],
        }
        row = self.store.edit(self.project, data)
        self.assertEqual(row["revision"], 1)
        self.assertEqual(self.store.edit(self.project, data)["revision"], 1)
        with self.assertRaises(ValueError):
            self.store.edit(self.project, {**data, "nodes": []})
        with self.assertRaises(ValueError):
            self.store.edit(self.project, {**data, "request_id": uuid.uuid4().hex})
        history = self.store.history(self.project, "main")
        self.assertEqual(history["versions"][0]["nodes"], [node()])
        self.assertFalse(self.store.export(self.project, "main")["fresh_attestation"])

    def test_real_queued_and_cancelled_sources_cannot_be_bound(self):
        run = submit(self.store.mathlib)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "COMPLETED/PROVED/E4"):
                attachment(self.store, self.project, run["run_id"])
            self.store.mathlib.cancel(run["run_id"])
        self.assertEqual(self.store.report(self.project, "main")["revision"], 0)

    def test_parent_and_side_sqlite_locks_do_not_change_graph(self):
        edit(self.store, self.project, [node()])
        for path in (self.store.research.path, self.store.path):
            with sqlite3.connect(path, isolation_level=None) as lock:
                lock.execute("BEGIN EXCLUSIVE")
                with self.assertRaises(sqlite3.OperationalError):
                    self.store.report(self.project, "main")
                lock.rollback()
        self.assertEqual(self.store.report(self.project, "main")["revision"], 1)

    def test_corrupt_scope_and_revision_ledgers_rejected(self):
        edit(self.store, self.project, [node()])
        with self.store.transaction() as db:
            db.execute("UPDATE revisions SET hash='bad'")
        with self.assertRaises(ValueError):
            self.store.history(self.project, "main")
        with self.store.transaction() as db:
            db.execute("UPDATE scopes SET hash='bad'")
        with self.assertRaises(ValueError):
            self.store.report(self.project, "main")
        with self.assertRaises(ValueError):
            self.store.report("../../outside", "main")

    def test_concurrent_edit_commits_one_version(self):
        data = {**context(self.store, self.project), "nodes": [node()]}
        results = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            try:
                self.store.edit(self.project, {**data, "request_id": uuid.uuid4().hex})
                results.append("ok")
            except ValueError:
                results.append("conflict")

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertCountEqual(results, ["ok", "conflict"])

    def test_http_exact_schemas_origin_guards_and_assets(self):
        root = self.store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.research_mathlib = self.store
            route = "/v1/research/" + self.project + "/mathlib/"
            for path in (
                route + "main",
                route + "main/history",
                route + "main/export",
                "/research-mathlib.js",
                "/research-mathlib.css",
                "/v1/research/" + self.project + "/mathlib-branches",
            ):
                self.assertEqual(request(server, "GET", path)[0], 200, path)
            data = {
                **context(self.store, self.project),
                "nodes": [node()],
                "request_id": uuid.uuid4().hex,
            }
            for headers in ({"Origin": "https://elsewhere.test"}, {"X-MRA-CSRF": "bad"}):
                self.assertEqual(
                    request(server, "POST", route + "edit", data, headers=headers)[0], 403
                )
            self.assertEqual(request(server, "POST", route + "edit", {**data, "extra": 1})[0], 400)
            self.assertEqual(request(server, "POST", route + "edit", data)[0], 200)
            self.assertEqual(
                request(server, "GET", route + "main")[1]["nodes"][0]["math_verdict"], "UNKNOWN"
            )

    def test_cli_report_rejects_invalid_project_path(self):
        result = subprocess.run(
            [sys.executable, "-m", "mra", "research-mathlib-report", "../../outside", "main"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires actual Lean and replay")
class RealEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mathlib = isolated_store()
        row = submit(cls.mathlib)
        cls.mathlib.work_once(row["run_id"])
        cls.run_id = row["run_id"]
        result = cls.mathlib.report(cls.run_id)
        if result["math_verdict"] != "PROVED":
            raise AssertionError(result)
        failed = submit(cls.mathlib, examples()[3])
        cls.mathlib.work_once(failed["run_id"])
        cls.failed_id = failed["run_id"]

    def setUp(self):
        self.store, self.project = make_store(self.mathlib)

    def bind(self, key="lemma", deps=None, kind="lemma", run_id=None, branch="main"):
        return self.store.attach(
            self.project,
            attachment(self.store, self.project, run_id or self.run_id, key, deps, kind, branch),
        )

    def test_attach_real_e4_preserves_original_and_full_statement(self):
        self.assertNotEqual(
            self.mathlib.report(self.run_id)["result"]["candidate"]["pid"],
            self.mathlib.report(self.run_id)["result"]["replay"]["pid"],
        )
        data = attachment(self.store, self.project, self.run_id)
        for change in ({"confirmed": False}, {"attachment_fingerprint": "old"}, {"reviewer": ""}):
            with self.assertRaises(ValueError):
                self.store.attach(self.project, {**data, **change})
        row = self.store.attach(self.project, data)
        self.assertEqual(
            (row["nodes"][0]["math_verdict"], row["nodes"][0]["evidence_level"]), ("PROVED", "E4")
        )
        self.assertEqual(
            row["nodes"][0]["request"], self.mathlib.report(self.run_id)["plan"]["request"]
        )
        self.assertEqual(self.store.attach(self.project, data)["revision"], row["revision"])

    def test_failed_source_and_inexact_existing_node_cannot_bind(self):
        with self.assertRaises(ValueError):
            attachment(self.store, self.project, self.failed_id)
        for change in (
            {"original": "Different original meaning"},
            {"timeout_seconds": 29},
            {"goal": examples()[1]["goal"], "proof": examples()[1]["proof"]},
        ):
            value = node()
            value["request"].update(change)
            edit(self.store, self.project, [value])
            with self.assertRaisesRegex(ValueError, "differs"):
                attachment(self.store, self.project, self.run_id)

    def test_browser_json_number_roundtrip_normalizes_before_binding_existing_node(self):
        value = node()
        value["request"]["timeout_seconds"] = 30
        edit(self.store, self.project, [value])
        with self.store.transaction(False) as db:
            saved = self.store.read(db, self.project, "main")
        self.assertIs(type(saved["nodes"][0]["request"]["timeout_seconds"]), float)
        row = self.bind()
        self.assertEqual(row["nodes"][0]["math_verdict"], "PROVED")
        self.assertEqual(
            self.store.report(self.project, "main")["bindings"]["lemma"]["state"], "ACTIVE"
        )

    def test_prerequisites_do_not_prove_main_and_rebind_does_not_revive_downstream(self):
        edit(
            self.store,
            self.project,
            [node("l1"), node("l2", ["l1"]), node("main", ["l2"], kind="problem")],
        )
        with self.assertRaisesRegex(ValueError, "Unproved"):
            self.bind("l2", ["l1"])
        self.bind("l1")
        row = self.bind("l2", ["l1"])
        self.assertEqual([n["math_verdict"] for n in row["nodes"]], ["PROVED", "PROVED", "UNKNOWN"])
        self.bind("main", ["l2"], "problem")
        row = self.store.withdraw(
            self.project,
            {
                **context(self.store, self.project),
                "request_id": uuid.uuid4().hex,
                "node_id": "l1",
                "reviewer": "Reviewer",
                "reason": "Correct research dependency",
                "confirmed": True,
            },
        )
        self.assertTrue(all(n["math_verdict"] == "UNKNOWN" for n in row["nodes"]))
        row = self.bind("l1")
        self.assertEqual(
            [n["math_verdict"] for n in row["nodes"]], ["PROVED", "UNKNOWN", "UNKNOWN"]
        )
        self.assertEqual(self.mathlib.report(self.run_id)["math_verdict"], "PROVED")

    def test_edited_and_removed_nodes_invalidate_dependents_permanently(self):
        self.bind("l1")
        self.bind("l2", ["l1"])
        row = self.store.report(self.project, "main")
        nodes = [
            {k: n[k] for k in ("id", "kind", "request", "depends_on", "notes")}
            for n in row["nodes"]
        ]
        changed = copy.deepcopy(nodes)
        changed[0]["notes"] = "Updated mathematical context"
        row = edit(self.store, self.project, changed)
        self.assertTrue(all(n["math_verdict"] == "UNKNOWN" for n in row["nodes"]))
        row = edit(self.store, self.project, nodes)
        self.assertTrue(all(n["math_verdict"] == "UNKNOWN" for n in row["nodes"]))
        with self.assertRaises(ValueError):
            edit(self.store, self.project, nodes[1:])
        row = edit(self.store, self.project, [])
        self.assertTrue(all(b["state"] == "INVALIDATED" for b in row["bindings"].values()))

    def test_parent_graph_change_and_fork_are_isolated(self):
        self.bind()
        parent = self.store.research.report(self.project)
        forked = self.store.research.fork(
            self.project,
            {
                "request_id": uuid.uuid4().hex,
                "expected_version": parent["version"],
                "branch_id": "main",
                "label": "Alternative route",
            },
        )
        key = next(k for k in forked["branches"] if k != "main")
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "PROVED"
        )
        self.assertEqual(self.store.report(self.project, key)["nodes"], [])
        self.store.research.edit(
            self.project,
            {
                "request_id": uuid.uuid4().hex,
                "expected_version": forked["version"],
                "branch_id": "main",
                "nodes": [],
            },
        )
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "UNKNOWN"
        )

    def test_real_source_sqlite_lock_never_permanently_invalidates_binding(self):
        self.bind()
        with sqlite3.connect(self.mathlib.path, isolation_level=None) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(sqlite3.OperationalError):
                self.store.report(self.project, "main")
            lock.rollback()
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "PROVED"
        )

    def test_adapter_change_persistently_invalidates(self):
        self.bind()
        with patch.object(self.store, "adapter", return_value={"changed": True}):
            self.assertEqual(
                self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "UNKNOWN"
            )
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "UNKNOWN"
        )

    def test_fresh_check_after_handoff_requires_explicit_new_binding(self):
        self.bind()
        request_value = self.store.report(self.project, "main")["nodes"][0]["request"]
        fresh = submit(self.mathlib, request_value)
        self.mathlib.work_once(fresh["run_id"])
        self.assertEqual(self.mathlib.report(fresh["run_id"])["math_verdict"], "PROVED")
        self.assertEqual(
            self.store.report(self.project, "main")["bindings"]["lemma"]["source"]["run_id"],
            self.run_id,
        )
        self.bind(run_id=fresh["run_id"])
        self.assertEqual(
            self.store.report(self.project, "main")["bindings"]["lemma"]["source"]["run_id"],
            fresh["run_id"],
        )

    def test_own_real_artifact_damage_invalidates_without_file_restore_revival(self):
        lab = isolated_store()
        run = submit(lab)
        lab.work_once(run["run_id"])
        self.store.mathlib = lab
        self.bind(run_id=run["run_id"])
        path = lab.directory(run["run_id"]) / "candidate/Candidate.lean"
        original = path.read_bytes()
        path.write_bytes(original + b"\n-- changed\n")
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "UNKNOWN"
        )
        path.write_bytes(original)
        self.assertEqual(
            self.store.report(self.project, "main")["nodes"][0]["math_verdict"], "UNKNOWN"
        )

    def test_z_real_source_revocation_public_api_cascades_and_preserves_artifacts(self):
        self.bind("l1")
        self.bind("l2", ["l1"])
        root = self.store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.research_mathlib = self.store
            server.mathlib = self.mathlib
            status, row, _ = request(
                server,
                "POST",
                "/v1/mathlib/runs/" + self.run_id + "/revoke",
                {
                    "reviewer": "Reviewer",
                    "reason": "Test revocation propagation",
                    "confirmed": True,
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(row["state"], "COMPLETED")
        self.assertTrue(row["files"])
        with self.store.transaction(False) as db:
            current = self.store.read(db, self.project, "main")
        self.assertTrue(all(b["state"] == "INVALIDATED" for b in current["bindings"].values()))


if __name__ == "__main__":
    unittest.main()
