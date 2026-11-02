"""Real source extraction and durable correspondence contracts; no invented mathematical evidence."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from mra.document_intake import DocumentStore, DocumentPump
from mra.document_obligations import ObligationStore, REVIEW_ITEMS, complete_statement
from mra.research_flow import atomic
from mra.research_flow import lease
from mra.tasks import WorkerBusy
from document_fixtures import source_request
from test_http_api import ROOT, copy_web_inputs, request, running_server


def content(**changes):
    return {
        "title": "Addition identity",
        "kind": "claim",
        "sources": [{"block_id": "block-0001", "role": "statement"}],
        "objects": "n : Nat",
        "definitions": "Addition is natural-number addition.",
        "quantifiers": "For every n : Nat.",
        "assumptions": [],
        "conclusion": "n + 0 = n",
        "references": [],
        "depends_on": [],
        "questions": [],
        **changes,
    }


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=ROOT / "artifacts", prefix="obligation-contract-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.docs = DocumentStore(self.root)
        doc = self.docs.create(source_request())
        self.doc = doc["document_id"]
        pump = DocumentPump(self.docs)
        with pump.owner() as fd:
            pump.execute(self.doc, fd)
        self.store = ObligationStore(self.root, documents=self.docs)
        self.create_data = {
            "request_id": "dossier-create",
            "document_id": self.doc,
            "expected_document_revision": 0,
        }
        self.report = self.store.create(self.create_data)
        self.key = self.report["dossier_id"]
        # The HTML fixture starts with a theorem; derive IDs rather than assume the indexer ordering.
        self.block = next(b["id"] for b in self.report["source"]["blocks"] if b["kind"] == "claim")
        self.other = next(b["id"] for b in self.report["source"]["blocks"] if b["id"] != self.block)

    def data(self, **changes):
        return {
            "request_id": "ob-test-" + uuid.uuid4().hex,
            "expected_revision": self.store.read(self.key)[0]["revision"],
            **changes,
        }

    def save(self, oid="main", **changes):
        value = content(**{"sources": [{"block_id": self.block, "role": "statement"}], **changes})
        self.report = self.store.save(self.key, self.data(obligation_id=oid, content=value))
        return self.report

    def extraction(self, confirmed=True):
        self.report = self.store.extraction_review(
            self.key,
            self.data(
                block_ids=[self.block, self.other],
                reviewer="Local source test author",
                note="Owned fixture extraction compared with the actual uploaded HTML",
                confirmed=confirmed,
            ),
        )

    def review(self, oid="main", decision="accept", **changes):
        preview = self.store.review_preview(self.key, {"obligation_id": oid})
        value = self.data(
            obligation_id=oid,
            preview_fingerprint=preview["preview_fingerprint"],
            decision=decision,
            reviewer="Local semantic test author",
            note="Protocol-only review of the complete owned fixture",
            confirmations=list(REVIEW_ITEMS),
        )
        self.report = self.store.review(self.key, {**value, **changes})
        return self.report

    def accept(self, oid="main"):
        self.extraction()
        return self.review(oid)

    def research(self, oid="main"):
        self.report = self.store.create_workspace(
            self.key, self.data(obligation_id=oid, area="数论")
        )
        return self.report["workspaces"][-1]

    def test_fixed_source_multifragment_and_one_fragment_multiple_obligations(self):
        refs = [
            {"block_id": self.block, "role": "statement"},
            {"block_id": self.other, "role": "definition"},
        ]
        self.save(sources=refs)
        self.save("second", conclusion="A separate obligation from the same original theorem.")
        loaded = ObligationStore(self.root).report(self.key)
        self.assertEqual(loaded["obligations"]["main"]["content"]["sources"], refs)
        self.assertEqual(
            loaded["obligations"]["second"]["content"]["sources"][0]["block_id"], self.block
        )
        self.assertEqual(
            loaded["source"]["inputs"]["sources"][0]["sha256"],
            self.docs.read(self.doc)[1]["sources"][0]["sha256"],
        )
        self.assertFalse(loaded["whole_article_verified"])
        self.assertEqual((loaded["math_verdict"], loaded["evidence_level"]), ("UNKNOWN", "E0"))

    def test_extraction_and_semantics_separate_and_no_automatic_proof(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "extraction separately"):
            self.review()
        self.accept()
        state = self.report["obligation_states"]["main"]
        self.assertEqual(state["semantic_state"], "ACCEPTED")
        self.assertEqual((state["math_verdict"], state["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(self.report["reviews"][-1]["authentication"], "local-assertion-only")

    def test_open_questions_and_partial_checklist_block_acceptance_and_research(self):
        self.save(questions=["Is the variable natural or integer?"])
        self.extraction()
        with self.assertRaisesRegex(ValueError, "clarification"):
            self.review()
        self.save(questions=[])
        with self.assertRaisesRegex(ValueError, "Confirm sources"):
            self.review(confirmations=["sources", "conclusion"])
        with self.assertRaisesRegex(ValueError, "Confirm the complete"):
            self.research()
        with self.assertRaises(ValueError):
            self.review(reviewer=" ")

    def test_field_changes_and_restoration_never_revive_old_semantics_or_workspace(self):
        self.save()
        self.accept()
        workspace = self.research()
        original = deepcopy(self.report["obligations"]["main"]["content"])
        for field, value in (
            ("quantifiers", "There exists n."),
            ("assumptions", ["n > 0"]),
            ("definitions", "Different addition"),
        ):
            with self.subTest(field=field):
                self.store.save(
                    self.key, self.data(obligation_id="main", content={**original, field: value})
                )
                self.report = self.store.save(
                    self.key, self.data(obligation_id="main", content=original)
                )
                self.assertEqual(
                    self.report["obligation_states"]["main"]["semantic_state"], "NEEDS_REVIEW"
                )
                self.assertEqual(self.report["workspace_states"][0]["status"], "STALE")
                self.assertEqual(self.report["workspaces"][0]["study_id"], workspace["study_id"])
        self.assertEqual(len(self.report["versions"]), 7)

    def test_withdrawn_extraction_and_reconfirmation_do_not_restore_old_semantics(self):
        self.save()
        self.accept()
        self.research()
        self.extraction(False)
        self.assertEqual(self.report["obligation_states"]["main"]["semantic_state"], "NEEDS_REVIEW")
        self.assertEqual(self.report["workspace_states"][0]["status"], "STALE")
        self.extraction(True)
        self.assertEqual(self.report["obligation_states"]["main"]["semantic_state"], "NEEDS_REVIEW")
        self.review()
        self.assertEqual(self.report["workspace_states"][0]["status"], "STALE")

    def test_dependency_changes_invalidate_transitive_review_and_cycles_never_commit(self):
        self.save("lemma")
        self.extraction()
        self.review("lemma")
        self.save(depends_on=["lemma"])
        self.review()
        self.research()
        before = self.store.read(self.key)[0]
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.save("lemma", depends_on=["main"])
        self.assertEqual(self.store.read(self.key)[0], before)
        with self.assertRaises(ValueError):
            self.save(depends_on=["missing"])
        self.save("lemma", quantifiers="Different quantifier")
        self.assertEqual(self.report["obligation_states"]["main"]["semantic_state"], "NEEDS_REVIEW")
        self.assertEqual(self.report["workspace_states"][0]["status"], "STALE")

    def test_rejection_and_revocation_cannot_revive_an_earlier_acceptance(self):
        self.save()
        self.accept()
        self.research()
        with self.assertRaisesRegex(ValueError, "named reviewer"):
            self.review(decision="revoke", reviewer="Different person")
        self.review(decision="revoke")
        self.assertEqual(self.report["workspace_states"][0]["status"], "STALE")
        self.review()
        self.review(decision="reject", confirmations=[])
        self.assertEqual(self.report["obligation_states"]["main"]["semantic_state"], "NEEDS_REVIEW")

    def test_document_new_revision_keeps_history_and_old_retries_never_restore_current(self):
        self.save()
        self.accept()
        self.research()
        old_snapshot = deepcopy(self.report["source"])
        b = self.docs.draft(self.doc)["blocks"][0]
        self.docs.review(
            self.doc,
            {
                "request_id": "new-document-revision",
                "expected_revision": 0,
                "block_id": b["id"],
                "text": b["text"] + " Revision.",
                "kind": b["kind"],
                "note": "Changed source text",
                "reviewed": True,
            },
        )
        old = self.store.create(self.create_data)
        self.assertEqual(old["source_state"], "SUPERSEDED")
        self.assertEqual(old["source"], old_snapshot)
        self.assertEqual(old["workspace_states"][0]["status"], "STALE")
        with self.assertRaisesRegex(ValueError, "Source revision"):
            self.save()
        new = self.store.create(
            {**self.create_data, "request_id": "new-dossier", "expected_document_revision": 1}
        )
        self.assertEqual(new["reviews"], [])
        self.assertEqual(new["obligations"], {})

    def test_source_and_fixed_snapshot_tampering_suppress_or_reject_identity(self):
        self.save()
        self.accept()
        source_file = self.docs.directory(self.doc) / "source-00.txt"
        source_file.write_bytes(source_file.read_bytes() + b"changed")
        loaded = self.store.report(self.key)
        self.assertEqual(loaded["source_state"], "SOURCE_UNAVAILABLE")
        self.assertEqual(loaded["obligation_states"]["main"]["math_verdict"], "UNKNOWN")
        row, source = self.store.read(self.key)
        source["blocks"][0]["text"] = "Another fixed source"
        atomic(self.store.directory(self.key) / "source.json", source)
        with self.assertRaisesRegex(ValueError, "snapshot identity"):
            self.store.report(self.key)

    def test_same_revision_changed_live_review_content_is_detected(self):
        row, _ = self.docs.read(self.doc)
        row["reviews"][self.block] = {
            "text": "Replaced without a version increment",
            "kind": "claim",
            "note": "",
            "reviewed": True,
        }
        atomic(self.docs.directory(self.doc) / "record.json", row)
        self.assertEqual(self.store.report(self.key)["source_state"], "SOURCE_CHANGED")

    def test_request_idempotency_conflict_stale_revision_and_stale_preview(self):
        data = self.data(
            obligation_id="main",
            content=content(sources=[{"block_id": self.block, "role": "statement"}]),
        )
        first = self.store.save(self.key, data)
        self.assertEqual(self.store.save(self.key, data), first)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.save(
                self.key, {**data, "content": {**data["content"], "conclusion": "Other"}}
            )
        with self.assertRaisesRegex(ValueError, "dossier changed"):
            self.store.save(self.key, {**data, "request_id": "new-request-with-stale-revision"})
        p = self.store.review_preview(self.key, {"obligation_id": "main"})
        self.extraction()
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.review(preview_fingerprint=p["preview_fingerprint"])

    def test_research_bridge_keeps_every_field_and_never_treats_claim_dependencies_as_hypotheses(
        self,
    ):
        self.save("lemma", conclusion="Auxiliary statement to prove")
        self.extraction()
        self.review("lemma")
        self.save(depends_on=["lemma"], assumptions=["Original domain condition"])
        self.review()
        workspace = self.research()
        study = self.store.studio.read(workspace["study_id"])
        self.assertEqual(
            study["source"]["question"],
            complete_statement(self.report["obligations"]["main"]["content"]),
        )
        self.assertEqual(study["source"]["assumptions"], "Original domain condition")
        self.assertIn("Auxiliary statement to prove", study["source"]["definitions"])
        self.assertNotIn("Auxiliary statement", study["source"]["assumptions"])
        self.assertEqual(
            study["source"]["question"], study["plans"][-1]["plan"]["steps"][0]["statement"]
        )
        self.assertEqual(study["transfers"], [])
        with self.assertRaisesRegex(ValueError, "actually transferred"):
            self.store.evidence_preview(
                self.key,
                {
                    "workspace_id": workspace["workspace_id"],
                    "graph_id": "unrelated",
                    "node_id": "root",
                },
            )

    def test_bounds_unknown_source_roles_duplicate_refs_and_definition_research(self):
        for change in (
            {"sources": []},
            {"sources": [{"block_id": "missing", "role": "statement"}]},
            {"sources": [{"block_id": self.block, "role": "proof"}]},
            {"objects": "x" * 4001},
            {"extra": "not allowed"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save(**change)
        self.save(kind="definition")
        self.accept()
        with self.assertRaisesRegex(ValueError, "definition is context"):
            self.research()

    def test_storage_redirection_and_changed_envelope_rejected(self):
        target = self.root / "outside.json"
        target.write_bytes((self.store.directory(self.key) / "source.json").read_bytes())
        (self.store.directory(self.key) / "source.json").unlink()
        (self.store.directory(self.key) / "source.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.read(self.key)

    def test_source_edit_lock_prevents_concurrent_semantic_commit(self):
        before = self.store.read(self.key)[0]
        with lease(self.docs.directory(self.doc)), self.assertRaises(WorkerBusy):
            self.save()
        self.assertEqual(self.store.read(self.key)[0], before)

    def test_historical_reference_never_grants_current_evidence_or_reads_provider_on_get(self):
        self.save()
        self.accept()
        workspace = self.research()
        row, _ = self.store.read(self.key)
        # Deliberately inert invented history. It must never count as proof.
        row["links"].append(
            {
                "link_id": "inert-history",
                "workspace_id": workspace["workspace_id"],
                "obligation_id": "main",
                "binding_fingerprint": workspace["binding_fingerprint"],
                "graph_id": "never-transferred",
                "node_id": "root",
                "identity": {},
            }
        )
        atomic(self.store.directory(self.key) / "record.json", row)
        with patch.object(
            self.store,
            "_evidence_preview",
            side_effect=AssertionError("GET must not start evidence reads"),
        ):
            report = self.store.report(self.key)
        self.assertEqual(report["link_states"][0]["status"], "NOT_RECHECKED")
        self.assertFalse(report["evidence_rechecked"])
        self.assertEqual(report["obligation_states"]["main"]["math_verdict"], "UNKNOWN")
        checked = self.store.recheck(self.key, {"obligation_id": "main"})
        self.assertEqual(checked["link_states"][0]["status"], "UNAVAILABLE")
        self.assertEqual(checked["obligation_states"]["main"]["math_verdict"], "UNKNOWN")
        self.assertTrue(checked["evidence_rechecked"])

    def test_no_change_save_preserves_acceptance_but_corrupt_version_history_fails(self):
        self.save()
        self.accept()
        self.research()
        self.save()
        self.assertEqual(self.report["obligations"]["main"]["version"], 1)
        self.assertEqual(self.report["workspace_states"][0]["status"], "CURRENT")
        row, _ = self.store.read(self.key)
        row["versions"][0]["version"] = 7
        row["obligations"]["main"]["version"] = 7
        atomic(self.store.directory(self.key) / "record.json", row)
        with self.assertRaisesRegex(ValueError, "version identity"):
            self.store.report(self.key)


class ObligationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="obligation-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        context = running_server(self.root)
        self.server = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        doc = self.server.documents.create(source_request())
        pump = DocumentPump(self.server.documents)
        with pump.owner() as fd:
            pump.execute(doc["document_id"], fd)
        code, self.row, _ = request(
            self.server,
            "POST",
            "/v1/obligations",
            {
                "request_id": "http-dossier",
                "document_id": doc["document_id"],
                "expected_document_revision": 0,
            },
        )
        self.assertEqual(code, 201, self.row)
        self.key = self.row["dossier_id"]

    def test_actual_http_save_preview_review_guards_and_no_automatic_execution(self):
        block = next(x["id"] for x in self.row["source"]["blocks"] if x["kind"] == "claim")
        path = "/v1/obligations/" + self.key
        data = {
            "request_id": "http-save",
            "expected_revision": 0,
            "obligation_id": "main",
            "content": content(sources=[{"block_id": block, "role": "statement"}]),
        }
        for headers in ({"Origin": "https://evil.example"}, {"X-MRA-CSRF": "incorrect"}):
            self.assertEqual(
                request(self.server, "POST", path + "/save", data, headers=headers)[0], 403
            )
        code, row, _ = request(self.server, "POST", path + "/save", data)
        self.assertEqual(code, 200, row)
        self.assertEqual(row["obligation_states"]["main"]["math_verdict"], "UNKNOWN")
        code, preview, _ = request(
            self.server, "POST", path + "/review-preview", {"obligation_id": "main"}
        )
        self.assertEqual(code, 200, preview)
        self.assertEqual(preview["complete_statement"], complete_statement(data["content"]))
        self.assertEqual(request(self.server, "GET", path + "?unknown=1")[0], 400)
        self.assertNotEqual(
            request(self.server, "GET", "/v1/obligations/../../source.json")[0], 200
        )
        self.assertEqual(
            request(self.server, "POST", path + "/save", {**data, "unknown": True})[0], 400
        )
        self.assertEqual(
            request(
                self.server, "POST", path + "/save", raw=b'{"request_id":"one","request_id":"two"}'
            )[0],
            400,
        )
        self.assertEqual(self.server.obligations.read(self.key)[0]["revision"], 1)
        self.assertEqual(row["workspaces"], [])

    def test_assets_catalog_and_fixed_snapshot_available_with_security_headers(self):
        for route in ("/obligations", "/obligations.css", "/obligations.js"):
            code, _, headers = request(self.server, "GET", route)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        code, value, _ = request(self.server, "GET", "/v1/obligations/catalog")
        self.assertEqual(code, 200)
        self.assertFalse(value["automatic_semantic_generation"])
        self.assertEqual(value["model_calls"], 0)
        code, value, _ = request(self.server, "GET", "/v1/obligations/" + self.key)
        self.assertEqual(code, 200)
        self.assertEqual(value["source_state"], "CURRENT")
        self.assertFalse(value["whole_article_verified"])


if __name__ == "__main__":
    unittest.main()
