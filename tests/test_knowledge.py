"""Private knowledge uses actual paper/Lean records, never invented positive evidence."""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.domain import canonical
from mra.knowledge import KnowledgeStore
from mra.paper_review import PaperReviewStore
from test_http_api import copy_web_inputs, request, running_server
from test_paper_review import example, submission

ROOT = Path(__file__).resolve().parents[1]


def admission(plan):
    return {
        "review_id": plan["review_id"],
        "request_id": "knowledge-test-" + uuid.uuid4().hex,
        "plan_fingerprint": plan["plan_fingerprint"],
        "reviewer": "repository-knowledge-test-author",
        "confirmed": True,
        "rights_confirmed": True,
    }


class KnowledgeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.store = KnowledgeStore(self.root)

    def test_empty_private_index_contains_no_preview_or_claimed_proofs(self):
        result = self.store.search()
        self.assertEqual(result["items"], [])
        self.assertEqual(result["active_count"], 0)
        self.assertFalse(result["fresh_attestation"])
        self.assertFalse((self.root / "artifacts/runs").exists())

    def test_pending_cancelled_and_recovered_source_cannot_be_admitted(self):
        papers = PaperReviewStore(self.root)
        queued = papers.submit(submission(example()))
        with self.assertRaises(ValueError):
            self.store.prepare(queued["review_id"])
        papers.cancel(queued["review_id"])
        with self.assertRaises(ValueError):
            self.store.prepare(queued["review_id"])

    def test_user_cannot_supply_pass_receipts_formulas_or_dependency_edges(self):
        with self.assertRaises(ValueError):
            self.store.admit(
                {
                    "math_verdict": "PROVED",
                    "evidence_level": "E4_ALIGNED_AND_VERIFIED",
                    "receipt": {"PASS": True},
                    "formula": "True",
                }
            )
        self.assertEqual(self.store.search()["total"], 0)

    def test_private_rights_and_named_confirmation_are_required_before_source_read(self):
        base = {
            "review_id": "paper-missing",
            "request_id": "test-missing",
            "plan_fingerprint": "unused",
            "reviewer": "researcher",
            "confirmed": True,
            "rights_confirmed": True,
        }
        for changes in ({"confirmed": False}, {"rights_confirmed": False}, {"reviewer": "  "}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.admit({**base, **changes})

    def test_corrupt_ledger_never_produces_search_results(self):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO items VALUES (?,?,?)",
                ("knowledge-fake", canonical({"item_id": "knowledge-fake"}).decode(), "wrong-hash"),
            )
        with self.assertRaises(ValueError):
            self.store.search()

    def test_symlink_storage_and_database_are_rejected(self):
        target = self.root / "outside"
        target.mkdir()
        self.store.path.unlink()
        self.store.path.symlink_to(target / "db")
        with self.assertRaises(ValueError):
            self.store.search()
        self.store.path.unlink()
        shutil.rmtree(self.store.proofs)
        self.store.proofs.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            KnowledgeStore(self.root)

    def test_search_limits_and_action_payloads_are_strict(self):
        for query, include in [("x" * 201, False), ([], False), ("", 1)]:
            with self.assertRaises(ValueError):
                self.store.search(query, include)
        for data in (
            {},
            {"reviewer": "a", "reason": "x", "confirmed": False},
            {"reviewer": "a", "reason": " ", "confirmed": True},
        ):
            with self.assertRaises(ValueError):
                self.store.action("knowledge-unknown", data)

    def test_http_search_prepare_actions_keep_origin_csrf_and_payload_boundaries(self):
        with running_server(self.root) as server:
            self.assertEqual(request(server, "GET", "/library.js")[0], 200)
            self.assertEqual(
                request(
                    server, "GET", "/v1/knowledge/search?q=%E6%95%B0%E5%AD%A6&include_inactive=true"
                )[1]["items"],
                [],
            )
            for route in (
                "/v1/knowledge/search?q=a&q=b",
                "/v1/knowledge/search?include_inactive=1",
                "/v1/knowledge/search?file=../private",
            ):
                self.assertEqual(request(server, "GET", route)[0], 400)
            for route in ("/v1/knowledge/search", "/library.js"):
                self.assertEqual(
                    request(server, "GET", route, headers={"Origin": "https://outside.example"})[0],
                    403,
                )
            self.assertEqual(
                request(
                    server,
                    "POST",
                    "/v1/knowledge/prepare",
                    {"review_id": "none"},
                    headers={"X-MRA-CSRF": "stale"},
                )[0],
                403,
            )
            self.assertEqual(
                request(server, "POST", "/v1/knowledge", {"math_verdict": "PROVED"})[0], 400
            )
            self.assertEqual(
                request(server, "POST", "/v1/knowledge/knowledge-unknown/remove", {})[0], 400
            )

    def test_cli_empty_search_and_unconfirmed_actions_are_real_offline_commands(self):
        result = subprocess.run(
            [sys.executable, "-m", "mra", "knowledge-search", "--query", uuid.uuid4().hex],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["total"], 0)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mra",
                "knowledge-remove",
                "knowledge-unknown",
                "--reviewer",
                "test",
                "--reason",
                "test",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(os.environ.get("MRA_REAL_LEAN") == "1", "requires actual sealed Lean runtime")
class KnowledgeRealLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.papers = PaperReviewStore(ROOT)
        cls.reviews = {}
        for name in ("valid-proof", "true-claim-bad-proof", "missing-condition"):
            queued = cls.papers.submit(submission(example(name)))
            report = cls.papers.work_once(queued["review_id"])["review"]
            if report["state"] != "COMPLETED":
                raise AssertionError(report)
            cls.reviews[name] = report

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(Path(self.temp.name))
        # Separate private archive, real authoritative source ledger and real checker files.
        self.store.papers = self.papers
        self.review = self.reviews["valid-proof"]

    def admitted(self):
        plan = self.store.prepare(self.review["review_id"])
        data = admission(plan)
        result = self.store.admit(data)
        return {i["source_ref"]["key"]: i for i in result["items"]}, data

    def test_real_e4_archive_is_exact_private_idempotent_and_restartable(self):
        items, data = self.admitted()
        self.assertEqual(set(items), {"claim", "s1", "s2"})
        self.assertEqual(items["s2"]["dependencies"], [items["s1"]["item_id"]])
        self.assertEqual(items["claim"]["dependencies"], [])
        for item in items.values():
            self.assertEqual(
                (item["state"], item["trust_level"], item["math_verdict"]),
                ("ACTIVE", "E4_ALIGNED_AND_VERIFIED", "PROVED"),
            )
            self.assertFalse(item["fresh_attestation"])
            self.assertFalse(item["privacy"]["sharing_allowed"])
            self.assertFalse(item["privacy"]["training_allowed"])
            self.assertTrue(self.store.archive_intact(item))
            directory = self.store.proofs / item["item_id"]
            payload = json.loads((directory / "receipt.json").read_text())["payload"]
            self.assertNotEqual(payload["candidate_pid"], payload["replay"]["pid"])
        self.assertEqual(self.store.admit(data)["items"], list(items.values()))
        with self.assertRaises(ValueError):
            self.store.admit({**data, "reviewer": "different"})
        restarted = KnowledgeStore(self.store.root)
        restarted.papers = self.papers
        self.assertEqual(restarted.search()["active_count"], 3)

    def test_withdrawal_propagates_to_step_but_not_independent_claim(self):
        items, _ = self.admitted()
        self.store.action(
            items["s1"]["item_id"],
            {"reviewer": "test", "reason": "withdraw original step", "confirmed": True},
        )
        self.assertEqual(self.store.report(items["s1"]["item_id"])["state"], "WITHDRAWN")
        self.assertEqual(self.store.report(items["s2"]["item_id"])["state"], "INVALIDATED")
        self.assertEqual(self.store.report(items["claim"]["item_id"])["state"], "ACTIVE")
        self.assertEqual(self.store.search()["total"], 1)
        self.assertEqual(self.store.search("", True)["total"], 3)
        self.assertTrue(self.papers.report(self.review["review_id"])["results_available"])

    def test_changed_archive_invalidates_dependent_and_never_revives_after_restoration(self):
        items, _ = self.admitted()
        path = self.store.proofs / items["s1"]["item_id"] / "replay/proof.ndjson"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        self.assertEqual(self.store.report(items["s2"]["item_id"])["state"], "INVALIDATED")
        path.write_bytes(original)
        self.assertEqual(self.store.report(items["s1"]["item_id"])["math_verdict"], "UNKNOWN")
        self.assertEqual(self.store.search()["total"], 1)

    def test_changed_original_source_disables_all_items_even_if_archive_is_intact(self):
        items, _ = self.admitted()
        path = Path(self.review["checks"][0]["result"]["artifact_directory"]) / "report.json"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        try:
            self.assertEqual(self.store.search()["total"], 0)
        finally:
            path.write_bytes(original)
        self.assertTrue(self.store.archive_intact(items["claim"]))
        self.assertEqual(self.store.search()["total"], 0)
        with self.assertRaises(ValueError):
            self.store.admit(admission(self.store.prepare(self.review["review_id"])))

    def test_remove_clears_step_subtree_archives_and_content_but_keeps_source(self):
        items, data = self.admitted()
        action = {"reviewer": "test", "reason": "remove private copies", "confirmed": True}
        result = self.store.action(items["s1"]["item_id"], action, delete=True)
        self.assertEqual(set(result["affected"]), {items["s1"]["item_id"], items["s2"]["item_id"]})
        for key in ("s1", "s2"):
            record = self.store.report(items[key]["item_id"])
            self.assertEqual(record["state"], "DELETED")
            self.assertIsNone(record["content"])
            self.assertFalse((self.store.proofs / items[key]["item_id"]).exists())
        self.assertEqual(self.store.search("", True)["total"], 1)
        self.assertFalse(result["source_review_deleted"])
        self.assertTrue(self.papers.report(self.review["review_id"])["results_available"])
        self.assertEqual(
            self.store.action(items["s1"]["item_id"], action, delete=True)["item"]["state"],
            "DELETED",
        )
        self.assertEqual(self.store.admit(data)["items"][1]["math_verdict"], "UNKNOWN")

    def test_refutation_is_not_inserted_and_true_claim_survives_bad_author_proof(self):
        with self.assertRaises(ValueError):
            self.store.prepare(self.reviews["missing-condition"]["review_id"])
        plan = self.store.prepare(self.reviews["true-claim-bad-proof"]["review_id"])
        self.assertEqual([i["source"]["key"] for i in plan["items"]], ["claim"])
        result = self.store.admit(admission(plan))
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["state"], "ACTIVE")

    def test_failed_admission_has_no_visible_rows_and_exact_orphan_archives_can_be_recovered(self):
        plan = self.store.prepare(self.review["review_id"])
        original = self.store.prepare
        data = admission(plan)
        with (
            patch.object(
                self.store, "prepare", side_effect=[plan, ValueError("source revoked during copy")]
            ),
            self.assertRaises(ValueError),
        ):
            self.store.admit(data)
        self.assertEqual(self.store.search()["total"], 0)
        self.assertEqual(len(self.store.admit(data)["items"]), 3)
        self.assertEqual(original(self.review["review_id"]), plan)

    def test_concurrent_admissions_preserve_one_source_entry_and_one_admission_event(self):
        plan = self.store.prepare(self.review["review_id"])
        data = admission(plan)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.store.admit, [data, copy.deepcopy(data)]))
        self.assertEqual(results[0]["items"], results[1]["items"])
        self.assertEqual(self.store.search()["total"], 3)
        self.assertTrue(all(len(item["events"]) == 1 for item in results[0]["items"]))

    def test_actual_source_confirmation_revocation_stops_search(self):
        bundle = example("valid-proof")
        bundle["steps"] = []
        queued = self.papers.submit(submission(bundle))
        report = self.papers.work_once(queued["review_id"])["review"]
        plan = self.store.prepare(report["review_id"])
        item = self.store.admit(admission(plan))["items"][0]
        self.papers.cancel(report["review_id"], revoke=True)
        self.assertEqual(self.store.report(item["item_id"])["state"], "INVALIDATED")
        self.assertEqual(self.store.search()["items"], [])


if __name__ == "__main__":
    unittest.main()
