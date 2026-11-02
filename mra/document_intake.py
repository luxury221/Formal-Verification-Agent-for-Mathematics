"""Durable local document drafts; intentionally independent from proof authority."""

import base64
from contextlib import contextmanager
from copy import deepcopy
import fcntl
from html import escape
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from .article_source import source_url
from .document_extract import KINDS, safe_name, sha
from .domain import digest
from .formalizer import exact_object
from .model_service import text_field
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _now

SCHEMA = "source-document-intake-v1"
ACTIVE = ("QUEUED", "RUNNING")
MAX_PDF = 16 * 1024 * 1024
MAX_SOURCES = 1024 * 1024
MAX_DOCUMENTS = 30


def capabilities(root):
    """Presence hints only; no environment validation, worker start or storage writes."""
    runtime = Path(root).resolve() / ".runtime/document-ingestion-v1"
    return {
        "pdf": (runtime / "python/bin/python").is_file(),
        "ocr": (runtime / "ocr/usr/bin/tesseract").is_file()
        and (runtime / "ocr/usr/share/tesseract-ocr/4.00/tessdata/eng.traineddata").is_file(),
        "ocr_language": "English",
        "remote_uploads": False,
        "source_formats": ["html", "tex"],
        "maximum_pdf_bytes": MAX_PDF,
        "maximum_source_bytes": MAX_SOURCES,
        "runtime_assurance": "FILE_PRESENCE_ONLY",
    }


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("Invalid document request identifier")
    return value


def read_json(path):
    if path.is_symlink() or path.parent.resolve() != path.parent or path.stat().st_size > 2097152:
        raise ValueError("Document metadata is redirected or oversized")
    return _envelope(path)


class DocumentStore:
    def __init__(self, root, *, runtime_root=None):
        self.root = Path(root).resolve()
        self.runtime_root = Path(runtime_root or root).resolve()
        self.base = self.root / "artifacts/document-intake"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Document storage cannot be redirected")

    def directory(self, key):
        path = self.base / identifier(key, "document")
        if self.base.resolve() != self.base or path.resolve() != path:
            raise ValueError("Document storage cannot be redirected")
        return path

    def paths(self):
        paths = sorted(self.base.glob("document-*"))
        if len(paths) > MAX_DOCUMENTS:
            raise ValueError("Document intake capacity exceeded")
        for p in paths:
            self.directory(p.name)
        return paths

    def read(self, key):
        path = self.directory(key)
        row, inputs = read_json(path / "record.json"), read_json(path / "inputs.json")
        if row["document_id"] != key or row["input_fingerprint"] != digest(inputs):
            raise ValueError("Document input identity changed")
        return row, inputs

    def checked_inputs(self, key):
        row, inputs = self.read(key)
        path = self.directory(key)
        for spec in inputs["sources"] + ([inputs["pdf"]] if inputs["pdf"] else []):
            file = path / spec["file"]
            if (
                file.parent != path
                or file.is_symlink()
                or file.stat().st_size != spec["bytes"]
                or sha(file.read_bytes()) != spec["sha256"]
            ):
                raise ValueError("Original document bytes changed")
        return row, inputs

    def create(self, data):
        exact_object(
            data,
            {"request_id", "title", "url", "pdf", "sources", "entrypoint", "ocr"},
            "Import document",
        )
        rid = request_id(data["request_id"])
        text_field(data["title"], "Document title", 200)
        url = data["url"]
        if url:
            source_url(url)
        elif not isinstance(url, str):
            raise ValueError("Source URL must be a string")
        if data["ocr"] not in ("auto", "off"):
            raise ValueError("Choose automatic fallback OCR or disable it")
        if not isinstance(data["sources"], list) or len(data["sources"]) > 16:
            raise ValueError("Supply at most 16 HTML/TeX source files")
        files, sources, total, names = {}, [], 0, set()
        for i, item in enumerate(data["sources"]):
            exact_object(item, {"name", "content"}, "Source file")
            name = safe_name(item["name"])
            if not name.lower().endswith((".html", ".htm", ".tex")) or name.casefold() in names:
                raise ValueError("Use distinct HTML or TeX source filenames")
            names.add(name.casefold())
            text_field(item["content"], "Source content", MAX_SOURCES)
            raw = item["content"].encode("utf-8")
            total += len(raw)
            file = "source-%02d.txt" % i
            files[file] = raw
            sources.append({"name": name, "file": file, "sha256": sha(raw), "bytes": len(raw)})
        if total > MAX_SOURCES:
            raise ValueError("Source files together must not exceed 1 MiB")
        if sum(s["name"].lower().endswith((".html", ".htm")) for s in sources) > 1:
            raise ValueError("Supply only one primary HTML source")
        pdf = None
        if data["pdf"] is not None:
            exact_object(data["pdf"], {"name", "base64"}, "PDF upload")
            name = safe_name(data["pdf"]["name"])
            encoded = data["pdf"]["base64"]
            if (
                not name.lower().endswith(".pdf")
                or not isinstance(encoded, str)
                or len(encoded) > (MAX_PDF + 2) // 3 * 4
            ):
                raise ValueError("Upload a PDF of at most 16 MiB")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("Invalid PDF base64") from error
            if not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF:
                raise ValueError("Upload an actual PDF of at most 16 MiB")
            files["document.pdf"] = raw
            pdf = {"name": name, "file": "document.pdf", "sha256": sha(raw), "bytes": len(raw)}
        if not pdf and not sources:
            raise ValueError("Supply a PDF, HTML or LaTeX source")
        tex = [s["name"] for s in sources if s["name"].lower().endswith(".tex")]
        entry = data["entrypoint"]
        if entry is not None and entry not in tex:
            raise ValueError("The TeX entrypoint must be one of the supplied files")
        if tex and entry is None:
            entry = next(
                (s for s in tex if s.lower() == "main.tex"), tex[0] if len(tex) == 1 else None
            )
            if entry is None and not any(
                s["name"].lower().endswith((".html", ".htm")) for s in sources
            ):
                raise ValueError("Choose the main TeX file when supplying multiple TeX files")
        inputs = {
            "title": data["title"],
            "url": url,
            "pdf": pdf,
            "sources": sources,
            "entrypoint": entry,
            "ocr": data["ocr"],
        }
        fingerprint = digest(inputs)
        with lease(self.base):
            for path in self.paths():
                row, _ = self.read(path.name)
                if row["request_id"] == rid:
                    if row["input_fingerprint"] != fingerprint:
                        raise ValueError(
                            "Document request identifier belongs to different bytes or options"
                        )
                    return self.report(path.name)
            if len(self.paths()) >= MAX_DOCUMENTS:
                raise ValueError("Local document intake capacity reached (30 retained imports)")
            key = "document-" + uuid.uuid4().hex
            target = self.directory(key)
            staging = self.base / (".staging-" + uuid.uuid4().hex)
            staging.mkdir(mode=0o700)
            for name, raw in files.items():
                with (staging / name).open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            row = {
                "schema": SCHEMA,
                "document_id": key,
                "request_id": rid,
                "input_fingerprint": fingerprint,
                "title": data["title"],
                "url": url,
                "state": "QUEUED",
                "created_at": _now(),
                "revision": 0,
                "cancel_requested": False,
                "work": None,
                "draft_sha256": None,
                "error": None,
                "reviews": {},
                "edits": [],
                "exports": [],
            }
            atomic(staging / "inputs.json", inputs)
            atomic(staging / "record.json", row)
            staging.rename(target)
            fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return self.report(key)

    def report(self, key):
        row, inputs = self.read(key)
        result = deepcopy(row)
        result["files"] = [{k: v for k, v in s.items() if k != "file"} for s in inputs["sources"]]
        result["pdf"] = (
            {k: v for k, v in inputs["pdf"].items() if k != "file"} if inputs["pdf"] else None
        )
        result["ocr"] = inputs["ocr"]
        result["math_verdict"], result["evidence_level"] = "UNKNOWN", "E0"
        result["whole_article_verified"] = False
        result["progress"] = None
        if row["state"] == "RUNNING" and row["work"]:
            path = self.directory(key) / row["work"] / "progress.json"
            if path.exists():
                result["progress"] = read_json(path)
        result.pop("work", None)
        return result

    def listing(self):
        return [self.report(p.name) for p in self.paths()]

    def draft(self, key):
        row, _ = self.checked_inputs(key)
        if row["state"] != "READY":
            raise ValueError("This document has no completed extraction")
        value = read_json(self.directory(key) / "extracted/draft.json")
        if digest(value) != row["draft_sha256"]:
            raise ValueError("Extracted document changed")
        value = deepcopy(value)
        for item in value["blocks"]:
            review = row["reviews"].get(item["id"])
            item["extraction_reviewed"] = bool(review and review["reviewed"])
            item["extracted_text"], item["extracted_kind"] = item["text"], item["kind"]
            if review:
                item.update({k: review[k] for k in ("text", "kind", "note")})
            item["math_verdict"], item["evidence_level"] = "UNKNOWN", "E0"
        value["document_id"], value["revision"] = key, row["revision"]
        value["title"], value["url"] = row["title"], row["url"]
        value["exports"] = row["exports"]
        return value

    def cancel(self, key):
        path = self.directory(key)
        with lease(path):
            row, _ = self.read(key)
            if row["state"] in ACTIVE:
                row["cancel_requested"] = True
                if row["state"] == "QUEUED":
                    row["state"] = "CANCELLED"
                atomic(path / "record.json", row)
        return self.report(key)

    def review(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "block_id", "text", "kind", "note", "reviewed"},
            "Review extracted block",
        )
        rid = request_id(data["request_id"])
        if type(data["expected_revision"]) is not int or type(data["reviewed"]) is not bool:
            raise ValueError("Use an explicit revision and extraction review flag")
        if data["kind"] not in KINDS:
            raise ValueError("Unknown structural classification")
        text_field(data["text"], "Corrected block text", 48000)
        if not isinstance(data["note"], str) or len(data["note"]) > 2000:
            raise ValueError("Use a review note of at most 2000 characters")
        fingerprint = digest(data)
        path = self.directory(key)
        with lease(path):
            row, _ = self.read(key)
            for item in row["edits"]:
                if item["request_id"] == rid:
                    if item["fingerprint"] != fingerprint:
                        raise ValueError("Review request identifier belongs to another edit")
                    return self.draft(key)
            if data["expected_revision"] != row["revision"]:
                raise ValueError("Document revision changed; reload before saving")
            draft = self.draft(key)
            if data["block_id"] not in {b["id"] for b in draft["blocks"]}:
                raise ValueError("Choose an extracted block")
            if len(row["edits"]) >= 300:
                raise ValueError(
                    "Extraction review history holds 300 edits; export before continuing"
                )
            value = {k: data[k] for k in ("text", "kind", "note", "reviewed")}
            row["reviews"][data["block_id"]] = value
            row["revision"] += 1
            row["edits"].append(
                {
                    "request_id": rid,
                    "fingerprint": fingerprint,
                    "block_id": data["block_id"],
                    "revision": row["revision"],
                    "value": value,
                    "at": _now(),
                }
            )
            atomic(path / "record.json", row)
        return self.draft(key)

    def image(self, key, page):
        draft = self.draft(key)
        if type(page) is not int or not 1 <= page <= len(draft["pages"]):
            raise ValueError("Choose an existing PDF page")
        spec = draft["pages"][page - 1]
        path = self.directory(key) / "extracted" / spec["image"]
        if path.parent != self.directory(key) / "extracted" or path.is_symlink():
            raise ValueError("Page image cannot be redirected")
        raw = path.read_bytes()
        if sha(raw) != spec["image_sha256"]:
            raise ValueError("Page image bytes changed")
        return raw

    def source(self, key, index):
        _, inputs = self.checked_inputs(key)
        if type(index) is not int or not 0 <= index < len(inputs["sources"]):
            raise ValueError("Choose an uploaded source file")
        spec = inputs["sources"][index]
        return {
            "name": spec["name"],
            "content": (self.directory(key) / spec["file"]).read_bytes().decode("utf-8"),
        }

    def publish(self, key, data, articles):
        exact_object(data, {"request_id", "expected_revision"}, "Create article snapshot")
        rid = request_id(data["request_id"])
        if type(data["expected_revision"]) is not int:
            raise ValueError("Use an explicit document revision")
        path = self.directory(key)
        with lease(path):
            row, _ = self.read(key)
            for exported in row["exports"]:
                if exported["request_id"] == rid:
                    if exported["revision"] != data["expected_revision"]:
                        raise ValueError("Export identifier belongs to a different revision")
                    return exported
            if row["revision"] != data["expected_revision"]:
                raise ValueError("Document revision changed; reload before exporting")
            if not row["url"]:
                raise ValueError("An article snapshot needs an explicit HTTPS source citation")
            if len(row["exports"]) >= 12:
                raise ValueError("A document holds at most 12 article snapshots")
            draft = self.draft(key)
            html = (
                "<article>"
                + "".join(
                    '<p id="' + b["id"] + '">' + escape(b["text"]) + "</p>" for b in draft["blocks"]
                )
                + "</article>"
            )
            if len(html.encode()) > 1048576:
                raise ValueError("The normalized article exceeds the 1 MiB article budget")
            article = articles.create(
                {
                    "request_id": "document-" + digest([key, rid])[:32],
                    "title": row["title"],
                    "url": row["url"],
                    "html": html,
                }
            )
            exported = {
                "request_id": rid,
                "revision": row["revision"],
                "article_id": article["article_id"],
                "normalized_html_sha256": sha(html.encode()),
                "at": _now(),
                "scope": "Unclassified immutable structural snapshot; semantic annotation and verification remain separate",
            }
            row["exports"].append(exported)
            atomic(path / "record.json", row)
        return exported


class DocumentPump:
    """One local queue owner; children inherit its lease until they are reaped."""

    def __init__(self, store):
        self.store = store
        self.stopped, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name="document-intake", daemon=True)
        self.state, self.error, self.current = "IDLE", None, None

    def snapshot(self):
        return {"state": self.state, "error": self.error, "document_id": self.current}

    @contextmanager
    def owner(self):
        fd = os.open(self.store.base / ".owner", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerBusy("Another document worker owns the queue") from error
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def record_lease(self, key):
        # HTTP writers use immediate contention responses. A queue owner must
        # wait for their short commit: WorkerBusy here does not mean that a
        # different process owns the extraction queue.
        path = self.store.directory(key)
        fd = os.open(path / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 5
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Document record update remained busy for 5 seconds")
                    time.sleep(0.01)
            yield
        finally:
            os.close(fd)

    def save_terminal(self, key, state, error=None, work=None):
        path = self.store.directory(key)
        with self.record_lease(key):
            row, _ = self.store.read(key)
            if row["cancel_requested"]:
                state = "CANCELLED"
            elif self.stopped.is_set():
                state = "INTERRUPTED"
            if state == "READY":
                self.store.checked_inputs(key)
                draft = read_json(work / "draft.json")
                if (
                    draft.get("whole_article_verified") is not False
                    or draft.get("math_verdict") != "UNKNOWN"
                    or draft.get("evidence_level") != "E0"
                ):
                    raise ValueError("Document extraction cannot grant mathematical authority")
                for page in draft["pages"]:
                    file = work / page["image"]
                    if (
                        file.parent != work
                        or file.is_symlink()
                        or sha(file.read_bytes()) != page["image_sha256"]
                    ):
                        raise ValueError("Extracted page image changed before publication")
                if self.stopped.is_set():
                    state = "INTERRUPTED"
                else:
                    work.rename(path / "extracted")
                    row["draft_sha256"] = digest(draft)
            row.update(state=state, error=error, finished_at=_now())
            atomic(path / "record.json", row)

    def execute(self, key, owner_fd):
        path = self.store.directory(key)
        with self.record_lease(key):
            row, inputs = self.store.checked_inputs(key)
            if row["state"] != "QUEUED" or row["cancel_requested"] or self.stopped.is_set():
                return
            name = ".work-" + uuid.uuid4().hex
            work = path / name
            work.mkdir(mode=0o700)
            row.update(state="RUNNING", work=name, started_at=_now())
            atomic(path / "record.json", row)
        runtime = self.store.runtime_root / ".runtime/document-ingestion-v1/python/bin/python"
        if inputs["pdf"] and not runtime.exists():
            self.save_terminal(
                key,
                "FAILED",
                "PDF_RUNTIME_UNAVAILABLE: run ops/scripts/prepare_document_runtime.py --with-ocr",
            )
            return
        python = str(runtime) if runtime.exists() else sys.executable
        cmd = [
            python,
            "-I",
            str(Path(__file__).with_name("document_worker.py")),
            str(self.store.root),
            str(self.store.runtime_root),
            key,
            name,
        ]
        process = None
        try:
            with (work / "worker.log").open("wb") as log:
                process = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(owner_fd,),
                )
                deadline = time.monotonic() + 300
                while process.poll() is None:
                    pending, _ = self.store.read(key)
                    if pending["cancel_requested"] or self.stopped.is_set():
                        self.terminate(process)
                        self.save_terminal(key, "INTERRUPTED")
                        return
                    if time.monotonic() >= deadline:
                        self.terminate(process)
                        self.save_terminal(
                            key, "FAILED", "DOCUMENT_TIMEOUT: extraction exceeded 300 seconds"
                        )
                        return
                    self.stopped.wait(0.05)
                if process.returncode:
                    error_file = work / "error.json"
                    error = (
                        read_json(error_file)["error"]
                        if error_file.exists()
                        else "The document process exited without a complete draft"
                    )
                    self.save_terminal(key, "FAILED", str(error)[:2000])
                else:
                    self.save_terminal(key, "READY", work=work)
        finally:
            if process is not None:
                self.terminate(process)

    @staticmethod
    def terminate(process):
        # A crashed parser can leave OCR descendants behind even after its own
        # PID has exited. Always clean the dedicated process group, then reap.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def run(self):
        try:
            with self.owner() as owner_fd:
                for path in self.store.paths():
                    row, _ = self.store.read(path.name)
                    if row["state"] == "RUNNING":
                        self.save_terminal(
                            path.name,
                            "INTERRUPTED",
                            "Previous extraction ended without publication; reimport to run again",
                        )
                while not self.stopped.is_set():
                    found = False
                    for path in self.store.paths():
                        row, _ = self.store.read(path.name)
                        if row["state"] == "QUEUED":
                            found, self.current, self.state = True, path.name, "RUNNING"
                            try:
                                self.execute(path.name, owner_fd)
                            except (
                                ValueError,
                                OSError,
                                RuntimeError,
                                KeyError,
                                TypeError,
                            ) as error:
                                self.error = type(error).__name__ + ": " + str(error)[:300]
                                self.save_terminal(path.name, "FAILED", self.error)
                            self.current, self.state = None, "IDLE"
                            break
                    if not found:
                        self.wake.wait(0.3)
                        self.wake.clear()
        except WorkerBusy:
            self.state = "EXTERNAL"
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            self.state, self.error = "FAILED", type(error).__name__ + ": " + str(error)[:300]

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=10)
