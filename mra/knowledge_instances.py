"""Capture-free specializations of verified knowledge, checked as new full targets."""

import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge import KnowledgeStore
from .model_service import text_field
from .nat_ideas import NAME, render_formula
from .research import ResearchStore
from .research_runs import ResearchRunStore, ACTIVE
from .tasks import _identifier, _now

SCHEMA = "verified-knowledge-instance-v1"
RESERVED = {
    "by",
    "in",
    "do",
    "if",
    "then",
    "else",
    "let",
    "fun",
    "match",
    "with",
    "forall",
    "exists",
}


def storage_busy(error):
    # SQLite primary codes 5/6; Python 3.10 has no public error-code attributes.
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 255 in (5, 6)
    message = str(error).lower()
    return isinstance(error, sqlite3.OperationalError) and (
        message
        in (
            "database is locked",
            "database is busy",
            "database table is locked",
            "database schema is locked",
        )
        or message.startswith(("database table is locked:", "database schema is locked:"))
    )


def specialize(formula, domain, parameters, substitutions):
    """Replace a leading universal prefix, never discard conditions or capture names."""
    render_formula(formula, domain)
    if not isinstance(parameters, list) or len(parameters) > 4:
        raise ValueError("Declare at most four fresh universal parameters")
    if any(
        not isinstance(n, str) or not NAME.fullmatch(n) or n in RESERVED for n in parameters
    ) or len(set(parameters)) != len(parameters):
        raise ValueError("Parameters need unique, valid variable names")
    names = set()

    def binders(value):
        if isinstance(value, dict):
            if value.get("op") in ("forall", "exists"):
                names.add(value["name"])
            for child in value.values():
                binders(child)

    binders(formula)
    if names.intersection(parameters):
        raise ValueError("Fresh parameters must not capture any source quantifier")
    if not isinstance(substitutions, list) or not 1 <= len(substitutions) <= 8:
        raise ValueError("Instantiate one to eight leading universal variables")
    body, replacements, used = formula, {}, set()
    for substitution in substitutions:
        exact_object(substitution, {"variable", "term"}, "Universal substitution")
        if body.get("op") != "forall" or substitution["variable"] != body["name"]:
            raise ValueError("Substitutions must follow the exact leading forall prefix")
        term = substitution["term"]
        probe = {"op": "eq", "left": term, "right": {"op": "num", "value": 0}}
        for name in reversed(parameters):
            probe = {"op": "forall", "name": name, "body": probe}
        render_formula(probe, domain)

        def variables(value):
            if isinstance(value, dict):
                if value.get("op") == "var":
                    used.add(value["name"])
                for child in value.values():
                    variables(child)

        variables(term)
        replacements[substitution["variable"]] = term
        body = body["body"]
    if used != set(parameters):
        raise ValueError("Every new universal parameter must occur in a substitution")
    count = 0

    def substitute(value, depth=0, replacing=True):
        nonlocal count
        if not isinstance(value, dict):
            return value
        if replacing and value.get("op") == "var" and value["name"] in replacements:
            return substitute(replacements[value["name"]], depth, False)
        count += 1
        if count + len(parameters) > 256 or depth + len(parameters) > 24:
            raise ValueError("Instantiated formula exceeds the supported structural budget")
        return {key: substitute(child, depth + 1, replacing) for key, child in value.items()}

    result = substitute(body)
    for name in reversed(parameters):
        result = {"op": "forall", "name": name, "body": result}
    render_formula(result, domain)
    return result


class InstanceView:
    """The frozen scheduler consumes a single-node view without implicit research writes."""

    branch = staticmethod(ResearchStore.branch)
    export_node = ResearchStore.export_node

    def __init__(self, owner, research):
        self.owner, self.underlying = owner, research

    def report(self, instance_id):
        draft = self.owner.draft(instance_id)
        p = draft["preview"]
        node = {
            "id": "instance",
            "kind": "lemma",
            "title": p["input"]["title"],
            "statement": p["input"]["statement"],
            "domain": p["domain"],
            "formula": p["formula"],
            "depends_on": [],
            "context": [],
            "notes": "Explicit specialization of " + p["citation"]["knowledge_id"],
        }
        return {
            "project_id": instance_id,
            "title": p["input"]["title"],
            "question": p["input"]["statement"],
            "version": 1,
            "branches": {
                "main": {
                    "branch_id": "main",
                    "label": "知识实例",
                    "revision": 1,
                    "nodes": [node],
                    "bindings": {},
                    "node_states": {"instance": {"math_verdict": "UNKNOWN", "status": "READY"}},
                }
            },
        }

    def source(self, review_id, key):
        return self.underlying.source(review_id, key)


class KnowledgeInstanceStore(ResearchRunStore):
    def __init__(self, root):
        super().__init__(root)
        self.base = self.root / "artifacts/knowledge-instances"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "instances.sqlite3"
        self.knowledge = KnowledgeStore(self.root)
        self.research = InstanceView(self, self.research)
        self.sweep_cursor = 0
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )

    def read_instance_row(self, row):
        if row is None:
            raise ValueError("Unknown knowledge instance ID")
        body = strict_json(row["body"])
        if body["instance_id"] != row["id"] or digest(body) != row["hash"]:
            raise ValueError("Knowledge instance ledger changed")
        return body

    def read_only(self, sql, values=()):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Instance storage must not be redirected")
        with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            return db.execute(sql, values).fetchall()

    def draft(self, instance_id):
        rows = self.read_only("SELECT * FROM instances WHERE id=?", (_identifier(instance_id),))
        return self.read_instance_row(rows[0] if rows else None)

    def knowledge_rows(self):
        store = self.knowledge
        if store.base.resolve() != store.base or store.path.is_symlink():
            raise ValueError("Knowledge storage must not be redirected")
        with closing(sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            return store.read_rows(db)

    def invalidate_knowledge(self, item):
        # Persist genuine mismatches using the original ledger/event/cascade protocol.
        store = self.knowledge
        with store.transaction() as db:
            items = store.read_rows(db)
            current = items.get(item["item_id"])
            if current and current["state"] == "ACTIVE" and current["content"] == item["content"]:
                current["state"] = "INVALIDATED"
                store.event(
                    current,
                    "SOURCE_OR_PROOF_INVALIDATED",
                    "internal-evidence-audit",
                    "Targeted instance audit found changed source, proof or dependency; no automatic revival",
                )
                store.save(db, current)
            store.cascade(db, items)

    def checked_knowledge(self, knowledge_id):
        """Audit only the cited dependency closure without taking a global writer lock."""
        items = self.knowledge_rows()
        verified, visiting, sources = set(), set(), {}

        def visit(key):
            if key in visiting:
                raise ValueError("Cited knowledge dependency cycle")
            body = items.get(key)
            if not body or body["state"] != "ACTIVE" or not body["content"]:
                raise ValueError("Only currently ACTIVE verified knowledge can be instantiated")
            if key in verified:
                return body
            visiting.add(key)
            try:
                for dependency in body["dependencies"]:
                    visit(dependency)
                review_id = body["source_ref"]["review_id"]
                if review_id not in sources:
                    sources[review_id] = {
                        c["item_id"]: c for c in self.knowledge.verified_source(review_id)
                    }
                if sources[review_id].get(key) != body[
                    "content"
                ] or not self.knowledge.archive_intact(body):
                    raise ValueError("Cited source or archived proof bytes changed")
            except sqlite3.Error:
                raise
            except (ValueError, OSError, RuntimeError, KeyError, TypeError):
                self.invalidate_knowledge(body)
                raise
            visiting.remove(key)
            verified.add(key)
            return body

        item = visit(knowledge_id)
        latest = self.knowledge_rows()
        if any(latest.get(key) != items[key] for key in verified):
            raise ValueError("Cited knowledge changed during audit")
        return self.knowledge.public(item)

    def citation(self, knowledge_id):
        item = self.checked_knowledge(_identifier(knowledge_id))
        if item["state"] != "ACTIVE" or item["math_verdict"] != "PROVED" or not item["content"]:
            raise ValueError("Only currently ACTIVE, verified E4 knowledge can be instantiated")
        content = item["content"]
        review = self.papers.raw(content["source"]["review_id"])
        parent = review.get("research_run_id")
        if parent and self.read_only("SELECT id FROM runs WHERE id=?", (parent,)):
            raise ValueError(
                "Recursive instance reuse is not enabled; select the original verified lemma"
            )
        return {
            "knowledge_id": knowledge_id,
            "version": item["version"],
            "content": content,
            "content_fingerprint": digest(content),
            "knowledge_implementation_sha256": file_hash(Path(__file__).with_name("knowledge.py")),
        }

    def preview(self, data):
        exact_object(
            data,
            {"knowledge_id", "title", "statement", "parameters", "substitutions"},
            "Knowledge specialization",
        )
        text_field(data["title"], "instance title", 200)
        text_field(data["statement"], "new original statement", 8000)
        citation = self.citation(data["knowledge_id"])
        domain = citation["content"]["domain"]
        formula = specialize(
            citation["content"]["formula"], domain, data["parameters"], data["substitutions"]
        )
        value = {
            "schema": SCHEMA,
            "input": data,
            "citation": citation,
            "domain": domain,
            "formula": formula,
            "formal_statement": render_formula(formula, domain),
            "implementation_sha256": file_hash(Path(__file__)),
            "model_calls": 0,
            "lean_calls": 0,
            "scope": "Explicit universal specialization; all conditions retained; new full target must be independently checked; archived proof terms are not imported",
        }
        return {**value, "preview_fingerprint": digest(value)}

    def create(self, data):
        exact_object(
            data, {"request_id", "input", "preview_fingerprint"}, "Save knowledge instance"
        )
        _identifier(data["request_id"])
        request_hash = digest(data)
        rows = self.read_only("SELECT * FROM instances WHERE request_id=?", (data["request_id"],))
        if rows:
            if rows[0]["request_hash"] != request_hash:
                raise ValueError("Instance request ID belongs to different content")
            return self.report_instance(rows[0]["id"])
        preview = self.preview(data["input"])
        if preview["preview_fingerprint"] != data["preview_fingerprint"]:
            raise ValueError("Source or specialization changed; prepare a new preview")
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM instances WHERE request_id=?", (data["request_id"],)
            ).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("Instance request ID belongs to different content")
                instance_id = old["id"]
            else:
                if db.execute("SELECT count(*) FROM instances").fetchone()[0] >= 100:
                    raise ValueError("Instance draft budget exhausted")
                instance_id = "instance-" + uuid.uuid4().hex
                body = {
                    "instance_id": instance_id,
                    "schema": SCHEMA,
                    "preview": preview,
                    "state": "DRAFT",
                    "created_at": _now(),
                    "invalid_reason": None,
                }
                db.execute(
                    "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
                    (
                        instance_id,
                        data["request_id"],
                        request_hash,
                        canonical(body).decode(),
                        digest(body),
                    ),
                )
        return self.report_instance(instance_id)

    def source_reason(self, draft):
        try:
            if draft["state"] == "INVALIDATED":
                return draft["invalid_reason"]
            p = draft["preview"]
            if p["implementation_sha256"] != file_hash(Path(__file__)):
                return "Knowledge instantiation protocol changed"
            if self.preview(p["input"]) != p:
                return "Cited knowledge, substitution or full proposition changed"
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            return "Cited source storage failed: " + str(error)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
            return "Cited source is unavailable: " + str(error)
        return None

    def audit_instance(self, instance_id):
        draft = self.draft(instance_id)
        reason = self.source_reason(draft)
        if reason:
            if draft["state"] != "INVALIDATED":
                with self.transaction() as db:
                    row = db.execute(
                        "SELECT * FROM instances WHERE id=?", (instance_id,)
                    ).fetchone()
                    current = self.read_instance_row(row)
                    current.update(state="INVALIDATED", invalid_reason=reason)
                    db.execute(
                        "UPDATE instances SET body=?, hash=? WHERE id=?",
                        (canonical(current).decode(), digest(current), instance_id),
                    )
                draft = current
            for row in self.read_only("SELECT * FROM runs"):
                run = self.read(row)
                if run["plan"]["project_id"] == instance_id and run["state"] in (
                    *ACTIVE,
                    "COMPLETED",
                    "BUDGET_EXHAUSTED",
                ):
                    self.stop(run["run_id"], "INVALIDATED", reason)
        return draft

    def reconcile(self):
        for row in self.read_only("SELECT id FROM instances ORDER BY rowid"):
            self.audit_instance(row["id"])

    def prepare(self, instance_id, data):
        exact_object(data, {"branch_id", "items", "budget"}, "Instance execution plan")
        items = data["items"]
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("Select the one complete instance target")
        exact_object(items[0], {"node_id", "plan"}, "Instance check")
        exact_object(items[0]["plan"], {"direction", "strategy", "witnesses"}, "Instance strategy")
        exact_object(
            data["budget"], {"max_checks", "worker_seconds", "wall_seconds"}, "Instance budget"
        )
        draft = self.audit_instance(instance_id)
        if draft["state"] != "DRAFT":
            raise ValueError("Knowledge instance is permanently invalidated")
        if data["branch_id"] != "main" or items[0]["node_id"] != "instance":
            raise ValueError("A knowledge instance checks exactly its one complete target")
        if (
            data["items"][0].get("plan", {}).get("direction") != "prove"
            or data.get("budget", {}).get("max_checks") != 1
            or data.get("budget", {}).get("worker_seconds") != 30
        ):
            raise ValueError(
                "Instance checking requires prove, one check and thirty worker seconds"
            )
        plan = super().prepare(instance_id, data)
        plan.pop("plan_fingerprint")
        plan.update(
            usage_schema=SCHEMA,
            instance_fingerprint=digest(draft["preview"]),
            citation=draft["preview"]["citation"],
            substitution=draft["preview"]["input"],
            instance_implementation_sha256=file_hash(Path(__file__)),
        )
        return {**plan, "plan_fingerprint": digest(plan)}

    def invalid_reason(self, body):
        try:
            plan = body["plan"]
            draft = self.draft(plan["project_id"])
            if (
                plan.get("usage_schema") != SCHEMA
                or plan.get("instance_implementation_sha256") != file_hash(Path(__file__))
                or plan.get("instance_fingerprint") != digest(draft["preview"])
            ):
                return "Confirmed knowledge instance protocol changed"
            reason = self.source_reason(draft)
            if reason:
                return reason
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            return "Instance storage audit failed: " + str(error)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return "Instance audit failed: " + str(error)
        return super().invalid_reason(body)

    def report_instance(self, instance_id):
        draft = self.audit_instance(instance_id)
        runs = self.list_runs(instance_id)
        latest = runs[0] if runs else None
        node = latest["nodes"][0] if latest else None
        return {
            **copy.deepcopy(draft),
            "runs": runs,
            "math_verdict": node["math_verdict"] if node else "UNKNOWN",
            "results_available": bool(node and node["results_available"]),
            "run_state": latest["state"] if latest else "NOT_SUBMITTED",
            "model_calls": 0,
            "historical_record": True,
            "fresh_attestation": False,
        }

    def list_instances(self, knowledge_id=None):
        if knowledge_id is not None:
            _identifier(knowledge_id)
        ids = [
            self.read_instance_row(row)["instance_id"]
            for row in self.read_only("SELECT * FROM instances ORDER BY rowid DESC")
            if knowledge_id is None
            or self.read_instance_row(row)["preview"]["input"]["knowledge_id"] == knowledge_id
        ][:30]
        return [self.report_instance(instance_id) for instance_id in ids]

    def work_once(self, run_id=None):
        rows = self.read_only("SELECT id FROM instances ORDER BY rowid")
        if rows:
            self.audit_instance(rows[self.sweep_cursor % len(rows)]["id"])
            self.sweep_cursor += 1
        return super().work_once(run_id)
