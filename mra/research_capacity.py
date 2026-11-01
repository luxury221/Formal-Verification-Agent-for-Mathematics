"""Reversible project organization; legacy mathematical records remain verifier-owned."""

from copy import deepcopy
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .model_service import integer_field, text_field
from .research import ResearchStore
from .tasks import _identifier, _now

SCHEMA = "research-project-capacity-v1"
LIMITS = {
    "active_projects": 100,
    "retained_projects": 10000,
    "lifecycle_requests": 50000,
}
TABLES = {
    "research_capacity_identity",
    "research_project_lifecycle",
    "research_lifecycle_requests",
}


class ManagedResearchStore(ResearchStore):
    """Old projects and evidence use the original body, schema and implementation."""

    def __init__(self, root):
        super().__init__(root)
        with self.transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables & TABLES and not TABLES <= tables:
                raise ValueError("Project lifecycle schema is incomplete; restore its backup")
            if not tables & TABLES:
                db.execute(
                    "CREATE TABLE research_capacity_identity (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL)"
                )
                db.execute("INSERT INTO research_capacity_identity VALUES (1,?)", (SCHEMA,))
                db.execute(
                    "CREATE TABLE research_project_lifecycle (project_id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE research_lifecycle_requests (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, UNIQUE(project_id,revision))"
                )
            self._schema(db)

    @staticmethod
    def _schema(db):
        rows = db.execute("SELECT id,schema FROM research_capacity_identity").fetchall()
        if [tuple(r) for r in rows] != [(1, SCHEMA)]:
            raise ValueError("Unsupported project lifecycle identity")

    @classmethod
    def state(cls, db, key):
        _identifier(key)
        row = db.execute(
            "SELECT body,hash FROM research_project_lifecycle WHERE project_id=?",
            (key,),
        ).fetchone()
        count, latest = db.execute(
            "SELECT count(*),max(revision) FROM research_lifecycle_requests WHERE project_id=?",
            (key,),
        ).fetchone()
        if row is None:
            if count:
                raise ValueError("Project lifecycle record is missing; history cannot revive it")
            return {
                "schema": SCHEMA,
                "project_id": key,
                "state": "ACTIVE",
                "revision": 0,
                "reviewer": None,
                "reason": None,
                "updated_at": None,
            }
        value = strict_json(row["body"])
        exact_object(
            value,
            {
                "schema",
                "project_id",
                "state",
                "revision",
                "reviewer",
                "reason",
                "updated_at",
            },
            "Project lifecycle",
        )
        if (
            digest(value) != row["hash"]
            or value["schema"] != SCHEMA
            or value["project_id"] != key
            or value["state"] not in ("ACTIVE", "ARCHIVED")
            or type(value["revision"]) is not int
            or value["revision"] != count
            or count != latest
        ):
            raise ValueError("Project lifecycle record or revision history changed")
        return value

    def _inventory(self, db):
        self._schema(db)
        for table in ("research_project_lifecycle", "research_lifecycle_requests"):
            if db.execute(
                f"SELECT 1 FROM {table} s LEFT JOIN projects p ON s.project_id=p.id WHERE p.id IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("Project lifecycle references a missing project")
        projects = []
        ids = [r[0] for r in db.execute("SELECT id FROM projects ORDER BY rowid DESC")]
        if len(ids) > LIMITS["retained_projects"]:
            raise ValueError("Retained project budget exceeded")
        for key in ids:
            body = self.read(db, key)
            lifecycle = self.state(db, key)
            projects.append(
                {
                    "project_id": key,
                    "title": body["title"],
                    "question": body["question"],
                    "created_at": body["created_at"],
                    "version": body["version"],
                    "branch_count": len(body["branches"]),
                    "state": "RESEARCHING",
                    "lifecycle": lifecycle,
                }
            )
        return projects

    def capacity_catalog(self):
        with self.transaction() as db:
            projects = self._inventory(db)
        active = sum(p["lifecycle"]["state"] == "ACTIVE" for p in projects)
        return {
            "schema": SCHEMA,
            "limits": dict(LIMITS),
            "counts": {
                "active": active,
                "archived": len(projects) - active,
                "retained": len(projects),
                "available": max(0, LIMITS["active_projects"] - active),
            },
            "projects": projects,
            "scope": "Organization and active capacity only; no deletion, evidence revocation or mathematical verdict",
        }

    def list_projects(self):
        return [
            p for p in self.capacity_catalog()["projects"] if p["lifecycle"]["state"] == "ACTIVE"
        ]

    def report(self, project_id):
        report = super().report(project_id)
        with self.transaction() as db:
            self._schema(db)
            lifecycle = self.state(db, project_id)
        return {**report, "lifecycle": lifecycle}

    def create(self, data):
        def operation(db, _):
            text_field(data["title"], "project title", 200)
            text_field(data["question"], "original research question", 12000)
            projects = self._inventory(db)
            if len(projects) >= LIMITS["retained_projects"]:
                raise ValueError(
                    "Retained project budget exhausted; export and provision another workspace"
                )
            if (
                sum(p["lifecycle"]["state"] == "ACTIVE" for p in projects)
                >= LIMITS["active_projects"]
            ):
                raise ValueError(
                    "Active project budget exhausted; archive an existing project in /research"
                )
            body = {
                "schema": "private-research-space-v1",
                "project_id": "research-" + uuid.uuid4().hex,
                "title": data["title"],
                "question": data["question"],
                "version": 1,
                "created_at": _now(),
                "branches": {},
                "events": [],
                "privacy": {
                    "visibility": "private-local",
                    "training_allowed": False,
                    "sharing_allowed": False,
                },
            }
            branch = {
                "branch_id": "main",
                "label": "主研究线",
                "parent": None,
                "revision": 1,
                "nodes": [],
                "bindings": {},
            }
            body["branches"]["main"] = branch
            self.snapshot(db, body, branch)
            self.event(body, "CREATED")
            return body

        return self.mutate(None, "create", data, {"title", "question"}, operation)

    def change_lifecycle(self, key, action, data):
        _identifier(key)
        if action not in ("archive", "restore"):
            raise ValueError("Choose archive or restore")
        exact_object(
            data,
            {"request_id", "expected_revision", "reviewer", "reason", "confirmed"},
            "Project lifecycle change",
        )
        _identifier(data["request_id"])
        if type(data["expected_revision"]) is not int:
            raise ValueError("Lifecycle revision must be an integer")
        integer_field(
            data["expected_revision"],
            "lifecycle revision",
            0,
            LIMITS["lifecycle_requests"],
        )
        text_field(data["reviewer"], "Project organizer", 200)
        text_field(data["reason"], "Organization reason", 1000)
        if data["confirmed"] is not True:
            raise ValueError("Explicit project organization confirmation is required")
        fingerprint = digest({"project_id": key, "action": action, "data": data})
        with self.transaction() as db:
            self._schema(db)
            self.read(db, key)
            current = self.state(db, key)
            previous = db.execute(
                "SELECT project_id,fingerprint FROM research_lifecycle_requests WHERE id=?",
                (data["request_id"],),
            ).fetchone()
            if previous:
                if previous["project_id"] != key or previous["fingerprint"] != fingerprint:
                    raise ValueError("Lifecycle request ID belongs to different content")
                return {"lifecycle": deepcopy(current), "idempotent_replay": True}
            if current["revision"] != data["expected_revision"]:
                raise ValueError("Lifecycle revision conflict; reload before saving")
            desired = "ARCHIVED" if action == "archive" else "ACTIVE"
            if desired == current["state"]:
                raise ValueError("Project already has this organization state; reload")
            projects = self._inventory(db)
            if (
                action == "restore"
                and sum(p["lifecycle"]["state"] == "ACTIVE" for p in projects)
                >= LIMITS["active_projects"]
            ):
                raise ValueError(
                    "Active project budget exhausted; archive another project before restoring"
                )
            if (
                db.execute("SELECT count(*) FROM research_lifecycle_requests").fetchone()[0]
                >= LIMITS["lifecycle_requests"]
            ):
                raise ValueError("Project lifecycle history budget exhausted")
            value = {
                "schema": SCHEMA,
                "project_id": key,
                "state": desired,
                "revision": current["revision"] + 1,
                "reviewer": data["reviewer"],
                "reason": data["reason"],
                "updated_at": _now(),
            }
            db.execute(
                "INSERT OR REPLACE INTO research_project_lifecycle VALUES (?,?,?)",
                (key, canonical(value).decode(), digest(value)),
            )
            db.execute(
                "INSERT INTO research_lifecycle_requests VALUES (?,?,?,?)",
                (data["request_id"], key, value["revision"], fingerprint),
            )
        return {"lifecycle": value, "idempotent_replay": False}
