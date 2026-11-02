"""Actual local persistence and child cancellation; no mathematical evidence."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mra.article_audit import ArticleAuditStore
from mra.document_extract import html_blocks, tex_blocks, safe_name, sha
from mra.document_intake import DocumentStore, DocumentPump, read_json
from mra.research_flow import atomic, lease
from mra.tasks import WorkerBusy
from document_fixtures import HTML, TEX, source_request, pdf_request

ROOT = Path(__file__).resolve().parents[1]


def wait_for(function, timeout=7):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Document fixture deadline")


class SourceStructureTests(unittest.TestCase):
    def test_html_keeps_exact_spans_and_does_not_execute_script(self):
        text = HTML.replace("</article>", "<script>throw Error('untrusted')</script></article>")
        blocks, sections, _ = html_blocks("paper.html", text)
        self.assertEqual(blocks[0]["kind"], "claim")
        self.assertIn("For every natural number", blocks[0]["text"])
        self.assertNotIn("untrusted", " ".join(b["text"] for b in blocks))
        a, b = blocks[0]["span"]
        self.assertTrue(text[a:b].startswith('<div class="ltx_theorem"'))
        self.assertEqual(sections[0]["title"], "1. Addition")

    def test_tex_includes_preserve_conditions_labels_and_unexecuted_macros(self):
        sources = {
            "main.tex": TEX,
            "parts/note.tex": r"\section{Notes}\begin{definition}Let $n$ be a natural number.\end{definition}",
        }
        blocks, _, _ = tex_blocks(sources, "main.tex")
        theorem = next(b for b in blocks if b["kind"] == "claim")
        proof = next(b for b in blocks if b["kind"] == "proof")
        self.assertIn("For every natural number", theorem["text"])
        self.assertEqual(theorem["labels"], ["thm:add"])
        self.assertEqual(proof["reference_labels"], ["thm:add"])
        self.assertEqual(blocks[-1]["source"], "parts/note.tex")

    def test_missing_cyclic_includes_comments_and_unreachable_files_are_visible(self):
        text = (
            r"\begin{document}"
            + "\n% SECRET COMMENT\n"
            + r"\input{missing}\input{main}\end{document}"
        )
        blocks, _, warnings = tex_blocks({"main.tex": text, "unused.tex": "unused"}, "main.tex")
        self.assertTrue(any("Unresolved" in w for w in warnings))
        self.assertTrue(any("Cyclic" in w for w in warnings))
        self.assertTrue(any("not reached" in w for w in warnings))
        self.assertNotIn("SECRET COMMENT", " ".join(b["text"] for b in blocks))

    def test_unicode_names_allowed_but_traversal_absolute_and_windows_paths_rejected(self):
        self.assertEqual(safe_name("子目录/论文.tex"), "子目录/论文.tex")
        for value in (
            "../main.tex",
            "/main.tex",
            "a/../b.tex",
            "a\\b.tex",
            "C:main.tex",
            "a//b.tex",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_name(value)


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="document-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DocumentStore(self.root)

    def create(self, **changes):
        return self.store.create(source_request(**changes))

    def complete(self, request=None):
        row = self.store.create(request or source_request())
        pump = DocumentPump(self.store)
        with pump.owner() as fd:
            pump.execute(row["document_id"], fd)
        result = self.store.report(row["document_id"])
        self.assertEqual(result["state"], "READY", result)
        return result["document_id"], self.store.draft(result["document_id"])

    def test_import_binds_exact_bytes_and_rejects_changed_idempotency_body(self):
        a, b = self.create(), self.create()
        self.assertEqual(a["document_id"], b["document_id"])
        changed = source_request(title="Different input")
        with self.assertRaises(ValueError):
            self.store.create(changed)
        self.assertEqual(a["math_verdict"], "UNKNOWN")
        self.assertEqual(a["evidence_level"], "E0")
        self.assertFalse(a["whole_article_verified"])

    def test_bom_and_crlf_survive_source_view_and_exact_span_extraction(self):
        text = "\ufeff" + HTML.replace("><", ">\r\n<")
        key, draft = self.complete(
            source_request(sources=[{"name": "paper.html", "content": text}])
        )
        self.assertEqual(self.store.source(key, 0)["content"], text)
        for item in draft["blocks"]:
            a, b = item["span"]
            self.assertEqual(item["source_slice_sha256"], sha(text[a:b].encode()))

    def test_invalid_pdf_duplicate_sources_extra_fields_and_ambiguous_tex_rejected(self):
        cases = [
            source_request(pdf={"name": "p.pdf", "base64": "invalid"}),
            source_request(unexpected=True),
            source_request(
                sources=[{"name": "a.tex", "content": "x"}, {"name": "A.tex", "content": "y"}]
            ),
            source_request(
                sources=[{"name": "a.tex", "content": "x"}, {"name": "b.tex", "content": "y"}]
            ),
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                self.store.create(data)

    def test_queued_cancellation_never_exposes_partial_draft(self):
        key = self.create()["document_id"]
        self.assertEqual(self.store.cancel(key)["state"], "CANCELLED")
        pump = DocumentPump(self.store)
        with pump.owner() as fd:
            pump.execute(key, fd)
        with self.assertRaises(ValueError):
            self.store.draft(key)
        self.assertFalse((self.store.directory(key) / "extracted").exists())

    def test_actual_source_child_publishes_structure_and_explicit_dependencies_only(self):
        request = source_request(
            sources=[
                {"name": "main.tex", "content": TEX},
                {"name": "parts/note.tex", "content": "A further unverified paragraph."},
            ],
            entrypoint="main.tex",
        )
        _, draft = self.complete(request)
        self.assertEqual(draft["method"], "LATEX_SOURCE")
        theorem = next(b for b in draft["blocks"] if b["kind"] == "claim")
        proof = next(b for b in draft["blocks"] if b["kind"] == "proof")
        self.assertEqual(proof["dependencies"], [theorem["id"]])
        self.assertFalse(draft["semantic_review_complete"])
        self.assertTrue(all(b["assumptions_status"] == "NOT_REVIEWED" for b in draft["blocks"]))

    def test_edits_are_versioned_idempotent_and_do_not_overwrite_extracted_original(self):
        key, draft = self.complete()
        original = (self.store.directory(key) / "extracted/draft.json").read_bytes()
        first = draft["blocks"][0]
        data = {
            "request_id": "review-1",
            "expected_revision": 0,
            "block_id": first["id"],
            "text": first["text"] + " Corrected typography.",
            "kind": "claim",
            "note": "Compared with source",
            "reviewed": True,
        }
        updated = self.store.review(key, data)
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(self.store.review(key, data)["revision"], 1)
        self.assertEqual(updated["blocks"][0]["extracted_text"], first["text"])
        self.assertEqual(
            (self.store.directory(key) / "extracted/draft.json").read_bytes(), original
        )
        self.assertEqual(updated["blocks"][0]["math_verdict"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.review(key, {**data, "request_id": "stale"})
        with self.assertRaises(ValueError):
            self.store.review(key, {**data, "text": "Different retry"})

    def test_modified_source_or_draft_is_rejected(self):
        key, _ = self.complete()
        path = self.store.directory(key) / "source-00.txt"
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"Theorem", b"Xheorem", 1))
        with self.assertRaises(ValueError):
            self.store.draft(key)
        path.write_bytes(raw)
        target = self.store.directory(key) / "extracted/draft.json"
        value = read_json(target)
        value["blocks"][0]["text"] = "Replaced source"
        atomic(target, value)
        with self.assertRaises(ValueError):
            self.store.draft(key)

    def test_history_byte_limit_rejects_edit_without_corrupting_readable_previous_revision(self):
        key, draft = self.complete()
        path = self.store.directory(key) / "record.json"
        for i in range(30):
            before = path.read_bytes()
            data = {
                "request_id": "large-edit-" + str(i),
                "expected_revision": i,
                "block_id": draft["blocks"][0]["id"],
                "text": "数" * 48000,
                "kind": "claim",
                "note": "Large correction exercises the persisted UTF-8 byte limit",
                "reviewed": False,
            }
            try:
                self.store.review(key, data)
            except ValueError as error:
                self.assertIn("2 MiB", str(error))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.store.draft(key)["revision"], i)
                self.assertLessEqual(path.stat().st_size, 2097152)
                break
        else:
            self.fail("Cumulative correction history must hit the byte limit")

    def test_symlinked_input_and_working_directories_are_rejected(self):
        key = self.create()["document_id"]
        file = self.store.directory(key) / "source-00.txt"
        raw = file.read_bytes()
        outside = self.root / "elsewhere.txt"
        outside.write_bytes(raw)
        file.unlink()
        file.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.checked_inputs(key)

    def test_article_snapshot_is_unclassified_immutable_and_retry_does_not_duplicate(self):
        key, draft = self.complete()
        articles = ArticleAuditStore(self.root)
        data = {"request_id": "export-1", "expected_revision": 0}
        exported = self.store.publish(key, data, articles)
        self.assertEqual(self.store.publish(key, data, articles), exported)
        row, source, _ = articles.read(exported["article_id"])
        self.assertEqual(row["annotations"], {})
        original = source["sha256"]
        b = draft["blocks"][0]
        self.store.review(
            key,
            {
                "request_id": "after-export",
                "expected_revision": 0,
                "block_id": b["id"],
                "text": "Changed draft only",
                "kind": b["kind"],
                "note": "",
                "reviewed": False,
            },
        )
        self.assertEqual(articles.read(exported["article_id"])[1]["sha256"], original)
        self.assertEqual(len(articles.listing()), 1)

    def test_source_only_import_operates_without_pdf_runtime(self):
        _, draft = self.complete()
        self.assertEqual(draft["method"], "HTML_SOURCE")
        self.assertEqual(draft["pages"], [])
        self.assertFalse((self.root / ".runtime").exists())

    def test_missing_pdf_runtime_fails_with_actionable_status_without_partial_success(self):
        key = self.store.create(pdf_request())["document_id"]
        pump = DocumentPump(self.store)
        with pump.owner() as fd:
            pump.execute(key, fd)
        value = self.store.report(key)
        self.assertEqual(value["state"], "FAILED")
        self.assertIn("PDF_RUNTIME_UNAVAILABLE", value["error"])
        with self.assertRaises(ValueError):
            self.store.draft(key)

    def test_actual_stalled_child_cancel_and_shutdown_reap_without_publishing(self):
        original_popen = subprocess.Popen
        processes = []

        def launch(*args, **kwargs):
            p = original_popen([sys.executable, "-c", "import time;time.sleep(60)"], **kwargs)
            processes.append(p)
            return p

        for action in ("cancel", "close"):
            with self.subTest(action=action):
                key = self.store.create(source_request("process-" + action))["document_id"]
                pump = DocumentPump(self.store)
                with patch("mra.document_intake.subprocess.Popen", side_effect=launch):
                    pump.thread.start()
                    try:
                        wait_for(
                            lambda: processes
                            and processes[-1].poll() is None
                            and self.store.report(key)["state"] == "RUNNING"
                        )
                        started = time.monotonic()
                        if action == "cancel":
                            self.store.cancel(key)
                            wait_for(lambda: self.store.report(key)["state"] == "CANCELLED")
                    finally:
                        pump.close()
                        for child in processes:
                            DocumentPump.terminate(child)
                    self.assertLess(time.monotonic() - started, 3)
                    self.assertIsNotNone(processes[-1].poll())
                    self.assertFalse((self.store.directory(key) / "extracted").exists())
                    self.assertIsNone(self.store.report(key)["draft_sha256"])
                    self.assertEqual(
                        self.store.report(key)["state"],
                        "CANCELLED" if action == "cancel" else "INTERRUPTED",
                    )

    def test_cancel_commit_contention_waits_without_losing_queue_owner(self):
        key = self.create()["document_id"]
        original_popen, processes = subprocess.Popen, []

        def launch(*args, **kwargs):
            child = original_popen([sys.executable, "-c", "import time;time.sleep(60)"], **kwargs)
            processes.append(child)
            return child

        pump = DocumentPump(self.store)
        with patch("mra.document_intake.subprocess.Popen", side_effect=launch):
            pump.thread.start()
            try:
                wait_for(lambda: processes and self.store.report(key)["state"] == "RUNNING")
                path = self.store.directory(key)
                with lease(path):
                    row, _ = self.store.read(key)
                    row["cancel_requested"] = True
                    atomic(path / "record.json", row)
                    wait_for(lambda: processes[0].poll() is not None)
                    time.sleep(0.1)
                    self.assertTrue(pump.thread.is_alive(), pump.snapshot())
                    self.assertEqual(pump.state, "RUNNING", pump.snapshot())
                    self.assertEqual(self.store.report(key)["state"], "RUNNING")
                wait_for(lambda: self.store.report(key)["state"] == "CANCELLED")
                self.assertIsNone(self.store.report(key)["draft_sha256"])
            finally:
                pump.close()
                for child in processes:
                    DocumentPump.terminate(child)

    def test_owner_prevents_second_worker_and_restart_marks_running_interrupted(self):
        key = self.create()["document_id"]
        row, _ = self.store.read(key)
        row["state"] = "RUNNING"
        atomic(self.store.directory(key) / "record.json", row)
        pump, other = DocumentPump(self.store), DocumentPump(self.store)
        with pump.owner():
            with self.assertRaises(WorkerBusy):
                with other.owner():
                    pass
        pump.thread.start()
        try:
            wait_for(lambda: self.store.report(key)["state"] == "INTERRUPTED")
        finally:
            pump.close()
        self.assertIsNone(self.store.report(key)["draft_sha256"])

    def test_cleanup_kills_descendant_even_after_parser_leader_exits(self):
        pidfile = self.root / "descendant.pid"
        code = (
            "import os,time,pathlib;pid=os.fork();"
            "pathlib.Path("
            + repr(str(pidfile))
            + ").write_text(str(pid)) if pid else time.sleep(60)"
        )
        process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        self.addCleanup(DocumentPump.terminate, process)
        process.wait(timeout=7)
        descendant = int(pidfile.read_text())

        def stopped():
            try:
                return Path(f"/proc/{descendant}/stat").read_text().split()[2] == "Z"
            except FileNotFoundError:
                return True

        self.assertFalse(stopped())
        DocumentPump.terminate(process)
        wait_for(stopped)


@unittest.skipUnless(
    os.environ.get("MRA_DOCUMENT_RUNTIME") == "1", "Explicit project-local PDF and actual OCR tools"
)
class ActualDocumentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts", prefix="actual-document-")
        self.addCleanup(self.temp.cleanup)
        self.store = DocumentStore(Path(self.temp.name), runtime_root=ROOT)

    def extract(self, data):
        key = self.store.create(data)["document_id"]
        pump = DocumentPump(self.store)
        with pump.owner() as fd:
            pump.execute(key, fd)
        self.assertEqual(self.store.report(key)["state"], "READY", self.store.report(key))
        return key, self.store.draft(key)

    def test_native_pdf_uses_text_layer_and_returns_real_page_image(self):
        key, draft = self.extract(pdf_request())
        self.assertEqual(draft["pages"][0]["method"], "PDF_TEXT")
        self.assertEqual(draft["summary"]["ocr_pages"], [])
        self.assertIn("natural number", draft["pages"][0]["text"])
        self.assertTrue(self.store.image(key, 1).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_real_scanned_page_uses_tesseract_and_off_mode_retains_unreadable_page(self):
        _, draft = self.extract(pdf_request(scan=True))
        self.assertEqual(draft["pages"][0]["method"], "OCR")
        self.assertIn("natural number", draft["pages"][0]["text"])
        self.assertEqual(draft["summary"]["ocr_pages"], [1])
        _, off = self.extract(pdf_request("ocr-off", scan=True, ocr="off"))
        self.assertEqual(off["pages"][0]["issue"], "OCR_DISABLED_TEXT_INSUFFICIENT")
        self.assertEqual(off["blocks"][0]["kind"], "unreadable")

    def test_valid_source_takes_precedence_and_does_not_ocr_scanned_pdf(self):
        data = pdf_request(scan=True)
        data["sources"] = [{"name": "paper.html", "content": HTML}]
        _, draft = self.extract(data)
        self.assertEqual(draft["method"], "HTML_SOURCE")
        self.assertEqual(draft["summary"]["ocr_pages"], [])
        self.assertTrue(any("For every natural number" in b["text"] for b in draft["blocks"]))
        self.assertEqual(draft["pages"][0]["issue"], "SOURCE_USED_PDF_PAGE_MATCH_UNAVAILABLE")

    def test_bad_html_falls_back_to_pdf_and_wrong_page_indices_are_rejected(self):
        data = pdf_request()
        data["sources"] = [{"name": "bad.html", "content": "<html>no indexed body</html>"}]
        key, draft = self.extract(data)
        self.assertEqual(draft["method"], "PDF_TEXT_WITH_OCR_FALLBACK")
        self.assertTrue(any("could not be indexed" in w for w in draft["warnings"]))
        for page in (0, 2, -1, True):
            with self.assertRaises(ValueError):
                self.store.image(key, page)
