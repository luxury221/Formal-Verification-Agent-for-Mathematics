"""Explicit specialization of private Mathlib lemmas, followed by fresh real checks."""

from contextlib import contextmanager
import copy
from pathlib import Path
import re
import sqlite3
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_knowledge import MathlibKnowledgeStore, PRIVACY
from .mathlib_lab import now
from .mathlib_terms import compile_request, identifier, text
from .tasks import _identifier

SCHEMA = "private-mathlib-reuse-v1"
INPUT_FIELDS = {"item_id", "variables", "substitution", "title", "original", "timeout_seconds"}
CURRENT = ("AWAITING_CHECK", "ACTIVE")


def reuse_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"ml-reuse-[a-f0-9]{32}", value):
        raise ValueError("Invalid Mathlib reuse identifier")
    return value


def specialize(source, data):
    """Simultaneous, capture-free substitution in the bounded source data trees."""
    exact_object(data, INPUT_FIELDS, "Mathlib specialization")
    _identifier(data["item_id"])
    text(data["title"], 160, "Title")
    text(data["original"], 12000, "Original")
    if type(data["timeout_seconds"]) not in (int, float) or not 1 <= data["timeout_seconds"] <= 30:
        raise ValueError("Candidate and replay share 1–30 worker seconds")
    variables = data["variables"]
    if not isinstance(variables, list) or len(variables) > 6:
        raise ValueError("At most six target Nat variables")
    variables = [identifier(v) for v in variables]
    if len(set(variables)) != len(variables):
        raise ValueError("Duplicate target variable")
    substitution = data["substitution"]
    exact_object(substitution, set(source["variables"]), "Complete variable substitution")
    nodes = 0

    def term(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 80 or not isinstance(value, dict) or len(value) != 1:
            raise ValueError(
                "Substitution requires bounded Nat expression trees (depth 8 / 80 nodes)"
            )
        op, arg = next(iter(value.items()))
        if op == "var" and isinstance(arg, str) and arg in variables:
            return {op: arg}
        if op == "nat" and type(arg) is int and 0 <= arg <= 1000000:
            return {op: arg}
        if op in ("add", "mul", "sub") and isinstance(arg, list) and len(arg) == 2:
            return {op: [term(v, depth + 1) for v in arg]}
        raise ValueError("Substitution uses an undefined variable, expression or non-Nat value")

    mapping = {key: term(value) for key, value in substitution.items()}
    if len(canonical(data)) > 65536:
        raise ValueError("Specialization exceeds 64 KiB")

    def replace(value):
        if isinstance(value, dict):
            if set(value) == {"var"}:
                return copy.deepcopy(mapping[value["var"]])
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value

    request = {
        **source,
        "title": data["title"],
        "original": data["original"],
        "variables": variables,
        "timeout_seconds": data["timeout_seconds"],
        **{key: replace(source[key]) for key in ("assumptions", "goal", "proof")},
    }
    return compile_request(request)["request"], mapping


class MathlibReuseStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/mathlib-reuse"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "reuse.sqlite3"
        self.knowledge = MathlibKnowledgeStore(self.root)
        self.cursor = 0
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, item_id TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Mathlib reuse ledger must not be redirected")
        db = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read(db, item_id):
        reuse_id(item_id)
        saved = db.execute("SELECT body,hash FROM items WHERE id=?", (item_id,)).fetchone()
        if saved is None:
            raise ValueError("Unknown Mathlib reuse item")
        row = strict_json(saved[0])
        if row["reuse_id"] != item_id or digest(row) != saved[1]:
            raise ValueError("Mathlib reuse ledger identity or digest mismatch")
        return row

    @staticmethod
    def save(db, row):
        if len(canonical(row)) > 2097152:
            raise ValueError("Mathlib reuse record exceeds 2 MiB")
        db.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?)",
            (row["reuse_id"], canonical(row).decode(), digest(row)),
        )

    @staticmethod
    def previous(db, request_id, fingerprint):
        row = db.execute(
            "SELECT fingerprint,item_id FROM requests WHERE id=?", (_identifier(request_id),)
        ).fetchone()
        if row and row[0] != fingerprint:
            raise ValueError("Request ID belongs to a different reuse operation")
        return row[1] if row else None

    @staticmethod
    def register(db, request_id, fingerprint, item_id):
        if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 2000:
            raise ValueError("Mathlib reuse request budget exhausted")
        db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, fingerprint, item_id))

    @staticmethod
    def event(row, kind, actor, reason, **extra):
        if len(row["events"]) >= 64:
            raise ValueError("Mathlib reuse event budget exhausted")
        row["events"].append(
            {
                "sequence": len(row["events"]) + 1,
                "at": now(),
                "kind": kind,
                "actor": actor,
                "reason": reason,
                **extra,
            }
        )

    def adapter(self):
        return {
            "schema": SCHEMA,
            "files": {
                name: file_hash(self.root / name)
                for name in (
                    "mra/mathlib_reuse.py",
                    "mra/mathlib_knowledge.py",
                    "mra/research_mathlib.py",
                    "mra/mathlib_terms.py",
                )
            },
        }

    def source(self, item_id):
        row = self.knowledge.report(item_id)
        if (row["state"], row["math_verdict"], row["evidence_level"]) != ("ACTIVE", "PROVED", "E4"):
            raise ValueError("A current private ACTIVE/PROVED/E4 Mathlib archive is required")
        return {
            "item_id": item_id,
            "version": row["version"],
            "content_fingerprint": digest(row["content"]),
            "run_id": row["content"]["source"]["run_id"],
            "request": row["content"]["source"]["request"],
            "formal_statement": row["content"]["source"]["formal_statement"],
        }

    def preview(self, data):
        exact_object(data, INPUT_FIELDS, "Mathlib specialization")
        source = self.source(data["item_id"])
        request, mapping = specialize(source["request"], data)
        plan = {
            "schema": SCHEMA,
            "source": source,
            "input": {
                **data,
                "substitution": mapping,
                "title": request["title"],
                "original": request["original"],
                "timeout_seconds": request["timeout_seconds"],
            },
            "target": self.knowledge.sources.mathlib.preview(request),
            "adapter": self.adapter(),
            "privacy": copy.deepcopy(PRIVACY),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "proof_imported": False,
            "new_lean_calls": 0,
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def create(self, data):
        exact_object(
            data,
            INPUT_FIELDS | {"request_id", "plan_fingerprint", "reviewer", "confirmed"},
            "Mathlib reuse confirmation",
        )
        actor = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True:
            raise ValueError(
                "Confirm source, simultaneous substitution, full premises and new original"
            )
        fingerprint = digest({"action": "create", "data": data})
        with self.transaction(False) as db:
            previous = self.previous(db, data["request_id"], fingerprint)
        if previous:
            return self.report(previous)
        plan = self.preview({key: data[key] for key in INPUT_FIELDS})
        if plan["plan_fingerprint"] != data["plan_fingerprint"]:
            raise ValueError("Reuse preview changed; review it again")
        with self.transaction() as db:
            previous = self.previous(db, data["request_id"], fingerprint)
            if previous:
                item_id = previous
            else:
                if db.execute("SELECT count(*) FROM items").fetchone()[0] >= 400:
                    raise ValueError("Mathlib reuse is limited to 400 records")
                item_id = "ml-reuse-" + uuid.uuid4().hex
                row = {
                    "schema": SCHEMA,
                    "reuse_id": item_id,
                    "source_ref": plan["source"]["item_id"],
                    "state": "AWAITING_CHECK",
                    "version": 1,
                    "created_at": now(),
                    "content": plan,
                    "confirmation": {"reviewer": actor, "confirmed": True},
                    "binding": None,
                    "events": [],
                }
                self.event(
                    row,
                    "CREATED",
                    actor,
                    "Confirmed complete specialization; independent new check required",
                )
                self.save(db, row)
                self.register(db, data["request_id"], fingerprint, item_id)
        return self.report(item_id)

    def reason(self, row):
        plan = row["content"]
        try:
            if self.preview(plan["input"]) != plan:
                return "Source archive, specialization or adapter changed"
            binding = row["binding"]
            if binding and self.knowledge.sources.source(binding["run_id"]) != binding["source"]:
                return "Independent result evidence changed"
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            return "Storage unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return "Reference chain unavailable: " + str(error)
        return None

    def report(self, item_id):
        for _ in range(4):
            with self.transaction(False) as db:
                row = self.read(db, item_id)
            reason = self.reason(row) if row["state"] in CURRENT else None
            if reason:
                with self.transaction() as db:
                    if self.read(db, item_id) != row:
                        continue
                    row.update(state="INVALIDATED", version=row["version"] + 1)
                    self.event(row, "INVALIDATED", "internal-source-audit", reason)
                    self.save(db, row)
            else:
                with self.transaction(False) as db:
                    if self.read(db, item_id) != row:
                        continue
            proved = row["state"] == "ACTIVE" and row["binding"] is not None
            return {
                **row,
                "math_verdict": "PROVED" if proved else "UNKNOWN",
                "evidence_level": "E4" if proved else "E0",
                "artifact_status": "MATCHES_RECORDED_HASHES" if proved else "NOT_CURRENTLY_PROVED",
                "fresh_attestation": False,
                "proof_imported": False,
                "new_lean_calls": 0,
            }
        raise RuntimeError("Reuse item changed concurrently; reload it")

    def request(self, item_id):
        row = self.report(item_id)
        if row["state"] not in CURRENT:
            raise ValueError("This reuse item is no longer available")
        return {
            "reuse_id": item_id,
            "source_item_id": row["source_ref"],
            "request": row["content"]["target"]["request"],
            "requires_new_preview_and_confirmation": True,
            "proof_imported": False,
            "new_lean_calls": 0,
        }

    def preview_attachment(self, item_id, data):
        exact_object(data, {"run_id"}, "New reuse check source")
        row = self.report(item_id)
        if row["state"] not in CURRENT:
            raise ValueError("Only current reuse records accept an independent check")
        if data["run_id"] == row["content"]["source"]["run_id"]:
            raise ValueError("The original source run cannot stand in for an independent new check")
        source = self.knowledge.sources.source(data["run_id"])
        if source["request"] != row["content"]["target"]["request"]:
            raise ValueError("New check differs from the complete specialized request")
        plan = {
            "reuse_id": item_id,
            "version": row["version"],
            "plan_fingerprint": row["content"]["plan_fingerprint"],
            "source": source,
            "new_lean_calls": 0,
            "fresh_attestation": False,
        }
        plan["attachment_fingerprint"] = digest(plan)
        return plan

    def attach(self, item_id, data):
        exact_object(
            data,
            {"request_id", "run_id", "attachment_fingerprint", "reviewer", "confirmed"},
            "Reuse result attachment",
        )
        actor = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True:
            raise ValueError("Explicit confirmation of the complete new result is required")
        fingerprint = digest({"action": "attach", "reuse_id": item_id, "data": data})
        with self.transaction(False) as db:
            previous = self.previous(db, data["request_id"], fingerprint)
        if previous:
            if previous != item_id:
                raise ValueError("Request belongs to another reuse item")
            return self.report(previous)
        plan = self.preview_attachment(item_id, {"run_id": data["run_id"]})
        if plan["attachment_fingerprint"] != data["attachment_fingerprint"]:
            raise ValueError("Attachment preview changed; review it again")
        with self.transaction() as db:
            previous = self.previous(db, data["request_id"], fingerprint)
            if not previous:
                row = self.read(db, item_id)
                if row["version"] != plan["version"] or row["state"] not in CURRENT:
                    raise ValueError("Reuse record changed during attachment")
                row.update(
                    state="ACTIVE",
                    version=row["version"] + 1,
                    binding={
                        "run_id": data["run_id"],
                        "source": plan["source"],
                        "reviewer": actor,
                        "confirmed": True,
                        "attachment_fingerprint": plan["attachment_fingerprint"],
                    },
                )
                self.event(
                    row,
                    "CHECK_ATTACHED",
                    actor,
                    "Exact independent real E4 check attached",
                    run_id=data["run_id"],
                )
                self.save(db, row)
                self.register(db, data["request_id"], fingerprint, item_id)
        return self.report(item_id)

    def retire(self, item_id, data, *, remove=False):
        exact_object(data, {"request_id", "reviewer", "reason", "confirmed"}, "Reuse retirement")
        actor, reason = text(data["reviewer"], 200, "Reviewer"), text(data["reason"], 500, "Reason")
        if data["confirmed"] is not True:
            raise ValueError("Explicit retirement confirmation is required")
        fingerprint = digest(
            {"action": "remove" if remove else "withdraw", "reuse_id": item_id, "data": data}
        )
        with self.transaction() as db:
            previous = self.previous(db, data["request_id"], fingerprint)
            row = self.read(db, item_id)
            if not previous:
                if not remove and row["state"] not in CURRENT:
                    raise ValueError("Only current reuse records can be withdrawn")
                row["state"] = "DELETED" if remove else "WITHDRAWN"
                if remove:
                    row.update(content=None, binding=None, confirmation=None)
                row["version"] += 1
                self.event(row, "REMOVED" if remove else "WITHDRAWN", actor, reason)
                self.save(db, row)
                self.register(db, data["request_id"], fingerprint, item_id)
        return self.report(item_id)

    def search(self, query="", include_inactive=False):
        if not isinstance(query, str) or len(query) > 200 or type(include_inactive) is not bool:
            raise ValueError("Invalid reuse search")
        with self.transaction(False) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM items ORDER BY rowid DESC")]
        items = []
        for item_id in ids:
            row = self.report(item_id)
            if not include_inactive and row["state"] not in CURRENT:
                continue
            plan = row["content"]
            title = plan["target"]["request"]["title"] if plan else "已移除的引理实例"
            formula = plan["target"]["formal_statement"] if plan else ""
            original = plan["target"]["request"]["original"] if plan else ""
            if (
                query.strip().casefold()
                not in " ".join([item_id, row["source_ref"], title, formula, original]).casefold()
            ):
                continue
            items.append(
                {
                    "reuse_id": item_id,
                    "title": title,
                    "original": original,
                    "formal_statement": formula,
                    "source_item_id": row["source_ref"],
                    "state": row["state"],
                    "math_verdict": row["math_verdict"],
                    "evidence_level": row["evidence_level"],
                    "created_at": row["created_at"],
                    "href": "/library#" + item_id,
                }
            )
        return {"schema": SCHEMA, "items": items, "count": len(items), "fresh_attestation": False}

    def reconcile(self, item_id=None, run_id=None):
        with self.transaction(False) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM items ORDER BY id")]
            rows = [self.read(db, key) for key in ids]
        rows = [
            row
            for row in rows
            if row["state"] in CURRENT
            and (item_id is None or row["source_ref"] == item_id)
            and (
                run_id is None
                or row["content"]["source"]["run_id"] == run_id
                or (row["binding"] and row["binding"]["run_id"] == run_id)
            )
        ]
        if item_id is None and run_id is None and rows:
            self.cursor %= len(rows)
            rows = rows[self.cursor : self.cursor + 1]
            self.cursor += 1
        return [self.report(row["reuse_id"])["state"] for row in rows]
