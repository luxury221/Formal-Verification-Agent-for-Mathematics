"""Capture-free specialization and real independent Mathlib proof associations."""

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

from mra.mathlib_reuse import MathlibReuseStore, specialize
from mra.mathlib_terms import compile_request, examples
from test_http_api import copy_web_inputs, request, running_server
from test_mathlib_lab import isolated_store, submit
from test_mathlib_knowledge import admission, retirement, store_for

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "ml-knowledge-" + "0" * 32


def specialization(item_id=SOURCE_ID, **changes):
    return {
        "item_id": item_id,
        "variables": ["n"],
        "substitution": {
            "a": {"nat": 2},
            "b": {"var": "n"},
            "c": {"mul": [{"nat": 2}, {"var": "n"}]},
        },
        "title": "An explicit specialization",
        "original": "For n in Nat, retain both divisibility assumptions and substitute a=2, b=n, c=2*n.",
        "timeout_seconds": 30,
        **changes,
    }


def create_data(store, data):
    preview = store.preview(data)
    return {
        **data,
        "request_id": uuid.uuid4().hex,
        "plan_fingerprint": preview["plan_fingerprint"],
        "reviewer": "Specialization reviewer",
        "confirmed": True,
    }


def reuse_store(knowledge=None):
    store = MathlibReuseStore(ROOT / "artifacts/mathlib-reuse-tests" / uuid.uuid4().hex)
    store.root = ROOT
    if knowledge:
        store.knowledge = knowledge
    return store


class SubstitutionTests(unittest.TestCase):
    def test_simultaneous_substitution_does_not_rewrite_inserted_names(self):
        raw = examples()[0]
        mapping = {"a": {"var": "b"}, "b": {"var": "a"}, "c": {"var": "c"}}
        result, _ = specialize(raw, specialization(variables=["a", "b", "c"], substitution=mapping))
        self.assertEqual(result["assumptions"][0]["formula"], {"dvd": [{"var": "b"}, {"var": "a"}]})
        self.assertEqual(result["proof"]["terms"], [{"var": "b"}, {"var": "a"}, {"var": "c"}])
        self.assertEqual(raw, examples()[0])

    def test_all_premises_and_nested_library_proof_inputs_are_preserved(self):
        source = examples()[0]
        target, mapping = specialize(source, specialization())
        self.assertEqual(len(target["assumptions"]), 2)
        self.assertEqual(
            [h["name"] for h in target["assumptions"]], [h["name"] for h in source["assumptions"]]
        )
        self.assertEqual(target["proof"]["proofs"][1]["terms"], list(mapping.values()))
        self.assertEqual(compile_request(target)["formal_statement"].count(" → "), 2)

    def test_closed_specialization_keeps_true_numeric_premises_explicit(self):
        target, _ = specialize(
            examples()[0],
            specialization(
                variables=[],
                substitution={name: {"nat": n} for name, n in zip(("a", "b", "c"), (2, 6, 12))},
            ),
        )
        self.assertEqual(target["variables"], [])
        self.assertEqual(len(target["assumptions"]), 2)
        self.assertNotIn("∀", compile_request(target)["formal_statement"])

    def test_full_mapping_required_and_extra_target_fields_rejected(self):
        for mapping in (
            {},
            {"a": {"nat": 1}},
            {**specialization()["substitution"], "extra": {"nat": 0}},
        ):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                specialize(examples()[0], specialization(substitution=mapping))
        with self.assertRaises(ValueError):
            specialize(examples()[0], {**specialization(), "goal": {"nat": 0}})

    def test_nat_types_unbound_variables_source_and_shadowing_rejected(self):
        for expression in (
            {"nat": True},
            {"nat": -1},
            {"nat": 1.5},
            {"nat": 1000001},
            {"var": "missing"},
            {"source": "by sorry"},
            {"div": [{"nat": 2}, {"nat": 1}]},
            {"var": []},
        ):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                specialize(
                    examples()[0],
                    specialization(
                        substitution={**specialization()["substitution"], "a": expression}
                    ),
                )
        for variables in (
            ["n", "n"],
            ["n; run_cmd"],
            [examples()[0]["assumptions"][0]["name"]],
            list("abcdefg"),
        ):
            with self.subTest(variables=variables), self.assertRaises(ValueError):
                specialize(examples()[0], specialization(variables=variables))

    def test_depth_node_and_final_target_expansion_budgets(self):
        deep = {"var": "n"}
        for _ in range(10):
            deep = {"add": [deep, {"nat": 1}]}
        with self.assertRaises(ValueError):
            specialize(
                examples()[0],
                specialization(substitution={"a": deep, "b": {"nat": 0}, "c": {"nat": 0}}),
            )
        wide = {"var": "n"}
        for _ in range(6):
            wide = {"add": [wide, wide]}
        with self.assertRaises(ValueError):
            specialize(
                examples()[0], specialization(substitution={key: wide for key in ("a", "b", "c")})
            )
        expression = {"var": "n"}
        for _ in range(4):
            expression = {"add": [expression, expression]}
        with self.assertRaisesRegex(ValueError, "node budget"):
            specialize(
                examples()[0],
                specialization(substitution={"a": expression, "b": expression, "c": {"nat": 1}}),
            )

    def test_invalid_budgets_and_unknown_archives_keep_empty_store(self):
        for timeout in (True, 0, 31, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                specialize(examples()[0], specialization(timeout_seconds=timeout))
        store = reuse_store()
        self.assertEqual(store.search()["items"], [])
        for item_id in (SOURCE_ID, "../outside"):
            with self.assertRaises(ValueError):
                store.preview(specialization(item_id))
        for args in (("a" * 201,), ("", "false")):
            with self.assertRaises(ValueError):
                store.search(*args)

    def test_http_assets_strict_query_and_original_guards(self):
        store = reuse_store()
        root = store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib_reuse = store
            for path in (
                "/mathlib-reuse.js",
                "/mathlib-reuse.css",
                "/v1/mathlib-reuse",
                "/v1/mathlib-reuse/search?q=&include_inactive=true",
            ):
                self.assertEqual(request(server, "GET", path)[0], 200)
            for path in ("?q=x&q=y", "?include_inactive=1", "?extra=x"):
                self.assertEqual(request(server, "GET", "/v1/mathlib-reuse/search" + path)[0], 400)
            for headers in ({"Origin": "https://elsewhere.test"}, {"X-MRA-CSRF": "bad"}):
                self.assertEqual(
                    request(server, "POST", "/v1/mathlib-reuse/preview", {}, headers=headers)[0],
                    403,
                )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/mathlib-reuse/preview",
                    raw=b'{"item_id":"x","item_id":"y"}',
                )[0],
                400,
            )
            self.assertEqual(request(server, "POST", "/v1/mathlib-reuse", {})[0], 400)
            self.assertEqual(request(server, "GET", "/v1/mathlib-reuse/../outside")[0], 404)

    def test_cli_empty_search_and_bad_identifier(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra",
                "mathlib-reuse-search",
                "--query",
                "no-match-" + uuid.uuid4().hex,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["items"], [])
        result = subprocess.run(
            [sys.executable, "-m", "mra", "mathlib-reuse-report", "../outside"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Mathlib reuse error:", result.stderr)


@unittest.skipUnless(
    os.environ.get("MRA_REAL_LEAN") == "1",
    "Set MRA_REAL_LEAN=1 for real Lean specialization checks",
)
class RealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = isolated_store()
        good = submit(cls.fixture)
        cls.fixture.work_once(good["run_id"])
        cls.source_run = good["run_id"]
        if cls.fixture.report(cls.source_run)["math_verdict"] != "PROVED":
            raise AssertionError(cls.fixture.report(cls.source_run))
        cls.archive = store_for(cls.fixture)
        cls.archive_id = cls.archive.admit(admission(cls.archive, cls.source_run))["item_id"]
        target, _ = specialize(
            cls.fixture.report(cls.source_run)["plan"]["request"], specialization(cls.archive_id)
        )
        fresh = submit(cls.fixture, target)
        cls.fixture.work_once(fresh["run_id"])
        cls.target_run = fresh["run_id"]
        if cls.fixture.report(cls.target_run)["math_verdict"] != "PROVED":
            raise AssertionError(cls.fixture.report(cls.target_run))

    def setUp(self):
        self.lab = isolated_store()
        with sqlite3.connect(self.fixture.path) as source, sqlite3.connect(self.lab.path) as target:
            source.backup(target)
        for key in (self.source_run, self.target_run):
            shutil.copytree(self.fixture.directory(key), self.lab.directory(key))
        self.knowledge = store_for(self.lab)
        with (
            sqlite3.connect(self.archive.path) as source,
            sqlite3.connect(self.knowledge.path) as target,
        ):
            source.backup(target)
        shutil.copytree(self.archive.proofs, self.knowledge.proofs, dirs_exist_ok=True)
        self.store = reuse_store(self.knowledge)
        self.data = specialization(self.archive_id)

    def create(self, **changes):
        return self.store.create(create_data(self.store, {**self.data, **changes}))

    def attach_data(self, item_id, run_id=None):
        run_id = run_id or self.target_run
        plan = self.store.preview_attachment(item_id, {"run_id": run_id})
        return {
            "request_id": uuid.uuid4().hex,
            "run_id": run_id,
            "attachment_fingerprint": plan["attachment_fingerprint"],
            "reviewer": "New result reviewer",
            "confirmed": True,
        }

    def bind(self):
        row = self.create()
        return self.store.attach(row["reuse_id"], self.attach_data(row["reuse_id"]))

    def test_confirmation_creates_unknown_and_json_budget_is_normalized(self):
        data = create_data(self.store, self.data)
        for change in ({"confirmed": False}, {"reviewer": ""}, {"plan_fingerprint": "stale"}):
            with self.assertRaises(ValueError):
                self.store.create({**data, **change})
        row = self.store.create(data)
        self.assertEqual(
            (row["state"], row["math_verdict"], row["evidence_level"]),
            ("AWAITING_CHECK", "UNKNOWN", "E0"),
        )
        self.assertEqual(self.store.create(data), row)
        with self.assertRaises(ValueError):
            self.store.create({**data, "original": "Changed"})
        self.assertIs(type(row["content"]["input"]["timeout_seconds"]), float)
        self.assertEqual(self.store.report(row["reuse_id"])["state"], "AWAITING_CHECK")
        self.assertFalse(self.store.request(row["reuse_id"])["proof_imported"])

    def test_real_independent_e4_attaches_with_complete_premises(self):
        row = self.create()
        data = self.attach_data(row["reuse_id"])
        for change in ({"confirmed": False}, {"reviewer": ""}, {"attachment_fingerprint": "stale"}):
            with self.assertRaises(ValueError):
                self.store.attach(row["reuse_id"], {**data, **change})
        bound = self.store.attach(row["reuse_id"], data)
        self.assertEqual(
            (bound["state"], bound["math_verdict"], bound["evidence_level"]),
            ("ACTIVE", "PROVED", "E4"),
        )
        self.assertEqual(len(bound["binding"]["source"]["request"]["assumptions"]), 2)
        self.assertFalse(bound["fresh_attestation"])
        self.assertEqual(self.store.attach(row["reuse_id"], data), bound)
        checked = self.lab.report(self.target_run)
        self.assertNotEqual(
            checked["result"]["candidate"]["pid"], checked["result"]["replay"]["pid"]
        )

    def test_identity_transformation_cannot_use_original_run_as_new_proof(self):
        source = self.lab.report(self.source_run)["plan"]["request"]
        row = self.create(
            variables=source["variables"],
            substitution={v: {"var": v} for v in source["variables"]},
            title=source["title"],
            original=source["original"],
        )
        with self.assertRaisesRegex(ValueError, "original source run"):
            self.store.preview_attachment(row["reuse_id"], {"run_id": self.source_run})
        self.assertEqual(self.store.report(row["reuse_id"])["math_verdict"], "UNKNOWN")

    def test_complete_new_request_mismatch_never_attaches(self):
        for change in (
            {"original": "Different intended meaning"},
            {"timeout_seconds": 29},
            {"variables": [], "substitution": {k: {"nat": 2} for k in ("a", "b", "c")}},
        ):
            row = self.create(**change)
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "complete specialized request"),
            ):
                self.store.preview_attachment(row["reuse_id"], {"run_id": self.target_run})

    def test_queued_and_cancelled_targets_are_not_proofs(self):
        row = self.create()
        queued = submit(self.lab, row["content"]["target"]["request"])
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "COMPLETED/PROVED/E4"):
                self.store.preview_attachment(row["reuse_id"], {"run_id": queued["run_id"]})
            self.lab.cancel(queued["run_id"])

    def test_source_withdrawal_invalidates_draft_and_bound_record(self):
        first, second = self.create(), self.bind()
        self.knowledge.retire(self.archive_id, retirement())
        self.store.reconcile(item_id=self.archive_id)
        for row in (first, second):
            self.assertEqual(self.store.report(row["reuse_id"])["state"], "INVALIDATED")
            with self.assertRaises(ValueError):
                self.store.request(row["reuse_id"])
        self.assertEqual(self.lab.report(self.target_run)["math_verdict"], "PROVED")

    def test_archived_bytes_restore_never_revives_reference_chain(self):
        row = self.bind()
        path = self.knowledge.directory(self.archive_id) / "source-record.json"
        raw = path.read_bytes()
        path.write_bytes(raw + b" changed")
        self.assertEqual(self.store.report(row["reuse_id"])["state"], "INVALIDATED")
        path.write_bytes(raw)
        self.assertEqual(self.store.report(row["reuse_id"])["math_verdict"], "UNKNOWN")

    def test_new_result_damage_and_restoration_never_revive(self):
        row = self.bind()
        path = self.lab.directory(self.target_run) / "candidate/Candidate.lean"
        raw = path.read_bytes()
        path.write_bytes(raw + b" changed")
        self.assertEqual(self.store.report(row["reuse_id"])["state"], "INVALIDATED")
        path.write_bytes(raw)
        self.assertEqual(self.store.report(row["reuse_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(self.knowledge.report(self.archive_id)["state"], "ACTIVE")

    def test_sqlite_locks_do_not_permanently_pollute_result(self):
        row = self.bind()
        for path in (self.store.path, self.knowledge.path, self.lab.path):
            with sqlite3.connect(path, isolation_level=None) as lock:
                lock.execute("BEGIN EXCLUSIVE")
                with self.assertRaises(sqlite3.OperationalError):
                    self.store.report(row["reuse_id"])
                lock.rollback()
            self.assertEqual(self.store.report(row["reuse_id"])["state"], "ACTIVE")

    def test_adapter_change_persistently_invalidates(self):
        row = self.bind()
        with patch.object(self.store, "adapter", return_value={"changed": True}):
            self.assertEqual(self.store.report(row["reuse_id"])["state"], "INVALIDATED")
        self.assertEqual(self.store.report(row["reuse_id"])["math_verdict"], "UNKNOWN")

    def test_concurrent_associations_do_not_overwrite_revision(self):
        row = self.create()
        first = self.attach_data(row["reuse_id"])
        second = {**first, "request_id": uuid.uuid4().hex}
        original = self.store.preview_attachment
        barrier = threading.Barrier(2)
        outcomes = []

        def synchronized(*args):
            plan = original(*args)
            barrier.wait(timeout=20)
            return plan

        def attach(data):
            try:
                self.store.attach(row["reuse_id"], data)
                outcomes.append("ok")
            except ValueError:
                outcomes.append("conflict")

        with patch.object(self.store, "preview_attachment", synchronized):
            threads = [threading.Thread(target=attach, args=(data,)) for data in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
                self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["ok", "conflict"])
        self.assertEqual(self.store.report(row["reuse_id"])["version"], 2)

    def test_withdraw_and_remove_are_idempotent_and_preserve_independent_runs(self):
        row = self.bind()
        data = retirement()
        withdrawn = self.store.retire(row["reuse_id"], data)
        self.assertEqual(self.store.retire(row["reuse_id"], data), withdrawn)
        self.assertEqual(self.store.search()["count"], 0)
        self.assertEqual(self.store.search(include_inactive=True)["count"], 1)
        data = retirement()
        removed = self.store.retire(row["reuse_id"], data, remove=True)
        self.assertIsNone(removed["content"])
        self.assertIsNone(removed["binding"])
        self.assertIsNone(removed["confirmation"])
        self.assertEqual(self.store.retire(row["reuse_id"], data, remove=True), removed)
        self.assertEqual(self.knowledge.report(self.archive_id)["state"], "ACTIVE")
        self.assertEqual(self.lab.report(self.target_run)["math_verdict"], "PROVED")

    def test_corrupt_ledger_and_redirected_path_are_rejected(self):
        row = self.create()
        with self.store.transaction() as db:
            db.execute("UPDATE items SET hash='bad'")
        with self.assertRaises(ValueError):
            self.store.report(row["reuse_id"])
        path = self.store.path
        self.store.path = path.with_name("redirected.sqlite3")
        self.store.path.symlink_to(path)
        with self.assertRaises(ValueError):
            self.store.search()

    def test_http_real_attachment_and_source_archive_removal_propagation(self):
        root = self.store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib_reuse = self.store
            server.mathlib_knowledge = self.knowledge
            server.mathlib = self.lab
            data = create_data(self.store, self.data)
            status, row, _ = request(server, "POST", "/v1/mathlib-reuse", data)
            self.assertEqual(status, 201, row)
            route = "/v1/mathlib-reuse/" + row["reuse_id"]
            for suffix in ("", "/request", "/export"):
                self.assertEqual(request(server, "GET", route + suffix)[0], 200)
            self.assertEqual(
                request(server, "POST", route + "/preview-attachment", {"run_id": self.target_run})[
                    0
                ],
                200,
            )
            self.assertEqual(
                request(server, "POST", route + "/attach", self.attach_data(row["reuse_id"]))[1][
                    "state"
                ],
                "ACTIVE",
            )
            status, result, _ = request(
                server, "POST", "/v1/mathlib-knowledge/" + self.archive_id + "/remove", retirement()
            )
            self.assertEqual(status, 200, result)
            with self.store.transaction(False) as db:
                self.assertEqual(self.store.read(db, row["reuse_id"])["state"], "INVALIDATED")
            self.assertEqual(request(server, "GET", route + "/request")[0], 400)
            self.assertEqual(
                request(server, "POST", route + "/remove", retirement())[1]["state"], "DELETED"
            )

    def test_public_result_revocation_propagates_without_source_archive_damage(self):
        row = self.bind()
        root = self.store.base.parents[1]
        copy_web_inputs(root)
        with running_server(root) as server:
            server.mathlib_reuse = self.store
            server.mathlib = self.lab
            status, result, _ = request(
                server,
                "POST",
                "/v1/mathlib/runs/" + self.target_run + "/revoke",
                {"reviewer": "Reviewer", "reason": "Withdraw result alignment", "confirmed": True},
            )
            self.assertEqual(status, 200, result)
        with self.store.transaction(False) as db:
            self.assertEqual(self.store.read(db, row["reuse_id"])["state"], "INVALIDATED")
        self.assertEqual(self.knowledge.report(self.archive_id)["state"], "ACTIVE")

    def test_handoff_creates_and_checks_another_independent_real_run(self):
        row = self.create()
        value = self.store.request(row["reuse_id"])
        target = json.loads(json.dumps(value["request"]).replace("30.0", "30"))
        fresh = submit(self.lab, target)
        self.assertNotIn(fresh["run_id"], (self.source_run, self.target_run))
        self.assertEqual(self.store.report(row["reuse_id"])["math_verdict"], "UNKNOWN")
        self.lab.work_once(fresh["run_id"])
        self.assertEqual(self.lab.report(fresh["run_id"])["math_verdict"], "PROVED")
        self.assertEqual(self.store.report(row["reuse_id"])["state"], "AWAITING_CHECK")
        bound = self.store.attach(
            row["reuse_id"], self.attach_data(row["reuse_id"], fresh["run_id"])
        )
        self.assertEqual(bound["math_verdict"], "PROVED")


if __name__ == "__main__":
    unittest.main()
