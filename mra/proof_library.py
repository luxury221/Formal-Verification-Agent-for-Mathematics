"""Private archives of complete compositions with bounded transitive provenance."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from .domain import canonical, digest
from .formalizer import exact_object
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lemma_composition import LemmaCompositionStore, load_source as legacy_source
from .lemma_composition_terms import target
from .mathlib_knowledge import MathlibKnowledgeStore, MAX_BYTES, PRIVACY
from .mathlib_lab import now, run_identifier
from .mathlib_terms import text
from .number_theory_terms import MODE as TARGET_MODE
from .replay import extract_export
from .research_flow import atomic, lease
from .tasks import WorkerBusy, _identifier

SCHEMA = "private-composition-knowledge-v1"
FILES = ("mra/proof_library.py", "mra/library_composition.py", "mra/research_flow.py")
LIMITS = {"ancestry": 64, "depth": 8, "audit_visits": 128, "proof_bytes": 524288}
_audit = ContextVar("mra_proof_library_audit", default=None)


@contextmanager
def audit_scope(item_id):
    context = _audit.get()
    token = None
    if context is None:
        context = {"path": [], "visits": 0}
        token = _audit.set(context)
    try:
        if item_id in context["path"]:
            raise ValueError("Cyclic proof archive provenance")
        if context["visits"] >= LIMITS["audit_visits"] or len(context["path"]) >= LIMITS["depth"]:
            raise WorkerBusy("Proof provenance audit budget reached; no evidence was promoted")
        context["visits"] += 1
        context["path"].append(item_id)
        try:
            yield
        finally:
            context["path"].pop()
    finally:
        if token is not None:
            _audit.reset(token)


def signature(request):
    value = {
        key: deepcopy(request[key])
        for key in ("title", "original", "variables", "assumptions", "goal", "timeout_seconds")
    }
    return {"schema": TARGET_MODE, **value}


def provenance(sources):
    ancestors = {}
    for source in sources:
        for ancestor in source["ancestors"]:
            key = ancestor["item_id"]
            if key in ancestors and ancestors[key] != ancestor:
                raise ValueError("The same ancestor has conflicting provenance versions")
            ancestors[key] = deepcopy(ancestor)
    depth = 1 + max((s["depth"] for s in sources), default=-1)
    if depth > LIMITS["depth"] or len(ancestors) >= LIMITS["ancestry"]:
        raise ValueError("Proof provenance exceeds depth 8 / 64 archived ancestors including self")
    return {"depth": depth, "ancestors": [ancestors[k] for k in sorted(ancestors)]}


def proof_data(path, expected):
    if path.is_symlink() or path.stat().st_size > LIMITS["proof_bytes"]:
        raise ValueError("Private proof data exceeds 512 KiB or was redirected")
    with path.open("rb") as stream:
        raw = stream.read(LIMITS["proof_bytes"] + 1)
    if len(raw) > LIMITS["proof_bytes"] or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Private proof data differs from its bounded archived hash")
    if extract_export("MRA_EXPORT_BEGIN\n" + raw.decode("utf-8") + "MRA_EXPORT_END\n") != raw:
        raise ValueError("Private proof data is not a canonical kernel export")
    return raw


class ProofLibraryStore(MathlibKnowledgeStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/proof-library"
        self.proofs = self.base / "proofs"
        self.path = self.base / "library.sqlite3"
        self.paths_intact()
        self.proofs.mkdir(parents=True, exist_ok=True)
        self.cursor = 0
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, item_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )
        self.legacy = LemmaCompositionStore(self.root)
        from .library_composition import LibraryCompositionStore

        self.compositions = LibraryCompositionStore(self)

    @contextmanager
    def archive_lock(self):
        self.paths_intact()
        with lease(self.base):
            yield

    def adapter(self):
        return {"schema": SCHEMA, "files": {name: file_hash(self.root / name) for name in FILES}}

    @staticmethod
    def source_ref(raw):
        exact_object(raw, {"origin", "run_id"}, "Composition archive source")
        if raw["origin"] not in ("composition", "library-composition"):
            raise ValueError("Choose the original or library composition protocol explicitly")
        return {"origin": raw["origin"], "run_id": run_identifier(raw["run_id"])}

    @staticmethod
    def entry_id(ref):
        return "proof-knowledge-" + digest(ref)[:32]

    def source_store(self, origin):
        return self.legacy if origin == "composition" else self.compositions

    def candidate(self, raw):
        ref = self.source_ref(raw)
        store = self.source_store(ref["origin"])
        row = store.report(ref["run_id"])
        if (
            row["state"],
            row["math_verdict"],
            row["evidence_level"],
            row["invalidated"],
            row["cancel_requested"],
            row["artifact_status"],
        ) != ("COMPLETED", "PROVED", "E4", False, False, "MATCHES_RECORDED_HASHES"):
            raise ValueError("Only intact, active composition E4 can enter the proof library")
        if (
            row["alignment"]["confirmed"] is not True
            or row["result"]["candidate"]["audit"]["type_matches"] is not True
            or row["result"]["replay"]["audit"]["mode"] != "data-only-kernel-replay-v1"
            or row["result"]["candidate"]["pid"] == row["result"]["replay"]["pid"]
        ):
            raise ValueError("Complete alignment and independent data replay are required")
        request = signature(row["plan"]["request"])
        if target(request)["formal_statement"] != row["plan"]["formal_statement"]:
            raise ValueError("Archived full signature differs from the checked composition")
        sources = [
            self.load_source({k: s[k] for k in ("alias", "item_id")})[0]
            for s in row["plan"]["source_items"]
        ]
        ancestry = provenance(sources)
        record = {
            key: deepcopy(row[key])
            for key in ("run_id", "plan", "alignment", "files", "result", "submission_fingerprint")
        }

        files = row["files"]
        if not files or len(files) > 128:
            raise ValueError("A composition archive requires 1–128 proof files")
        directory = store.directory(ref["run_id"])
        if directory.resolve() != directory:
            raise ValueError("Composition source directory redirected")
        total = 0
        for name, expected in files.items():
            self.safe_name(name)
            path = directory / name
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(directory)
                or len(expected) != 64
            ):
                raise ValueError("Composition proof file redirected or invalid")
            size = path.stat().st_size
            total += size
            if size > 16 * 1024 * 1024 or total > MAX_BYTES:
                raise ValueError(
                    "Composition archive exceeds its 16 MiB file / 64 MiB total budget"
                )
        proof_data(directory / "replay/proof.ndjson", files.get("replay/proof.ndjson"))
        generated = canonical(record) + b"\n"
        hashes = {"proof/" + n: h for n, h in files.items()}
        hashes["source-record.json"] = hashlib.sha256(generated).hexdigest()
        source = {
            **ref,
            "request": request,
            "formal_statement": row["plan"]["formal_statement"],
            "source_alignment": deepcopy(row["alignment"]),
            "source_fingerprint": digest(record),
        }
        return {
            "item_id": self.entry_id(ref),
            "source": source,
            "source_record": record,
            "file_hashes": hashes,
            "proposition_fingerprint": digest(request),
            "source_bytes": total,
            "adapter": self.adapter(),
            **ancestry,
        }

    def damage_marker(self, namespace, key, reason=None):
        _identifier(key)
        directory = self.base / ("invalidated-" + namespace)
        if directory.resolve() != directory:
            raise ValueError("Proof damage marker path redirected")
        path = directory / (key + ".json")
        if reason is not None:
            directory.mkdir(exist_ok=True)
            atomic(path, {"reason": str(reason)[:1000]})
        return path.exists()

    def read(self, db, item_id):
        _identifier(item_id)
        stored = db.execute("SELECT body,hash FROM items WHERE id=?", (item_id,)).fetchone()
        if stored is None:
            raise ValueError("Unknown composition proof archive")
        try:
            row = self.decode(stored)
            if (
                row["item_id"] != item_id
                or row["schema"] != SCHEMA
                or self.entry_id(self.source_ref(row["source_ref"])) != item_id
            ):
                raise ValueError("Composition archive identity or schema changed")
            return row
        except (ValueError, KeyError, TypeError) as error:
            self.damage_marker("items", item_id, error)
            raise

    def prepare(self, raw):
        content = self.candidate(raw)
        plan = {
            "schema": SCHEMA,
            "content": content,
            "privacy": deepcopy(PRIVACY),
            "new_lean_calls": 0,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def archive(self, content):
        directory = self.directory(content["item_id"])
        if directory.exists():
            if self.archive_intact(content):
                return
            raise ValueError("Changed or incomplete composition archive already exists")
        source = self.source_store(content["source"]["origin"]).directory(
            content["source"]["run_id"]
        )
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
                        raise ValueError("Composition proof redirected during copy")
                    with path.open("rb") as stream:
                        raw = stream.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError("Composition proof exceeds copy budget")
                total += len(raw)
                if total > MAX_BYTES + 2097152 or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("Composition proof changed during archive copy")
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            stage.rename(directory)
        if not self.archive_intact(content):
            raise ValueError("Archived composition bytes differ from the confirmed proof")

    def admit(self, data):
        exact_object(
            data,
            {
                "origin",
                "run_id",
                "request_id",
                "plan_fingerprint",
                "reviewer",
                "confirmed",
                "rights_confirmed",
            },
            "Composition knowledge admission",
        )
        ref = self.source_ref({k: data[k] for k in ("origin", "run_id")})
        reviewer = text(data["reviewer"], 200, "Reviewer")
        if data["confirmed"] is not True or data["rights_confirmed"] is not True:
            raise ValueError("Confirm the complete proof and the right to store it privately")
        fingerprint = digest({"action": "admit", "data": data})
        with self.transaction(False) as db:
            previous = self.previous(db, data["request_id"], fingerprint)
        if previous:
            return self.report(previous)
        plan = self.prepare(ref)
        if plan["plan_fingerprint"] != data["plan_fingerprint"]:
            raise ValueError("Composition admission changed; preview and confirm again")
        content, item_id = plan["content"], plan["content"]["item_id"]
        with self.archive_lock():
            with self.transaction(False) as db:
                saved = db.execute("SELECT body,hash FROM items WHERE id=?", (item_id,)).fetchone()
            if saved and (
                self.decode(saved)["state"] != "ACTIVE" or self.decode(saved)["content"] != content
            ):
                raise ValueError("A retired or changed proof cannot be readmitted; run a new proof")
            self.archive(content)
            if self.candidate(ref) != content:
                raise ValueError("Composition source changed during archive copy")
            with self.transaction() as db:
                if not self.previous(db, data["request_id"], fingerprint):
                    saved = db.execute(
                        "SELECT body,hash FROM items WHERE id=?", (item_id,)
                    ).fetchone()
                    if saved:
                        row = self.decode(saved)
                        if row["state"] != "ACTIVE" or row["content"] != content:
                            raise ValueError("Composition knowledge changed during admission")
                    else:
                        if db.execute("SELECT count(*) FROM items").fetchone()[0] >= 500:
                            raise ValueError("Proof library is limited to 500 archives")
                        row = {
                            "schema": SCHEMA,
                            "item_id": item_id,
                            "source_ref": ref,
                            "state": "ACTIVE",
                            "version": 1,
                            "created_at": now(),
                            "content": content,
                            "privacy": deepcopy(PRIVACY),
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
                            "Confirmed complete composition E4 and private copy",
                        )
                        self.save(db, row)
                    self.register_request(db, data, fingerprint, item_id)
        return self.report(item_id)

    def reason(self, row):
        try:
            if self.damage_marker("items", row["item_id"]):
                return "Previously detected damaged composition archive ledger"
            if row["content"]["adapter"] != self.adapter():
                return "Composition archive adapter changed"
            current = self.candidate(row["source_ref"])
            if current != row["content"]:
                return "Composition source, alignment or transitive provenance changed"
            if not self.archive_intact(row["content"]):
                return "Archived composition proof bytes changed or are missing"
            return None
        except WorkerBusy:
            raise
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            return "Composition source storage unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return "Composition source unavailable: " + str(error)

    def report(self, item_id):
        with audit_scope(item_id):
            return super().report(item_id)

    def search(self, query="", include_inactive=False):
        value = {
            **super().search(query, include_inactive),
            "schema": SCHEMA,
            "privacy": deepcopy(PRIVACY),
        }
        for item in value["items"]:
            item["href"] = "/proof-library#" + item["item_id"]
            if item["state"] == "DELETED":
                item["title"] = "已移除的组合证明"
        return value

    def load_source(self, binding):
        exact_object(binding, {"alias", "item_id"}, "Proof library source")
        _identifier(binding["item_id"])
        if binding["item_id"].startswith("ml-knowledge-"):
            descriptor, raw = legacy_source(self.legacy.knowledge, binding)
            return {
                **descriptor,
                "origin": "mathlib-archive",
                "depth": 0,
                "ancestors": [
                    {k: descriptor[k] for k in ("item_id", "version", "archive_fingerprint")}
                ],
            }, raw
        if not binding["item_id"].startswith("proof-knowledge-"):
            raise ValueError("Unknown private proof archive namespace")
        row = self.report(binding["item_id"])
        if (row["state"], row["math_verdict"], row["evidence_level"], row["artifact_status"]) != (
            "ACTIVE",
            "PROVED",
            "E4",
            "MATCHES_RECORDED_HASHES",
        ):
            raise ValueError("Library source must be an intact active composition archive E4")
        content = row["content"]
        source = content["source"]
        if target(source["request"])["formal_statement"] != source["formal_statement"]:
            raise ValueError("Proof library source type differs from its complete signature")
        name = "proof/replay/proof.ndjson"
        raw = proof_data(
            self.directory(binding["item_id"]) / name, content["file_hashes"].get(name)
        )
        descriptor = {
            **binding,
            "origin": "composition-archive",
            "version": row["version"],
            "archive_fingerprint": digest(content),
            "source_fingerprint": source["source_fingerprint"],
            "request": source["request"],
            "formal_statement": source["formal_statement"],
            "alignment": source["source_alignment"],
            "data_sha256": hashlib.sha256(raw).hexdigest(),
            "data_bytes": len(raw),
            "depth": content["depth"],
        }
        descriptor["ancestors"] = [
            *deepcopy(content["ancestors"]),
            {k: descriptor[k] for k in ("item_id", "version", "archive_fingerprint")},
        ]
        return descriptor, raw
