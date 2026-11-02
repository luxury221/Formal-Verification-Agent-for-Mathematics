"""Source-backed draft contracts; fixtures and review assertions are not proofs."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from document_fixtures import source_request
from test_http_api import ROOT, copy_web_inputs, request, running_server
from mra import obligation_drafts as drafts
from mra.document_intake import DocumentPump, DocumentStore
from mra.document_obligations import LIMITS, ObligationStore, REVIEW_ITEMS, complete_statement
from mra.research_flow import atomic, lease
from mra.tasks import WorkerBusy

TEX = r"""\documentclass{article}
\newtheorem{theorem}{Theorem}
\newtheorem{definition}{Definition}
\begin{document}
\section{Addition}
Let $n$ be a natural number. This context still needs review.
\begin{definition}\label{def:zero}
Zero is the additive identity of natural-number addition.
\end{definition}
\begin{theorem}\label{thm:first}
For every natural number $n$, if $n=0$ then $n+0=n$; see Definition~\ref{def:zero}.
\end{theorem}
\begin{proof}Use the definition.\end{proof}
\begin{theorem}\label{thm:second}
For all natural numbers $m$, $m+0=m$; see Theorem~\ref{thm:first} and~\ref{missing}.
\end{theorem}
\end{document}
"""


def block(key, kind="claim", text="For every n, if n = 0 then n + 0 = n.", **changes):
    return {"id": key, "kind": kind, "text": text, "source": "paper.tex", **changes}


class LiteralDraftTests(unittest.TestCase):
    def test_full_source_is_literal_without_inferred_semantics_or_dependencies(self):
        text = "设空间 X。\r\nFor every x in X, if x = 0 then f(x) = 0.\n且反过来也成立。"
        original = {"blocks": [block("claim", text=text)]}
        snapshot = deepcopy(original)
        value = drafts.candidate(original, "claim")
        content = value["content"]
        self.assertEqual(content["conclusion"], text)
        self.assertEqual(content["sources"], [{"block_id": "claim", "role": "statement"}])
        for name in ("objects", "definitions", "quantifiers"):
            self.assertEqual(content[name], "")
        self.assertEqual(content["assumptions"], [])
        self.assertEqual(content["depends_on"], [])
        self.assertGreaterEqual(len(content["questions"]), 4)
        self.assertEqual((value["math_verdict"], value["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertEqual(original, snapshot)
        for cue in value["cues"]["items"]:
            a, b = cue["span"]
            self.assertEqual(text[a:b], cue["quote"])
        self.assertEqual(value["cues"]["basis"], "FIXED_FRAGMENT_TEXT")

    def test_cue_limits_report_unexpanded_lines_without_truncating_quotes(self):
        text = "\n".join(["Assume " + "x" * 2000] + ["For all n, n = n."] * 25)
        cues = drafts.cues(text)
        self.assertEqual(cues["matched_lines"], 26)
        self.assertEqual(len(cues["items"]), 24)
        self.assertEqual(cues["not_expanded"], 2)
        for cue in cues["items"]:
            self.assertEqual(text[slice(*cue["span"])], cue["quote"])

    def test_only_unique_readable_explicit_references_are_attached(self):
        source = {
            "blocks": [
                block("definition", "definition", "Zero is the additive identity.", labels=["d"]),
                block("lemma", labels=["lemma"]),
                block("amb1", "definition", labels=["dup"]),
                block("amb2", "definition", labels=["dup"]),
                block("bad", "unreadable", labels=["bad"]),
                block(
                    "main",
                    labels=["self"],
                    reference_labels=["d", "d", "lemma", "dup", "missing", "self", "bad"],
                ),
            ]
        }
        value = drafts.candidate(source, "main")
        self.assertEqual(
            value["content"]["sources"],
            [
                {"block_id": "main", "role": "statement"},
                {"block_id": "definition", "role": "definition"},
                {"block_id": "lemma", "role": "context"},
            ],
        )
        self.assertEqual(value["content"]["definitions"], source["blocks"][0]["text"])
        self.assertEqual(value["content"]["depends_on"], [])
        self.assertEqual(len(value["reference_hints"]), 6)
        self.assertEqual(len(value["content"]["references"]), 6)
        self.assertTrue(
            all(x["status"] == "UNRESOLVED_OR_AMBIGUOUS" for x in value["reference_hints"][2:])
        )
        self.assertGreater(len(value["content"]["questions"]), len(drafts.QUESTIONS))

    def test_nearby_context_is_advisory_and_stops_at_scope_or_claim_boundaries(self):
        source = {
            "blocks": [
                block("old", "passage"),
                block("stop"),
                block("d", "definition"),
                block("main"),
                block("proof", "proof"),
                block("new-section", "section"),
                block("later", "passage"),
            ]
        }
        value = drafts.candidate(source, "main")
        self.assertEqual([x["block_id"] for x in value["nearby_context"]], ["d", "proof"])
        self.assertTrue(all(not x["automatically_attached"] for x in value["nearby_context"]))
        self.assertEqual(len(value["content"]["sources"]), 1)
        for change in ({"source": "other.tex"}, {"section": "Different scope"}):
            with self.subTest(change=change):
                changed = deepcopy(source)
                changed["blocks"][2].update(change)
                self.assertEqual(
                    [x["block_id"] for x in drafts.candidate(changed, "main")["nearby_context"]],
                    ["proof"],
                )

    def test_overlong_statement_and_reference_inventory_are_blocked_without_truncation(self):
        for primary in (
            block("main", text="x" * 6001),
            block("main", reference_labels=[f"ref-{i}" for i in range(21)]),
        ):
            with self.subTest(primary=primary["id"]):
                source = {"blocks": [primary]}
                before = deepcopy(source)
                value = drafts.candidate(source, "main")
                self.assertIsNone(value["content"])
                self.assertTrue(value["issues"])
                self.assertEqual(source, before)
        value = drafts.candidate({"blocks": [block("main", text="x" * 6000)]}, "main")
        self.assertEqual(len(value["content"]["conclusion"]), 6000)

    def test_large_definition_remains_linked_in_full_without_partial_copy(self):
        for size, claim_size in ((4001, 40), (4001, 5000)):
            source = {
                "blocks": [
                    block("d", "definition", "D" * size, labels=["d"]),
                    block("main", text="C" * claim_size, reference_labels=["d"]),
                ]
            }
            value = drafts.candidate(source, "main")["content"]
            self.assertEqual(value["definitions"], "")
            self.assertIn({"block_id": "d", "role": "definition"}, value["sources"])
            self.assertEqual(len(source["blocks"][0]["text"]), size)
            self.assertTrue(any("定义未填入" in q for q in value["questions"]))

    def test_titles_only_are_abbreviated_and_nonclaims_are_not_promoted(self):
        text = "For every n, " + "x" * 240
        value = drafts.candidate({"blocks": [block("main", text=text)]}, "main")
        self.assertEqual(len(value["title"]), 200)
        self.assertEqual(value["content"]["conclusion"], text)
        for key in ("missing", "passage"):
            with self.assertRaises(ValueError):
                drafts.candidate({"blocks": [block("passage", "passage")]}, key)


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="draft-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.docs = DocumentStore(self.root)
        doc = self.docs.create(
            source_request(sources=[{"name": "paper.tex", "content": TEX}], entrypoint="paper.tex")
        )
        self.doc = doc["document_id"]
        pump = DocumentPump(self.docs)
        with pump.owner() as fd:
            pump.execute(self.doc, fd)
        self.store = ObligationStore(self.root, documents=self.docs)
        self.row = self.store.create(
            {"request_id": "freeze", "document_id": self.doc, "expected_document_revision": 0}
        )
        self.key = self.row["dossier_id"]
        self.claims = [b for b in self.row["source"]["blocks"] if b["kind"] == "claim"]
        self.assertEqual(len(self.claims), 2)
        self.bid = self.claims[0]["id"]
        self.path = self.store.directory(self.key) / "record.json"

    def data(self, **changes):
        return {
            "request_id": "draft-" + uuid.uuid4().hex,
            "expected_revision": self.store.read(self.key)[0]["revision"],
            **changes,
        }

    def plan(self):
        return self.store.draft_plan(self.key, {})

    def packet(self, bid=None, plan=None):
        return self.store.draft_preview(
            self.key,
            {
                "block_id": bid or self.bid,
                "plan_fingerprint": (plan or self.plan())["plan_fingerprint"],
            },
        )

    def import_data(self, ids=None, plan=None, **changes):
        return self.data(
            plan_fingerprint=(plan or self.plan())["plan_fingerprint"],
            block_ids=ids if ids is not None else [self.bid],
            **changes,
        )

    def test_plan_and_preview_do_not_mutate_or_call_any_evidence_provider(self):
        before = self.path.read_bytes()
        with (
            patch.object(
                self.store, "_evidence_preview", side_effect=AssertionError("no provider")
            ),
            patch.object(self.store.studio, "create", side_effect=AssertionError("no research")),
        ):
            plan = self.plan()
            packet = self.packet(plan=plan)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(plan["candidates"]), 2)
        self.assertEqual(plan["coverage"]["claim_fragments"], 2)
        self.assertGreater(plan["coverage"]["nonclaim_fragments"], 0)
        self.assertTrue(plan["coverage"]["not_a_complete_mathematical_inventory"])
        self.assertFalse(plan["whole_article_verified"])
        self.assertFalse(plan["automatic_semantic_generation"])
        self.assertEqual((plan["model_calls"], plan["lean_executions"]), (0, 0))
        self.assertEqual(packet["content"]["conclusion"], self.claims[0]["text"])
        self.assertEqual(packet["status"], "READY")
        self.assertEqual(
            packet["content"]["definitions"],
            next(b["text"] for b in self.row["source"]["blocks"] if b["kind"] == "definition"),
        )

    def test_bridge_budget_retains_exactly_fitting_definitions_and_links_oversize_in_full(self):
        source = {
            "blocks": [
                block("d", "definition", "D", labels=["d"]),
                block("main", text="C" * 6000, reference_labels=["d"]),
            ]
        }
        raw = drafts.candidate(source, "main")["content"]
        overhead = len(complete_statement(raw)) - len(raw["conclusion"]) - 1
        source["blocks"][0]["text"] = "D" * (2000 - overhead)
        row, _ = self.store.read(self.key)
        fits = self.store._draft(row, source, "main")
        self.assertEqual(len(complete_statement(fits["content"])), 8000)
        self.assertEqual(fits["content"]["definitions"], source["blocks"][0]["text"])
        self.assertEqual(fits["status"], "READY")
        source["blocks"][0]["text"] += "D"
        exceeds = self.store._draft(row, source, "main")
        self.assertEqual(exceeds["content"]["definitions"], "")
        self.assertIn({"block_id": "d", "role": "definition"}, exceeds["content"]["sources"])
        self.assertEqual(len(source["blocks"][0]["text"]), 2001 - overhead)
        self.assertTrue(any("8000" in q for q in exceeds["content"]["questions"]))
        self.assertEqual(exceeds["content"]["conclusion"], "C" * 6000)

    def test_empty_inventory_is_explicitly_incomplete(self):
        first = self.docs.draft(self.doc)["blocks"]
        for i, b in enumerate(x for x in first if x["kind"] == "claim"):
            self.docs.review(
                self.doc,
                {
                    "request_id": "classify-" + str(i),
                    "expected_revision": i,
                    "block_id": b["id"],
                    "text": b["text"],
                    "kind": "passage",
                    "note": "Owned classification fixture",
                    "reviewed": True,
                },
            )
        row = self.store.create(
            {"request_id": "new-freeze", "document_id": self.doc, "expected_document_revision": 2}
        )
        plan = self.store.draft_plan(row["dossier_id"], {})
        self.assertEqual(plan["candidates"], [])
        self.assertTrue(plan["coverage"]["not_a_complete_mathematical_inventory"])
        self.assertFalse(plan["whole_article_verified"])

    def test_batch_persists_literal_version_one_receipts_and_no_acceptance(self):
        result = self.store.import_drafts(
            self.key, self.import_data(ids=[b["id"] for b in self.claims])
        )
        self.assertEqual(result["revision"], 1)
        self.assertEqual(len(result["versions"]), 2)
        for b in self.claims:
            oid = drafts.obligation_id(result["source_fingerprint"], b["id"])
            self.assertEqual(result["obligations"][oid]["content"]["conclusion"], b["text"])
            self.assertEqual(result["obligations"][oid]["version"], 1)
            self.assertEqual(result["draft_imports"][b["id"]]["import_revision"], 1)
            self.assertEqual(result["obligation_states"][oid]["semantic_state"], "NEEDS_REVIEW")
            self.assertEqual(result["obligation_states"][oid]["math_verdict"], "UNKNOWN")
        self.assertEqual(result["reviews"], [])
        self.assertEqual(result["source_reviews"], [])
        self.assertEqual(result["workspaces"], [])
        self.assertEqual(result["links"], [])
        self.assertEqual({x["status"] for x in self.plan()["candidates"]}, {"IMPORTED"})

    def test_identical_retry_is_idempotent_and_conflicting_or_stale_requests_fail(self):
        data = self.import_data()
        first = self.store.import_drafts(self.key, data)
        self.assertEqual(self.store.import_drafts(self.key, data), first)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.import_drafts(self.key, {**data, "block_ids": [self.claims[1]["id"]]})
        with self.assertRaisesRegex(ValueError, "dossier changed"):
            self.store.import_drafts(self.key, {**data, "request_id": "another-request"})
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.store.import_drafts(
                self.key, self.data(plan_fingerprint=data["plan_fingerprint"], block_ids=[self.bid])
            )

    def test_regeneration_preserves_edited_and_accepted_obligations(self):
        result = self.store.import_drafts(self.key, self.import_data())
        oid = next(iter(result["obligations"]))
        content = {
            **result["obligations"][oid]["content"],
            "objects": "Owned software review fixture",
            "questions": [],
        }
        self.store.save(self.key, self.data(obligation_id=oid, content=content))
        self.store.extraction_review(
            self.key,
            self.data(
                block_ids=[x["block_id"] for x in content["sources"]],
                reviewer="Fixture reviewer",
                note="Software contract only",
                confirmed=True,
            ),
        )
        preview = self.store.review_preview(self.key, {"obligation_id": oid})
        self.store.review(
            self.key,
            self.data(
                obligation_id=oid,
                preview_fingerprint=preview["preview_fingerprint"],
                decision="accept",
                reviewer="Fixture reviewer",
                note="Software contract only, not mathematical evidence",
                confirmations=list(REVIEW_ITEMS),
            ),
        )
        before = self.store.read(self.key)[0]
        result = self.store.import_drafts(
            self.key, self.import_data(ids=[b["id"] for b in self.claims])
        )
        for field in ("reviews", "source_reviews"):
            self.assertEqual(result[field], before[field])
        self.assertEqual(result["obligations"][oid], before["obligations"][oid])
        self.assertEqual(result["obligation_states"][oid]["semantic_state"], "ACCEPTED")
        self.assertEqual(result["obligation_states"][oid]["math_verdict"], "UNKNOWN")
        self.assertEqual(len(result["versions"]), 3)

    def test_initial_open_questions_block_acceptance_and_research(self):
        result = self.store.import_drafts(self.key, self.import_data())
        oid = next(iter(result["obligations"]))
        self.store.extraction_review(
            self.key,
            self.data(
                block_ids=[x["block_id"] for x in result["obligations"][oid]["content"]["sources"]],
                reviewer="Fixture reviewer",
                note="Software contract only",
                confirmed=True,
            ),
        )
        preview = self.store.review_preview(self.key, {"obligation_id": oid})
        with self.assertRaisesRegex(ValueError, "clarification"):
            self.store.review(
                self.key,
                self.data(
                    obligation_id=oid,
                    preview_fingerprint=preview["preview_fingerprint"],
                    decision="accept",
                    reviewer="Fixture reviewer",
                    note="Software contract only",
                    confirmations=list(REVIEW_ITEMS),
                ),
            )
        with self.assertRaisesRegex(ValueError, "Confirm the complete"):
            self.store.create_workspace(self.key, self.data(obligation_id=oid, area="数论"))

    def test_any_blocked_candidate_or_existing_id_conflict_rolls_back_entire_batch(self):
        first = self.packet()
        self.store.save(
            self.key,
            self.data(
                obligation_id=first["obligation_id"],
                content={
                    **first["content"],
                    "title": "Manual obligation occupying the generated ID",
                },
            ),
        )
        before = self.path.read_bytes()
        self.assertEqual(self.packet()["status"], "BLOCKED")
        with self.assertRaisesRegex(ValueError, "no drafts were imported"):
            self.store.import_drafts(self.key, self.import_data(ids=[b["id"] for b in self.claims]))
        self.assertEqual(self.path.read_bytes(), before)

    def test_blocked_generated_content_never_commits_a_partial_batch(self):
        actual = drafts.candidate

        def fail_one(source, bid):
            value = actual(source, bid)
            if bid == self.claims[1]["id"]:
                value["content"]["conclusion"] = "x" * 6001
            return value

        before = self.path.read_bytes()
        with patch.object(drafts, "candidate", side_effect=fail_one):
            self.assertEqual(self.packet(self.claims[1]["id"])["status"], "BLOCKED")
            with self.assertRaisesRegex(ValueError, "no drafts were imported"):
                self.store.import_drafts(
                    self.key, self.import_data(ids=[b["id"] for b in self.claims])
                )
        self.assertEqual(self.path.read_bytes(), before)

    def test_generated_id_collision_within_batch_is_rejected_before_write(self):
        before = self.path.read_bytes()
        with patch.object(drafts, "obligation_id", return_value="draft_collision"):
            with self.assertRaisesRegex(ValueError, "IDs conflict"):
                self.store.import_drafts(
                    self.key, self.import_data(ids=[b["id"] for b in self.claims])
                )
        self.assertEqual(self.path.read_bytes(), before)

    def test_capacity_limits_roll_back_every_item_and_expose_remaining_capacity(self):
        for name in ("obligations", "versions"):
            with self.subTest(name=name), patch.dict(LIMITS, {name: 1}):
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "capacity reached"):
                    self.store.import_drafts(
                        self.key, self.import_data(ids=[b["id"] for b in self.claims])
                    )
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.plan()["limits"][name + "_remaining"], 1)

    def test_metadata_budget_failure_preserves_previous_record_and_no_pending_file(self):
        from mra.research_flow import LIMITS as FLOW_LIMITS

        before = self.path.read_bytes()
        with patch.dict(FLOW_LIMITS, {"bytes": len(before) + 100}):
            with self.assertRaisesRegex(ValueError, "2 MiB"):
                self.store.import_drafts(self.key, self.import_data())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".pending-*")), [])
        self.assertEqual(self.store.read(self.key)[0]["revision"], 0)

    def test_invalid_ids_input_types_unknown_fields_and_batch_size_do_not_write(self):
        before = self.path.read_bytes()
        for ids in (
            [],
            [self.bid, self.bid],
            ["missing"],
            [b["id"] for b in self.row["source"]["blocks"] if b["kind"] == "proof"],
            [f"block-{i}" for i in range(17)],
            None,
            "claim",
            [False],
        ):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.store.import_drafts(self.key, {**self.import_data(), "block_ids": ids})
        for bid in ({}, [], None, False):
            with self.subTest(bid=bid), self.assertRaises(ValueError):
                self.store.draft_preview(
                    self.key, {"block_id": bid, "plan_fingerprint": self.plan()["plan_fingerprint"]}
                )
        with self.assertRaises(ValueError):
            self.store.draft_plan(self.key, {"run_lean": True})
        with self.assertRaises(ValueError):
            self.store.import_drafts(self.key, {**self.import_data(), "accept": True})
        self.assertEqual(self.path.read_bytes(), before)

    def test_stale_plan_after_unrelated_edit_is_rejected_for_preview_and_import(self):
        plan, packet = self.plan(), self.packet()
        self.store.save(self.key, self.data(obligation_id="manual", content=packet["content"]))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.packet(plan=plan)
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.store.import_drafts(self.key, self.import_data(plan=plan))
        self.assertEqual(self.path.read_bytes(), before)

    def test_source_edit_locks_and_dossier_locks_prevent_import(self):
        data, before = self.import_data(), self.path.read_bytes()
        for path in (self.docs.directory(self.doc), self.path.parent):
            with self.subTest(path=path), lease(path), self.assertRaises(WorkerBusy):
                self.store.import_drafts(self.key, data)
        self.assertEqual(self.path.read_bytes(), before)

    def test_source_revision_and_source_bytes_changes_invalidate_plan_and_import(self):
        plan, data = self.plan(), self.import_data()
        b = self.docs.draft(self.doc)["blocks"][0]
        self.docs.review(
            self.doc,
            {
                "request_id": "revise-source",
                "expected_revision": 0,
                "block_id": b["id"],
                "text": b["text"] + " Revision.",
                "kind": b["kind"],
                "note": "Owned revision fixture",
                "reviewed": True,
            },
        )
        before = self.path.read_bytes()
        for operation in (
            self.plan,
            lambda: self.packet(plan=plan),
            lambda: self.store.import_drafts(self.key, data),
        ):
            with self.assertRaisesRegex(ValueError, "Source revision"):
                operation()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.report(self.key)["source_state"], "SUPERSEDED")

    def test_original_file_tampering_rejects_plan_without_repairing_source(self):
        source_file = self.docs.directory(self.doc) / "source-00.txt"
        source_file.write_bytes(source_file.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "Source revision"):
            self.plan()
        self.assertTrue(source_file.read_bytes().endswith(b"tampered"))

    def test_optional_receipts_preserve_old_format_and_reject_damaged_provenance(self):
        self.assertNotIn("draft_imports", self.store.read(self.key)[0])
        self.store.import_drafts(self.key, self.import_data())
        original, _ = self.store.read(self.key)
        receipt = original["draft_imports"][self.bid]
        for field, value in (
            ("obligation_id", "wrong"),
            ("initial_content_fingerprint", "0" * 64),
            ("generator", "unrecognized"),
            ("plan_fingerprint", "not-a-hash"),
            ("import_revision", False),
            ("import_revision", 2),
        ):
            row = deepcopy(original)
            row["draft_imports"][self.bid] = {**receipt, field: value}
            atomic(self.path, row)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.store.read(self.key)
        row = deepcopy(original)
        row["events"][0]["kind"] = "OBLIGATION_VERSION_SAVED"
        atomic(self.path, row)
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.store.read(self.key)

    def test_fixed_source_snapshot_tampering_is_rejected(self):
        source_path = self.path.parent / "source.json"
        _, source = self.store.read(self.key)
        source["blocks"][0]["text"] += " changed"
        atomic(source_path, source)
        with self.assertRaisesRegex(ValueError, "snapshot identity"):
            self.plan()

    def test_plan_identity_binds_generator_implementation(self):
        plan = self.plan()
        with patch.object(drafts, "IMPLEMENTATION", "0" * 64):
            self.assertNotEqual(self.plan()["plan_fingerprint"], plan["plan_fingerprint"])
            with self.assertRaisesRegex(ValueError, "plan changed"):
                self.packet(plan=plan)

    def test_actual_http_draft_flow_guards_and_strict_inputs(self):
        with running_server(self.root) as server:
            path = "/v1/obligations/" + self.key
            code, plan, _ = request(server, "POST", path + "/draft-plan", {})
            self.assertEqual(code, 200, plan)
            packet_data = {"block_id": self.bid, "plan_fingerprint": plan["plan_fingerprint"]}
            data = self.import_data(plan=plan)
            for action, body in (
                ("draft-plan", {}),
                ("draft-preview", packet_data),
                ("import-drafts", data),
            ):
                for headers in ({"Origin": "https://evil.example"}, {"X-MRA-CSRF": "bad"}):
                    self.assertEqual(
                        request(server, "POST", path + "/" + action, body, headers=headers)[0], 403
                    )
                self.assertNotEqual(request(server, "GET", path + "/" + action)[0], 200)
                self.assertEqual(
                    request(server, "POST", path + "/" + action + "?x=1", body)[0], 400
                )
            code, packet, _ = request(server, "POST", path + "/draft-preview", packet_data)
            self.assertEqual(code, 200, packet)
            self.assertEqual(packet["content"]["conclusion"], self.claims[0]["text"])
            self.assertEqual(
                request(server, "POST", path + "/draft-preview", {**packet_data, "block_id": {}})[
                    0
                ],
                400,
            )
            self.assertEqual(
                request(server, "POST", path + "/draft-plan", raw=b'{"x":1,"x":2}')[0], 400
            )
            code, result, _ = request(server, "POST", path + "/import-drafts", data)
            self.assertEqual(code, 200, result)
            self.assertEqual(result["revision"], 1)
            self.assertEqual(result["workspaces"], [])
            self.assertFalse(result["whole_article_verified"])
            self.assertEqual(request(server, "POST", path + "/import-drafts", data)[1], result)
            catalog = request(server, "GET", "/v1/obligations/catalog")[1]
            self.assertTrue(catalog["automatic_draft_preparation"])
            self.assertFalse(catalog["automatic_semantic_generation"])
            self.assertEqual(catalog["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
