"""Explicit HTML source locations; neither fixtures nor links are mathematical proof."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from document_fixtures import source_request
from test_http_api import ROOT, copy_web_inputs, request, running_server
from mra import document_html_refs as refs
from mra.document_extract import html_blocks, sha
from mra.document_intake import DocumentPump, DocumentStore
from mra.document_obligations import ObligationStore
from mra.obligation_drafts import candidate

HTML = """<article><section id="S1"><h2>Local references</h2>
<div class="ltx_theorem ltx_definition" id="definition"><p>Let n be a natural number. <span id="D.inner">Zero is the additive identity.</span></p></div>
<div id="equation-wrapper"><table class="ltx_equation" id="E1"><tr><td id="E.inner">n + 0 = n</td></tr></table></div>
<div class="ltx_theorem" id="main"><p>Theorem. For every natural number n, n + 0 = n; see <a href="#D.inner">Definition</a>, <a href="#equation-wrapper">Equation</a>, <a href="#missing">missing reference</a> and <a href="https://other.example/paper#D.inner">external source</a>.</p></div>
<p id="proof">Proof. The assertion remains unverified.</p>
</section></article>"""


def indexed(text=HTML, name="paper.html", citation=""):
    blocks = html_blocks(name, text, citation)[0]
    for i, b in enumerate(blocks, 1):
        b["id"] = f"block-{i:04d}"
    return blocks


def main_block(blocks):
    return next(b for b in blocks if b.get("label") == "main")


class HtmlReferenceTests(unittest.TestCase):
    def test_same_document_requires_exact_local_file_or_exact_citation(self):
        citation = "https://arxiv.org/html/2503.01639v3?view=full"
        for href in (
            "#eq",
            "paper.html#eq",
            "./paper.html#eq",
            citation + "#eq",
            "/html/2503.01639v3?view=full#eq",
            "//arxiv.org/html/2503.01639v3?view=full#eq",
        ):
            with self.subTest(href=href):
                self.assertEqual(
                    refs.same_document(href, "paper.html", citation, False), ("eq", None)
                )
        for href in (
            "https://arxiv.org/html/2503.01639v2?view=full#eq",
            "https://arxiv.org/html/2503.01639v3#eq",
            "http://arxiv.org/html/2503.01639v3?view=full#eq",
            "https://other.example/html/2503.01639v3?view=full#eq",
            "https://arxiv.org:444/html/2503.01639v3?view=full#eq",
            "other.html#eq",
            "paper.html?other=1#eq",
        ):
            with self.subTest(href=href):
                self.assertEqual(
                    refs.same_document(href, "paper.html", citation, False)[1], "DIFFERENT_DOCUMENT"
                )
        self.assertEqual(
            refs.same_document("./论文.html#定义", "目录/论文.html", "", False), ("定义", None)
        )

    def test_base_urls_invalid_encodings_and_unsafe_schemes_never_become_local(self):
        citation = "https://example.org/paper.html"
        for href in ("#d", "paper.html#d", "/paper.html#d", "//example.org/paper.html#d"):
            with self.subTest(href=href):
                self.assertEqual(
                    refs.same_document(href, "paper.html", citation, True)[1],
                    "BASE_URL_REQUIRES_REVIEW",
                )
        self.assertEqual(
            refs.same_document(citation + "#d", "paper.html", citation, True), ("d", None)
        )
        for href in (
            "javascript:alert(1)#d",
            "data:text/html,#d",
            "file:///paper.html#d",
            "#%zz",
            "#%ff",
            "#%00",
            "#has%20space",
            " #d",
            "#d\n",
            "https://user:secret@example.org/paper.html#d",
            "https://[broken/#d",
        ):
            with self.subTest(href=href):
                label, problem = refs.same_document(href, "paper.html", citation, False)
                self.assertIsNone(label)
                self.assertTrue(problem)

    def test_inner_and_wrapper_ids_resolve_to_full_existing_fragments(self):
        blocks = indexed()
        main = main_block(blocks)
        links = main["html_references"]
        self.assertEqual(
            [r["status"] for r in links],
            ["LOCATED", "LOCATED", "MISSING_TARGET", "DIFFERENT_DOCUMENT"],
        )
        definition = next(b for b in blocks if b["kind"] == "definition")
        equation = next(b for b in blocks if b["kind"] == "equation")
        self.assertIn("D.inner", refs.target_labels(definition))
        self.assertIn("equation-wrapper", refs.target_labels(equation))
        self.assertEqual(links[0]["target_block_span"], definition["span"])
        self.assertEqual(links[1]["target_block_span"], equation["span"])
        self.assertEqual(main["reference_labels"], ["D.inner", "equation-wrapper", "missing"])
        draft = candidate({"blocks": blocks}, main["id"])
        self.assertEqual(draft["content"]["definitions"], definition["text"])
        self.assertIn({"block_id": equation["id"], "role": "context"}, draft["content"]["sources"])
        self.assertEqual(draft["content"]["assumptions"], [])
        self.assertEqual(draft["content"]["depends_on"], [])
        self.assertIn(
            "https://other.example/paper#D.inner", "\n".join(draft["content"]["references"])
        )
        self.assertGreater(len(draft["content"]["questions"]), 4)

    def test_exact_raw_html_spans_survive_bom_crlf_unicode_and_entities(self):
        text = '\ufeff<article>\r\n<p id="d">定义 𝑛 <span id="定义&amp;值">x</span></p>\r\n<p id="main">设 x。<a href="#%E5%AE%9A%E4%B9%89%26%E5%80%BC">定义</a></p></article>'
        blocks = indexed(text)
        main = main_block(blocks)
        link = main["html_references"][0]
        self.assertEqual(link["label"], "定义&值")
        self.assertEqual(link["status"], "LOCATED")
        self.assertTrue(text[slice(*link["span"])].startswith('<a href="#%E5'))
        self.assertEqual(text[slice(*link["target_span"])], '<span id="定义&amp;值">x</span>')
        for b in blocks:
            self.assertEqual(sha(text[slice(*b["span"])].encode()), b["source_slice_sha256"])

    def test_duplicate_ids_anywhere_do_not_choose_the_first_definition(self):
        for duplicate in (
            '<p><span id="D.inner">Second location</span></p>',
            '<nav><span id="D.inner">Navigation</span></nav>',
        ):
            with self.subTest(duplicate=duplicate):
                blocks = indexed(HTML.replace("</article>", duplicate + "</article>"))
                main = main_block(blocks)
                self.assertEqual(main["html_references"][0]["status"], "AMBIGUOUS_TARGET")
                self.assertFalse(any("D.inner" in refs.target_labels(b) for b in blocks))
                packet = candidate({"blocks": blocks}, main["id"])
                self.assertEqual(packet["content"]["definitions"], "")

    def test_duplicate_id_or_href_attributes_are_not_silently_resolved(self):
        text = '<article><p id="d">Definition <span id="a" id="b">ambiguous</span></p><p id="main">See <a href="#a">A</a>, <a href="#b">B</a>, <a href="#d" href="#main">two hrefs</a>.</p></article>'
        main = main_block(indexed(text))
        self.assertEqual(
            [r["status"] for r in main["html_references"]],
            ["AMBIGUOUS_TARGET", "AMBIGUOUS_TARGET", "AMBIGUOUS_HREF_ATTRIBUTE"],
        )

    def test_sections_multifragment_wrappers_and_unindexed_targets_remain_unresolved(self):
        text = '<article><section id="section"><div id="many"><p>One.</p><p>Two.</p></div><span id="orphan">unindexed</span><p id="main">See <a href="#section">section</a>, <a href="#many">many</a>, <a href="#orphan">orphan</a>.</p></section></article>'
        blocks = indexed(text)
        self.assertEqual(
            [r["status"] for r in main_block(blocks)["html_references"]],
            ["MULTIPLE_FRAGMENTS", "MULTIPLE_FRAGMENTS", "UNINDEXED_TARGET"],
        )
        self.assertFalse(any({"section", "many", "orphan"} & refs.target_labels(b) for b in blocks))

    def test_generated_display_anchor_is_not_an_actual_html_id(self):
        blocks = indexed('<p>See <a href="#block-0">display anchor</a>.</p>')
        self.assertEqual(blocks[0]["label"], "block-0")
        self.assertEqual(refs.target_labels(blocks[0]), set())
        self.assertEqual(len(blocks[0]["html_references"]), 1)
        self.assertEqual(blocks[0]["html_references"][0]["status"], "MISSING_TARGET")
        self.assertEqual(refs.target_labels({"label": "tex:d", "labels": ["tex:d"]}), {"tex:d"})

    def test_nested_equations_link_all_containing_fragments_but_target_one_wrapper(self):
        text = '<article><p id="d">Definition.</p><div id="wrap"><table><tbody id="outer"><tr class="ltx_equation" id="inner"><td><a href="#d">D</a></td></tr></tbody></table></div><p id="main">See <a href="#wrap">E</a>.</p></article>'
        blocks = indexed(text)
        equation_blocks = [b for b in blocks if b["kind"] == "equation"]
        self.assertEqual(len(equation_blocks), 2)
        self.assertTrue(all(len(b["html_references"]) == 1 for b in equation_blocks))
        self.assertEqual(main_block(blocks)["html_references"][0]["status"], "LOCATED")
        self.assertIn("wrap", refs.target_labels(equation_blocks[0]))
        self.assertNotIn("wrap", refs.target_labels(equation_blocks[1]))

    def test_hidden_script_navigation_and_template_links_are_not_executed_or_resolved(self):
        text = '<article><p id="d">Definition.</p><p id="main">See <a href="#hidden">hidden</a>.<script>throw Error("not executed")</script><template><span id="hidden">Hidden</span><a href="#d">D</a></template></p></article>'
        blocks = indexed(text)
        links = main_block(blocks)["html_references"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["status"], "UNINDEXED_TARGET")
        self.assertFalse(any("hidden" in refs.target_labels(b) for b in blocks))

    def test_self_and_foreign_links_cannot_become_prerequisite_claims(self):
        text = '<article><div class="ltx_theorem" id="main"><p>Claim <a href="#main">self</a>, <a href="https://other.example/#main">other</a>.</p></div></article>'
        blocks = indexed(text)
        main = main_block(blocks)
        self.assertEqual(
            [r["status"] for r in main["html_references"]], ["SELF_REFERENCE", "DIFFERENT_DOCUMENT"]
        )
        packet = candidate({"blocks": blocks}, main["id"])
        self.assertEqual(
            packet["content"]["sources"], [{"block_id": main["id"], "role": "statement"}]
        )
        self.assertEqual(packet["content"]["depends_on"], [])

    def test_budget_failures_are_explicit_instead_of_truncating_references(self):
        with (
            patch.object(refs, "MAX_REFERENCES", 1),
            self.assertRaisesRegex(ValueError, "link budget"),
        ):
            indexed()
        with (
            patch.object(refs, "MAX_LABELS", 1),
            self.assertRaisesRegex(ValueError, "label budget"),
        ):
            indexed()
        with self.assertRaisesRegex(ValueError, "2000-character"):
            indexed('<p><a href="https://example.org/' + "x" * 2000 + '#d">D</a></p>')

    def test_corrected_text_keeps_original_links_with_an_explicit_scope_question(self):
        blocks = indexed()
        main = main_block(blocks)
        main["extracted_text"] = main["text"]
        main["text"] += " Corrected wording."
        packet = candidate({"blocks": blocks}, main["id"])
        self.assertEqual(packet["html_links"], main["html_references"])
        self.assertTrue(any("文字修正" in q for q in packet["content"]["questions"]))

    def test_reference_within_full_claim_is_already_in_source_not_a_missing_prerequisite(self):
        blocks = indexed(
            '<article><div class="ltx_theorem" id="main"><p>Let <span id="formula">f(x) = x</span>. By <a href="#formula">this formula</a>, f(0) = 0.</p></div></article>'
        )
        main = main_block(blocks)
        packet = candidate({"blocks": blocks}, main["id"])
        self.assertEqual(main["html_references"][0]["status"], "SELF_REFERENCE")
        self.assertEqual(main["reference_labels"], [])
        self.assertEqual(packet["content"]["references"], [])
        self.assertEqual(len(packet["content"]["questions"]), 4)
        self.assertEqual(packet["content"]["conclusion"], main["text"])


class HtmlReferenceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=ROOT / "artifacts", prefix="html-reference-contract-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_web_inputs(self.root)
        self.docs = DocumentStore(self.root)
        self.doc = self.docs.create(
            source_request(sources=[{"name": "paper.html", "content": HTML}])
        )["document_id"]
        pump = DocumentPump(self.docs)
        with pump.owner() as fd:
            pump.execute(self.doc, fd)
        self.store = ObligationStore(self.root, documents=self.docs)
        self.row = self.store.create(
            {"request_id": "freeze-html", "document_id": self.doc, "expected_document_revision": 0}
        )
        self.key = self.row["dossier_id"]

    def test_actual_child_extracts_locations_and_import_keeps_full_statement_without_proof(self):
        draft = self.docs.draft(self.doc)
        main = main_block(draft["blocks"])
        self.assertEqual(len(main["dependencies"]), 2)
        self.assertEqual(main["unresolved_references"], ["missing"])
        self.assertEqual(
            draft["extraction_components"]["document_html_refs_sha256"], refs.IMPLEMENTATION
        )
        plan = self.store.draft_plan(self.key, {})
        packet = self.store.draft_preview(
            self.key, {"block_id": main["id"], "plan_fingerprint": plan["plan_fingerprint"]}
        )
        before = deepcopy(self.row["source"])
        row = self.store.import_drafts(
            self.key,
            {
                "request_id": "import-html",
                "expected_revision": 0,
                "plan_fingerprint": plan["plan_fingerprint"],
                "block_ids": [main["id"]],
            },
        )
        content = row["obligations"][packet["obligation_id"]]["content"]
        self.assertEqual(content["conclusion"], main["text"])
        self.assertEqual(len(content["sources"]), 3)
        self.assertEqual(content["depends_on"], [])
        self.assertEqual(row["source"], before)
        self.assertEqual(row["reviews"], [])
        self.assertEqual(row["workspaces"], [])
        self.assertEqual((row["math_verdict"], row["evidence_level"]), ("UNKNOWN", "E0"))
        self.assertFalse(row["whole_article_verified"])

    def test_actual_http_preserves_link_metadata_and_prior_dossiers_remain_fixed(self):
        with running_server(self.root) as server:
            code, draft, _ = request(server, "GET", "/v1/documents/" + self.doc + "/draft")
            self.assertEqual(code, 200, draft)
            main = main_block(draft["blocks"])
            path = "/v1/obligations/" + self.key
            code, plan, _ = request(server, "POST", path + "/draft-plan", {})
            self.assertEqual(code, 200, plan)
            code, packet, _ = request(
                server,
                "POST",
                path + "/draft-preview",
                {"block_id": main["id"], "plan_fingerprint": plan["plan_fingerprint"]},
            )
            self.assertEqual(code, 200, packet)
            self.assertEqual(packet["html_links"], main["html_references"])
            self.docs.review(
                self.doc,
                {
                    "request_id": "edit-source",
                    "expected_revision": 0,
                    "block_id": main["id"],
                    "text": main["text"] + " Edited.",
                    "kind": main["kind"],
                    "reviewed": False,
                    "note": "Owned source revision contract",
                },
            )
            self.assertEqual(self.store.report(self.key)["source_state"], "SUPERSEDED")
            self.assertEqual(self.store.report(self.key)["source"], self.row["source"])
            self.assertEqual(request(server, "POST", path + "/draft-plan", {})[0], 400)

    def test_reference_budget_failure_never_publishes_a_partial_child_result(self):
        # Actual child failure is induced with an oversized URL, not a mock proof.
        doc = self.docs.create(
            source_request(
                "long-link",
                sources=[
                    {
                        "name": "paper.html",
                        "content": '<p>See <a href="https://example.org/'
                        + "x" * 2000
                        + '#d">D</a>.</p>',
                    }
                ],
            )
        )["document_id"]
        pump = DocumentPump(self.docs)
        with pump.owner() as fd:
            pump.execute(doc, fd)
        self.assertEqual(self.docs.report(doc)["state"], "FAILED")
        self.assertFalse((self.docs.directory(doc) / "extracted").exists())
        with self.assertRaises(ValueError):
            self.docs.draft(doc)


if __name__ == "__main__":
    unittest.main()
