"""Private, provenance-bound indexing of already checked paper obligations.

Admission never runs Lean or grants new evidence. Every read checks the source
ledger and the archived proof bytes; invalidation is persistent and monotonic.
"""

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .domain import digest, canonical
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .model_service import text_field
from .nat_ideas import target_from_dict
from .paper_review import PaperReviewStore
from .tasks import _identifier, _now

SCHEMA = "private-paper-knowledge-v1"
E4 = "E4_ALIGNED_AND_VERIFIED"
MAX_ITEMS = 500


class KnowledgeStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/knowledge"
        self.proofs = self.base / "proofs"
        self.proofs.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base or self.proofs.resolve() != self.proofs:
            raise ValueError("Knowledge storage must not be redirected")
        self.path = self.base / "knowledge.sqlite3"
        self.papers = PaperReviewStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, body TEXT NOT NULL, body_hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, items TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self):
        if self.path.is_symlink() or self.base.resolve() != self.base:
            raise ValueError("Knowledge database must not be redirected")
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read_rows(db):
        items = {}
        for row in db.execute("SELECT * FROM items ORDER BY rowid"):
            body = strict_json(row["body"])
            if digest(body) != row["body_hash"] or body["item_id"] != row["id"]:
                raise ValueError("Knowledge ledger changed")
            items[row["id"]] = body
        return items

    @staticmethod
    def save(db, body):
        db.execute(
            "INSERT OR REPLACE INTO items VALUES (?, ?, ?)",
            (body["item_id"], canonical(body).decode(), digest(body)),
        )

    @staticmethod
    def event(body, kind, actor, reason):
        body["events"].append(
            {
                "sequence": len(body["events"]) + 1,
                "at": _now(),
                "kind": kind,
                "actor": actor,
                "reason": reason,
            }
        )

    @staticmethod
    def item_id(review_id, key):
        return "knowledge-" + digest({"review_id": review_id, "key": key})[:32]

    def verified_source(self, review_id):
        report = self.papers.report(_identifier(review_id))
        if (
            not report["results_available"]
            or report["artifact_status"] != "MATCHES_RECORDED_HASHES"
        ):
            raise ValueError("The source review has no currently available aligned evidence")
        checks = {check["key"]: check for check in report["checks"]}
        candidates = []
        accepted = set()
        for obligation in report["plan"]["obligations"]:
            key = obligation["key"]
            check = checks.get(key)
            if (
                not check
                or check["result"]["math_verdict"] != "PROVED"
                or check["result"]["evidence_level"] != E4
                or check["result"]["run_status"] != "COMPLETED"
                or any(dep not in accepted for dep in obligation["dependencies"])
            ):
                continue
            data = self.papers.evidence(review_id, key)
            if not data["available"]:
                raise ValueError("Source evidence became unavailable")
            proof = data["report"]
            receipt = data["receipt"]["payload"]
            target = target_from_dict(obligation["target"])
            claim, formalization = target.contract("prove")
            evidence = proof["evidence"]
            if (
                proof["obligation"] != obligation
                or proof["task"] != check["result"]
                or proof["plan_fingerprint"] != report["plan"]["plan_fingerprint"]
                or evidence is None
                or evidence["level"] != E4
                or evidence["claim_fingerprint"] != claim.fingerprint
                or evidence["formalization_fingerprint"] != digest(asdict(formalization))
                or proof["alignment"]["reviewer"] != report["confirmation"]["reviewer"]
                or receipt["candidate_pid"] == receipt["replay"]["pid"]
                or not receipt["replay"]["audit"]["type_matches"]
            ):
                raise ValueError("Source proof, alignment and exact target disagree")
            accepted.add(key)
            source = {
                "kind": "paper-review",
                "review_id": review_id,
                "key": key,
                "document_id": report["bundle"]["document"]["id"],
                "version": report["bundle"]["document"]["version"],
                "plan_fingerprint": report["plan"]["plan_fingerprint"],
                "span": obligation["span"],
                "reviewer": proof["alignment"]["reviewer"],
                "href": "/papers#" + review_id,
            }
            candidates.append(
                {
                    "item_id": self.item_id(review_id, key),
                    "title": report["bundle"]["document"]["title"] + " · " + key,
                    "formal_statement": obligation["formal_statement"],
                    "quote": obligation["quote"],
                    "formula": json.loads(target.formula_json),
                    "domain": target.DOMAIN,
                    "proposition_fingerprint": digest(
                        {"domain": target.DOMAIN, "formula": json.loads(target.formula_json)}
                    ),
                    "source": source,
                    "dependencies": [
                        self.item_id(review_id, dep) for dep in obligation["dependencies"]
                    ],
                    "evidence": evidence,
                    "file_hashes": check["file_hashes"],
                    "environment": {
                        "fingerprint": receipt["environment_fingerprint"],
                        "runtime_digest": report["runtime_snapshot_fingerprint"],
                        "snapshot_sha256": check["file_hashes"]["runtime-snapshot.json"],
                    },
                }
            )
        return candidates

    def prepare(self, review_id):
        candidates = self.verified_source(review_id)
        if not candidates:
            raise ValueError("Only PROVED/E4 paper obligations can enter this private library")
        plan = {
            "schema": SCHEMA,
            "review_id": review_id,
            "items": candidates,
            "privacy": {
                "visibility": "private-local",
                "training_allowed": False,
                "sharing_allowed": False,
            },
            "scope": "Exact historically aligned paper obligations; no new attestation or automatic lemma execution",
        }
        return {**plan, "plan_fingerprint": digest(plan)}

    def archive(self, candidate):
        directory = self.proofs / candidate["item_id"]
        if self.proofs.resolve() != self.proofs:
            raise ValueError("Proof storage was redirected")
        if directory.exists() or directory.is_symlink():
            if self.archive_intact({"item_id": candidate["item_id"], "content": candidate}):
                return
            raise ValueError("An incomplete or changed proof archive already exists")
        source = Path(candidate["evidence"]["artifact_directory"])
        total = 0
        with tempfile.TemporaryDirectory(prefix=".admission-", dir=self.proofs) as temporary:
            stage = Path(temporary) / "proof"
            stage.mkdir()
            for name, expected in candidate["file_hashes"].items():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("Proof archive file names must be relative")
                path = source / name
                if (
                    path.is_symlink()
                    or not path.resolve().is_relative_to(source.resolve())
                    or path.stat().st_size > 16 * 1024 * 1024
                ):
                    raise ValueError("Proof file is redirected or oversized")
                raw = path.read_bytes()
                total += len(raw)
                if total > 64 * 1024 * 1024 or file_hash(path) != expected:
                    raise ValueError("Proof archive budget or source fingerprint changed")
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("Proof changed while copying")
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            stage.rename(directory)

    def archive_intact(self, item):
        directory = self.proofs / item["item_id"]
        try:
            return directory.resolve() == directory and all(
                not (directory / name).is_symlink()
                and (directory / name).resolve().is_relative_to(directory)
                and file_hash(directory / name) == expected
                for name, expected in item["content"]["file_hashes"].items()
            )
        except OSError:
            return False

    def admit(self, data):
        exact_object(
            data,
            {
                "request_id",
                "review_id",
                "plan_fingerprint",
                "reviewer",
                "confirmed",
                "rights_confirmed",
            },
            "Knowledge admission",
        )
        _identifier(data["request_id"])
        text_field(data["reviewer"], "reviewer", 200)
        if data["confirmed"] is not True or data["rights_confirmed"] is not True:
            raise ValueError("Confirm the exact entries and your right to store them privately")
        # Hold one SQLite transaction through archive creation to serialize admissions.
        with self.transaction() as db:
            old = db.execute("SELECT * FROM requests WHERE id=?", (data["request_id"],)).fetchone()
            if old:
                if old["fingerprint"] != digest(data):
                    raise ValueError("Knowledge request ID has different content")
                ids = exact_object(strict_json(old["items"]), {"ids"}, "Saved admission")["ids"]
            else:
                plan = self.prepare(data["review_id"])
                if plan["plan_fingerprint"] != data["plan_fingerprint"]:
                    raise ValueError("Source, proof or displayed knowledge plan changed")
                items = self.read_rows(db)
                if len(items) + sum(c["item_id"] not in items for c in plan["items"]) > MAX_ITEMS:
                    raise ValueError("Local knowledge capacity reached")
                ids = []
                for candidate in plan["items"]:
                    item_id = candidate["item_id"]
                    ids.append(item_id)
                    if item_id in items:
                        if (
                            items[item_id]["state"] != "ACTIVE"
                            or items[item_id]["content"] != candidate
                        ):
                            raise ValueError(
                                "Retired or changed source cannot be silently readmitted; create a new review"
                            )
                        continue
                    self.archive(candidate)
                    body = {
                        "schema": SCHEMA,
                        "item_id": item_id,
                        "version": 1,
                        "source_ref": {
                            "review_id": data["review_id"],
                            "key": candidate["source"]["key"],
                        },
                        "content": candidate,
                        "dependencies": candidate["dependencies"],
                        "state": "ACTIVE",
                        "created_at": _now(),
                        "events": [],
                        "admission": {
                            "reviewer": data["reviewer"],
                            "rights_basis": "local-private-storage-assertion",
                            "plan_fingerprint": plan["plan_fingerprint"],
                        },
                        "privacy": plan["privacy"],
                    }
                    self.event(
                        body,
                        "ADMITTED",
                        data["reviewer"],
                        "Exact historical E4 source and proof bytes checked",
                    )
                    self.save(db, body)
                # Re-read source after copying. A failed/partial admission has no visible rows.
                if self.prepare(data["review_id"]) != plan:
                    raise ValueError("Source changed during admission")
                db.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (data["request_id"], digest(data), canonical({"ids": ids}).decode()),
                )
        return {
            "items": [self.report(item_id) for item_id in ids],
            "model_calls": 0,
            "lean_calls": 0,
        }

    def refresh(self):
        with self.transaction() as db:
            items = self.read_rows(db)
            sources = {}
            for body in items.values():
                if body["state"] != "ACTIVE":
                    continue
                review_id = body["source_ref"]["review_id"]
                if review_id not in sources:
                    try:
                        sources[review_id] = {
                            c["item_id"]: c for c in self.verified_source(review_id)
                        }
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
                        sources[review_id] = {}
                if sources[review_id].get(body["item_id"]) != body[
                    "content"
                ] or not self.archive_intact(body):
                    body["state"] = "INVALIDATED"
                    self.event(
                        body,
                        "SOURCE_OR_PROOF_INVALIDATED",
                        "internal-evidence-audit",
                        "Source approval, protocol or proof bytes no longer match; no automatic revival",
                    )
                    self.save(db, body)
            self.cascade(db, items)
            return items

    def cascade(self, db, items):
        changed = True
        while changed:
            changed = False
            for body in items.values():
                if body["state"] == "ACTIVE" and any(
                    dep not in items or items[dep]["state"] != "ACTIVE"
                    for dep in body["dependencies"]
                ):
                    body["state"] = "INVALIDATED"
                    self.event(
                        body,
                        "DEPENDENCY_INVALIDATED",
                        "internal-evidence-audit",
                        "A referenced author step is unavailable",
                    )
                    self.save(db, body)
                    changed = True

    @staticmethod
    def public(body):
        active = body["state"] == "ACTIVE"
        return {
            **body,
            "trust_level": E4 if active else "E0_CANDIDATE",
            "math_verdict": "PROVED" if active else "UNKNOWN",
            "historical_record": True,
            "fresh_attestation": False,
            "scope": "Historically verified exact obligation with private local provenance; not a new Lean run",
        }

    def report(self, item_id):
        _identifier(item_id)
        items = self.refresh()
        if item_id not in items:
            raise ValueError("Unknown knowledge item")
        return self.public(items[item_id])

    def search(self, query="", include_inactive=False):
        if not isinstance(query, str) or len(query) > 200 or type(include_inactive) is not bool:
            raise ValueError("Invalid knowledge search")
        items = self.refresh()
        query = query.casefold().strip()
        found = []
        for item in reversed(list(items.values())):
            if item["state"] == "DELETED" or (not include_inactive and item["state"] != "ACTIVE"):
                continue
            content = item["content"]
            if (
                query
                and query
                not in " ".join(
                    [
                        item["item_id"],
                        content["title"],
                        content["formal_statement"],
                        content["quote"],
                        content["source"]["review_id"],
                    ]
                ).casefold()
            ):
                continue
            found.append(self.public(item))
        return {
            "items": found,
            "total": len(found),
            "active_count": sum(i["state"] == "ACTIVE" for i in items.values()),
            "fresh_attestation": False,
            "privacy": "private-local",
        }

    def action(self, item_id, data, delete=False):
        exact_object(data, {"reviewer", "reason", "confirmed"}, "Knowledge action")
        text_field(data["reviewer"], "reviewer", 200)
        text_field(data["reason"], "reason", 500)
        if data["confirmed"] is not True:
            raise ValueError("Explicit withdrawal/removal confirmation is required")
        _identifier(item_id)
        self.refresh()
        with self.transaction() as db:
            items = self.read_rows(db)
            if item_id not in items:
                raise ValueError("Unknown knowledge item")
            affected = {item_id}
            if delete:
                changed = True
                while changed:
                    expanded = affected | {
                        i
                        for i, b in items.items()
                        if any(dep in affected for dep in b["dependencies"])
                    }
                    changed = expanded != affected
                    affected = expanded
            for selected in affected:
                body = items[selected]
                if body["state"] == "DELETED":
                    continue
                body["state"] = "DELETED" if delete else "WITHDRAWN"
                self.event(body, body["state"], data["reviewer"], data["reason"])
                if delete:
                    body["content"] = None
                self.save(db, body)
            self.cascade(db, items)
        # Tombstones commit first: a crash can retain old bytes but never a usable result.
        if delete:
            for selected in affected:
                directory = self.proofs / selected
                if directory.is_symlink() or directory.resolve() != directory:
                    raise ValueError("Refusing to remove a redirected archive")
                if directory.exists():
                    shutil.rmtree(directory)
        return {
            "item": self.report(item_id),
            "affected": sorted(affected),
            "source_review_deleted": False,
        }
