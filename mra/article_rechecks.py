"""Durable background reads of linked evidence; observations are historical, not authority."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from . import article_audit, article_source
from .article_audit import ArticleAuditStore, annotation_identity, evidence_identity
from .article_evidence_reader import (
    DEFAULT_TIMEOUT_SECONDS,
    ArticleEvidenceReader,
    ArticleEvidenceReadCancelled,
)
from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .model_service import text_field
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier, _now

SCHEMA = "article-evidence-rechecks-v1"
ACTIVE = ("QUEUED", "RUNNING")
STATES = (*ACTIVE, "COMPLETED", "CANCELLED", "INTERRUPTED", "FAILED", "STALE")
LIMITS = {"jobs": 200, "per_article": 20, "active": 8, "graphs": 64, "links": 64, "bytes": 2097152}
ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error)
LINK_STATES = ("MATCHED_AS_OF_CHECK", "STALE_BINDING", "DIFFERENT_EVIDENCE", "UNAVAILABLE", "BUSY")


def implementation():
    return digest(
        {
            "schema": SCHEMA,
            "files": {
                "article_rechecks": file_hash(Path(__file__).resolve()),
                "article_audit": file_hash(Path(article_audit.__file__).resolve()),
                "article_source": file_hash(Path(article_source.__file__).resolve()),
                "article_evidence_reader": file_hash(
                    Path(__file__).with_name("article_evidence_reader.py")
                ),
                "article_evidence_reader_worker": file_hash(
                    Path(__file__).with_name("article_evidence_reader_worker.py")
                ),
            },
        }
    )


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid article recheck fingerprint")


def snapshot(articles, article_id):
    row, source, _ = articles.read(article_id)
    if type(row["revision"]) is not int or row["revision"] < 0:
        raise ValueError("Invalid article revision")
    if not isinstance(row["links"], list) or len(row["links"]) > LIMITS["links"]:
        raise ValueError("Article linked-evidence budget exceeded")
    refs, seen = [], set()
    for link in row["links"]:
        identifier(link["link_id"], "articlelink")
        if link["link_id"] in seen or link["backend"] not in article_audit.BACKENDS:
            raise ValueError("Invalid or duplicate linked-evidence identity")
        seen.add(link["link_id"])
        for name in ("graph_id", "node_id"):
            if not isinstance(link[name], str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", link[name]
            ):
                raise ValueError("Invalid linked verifier identifier")
        if link["block_id"] not in row["annotations"]:
            raise ValueError("Linked evidence has no classified article obligation")
        annotation_identity(row, link["block_id"])
        refs.append(
            {
                "link_id": link["link_id"],
                "backend": link["backend"],
                "graph_id": link["graph_id"],
                "node_id": link["node_id"],
                "block_id": link["block_id"],
                "binding_fingerprint": digest(link),
            }
        )
    value = {
        "article_id": article_id,
        "revision": row["revision"],
        "source_fingerprint": row["source_fingerprint"],
        "source_sha256": source["sha256"],
        "inventory_fingerprint": row["inventory_fingerprint"],
        "record_fingerprint": digest(row),
        "links": refs,
    }
    return value, row


def groups_for(value):
    groups = []
    for ref in value["links"]:
        group = next(
            (
                g
                for g in groups
                if (g["backend"], g["graph_id"]) == (ref["backend"], ref["graph_id"])
            ),
            None,
        )
        if group is None:
            group = {"backend": ref["backend"], "graph_id": ref["graph_id"], "link_ids": []}
            groups.append(group)
        group["link_ids"].append(ref["link_id"])
    return groups


class ArticleRecheckStore:
    """Owns only job progress and historical references to original provider reads.

    A pump holds worker_session for its full lifetime, including graph boundaries and
    idle periods. Its owner lease is independent from mathematical checker locks.
    Neither a job's completion nor polling can authorize a mathematical conclusion.
    """

    def __init__(self, root, *, articles=None, reporter=None):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/article-rechecks"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Article recheck storage cannot be redirected")
        self.articles = articles or ArticleAuditStore(self.root)
        # Explicitly injected providers retain their in-process test/embedding
        # contract. Production uses the fixed, bounded original-provider reader.
        self.reporter = reporter
        self.stop_requested = lambda: False
        self._local = threading.local()

    def directory(self, job_id):
        path = self.base / identifier(job_id, "articlecheck")
        if self.base.resolve() != self.base or path.resolve() != path:
            raise ValueError("Article recheck storage cannot be redirected")
        return path

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Article recheck storage cannot be redirected")
        paths = sorted(self.base.glob("articlecheck-*"))
        if len(paths) > LIMITS["jobs"]:
            raise ValueError("Article recheck job budget exceeded")
        for path in paths:
            self.directory(path.name)
        return paths

    @staticmethod
    def envelope(path):
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError("Article recheck file was redirected")
        if path.stat().st_size > LIMITS["bytes"]:
            raise ValueError("Article recheck file exceeds 2 MiB")
        return _envelope(path)

    def _snapshot(self, job_id):
        value = self.envelope(self.directory(job_id) / "snapshot.json")
        exact_object(
            value,
            {
                "article_id",
                "revision",
                "source_fingerprint",
                "source_sha256",
                "inventory_fingerprint",
                "record_fingerprint",
                "links",
            },
            "Article recheck snapshot",
        )
        identifier(value["article_id"], "article")
        if type(value["revision"]) is not int or value["revision"] < 0:
            raise ValueError("Invalid recheck article revision")
        for name in (
            "source_fingerprint",
            "source_sha256",
            "inventory_fingerprint",
            "record_fingerprint",
        ):
            _sha(value[name])
        if not isinstance(value["links"], list) or len(value["links"]) > LIMITS["links"]:
            raise ValueError("Invalid recheck link budget")
        seen = set()
        for ref in value["links"]:
            exact_object(
                ref,
                {"link_id", "backend", "graph_id", "node_id", "block_id", "binding_fingerprint"},
                "Article recheck link reference",
            )
            identifier(ref["link_id"], "articlelink")
            if ref["link_id"] in seen or ref["backend"] not in article_audit.BACKENDS:
                raise ValueError("Invalid recheck link identity")
            seen.add(ref["link_id"])
            for name in ("graph_id", "node_id"):
                if not isinstance(ref[name], str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", ref[name]
                ):
                    raise ValueError("Invalid recheck verifier reference")
            text_field(ref["block_id"], "Article source block", 4000)
            _sha(ref["binding_fingerprint"])
        return value

    def _observations(self, row):
        values = []
        refs = {ref["link_id"]: ref for ref in self._snapshot(row["job_id"])["links"]}
        for ordinal, reference in enumerate(row["observations"]):
            exact_object(reference, {"group_index", "file", "sha256"}, "Recheck report reference")
            name = f"group-{ordinal:03d}.json"
            if reference["group_index"] != ordinal or reference["file"] != name:
                raise ValueError("Article recheck report order or path changed")
            _sha(reference["sha256"])
            path = self.directory(row["job_id"]) / name
            value = self.envelope(path)
            if file_hash(path) != reference["sha256"]:
                raise ValueError("Historical recheck report changed")
            self._validate_observation(value, row["groups"][ordinal], ordinal, refs)
            values.append(value)
        return values

    @staticmethod
    def _validate_observation(value, group, ordinal, refs):
        exact_object(
            value,
            {
                "group_index",
                "backend",
                "graph_id",
                "checked_at",
                "status",
                "provider_called",
                "provider_report_sha256",
                "links",
                "error",
            },
            "Historical provider observation",
        )
        if (
            value["group_index"] != ordinal
            or (value["backend"], value["graph_id"]) != (group["backend"], group["graph_id"])
            or value["status"] not in ("RETURNED", "BUSY", "UNAVAILABLE", "SKIPPED_STALE")
            or type(value["provider_called"]) is not bool
        ):
            raise ValueError("Historical provider observation identity changed")
        text_field(value["checked_at"], "Provider observation time", 80)
        if value["provider_report_sha256"] is not None:
            _sha(value["provider_report_sha256"])
        if (value["status"] == "SKIPPED_STALE") != (not value["provider_called"]) or (
            value["status"] == "RETURNED"
        ) != (value["provider_report_sha256"] is not None):
            raise ValueError("Historical provider lifecycle changed")
        if value["error"] is not None:
            text_field(value["error"], "Provider observation error", 2000)
        if (
            not isinstance(value["links"], list)
            or [x["link_id"] for x in value["links"]] != group["link_ids"]
        ):
            raise ValueError("Historical provider link set changed")
        for link in value["links"]:
            exact_object(
                link,
                {"link_id", "block_id", "node_id", "status", "reason", "evidence_reference"},
                "Historical link observation",
            )
            if link["status"] not in LINK_STATES:
                raise ValueError("Unknown historical link observation status")
            expected = refs[link["link_id"]]
            if (link["block_id"], link["node_id"]) != (expected["block_id"], expected["node_id"]):
                raise ValueError("Historical source or verifier node identity changed")
            if value["status"] == "SKIPPED_STALE" and link["status"] != "STALE_BINDING":
                raise ValueError("A skipped graph cannot contain checked evidence")
            if value["status"] in ("BUSY", "UNAVAILABLE") and link["status"] not in (
                value["status"],
                "STALE_BINDING",
            ):
                raise ValueError("A failed provider cannot contain matching evidence")
            for name in ("block_id", "node_id", "reason"):
                text_field(
                    link[name], "Historical link " + name, 4000 if name == "block_id" else 2000
                )
            reference = link["evidence_reference"]
            if reference is not None:
                exact_object(
                    reference,
                    {
                        "attempt_id",
                        "source_fingerprint",
                        "plan_fingerprint",
                        "formal_statement_sha256",
                        "artifact_hashes_digest",
                    },
                    "Historical evidence reference",
                )
                for name in ("attempt_id", "source_fingerprint", "plan_fingerprint"):
                    text_field(reference[name], "Evidence reference " + name, 256)
                _sha(reference["formal_statement_sha256"])
                _sha(reference["artifact_hashes_digest"])
            if (link["status"] == "MATCHED_AS_OF_CHECK") != (reference is not None):
                raise ValueError("Historical matching observation lost its exact reference")

    @staticmethod
    def summarize(row, observations):
        links = [link for value in observations for link in value["links"]]
        return {
            "checked_graphs": len(observations),
            "total_graphs": len(row["groups"]),
            "checked_links": len(links),
            "total_links": sum(len(group["link_ids"]) for group in row["groups"]),
            "matched_links": sum(link["status"] == "MATCHED_AS_OF_CHECK" for link in links),
            "issue_links": sum(link["status"] != "MATCHED_AS_OF_CHECK" for link in links),
            "busy_graphs": sum(value["status"] == "BUSY" for value in observations),
            "unavailable_graphs": sum(value["status"] == "UNAVAILABLE" for value in observations),
            "provider_reports_read": sum(value["provider_called"] for value in observations),
        }

    def read(self, job_id):
        row = self.envelope(self.directory(job_id) / "record.json")
        exact_object(
            row,
            {
                "schema",
                "job_id",
                "article_id",
                "request_id",
                "request_fingerprint",
                "implementation_fingerprint",
                "snapshot_fingerprint",
                "created_at",
                "updated_at",
                "started_at",
                "completed_at",
                "state",
                "cancel_requested",
                "owner_id",
                "active_group",
                "next_group",
                "groups",
                "observations",
                "summary",
                "error",
                "revision",
            },
            "Article recheck job",
        )
        value = self._snapshot(job_id)
        if (
            row["schema"] != SCHEMA
            or row["job_id"] != job_id
            or row["article_id"] != value["article_id"]
            or row["snapshot_fingerprint"] != digest(value)
            or row["groups"] != groups_for(value)
        ):
            raise ValueError("Article recheck job or snapshot identity changed")
        _identifier(row["request_id"])
        for name in ("request_fingerprint", "implementation_fingerprint", "snapshot_fingerprint"):
            _sha(row[name])
        if row["request_fingerprint"] != digest(
            {
                "article_id": row["article_id"],
                "request": {
                    "request_id": row["request_id"],
                    "expected_revision": value["revision"],
                },
            }
        ):
            raise ValueError("Article recheck request identity changed")
        if (
            row["state"] not in STATES
            or type(row["cancel_requested"]) is not bool
            or type(row["revision"]) is not int
            or row["revision"] < 0
            or type(row["next_group"]) is not int
            or not 0 <= row["next_group"] <= len(row["groups"])
            or not isinstance(row["observations"], list)
            or len(row["observations"]) != row["next_group"]
        ):
            raise ValueError("Invalid article recheck lifecycle or progress")
        for name in ("created_at", "updated_at", "started_at", "completed_at"):
            if row[name] is not None:
                text_field(row[name], "Recheck " + name, 80)
        if row["created_at"] is None or row["updated_at"] is None:
            raise ValueError("Article recheck creation/update times are required")
        if row["owner_id"] is not None:
            identifier(row["owner_id"], "articleowner")
        active = row["active_group"]
        if active is not None and (
            type(active) is not int
            or active != row["next_group"]
            or active >= len(row["groups"])
            or row["state"] != "RUNNING"
        ):
            raise ValueError("Invalid active article recheck graph")
        if row["state"] == "QUEUED" and (
            row["owner_id"] is not None or row["next_group"] or row["started_at"] is not None
        ):
            raise ValueError("A queued recheck cannot contain prior execution")
        if row["state"] == "RUNNING" and (row["owner_id"] is None or row["started_at"] is None):
            raise ValueError("A running recheck needs a live owner identity")
        if row["state"] not in ACTIVE and (active is not None or row["completed_at"] is None):
            raise ValueError("Terminal article recheck has incomplete lifecycle metadata")
        if row["state"] in ACTIVE and row["completed_at"] is not None:
            raise ValueError("An active article recheck cannot have a completion time")
        if row["state"] == "COMPLETED" and row["next_group"] != len(row["groups"]):
            raise ValueError("Completed article recheck is missing graph observations")
        if row["state"] == "COMPLETED" and row["cancel_requested"]:
            raise ValueError("Cancelled article recheck cannot publish a completed summary")
        if row["error"] is not None:
            exact_object(row["error"], {"code", "message", "group_index"}, "Recheck error")
            for name in ("code", "message"):
                text_field(row["error"][name], "Recheck error " + name, 2000)
            index = row["error"]["group_index"]
            if index is not None and (
                type(index) is not int or not 0 <= index < len(row["groups"])
            ):
                raise ValueError("Invalid failed recheck graph location")
        observations = self._observations(row)
        expected = self.summarize(row, observations) if row["state"] == "COMPLETED" else None
        if expected is not None:
            exact_object(row["summary"], set(expected), "Recheck summary")
            if any(type(row["summary"][name]) is not int for name in expected):
                raise ValueError("Invalid saved recheck summary counts")
        if row["summary"] != expected:
            raise ValueError("Saved article recheck summary changed")
        return row

    def _save(self, row):
        row["updated_at"] = _now()
        row["revision"] += 1
        atomic(self.directory(row["job_id"]) / "record.json", row)

    @staticmethod
    def _finish(row, state, code=None, message=None):
        row.update(state=state, active_group=None, completed_at=_now(), summary=None)
        row["error"] = (
            {
                "code": code,
                "message": message[:2000],
                "group_index": row["next_group"]
                if row["next_group"] < len(row["groups"])
                else None,
            }
            if code
            else None
        )

    def create(self, article_id, data):
        identifier(article_id, "article")
        exact_object(data, {"request_id", "expected_revision"}, "Create linked-evidence recheck")
        _identifier(data["request_id"])
        if type(data["expected_revision"]) is not int or data["expected_revision"] < 0:
            raise ValueError("Expected article revision must be a nonnegative integer")
        fingerprint = digest({"article_id": article_id, "request": data})
        current_implementation = implementation()
        with lease(self.base):
            rows = [self.read(path.name) for path in self.paths()]
            old = next((row for row in rows if row["request_id"] == data["request_id"]), None)
            if old is not None:
                if old["request_fingerprint"] != fingerprint:
                    raise ValueError("Recheck request identifier belongs to different content")
                return self.report(article_id, old["job_id"])
            if (
                len(rows) >= LIMITS["jobs"]
                or sum(row["article_id"] == article_id for row in rows) >= LIMITS["per_article"]
                or sum(
                    row["state"] in ACTIVE
                    and row["implementation_fingerprint"] == current_implementation
                    for row in rows
                )
                >= LIMITS["active"]
            ):
                raise ValueError("Article recheck job capacity exhausted")
            with lease(self.articles.directory(article_id)):
                value, _ = snapshot(self.articles, article_id)
                if value["revision"] != data["expected_revision"]:
                    raise ValueError("Article changed; reload before requesting a recheck")
                key = "articlecheck-" + uuid.uuid4().hex
                directory = self.directory(key)
                staging = self.base / (".creating-" + key)
                if staging.resolve() != staging or staging.exists() or directory.exists():
                    raise ValueError("Article recheck publication path changed")
                staging.mkdir()
                row = {
                    "schema": SCHEMA,
                    "job_id": key,
                    "article_id": article_id,
                    "request_id": data["request_id"],
                    "request_fingerprint": fingerprint,
                    "implementation_fingerprint": current_implementation,
                    "snapshot_fingerprint": digest(value),
                    "created_at": _now(),
                    "updated_at": _now(),
                    "started_at": None,
                    "completed_at": None,
                    "state": "QUEUED",
                    "cancel_requested": False,
                    "owner_id": None,
                    "active_group": None,
                    "next_group": 0,
                    "groups": groups_for(value),
                    "observations": [],
                    "summary": None,
                    "error": None,
                    "revision": 0,
                }
                atomic(staging / "snapshot.json", value)
                atomic(staging / "record.json", row)
                staging.rename(directory)
                fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return self.report(article_id, key)

    def _snapshot_state(self, row):
        try:
            value, _ = snapshot(self.articles, row["article_id"])
            return {
                "status": "MATCHES_AT_READ"
                if digest(value) == row["snapshot_fingerprint"]
                else "CHANGED",
                "revision_at_read": value["revision"],
                "reason": None,
            }
        except ERRORS as error:
            return {"status": "UNAVAILABLE", "revision_at_read": None, "reason": str(error)[:1000]}

    def _view(self, row):
        state = self._snapshot_state(row)
        implementation_status = (
            "MATCHES_AT_READ"
            if row["implementation_fingerprint"] == implementation()
            else "CHANGED"
        )
        frozen = self._snapshot(row["job_id"])
        observations = self._observations(row)
        progress = self.summarize(row, observations)
        active = row["active_group"]
        return {
            "schema": SCHEMA,
            "job_id": row["job_id"],
            "article_id": row["article_id"],
            "request_id": row["request_id"],
            "state": row["state"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "cancel_requested": row["cancel_requested"],
            "active_graph": deepcopy(row["groups"][active]) if active is not None else None,
            "progress": {
                "completed_graphs": progress["checked_graphs"],
                "total_graphs": progress["total_graphs"],
                "checked_links": progress["checked_links"],
                "total_links": progress["total_links"],
            },
            "implementation_fingerprint": row["implementation_fingerprint"],
            "implementation_status": implementation_status,
            "snapshot_status": state["status"],
            "snapshot_observation": state,
            "snapshot_revision": frozen["revision"],
            "snapshot_fingerprint": row["snapshot_fingerprint"],
            "summary": deepcopy(row["summary"])
            if state["status"] == implementation_status == "MATCHES_AT_READ"
            else None,
            "error": deepcopy(row["error"]),
            "result_available": row["state"] == "COMPLETED",
            "error_graph": deepcopy(row["groups"][row["error"]["group_index"]])
            if row["error"] and row["error"]["group_index"] is not None
            else None,
            "as_of_only": True,
            "fresh_attestation": False,
            "evidence_authority": False,
            "model_calls": 0,
            "new_lean_executions": 0,
            "read_control": {
                "mode": "ISOLATED_PROCESS" if self.reporter is None else "IN_PROCESS_PROVIDER",
                "applicable_to_job_implementation": implementation_status == "MATCHES_AT_READ",
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS if self.reporter is None else None,
                "cancel_during_read": self.reporter is None,
            },
            "scope": "Historical linked-evidence observations; polling never grants current mathematical evidence",
        }

    def report(self, article_id, job_id):
        identifier(article_id, "article")
        row = self.read(job_id)
        if row["article_id"] != article_id:
            raise ValueError("Recheck job belongs to another article")
        return self._view(row)

    def listing(self, article_id):
        identifier(article_id, "article")
        self.articles.read(article_id)
        rows = [self.read(path.name) for path in self.paths()]
        return {
            "schema": SCHEMA,
            "jobs": [
                self._view(row)
                for row in sorted(
                    rows, key=lambda item: (item["created_at"], item["job_id"]), reverse=True
                )
                if row["article_id"] == article_id
            ],
            "as_of_only": True,
            "fresh_attestation": False,
        }

    def result(self, article_id, job_id):
        row = self.read(job_id)
        identifier(article_id, "article")
        if row["article_id"] != article_id:
            raise ValueError("Recheck job belongs to another article")
        job = self._view(row)
        result = None
        if row["state"] == "COMPLETED":
            result = {
                "job_id": job_id,
                "article_id": article_id,
                "as_of_only": True,
                "fresh_attestation": False,
                "implementation_fingerprint": row["implementation_fingerprint"],
                "implementation_status": job["implementation_status"],
                "applicable_to_current_implementation": job["implementation_status"]
                == "MATCHES_AT_READ",
                "snapshot_status": job["snapshot_status"],
                "applicable_to_snapshot_at_read": job["snapshot_status"] == "MATCHES_AT_READ",
                "snapshot": self._snapshot(job_id),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "summary": deepcopy(row["summary"]),
                "groups": self._observations(row),
                "whole_article_verified": False,
                "evidence_authority": False,
                "scope": "Each graph was read at its recorded time; this archive is not a current aggregate or proof authority",
            }
        return {"job": job, "result": result}

    def cancel(self, article_id, job_id, data):
        exact_object(data, set(), "Cancel article recheck")
        with lease(self.directory(job_id)):
            row = self.read(job_id)
            if row["article_id"] != article_id:
                raise ValueError("Recheck job belongs to another article")
            if row["state"] in ACTIVE and not row["cancel_requested"]:
                row["cancel_requested"] = True
                if row["active_group"] is None:
                    self._finish(row, "CANCELLED")
                self._save(row)
        return self.report(article_id, job_id)

    @contextmanager
    def worker_session(self):
        if getattr(self._local, "owner_id", None) is not None:
            raise WorkerBusy("This thread already owns an article recheck session")
        if self.base.resolve() != self.base:
            raise ValueError("Article recheck worker storage was redirected")
        fd = os.open(self.base / ".owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerBusy("A live article recheck pump owns the lifecycle lease") from error
            self._local.owner_id = "articleowner-" + uuid.uuid4().hex
            self._local.owner_fd = fd
            yield
        finally:
            self._local.owner_id = None
            self._local.owner_fd = None
            os.close(fd)

    @contextmanager
    def _owned(self):
        if getattr(self._local, "owner_id", None) is not None:
            yield
        else:
            with self.worker_session():
                yield

    @staticmethod
    def _interrupt_implementation_change(row):
        if row["state"] in ACTIVE and row["implementation_fingerprint"] != implementation():
            ArticleRecheckStore._finish(
                row,
                "CANCELLED" if row["cancel_requested"] else "INTERRUPTED",
                "IMPLEMENTATION_CHANGED",
                "Recheck implementation changed; historical observations remain available and an explicit new job is required",
            )
            return True
        return False

    def recover(self):
        if getattr(self._local, "in_step", False):
            raise WorkerBusy("A provider read is still executing in this session")
        with self._owned():
            changed = []
            for path in self.paths():
                with lease(path):
                    row = self.read(path.name)
                    if self._interrupt_implementation_change(row):
                        self._save(row)
                        changed.append(row["job_id"])
                    elif row["state"] == "RUNNING":
                        state = "CANCELLED" if row["cancel_requested"] else "INTERRUPTED"
                        self._finish(
                            row,
                            state,
                            "WORKER_INTERRUPTED",
                            "Previous pump ended; no provider report is replayed or automatically retried",
                        )
                        self._save(row)
                        changed.append(row["job_id"])
            return {"interrupted_jobs": changed, "automatic_retry": False}

    def _read_group(self, row, article_row, index):
        group = row["groups"][index]
        by_id = {link["link_id"]: link for link in article_row["links"]}
        links = [by_id[key] for key in group["link_ids"]]
        observations, live = {}, []
        for link in links:
            base = {
                "link_id": link["link_id"],
                "block_id": link["block_id"],
                "node_id": link["node_id"],
                "status": "STALE_BINDING",
                "reason": "Original interpretation or a prerequisite changed; review and rebind",
                "evidence_reference": None,
            }
            if link["source_fingerprint"] == article_row["source_fingerprint"] and link[
                "annotation_identity"
            ] == annotation_identity(article_row, link["block_id"]):
                live.append(link)
            observations[link["link_id"]] = base
        value = {
            "group_index": index,
            "backend": group["backend"],
            "graph_id": group["graph_id"],
            "checked_at": _now(),
            "status": "SKIPPED_STALE",
            "provider_called": bool(live),
            "provider_report_sha256": None,
            "links": [],
            "error": None,
        }
        if live:
            try:
                if self.reporter is None:

                    def cancelled():
                        if self.stop_requested():
                            return True
                        current = self.read(row["job_id"])
                        return (
                            current["cancel_requested"]
                            or current["implementation_fingerprint"] != implementation()
                        )

                    report = ArticleEvidenceReader(
                        self.root, group["backend"], group["graph_id"]
                    ).run(
                        cancel_requested=cancelled,
                        lifecycle_lock_fd=self._local.owner_fd,
                    )
                else:
                    report = self.reporter(group["backend"], group["graph_id"])
                if not isinstance(report, dict):
                    raise ValueError("Verifier provider did not return a report object")
                value.update(status="RETURNED", provider_report_sha256=digest(report))
                for link in live:
                    observation = observations[link["link_id"]]
                    try:
                        current = evidence_identity(report, link["node_id"])
                        if current != link["identity"]:
                            observation.update(
                                status="DIFFERENT_EVIDENCE",
                                reason="Exact attempt, target or proof reference changed; review and rebind",
                            )
                        else:
                            observation.update(
                                status="MATCHED_AS_OF_CHECK",
                                reason="Existing exact evidence matched when this provider report was read; no new Lean execution",
                                evidence_reference={
                                    "attempt_id": current["attempt_id"],
                                    "source_fingerprint": current["source_fingerprint"],
                                    "plan_fingerprint": current["plan_fingerprint"],
                                    "formal_statement_sha256": hashlib.sha256(
                                        current["formal_statement"].encode()
                                    ).hexdigest(),
                                    "artifact_hashes_digest": digest(current["artifact_hashes"]),
                                },
                            )
                    except ERRORS as error:
                        observation.update(status="UNAVAILABLE", reason=str(error)[:2000])
            except ArticleEvidenceReadCancelled:
                # No observation of an incomplete or discarded read is saved.
                raise
            except ERRORS as error:
                status = "BUSY" if isinstance(error, WorkerBusy) else "UNAVAILABLE"
                value.update(status=status, error=type(error).__name__ + ": " + str(error)[:1800])
                for link in live:
                    observations[link["link_id"]].update(status=status, reason=value["error"])
        value["checked_at"] = _now()
        value["links"] = [observations[link["link_id"]] for link in links]
        return value

    def _complete(self, job_id):
        # Only this final short section holds article + job locks, in that order.
        # Provider reads never run under either lock or a global checker lease.
        initial = self.read(job_id)
        with lease(self.articles.directory(initial["article_id"])):
            with lease(self.directory(job_id)):
                row = self.read(job_id)
                if row["state"] != "RUNNING":
                    return
                if self._interrupt_implementation_change(row):
                    self._save(row)
                    return
                if row["cancel_requested"]:
                    self._finish(row, "CANCELLED")
                elif self.stop_requested():
                    self._finish(
                        row,
                        "INTERRUPTED",
                        "READER_STOPPED",
                        "Service stopped before final publication; explicit new job required",
                    )
                else:
                    state = self._snapshot_state(row)
                    if state["status"] != "MATCHES_AT_READ":
                        self._finish(
                            row,
                            "STALE" if state["status"] == "CHANGED" else "FAILED",
                            "ARTICLE_CHANGED"
                            if state["status"] == "CHANGED"
                            else "ARTICLE_UNAVAILABLE",
                            "Article snapshot changed or became unavailable before final publication",
                        )
                    else:
                        summary = self.summarize(row, self._observations(row))
                        # Stop and code changes can arrive during those reads;
                        # neither is protected by the article/job file locks.
                        if self._interrupt_implementation_change(row):
                            pass
                        elif self.stop_requested():
                            self._finish(
                                row,
                                "INTERRUPTED",
                                "READER_STOPPED",
                                "Service stopped before final publication; explicit new job required",
                            )
                        else:
                            row.update(
                                state="COMPLETED",
                                active_group=None,
                                completed_at=_now(),
                                error=None,
                                summary=summary,
                            )
                self._save(row)

    def _work_once(self, job_id):
        if job_id is None:
            rows = [self.read(path.name) for path in self.paths()]
            eligible = sorted(
                (row for row in rows if row["state"] in ACTIVE),
                key=lambda row: (row["created_at"], row["job_id"]),
            )
            if not eligible:
                return {"worker_state": "IDLE"}
            job_id = eligible[0]["job_id"]
        row = self.read(job_id)
        if row["state"] not in ACTIVE:
            return {"worker_state": "IDLE", "job_id": job_id}
        if row["implementation_fingerprint"] != implementation():
            with lease(self.directory(job_id)):
                row = self.read(job_id)
                if self._interrupt_implementation_change(row):
                    self._save(row)
            return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
        if row["state"] == "RUNNING" and (
            row["owner_id"] != self._local.owner_id or row["active_group"] is not None
        ):
            with lease(self.directory(job_id)):
                row = self.read(job_id)
                self._finish(
                    row,
                    "CANCELLED" if row["cancel_requested"] else "INTERRUPTED",
                    "WORKER_INTERRUPTED",
                    "An earlier provider step has no completed commit; explicit new job required",
                )
                self._save(row)
            return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
        with lease(self.articles.directory(row["article_id"])):
            with lease(self.directory(job_id)):
                row = self.read(job_id)
                if row["state"] not in ACTIVE:
                    return {"worker_state": "IDLE", "job_id": job_id}
                if self._interrupt_implementation_change(row):
                    self._save(row)
                    return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
                if row["cancel_requested"]:
                    self._finish(row, "CANCELLED")
                    self._save(row)
                    return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
                try:
                    current, article_row = snapshot(self.articles, row["article_id"])
                    if digest(current) != row["snapshot_fingerprint"]:
                        self._finish(
                            row,
                            "STALE",
                            "ARTICLE_CHANGED",
                            "Article snapshot changed before the next graph read",
                        )
                        self._save(row)
                        return {
                            "worker_state": "EXECUTED",
                            "job_id": job_id,
                            "provider_called": False,
                        }
                except ERRORS as error:
                    self._finish(row, "FAILED", "ARTICLE_UNAVAILABLE", str(error))
                    self._save(row)
                    return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
                index = row["next_group"]
                row.update(
                    state="RUNNING",
                    owner_id=self._local.owner_id,
                    started_at=row["started_at"] or _now(),
                )
                row["active_group"] = index if index < len(row["groups"]) else None
                self._save(row)
        if index == len(row["groups"]):
            self._complete(job_id)
            return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": False}
        try:
            observation = self._read_group(row, article_row, index)
        except ArticleEvidenceReadCancelled:
            with lease(self.directory(job_id)):
                current = self.read(job_id)
                if (
                    current["state"] != "RUNNING"
                    or current["owner_id"] != self._local.owner_id
                    or current["active_group"] != index
                ):
                    raise ValueError("Article recheck ownership changed during cancellation")
                if self._interrupt_implementation_change(current):
                    pass
                elif current["cancel_requested"]:
                    self._finish(current, "CANCELLED")
                else:
                    self._finish(
                        current,
                        "INTERRUPTED",
                        "READER_STOPPED",
                        "Service stopped its current provider read; explicit new job required",
                    )
                self._save(current)
            return {"worker_state": "EXECUTED", "job_id": job_id, "provider_called": True}
        with lease(self.directory(job_id)):
            current = self.read(job_id)
            if (
                current["state"] != "RUNNING"
                or current["owner_id"] != self._local.owner_id
                or current["active_group"] != index
            ):
                raise ValueError("Article recheck step ownership changed before commit")
            if self._interrupt_implementation_change(current):
                pass
            elif current["cancel_requested"]:
                self._finish(current, "CANCELLED")
            elif self.stop_requested():
                self._finish(
                    current,
                    "INTERRUPTED",
                    "READER_STOPPED",
                    "Service stopped before this observation committed; explicit new job required",
                )
            else:
                name = f"group-{index:03d}.json"
                path = self.directory(job_id) / name
                if path.exists():
                    # A crash between observation write and job commit is not replayed.
                    self._finish(
                        current,
                        "INTERRUPTED",
                        "UNCOMMITTED_OBSERVATION",
                        "An uncommitted historical observation already exists; explicit new job required",
                    )
                else:
                    atomic(path, observation)
                    current["observations"].append(
                        {"group_index": index, "file": name, "sha256": file_hash(path)}
                    )
                    current.update(next_group=index + 1, active_group=None)
            self._save(current)
        if current["state"] == "RUNNING" and current["next_group"] == len(current["groups"]):
            try:
                self._complete(job_id)
            except WorkerBusy:
                # The next step only finalizes; it never re-reads the completed provider.
                pass
        return {
            "worker_state": "EXECUTED",
            "job_id": job_id,
            "provider_called": observation["provider_called"],
        }

    def work_once(self, job_id=None):
        if getattr(self._local, "in_step", False):
            return {
                "worker_state": "BUSY",
                "reason": "A provider step is already active in this thread",
            }
        try:
            with self._owned():
                self._local.in_step = True
                try:
                    return self._work_once(job_id)
                finally:
                    self._local.in_step = False
        except WorkerBusy as error:
            return {"worker_state": "BUSY", "reason": str(error)}
