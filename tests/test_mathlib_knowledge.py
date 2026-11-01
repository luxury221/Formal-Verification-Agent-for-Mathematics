"""Private archives use real Lean evidence; storage faults never manufacture proofs."""

import copy
import json
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

from mra.mathlib_knowledge import MathlibKnowledgeStore
from mra.mathlib_terms import examples
from test_http_api import copy_web_inputs, request, running_server
from test_mathlib_lab import isolated_store, submit
from test_research_mathlib import attachment, context, make_store

ROOT = Path(__file__).resolve().parents[1]


def store_for(lab=None):
    store = MathlibKnowledgeStore(ROOT / "artifacts/mathlib-knowledge-tests" / uuid.uuid4().hex)
    store.root = ROOT
    if lab:
        store.sources.mathlib = lab
        store.sources.root = ROOT
    return store


def admission(store, run_id):
    plan = store.prepare({"run_id": run_id})
    return {
        "request_id": uuid.uuid4().hex,
        "run_id": run_id,
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "Private archive reviewer",
        "confirmed": True,
        "rights_confirmed": True,
    }


def retirement(**changes):
    return {
        "request_id": uuid.uuid4().hex,
        "reviewer": "Reviewer",
        "reason": "Archive lifecycle test",
        "confirmed": True,
        **changes,
    }


class ProtocolTests(unittest.TestCase):
    def test_empty_store_and_strict_search(self):
        store = store_for()
        self.assertEqual(store.search()["items"], [])
        for args in (("a" * 201,), (True,), ("", "true")):
            with self.assertRaises(ValueError):
                store.search(*args)
        with self.assertRaises(ValueError):
            store.prepare({"run_id": "../x"})
        with self.assertRaises(ValueError):
            store.prepare({"run_id": "mathlib-" + "0" * 32, "extra": True})

    def test_paths_and_symlink_storage_rejected(self):
        store = store_for()
        for name in ("../a", "/absolute", "a//b", "a/./b", "a\\b", "", ".", "x" * 241):
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.safe_name(name)
        for name in ("../../outside", "/outside"):
            with self.assertRaises(ValueError):
                store.directory(name)
        original = store.proofs
        redirected = store.base / "redirected"
        redirected.symlink_to(original, target_is_directory=True)
        store.proofs = redirected
        with self.assertRaises(ValueError):
            store.search()

    def test_queued_cancelled_unknown_and_unconfirmed_sources_rejected(self):
        lab = isolated_store()
        store = store_for(lab)
        run = submit(lab)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "COMPLETED/PROVED/E4"):
                store.prepare({"run_id": run["run_id"]})
            lab.cancel(run["run_id"])
        with self.assertRaises(ValueError):
            store.prepare({"run_id": "mathlib-" + "0" * 32})
        self.assertEqual(store.search()["count"], 0)

    def test_http_strict_queries_origin_body_and_new_assets(self):
        store = store_for()
        root = store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib_knowledge = store
            for path in (
                "/library",
                "/mathlib-knowledge.js",
                "/mathlib-knowledge.css",
                "/v1/mathlib-knowledge",
                "/v1/mathlib-knowledge/search?q=&include_inactive=true",
            ):
                self.assertEqual(request(server, "GET", path)[0], 200, path)
            for path in ("?q=x&q=y", "?include_inactive=1", "?unknown=true"):
                self.assertEqual(
                    request(server, "GET", "/v1/mathlib-knowledge/search" + path)[0], 400
                )
            for headers in ({"Origin": "https://elsewhere.test"}, {"X-MRA-CSRF": "bad"}):
                self.assertEqual(
                    request(server, "POST", "/v1/mathlib-knowledge/prepare", {}, headers=headers)[
                        0
                    ],
                    403,
                )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/mathlib-knowledge/prepare",
                    raw=b'{"run_id":"x","run_id":"y"}',
                )[0],
                400,
            )
            self.assertEqual(request(server, "POST", "/v1/mathlib-knowledge", {})[0], 400)
            self.assertEqual(request(server, "GET", "/v1/mathlib-knowledge/unknown")[0], 400)

    def test_cli_empty_and_invalid_source(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra",
                "mathlib-knowledge-search",
                "--query",
                "nonexistent-test-" + uuid.uuid4().hex,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["count"], 0)
        path = ROOT / ".tmp" / ("invalid-admission-" + uuid.uuid4().hex + ".json")
        path.write_text('{"run_id":"../invalid"}')
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mra", "mathlib-knowledge-prepare", "--input", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("Mathlib knowledge error:", result.stderr)
        finally:
            path.unlink()


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1", "Set MRA_REAL_LEAN=1 for actual Lean integration"
)
class RealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_lab = isolated_store()
        good = submit(cls.original_lab)
        cls.original_lab.work_once(good["run_id"])
        cls.run_id = good["run_id"]
        row = cls.original_lab.report(cls.run_id)
        if (row["state"], row["math_verdict"], row["evidence_level"]) != (
            "COMPLETED",
            "PROVED",
            "E4",
        ):
            raise AssertionError(row)
        bad = submit(cls.original_lab, examples()[3])
        cls.original_lab.work_once(bad["run_id"])
        cls.failed_id = bad["run_id"]

    def setUp(self):
        # Copy actual checked bytes and the unchanged ledger into an isolated test store.
        self.lab = isolated_store()
        with (
            sqlite3.connect(self.original_lab.path) as source,
            sqlite3.connect(self.lab.path) as target,
        ):
            source.backup(target)
        for run_id in (self.run_id, self.failed_id):
            shutil.copytree(self.original_lab.directory(run_id), self.lab.directory(run_id))
        self.store = store_for(self.lab)

    def admit(self):
        return self.store.admit(admission(self.store, self.run_id))

    def test_real_admission_preserves_full_original_and_independent_audit(self):
        data = admission(self.store, self.run_id)
        for change in (
            {"confirmed": False},
            {"rights_confirmed": False},
            {"reviewer": ""},
            {"plan_fingerprint": "stale"},
            {"extra": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.admit({**data, **change})
        row = self.store.admit(data)
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]), ("ACTIVE", "PROVED", "E4")
        )
        content = row["content"]
        self.assertEqual(
            content["source"]["request"], self.lab.report(self.run_id)["plan"]["request"]
        )
        self.assertNotEqual(
            content["source_record"]["candidate"]["pid"], content["source_record"]["replay"]["pid"]
        )
        self.assertFalse(row["fresh_attestation"])
        self.assertEqual(row["new_lean_calls"], 0)
        self.assertTrue(self.store.archive_intact(content))
        self.assertEqual(self.store.admit(data), row)
        with self.assertRaises(ValueError):
            self.store.admit({**data, "reviewer": "Someone else"})

    def test_search_formula_original_reference_and_json_roundtrip(self):
        row = self.admit()
        c = row["content"]
        for query in (
            c["source"]["formal_statement"][:40],
            c["source"]["request"]["original"][:20],
            "Nat.dvd_add",
            self.run_id,
        ):
            self.assertEqual(self.store.search(query)["items"][0]["item_id"], row["item_id"])
        self.assertEqual(
            self.store.prepare({"run_id": self.run_id})["same_statement_ids"], [row["item_id"]]
        )
        value = self.store.recheck_request(row["item_id"])
        value = json.loads(json.dumps(value).replace("30.0", "30"))
        self.assertEqual(self.lab.preview(value["request"])["request"], c["source"]["request"])
        self.assertFalse(value["proof_imported"])

    def test_failed_real_source_cannot_enter_archive(self):
        self.assertEqual(self.lab.report(self.failed_id)["math_verdict"], "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "COMPLETED/PROVED/E4"):
            self.store.prepare({"run_id": self.failed_id})
        self.assertEqual(self.store.search()["count"], 0)

    def test_archive_damage_and_restore_never_revive(self):
        row = self.admit()
        path = self.store.directory(row["item_id"]) / "source-record.json"
        raw = path.read_bytes()
        path.write_bytes(raw + b"changed")
        self.assertEqual(self.store.report(row["item_id"])["state"], "INVALIDATED")
        path.write_bytes(raw)
        self.assertEqual(self.store.report(row["item_id"])["math_verdict"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.admit(admission(self.store, self.run_id))
        self.assertEqual(self.lab.report(self.run_id)["math_verdict"], "PROVED")

    def test_extra_archive_file_or_symlink_is_rejected(self):
        row = self.admit()
        directory = self.store.directory(row["item_id"])
        extra = directory / "extra"
        extra.write_text("unrecorded")
        self.assertFalse(self.store.archive_intact(row["content"]))
        extra.unlink()
        extra.symlink_to(directory / "source-record.json")
        self.assertFalse(self.store.archive_intact(row["content"]))
        self.assertEqual(self.store.report(row["item_id"])["state"], "INVALIDATED")

    def test_source_artifact_damage_persistently_invalidates(self):
        row = self.admit()
        path = self.lab.directory(self.run_id) / "candidate/Candidate.lean"
        raw = path.read_bytes()
        path.write_bytes(raw + b"\n-- changed\n")
        self.assertEqual(self.store.report(row["item_id"])["state"], "INVALIDATED")
        path.write_bytes(raw)
        self.assertEqual(self.store.report(row["item_id"])["math_verdict"], "UNKNOWN")

    def test_temporary_source_or_archive_sqlite_lock_never_invalidates(self):
        row = self.admit()
        for path in (self.lab.path, self.store.path):
            with sqlite3.connect(path, isolation_level=None) as lock:
                lock.execute("BEGIN EXCLUSIVE")
                with self.assertRaises(sqlite3.OperationalError):
                    self.store.report(row["item_id"])
                lock.rollback()
            self.assertEqual(self.store.report(row["item_id"])["state"], "ACTIVE")

    def test_adapter_change_invalidates_permanently(self):
        row = self.admit()
        with patch.object(self.store, "adapter", return_value={"changed": True}):
            self.assertEqual(self.store.report(row["item_id"])["state"], "INVALIDATED")
        self.assertEqual(self.store.report(row["item_id"])["math_verdict"], "UNKNOWN")

    def test_withdrawal_and_removal_idempotent_keep_original_proof(self):
        row = self.admit()
        item_id = row["item_id"]
        data = retirement()
        withdrawn = self.store.retire(item_id, data)
        self.assertEqual(withdrawn["state"], "WITHDRAWN")
        self.assertEqual(self.store.retire(item_id, data), withdrawn)
        self.assertEqual(self.store.search()["count"], 0)
        self.assertEqual(self.store.search(include_inactive=True)["count"], 1)
        with self.assertRaises(ValueError):
            self.store.recheck_request(item_id)
        with self.assertRaises(ValueError):
            self.store.admit(admission(self.store, self.run_id))
        data = retirement()
        removed = self.store.retire(item_id, data, remove=True)
        self.assertIsNone(removed["content"])
        self.assertIsNone(removed["admission"])
        self.assertEqual(self.store.retire(item_id, data, remove=True), removed)
        self.assertFalse(self.store.directory(item_id).exists())
        self.assertEqual(self.lab.report(self.run_id)["math_verdict"], "PROVED")

    def test_incomplete_copy_and_source_changed_during_copy_cannot_admit(self):
        data = admission(self.store, self.run_id)
        real_write = Path.write_bytes

        def fail(path, raw):
            if ".admission-" in str(path):
                raise OSError("injected disk failure")
            return real_write(path, raw)

        with patch.object(Path, "write_bytes", fail), self.assertRaises(OSError):
            self.store.admit(data)
        self.assertEqual(self.store.search()["count"], 0)
        self.assertFalse(self.store.directory(self.store.item_id(self.run_id)).exists())
        real_archive = self.store.archive

        def revoke(content):
            real_archive(content)
            self.lab.revoke(
                self.run_id, {"reviewer": "Reviewer", "reason": "during copy", "confirmed": True}
            )

        with patch.object(self.store, "archive", revoke), self.assertRaises(ValueError):
            self.store.admit(data)
        self.assertEqual(self.store.search()["count"], 0)

    def test_orphan_copy_can_retry_and_remove_cleanup_can_retry(self):
        data = admission(self.store, self.run_id)
        self.store.archive(self.store.prepare({"run_id": self.run_id})["content"])
        row = self.store.admit(data)
        removal = retirement()
        with (
            patch("mra.mathlib_knowledge.shutil.rmtree", side_effect=OSError("disk busy")),
            self.assertRaises(OSError),
        ):
            self.store.retire(row["item_id"], removal, remove=True)
        self.assertEqual(self.store.report(row["item_id"])["state"], "DELETED")
        self.store.retire(row["item_id"], removal, remove=True)
        self.assertFalse(self.store.directory(row["item_id"]).exists())

    def test_archive_lock_and_concurrent_admission_leave_single_item(self):
        data = admission(self.store, self.run_id)
        with self.store.archive_lock(), self.assertRaises(RuntimeError):
            self.store.admit(data)
        barrier = threading.Barrier(2)
        outcomes = []

        def run():
            barrier.wait()
            try:
                self.store.admit(data)
                outcomes.append("ok")
            except RuntimeError:
                outcomes.append("busy")

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        self.assertIn("ok", outcomes)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(self.store.search()["count"], 1)

    def test_corrupt_ledger_is_fail_closed(self):
        row = self.admit()
        with self.store.transaction() as db:
            db.execute("UPDATE items SET hash='changed'")
        with self.assertRaises(ValueError):
            self.store.report(row["item_id"])

    def test_proof_budgets_and_path_redirects_rejected_before_copy(self):
        original = self.store.candidate(self.run_id)
        with patch("mra.mathlib_knowledge.MAX_BYTES", 1), self.assertRaises(ValueError):
            self.store.candidate(self.run_id)
        source_path = self.lab.directory(self.run_id) / "candidate/Candidate.lean"
        raw = source_path.read_bytes()
        source_path.unlink()
        target = source_path.with_name("redirect-target")
        target.write_bytes(raw)
        source_path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.candidate(self.run_id)
        source_path.unlink()
        source_path.write_bytes(raw)
        target.unlink()
        content = copy.deepcopy(original)
        content["file_hashes"]["../escape"] = "0" * 64
        with self.assertRaises(ValueError):
            self.store.archive(content)
        self.assertFalse(self.store.directory(content["item_id"]).exists())

    def test_research_binding_withdrawal_does_not_revoke_independent_archive(self):
        row = self.admit()
        bridge, project = make_store(self.lab)
        bridge.attach(project, attachment(bridge, project, self.run_id))
        bridge.withdraw(
            project,
            {
                **context(bridge, project),
                "request_id": uuid.uuid4().hex,
                "node_id": "lemma",
                "reviewer": "Reviewer",
                "reason": "Research direction changed",
                "confirmed": True,
            },
        )
        self.assertEqual(self.store.report(row["item_id"])["state"], "ACTIVE")

    def test_http_real_admission_handoff_export_and_public_source_revocation(self):
        root = self.store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib_knowledge = self.store
            server.mathlib = self.lab
            status, plan, _ = request(
                server, "POST", "/v1/mathlib-knowledge/prepare", {"run_id": self.run_id}
            )
            self.assertEqual(status, 200)
            data = {
                **admission(self.store, self.run_id),
                "plan_fingerprint": plan["plan_fingerprint"],
            }
            status, row, _ = request(server, "POST", "/v1/mathlib-knowledge", data)
            self.assertEqual(status, 201, row)
            route = "/v1/mathlib-knowledge/" + row["item_id"]
            for path in (route, route + "/export", route + "/request"):
                self.assertEqual(request(server, "GET", path)[0], 200)
            status, result, _ = request(
                server,
                "POST",
                "/v1/mathlib/runs/" + self.run_id + "/revoke",
                {
                    "reviewer": "Reviewer",
                    "reason": "Withdraw semantic alignment",
                    "confirmed": True,
                },
            )
            self.assertEqual(status, 200, result)
            with self.store.transaction(False) as db:
                self.assertEqual(self.store.read(db, row["item_id"])["state"], "INVALIDATED")
            self.assertEqual(request(server, "GET", route + "/request")[0], 400)
            self.assertEqual(
                request(server, "POST", route + "/remove", retirement())[1]["state"], "DELETED"
            )

    def test_handoff_requires_new_real_run_and_keeps_old_source(self):
        row = self.admit()
        value = self.store.recheck_request(row["item_id"])
        data = json.loads(json.dumps(value["request"]).replace("30.0", "30"))
        fresh = submit(self.lab, data)
        self.assertNotEqual(fresh["run_id"], self.run_id)
        self.assertEqual(fresh["math_verdict"], "UNKNOWN")
        self.lab.work_once(fresh["run_id"])
        result = self.lab.report(fresh["run_id"])
        self.assertEqual(
            (result["state"], result["math_verdict"], result["evidence_level"]),
            ("COMPLETED", "PROVED", "E4"),
        )
        second = self.store.admit(admission(self.store, fresh["run_id"]))
        self.assertNotEqual(second["item_id"], row["item_id"])
        self.assertEqual(
            second["content"]["proposition_fingerprint"], row["content"]["proposition_fingerprint"]
        )
        self.assertEqual(self.store.report(row["item_id"])["source_ref"]["run_id"], self.run_id)


if __name__ == "__main__":
    unittest.main()
