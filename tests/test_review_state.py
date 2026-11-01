"""Real SQLite/filesystem/concurrency tests, using local fixtures, never a live model."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from mra.domain import canonical
from mra.formalizer import CandidateReview, SourceClaim, StructuredFormalizer, strict_json
from mra.intake import FixtureModelService, IntakeStore

ROOT = Path(__file__).resolve().parents[1]


class ReviewStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = IntakeStore(self.root)
        self.formalizer = StructuredFormalizer(
            FixtureModelService(ROOT / "evals/formalization/fixtures.json")
        )
        self.source = SourceClaim.from_dict(
            strict_json((ROOT / "evals/formalization/clear-nat.json").read_text(encoding="utf-8"))
        )
        self.draft = self.formalizer.formalize_claim(self.source)
        self.directory = self.store.save(self.draft)
        self.review = CandidateReview(
            self.draft.fingerprint,
            self.draft.candidates[0].fingerprint,
            "local reviewer",
            "accept",
            "Checked domain, quantifiers and assumptions",
        )

    def state(self, directory=None):
        return self.store.report(directory or self.directory)["review_state"]

    def event(self, review=None, event_id=None):
        path = self.store.review(self.directory, review or self.review, event_id=event_id)
        return strict_json(path.read_text())

    def revoke(self, review_id, **changes):
        args = {
            "target": review_id,
            "reviewer": self.review.reviewer,
            "note": "Withdrawn after review",
            "draft_fingerprint": self.draft.fingerprint,
            "candidate_fingerprint": self.draft.candidates[0].fingerprint,
        }
        args.update(changes)
        path = self.store.revoke(self.directory, **args)
        return strict_json(path.read_text())

    def revision(self):
        source = self.source.revise("所有自然数都等于零。", ())
        return self.formalizer.formalize_claim(source)

    def test_current_state_survives_a_fresh_store_and_never_grants_evidence(self):
        self.assertEqual(self.state()["candidates"][0]["status"], "UNREVIEWED")
        event = self.event()
        self.store = IntakeStore(self.root)
        state = self.state()["candidates"][0]
        self.assertEqual(state["status"], "ACCEPTED")
        self.assertEqual(state["effective_review_id"], event["event_id"])
        self.assertTrue(state["accepted"])
        self.assertEqual(state["math_verdict"], "UNKNOWN")
        self.assertEqual(state["evidence_level"], "E0_CANDIDATE")
        self.assertFalse(state["can_execute_lean"])
        self.assertEqual(self.state()["actor_authentication"], "local-assertion-only")

    def test_reject_overrides_accept_and_revocation_does_not_revive_accept(self):
        self.event()
        rejected = self.event(replace(self.review, decision="reject"))
        self.assertEqual(self.state()["candidates"][0]["status"], "REJECTED")
        self.revoke(rejected["event_id"])
        state = self.state()["candidates"][0]
        self.assertEqual(state["status"], "REVOKED")
        self.assertFalse(state["accepted"])
        self.assertIsNone(state["effective_review_id"])
        self.assertEqual(len(self.state()["history"]), 3)
        renewed = self.event(replace(self.review, note="Reviewed again explicitly"))
        self.assertEqual(self.state()["candidates"][0]["effective_review_id"], renewed["event_id"])

    def test_revoking_an_older_review_does_not_change_the_latest_decision(self):
        first = self.event()
        second = self.event(
            replace(self.review, reviewer="second local reviewer", decision="reject")
        )
        self.revoke(first["event_id"])
        state = self.state()["candidates"][0]
        self.assertEqual(state["status"], "REJECTED")
        self.assertEqual(state["effective_review_id"], second["event_id"])

    def test_revocation_requires_exact_target_binding_and_original_actor(self):
        event = self.event()
        for changes in (
            {"target": "unknown"},
            {"reviewer": "another actor"},
            {"note": " "},
            {"draft_fingerprint": "old"},
            {"candidate_fingerprint": "old"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.revoke(event["event_id"], **changes)
        self.assertEqual(len(self.state()["history"]), 1)
        revoked = self.revoke(event["event_id"])
        with self.assertRaises(ValueError):
            self.revoke(event["event_id"])
        with self.assertRaises(ValueError):
            self.revoke(revoked["event_id"])

    def test_event_id_retries_are_idempotent_and_conflicting_reuse_fails(self):
        first = self.event(event_id="stable-review")
        self.assertEqual(first, self.event(event_id="stable-review"))
        with self.assertRaisesRegex(ValueError, "different content"):
            self.event(replace(self.review, decision="reject"), event_id="stable-review")
        withdrawn = self.revoke(first["event_id"], event_id="stable-withdrawal")
        self.assertEqual(withdrawn, self.revoke(first["event_id"], event_id="stable-withdrawal"))
        self.assertEqual(len(self.state()["history"]), 2)
        for invalid in ("../escape", "", "a" * 129):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.event(event_id=invalid)

    def test_revision_invalidates_all_drafts_of_the_previous_source_version(self):
        self.event(event_id="old-approval")
        sibling = self.store.save(self.formalizer.formalize_claim(self.source))
        sibling_draft = self.store.load(sibling)
        self.store.review(
            sibling, replace(self.review, draft_fingerprint=sibling_draft.fingerprint)
        )
        revised = self.store.save(self.revision(), previous=self.directory)
        for old in (self.directory, sibling):
            state = self.state(old)
            self.assertFalse(state["source_is_current"])
            self.assertEqual(state["candidates"][0]["status"], "SUPERSEDED")
            self.assertFalse(state["candidates"][0]["accepted"])
        self.assertEqual(self.state(revised)["revision_parent"], self.directory.name)
        self.assertEqual(self.state(revised)["candidates"][0]["status"], "UNREVIEWED")
        with self.assertRaisesRegex(ValueError, "Superseded"):
            self.event()
        # Replaying a committed event can restore its export but never its eligibility.
        self.event(event_id="old-approval")
        self.assertEqual(self.state()["candidates"][0]["status"], "SUPERSEDED")
        self.revoke("old-approval")
        self.assertEqual(self.state()["candidates"][0]["last_review_status"], "REVOKED")

    def test_same_source_version_cannot_be_redefined_or_silently_resurrected(self):
        altered = self.formalizer.formalize_claim(replace(self.source, context=("n > 0",)))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.store.save(altered)
        revised = self.revision()
        with self.assertRaisesRegex(ValueError, "use --revise"):
            self.store.save(revised)
        self.store.save(revised, previous=self.directory)
        with self.assertRaises(ValueError):
            self.store.save(self.draft)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            self.store.save(revised, previous=self.directory)

    def test_review_does_not_transfer_to_another_generation_or_candidate(self):
        another = self.store.save(self.formalizer.formalize_claim(self.source))
        self.event()
        with self.assertRaises(ValueError):
            self.store.review(another, self.review)
        self.assertEqual(self.state(another)["candidates"][0]["status"], "UNREVIEWED")
        revised = self.store.save(self.revision(), previous=self.directory)
        with self.assertRaises(ValueError):
            self.store.review(revised, self.review)

    def test_concurrent_reviews_are_serialized_with_unique_transaction_sequences(self):
        barrier = Barrier(6)

        def write(index):
            barrier.wait(timeout=10)
            return self.event(
                replace(
                    self.review,
                    note=f"Review {index}",
                    decision="accept" if index % 2 else "reject",
                )
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            events = list(pool.map(write, range(6)))
        history = self.state()["history"]
        self.assertEqual(len(history), 6)
        self.assertEqual(len({e["sequence"] for e in events}), 6)
        latest = max(events, key=lambda event: event["sequence"])
        self.assertEqual(self.state()["candidates"][0]["status"], latest["review_status"])
        self.assertEqual(self.state()["candidates"][0]["last_review_id"], latest["event_id"])

    def test_concurrent_same_event_id_commits_once(self):
        barrier = Barrier(4)

        def write(_):
            barrier.wait(timeout=10)
            return self.event(event_id="one-logical-request")

        with ThreadPoolExecutor(max_workers=4) as pool:
            events = list(pool.map(write, range(4)))
        self.assertTrue(all(e == events[0] for e in events))
        self.assertEqual(len(self.state()["history"]), 1)

    def test_concurrent_revisions_allow_exactly_one_successor(self):
        barrier = Barrier(2)
        drafts = [self.revision(), self.revision()]

        def write(draft):
            barrier.wait(timeout=10)
            try:
                return self.store.save(draft, previous=self.directory)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, drafts))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.state()["source_head"]["version"], 2)

    def test_concurrent_review_and_revision_cannot_leave_an_old_acceptance_effective(self):
        barrier = Barrier(2)
        revised = self.revision()

        def review():
            barrier.wait(timeout=10)
            try:
                self.event()
                return "committed-before-revision"
            except ValueError:
                return "blocked-after-revision"

        def revise():
            barrier.wait(timeout=10)
            return self.store.save(revised, previous=self.directory)

        with ThreadPoolExecutor(max_workers=2) as pool:
            review_future, revision_future = pool.submit(review), pool.submit(revise)
            outcome, new_directory = review_future.result(), revision_future.result()
        state = self.state()
        self.assertEqual(state["candidates"][0]["status"], "SUPERSEDED")
        self.assertFalse(state["candidates"][0]["accepted"])
        self.assertEqual(len(state["history"]), int(outcome == "committed-before-revision"))
        self.assertTrue(self.state(new_directory)["source_is_current"])

    def test_concurrent_revocations_with_different_ids_cannot_withdraw_twice(self):
        first = self.event()
        barrier = Barrier(2)

        def withdraw(index):
            barrier.wait(timeout=10)
            try:
                self.revoke(first["event_id"], event_id=f"withdrawal-{index}")
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(withdraw, range(2))), 1)
        self.assertEqual(self.state()["candidates"][0]["status"], "REVOKED")
        self.assertEqual(len(self.state()["history"]), 2)

    def test_multiple_candidate_decisions_remain_bound_to_each_candidate(self):
        examples = strict_json(
            (ROOT / "evals/formalization/fixtures.json").read_text(encoding="utf-8")
        )
        first = examples["examples"][0]["candidates"][0]
        examples["examples"][0]["candidates"].append(
            {
                **first,
                "label": "Renamed bound variable",
                "lean_statement": "∀ m : Nat, m + 0 = m",
                "variables": [{"name": "m", "domain": "Nat", "quantifier": "forall"}],
            }
        )
        fixture = self.root / "two-candidate-fixture.json"
        fixture.write_bytes(canonical(examples))
        draft = StructuredFormalizer(FixtureModelService(fixture)).formalize_claim(self.source)
        directory = self.store.save(draft)
        review = replace(
            self.review,
            draft_fingerprint=draft.fingerprint,
            candidate_fingerprint=draft.candidates[0].fingerprint,
        )
        self.store.review(directory, review)
        self.store.review(
            directory,
            replace(
                review, candidate_fingerprint=draft.candidates[1].fingerprint, decision="reject"
            ),
        )
        states = self.state(directory)["candidates"]
        self.assertEqual([s["status"] for s in states], ["ACCEPTED", "REJECTED"])

    def test_failed_revision_transaction_does_not_invalidate_existing_review(self):
        self.event()
        with sqlite3.connect(self.store.ledger.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_head BEFORE UPDATE ON heads "
                "BEGIN SELECT RAISE(ABORT, 'injected interruption'); END"
            )
        before = set(self.store.base.iterdir())
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save(self.revision(), previous=self.directory)
        self.assertEqual(self.state()["candidates"][0]["status"], "ACCEPTED")
        orphan = next(p for p in set(self.store.base.iterdir()) - before if p.is_dir())
        self.assertFalse(self.state(orphan)["registered"])
        with sqlite3.connect(self.store.ledger.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
            connection.execute("DROP TRIGGER fail_head")
        self.store.register(orphan, previous=self.directory)
        self.assertEqual(self.state()["candidates"][0]["status"], "SUPERSEDED")

    def test_export_failure_after_commit_can_be_recovered_by_idempotent_retry(self):
        with patch.object(
            self.store, "_export_event", side_effect=OSError("injected export failure")
        ):
            with self.assertRaises(OSError):
                self.event(event_id="committed-before-export")
        self.assertEqual(self.state()["candidates"][0]["status"], "ACCEPTED")
        original = self.state()["history"][0]
        recovered = self.event(event_id="committed-before-export")
        self.assertEqual(original, recovered)
        self.assertEqual(len(self.state()["history"]), 1)

    def test_process_killed_during_transaction_cannot_commit_a_partial_state(self):
        self.event()
        script = """
import os, signal, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE heads SET version=99')
connection.execute('INSERT INTO events(event_id, draft_id, payload) VALUES (?, ?, ?)',
                   ('uncommitted-event', sys.argv[2], 'x' * 20000))
os.kill(os.getpid(), signal.SIGKILL)
"""
        killed = subprocess.run(
            [sys.executable, "-c", script, str(self.store.ledger.path), self.directory.name],
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
        self.store = IntakeStore(self.root)
        state = self.state()
        self.assertEqual(state["source_head"]["version"], 1)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["candidates"][0]["status"], "ACCEPTED")

    def test_json_exports_cannot_forge_effective_state_and_can_be_regenerated(self):
        event = self.event(event_id="recoverable")
        path = self.directory / "reviews/recoverable.json"
        path.write_text('{"review_status":"PROVED"}')
        (path.parent / "forged.json").write_text('{"review_status":"ACCEPTED"}')
        self.assertEqual(self.state()["history"], [event])
        path.unlink()
        self.assertEqual(self.event(event_id="recoverable"), event)
        self.assertTrue(path.is_file())

    def test_legacy_bundle_is_inert_until_registered_and_old_reviews_stay_historical(self):
        legacy_root = self.root / "legacy-project"
        legacy = legacy_root / "artifacts/formalization/legacy-bundle"
        shutil.copytree(self.directory, legacy)
        (legacy / "reviews").mkdir()
        (legacy / "reviews/old.json").write_text('{"review_status":"ACCEPTED"}')
        store = IntakeStore(legacy_root)
        self.assertEqual(
            store.report(legacy)["review_state"]["candidates"][0]["status"], "UNREGISTERED"
        )
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            store.review(legacy, self.review)
        store.register(legacy)
        store.register(legacy)  # Exact registration replay is harmless.
        state = store.report(legacy)["review_state"]
        self.assertEqual(state["candidates"][0]["status"], "UNREVIEWED")
        self.assertEqual(state["history"], [])
        store.review(legacy, self.review)
        self.assertEqual(len(store.report(legacy)["review_state"]["history"]), 1)

    def test_recomputed_bundle_hashes_cannot_replace_the_registered_draft(self):
        changed = self.formalizer.formalize_claim(self.source)
        raw = canonical(changed.to_dict()) + b"\n"
        (self.directory / "draft.json").write_bytes(raw)
        manifest = strict_json((self.directory / "manifest.json").read_text())
        manifest["files"]["draft.json"] = hashlib.sha256(raw).hexdigest()
        (self.directory / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(self.store.load(self.directory), changed)
        with self.assertRaisesRegex(ValueError, "registered version"):
            self.store.report(self.directory)
        with self.assertRaises(ValueError):
            self.event()

    def test_modified_event_payload_is_revalidated_before_reporting(self):
        self.event()
        with sqlite3.connect(self.store.ledger.path) as connection:
            raw = connection.execute("SELECT payload FROM events").fetchone()[0]
            payload = strict_json(raw)
            payload["evidence_level"] = "E4_ALIGNED_AND_VERIFIED"
            connection.execute("UPDATE events SET payload=?", (json.dumps(payload),))
        with self.assertRaisesRegex(ValueError, "event changed"):
            self.store.report(self.directory)

    def test_symlinked_manifest_or_database_is_rejected(self):
        manifest = self.directory / "manifest.json"
        moved = self.directory / "manifest-copy.json"
        manifest.rename(moved)
        manifest.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.report(self.directory)
        manifest.unlink()
        moved.rename(manifest)
        database = self.store.ledger.path
        copied = database.with_suffix(".copy")
        database.rename(copied)
        database.symlink_to(copied)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.report(self.directory)

    def test_unknown_database_schema_fails_without_resetting_it(self):
        with sqlite3.connect(self.store.ledger.path) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "database version"):
            self.event()
        with sqlite3.connect(self.store.ledger.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)


if __name__ == "__main__":
    unittest.main()
