"""Complete structure graph proofs reused through the unchanged data-import executor."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from pathlib import Path

from .domain import digest
from .formalizer import exact_object
from .integrity import file_hash
from .lemma_composition import bindings, manifest as import_manifest
from .library_composition import LibraryCompositionStore
from .mathlib_lab import MathlibLabStore, run_identifier
from .number_theory_terms import identifier
from .proof_library import audit_scope, proof_data, provenance
from .research_flow import atomic, identifier as graph_identifier
from .tasks import TaskStore, _identifier
from .structure_composition_terms import FIELDS, LIMITS, MODE, compile_composition
from .structure_research import StructureResearchStore, source_identity
from .structured_terms import compile_request
from .typed_terms import bounded_data

FILES = (
    "mra/structure_composition_terms.py",
    "mra/structure_reuse.py",
    "mra/library_composition.py",
    "mra/proof_library.py",
    "mra/structure_research.py",
    "mra/structured_terms.py",
    "mra/structured_proofs.py",
    "mra/structures.py",
    "mra/typed_terms.py",
)
_connections = ContextVar("mra_structure_reuse_connections", default={})
_executing = ContextVar("mra_structure_reuse_executing", default=False)


def manifest(root):
    original = import_manifest(root)
    return {
        "mode": MODE,
        "executor": original["executor"],
        "import_protocol": original,
        "files": {n: file_hash(root / n) for n in FILES},
        "limits": dict(LIMITS),
    }


def checked(row):
    if (
        row["state"] != "COMPLETED"
        or row["math_verdict"] != "PROVED"
        or row["evidence_level"] != "E4"
        or row["artifact_status"] != "MATCHES_RECORDED_HASHES"
        or row["invalidated"]
        or row["cancel_requested"]
        or row["alignment"]["confirmed"] is not True
    ):
        raise ValueError("Source requires current intact, named, completed PROVED/E4 evidence")
    result = row["result"]
    for phase in ("candidate", "replay"):
        outcome = result[phase]
        if (
            outcome["run_status"] != "COMPLETED"
            or outcome["returncode"] != 0
            or outcome["audit"]["type_matches"] is not True
        ):
            raise ValueError("Source lacks a successful complete-type candidate or replay audit")
    if result["candidate"]["pid"] == result["replay"]["pid"]:
        raise ValueError("Source requires independent candidate and replay processes")


class StructureReuseStore(LibraryCompositionStore):
    def __init__(self, root, *, graphs=None, base=None):
        self.root = Path(root).resolve()
        self.base = (
            Path(base) if base is not None else self.root / "artifacts/structure-proof-reuse"
        )
        if self.base.resolve() != self.base or not self.base.is_relative_to(
            self.root / "artifacts"
        ):
            raise ValueError("Structure reuse storage must stay inside project artifacts")
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "lab.sqlite3"
        self.tasks = TaskStore(self.root)
        self.graphs = graphs if graphs is not None else StructureResearchStore(self.root)
        self.knowledge = self  # The inherited damaged-ledger reader uses this marker interface.
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def transaction(self, write=True):
        """Share the current transaction when a final gate recursively audits ancestors.

        Reopening a write transaction on the same SQLite file would deadlock against
        the final executor commit. Never upgrade a read-only enclosing transaction.
        """
        if self.base.resolve() != self.base or self.path.is_symlink():
            raise ValueError("Structure reuse ledger cannot be redirected")
        key = str(self.path)
        active = _connections.get()
        if key in active:
            db, writable = active[key]
            if write and not writable:
                raise ValueError("Cannot upgrade a structure reuse read transaction")
            yield db
            return
        with super().transaction(write) as db:
            token = _connections.set({**active, key: (db, write)})
            try:
                yield db
            finally:
                _connections.reset(token)

    def damage_marker(self, namespace, key, reason=None):
        if namespace != "runs":
            raise ValueError("Unknown structure reuse damage namespace")
        run_identifier(key)
        directory = self.base / "invalidated-runs"
        if directory.resolve() != directory:
            raise ValueError("Structure reuse marker cannot be redirected")
        path = directory / (key + ".json")
        if reason is not None:
            directory.mkdir(exist_ok=True)
            atomic(path, {"reason": str(reason)[:1000]})
        return path.exists()

    def context(self, graph_id, node_id):
        graph_identifier(graph_id, "structureflow")
        _identifier(node_id)
        if _executing.get():
            # The executor retains the global worker lease. Retry only graph-lock
            # entry with the existing bounded helper, never Lean or a yielded write.
            with self.graphs.worker_audited(graph_id) as values:
                row = self.graphs._view(*values)
        else:
            row = self.graphs.report(graph_id)
        if row["invalidated"]:
            raise ValueError("Original structure research context is unavailable")
        plan = row["plan"]
        node = next((n for n in plan["request"]["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise ValueError("Unknown complete structure research node")
        value = {
            "graph_id": graph_id,
            "node_id": node_id,
            "graph_plan_fingerprint": plan["plan_fingerprint"],
            "parent": deepcopy(plan["parent"]),
            "depends_on": deepcopy(node["depends_on"]),
            "target": deepcopy(plan["node_plans"][node_id]["request"]),
        }
        value["context_fingerprint"] = digest(value)
        return value, row

    def load_source(self, binding):
        exact_object(binding, {"alias", "item_id"}, "Structure proof source")
        identifier(binding["alias"])
        item_id = _identifier(binding["item_id"])
        if item_id.startswith("node."):
            pieces = item_id.split(".", 2)
            if len(pieces) != 3:
                raise ValueError("Use node.<structureflow-id>.<node-id>")
            _, graph_id, node_id = pieces
            context, graph = self.context(graph_id, node_id)
            state = graph["node_states"][node_id]
            if (state["math_verdict"], state["evidence_level"]) != ("PROVED", "E4"):
                raise ValueError("Original node has no current E4 source")
            row = graph["checks"][state["best_attempt"]]
            checked(row)
            directory = self.graphs.checker(graph_id).directory(row["run_id"])
            fingerprint = digest(
                {"node": state["source_fingerprint"], "source": source_identity(row)}
            )
            depth, ancestors, origin = 0, [], "structure-graph-node"
        elif item_id.startswith("run."):
            run_id = run_identifier(item_id.removeprefix("run."))
            row = self.report(run_id)
            checked(row)
            raw = row["plan"]["request"]
            context, _ = self.context(raw["graph_id"], raw["node_id"])
            if context != row["plan"]["context"]:
                raise ValueError("Composition source context changed")
            directory = self.directory(run_id)
            fingerprint = source_identity(row)
            ancestry = row["plan"]["provenance"]
            depth, ancestors, origin = (
                ancestry["depth"],
                deepcopy(ancestry["ancestors"]),
                "structure-composition-run",
            )
        else:
            raise ValueError("Structure sources require an explicit node. or run. namespace")
        request = context["target"]
        compiled = compile_request(request)
        if compiled["formal_statement"] != row["plan"]["formal_statement"]:
            raise ValueError("Source full type differs from its original graph target")
        raw = proof_data(directory / "replay/proof.ndjson", row["files"].get("replay/proof.ndjson"))
        descriptor = {
            **binding,
            "origin": origin,
            "graph_id": context["graph_id"],
            "node_id": context["node_id"],
            "context_fingerprint": context["context_fingerprint"],
            "version": 1,
            "archive_fingerprint": fingerprint,
            "source_fingerprint": fingerprint,
            "request": deepcopy(request),
            "formal_statement": compiled["formal_statement"],
            "alignment": deepcopy(row["alignment"]),
            "data_sha256": hashlib.sha256(raw).hexdigest(),
            "data_bytes": len(raw),
            "depth": depth,
        }
        descriptor["ancestors"] = [
            *ancestors,
            {k: descriptor[k] for k in ("item_id", "version", "archive_fingerprint")},
        ]
        return descriptor, raw

    def preview(self, raw):
        bounded_data(raw)
        exact_object(raw, FIELDS, "Structure composition request")
        if raw["schema"] != MODE:
            raise ValueError("Use the explicit structure composition schema")
        raw = deepcopy(raw)
        context, _ = self.context(raw["graph_id"], raw["node_id"])
        sources = [self.load_source(b)[0] for b in bindings(raw)]
        prerequisites = context["depends_on"]
        if (
            len(sources) != len(prerequisites)
            or {s["node_id"] for s in sources} != set(prerequisites)
            or any(s["graph_id"] != context["graph_id"] for s in sources)
        ):
            raise ValueError(
                "Bind exactly one current E4 source for every direct prerequisite in the same graph"
            )
        compiled = compile_composition(raw, context["target"], sources)
        plan = {
            **compiled,
            "context": context,
            "source_items": sources,
            "provenance": provenance(sources),
            "source_manifest": manifest(self.root),
            "budget": {
                "candidate_processes": 1,
                "replay_processes": 1,
                "shared_worker_seconds": raw["timeout_seconds"],
                "integrity_time_separate": True,
                "automatic_retries": 0,
                "source_imports_inside_candidate": len(sources),
            },
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
            "limitations": [
                "Explicit Type carriers and Nat/Int/Rat proof terms; no model search or arbitrary Lean.",
                "All original carriers, operations, definitions, variables and premises remain in the complete target.",
                "Exact shared structure environment and one current source for each direct prerequisite are required.",
                "Imported proof data is kernel checked; the complete result is independently replayed.",
                "Original graph judgments remain separate; new composition results need explicit run. sources.",
                "Source damage, replacement or withdrawal persistently invalidates descendants on audit.",
                "Same pinned kernel implementation; local reviewer names are not authenticated.",
            ],
        }
        plan["plan_fingerprint"] = digest(plan)
        return plan

    def gate(self, row, check_files=True):
        if self.damage_marker("runs", row["run_id"]):
            return "Previously detected damaged structure composition ledger"
        if row["invalidated"]:
            return "Previously invalidated; confirm a new complete structure composition"
        plan = row["plan"]
        if plan["source_manifest"] != manifest(self.root):
            return "Structure compiler, source adapter, import template or runtime changed"
        context, _ = self.context(plan["request"]["graph_id"], plan["request"]["node_id"])
        if context != plan["context"] or context["target"] != plan["target"]:
            return "Original graph, full target or project context changed"
        current = [
            self.load_source({k: s[k] for k in ("alias", "item_id")})[0]
            for s in plan["source_items"]
        ]
        if current != plan["source_items"] or provenance(current) != plan["provenance"]:
            return "Direct or transitive structure proof source changed"
        view = {**row, "plan": {**plan, "source_manifest": plan["source_manifest"]["executor"]}}
        return MathlibLabStore.gate(self, view, check_files)

    def report(self, run_id):
        with audit_scope("structure-run." + str(self.path) + "." + run_identifier(run_id)):
            return {**super().report(run_id), "mode": MODE}

    def execute(self, run_id, lock_fd):
        token = _executing.set(True)
        try:
            return super().execute(run_id, lock_fd)
        finally:
            _executing.reset(token)

    def list_runs(self):
        with self.transaction(False) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        return [
            {
                k: r[k]
                for k in (
                    "run_id",
                    "state",
                    "math_verdict",
                    "evidence_level",
                    "invalidated",
                    "created_at",
                )
            }
            | {
                "graph_id": r["plan"]["request"]["graph_id"],
                "node_id": r["plan"]["request"]["node_id"],
                "title": r["plan"]["target"]["title"],
            }
            for r in (self.report(key) for key in ids)
        ]

    def catalog(self, graph_id, node_id):
        context, graph = self.context(graph_id, node_id)
        with self.transaction(False) as db:
            rows = [
                self.decode(r)
                for r in db.execute("SELECT body,sha256 FROM runs ORDER BY rowid DESC LIMIT 200")
            ]
        candidates = [
            f"node.{graph_id}.{key}"
            for key in context["depends_on"]
            if graph["node_states"][key]["math_verdict"] == "PROVED"
        ] + [
            "run." + r["run_id"]
            for r in rows
            if r["plan"]["request"]["graph_id"] == graph_id
            and r["plan"]["request"]["node_id"] in context["depends_on"]
        ]
        return {
            "mode": MODE,
            "context": context,
            "candidate_ids": candidates,
            "candidates_require_fresh_preview": True,
            "maximum_sources": 4,
            "maximum_runs": 200,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
