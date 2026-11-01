"""Versioned research graphs; proof references are exact historical evidence, not inference."""

from contextlib import contextmanager
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .model_service import integer_field, text_field
from .nat_ideas import render_formula, target_from_dict
from .paper_review import PaperReviewStore, prepare_review
from .tasks import _identifier, _now

SCHEMA = "private-research-space-v1"
E4 = "E4_ALIGNED_AND_VERIFIED"
LIMITS = {"projects": 100, "branches": 8, "nodes": 24, "revisions_per_branch": 64}


def validate_nodes(nodes):
    if not isinstance(nodes, list) or len(nodes) > LIMITS["nodes"]:
        raise ValueError("A research branch supports at most 24 nodes")
    if len(canonical(nodes)) > 131072:
        raise ValueError("Research graph exceeds 128 KiB")
    by_id = {}
    for node in nodes:
        exact_object(
            node,
            {
                "id",
                "kind",
                "title",
                "statement",
                "domain",
                "formula",
                "depends_on",
                "context",
                "notes",
            },
            "Research node",
        )
        key = _identifier(node["id"])
        if key in by_id or len(key) > 32:
            raise ValueError("Node IDs must be unique and at most 32 characters")
        if node["kind"] not in ("problem", "subproblem", "lemma", "definition"):
            raise ValueError("Unknown research node kind")
        text_field(node["title"], "node title", 200)
        text_field(node["statement"], "node original statement", 8000)
        if not isinstance(node["notes"], str) or len(node["notes"]) > 4000:
            raise ValueError("Node notes exceed 4000 characters")
        for field in ("depends_on", "context"):
            values = node[field]
            if not isinstance(values, list) or len(values) > 12:
                raise ValueError("Expected at most 12 node references")
            for value in values:
                _identifier(value)
            if len(set(values)) != len(values):
                raise ValueError("Duplicate node reference")
        if node["kind"] == "definition":
            if any(node[k] for k in ("depends_on", "context")) or any(
                node[k] is not None for k in ("domain", "formula")
            ):
                raise ValueError(
                    "Definitions are contextual text, not checked formulas or premises"
                )
        else:
            render_formula(node["formula"], node["domain"])
        by_id[key] = node
    for node in nodes:
        for key in (*node["depends_on"], *node["context"]):
            if key not in by_id:
                raise ValueError("Dangling research node reference")
        if any(by_id[key]["kind"] == "definition" for key in node["depends_on"]):
            raise ValueError("Definition notes belong in context, not proven prerequisites")
        if any(by_id[key]["kind"] != "definition" for key in node["context"]):
            raise ValueError("Context references must name definition notes")
    order, visiting, seen = [], set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("Research dependencies must be acyclic")
        if key in seen:
            return
        visiting.add(key)
        for dependency in (*by_id[key]["depends_on"], *by_id[key]["context"]):
            visit(dependency)
        visiting.remove(key)
        seen.add(key)
        order.append(key)

    for key in by_id:
        visit(key)
    return order


def node_fingerprint(node, nodes):
    by_id = {item["id"]: item for item in nodes}
    return digest({"node": node, "context": [by_id[key] for key in node["context"]]})


class ResearchStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "research.sqlite3"
        self.papers = PaperReviewStore(self.root)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, project_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS revisions (project_id TEXT, branch_id TEXT, revision INTEGER, body TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(project_id, branch_id, revision))"
            )

    @contextmanager
    def transaction(self):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Research storage must not be redirected")
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
    def read(db, project_id):
        row = db.execute("SELECT * FROM projects WHERE id=?", (_identifier(project_id),)).fetchone()
        if row is None:
            raise ValueError("Unknown research project")
        body = strict_json(row["body"])
        if digest(body) != row["hash"] or body["project_id"] != row["id"]:
            raise ValueError("Research ledger changed")
        return body

    @staticmethod
    def save(db, body):
        if len(canonical(body)) > 2097152:
            raise ValueError("Research record exceeds 2 MiB; export and start a new project")
        db.execute(
            "INSERT OR REPLACE INTO projects VALUES (?, ?, ?)",
            (body["project_id"], canonical(body).decode(), digest(body)),
        )

    @staticmethod
    def event(body, kind, **details):
        if len(body["events"]) >= 1024:
            raise ValueError("Research event budget exhausted; export before continuing")
        body["events"].append(
            {"sequence": len(body["events"]) + 1, "at": _now(), "kind": kind, **details}
        )

    @staticmethod
    def branch(body, branch_id):
        _identifier(branch_id)
        if branch_id not in body["branches"]:
            raise ValueError("Unknown research branch")
        return body["branches"][branch_id]

    @staticmethod
    def snapshot(db, body, branch):
        version = {"nodes": branch["nodes"], "label": branch["label"], "at": _now()}
        db.execute(
            "INSERT INTO revisions VALUES (?, ?, ?, ?, ?)",
            (
                body["project_id"],
                branch["branch_id"],
                branch["revision"],
                canonical(version).decode(),
                digest(version),
            ),
        )

    def mutate(self, project_id, action, data, fields, operation):
        exact_object(data, {"request_id", *fields}, "Research mutation")
        _identifier(data["request_id"])
        fingerprint = digest({"project_id": project_id, "action": action, "data": data})
        if project_id is not None:
            self.report(project_id)
        with self.transaction() as db:
            previous = db.execute(
                "SELECT * FROM requests WHERE id=?", (data["request_id"],)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("Request ID belongs to different research content")
                result_id = previous["project_id"]
            else:
                body = self.read(db, project_id) if project_id is not None else None
                if body is not None:
                    integer_field(data["expected_version"], "expected version", 1, 1000000)
                    if data["expected_version"] != body["version"]:
                        raise ValueError("Research version conflict; reload before saving")
                body = operation(db, body)
                if project_id is not None:
                    body["version"] += 1
                self.save(db, body)
                result_id = body["project_id"]
                db.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (data["request_id"], fingerprint, result_id),
                )
        return self.report(result_id)

    def create(self, data):
        def operation(db, _):
            text_field(data["title"], "project title", 200)
            text_field(data["question"], "original research question", 12000)
            if db.execute("SELECT count(*) FROM projects").fetchone()[0] >= LIMITS["projects"]:
                raise ValueError("Research project budget exhausted")
            body = {
                "schema": SCHEMA,
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

    def edit(self, project_id, data):
        def operation(db, body):
            validate_nodes(data["nodes"])
            branch = self.branch(body, data["branch_id"])
            if branch["revision"] >= LIMITS["revisions_per_branch"]:
                raise ValueError("Branch revision budget exhausted")
            branch["nodes"] = copy.deepcopy(data["nodes"])
            branch["revision"] += 1
            self.snapshot(db, body, branch)
            self.event(
                body, "GRAPH_SAVED", branch_id=branch["branch_id"], revision=branch["revision"]
            )
            return body

        return self.mutate(
            project_id, "edit", data, {"expected_version", "branch_id", "nodes"}, operation
        )

    def fork(self, project_id, data):
        def operation(db, body):
            text_field(data["label"], "branch label", 120)
            if len(body["branches"]) >= LIMITS["branches"]:
                raise ValueError("Research branch budget exhausted")
            parent = self.branch(body, data["branch_id"])
            key = "branch-" + uuid.uuid4().hex
            branch = {
                "branch_id": key,
                "label": data["label"],
                "parent": {"branch_id": parent["branch_id"], "revision": parent["revision"]},
                "revision": 1,
                "nodes": copy.deepcopy(parent["nodes"]),
                "bindings": {},
            }
            body["branches"][key] = branch
            self.snapshot(db, body, branch)
            self.event(body, "BRANCH_CREATED", branch_id=key, parent=branch["parent"])
            return body

        return self.mutate(
            project_id, "fork", data, {"expected_version", "branch_id", "label"}, operation
        )

    def source(self, review_id, key):
        _identifier(review_id)
        _identifier(key)
        report = self.papers.report(review_id)
        if (
            not report["results_available"]
            or report["artifact_status"] != "MATCHES_RECORDED_HASHES"
        ):
            raise ValueError("Source review has no available aligned evidence")
        obligation = next(
            (item for item in report["plan"]["obligations"] if item["key"] == key), None
        )
        check = next((item for item in report["checks"] if item["key"] == key), None)
        if (
            not obligation
            or not check
            or check["result"]["evidence_level"] != E4
            or check["result"]["run_status"] != "COMPLETED"
            or check["result"]["math_verdict"] not in ("PROVED", "REFUTED")
        ):
            raise ValueError("Source obligation needs a definite real E4 verdict")
        detail = self.papers.evidence(review_id, key)
        if not detail["available"]:
            raise ValueError("Source evidence became unavailable")
        proof, receipt = detail["report"], detail["receipt"]["payload"]
        if (
            proof["obligation"] != obligation
            or proof["task"] != check["result"]
            or not proof["evidence"]
            or proof["evidence"]["level"] != E4
            or proof["alignment"]["reviewer"] != report["confirmation"]["reviewer"]
            or receipt["candidate_pid"] == receipt["replay"]["pid"]
            or not receipt["replay"]["audit"]["type_matches"]
        ):
            raise ValueError("Source target, alignment and independent replay disagree")
        target = target_from_dict(obligation["target"])
        claim, formalization = target.contract(
            "prove" if check["result"]["math_verdict"] == "PROVED" else "refute"
        )
        if (
            proof["evidence"]["claim_fingerprint"] != claim.fingerprint
            or proof["evidence"]["formalization_fingerprint"] != digest(asdict(formalization))
            or proof["plan_fingerprint"] != report["plan"]["plan_fingerprint"]
        ):
            raise ValueError("Source evidence does not bind the exact checked target")
        return {
            "review_id": review_id,
            "key": key,
            "domain": target.DOMAIN,
            "formula": json.loads(target.formula_json),
            "formal_statement": obligation["formal_statement"],
            "quote": obligation["quote"],
            "math_verdict": check["result"]["math_verdict"],
            "source_evidence_level": E4,
            "plan_fingerprint": report["plan"]["plan_fingerprint"],
            "file_hashes": check["file_hashes"],
            "source_reviewer": proof["alignment"]["reviewer"],
            "runtime_snapshot_fingerprint": report["runtime_snapshot_fingerprint"],
        }

    def report(self, project_id):
        cache = {}
        with self.transaction() as db:
            body = self.read(db, project_id)
            changed = False
            for branch in body["branches"].values():
                order = validate_nodes(branch["nodes"])
                nodes = {item["id"]: item for item in branch["nodes"]}
                bindings = branch["bindings"]
                for key in [*order, *(key for key in bindings if key not in nodes)]:
                    binding = bindings.get(key)
                    if not binding or binding["state"] != "ACTIVE":
                        continue
                    reference = binding["source_ref"]
                    source_key = (reference["review_id"], reference["key"])
                    if source_key not in cache:
                        try:
                            cache[source_key] = self.source(*source_key)
                        except (
                            OSError,
                            ValueError,
                            RuntimeError,
                            KeyError,
                            TypeError,
                            sqlite3.Error,
                        ):
                            cache[source_key] = None
                    node = nodes.get(key)
                    valid = (
                        node is not None
                        and node_fingerprint(node, branch["nodes"]) == binding["node_fingerprint"]
                        and binding["implementation_sha256"] == file_hash(Path(__file__))
                        and digest(cache[source_key]) == binding["source_fingerprint"]
                    )
                    if valid:
                        valid = all(
                            dep in bindings
                            and bindings[dep]["state"] == "ACTIVE"
                            and bindings[dep]["source"]["math_verdict"] == "PROVED"
                            and digest(bindings[dep]) == binding["dependency_fingerprints"].get(dep)
                            for dep in node["depends_on"]
                        )
                    if not valid:
                        binding["state"] = "INVALIDATED"
                        self.event(
                            body, "BINDING_INVALIDATED", branch_id=branch["branch_id"], node_id=key
                        )
                        changed = True
            if changed:
                body["version"] += 1
                self.save(db, body)
        result = copy.deepcopy(body)
        for branch in result["branches"].values():
            bindings = branch["bindings"]
            states = {}
            for node in branch["nodes"]:
                binding = bindings.get(node["id"])
                blocked = [
                    dep
                    for dep in node["depends_on"]
                    if dep not in bindings
                    or bindings[dep]["state"] != "ACTIVE"
                    or bindings[dep]["source"]["math_verdict"] != "PROVED"
                ]
                active = binding and binding["state"] == "ACTIVE" and not blocked
                status = (
                    "DOCUMENTED"
                    if node["kind"] == "definition"
                    else "BLOCKED"
                    if blocked
                    else binding["state"]
                    if binding
                    else "READY"
                )
                states[node["id"]] = {
                    "status": status,
                    "blocked_by": blocked,
                    "math_verdict": binding["source"]["math_verdict"] if active else "UNKNOWN",
                    "formal_statement": render_formula(node["formula"], node["domain"])
                    if node["formula"] is not None
                    else None,
                    "fresh_attestation": False,
                }
            branch["node_states"] = states
            branch["progress"] = {
                "total": len(states),
                "proved": sum(s["math_verdict"] == "PROVED" for s in states.values()),
                "refuted": sum(s["math_verdict"] == "REFUTED" for s in states.values()),
                "definitions": sum(s["status"] == "DOCUMENTED" for s in states.values()),
            }
        return {
            **result,
            "limits": LIMITS,
            "model_calls": 0,
            "lean_calls": 0,
            "fresh_attestation": False,
        }

    def prepare_binding(self, project_id, data):
        exact_object(
            data,
            {"expected_version", "branch_id", "node_id", "review_id", "key"},
            "Research evidence preview",
        )
        integer_field(data["expected_version"], "expected version", 1, 1000000)
        body = self.report(project_id)
        if data["expected_version"] != body["version"]:
            raise ValueError("Research version conflict; reload before binding")
        branch = self.branch(body, data["branch_id"])
        node = next((node for node in branch["nodes"] if node["id"] == data["node_id"]), None)
        if node is None or node["kind"] == "definition":
            raise ValueError("Select a typed research problem or lemma")
        if branch["node_states"][node["id"]]["blocked_by"]:
            raise ValueError("Unverified research prerequisites block this node")
        source = self.source(data["review_id"], data["key"])
        if source["domain"] != node["domain"] or source["formula"] != node["formula"]:
            raise ValueError(
                "Source must match the exact domain and full formula including assumptions"
            )
        plan = {
            "schema": SCHEMA,
            "project_id": project_id,
            **data,
            "node": node,
            "node_fingerprint": node_fingerprint(node, branch["nodes"]),
            "source": source,
            "source_fingerprint": digest(source),
            "implementation_sha256": file_hash(Path(__file__)),
            "dependency_fingerprints": {
                dep: digest(branch["bindings"][dep]) for dep in node["depends_on"]
            },
            "scope": "Historical exact proof reference; prerequisites are not inserted as Lean assumptions; named local alignment required",
        }
        return {**plan, "binding_fingerprint": digest(plan), "model_calls": 0, "lean_calls": 0}

    def bind(self, project_id, data):
        def operation(_, body):
            text_field(data["reviewer"], "binding reviewer", 200)
            if data["confirmed"] is not True:
                raise ValueError("Explicit named alignment confirmation is required")
            # Prepare outside this SQLite transaction; this value is rechecked below.
            plan = prepared[0]
            if data["binding_fingerprint"] != plan["binding_fingerprint"]:
                raise ValueError("Evidence preview changed; prepare and confirm again")
            branch = self.branch(body, data["branch_id"])
            branch["bindings"][data["node_id"]] = {
                "state": "ACTIVE",
                "source_ref": {"review_id": data["review_id"], "key": data["key"]},
                "node_fingerprint": plan["node_fingerprint"],
                "source_fingerprint": plan["source_fingerprint"],
                "source": plan["source"],
                "implementation_sha256": plan["implementation_sha256"],
                "dependency_fingerprints": plan["dependency_fingerprints"],
                "reviewer": data["reviewer"],
                "at": _now(),
                "binding_fingerprint": plan["binding_fingerprint"],
            }
            self.event(
                body,
                "EVIDENCE_BOUND",
                branch_id=branch["branch_id"],
                node_id=data["node_id"],
                reviewer=data["reviewer"],
                source_ref={"review_id": data["review_id"], "key": data["key"]},
                source_fingerprint=plan["source_fingerprint"],
                node_fingerprint=plan["node_fingerprint"],
                binding_fingerprint=plan["binding_fingerprint"],
            )
            return body

        fields = {
            "expected_version",
            "branch_id",
            "node_id",
            "review_id",
            "key",
            "binding_fingerprint",
            "reviewer",
            "confirmed",
        }
        exact_object(data, {"request_id", *fields}, "Research binding")
        # Idempotent replays return the current audited report, even after withdrawal.
        with self.transaction() as db:
            previous = db.execute(
                "SELECT * FROM requests WHERE id=?", (_identifier(data["request_id"]),)
            ).fetchone()
        prepared = (
            []
            if previous
            else [
                self.prepare_binding(
                    project_id,
                    {
                        key: data[key]
                        for key in ("expected_version", "branch_id", "node_id", "review_id", "key")
                    },
                )
            ]
        )
        return self.mutate(project_id, "bind", data, fields, operation)

    def withdraw(self, project_id, data):
        def operation(_, body):
            text_field(data["reviewer"], "reviewer", 200)
            text_field(data["reason"], "withdrawal reason", 500)
            if data["confirmed"] is not True:
                raise ValueError("Withdrawal confirmation is required")
            branch = self.branch(body, data["branch_id"])
            if data["node_id"] not in branch["bindings"]:
                raise ValueError("Node has no evidence binding")
            branch["bindings"][data["node_id"]]["state"] = "WITHDRAWN"
            self.event(
                body,
                "BINDING_WITHDRAWN",
                branch_id=branch["branch_id"],
                node_id=data["node_id"],
                reviewer=data["reviewer"],
                reason=data["reason"],
            )
            return body

        return self.mutate(
            project_id,
            "withdraw",
            data,
            {"expected_version", "branch_id", "node_id", "reviewer", "reason", "confirmed"},
            operation,
        )

    def history(self, project_id, branch_id):
        self.report(project_id)
        with self.transaction() as db:
            self.branch(self.read(db, project_id), branch_id)
            versions = []
            for row in db.execute(
                "SELECT * FROM revisions WHERE project_id=? AND branch_id=? ORDER BY revision DESC",
                (project_id, branch_id),
            ):
                graph = strict_json(row["body"])
                if digest(graph) != row["hash"]:
                    raise ValueError("Research revision changed")
                versions.append(
                    {"revision": row["revision"], "graph": graph, "fingerprint": row["hash"]}
                )
        return {"revisions": versions, "fresh_attestation": False}

    def list_projects(self):
        with self.transaction() as db:
            bodies = [
                self.read(db, row[0])
                for row in db.execute("SELECT id FROM projects ORDER BY rowid DESC")
            ]
        return [
            {
                "project_id": b["project_id"],
                "title": b["title"],
                "question": b["question"],
                "created_at": b["created_at"],
                "version": b["version"],
                "branch_count": len(b["branches"]),
                "state": "RESEARCHING",
            }
            for b in bodies
        ]

    def export_node(self, project_id, branch_id, node_id):
        body = self.report(project_id)
        branch = self.branch(body, branch_id)
        node = next((node for node in branch["nodes"] if node["id"] == node_id), None)
        if node is None or node["kind"] == "definition":
            raise ValueError("Only typed claims can become single-theorem annotations")
        bundle = {
            "schema": "int-paper-review-v1" if node["domain"] == "Int" else "nat-paper-review-v1",
            "document": {
                "id": "research-node-"
                + digest({"project_id": project_id, "branch_id": branch_id, "node_id": node_id})[
                    :24
                ],
                "version": branch["revision"],
                "title": node["title"],
                "text": node["statement"],
            },
            "variables": [],
            "assumptions": [],
            "conclusion": node["formula"],
            "claim_span": [1, len(node["statement"].splitlines())],
            "claim_plan": {"direction": "prove", "strategy": "omega", "witnesses": []},
            "steps": [],
        }
        prepare_review(bundle)
        return {
            "bundle": bundle,
            "project_id": project_id,
            "branch_id": branch_id,
            "node_id": node_id,
            "model_calls": 0,
            "lean_calls": 0,
            "scope": "Independent full target; definition notes and graph prerequisites do not become Lean assumptions",
        }
