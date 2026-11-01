"""Source retrieval and studio bindings. Fixtures are not Lean proof evidence."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mra.domain import canonical, digest
from mra.integrity import ContentSnapshot, file_record
from mra.premise_search import PremiseIndex, excerpts, mask_noncode, query_terms
from mra.research_studio import StudioStore
from mra import studio_premises
import test_research_studio as studio_fixtures
from test_http_api import copy_web_inputs, request, running_server

SOURCE = """/-! A fixture file; no compiled declarations or proof evidence.
/- theorem nested_fake : False := by sorry -/
-/
namespace Fixture
def message := "
theorem string_fake : False := by sorry
"
def rawMessage := r#"
theorem raw_fake : False := by sorry
"#
/-- Uniform continuity of a continuous map on a compact space. -/
@[simp] protected lemma uniformContinuous_fixture : True := by trivial
lemma prime_fixture : True := by trivial
end Fixture
"""


def install_fixture_sources(root):
    base = root / ".runtime/mathlib"
    (base / "Mathlib/Topology").mkdir(parents=True)
    source = base / "Mathlib/Topology/Fixture.lean"
    source.write_text(SOURCE, encoding="utf-8")
    (base / "LICENSE").write_text("Fixture license metadata, not the real Mathlib.\n")
    snapshot = ContentSnapshot.load_runtime(root)
    snapshot.data["files"]["lean_workspace/environment.json"] = file_record(
        root / "lean_workspace/environment.json"
    )
    snapshot.data["trees"][".runtime/mathlib"] = {
        "LICENSE": file_record(base / "LICENSE"),
        "Mathlib/Topology/Fixture.lean": file_record(source),
    }
    snapshot.write(replace=True)  # Isolated, deliberately non-executable test runtime only.
    return source


class PremiseSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.source = install_fixture_sources(self.root)
        self.index = PremiseIndex(self.root)
        self.index.build()

    def hit(self):
        return next(
            x
            for x in self.index.search({"query": "一致连续", "limit": 8})["hits"]
            if x["written_name"] == "uniformContinuous_fixture"
        )

    def study(self):
        self.store = StudioStore(self.root, service_factory=studio_fixtures.Fixture)
        row = self.store.create({"request_id": "study", "source": studio_fixtures.source()})
        self.key = row["study_id"]
        return self.store.scaffold(self.key, self.mutation(row))

    @staticmethod
    def mutation(row, **extra):
        return {
            "request_id": "request-" + str(row["revision"]),
            "expected_revision": row["revision"],
            **extra,
        }

    def attach(self, row):
        return studio_premises.attach(
            self.store,
            self.key,
            self.mutation(row, node_id="root", item_id=self.hit()["item_id"]),
        )

    def configure_model_fixture(self):
        directory = self.root / "config"
        directory.mkdir(exist_ok=True)
        (directory / "model.local.json").write_text(
            json.dumps(
                {
                    "endpoint": "http://127.0.0.1:9999/v1/chat/completions",
                    "model": "fixture",
                    "revision": "fixture-v1",
                    "api_key_env": "MRA_STUDIO_TEST_KEY",
                    "token_field": "max_tokens",
                }
            )
        )

    def test_comments_nested_comments_strings_and_raw_strings_are_not_declarations(self):
        masked = mask_noncode(SOURCE)
        self.assertEqual(len(masked), len(SOURCE))
        self.assertEqual(masked.count("\n"), SOURCE.count("\n"))
        items = list(excerpts("Mathlib/Fixture.lean", SOURCE, "a" * 64, "b" * 64, "c" * 40))
        names = [x["written_name"] for x in items]
        self.assertIn("uniformContinuous_fixture", names)
        self.assertFalse(any("fake" in x for x in names))
        item = next(x for x in items if x["written_name"] == "uniformContinuous_fixture")
        self.assertEqual(
            SOURCE.splitlines()[item["declaration_line"] - 1],
            "@[simp] protected lemma uniformContinuous_fixture : True := by trivial",
        )

    def test_live_source_search_is_versioned_and_never_publishes_mathematical_evidence(self):
        result = self.index.search({"query": "一致连续", "limit": 8})
        self.assertEqual(result["query_terms"], ["uniform", "continuous"])
        self.assertEqual((result["math_verdict"], result["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(result["new_lean_checks"], 0)
        hit = self.hit()
        self.assertEqual(hit["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(self.index.lookup(hit["item_id"]), hit)
        self.assertIn(hit["mathlib_commit"], hit["source_url"])
        self.assertFalse((self.root / ".runtime/lean-4.35.0-linux").exists())

    def test_query_syntax_cannot_be_fts_or_sql_and_budgets_are_strict(self):
        self.assertEqual(
            query_terms("UniformContinuous.norm_inner"), ["uniform", "continuous", "norm", "inner"]
        )
        self.assertEqual(
            self.index.search({"query": "'; DROP TABLE entries; --", "limit": 1})["hits"], []
        )
        self.assertTrue(self.index.status()["ready"])
        for data in (
            {"query": "", "limit": 1},
            {"query": "prime", "limit": True},
            {"query": "prime", "limit": 13},
            {"query": " ".join("term" + str(i) for i in range(14)), "limit": 1},
        ):
            with self.assertRaises(ValueError):
                self.index.search(data)
        with self.assertRaises(ValueError):
            self.index.lookup("../outside")

    def test_changed_source_cannot_be_read_or_attached_and_restoration_is_only_source_validity(
        self,
    ):
        row = self.study()
        item_id = self.hit()["item_id"]
        self.source.write_text(SOURCE.replace("True", "False"))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.index.lookup(item_id)
        with self.assertRaises(ValueError):
            studio_premises.attach(
                self.store, self.key, self.mutation(row, node_id="root", item_id=item_id)
            )
        self.assertEqual(self.store.report(self.key)["math_verdict"], "UNKNOWN")
        self.source.write_text(SOURCE)
        self.assertEqual(self.index.lookup(item_id)["math_verdict"], "UNKNOWN")

    def test_exact_written_declaration_name_ranks_before_neighboring_mentions(self):
        result = self.index.search({"query": "uniformContinuous_fixture", "limit": 8})
        self.assertEqual(result["hits"][0]["written_name"], "uniformContinuous_fixture")

    def test_corrupted_index_payload_is_rejected_against_real_source(self):
        hit = self.hit()
        hit["excerpt"] = "Forged mathematical statement"
        hit["item_id"] = "premise-" + digest({k: v for k, v in hit.items() if k != "item_id"})
        with sqlite3.connect(self.index.path) as db:
            db.execute(
                "INSERT INTO entries VALUES (?,?)", (hit["item_id"], canonical(hit).decode())
            )
        with self.assertRaisesRegex(ValueError, "real source"):
            self.index.lookup(hit["item_id"])

    def test_swapped_valid_entry_cannot_impersonate_the_requested_identity(self):
        first = self.hit()
        other = next(
            x
            for x in self.index.search({"query": "prime", "limit": 8})["hits"]
            if x["written_name"] == "prime_fixture"
        )
        with sqlite3.connect(self.index.path) as db:
            db.execute(
                "UPDATE entries SET body=? WHERE id=?",
                (canonical(other).decode(), first["item_id"]),
            )
        with self.assertRaisesRegex(ValueError, "row identity"):
            self.index.lookup(first["item_id"])

    def test_failed_rebuild_keeps_previous_complete_index_and_cleans_pending(self):
        previous = self.index.path.read_bytes()
        with patch.object(
            self.index, "read_source", side_effect=OSError("Controlled build failure")
        ):
            with self.assertRaises(OSError):
                self.index.build()
        self.assertEqual(self.index.path.read_bytes(), previous)
        self.assertFalse(list(self.index.base.glob(".pending-*")))
        self.assertTrue(self.index.status()["ready"])

    def test_source_version_change_refuses_old_index_without_rebuilding(self):
        config = self.root / "lean_workspace/environment.json"
        data = json.loads(config.read_text())
        data["mathlib_commit"] = "a" * 40
        config.write_text(json.dumps(data))
        self.assertFalse(self.index.status()["ready"])
        with self.assertRaises(ValueError):
            self.hit()

    def test_reference_has_step_identity_idempotency_and_stale_revision_protection(self):
        row = self.study()
        data = self.mutation(row, node_id="root", item_id=self.hit()["item_id"])
        row = studio_premises.attach(self.store, self.key, data)
        self.assertEqual(len(row["citations"]), 1)
        self.assertEqual(
            studio_premises.attach(self.store, self.key, data)["revision"], row["revision"]
        )
        self.assertEqual(row["citation_states"][0]["status"], "AVAILABLE")
        with self.assertRaises(ValueError):
            studio_premises.attach(self.store, self.key, {**data, "request_id": "different"})
        self.assertEqual(row["math_verdict"], "UNKNOWN")
        self.assertEqual(row["transfers"], [])

    def test_current_references_reach_model_with_full_original_context_and_withdrawal_removes_them(
        self,
    ):
        row = self.attach(self.study())
        self.configure_model_fixture()
        row = self.store.generate(
            self.key, self.mutation(row, operation="step", node_id="root", backend="none")
        )
        context = json.loads(studio_fixtures.Fixture.seen[-1].input_json)
        self.assertEqual(context["source"], studio_fixtures.source())
        self.assertEqual(context["retrieved_premises"]["available"][0], row["citations"][0]["item"])
        row = studio_premises.withdraw(
            self.store, self.key, self.mutation(row, citation_id=row["citations"][0]["citation_id"])
        )
        self.assertEqual(
            studio_premises.context(self.root, self.store.read(self.key), "root")["available"], []
        )
        self.assertEqual(row["citation_states"][0]["status"], "WITHDRAWN")
        self.assertEqual(row["evidence_level"], "E0")

    def test_replanning_preserves_history_without_using_previous_plan_references(self):
        row = self.attach(self.study())
        plan = deepcopy(row["plan"])
        plan["steps"][0]["approach"] += " Try another route."
        row = self.store.plan(self.key, self.mutation(row, plan=plan))
        self.assertEqual(row["citation_states"][0]["status"], "PREVIOUS_PLAN")
        self.assertEqual(
            studio_premises.context(self.root, self.store.read(self.key), "root")["available"], []
        )

    def test_unavailable_source_and_damaged_index_do_not_hide_original_research(self):
        self.attach(self.study())
        self.source.write_text(SOURCE + "\n")
        row = self.store.report(self.key)
        self.assertEqual(row["citation_states"][0]["status"], "UNAVAILABLE")
        self.assertEqual(
            studio_premises.context(self.root, self.store.read(self.key), "root")["available"], []
        )
        self.assertEqual(row["source"], studio_fixtures.source())
        self.index.path.write_bytes(b"corrupt SQLite")
        self.assertEqual(self.store.report(self.key)["citation_states"][0]["status"], "UNAVAILABLE")

    def test_http_search_attachment_and_withdrawal_share_existing_guards(self):
        row = self.study()
        with running_server(self.root) as server:
            path = "/v1/studio/" + self.key
            status, catalog, _ = request(server, "GET", "/v1/studio/catalog")
            self.assertEqual(status, 200)
            self.assertTrue(catalog["premise_index"]["ready"])
            body = {"node_id": "root", "query": "一致连续", "limit": 8}
            self.assertEqual(
                request(
                    server, "POST", path + "/premise-search", body, headers={"X-MRA-CSRF": "bad"}
                )[0],
                403,
            )
            status, results, _ = request(server, "POST", path + "/premise-search", body)
            self.assertEqual(status, 200)
            body = self.mutation(row, node_id="root", item_id=results["hits"][0]["item_id"])
            status, row, _ = request(server, "POST", path + "/attach-premise", body)
            self.assertEqual(status, 200)
            status, row, _ = request(
                server,
                "POST",
                path + "/withdraw-premise",
                self.mutation(row, citation_id=row["citations"][0]["citation_id"]),
            )
            self.assertEqual(status, 200)
            self.assertTrue(row["citations"][0]["withdrawn"])
            self.assertEqual(row["math_verdict"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
