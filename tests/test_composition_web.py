"""Loopback transport contracts; separately opted-in real worker integration."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import unittest
from unittest.mock import patch
import uuid

from mra.http_api import TaskPump
from mra.lemma_composition import FILES
from mra.tasks import WorkerBusy
from test_http_api import request, running_server
from test_lemma_composition import composition, real_store, signatures, admit
from test_search_evaluation import copy_sources

ROOT = Path(__file__).resolve().parents[1]
BASE = "/v1/lemma-compositions"


class ContractTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_sources(self.root)
        for name in FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.input = composition()
        fixtures = {
            b["item_id"]: {
                "request": signature,
                "version": 1,
                "archive_fingerprint": "fixture",
                "source_fingerprint": "fixture",
                "alignment": {"reviewer": "fixture"},
                "formal_statement": "nonexecutable protocol fixture",
            }
            for b, signature in zip(self.input["sources"], signatures())
        }

        def source(_, binding):
            return {**deepcopy(fixtures[binding["item_id"]]), **binding}, b"not proof data"

        self.addCleanup(patch.stopall)
        patch("mra.lemma_composition.load_source", side_effect=source).start()
        patch("mra.composition_web.load_source", side_effect=source).start()

    def submit(self, server, **changes):
        status, plan, _ = request(server, "POST", BASE + "/preview", self.input)
        self.assertEqual(status, 200)
        return {
            "request_id": "web-" + uuid.uuid4().hex,
            "request": self.input,
            "plan_fingerprint": plan["plan_fingerprint"],
            "reviewer": "HTTP contract author",
            "confirmed": True,
            **changes,
        }

    def test_static_page_catalog_and_safe_complete_source_descriptor(self):
        with running_server(self.root) as server:
            for path in (
                "/compositions",
                "/compositions.js",
                "/composition-notation.js",
                "/compositions.css",
            ):
                self.assertEqual(request(server, "GET", path)[0], 200)
            status, value, _ = request(server, "GET", BASE + "/catalog")
            self.assertEqual(status, 200)
            self.assertEqual(len(value["references"]), 22)
            status, value, _ = request(
                server, "GET", BASE + "/sources/" + self.input["sources"][0]["item_id"]
            )
            self.assertEqual(status, 200)
            self.assertEqual(value["signature"]["assumptions"], signatures()[0]["assumptions"])
            self.assertFalse(value["fresh_attestation"])
            self.assertNotIn("proof", value["signature"])
            self.assertNotIn("data", value)

    def test_preview_is_unknown_and_never_enqueues_or_executes(self):
        with (
            running_server(self.root) as server,
            patch.object(
                server.compositions,
                "execute",
                side_effect=AssertionError("preview must not execute"),
            ),
        ):
            status, plan, _ = request(server, "POST", BASE + "/preview", self.input)
            self.assertEqual(status, 200)
            self.assertEqual((plan["math_verdict"], plan["evidence_level"]), ("UNKNOWN", "E0"))
            self.assertEqual(len(plan["applications"]), 2)
            self.assertEqual(request(server, "GET", BASE + "/runs")[1]["runs"], [])

    def test_exact_named_idempotent_submission_and_changed_body_conflict(self):
        with running_server(self.root) as server:
            data = self.submit(server)
            for change in (
                {"confirmed": False},
                {"confirmed": 1},
                {"reviewer": " "},
                {"plan_fingerprint": "old"},
                {"extra": True},
            ):
                self.assertEqual(
                    request(server, "POST", BASE + "/runs", {**data, **change})[0], 400
                )
            first = request(server, "POST", BASE + "/runs", data)
            again = request(server, "POST", BASE + "/runs", data)
            self.assertEqual(first[0], 201)
            self.assertEqual(first[1]["run_id"], again[1]["run_id"])
            self.assertEqual(
                request(server, "POST", BASE + "/runs", {**data, "reviewer": "other"})[0], 400
            )

    def test_cancel_revoke_export_and_recovery_keep_unknown(self):
        with running_server(self.root) as server:
            row = request(server, "POST", BASE + "/runs", self.submit(server))[1]
            path = BASE + "/runs/" + row["run_id"]
            self.assertEqual(request(server, "POST", path + "/cancel", {"extra": True})[0], 400)
            self.assertEqual(request(server, "POST", path + "/cancel", {})[1]["state"], "CANCELLED")
            revoke = {"reviewer": "HTTP author", "reason": "control case", "confirmed": True}
            self.assertEqual(
                request(server, "POST", path + "/revoke", revoke)[1]["math_verdict"], "UNKNOWN"
            )
            self.assertTrue(request(server, "GET", path + "/export")[1]["invalidated"])
            row = request(server, "POST", BASE + "/runs", self.submit(server))[1]
            with server.compositions.transaction() as db:
                value = server.compositions.read(row["run_id"], db)
                value.update(state="RUNNING", stage="REPLAY_RUNNING")
                server.compositions.save(db, value)
            server.compositions.recover()
            row = request(server, "GET", BASE + "/runs/" + row["run_id"])[1]
            self.assertEqual((row["state"], row["math_verdict"]), ("INTERRUPTED", "UNKNOWN"))

    def test_security_strict_json_limits_and_unknown_routes(self):
        with running_server(self.root) as server:
            for header in ({"Origin": "https://invalid.example"}, {"X-MRA-CSRF": "bad"}):
                self.assertEqual(
                    request(server, "POST", BASE + "/preview", self.input, headers=header)[0], 403
                )
            self.assertEqual(
                request(server, "POST", BASE + "/preview", raw=b'{"schema":1,"schema":2}')[0], 400
            )
            self.assertEqual(request(server, "POST", BASE + "/preview", raw=b" " * 196609)[0], 413)
            for path in (
                BASE + "/runs/../../README.md",
                BASE + "/sources/%2e%2e",
                BASE + "/runs/x/download",
                BASE + "/unknown",
            ):
                self.assertIn(request(server, "GET", path)[0], (400, 404))

    def test_temporary_busy_returns_503_without_invalidation_or_hidden_retry(self):
        with running_server(self.root) as server:
            data = self.submit(server)
            row = request(server, "POST", BASE + "/runs", data)[1]
            for failure in (WorkerBusy("busy"), sqlite3.OperationalError("database is locked")):
                with patch("mra.lemma_composition.load_source", side_effect=failure):
                    self.assertEqual(
                        request(server, "GET", BASE + "/runs/" + row["run_id"])[0], 503
                    )
                self.assertFalse(server.compositions.read(row["run_id"])["invalidated"])
            with patch.object(
                server.compositions, "submit", side_effect=WorkerBusy("busy")
            ) as submit:
                self.assertEqual(request(server, "POST", BASE + "/runs", data)[0], 503)
                self.assertEqual(submit.call_count, 1)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "Explicit real Lean integration only")
class RealTests(unittest.TestCase):
    def test_actual_http_background_composition_wrong_premise_cancel_and_revoke(self):
        out = (
            ROOT / "artifacts/lemma-composition-web-20261028-01" / ("real-http-" + uuid.uuid4().hex)
        )
        out.mkdir(parents=True)
        store = real_store(out / "data")
        lab = store.knowledge.sources.mathlib
        source = signatures()[0]
        plan = lab.preview(source)
        row = lab.submit(
            {
                "request_id": "web-source-" + uuid.uuid4().hex,
                "request": source,
                "plan_fingerprint": plan["plan_fingerprint"],
                "reviewer": "MRA048 authored-source reviewer",
                "confirmed": True,
            }
        )
        lab.work_once(row["run_id"])
        self.assertEqual(lab.report(row["run_id"])["math_verdict"], "PROVED")
        item = admit(store.knowledge, row["run_id"])
        value = {
            **source,
            "schema": "private-lemma-composition-v1",
            "sources": [{"alias": "chain", "item_id": item["item_id"]}],
            "proof": {
                "lemma": "chain",
                "terms": [{"var": n} for n in source["variables"]],
                "proofs": [{"hyp": h["name"]} for h in source["assumptions"]],
            },
        }
        # Isolated data, with the real project seal/runtime and real global worker lock.
        shutil.copytree(ROOT / "mra/web", out / "server/mra/web")
        with running_server(out / "server") as server:
            server.compositions = store
            results = {}

            def submit(input):
                status, preview, _ = request(server, "POST", BASE + "/preview", input)
                self.assertEqual(status, 200)
                status, result, _ = request(
                    server,
                    "POST",
                    BASE + "/runs",
                    {
                        "request_id": "real-http-" + uuid.uuid4().hex,
                        "request": input,
                        "plan_fingerprint": preview["plan_fingerprint"],
                        "reviewer": "MRA048 real HTTP alignment reviewer",
                        "confirmed": True,
                    },
                )
                self.assertEqual(status, 201)
                return result

            cancelled = submit(value)
            results["cancelled"] = request(
                server, "POST", BASE + "/runs/" + cancelled["run_id"] + "/cancel", {}
            )[1]
            good = submit(value)
            wrong = deepcopy(value)
            wrong["proof"]["proofs"].reverse()
            bad = submit(wrong)
            pump = TaskPump(server.store, compositions=store)
            pump.thread.start()
            try:
                for name, row in (("positive", good), ("wrong_premise", bad)):
                    end = time.monotonic() + 150
                    while time.monotonic() < end:
                        status, report, _ = request(server, "GET", BASE + "/runs/" + row["run_id"])
                        if status == 200 and report["state"] not in ("RUNNING", "QUEUED"):
                            results[name] = report
                            break
                        time.sleep(0.3)
                    else:
                        self.fail("Real worker did not reach a terminal state")
            finally:
                pump.close()
            positive = results["positive"]
            self.assertEqual(
                (positive["state"], positive["math_verdict"], positive["evidence_level"]),
                ("COMPLETED", "PROVED", "E4"),
            )
            self.assertNotEqual(
                positive["result"]["candidate"]["pid"], positive["result"]["replay"]["pid"]
            )
            self.assertEqual(results["wrong_premise"]["math_verdict"], "UNKNOWN")
            self.assertEqual(results["wrong_premise"]["state"], "REJECTED")
            self.assertEqual(results["cancelled"]["state"], "CANCELLED")
            path = BASE + "/runs/" + good["run_id"]
            self.assertEqual(request(server, "GET", path + "/export")[1]["math_verdict"], "PROVED")
            results["revoked"] = request(
                server,
                "POST",
                path + "/revoke",
                {
                    "reviewer": "MRA048 revocation reviewer",
                    "reason": "Retire isolated verification case",
                    "confirmed": True,
                },
            )[1]
            self.assertEqual(results["revoked"]["math_verdict"], "UNKNOWN")
            self.assertTrue(results["revoked"]["invalidated"])
        (out / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
