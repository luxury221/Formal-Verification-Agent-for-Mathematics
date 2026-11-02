"""Durable automatic paper jobs, separate from human-reviewed mathematical ledgers."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
from pathlib import Path
import time
import uuid

from .chat_model import model_status
from .document_intake import DocumentStore, read_json, request_id
from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .paper_protocol import SCHEMA as LEGACY_SCHEMA, DEFAULT_BUDGET, MAX_BUDGET, budget, chunks
from . import paper_budget, paper_structure
from .research_flow import atomic, identifier, lease
from .tasks import WorkerBusy, _now

ACTIVE = ("QUEUED", "WAITING_FOR_SOURCE", "RUNNING")
RESUMABLE = ("WAITING_FOR_MODEL", "INTERRUPTED", "MODEL_ERROR", "EXECUTION_ERROR")
CODE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "whole-paper-automation-v2"


def capabilities(root):
    """Describe the existing pipeline without opening ledgers or executing work."""
    return {
        "schema": SCHEMA,
        "model": model_status(root),
        "default_budget": deepcopy(DEFAULT_BUDGET),
        "maximum_budget": deepcopy(MAX_BUDGET),
        "profiles": ["typed", "calculus", "proof2"],
        "whole_article_verified": False,
        "human_review_generated": False,
        "maximum_reading_chunks": paper_structure.MAX_CHUNKS,
        "maximum_split_depth": paper_structure.MAX_DEPTH,
        "accounting_schema": paper_budget.SCHEMA,
    }


@contextmanager
def metadata_lease(directory):
    """Wait briefly for a concurrent extractor's short metadata commit."""
    if directory.resolve() != directory:
        raise ValueError("Paper metadata cannot be redirected")
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + 5
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkerBusy("Paper metadata remained busy for 5 seconds")
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def implementation():
    # Small source files only; never walk or reseal a runtime tree here.
    paths = sorted((CODE_ROOT / "mra").glob("*.py"))
    paths += sorted((CODE_ROOT / "lean_workspace").glob("*.in"))
    paths += [CODE_ROOT / "ops/scripts/sandbox_worker.py"]
    return {str(p.relative_to(CODE_ROOT)): file_hash(p) for p in paths}


class PaperAutomationStore:
    def __init__(self, root, *, documents=None, runtime_root=None):
        self.root = Path(root).resolve()
        self.runtime_root = Path(runtime_root or root).resolve()
        self.documents = documents or DocumentStore(self.root, runtime_root=self.runtime_root)
        self.base = self.root / "artifacts/paper-automation"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Paper automation storage cannot be redirected")

    def directory(self, key):
        p = self.base / identifier(key, "paperauto")
        if p.resolve() != p or self.base.resolve() != self.base:
            raise ValueError("Paper automation storage cannot be redirected")
        return p

    def paths(self):
        paths = sorted(self.base.glob("paperauto-*"))
        if len(paths) > 30:
            raise ValueError("Paper automation capacity exceeded")
        for p in paths:
            self.directory(p.name)
        return paths

    def read(self, key):
        row = read_json(self.directory(key) / "record.json")
        if row["job_id"] != key or row["schema"] not in (SCHEMA, LEGACY_SCHEMA):
            raise ValueError("Paper job identity changed")
        return row

    @contextmanager
    def edit(self, key):
        # Short metadata transactions only. No lock is held during model/Lean calls.
        directory = self.directory(key)
        fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 5
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WorkerBusy("Paper job update remained busy")
                    time.sleep(0.01)
            row = self.read(key)
            before = deepcopy(row)
            yield row
            if row != before:
                row["revision"] += 1
                row["updated_at"] = _now()
                atomic(directory / "record.json", row)
        finally:
            os.close(fd)

    def create(self, raw):
        exact_object(
            raw,
            {"request_id", "document_id", "expected_document_revision", "budget"},
            "Automatic paper job",
        )
        request_id(raw["request_id"])
        if (
            type(raw["expected_document_revision"]) is not int
            or raw["expected_document_revision"] < 0
        ):
            raise ValueError("Pin the exact document revision")
        limits = budget(raw["budget"])
        with lease(self.base):
            for p in self.paths():
                old = self.read(p.name)
                if old["request_id"] == raw["request_id"]:
                    if old["input_fingerprint"] != digest(raw):
                        raise ValueError("Paper request ID belongs to different input")
                    return self.report(p.name)
            if len(self.paths()) >= 30:
                raise ValueError("Retain at most 30 automatic paper jobs")
            with metadata_lease(self.documents.directory(raw["document_id"])):
                doc, _ = self.documents.checked_inputs(raw["document_id"])
                if doc["revision"] != raw["expected_document_revision"] or doc["state"] not in (
                    "READY",
                    "QUEUED",
                    "RUNNING",
                ):
                    raise ValueError("Use a current ready or extracting document revision")
                source = (
                    self.documents.draft(raw["document_id"]) if doc["state"] == "READY" else None
                )
            key = "paperauto-" + uuid.uuid4().hex
            plan = self.chunk_records(source) if source else []
            index = paper_structure.build_index(source) if source else None
            staging = self.base / (".staging-" + uuid.uuid4().hex)
            staging.mkdir(mode=0o700)
            row = {
                "schema": SCHEMA,
                "job_id": key,
                "request_id": raw["request_id"],
                "input_fingerprint": digest(raw),
                "document_id": doc["document_id"],
                "document_revision": doc["revision"],
                "document_input": doc["input_fingerprint"],
                "title": doc["title"],
                "created_at": _now(),
                "updated_at": _now(),
                "revision": 0,
                "state": "QUEUED" if source else "WAITING_FOR_SOURCE",
                "stage": "READING",
                "source_fingerprint": digest(source) if source else None,
                "implementation": implementation(),
                "model": None,
                "structure": "structure.json" if source else None,
                "accounting_schema": paper_budget.SCHEMA,
                "reservations": {},
                "budget": limits,
                "cancel_requested": False,
                "error": None,
                "chunks": plan,
                "items": {},
                "artifacts": {},
                "calls": {},
                "actions": {},
                "active": None,
                "elapsed_seconds": 0,
                "used": {
                    "model_calls": 0,
                    "output_tokens_reserved": 0,
                    "input_tokens_reported": 0,
                    "output_tokens_reported": 0,
                    "kernel_seconds_reserved": 0,
                    "kernel_seconds_reported": 0,
                },
            }
            if source:
                atomic(staging / "source.json", source)
                row["artifacts"]["source.json"] = file_hash(staging / "source.json")
                atomic(staging / "structure.json", index)
                row["artifacts"]["structure.json"] = file_hash(staging / "structure.json")
            atomic(staging / "record.json", row)
            staging.rename(self.directory(key))
            fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return self.report(key)

    @staticmethod
    def chunk_records(source):
        return [
            {
                "index": i,
                "status": "PENDING",
                "attempts": 0,
                "extra_context": [],
                "feedback": None,
                "reading": None,
                "audit": None,
                "parent": None,
                "children": [],
                "depth": 0,
                "focus": refs,
            }
            for i, refs in enumerate(paper_structure.partition(source))
        ]

    def focus(self, row, chunk, source=None):
        source = source or self.source(row)
        if row["schema"] == LEGACY_SCHEMA:
            return chunks(source)[chunk["index"]]
        return paper_structure.materialize(source, chunk["focus"])

    def split(self, key, index, reason):
        row = self.read(key)
        children = paper_structure.split_focus(self.source(row), row["chunks"][index]["focus"])
        with self.edit(key) as current:
            parent = current["chunks"][index]
            if parent["children"]:
                return
            if (
                children is None
                or parent["depth"] >= paper_structure.MAX_DEPTH
                or len(current["chunks"]) + 2 > paper_structure.MAX_CHUNKS
            ):
                parent.update(
                    status="GAP",
                    feedback="无法继续拆分（粒度、深度或任务数量上限）：" + reason[:4000],
                )
                return
            indices = []
            for refs in children:
                child = {
                    "index": len(current["chunks"]),
                    "parent": index,
                    "children": [],
                    "depth": parent["depth"] + 1,
                    "focus": refs,
                    "status": "PENDING",
                    "attempts": 0,
                    "extra_context": list(parent["extra_context"]),
                    "feedback": None,
                    "reading": None,
                    "audit": None,
                }
                indices.append(child["index"])
                current["chunks"].append(child)
            parent.update(status="SPLIT", children=indices, feedback=reason[:4000])

    def put(self, key, name, value):
        p = self.directory(key) / name
        if p.resolve() != p or not p.is_relative_to(self.directory(key)):
            raise ValueError("Invalid paper artifact path")
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            if read_json(p) != value:
                raise ValueError("Paper artifacts are immutable")
        else:
            atomic(p, value)
        with self.edit(key) as row:
            row["artifacts"][name] = file_hash(p)
        return name

    def artifact(self, row, name):
        if not isinstance(name, str) or name not in row["artifacts"]:
            raise ValueError("Unregistered paper artifact")
        p = self.directory(row["job_id"]) / name
        if (
            p.resolve() != p
            or not p.is_relative_to(self.directory(row["job_id"]))
            or file_hash(p) != row["artifacts"][name]
        ):
            raise ValueError("Paper artifact bytes changed")
        return read_json(p)

    def source(self, row):
        value = self.artifact(row, "source.json")
        if digest(value) != row["source_fingerprint"]:
            raise ValueError("Fixed paper source changed")
        return value

    def current(self, row):
        if implementation() != row["implementation"]:
            raise ValueError("Automation implementation changed; create a new job")
        with metadata_lease(self.documents.directory(row["document_id"])):
            doc, _ = self.documents.checked_inputs(row["document_id"])
            if (
                doc["input_fingerprint"] != row["document_input"]
                or doc["revision"] != row["document_revision"]
            ):
                raise ValueError("Document revision changed; create a new job")
            if (
                row["source_fingerprint"]
                and digest(self.documents.draft(row["document_id"])) != row["source_fingerprint"]
            ):
                raise ValueError("Extracted source changed; create a new job")
        return doc

    def action(self, key, action, raw):
        exact_object(raw, {"request_id", "expected_revision"}, "Paper job action")
        rid = request_id(raw["request_id"])
        if action not in ("cancel", "resume"):
            raise ValueError("Unknown automatic paper action")
        with self.edit(key) as row:
            fp = digest({"action": action, **raw})
            if rid in row["actions"]:
                if row["actions"][rid] != fp:
                    raise ValueError("Action request ID belongs to another action")
            else:
                if (
                    type(raw["expected_revision"]) is not int
                    or raw["expected_revision"] != row["revision"]
                ):
                    raise ValueError("Paper job changed; reload before acting")
                if len(row["actions"]) >= 128:
                    raise ValueError("Paper job action limit reached")
                if action == "cancel":
                    if row["state"] in (*ACTIVE, *RESUMABLE):
                        row["cancel_requested"] = True
                        if row["state"] != "RUNNING":
                            row["state"] = "CANCELLED"
                else:
                    if row["schema"] != SCHEMA:
                        raise ValueError(
                            "Historical v1 jobs cannot resume under v2; create a new job"
                        )
                    if row["state"] not in RESUMABLE:
                        raise ValueError("Only a paused or interrupted job can resume")
                    self.current(row)
                    row.update(state="QUEUED", error=None, active=None)
                row["actions"][rid] = fp
        return self.report(key)

    def report(self, key):
        from .paper_kernel import validate_receipt

        row = self.read(key)
        result = deepcopy(row)
        result.pop("implementation")
        result.pop("actions")
        result["implementation_fingerprint"] = digest(row["implementation"])
        result.update(
            math_verdict="UNKNOWN",
            evidence_level="E0",
            whole_article_verified=False,
            semantic_review="MODEL_AUDIT_ONLY",
            human_confirmation=None,
            stale=False,
        )
        result["coverage_scope"] = "EXTRACTED_TEXT_ONLY"
        result["extraction_fidelity_certified"] = False
        result["original_semantic_alignment_certified"] = False
        result["legacy_job"] = row["schema"] == LEGACY_SCHEMA
        result["accounting"] = paper_budget.totals(row) if not result["legacy_job"] else None
        try:
            self.current(row)
            for name in row["artifacts"]:
                p = self.directory(key) / name
                if (
                    p.resolve() != p
                    or not p.is_relative_to(self.directory(key))
                    or file_hash(p) != row["artifacts"][name]
                ):
                    raise ValueError("Paper artifact bytes changed")
        except (OSError, ValueError, KeyError, TypeError) as error:
            result.update(stale=True, availability_error=str(error)[:1000])
        source = self.source(row) if row["source_fingerprint"] and not result["stale"] else None
        result["source"] = source
        result["structure_index"] = (
            self.artifact(row, row["structure"]) if source and row.get("structure") else None
        )
        coverage, items = [], []
        result["reading_audits"] = []
        if source:
            for chunk in row["chunks"]:
                if chunk.get("children"):
                    continue
                readings = self.artifact(row, chunk["reading"]) if chunk["reading"] else None
                accepted = chunk["status"] == "ACCEPTED"
                by_id = {x["unit_id"]: x for x in readings["coverage"]} if accepted else {}
                if chunk.get("audit"):
                    result["reading_audits"].append(
                        {"chunk": chunk["index"], "audit": self.artifact(row, chunk["audit"])}
                    )
                for unit in self.focus(row, chunk, source):
                    coverage.append(
                        {
                            "unit_id": unit["id"],
                            "block_id": unit["block_id"],
                            "span": unit["span"],
                            "reading_status": chunk["status"],
                            "reading_accepted": accepted,
                            "chunk": chunk["index"],
                            **by_id.get(
                                unit["id"],
                                {
                                    "disposition": "UNRESOLVED",
                                    "reason": chunk["feedback"] or "尚未完成自动阅读",
                                    "obligations": [],
                                },
                            ),
                        }
                    )
        for item in row["items"].values():
            view = deepcopy(item)
            if not result["stale"]:
                view["obligation"] = self.artifact(row, item["obligation"])
                if item.get("candidate"):
                    view["candidate"] = self.artifact(row, item["candidate"])
                if item.get("semantic_audit"):
                    view["component_audit"] = self.artifact(row, item["semantic_audit"])
                if item.get("target_contract"):
                    view["locked_target"] = self.artifact(row, item["target_contract"])
                if item.get("receipt"):
                    receipt = self.artifact(row, item["receipt"])
                    try:
                        validate_receipt(
                            self.directory(key) / item["kernel_directory"],
                            view["candidate"],
                            receipt,
                        )
                        view["kernel_receipt"] = receipt
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        view.update(status="EVIDENCE_UNAVAILABLE", error=str(error)[:1000])
            if result["stale"] and view["status"] == "KERNEL_CHECKED":
                view["status"] = "EVIDENCE_UNAVAILABLE"
            items.append(view)
        result["items"] = items
        result["coverage"] = coverage
        result["summary"] = {
            "source_blocks": len(source["blocks"]) if source else None,
            "source_characters": sum(len(b["text"]) for b in source["blocks"]) if source else None,
            "reading_units": len(coverage) if source else None,
            "reading_leaves": sum(not c.get("children") for c in row["chunks"]),
            "split_chunks": sum(bool(c.get("children")) for c in row["chunks"]),
            "reading_units_accepted": sum(c["reading_accepted"] for c in coverage),
            "obligations": len(items),
            "kernel_candidates_checked": sum(i["status"] == "KERNEL_CHECKED" for i in items),
            "obligations_with_gaps": sum(
                i["status"] not in ("KERNEL_CHECKED", "READY", "RUNNING") for i in items
            ),
            "semantic_confirmation_required": True,
        }
        return result

    def listing(self):
        # Compact inventory; detail/export validates all historical evidence.
        return [
            {
                k: row[k]
                for k in ("job_id", "document_id", "title", "state", "revision", "created_at")
            }
            for row in (self.read(p.name) for p in self.paths())
        ]

    def capabilities(self):
        return capabilities(self.root)
