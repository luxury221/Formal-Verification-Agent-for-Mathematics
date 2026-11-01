"""Transactional local review ledger. Actors are assertions, not authenticated identities."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import uuid

from .domain import canonical
from .formalizer import CandidateReview, review_candidate, strict_json, text_field


class ReviewLedger:
    """SQLite is authoritative; JSON event files are recoverable export projections."""

    def __init__(self, base: Path, load_bundle) -> None:
        self.base, self.load_bundle = base, load_bundle
        self.path = base / "review-state.sqlite3"

    @contextmanager
    def connection(self, *, write=False):
        if not self.base.resolve().is_relative_to(self.base.parents[1]):
            raise ValueError("Formalization storage redirects outside the project")
        for suffix in ("", "-journal", "-wal", "-shm"):
            if Path(str(self.path) + suffix).is_symlink():
                raise ValueError("Review database may not be a symlink")
        if not write and not self.path.exists():
            yield None
            return
        self.base.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and write:
                if connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ValueError("Unrecognized review database")
                for sql in (
                    "CREATE TABLE sources (source_id TEXT, version INTEGER, fingerprint TEXT, "
                    "PRIMARY KEY(source_id, version))",
                    "CREATE TABLE heads (source_id TEXT PRIMARY KEY, version INTEGER, "
                    "fingerprint TEXT)",
                    "CREATE TABLE drafts (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                    "source_id TEXT, version INTEGER, source_fingerprint TEXT, "
                    "manifest_hash TEXT, parent TEXT REFERENCES drafts(id), "
                    "FOREIGN KEY(source_id, version) REFERENCES sources(source_id, version))",
                    "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "event_id TEXT UNIQUE NOT NULL, draft_id TEXT NOT NULL REFERENCES drafts(id), "
                    "payload TEXT NOT NULL)",
                    "CREATE INDEX events_by_draft ON events(draft_id, sequence)",
                ):
                    connection.execute(sql)
                connection.execute("PRAGMA user_version=1")
            elif version != 1:
                raise ValueError("Unsupported or incomplete review database version")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _bundle(self, connection, directory):
        draft = self.load_bundle(directory)
        directory = directory.resolve()
        if directory.parent != self.base:
            raise ValueError("Registered draft must be a direct formalization bundle")
        row = connection.execute("SELECT * FROM drafts WHERE id=?", (directory.name,)).fetchone()
        if row is None:
            raise ValueError("Unregistered draft; explicitly register-formalization first")
        if (
            row["fingerprint"] != draft.fingerprint
            or row["source_id"] != draft.source.id
            or row["version"] != draft.source.version
            or row["source_fingerprint"] != draft.source.fingerprint
            or row["manifest_hash"]
            != hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
        ):
            raise ValueError("Draft differs from the registered version")
        return draft, row

    @staticmethod
    def _current(connection, source):
        row = connection.execute("SELECT * FROM heads WHERE source_id=?", (source.id,)).fetchone()
        if row is None:
            raise ValueError("Missing source head")
        return (row["version"], row["fingerprint"]) == (source.version, source.fingerprint), dict(
            row
        )

    def register(self, directory: Path, previous: Path | None = None) -> None:
        with self.connection(write=True) as connection:
            draft = self.load_bundle(directory)
            directory = directory.resolve()
            if directory.parent != self.base:
                raise ValueError("Registered draft must be a direct formalization bundle")
            existing = connection.execute(
                "SELECT * FROM drafts WHERE id=?", (directory.name,)
            ).fetchone()
            if existing:
                self._bundle(connection, directory)
                expected_parent = previous.resolve().name if previous else None
                if existing["parent"] != expected_parent:
                    raise ValueError("Registration replay changes the revision parent")
                return
            source, parent = draft.source, None
            head = connection.execute(
                "SELECT * FROM heads WHERE source_id=?", (source.id,)
            ).fetchone()
            known = connection.execute(
                "SELECT * FROM sources WHERE source_id=? AND version=?", (source.id, source.version)
            ).fetchone()
            if known and known["fingerprint"] != source.fingerprint:
                raise ValueError("Conflicting content for the same source id/version")
            if previous is not None:
                prior, prior_row = self._bundle(connection, previous)
                if not self._current(connection, prior.source)[0]:
                    raise ValueError("Revision parent is no longer current")
                if source != prior.source.revise(source.statement, source.context):
                    raise ValueError("A revision must keep its source id and increment its version")
                parent = prior_row["id"]
            elif head is None:
                if source.version != 1:
                    raise ValueError(
                        "Initial source must be version 1; use --revise for later versions"
                    )
            elif (head["version"], head["fingerprint"]) != (source.version, source.fingerprint):
                raise ValueError("Source is not current; use --revise with the current parent")
            connection.execute(
                "INSERT OR IGNORE INTO sources VALUES (?, ?, ?)",
                (source.id, source.version, source.fingerprint),
            )
            connection.execute(
                "INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    directory.name,
                    draft.fingerprint,
                    source.id,
                    source.version,
                    source.fingerprint,
                    hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
                    parent,
                ),
            )
            connection.execute(
                "INSERT INTO heads VALUES (?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "version=excluded.version, fingerprint=excluded.fingerprint",
                (source.id, source.version, source.fingerprint),
            )

    def _history(self, connection, draft, draft_id):
        events, reviews, revoked = [], {}, set()
        rows = connection.execute(
            "SELECT * FROM events WHERE draft_id=? ORDER BY sequence", (draft_id,)
        )
        for row in rows:
            event = strict_json(row["payload"])
            if event["event_type"] == "REVIEW":
                body = review_candidate(draft, CandidateReview(**event["review"]))
                reviews[row["event_id"]] = event
            elif event["event_type"] == "REVOKE":
                body = self._revocation_body(
                    draft,
                    reviews,
                    revoked,
                    event["target_review_id"],
                    event["reviewer"],
                    event["note"],
                    event["draft_fingerprint"],
                    event["candidate_fingerprint"],
                )
                revoked.add(event["target_review_id"])
            else:
                raise ValueError("Unknown stored review event")
            timestamp = datetime.fromisoformat(event["recorded_at"])
            if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                raise ValueError("Review event timestamp must be UTC")
            expected = {
                **body,
                "event_id": row["event_id"],
                "event_type": event["event_type"],
                "recorded_at": event["recorded_at"],
            }
            if canonical(event) != canonical(expected):
                raise ValueError("Stored review event changed")
            events.append({**event, "sequence": row["sequence"]})
        return events

    @staticmethod
    def _revocation_body(draft, reviews, revoked, target, reviewer, note, draft_fp, candidate_fp):
        text_field(reviewer, "reviewer")
        text_field(note, "note")
        if target not in reviews or target in revoked:
            raise ValueError("Unknown or already revoked review")
        original = reviews[target]["review"]
        if (draft.fingerprint, original["candidate_fingerprint"]) != (draft_fp, candidate_fp):
            raise ValueError("Revocation belongs to another draft/candidate")
        if original["reviewer"] != reviewer:
            raise ValueError("Revocation must name the original reviewer (local assertion only)")
        return {
            "schema_version": "1",
            "target_review_id": target,
            "reviewer": reviewer,
            "note": note,
            "draft_fingerprint": draft_fp,
            "candidate_fingerprint": candidate_fp,
            "review_status": "REVOKED",
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0_CANDIDATE",
            "can_execute_lean": False,
        }

    def record(
        self,
        directory,
        review=None,
        *,
        target=None,
        reviewer=None,
        note=None,
        draft_fingerprint=None,
        candidate_fingerprint=None,
        event_id=None,
    ):
        event_id = uuid.uuid4().hex if event_id is None else event_id
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
            raise ValueError("Event id must contain 1-128 letters, digits, underscores or hyphens")
        with self.connection(write=True) as connection:
            draft, row = self._bundle(connection, directory)
            events = self._history(connection, draft, row["id"])
            existing = connection.execute(
                "SELECT * FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            event_type = "REVIEW" if review is not None else "REVOKE"
            if review is not None:
                body = review_candidate(draft, review)
            else:
                # A retry must be compared before rejecting an already committed revocation.
                reviews = {e["event_id"]: e for e in events if e["event_type"] == "REVIEW"}
                revoked = {
                    e["target_review_id"]
                    for e in events
                    if e["event_type"] == "REVOKE" and e["event_id"] != event_id
                }
                body = self._revocation_body(
                    draft,
                    reviews,
                    revoked,
                    target,
                    reviewer,
                    note,
                    draft_fingerprint,
                    candidate_fingerprint,
                )
            if existing:
                old = strict_json(existing["payload"])
                expected = {
                    **body,
                    "event_id": event_id,
                    "event_type": event_type,
                    "recorded_at": old["recorded_at"],
                }
                if existing["draft_id"] != row["id"] or canonical(old) != canonical(expected):
                    raise ValueError("Event id was already used for different content")
                return {**old, "sequence": existing["sequence"]}
            if review is not None and not self._current(connection, draft.source)[0]:
                raise ValueError("Superseded source cannot receive a new review")
            event = {
                **body,
                "event_id": event_id,
                "event_type": event_type,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            cursor = connection.execute(
                "INSERT INTO events(event_id, draft_id, payload) VALUES (?, ?, ?)",
                (event_id, row["id"], canonical(event).decode()),
            )
            return {**event, "sequence": cursor.lastrowid}

    def report(self, directory):
        with self.connection() as connection:
            draft = self.load_bundle(directory)
            registered = (
                connection is not None
                and connection.execute(
                    "SELECT 1 FROM drafts WHERE id=?", (directory.resolve().name,)
                ).fetchone()
                is not None
            )
            events, current, head, parent = [], False, None, None
            if registered:
                draft, row = self._bundle(connection, directory)
                events = self._history(connection, draft, row["id"])
                current, head = self._current(connection, draft.source)
                parent = row["parent"]
            latest = {}
            revoked = {e["target_review_id"] for e in events if e["event_type"] == "REVOKE"}
            for event in events:
                if event["event_type"] == "REVIEW":
                    latest[event["review"]["candidate_fingerprint"]] = event
            states = []
            for candidate in draft.candidates:
                last = latest.get(candidate.fingerprint)
                recorded = (
                    ("REVOKED" if last["event_id"] in revoked else last["review_status"])
                    if last
                    else "UNREVIEWED"
                )
                status = (
                    "UNREGISTERED" if not registered else (recorded if current else "SUPERSEDED")
                )
                states.append(
                    {
                        "candidate_fingerprint": candidate.fingerprint,
                        "status": status,
                        "last_review_status": recorded,
                        "last_review_id": last["event_id"] if last else None,
                        "effective_review_id": last["event_id"]
                        if status in ("ACCEPTED", "REJECTED")
                        else None,
                        "accepted": status == "ACCEPTED",
                        "math_verdict": "UNKNOWN",
                        "evidence_level": "E0_CANDIDATE",
                        "can_execute_lean": False,
                    }
                )
            return {
                **draft.to_dict(),
                "review_state": {
                    "registered": bool(registered),
                    "source_is_current": current,
                    "source_head": head,
                    "revision_parent": parent,
                    "actor_authentication": "local-assertion-only",
                    "candidates": states,
                    "history": events,
                },
            }
