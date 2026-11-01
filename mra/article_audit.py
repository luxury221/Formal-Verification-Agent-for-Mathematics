"""Version-bound full-article obligations; proof evidence remains verifier-owned."""

from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import uuid

from .article_source import index_html, source_url
from .domain import digest
from .formalizer import exact_object
from .model_service import text_field
from .research_flow import atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import WorkerBusy, _identifier, _now

SCHEMA = "article-reasoning-audit-v1"
CLASSES = (
    "claim",
    "author_step",
    "definition",
    "external_fact",
    "empirical",
    "context",
    "needs_clarification",
)
BACKENDS = ("calculus", "pde", "typed", "proof")
MATH_CLASSES = ("claim", "author_step", "external_fact")


def backend_report(root, backend, graph_id):
    # Existing providers perform their own target, artifact and runtime gates.
    from .research_studio import backend_store

    return backend_store(root, backend).report(graph_id)


def closure(annotations, block_id):
    visiting, seen, order = set(), set(), []
    pending = [(block_id, False)]
    while pending:
        key, exiting = pending.pop()
        if exiting:
            visiting.remove(key)
            seen.add(key)
            order.append(key)
            continue
        if key in seen:
            continue
        if key in visiting:
            raise ValueError("Article dependencies contain a cycle")
        if key not in annotations:
            raise ValueError("Classify each prerequisite before citing it")
        visiting.add(key)
        pending.append((key, True))
        pending.extend(
            (dependency, False) for dependency in reversed(annotations[key]["depends_on"])
        )
    return order


def annotation_identity(row, block_id):
    return digest({key: row["annotations"][key] for key in closure(row["annotations"], block_id)})


def evidence_identity(report, node_id):
    if report["invalidated"]:
        raise ValueError("Verifier graph was invalidated")
    state = report["node_states"].get(node_id)
    if not state or (state["math_verdict"], state["evidence_level"]) != ("PROVED", "E4"):
        raise ValueError("The exact node needs current PROVED/E4 evidence")
    child = report["checks"][state["best_attempt"]]
    if child["invalidated"] or child["artifact_status"] != "MATCHES_RECORDED_HASHES":
        raise ValueError("Verifier artifacts are unavailable or changed")
    if (child["state"], child["math_verdict"], child["evidence_level"]) != (
        "COMPLETED",
        "PROVED",
        "E4",
    ):
        raise ValueError("Child check did not complete with PROVED/E4")
    plan = report["plan"]
    node_plans = plan.get("node_plans") or plan["graph_compilation"]["node_plans"]
    formal_statement = node_plans[node_id]["formal_statement"]
    original = next(
        (node["target"]["original"] for node in plan["request"]["nodes"] if node["id"] == node_id),
        None,
    )
    if not isinstance(original, str) or not original:
        raise ValueError("Verifier graph has no original statement for this node")
    if child["plan"]["formal_statement"] != formal_statement:
        raise ValueError("Checked child differs from the full graph node target")
    return {
        "plan_fingerprint": report["plan"]["plan_fingerprint"],
        "node_id": node_id,
        "attempt_id": state["best_attempt"],
        "source_fingerprint": state["source_fingerprint"],
        "formal_statement": formal_statement,
        "original": original,
        "artifact_hashes": child["files"],
    }


class ArticleAuditStore:
    def __init__(self, root, *, reporter=None):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/article-audits"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Article storage cannot be redirected")
        self.reporter = reporter or (lambda backend, key: backend_report(self.root, backend, key))

    def directory(self, key):
        directory = self.base / identifier(key, "article")
        if self.base.resolve() != self.base or directory.resolve() != directory:
            raise ValueError("Article storage cannot be redirected")
        return directory

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Article storage cannot be redirected")
        paths = sorted(self.base.glob("article-*"))
        if len(paths) > 50:
            raise ValueError("Export article records before exceeding 50 articles")
        return paths

    @staticmethod
    def envelope(path):
        if path.is_symlink() or path.stat().st_size > 2097152:
            raise ValueError("Article file is redirected or exceeds storage budget")
        return _envelope(path)

    def read(self, key):
        directory = self.directory(key)
        row = self.envelope(directory / "record.json")
        source = self.envelope(directory / "source.json")
        if (
            row["schema"] != SCHEMA
            or row["article_id"] != key
            or digest(source) != row["source_fingerprint"]
            or hashlib.sha256(source["html"].encode("utf-8")).hexdigest() != source["sha256"]
        ):
            raise ValueError("Article source identity changed; import a new version")
        inventory = index_html(source["html"])
        if digest(inventory) != row["inventory_fingerprint"]:
            raise ValueError("Article source inventory or indexer changed")
        ids = {block["id"] for block in inventory["blocks"]}
        for key_, annotation in row["annotations"].items():
            self.validate_annotation(annotation, ids)
            if key_ not in ids:
                raise ValueError("Annotation has no original source block")
            closure(row["annotations"], key_)
        workspaces = row.get("workspaces", [])
        if not isinstance(workspaces, list) or len(workspaces) > 16:
            raise ValueError("Invalid article research-task references")
        seen = set()
        for workspace in workspaces:
            exact_object(
                workspace,
                {
                    "workspace_id",
                    "block_id",
                    "study_id",
                    "snapshot_identity",
                    "study_source_fingerprint",
                    "created_at",
                },
                "Article research reference",
            )
            identifier(workspace["workspace_id"], "articlework")
            identifier(workspace["study_id"], "study")
            if workspace["block_id"] not in ids or workspace["workspace_id"] in seen:
                raise ValueError(
                    "Research reference has an unknown source block or duplicate identity"
                )
            seen.add(workspace["workspace_id"])
            for field in ("snapshot_identity", "study_source_fingerprint", "created_at"):
                text_field(workspace[field], "Research reference " + field, 200)
        return row, source, inventory

    @staticmethod
    def validate_annotation(value, ids):
        if (
            value["classification"] not in CLASSES
            or type(value["version"]) is not int
            or value["version"] < 1
        ):
            raise ValueError("Unknown annotation class or version")
        text_field(value["statement"], "Exact obligation statement", 8000)
        text_field(value["note"], "Source interpretation and scope", 4000)
        if not isinstance(value["assumptions"], list) or len(value["assumptions"]) > 20:
            raise ValueError("Use at most 20 explicit assumption descriptions")
        for item in value["assumptions"]:
            text_field(item, "Assumption", 2000)
        dependencies = value["depends_on"]
        if (
            not isinstance(dependencies, list)
            or len(dependencies) > 32
            or any(not isinstance(key, str) or key not in ids for key in dependencies)
            or len(dependencies) != len(set(dependencies))
        ):
            raise ValueError("Use distinct existing source blocks as prerequisites")

    def create(self, data):
        exact_object(data, {"request_id", "title", "url", "html"}, "Import article HTML")
        _identifier(data["request_id"])
        text_field(data["title"], "Article title", 200)
        source_url(data["url"])
        inventory = index_html(data["html"])
        source = {
            "title": data["title"],
            "url": data["url"],
            "html": data["html"],
            "sha256": hashlib.sha256(data["html"].encode("utf-8")).hexdigest(),
        }
        fingerprint = digest(data)
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                row, _, _ = self.read(path.name)
                if row["request_id"] == data["request_id"]:
                    if row["request_fingerprint"] != fingerprint:
                        raise ValueError("Import request ID belongs to different source content")
                    return self.report(row["article_id"])
            if len(paths) >= 50:
                raise ValueError("Article storage budget exhausted")
            key = "article-" + uuid.uuid4().hex
            directory = self.directory(key)
            staging = self.base / (".creating-" + key)
            staging.mkdir()
            row = {
                "schema": SCHEMA,
                "article_id": key,
                "request_id": data["request_id"],
                "request_fingerprint": fingerprint,
                "source_fingerprint": digest(source),
                "inventory_fingerprint": digest(inventory),
                "created_at": _now(),
                "revision": 0,
                "annotations": {},
                "links": [],
                "requests": [],
                "events": [],
            }
            atomic(staging / "source.json", source)
            atomic(staging / "record.json", row)
            staging.rename(directory)
        return self.report(key)

    def listing(self):
        return [
            {
                "article_id": row["article_id"],
                "title": source["title"],
                "created_at": row["created_at"],
                "source_sha256": source["sha256"],
                "blocks": len(inventory["blocks"]),
                "annotated": len(row["annotations"]),
            }
            for path in self.paths()
            for row, source, inventory in [self.read(path.name)]
        ]

    def change(self, key, data, action):
        _identifier(data["request_id"])
        fingerprint = digest(data)
        with lease(self.directory(key)):
            row, _, inventory = self.read(key)
            old = next(
                (item for item in row["requests"] if item["request_id"] == data["request_id"]), None
            )
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Mutation request ID belongs to different content")
                return self.report(key)
            if (
                type(data["expected_revision"]) is not int
                or data["expected_revision"] != row["revision"]
            ):
                raise ValueError("Article changed; reload before editing obligations")
            if len(row["requests"]) >= 600:
                raise ValueError("Export article history before making further edits")
            before = digest(
                {name: row[name] for name in ("annotations", "links", "workspaces") if name in row}
            )
            action(row, inventory)
            row["revision"] += 1
            row["requests"].append({"request_id": data["request_id"], "fingerprint": fingerprint})
            row["events"].append(
                {
                    "at": _now(),
                    "revision": row["revision"],
                    "before": before,
                    "after": digest(
                        {
                            name: row[name]
                            for name in ("annotations", "links", "workspaces")
                            if name in row
                        }
                    ),
                    "request_id": data["request_id"],
                }
            )
            # Immutable per-revision payloads retain old annotations/bindings.
            history = self.directory(key) / "history"
            history.mkdir(exist_ok=True)
            history_path = history / (str(row["revision"]) + "-" + fingerprint + ".json")
            if history_path.exists():
                if self.envelope(history_path) != row:
                    raise ValueError("A retained history revision already has different content")
            else:
                atomic(history_path, row)
            atomic(self.directory(key) / "record.json", row)
        return self.report(key)

    def annotate(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "block_id",
                "classification",
                "statement",
                "assumptions",
                "depends_on",
                "note",
            },
            "Annotate source obligation",
        )

        return self._annotate_entries(
            key,
            data,
            [
                {
                    name: value
                    for name, value in data.items()
                    if name not in ("request_id", "expected_revision")
                }
            ],
        )

    def annotate_many(self, key, data):
        exact_object(
            data, {"request_id", "expected_revision", "annotations"}, "Review source blocks"
        )
        return self._annotate_entries(key, data, data["annotations"])

    def _annotate_entries(self, key, data, entries):
        if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
            raise ValueError("Review 1–32 source blocks in one atomic revision")
        for entry in entries:
            exact_object(
                entry,
                {"block_id", "classification", "statement", "assumptions", "depends_on", "note"},
                "Source annotation",
            )
        keys = [entry["block_id"] for entry in entries]
        if any(not isinstance(item, str) for item in keys) or len(set(keys)) != len(keys):
            raise ValueError("Review each source block at most once per batch")

        def action(row, inventory):
            ids = {block["id"] for block in inventory["blocks"]}
            for entry in entries:
                if entry["block_id"] not in ids:
                    raise ValueError("Choose an indexed source block")
                annotation = {
                    name: deepcopy(entry[name])
                    for name in ("classification", "statement", "assumptions", "depends_on", "note")
                }
                annotation["version"] = (
                    row["annotations"].get(entry["block_id"], {}).get("version", 0) + 1
                )
                self.validate_annotation(annotation, ids)
                if annotation["classification"] not in MATH_CLASSES and annotation["depends_on"]:
                    raise ValueError("Only mathematical obligations may have proof prerequisites")
                row["annotations"][entry["block_id"]] = annotation
            for block_id, item in row["annotations"].items():
                closure(row["annotations"], block_id)
                if any(
                    row["annotations"][dependency]["classification"] not in MATH_CLASSES
                    for dependency in item["depends_on"]
                ):
                    raise ValueError("A proof prerequisite must be a mathematical obligation")

        return self.change(key, data, action)

    def bind(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "block_id",
                "backend",
                "graph_id",
                "node_id",
                "formal_statement",
                "relation",
                "scope",
                "reviewer",
                "confirmed",
            },
            "Bind exact verifier evidence",
        )
        if data["backend"] not in BACKENDS or data["relation"] not in (
            "equivalent",
            "counterexample_specialization",
        ):
            raise ValueError("Choose a supported backend and explicit source relation")
        if data["confirmed"] is not True:
            raise ValueError(
                "Source, full target and relation require explicit semantic confirmation"
            )
        text_field(data["reviewer"], "Local semantic reviewer", 200)
        text_field(data["scope"], "Exact source correspondence and limitations", 4000)

        def action(row, inventory):
            annotation = row["annotations"].get(data["block_id"])
            if not annotation or annotation["classification"] not in MATH_CLASSES:
                raise ValueError("Classify a mathematical obligation before binding evidence")
            if len(row["links"]) >= 64:
                raise ValueError("Article evidence-reference budget exhausted")
            identity = evidence_identity(
                self.reporter(data["backend"], data["graph_id"]), data["node_id"]
            )
            if identity["formal_statement"] != data["formal_statement"]:
                raise ValueError("Exact full formal target differs from the displayed target")
            if data["relation"] == "equivalent" and identity["original"] != annotation["statement"]:
                raise ValueError("Verifier original must equal this exact obligation statement")
            row["links"].append(
                {
                    "link_id": "articlelink-" + uuid.uuid4().hex,
                    **{
                        name: deepcopy(data[name])
                        for name in (
                            "block_id",
                            "backend",
                            "graph_id",
                            "node_id",
                            "relation",
                            "scope",
                            "reviewer",
                        )
                    },
                    "source_fingerprint": row["source_fingerprint"],
                    "annotation_identity": annotation_identity(row, data["block_id"]),
                    "identity": identity,
                    "confirmed_at": _now(),
                    "authentication": "local-assertion-only",
                }
            )

        return self.change(key, data, action)

    def report(self, key, *, recheck=False):
        row, source, inventory = self.read(key)
        observations, cache = {}, {}
        for link in row["links"]:
            status, reason = (
                "NOT_RECHECKED",
                "Historical binding; explicitly recheck current evidence",
            )
            if link["source_fingerprint"] != row["source_fingerprint"] or link[
                "annotation_identity"
            ] != annotation_identity(row, link["block_id"]):
                status, reason = (
                    "STALE",
                    "Source interpretation or a prerequisite changed; rebind after review",
                )
            elif recheck:
                try:
                    identity = (link["backend"], link["graph_id"])
                    if identity not in cache:
                        cache[identity] = self.reporter(*identity)
                    current = evidence_identity(cache[identity], link["node_id"])
                    if current != link["identity"]:
                        raise ValueError(
                            "Exact attempt, target or proof evidence changed; rebind after review"
                        )
                    status, reason = (
                        "CURRENT",
                        "Existing exact PROVED/E4 evidence rechecked; no Lean execution",
                    )
                except WorkerBusy:
                    status, reason = (
                        "BUSY",
                        "Verifier evidence is being updated; no verdict granted",
                    )
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    status, reason = "UNAVAILABLE", str(error)[:2000]
            observations[link["link_id"]] = {"status": status, "reason": reason}
        by_block = {}
        for block in inventory["blocks"]:
            annotation = row["annotations"].get(block["id"])
            links = [
                dict(link, observation=observations[link["link_id"]])
                for link in row["links"]
                if link["block_id"] == block["id"]
            ]
            equivalent = any(
                link["relation"] == "equivalent" and link["observation"]["status"] == "CURRENT"
                for link in links
            )
            finding = any(
                link["relation"] == "counterexample_specialization"
                and link["observation"]["status"] == "CURRENT"
                for link in links
            )
            by_block[block["id"]] = {
                **block,
                "annotation": annotation,
                "links": links,
                "claim_verdict": "PROVED" if equivalent else "UNKNOWN",
                "evidence_level": "E4" if equivalent else "E0",
                "finding": "CHECKED_COUNTEREXAMPLE_SPECIALIZATION" if finding else None,
                "author_proof_verdict": "UNKNOWN",
                "step_status": "UNVERIFIED",
                "blocked_by": [],
            }

        for block_id, item in row["annotations"].items():
            for current in closure(row["annotations"], block_id):
                block = by_block[current]
                annotation = row["annotations"][current]
                blocked = [
                    dependency
                    for dependency in annotation["depends_on"]
                    if by_block[dependency]["step_status"] != "VERIFIED"
                ]
                block["blocked_by"] = blocked
                if annotation["classification"] in MATH_CLASSES:
                    block["step_status"] = (
                        "BLOCKED"
                        if blocked
                        else "VERIFIED"
                        if block["claim_verdict"] == "PROVED"
                        else "UNVERIFIED"
                    )
                    block["author_proof_verdict"] = (
                        "GAP_FOUND"
                        if blocked
                        or block["claim_verdict"] != "PROVED"
                        or annotation["classification"] == "claim"
                        else "UNKNOWN"
                    )
        counts = Counter(item["classification"] for item in row["annotations"].values())
        verified = [block for block in by_block.values() if block["step_status"] == "VERIFIED"]
        return {
            "schema": SCHEMA,
            "article_id": key,
            "revision": row["revision"],
            "source": {name: source[name] for name in ("title", "url", "sha256")},
            "source_fingerprint": row["source_fingerprint"],
            "sections": inventory["sections"],
            "blocks": list(by_block.values()),
            "events": row["events"],
            "workspaces": deepcopy(row.get("workspaces", [])),
            "counts": {
                "source_blocks": len(by_block),
                "classified": len(row["annotations"]),
                "unclassified": len(by_block) - len(row["annotations"]),
                "classes": dict(counts),
                "current_verified_obligations": len(verified),
                "unverified_mathematical_obligations": sum(
                    1
                    for block in by_block.values()
                    if block["annotation"]
                    and block["annotation"]["classification"] in MATH_CLASSES
                    and block["step_status"] != "VERIFIED"
                ),
            },
            "evidence_rechecked": recheck,
            "checked_at": _now() if recheck else None,
            "whole_article_verified": False,
            "claim_verdict": "UNKNOWN",
            "author_proof_verdict": "GAP_FOUND",
            "model_calls": 0,
            "new_lean_executions": 0,
            "limitations": [
                "Extracted blocks are an inventory, not exhaustive semantic proof obligations.",
                "Whole-article certification is not implemented; no coverage percentage is a proof.",
                "A proven conclusion never certifies the author's complete argument.",
                "Only equivalent exact-node links can grant a local claim verdict; counterexample specializations remain separate findings.",
                "Source interpretation is manually reviewed with local-assertion-only authentication.",
                "GET shows historical references without rechecking; explicit recheck reads existing verifiers and never executes Lean.",
            ],
        }
