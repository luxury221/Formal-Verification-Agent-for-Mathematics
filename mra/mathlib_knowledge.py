"""Private archives of exact Mathlib experiments; no proof import or new certification."""

from contextlib import contextmanager
import copy
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_lab import now, run_identifier
from .mathlib_terms import text
from .research_mathlib import ResearchMathlibStore
from .tasks import _identifier

SCHEMA = "private-mathlib-knowledge-v1"
PRIVACY = {"visibility": "private-local", "sharing_allowed": False, "training_allowed": False}
MAX_BYTES = 64 * 1024 * 1024


class MathlibKnowledgeStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/mathlib-knowledge"
        self.proofs = self.base / "proofs"
        self.proofs.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "knowledge.sqlite3"
        self.sources = ResearchMathlibStore(self.root)
        self.cursor = 0
        self.paths_intact()
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, item_id TEXT NOT NULL)"
            )

    def paths_intact(self):
        if (
            self.base.resolve() != self.base
            or self.proofs.resolve() != self.proofs
            or self.path.is_symlink()
        ):
            raise ValueError("Mathlib knowledge storage must not be redirected")

    @contextmanager
    def transaction(self, write=True):
        self.paths_intact()
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

    @contextmanager
    def archive_lock(self):
        import fcntl

        self.paths_intact()
        path = self.base / "archive.lock"
        if path.is_symlink():
            raise ValueError("Archive lock redirected")
        with path.open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "Another archive operation is in progress; retry this request"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def item_id(run_id):
        return "ml-knowledge-" + digest({"run_id": run_identifier(run_id)})[:32]

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("Unknown Mathlib knowledge item")
        body = strict_json(row[0])
        if digest(body) != row[1]:
            raise ValueError("Mathlib knowledge ledger digest mismatch")
        return body

    def read(self, db, item_id):
        _identifier(item_id)
        row = self.decode(
            db.execute("SELECT body,hash FROM items WHERE id=?", (item_id,)).fetchone()
        )
        if row["item_id"] != item_id:
            raise ValueError("Mathlib knowledge identity mismatch")
        return row

    @staticmethod
    def save(db, row):
        if len(canonical(row)) > 2097152:
            raise ValueError("Mathlib knowledge record exceeds 2 MiB")
        db.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?)",
            (row["item_id"], canonical(row).decode(), digest(row)),
        )

    def adapter(self):
        return {
            "schema": SCHEMA,
            "files": {
                name: file_hash(self.root / name)
                for name in ("mra/mathlib_knowledge.py", "mra/research_mathlib.py")
            },
        }

    @staticmethod
    def event(row, kind, actor, reason):
        if len(row["events"]) >= 64:
            raise ValueError("Mathlib knowledge event budget exhausted")
        row["events"].append(
            {
                "sequence": len(row["events"]) + 1,
                "at": now(),
                "kind": kind,
                "actor": actor,
                "reason": reason,
            }
        )

    @staticmethod
    def safe_name(name):
        if (
            not isinstance(name, str)
            or not name
            or name == "."
            or "\x00" in name
            or len(name) > 240
            or "\\" in name
            or str(PurePosixPath(name)) != name
            or PurePosixPath(name).is_absolute()
            or any(part in (".", "..") for part in PurePosixPath(name).parts)
        ):
            raise ValueError("Archive file names must be normalized relative paths")
        return name

    def directory(self, item_id):
        _identifier(item_id)
        self.paths_intact()
        directory = self.proofs / item_id
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError("Mathlib proof archive redirected")
        return directory

    def candidate(self, run_id):
        source = self.sources.source(run_id)
        row = self.sources.mathlib.read(run_id)
        record = {
            "run_id": run_id,
            "plan": row["plan"],
            "alignment": row["alignment"],
            "files": row["files"],
            "candidate": row["result"]["candidate"],
            "replay": row["result"]["replay"],
        }
        if (
            row["invalidated"]
            or row["math_verdict"] != "PROVED"
            or digest(record) != source["source_fingerprint"]
        ):
            raise ValueError("Mathlib source changed while preparing its archive")
        files = row["files"]
        if not files or len(files) > 128:
            raise ValueError("An archive requires between 1 and 128 proof files")
        directory = self.sources.mathlib.directory(run_id)
        if directory.resolve() != directory:
            raise ValueError("Source proof directory redirected")
        total = 0
        for name, expected in files.items():
            self.safe_name(name)
            path = directory / name
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(directory)
                or len(expected) != 64
            ):
                raise ValueError("Source proof file redirected or invalid")
            size = path.stat().st_size
            total += size
            if size > 16 * 1024 * 1024 or total > MAX_BYTES:
                raise ValueError("Proof archive exceeds its 16 MiB file / 64 MiB total budget")
        request = source["request"]
        proposition = {
            "schema": request["schema"],
            "domain": "Nat",
            "variables": request["variables"],
            "assumptions": request["assumptions"],
            "goal": request["goal"],
        }
        generated = {"source-record.json": canonical(record) + b"\n"}
        hashes = {"proof/" + name: sha for name, sha in files.items()}
        hashes.update({name: hashlib.sha256(raw).hexdigest() for name, raw in generated.items()})
        return {
            "item_id": self.item_id(run_id),
            "source": source,
            "source_record": record,
            "file_hashes": hashes,
            "proposition_fingerprint": digest(proposition),
            "source_bytes": total,
            "adapter": self.adapter(),
        }

    def prepare(self, data):
        exact_object(data, {"run_id"}, "Mathlib knowledge source")
        content = self.candidate(data["run_id"])
        plan = {"schema": SCHEMA, "content": content, "privacy": PRIVACY, "new_lean_calls": 0}
        plan["plan_fingerprint"] = digest(plan)
        with self.transaction(False) as db:
            items = [self.decode(row) for row in db.execute("SELECT body,hash FROM items")]
        plan["same_statement_ids"] = [
            row["item_id"]
            for row in items
            if row["content"]
            and row["content"]["proposition_fingerprint"] == content["proposition_fingerprint"]
        ]
        return plan

    def archive_intact(self, content):
        try:
            directory = self.directory(content["item_id"])
            expected = content["file_hashes"]
            found = set()
            for path in directory.rglob("*"):
                if path.is_symlink():
                    return False
                if path.is_file():
                    name = str(path.relative_to(directory))
                    if (
                        name not in expected
                        or not path.resolve().is_relative_to(directory)
                        or file_hash(path) != expected[name]
                    ):
                        return False
                    found.add(name)
            return found == set(expected)
        except (OSError, ValueError):
            return False

    def archive(self, content):
        directory = self.directory(content["item_id"])
        if directory.exists():
            if self.archive_intact(content):
                return
            raise ValueError("Changed or incomplete Mathlib archive already exists")
        source = self.sources.mathlib.directory(content["source"]["run_id"])
        with tempfile.TemporaryDirectory(prefix=".admission-", dir=self.proofs) as temporary:
            stage = Path(temporary) / "proof"
            stage.mkdir()
            total = 0
            for name, expected in content["file_hashes"].items():
                self.safe_name(name)
                if name == "source-record.json":
                    raw = canonical(content["source_record"]) + b"\n"
                else:
                    path = source / name.removeprefix("proof/")
                    if path.is_symlink() or not path.resolve().is_relative_to(source):
                        raise ValueError("Source proof redirected during copy")
                    if path.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError("Source proof exceeded its copy budget")
                    with path.open("rb") as stream:
                        raw = stream.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError("Source proof exceeded its copy budget")
                total += len(raw)
                if total > MAX_BYTES + 2097152 or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("Proof changed during archive copy or exceeded budget")
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            stage.rename(directory)
        if not self.archive_intact(content):
            raise ValueError("Archived bytes do not match the confirmed proof")

    def previous(self, db, request_id, fingerprint):
        row = db.execute(
            "SELECT fingerprint,item_id FROM requests WHERE id=?", (_identifier(request_id),)
        ).fetchone()
        if row and row[0] != fingerprint:
            raise ValueError("Request ID belongs to different Mathlib knowledge content")
        return row[1] if row else None

    def register_request(self, db, data, fingerprint, item_id):
        if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 2000:
            raise ValueError("Mathlib knowledge request budget exhausted")
        db.execute(
            "INSERT INTO requests VALUES (?,?,?)", (data["request_id"], fingerprint, item_id)
        )

    def admit(self, data):
        exact_object(
            data,
            {
                "request_id",
                "run_id",
                "plan_fingerprint",
                "reviewer",
                "confirmed",
                "rights_confirmed",
            },
            "Mathlib knowledge admission",
        )
        reviewer = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True or data["rights_confirmed"] is not True:
            raise ValueError("Confirm the complete entry and your right to store it privately")
        fingerprint = digest({"action": "admit", "data": data})
        with self.transaction(False) as db:
            previous = self.previous(db, data["request_id"], fingerprint)
        if previous:
            return self.report(previous)
        plan = self.prepare({"run_id": data["run_id"]})
        if plan["plan_fingerprint"] != data["plan_fingerprint"]:
            raise ValueError("Mathlib admission preview changed; review it again")
        content, item_id = plan["content"], plan["content"]["item_id"]
        with self.archive_lock():
            with self.transaction(False) as db:
                saved = db.execute("SELECT body,hash FROM items WHERE id=?", (item_id,)).fetchone()
            if saved:
                old = self.decode(saved)
                if old["state"] != "ACTIVE" or old["content"] != content:
                    raise ValueError(
                        "Retired or changed source cannot be readmitted; complete a new Mathlib run"
                    )
            self.archive(content)
            if self.candidate(data["run_id"]) != content:
                raise ValueError("Source changed during proof archiving")
            with self.transaction() as db:
                previous = self.previous(db, data["request_id"], fingerprint)
                if not previous:
                    current = db.execute(
                        "SELECT body,hash FROM items WHERE id=?", (item_id,)
                    ).fetchone()
                    if current:
                        row = self.decode(current)
                        if row["state"] != "ACTIVE" or row["content"] != content:
                            raise ValueError("Knowledge state changed during admission")
                    else:
                        if db.execute("SELECT count(*) FROM items").fetchone()[0] >= 500:
                            raise ValueError("Mathlib knowledge is limited to 500 items")
                        row = {
                            "schema": SCHEMA,
                            "item_id": item_id,
                            "source_ref": {"run_id": data["run_id"]},
                            "state": "ACTIVE",
                            "version": 1,
                            "created_at": now(),
                            "content": content,
                            "privacy": copy.deepcopy(PRIVACY),
                            "events": [],
                            "admission": {
                                "reviewer": reviewer,
                                "plan_fingerprint": data["plan_fingerprint"],
                                "rights_basis": "local-private-storage-assertion",
                            },
                        }
                        self.event(
                            row,
                            "ADMITTED",
                            reviewer,
                            "Confirmed exact historical E4 source and private proof copy",
                        )
                        self.save(db, row)
                    self.register_request(db, data, fingerprint, item_id)
        return self.report(item_id)

    def reason(self, row):
        if row["content"]["adapter"] != self.adapter():
            return "Archive adapter changed"
        try:
            source = self.sources.source(row["source_ref"]["run_id"])
            if source != row["content"]["source"]:
                return "Source evidence changed"
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            return "Source storage unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return "Source unavailable: " + str(error)
        if not self.archive_intact(row["content"]):
            return "Archived proof bytes changed or missing"
        return None

    def report(self, item_id):
        for _ in range(4):
            with self.transaction(False) as db:
                row = self.read(db, item_id)
            reason = self.reason(row) if row["state"] == "ACTIVE" else None
            if reason:
                with self.transaction() as db:
                    if self.read(db, item_id) != row:
                        continue
                    row.update(state="INVALIDATED", version=row["version"] + 1)
                    self.event(row, "INVALIDATED", "internal-evidence-audit", reason)
                    self.save(db, row)
            else:
                with self.transaction(False) as db:
                    if self.read(db, item_id) != row:
                        continue
            active = row["state"] == "ACTIVE"
            return {
                **row,
                "math_verdict": "PROVED" if active else "UNKNOWN",
                "evidence_level": "E4" if active else "E0",
                "artifact_status": "MATCHES_RECORDED_HASHES" if active else "UNAVAILABLE",
                "historical_record": True,
                "fresh_attestation": False,
                "new_lean_calls": 0,
            }
        raise RuntimeError("Knowledge changed concurrently; reload the item")

    def search(self, query="", include_inactive=False):
        if not isinstance(query, str) or len(query) > 200 or type(include_inactive) is not bool:
            raise ValueError("Invalid Mathlib knowledge search")
        with self.transaction(False) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM items ORDER BY rowid DESC")]
        found = []
        for item_id in ids:
            row = self.report(item_id)
            content = row["content"]
            if not include_inactive and row["state"] != "ACTIVE":
                continue
            source = content["source"] if content else None
            request = source["request"] if source else None
            title = request["title"] if request else "已移除的 Mathlib 条目"
            original = request["original"] if request else ""
            formula = source["formal_statement"] if source else ""
            haystack = " ".join(
                [
                    item_id,
                    row["source_ref"]["run_id"],
                    title,
                    original,
                    formula,
                    " ".join(content["source_record"]["plan"]["references"]) if content else "",
                ]
            )
            if query.strip().casefold() not in haystack.casefold():
                continue
            found.append(
                {
                    "item_id": item_id,
                    "title": title,
                    "original": original,
                    "formal_statement": formula,
                    "state": row["state"],
                    "math_verdict": row["math_verdict"],
                    "evidence_level": row["evidence_level"],
                    "run_id": row["source_ref"]["run_id"],
                    "created_at": row["created_at"],
                    "proposition_fingerprint": content["proposition_fingerprint"]
                    if content
                    else None,
                    "href": "/library#" + item_id,
                }
            )
        return {
            "schema": SCHEMA,
            "items": found,
            "count": len(found),
            "privacy": PRIVACY,
            "fresh_attestation": False,
        }

    def retire(self, item_id, data, *, remove=False):
        exact_object(
            data, {"request_id", "reviewer", "reason", "confirmed"}, "Mathlib knowledge retirement"
        )
        actor, reason = text(data["reviewer"], 200, "Reviewer"), text(data["reason"], 500, "Reason")
        if data["confirmed"] is not True:
            raise ValueError("Explicit retirement confirmation required")
        fingerprint = digest(
            {"action": "remove" if remove else "withdraw", "item_id": item_id, "data": data}
        )
        with self.archive_lock():
            with self.transaction() as db:
                previous = self.previous(db, data["request_id"], fingerprint)
                if previous and previous != item_id:
                    raise ValueError("Retirement request belongs to a different item")
                row = self.read(db, item_id)
                if not previous:
                    if not remove and row["state"] != "ACTIVE":
                        raise ValueError("Only active Mathlib knowledge can be withdrawn")
                    if remove:
                        row.update(content=None, admission=None, state="DELETED")
                    else:
                        row["state"] = "WITHDRAWN"
                    row["version"] += 1
                    self.event(row, "REMOVED" if remove else "WITHDRAWN", actor, reason)
                    self.save(db, row)
                    self.register_request(db, data, fingerprint, item_id)
            if remove:
                directory = self.directory(item_id)
                if directory.exists():
                    shutil.rmtree(directory)
        return self.report(item_id)

    def recheck_request(self, item_id):
        row = self.report(item_id)
        if row["state"] != "ACTIVE":
            raise ValueError("Only currently available knowledge can supply a recheck request")
        return {
            "item_id": item_id,
            "request": row["content"]["source"]["request"],
            "original_run_id": row["source_ref"]["run_id"],
            "new_lean_calls": 0,
            "requires_new_preview_and_confirmation": True,
            "proof_imported": False,
        }

    def reconcile(self, run_id=None):
        with self.transaction(False) as db:
            rows = [
                self.decode(row) for row in db.execute("SELECT body,hash FROM items ORDER BY id")
            ]
        rows = [
            row
            for row in rows
            if row["state"] == "ACTIVE"
            and (run_id is None or row["source_ref"]["run_id"] == run_id)
        ]
        if run_id is None and rows:
            self.cursor %= len(rows)
            rows = rows[self.cursor : self.cursor + 1]
            self.cursor += 1
        return [self.report(row["item_id"])["state"] for row in rows]
