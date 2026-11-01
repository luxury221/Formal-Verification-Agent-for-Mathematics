"""Versioned Mathlib research nodes; historical proof references, never inference."""

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import canonical, digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .mathlib_lab import MathlibLabStore, audit, now
from .mathlib_terms import compile_request, text
from .research import ResearchStore
from .tasks import _identifier

SCHEMA = "research-mathlib-v1"
CONTEXT_FIELDS = {"branch_id", "expected_revision", "parent_context_fingerprint"}
ATTACH_FIELDS = CONTEXT_FIELDS | {"node_id", "kind", "depends_on", "notes", "run_id"}
LIMITS = {"nodes": 16, "dependencies": 8, "revisions": 64, "events": 1024, "scopes": 800}


def validate_nodes(nodes):
    if not isinstance(nodes, list) or len(nodes) > LIMITS["nodes"]:
        raise ValueError("At most 16 Mathlib nodes per research branch")
    if len(canonical(nodes)) > 160000:
        raise ValueError("Mathlib graph exceeds 160000 bytes")
    by_id = {}
    for node in nodes:
        exact_object(node, {"id", "kind", "request", "depends_on", "notes"}, "Mathlib node")
        key = _identifier(node["id"])
        if len(key) > 32 or key in by_id:
            raise ValueError("Node IDs must be unique and at most 32 characters")
        if node["kind"] not in ("problem", "subproblem", "lemma"):
            raise ValueError("Unknown Mathlib node kind")
        if not isinstance(node["notes"], str) or len(node["notes"]) > 4000:
            raise ValueError("Node notes exceed 4000 characters")
        compiled = compile_request(node["request"])
        if node["request"] != compiled["request"]:
            raise ValueError("Use the normalized complete Mathlib request from preview")
        deps = node["depends_on"]
        if not isinstance(deps, list) or len(deps) > LIMITS["dependencies"]:
            raise ValueError("At most 8 explicit prerequisites")
        for dependency in deps:
            _identifier(dependency)
        if len(set(deps)) != len(deps):
            raise ValueError("Duplicate prerequisite")
        by_id[key] = node
    order, visiting, seen = [], set(), set()

    def visit(key):
        if key not in by_id:
            raise ValueError("Prerequisites must name Mathlib nodes in this branch")
        if key in visiting:
            raise ValueError("Mathlib dependencies must be acyclic")
        if key in seen:
            return
        visiting.add(key)
        for dependency in by_id[key]["depends_on"]:
            visit(dependency)
        visiting.remove(key)
        seen.add(key)
        order.append(key)

    for key in by_id:
        visit(key)
    return order


def normalized_nodes(nodes):
    validate_nodes(nodes)
    result = copy.deepcopy(nodes)
    for node in result:
        node["request"] = compile_request(node["request"])["request"]
    return result


class ResearchMathlibStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/research-mathlib"
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "research-mathlib.sqlite3"
        self.research = ResearchStore(self.root)
        self.mathlib = MathlibLabStore(self.root)
        self.cursor = 0
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scopes (id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, scope_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS revisions (scope_id TEXT, revision INTEGER, body TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(scope_id,revision))"
            )

    @contextmanager
    def transaction(self, write=True):
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Mathlib research storage must not be redirected")
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

    def parent_body(self, project_id):
        path = self.research.path
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError("Parent research ledger redirected")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            return ResearchStore.read(db, project_id)
        finally:
            db.close()

    def branches(self, project_id):
        body = self.parent_body(project_id)
        return {
            "project_id": project_id,
            "branches": [
                {"branch_id": key, "label": branch["label"]}
                for key, branch in body["branches"].items()
            ],
        }

    def parent(self, project_id, branch_id):
        body = self.parent_body(project_id)
        branch = ResearchStore.branch(body, branch_id)
        context = {
            "project_id": project_id,
            "question": body["question"],
            "branch_id": branch_id,
            "revision": branch["revision"],
            "nodes": branch["nodes"],
        }
        return {
            "project_id": project_id,
            "title": body["title"],
            "branch_id": branch_id,
            "branch_label": branch["label"],
            "question": body["question"],
            "parent_context_fingerprint": digest(context),
        }

    def adapter(self):
        return {"schema": SCHEMA, "sha256": file_hash(self.root / "mra/research_mathlib.py")}

    @staticmethod
    def scope_id(project_id, branch_id):
        return _identifier(project_id) + ":" + _identifier(branch_id)

    @staticmethod
    def decode(row):
        value = strict_json(row[0])
        if digest(value) != row[1]:
            raise ValueError("Mathlib research ledger digest mismatch")
        return value

    def read(self, db, project_id, branch_id):
        key = self.scope_id(project_id, branch_id)
        row = db.execute("SELECT body, hash FROM scopes WHERE id=?", (key,)).fetchone()
        if row:
            value = self.decode(row)
            if (value["project_id"], value["branch_id"]) != (project_id, branch_id):
                raise ValueError("Mathlib research scope identity mismatch")
            return value
        return {
            "schema": SCHEMA,
            "project_id": project_id,
            "branch_id": branch_id,
            "revision": 0,
            "graph_revision": 0,
            "nodes": [],
            "bindings": {},
            "events": [],
        }

    def save(self, db, row):
        if len(canonical(row)) > 2097152:
            raise ValueError("Mathlib research scope exceeds 2 MiB; export before continuing")
        db.execute(
            "INSERT OR REPLACE INTO scopes VALUES (?,?,?)",
            (
                self.scope_id(row["project_id"], row["branch_id"]),
                canonical(row).decode(),
                digest(row),
            ),
        )

    @staticmethod
    def event(row, kind, **details):
        if len(row["events"]) >= LIMITS["events"]:
            raise ValueError("Mathlib research event budget exhausted")
        row["events"].append(
            {"sequence": len(row["events"]) + 1, "at": now(), "kind": kind, **details}
        )

    def snapshot(self, db, row):
        if row["graph_revision"] >= LIMITS["revisions"]:
            raise ValueError("Mathlib graph revision budget exhausted")
        row["graph_revision"] += 1
        value = {"nodes": copy.deepcopy(row["nodes"]), "at": now()}
        db.execute(
            "INSERT INTO revisions VALUES (?,?,?,?)",
            (
                self.scope_id(row["project_id"], row["branch_id"]),
                row["graph_revision"],
                canonical(value).decode(),
                digest(value),
            ),
        )

    def source(self, run_id):
        row = self.mathlib.report(run_id)
        if (
            row["state"],
            row["math_verdict"],
            row["evidence_level"],
            row["invalidated"],
            row["artifact_status"],
        ) != ("COMPLETED", "PROVED", "E4", False, "MATCHES_RECORDED_HASHES"):
            raise ValueError("Source must have available real COMPLETED/PROVED/E4 evidence")
        plan, alignment = row["plan"], row["alignment"]
        if (
            alignment.get("confirmed") is not True
            or alignment.get("plan_fingerprint") != plan["plan_fingerprint"]
        ):
            raise ValueError("Source semantic alignment unavailable")
        text(alignment.get("reviewer"), 200, "Source reviewer")
        compiled = compile_request(plan["request"])
        if any(compiled[key] != plan[key] for key in compiled):
            raise ValueError("Source complete statement or proof differs from compiled request")
        candidate, replay = (row["result"][key] for key in ("candidate", "replay"))
        for result, marker, is_replay in (
            (candidate, "MRA_AUDIT:", False),
            (replay, "MRA_REPLAY:", True),
        ):
            if result["run_status"] != "COMPLETED" or result["returncode"] != 0:
                raise ValueError("Source kernel check did not complete")
            audit(marker + json.dumps(result["audit"]), marker, replay=is_replay)
        if (
            type(candidate["pid"]) is not int
            or type(replay["pid"]) is not int
            or candidate["pid"] == replay["pid"]
        ):
            raise ValueError("Independent checker processes required")
        value = {
            "run_id": run_id,
            "plan": plan,
            "alignment": alignment,
            "files": row["files"],
            "candidate": candidate,
            "replay": replay,
        }
        return {
            "run_id": run_id,
            "source_fingerprint": digest(value),
            "request": plan["request"],
            "formal_statement": plan["formal_statement"],
            "proof_source": plan["proof_source"],
            "source_alignment": alignment,
            "runtime_snapshot": plan["source_manifest"]["runtime_snapshot"],
            "math_verdict": "PROVED",
            "evidence_level": "E4",
        }

    def audited(self, row, parent):
        result = copy.deepcopy(row)
        by_id = {node["id"]: node for node in row["nodes"]}
        sources = {}
        for key in validate_nodes(row["nodes"]):
            binding = result["bindings"].get(key)
            if not binding or binding["state"] != "ACTIVE":
                continue
            node, reason = by_id[key], None
            if binding["adapter"] != self.adapter():
                reason = "Adapter implementation changed"
            elif binding["parent_context_fingerprint"] != parent["parent_context_fingerprint"]:
                reason = "Parent research context changed"
            elif binding["node_fingerprint"] != digest(node):
                reason = "Complete Mathlib node changed"
            else:
                for dep in node["depends_on"]:
                    current = result["bindings"].get(dep)
                    if (
                        not current
                        or current["state"] != "ACTIVE"
                        or binding["prerequisites"].get(dep) != current["binding_fingerprint"]
                    ):
                        reason = "Prerequisite binding changed or unavailable: " + dep
                        break
            if reason is None:
                try:
                    run_id = binding["source"]["run_id"]
                    if run_id not in sources:
                        sources[run_id] = self.source(run_id)
                    if (
                        sources[run_id]["source_fingerprint"]
                        != binding["source"]["source_fingerprint"]
                    ):
                        reason = "Source evidence changed"
                except sqlite3.Error as error:
                    if storage_busy(error):
                        raise
                    reason = "Source storage unavailable: " + str(error)
                except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                    reason = "Source unavailable: " + str(error)
            if reason:
                binding.update(state="INVALIDATED", invalidated_at=now(), reason=reason)
                self.event(result, "BINDING_INVALIDATED", node_id=key, reason=reason)
        # Removed nodes retain their revoked history, but can never authorize dependents.
        for key, binding in result["bindings"].items():
            if key not in by_id and binding["state"] == "ACTIVE":
                binding.update(state="INVALIDATED", invalidated_at=now(), reason="Node removed")
                self.event(result, "BINDING_INVALIDATED", node_id=key, reason="Node removed")
        return result

    def report(self, project_id, branch_id):
        for _ in range(4):
            parent = self.parent(project_id, branch_id)
            with self.transaction(False) as db:
                row = self.read(db, project_id, branch_id)
            checked = self.audited(row, parent)
            if self.parent(project_id, branch_id) != parent:
                continue
            if checked != row:
                with self.transaction() as db:
                    if self.read(db, project_id, branch_id) != row:
                        continue
                    checked["revision"] += 1
                    self.save(db, checked)
            else:
                with self.transaction(False) as db:
                    if self.read(db, project_id, branch_id) != row:
                        continue
            return self.view(checked, parent)
        raise RuntimeError("Research changed concurrently; reload the Mathlib branch")

    @staticmethod
    def view(row, parent):
        result = copy.deepcopy(row)
        result["parent"] = parent
        result["parent_context_fingerprint"] = parent["parent_context_fingerprint"]
        result["limits"] = LIMITS
        result["model_calls"] = 0
        result["fresh_attestation"] = False
        for node in result["nodes"]:
            binding = result["bindings"].get(node["id"])
            node["math_verdict"] = (
                "PROVED" if binding and binding["state"] == "ACTIVE" else "UNKNOWN"
            )
            node["evidence_level"] = "E4" if node["math_verdict"] == "PROVED" else "E0"
            node["formal_statement"] = compile_request(node["request"])["formal_statement"]
            node["blocked_by"] = [
                key
                for key in node["depends_on"]
                if result["bindings"].get(key, {}).get("state") != "ACTIVE"
            ]
        return result

    @staticmethod
    def check_context(report, data):
        if (
            type(data["expected_revision"]) is not int
            or data["expected_revision"] != report["revision"]
        ):
            raise ValueError("Mathlib branch revision changed; reload before confirming")
        if data["parent_context_fingerprint"] != report["parent_context_fingerprint"]:
            raise ValueError("Parent research context changed; reload before confirming")

    def preview_attachment(self, project_id, data):
        exact_object(data, ATTACH_FIELDS, "Mathlib attachment preview")
        report = self.report(project_id, data["branch_id"])
        self.check_context(report, data)
        source = self.source(data["run_id"])
        node = {
            "id": data["node_id"],
            "kind": data["kind"],
            "request": source["request"],
            "depends_on": data["depends_on"],
            "notes": data["notes"],
        }
        nodes = [
            {key: item[key] for key in ("id", "kind", "request", "depends_on", "notes")}
            for item in report["nodes"]
        ]
        existing = next((item for item in nodes if item["id"] == node["id"]), None)
        if existing is not None and existing != node:
            raise ValueError(
                "Existing node differs from the complete source request or metadata; edit explicitly"
            )
        validate_nodes(nodes if existing is not None else [*nodes, node])
        prerequisites = {}
        for key in node["depends_on"]:
            binding = report["bindings"].get(key)
            if not binding or binding["state"] != "ACTIVE":
                raise ValueError("Unproved Mathlib prerequisite: " + key)
            prerequisites[key] = binding["binding_fingerprint"]
        result = {
            **data,
            "project_id": project_id,
            "node": node,
            "source": source,
            "parent": report["parent"],
            "prerequisites": prerequisites,
            "adapter": self.adapter(),
            "new_node": existing is None,
            "new_lean_calls": 0,
        }
        result["attachment_fingerprint"] = digest(result)
        return result

    def mutate(self, project_id, action, data, fields, operation):
        exact_object(data, CONTEXT_FIELDS | fields | {"request_id"}, "Mathlib research mutation")
        request_id = _identifier(data["request_id"])
        fingerprint = digest({"project_id": project_id, "action": action, "data": data})
        scope_id = self.scope_id(project_id, data["branch_id"])
        with self.transaction(False) as db:
            old = db.execute(
                "SELECT fingerprint,scope_id FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        if old:
            if old != (fingerprint, scope_id):
                raise ValueError("Request ID belongs to a different Mathlib research operation")
            return self.report(project_id, data["branch_id"])
        report = self.report(project_id, data["branch_id"])
        self.check_context(report, data)
        prepared = operation(None, report)
        # Cross-ledger writes are intentionally not atomic. Read-time audit remains authoritative.
        if self.parent(project_id, data["branch_id"]) != report["parent"]:
            raise ValueError("Parent research changed while confirming")
        with self.transaction() as db:
            old = db.execute(
                "SELECT fingerprint,scope_id FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if old:
                if old != (fingerprint, scope_id):
                    raise ValueError("Request ID belongs to a different Mathlib research operation")
            else:
                row = self.read(db, project_id, data["branch_id"])
                if row["revision"] != report["revision"]:
                    raise ValueError("Mathlib branch changed while confirming")
                if (
                    row["revision"] == 0
                    and db.execute("SELECT count(*) FROM scopes").fetchone()[0] >= LIMITS["scopes"]
                ):
                    raise ValueError("Mathlib research scope budget exhausted")
                if (
                    db.execute(
                        "SELECT count(*) FROM requests WHERE scope_id=?", (scope_id,)
                    ).fetchone()[0]
                    >= 512
                ):
                    raise ValueError("Mathlib research request budget exhausted")
                prepared(db, row)
                row["revision"] += 1
                self.save(db, row)
                db.execute(
                    "INSERT INTO requests VALUES (?,?,?)", (request_id, fingerprint, scope_id)
                )
        return self.report(project_id, data["branch_id"])

    def edit(self, project_id, data):
        def operation(_, report):
            nodes = normalized_nodes(data["nodes"])

            def commit(db, row):
                row["nodes"] = copy.deepcopy(nodes)
                checked = self.audited(row, report["parent"])
                row.update(checked)
                self.snapshot(db, row)
                self.event(row, "GRAPH_SAVED", graph_revision=row["graph_revision"])

            return commit

        return self.mutate(project_id, "edit", data, {"nodes"}, operation)

    def attach(self, project_id, data):
        def operation(_, report):
            if data["confirmed"] is not True:
                raise ValueError("Explicit confirmation of complete Mathlib attachment is required")
            reviewer = text(data["reviewer"], 200, "Reviewer")
            preview = self.preview_attachment(project_id, {key: data[key] for key in ATTACH_FIELDS})
            if data["attachment_fingerprint"] != preview["attachment_fingerprint"]:
                raise ValueError("Attachment preview changed; review it again")

            def commit(db, row):
                key = data["node_id"]
                row["nodes"] = normalized_nodes(row["nodes"])
                if preview["new_node"]:
                    row["nodes"].append(copy.deepcopy(preview["node"]))
                    self.snapshot(db, row)
                previous = row["bindings"].get(key)
                binding = {
                    "state": "ACTIVE",
                    "id": "mathlib-binding-" + uuid.uuid4().hex,
                    "node_fingerprint": digest(preview["node"]),
                    "source": preview["source"],
                    "prerequisites": preview["prerequisites"],
                    "adapter": preview["adapter"],
                    "parent_context_fingerprint": preview["parent_context_fingerprint"],
                    "reviewer": reviewer,
                    "confirmed": True,
                    "at": now(),
                    "attachment_fingerprint": preview["attachment_fingerprint"],
                }
                binding["binding_fingerprint"] = digest(binding)
                row["bindings"][key] = binding
                self.event(
                    row,
                    "SOURCE_ATTACHED",
                    node_id=key,
                    binding=copy.deepcopy(binding),
                    replaces=previous["id"] if previous else None,
                )
                row.update(self.audited(row, report["parent"]))

            return commit

        return self.mutate(
            project_id,
            "attach",
            data,
            (ATTACH_FIELDS - CONTEXT_FIELDS) | {"reviewer", "confirmed", "attachment_fingerprint"},
            operation,
        )

    def withdraw(self, project_id, data):
        def operation(_, report):
            reviewer = text(data["reviewer"], 200, "Reviewer")
            reason = text(data["reason"], 500, "Reason")
            if data["confirmed"] is not True:
                raise ValueError("Withdrawal requires explicit confirmation")
            key = _identifier(data["node_id"])
            if report["bindings"].get(key, {}).get("state") != "ACTIVE":
                raise ValueError("No active Mathlib binding to withdraw")

            def commit(db, row):
                row["bindings"][key].update(state="WITHDRAWN", reason=reason, withdrawn_at=now())
                self.event(row, "BINDING_WITHDRAWN", node_id=key, reviewer=reviewer, reason=reason)
                row.update(self.audited(row, report["parent"]))

            return commit

        return self.mutate(
            project_id, "withdraw", data, {"node_id", "reviewer", "reason", "confirmed"}, operation
        )

    def history(self, project_id, branch_id):
        report = self.report(project_id, branch_id)
        with self.transaction(False) as db:
            versions = [
                {"graph_revision": row[0], **self.decode(row[1:])}
                for row in db.execute(
                    "SELECT revision,body,hash FROM revisions WHERE scope_id=? ORDER BY revision",
                    (self.scope_id(project_id, branch_id),),
                ).fetchall()
            ]
        return {
            "schema": SCHEMA,
            "project_id": project_id,
            "branch_id": branch_id,
            "revision": report["revision"],
            "versions": versions,
            "events": report["events"],
        }

    def export(self, project_id, branch_id):
        return {
            "report": self.report(project_id, branch_id),
            "history": self.history(project_id, branch_id),
            "historical_evidence": True,
            "fresh_attestation": False,
            "new_lean_calls": 0,
        }

    def reconcile(self, run_id=None):
        with self.transaction(False) as db:
            rows = [
                self.decode(row) for row in db.execute("SELECT body,hash FROM scopes ORDER BY id")
            ]
        if run_id:
            rows = [
                row
                for row in rows
                if any(
                    b["state"] == "ACTIVE" and b["source"]["run_id"] == run_id
                    for b in row["bindings"].values()
                )
            ]
        elif rows:
            self.cursor %= len(rows)
            rows = rows[self.cursor : self.cursor + 1]
            self.cursor += 1
        return [self.report(row["project_id"], row["branch_id"])["revision"] for row in rows]
