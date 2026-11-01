"""Actual SQLite transactions and HTTP; no synthetic mathematical evidence."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from mra.research import ResearchStore as Legacy
from mra.research_capacity import ManagedResearchStore, LIMITS
from mra.research_studio import StudioStore
from mra.research_protocol import source_request
from test_http_api import copy_web_inputs, request, running_server

ROOT = Path(__file__).resolve().parents[1]


def rid():
    return "capacity-" + uuid.uuid4().hex


def new_data():
    return {
        "request_id": rid(),
        "title": "Capacity transaction test",
        "question": "Preserve complete historical research while organizing active projects.",
    }


def change(revision=0, **extra):
    return {
        "request_id": rid(),
        "expected_revision": revision,
        "reviewer": "Repository capacity test",
        "reason": "Reversible organization; no evidence claim",
        "confirmed": True,
        **extra,
    }


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = Legacy(self.root)
        self.old = [self.legacy.create(new_data()) for _ in range(2)]
        self.policy = patch.dict(LIMITS, active_projects=2, retained_projects=4)
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.store = ManagedResearchStore(self.root)
        self.key = self.old[0]["project_id"]

    def legacy_rows(self):
        with self.legacy.transaction() as db:
            return {
                name: [tuple(r) for r in db.execute("SELECT * FROM " + name + " ORDER BY 1,2")]
                for name in ("projects", "requests", "revisions")
            }

    def test_upgrade_archive_and_rollback_keep_exact_legacy_records(self):
        before = self.legacy_rows()
        first = self.store.change_lifecycle(self.key, "archive", change())
        self.assertEqual(first["lifecycle"]["state"], "ARCHIVED")
        self.assertEqual(
            self.store.capacity_catalog()["counts"],
            {"active": 1, "archived": 1, "retained": 2, "available": 1},
        )
        self.assertEqual(self.legacy_rows(), before)
        self.assertEqual(self.legacy.report(self.key), self.old[0])
        self.store.change_lifecycle(self.key, "restore", change(1))
        self.assertEqual(self.legacy_rows(), before)
        self.assertEqual(self.store.report(self.key)["lifecycle"]["revision"], 2)

    def test_archive_releases_active_slot_but_does_not_release_retained_capacity(self):
        with self.assertRaisesRegex(ValueError, "Active project"):
            self.store.create(new_data())
        self.store.change_lifecycle(self.key, "archive", change())
        third = self.store.create(new_data())
        self.assertEqual(self.store.capacity_catalog()["counts"]["retained"], 3)
        self.store.change_lifecycle(third["project_id"], "archive", change())
        fourth = self.store.create(new_data())
        self.store.change_lifecycle(fourth["project_id"], "archive", change())
        with self.assertRaisesRegex(ValueError, "Retained project"):
            self.store.create(new_data())

    def test_restore_checks_current_capacity_and_failure_does_not_change_state(self):
        self.store.change_lifecycle(self.key, "archive", change())
        self.store.create(new_data())
        before = self.store.capacity_catalog()
        with self.assertRaisesRegex(ValueError, "Active project"):
            self.store.change_lifecycle(self.key, "restore", change(1))
        self.assertEqual(self.store.capacity_catalog(), before)
        self.assertEqual(len(self.store.list_projects()), 2)
        self.assertEqual(self.store.report(self.key)["lifecycle"]["state"], "ARCHIVED")

    def test_repeated_creation_at_capacity_returns_original_project(self):
        self.store.change_lifecycle(self.key, "archive", change())
        data = new_data()
        first = self.store.create(data)
        self.assertEqual(self.store.create(data)["project_id"], first["project_id"])
        with self.assertRaises(ValueError):
            self.store.create({**data, "question": "Changed content"})

    def test_old_archive_request_after_restore_does_not_archive_again(self):
        data = change()
        self.store.change_lifecycle(self.key, "archive", data)
        self.assertTrue(self.store.change_lifecycle(self.key, "archive", data)["idempotent_replay"])
        self.store.change_lifecycle(self.key, "restore", change(1))
        replay = self.store.change_lifecycle(self.key, "archive", data)
        self.assertEqual(replay["lifecycle"]["state"], "ACTIVE")
        self.assertEqual(replay["lifecycle"]["revision"], 2)
        with self.assertRaises(ValueError):
            self.store.change_lifecycle(self.key, "archive", {**data, "reason": "Different reason"})
        with self.assertRaises(ValueError):
            self.store.change_lifecycle(self.old[1]["project_id"], "archive", data)

    def test_stale_revision_truthy_confirmation_and_extra_fields_are_rejected(self):
        for extra in (
            {"expected_revision": True},
            {"expected_revision": 1},
            {"confirmed": 1},
            {"confirmed": False},
            {"reviewer": " "},
            {"force": True},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.store.change_lifecycle(self.key, "archive", change(**extra))
        self.assertEqual(self.store.capacity_catalog()["counts"]["active"], 2)

    def test_deleted_lifecycle_cannot_silently_revive_project(self):
        self.store.change_lifecycle(self.key, "archive", change())
        with self.store.transaction() as db:
            db.execute("DELETE FROM research_project_lifecycle WHERE project_id=?", (self.key,))
        for method in (
            self.store.capacity_catalog,
            lambda: self.store.report(self.key),
        ):
            with self.assertRaisesRegex(ValueError, "missing"):
                method()

    def test_missing_request_history_and_changed_lifecycle_fail_closed(self):
        self.store.change_lifecycle(self.key, "archive", change())
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM research_lifecycle_requests WHERE project_id=?",
                (self.key,),
            )
        with self.assertRaises(ValueError):
            self.store.capacity_catalog()

    def test_partial_schema_is_not_automatically_recreated(self):
        with self.store.transaction() as db:
            db.execute("DROP TABLE research_lifecycle_requests")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            ManagedResearchStore(self.root)
        self.assertEqual(self.legacy.report(self.key), self.old[0])

    def test_changed_lifecycle_hash_is_rejected_without_rewriting_legacy_body(self):
        before = self.legacy_rows()
        self.store.change_lifecycle(self.key, "archive", change())
        with self.store.transaction() as db:
            db.execute(
                "UPDATE research_project_lifecycle SET hash='changed' WHERE project_id=?",
                (self.key,),
            )
        with self.assertRaises(ValueError):
            self.store.capacity_catalog()
        self.assertEqual(self.legacy_rows(), before)

    def test_changed_schema_identity_is_not_accepted_as_a_new_version(self):
        with self.store.transaction() as db:
            db.execute("UPDATE research_capacity_identity SET schema='unsupported'")
        with self.assertRaises(ValueError):
            ManagedResearchStore(self.root)

    def test_archive_sql_failure_rolls_back_metadata_and_idempotency(self):
        with self.store.transaction() as db:
            db.execute(
                "CREATE TRIGGER fail_lifecycle BEFORE INSERT ON research_lifecycle_requests BEGIN SELECT RAISE(ABORT,'injected lifecycle failure'); END"
            )
        before = self.legacy_rows()
        data = change()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.change_lifecycle(self.key, "archive", data)
        self.assertEqual(self.legacy_rows(), before)
        self.assertEqual(self.store.capacity_catalog()["counts"]["active"], 2)
        with self.store.transaction() as db:
            db.execute("DROP TRIGGER fail_lifecycle")
        self.assertFalse(
            self.store.change_lifecycle(self.key, "archive", data)["idempotent_replay"]
        )

    def test_create_sql_failure_rolls_back_body_snapshot_and_request(self):
        self.store.change_lifecycle(self.key, "archive", change())
        with self.store.transaction() as db:
            db.execute(
                "CREATE TRIGGER fail_project_request BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'injected create failure'); END"
            )
        before = self.legacy_rows()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create(new_data())
        self.assertEqual(self.legacy_rows(), before)

    def test_concurrent_creates_cannot_overbook_one_slot(self):
        self.store.change_lifecycle(self.key, "archive", change())

        def create_one(_):
            try:
                return ManagedResearchStore(self.root).create(new_data())["project_id"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(create_one, range(6)))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(self.store.capacity_catalog()["counts"]["active"], 2)

    def test_concurrent_restore_and_create_share_the_same_quota_transaction(self):
        self.store.change_lifecycle(self.key, "archive", change())

        def attempt(which):
            try:
                store = ManagedResearchStore(self.root)
                if which:
                    store.create(new_data())
                else:
                    store.change_lifecycle(self.key, "restore", change(1))
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (False, True)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.capacity_catalog()["counts"]["active"], 2)

    def test_studio_can_create_after_archive_when_retained_count_exceeds_active_limit(
        self,
    ):
        self.store.change_lifecycle(self.key, "archive", change())
        source = {
            "title": "New studio capacity check",
            "question": "Preserve the original mathematical statement and every assumption.",
            "areas": ["实分析与复分析"],
            "definitions": "",
            "assumptions": "",
        }
        study = StudioStore(self.root).create({"request_id": rid(), "source": source})
        self.assertEqual(study["source"], source_request(source))
        self.assertEqual(study["math_verdict"], "UNKNOWN")
        self.assertEqual(self.legacy.report(study["project_id"])["title"], source["title"])
        self.assertEqual(self.store.capacity_catalog()["counts"]["retained"], 3)


class CapacityHttpTests(unittest.TestCase):
    def test_real_routes_filter_archived_keep_direct_links_and_guard_mutations(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            root = Path(directory)
            copy_web_inputs(root)
            with (
                patch.dict(LIMITS, active_projects=2, retained_projects=4),
                running_server(root) as server,
            ):
                projects = [
                    request(server, "POST", "/v1/research", new_data())[1] for _ in range(2)
                ]
                key = projects[0]["project_id"]
                endpoint = "/v1/research-capacity/" + key + "/archive"
                status, _, _ = request(
                    server, "POST", endpoint, change(), headers={"X-MRA-CSRF": "bad"}
                )
                self.assertEqual(status, 403)
                status, result, _ = request(server, "POST", endpoint, change())
                self.assertEqual(status, 200)
                self.assertEqual(result["lifecycle"]["state"], "ARCHIVED")
                status, listed, _ = request(server, "GET", "/v1/research")
                self.assertEqual(len(listed["projects"]), 1)
                self.assertEqual(listed["capacity"]["counts"]["archived"], 1)
                status, direct, _ = request(server, "GET", "/v1/research/" + key)
                self.assertEqual(status, 200)
                self.assertEqual(direct["version"], projects[0]["version"])
                self.assertEqual(direct["lifecycle"]["state"], "ARCHIVED")
                self.assertEqual(direct["lean_calls"], 0)
                status, _, _ = request(server, "POST", "/v1/research", new_data())
                self.assertEqual(status, 201)
                status, _, _ = request(
                    server,
                    "POST",
                    "/v1/research-capacity/" + key + "/restore",
                    change(1),
                )
                self.assertEqual(status, 400)
                status, catalog, _ = request(server, "GET", "/v1/research-capacity")
                self.assertEqual(status, 200)
                self.assertEqual(
                    catalog["counts"],
                    {"active": 2, "archived": 1, "retained": 3, "available": 0},
                )
                self.assertEqual(request(server, "GET", "/v1/research-capacity?force=1")[0], 400)


if __name__ == "__main__":
    unittest.main()
