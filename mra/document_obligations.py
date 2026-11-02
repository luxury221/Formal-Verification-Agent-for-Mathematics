"""Versioned source-to-obligation review; formal authority stays with existing providers."""

from copy import deepcopy
from pathlib import Path
import re
import uuid

from .article_audit import closure, evidence_identity
from .article_evidence_reader import ArticleEvidenceReader
from .document_intake import DocumentStore, read_json, request_id
from .domain import digest
from .formalizer import exact_object
from .model_service import text_field
from . import obligation_drafts as drafts
from .research_flow import atomic, identifier, lease
from .research_protocol import BACKENDS, source_request, texts
from .research_studio import StudioStore
from .tasks import WorkerBusy, _now

SCHEMA = "source-mathematical-obligations-v1"
REVIEW_ITEMS = (
    "sources",
    "objects",
    "definitions",
    "quantifiers",
    "assumptions",
    "conclusion",
    "dependencies",
)
FIELDS = {
    "title",
    "kind",
    "sources",
    "objects",
    "definitions",
    "quantifiers",
    "assumptions",
    "conclusion",
    "references",
    "depends_on",
    "questions",
}
LIMITS = {
    "dossiers": 30,
    "obligations": 64,
    "versions": 160,
    "actions": 240,
    "workspaces": 24,
    "links": 32,
}
ROLES = ("statement", "definition", "assumption", "proof", "context")


def short_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", value):
        raise ValueError("Use a short ASCII obligation ID starting with a letter")
    return value


def validate_content(raw, blocks):
    """Shared human / future model candidate DTO. Validation grants no evidence."""
    exact_object(raw, FIELDS, "Complete mathematical obligation")
    text_field(raw["title"], "Obligation title", 200)
    if raw["kind"] not in ("claim", "author_step", "external_fact", "definition"):
        raise ValueError("Unknown mathematical obligation kind")
    for name in ("objects", "definitions", "quantifiers"):
        if not isinstance(raw[name], str) or len(raw[name]) > 4000:
            raise ValueError(name + " must be text of at most 4000 characters")
    text_field(raw["conclusion"], "Complete conclusion", 6000)
    for name in ("assumptions", "references", "questions"):
        texts(raw[name], name, 20, 2000)
    deps = texts(raw["depends_on"], "Prerequisite obligations", 32, 48)
    if len(deps) != len(set(deps)):
        raise ValueError("Duplicate prerequisite obligation")
    for key in deps:
        short_id(key)
    refs = raw["sources"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 24:
        raise ValueError("Select 1–24 original source fragments")
    seen = set()
    for ref in refs:
        exact_object(ref, {"block_id", "role"}, "Original source reference")
        if (
            not isinstance(ref["block_id"], str)
            or ref["block_id"] not in blocks
            or ref["role"] not in ROLES
        ):
            raise ValueError("Source reference must select an existing fixed fragment and role")
        pair = (ref["block_id"], ref["role"])
        if pair in seen:
            raise ValueError("Duplicate source reference and role")
        if blocks[ref["block_id"]]["kind"] == "unreadable":
            raise ValueError("Recover unreadable source text before creating an obligation")
        seen.add(pair)
    if not any(r["role"] == "statement" for r in refs):
        raise ValueError("Select at least one fragment containing the original claim")
    value = deepcopy(raw)
    if len(complete_statement(value)) > 8000:
        raise ValueError(
            "Complete obligation exceeds the 8000-character verifier bridge budget; split explicitly without truncating conditions"
        )
    return value


def complete_statement(content):
    """Deterministic complete text: no field containing mathematical conditions is dropped."""
    parts = [
        ("对象与类型", content["objects"]),
        ("定义与记号", content["definitions"]),
        ("量词与范围", content["quantifiers"]),
        ("全部前提", "\n".join(content["assumptions"]) or "无额外前提"),
        ("完整结论", content["conclusion"]),
    ]
    return "\n\n".join(label + "：\n" + value for label, value in parts)


class ObligationStore:
    def __init__(self, root, *, documents=None, studio=None):
        self.root = Path(root).resolve()
        self.documents = documents or DocumentStore(self.root)
        self.studio = studio or StudioStore(self.root)
        self.base = self.root / "artifacts/document-obligations"
        self.base.mkdir(parents=True, exist_ok=True)
        if self.base.resolve() != self.base:
            raise ValueError("Obligation storage cannot be redirected")

    def directory(self, key):
        path = self.base / identifier(key, "dossier")
        if self.base.resolve() != self.base or path.resolve() != path:
            raise ValueError("Obligation storage cannot be redirected")
        return path

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Obligation storage cannot be redirected")
        paths = sorted(self.base.glob("dossier-*"))
        if len(paths) > LIMITS["dossiers"]:
            raise ValueError("Obligation dossier capacity exceeded")
        return paths

    def read(self, key):
        path = self.directory(key)
        row, source = read_json(path / "record.json"), read_json(path / "source.json")
        if (
            row["schema"] != SCHEMA
            or row["dossier_id"] != key
            or digest(source) != row["source_fingerprint"]
        ):
            raise ValueError("Fixed source snapshot identity changed")
        blocks = {b["id"]: b for b in source["blocks"]}
        if len(blocks) != len(source["blocks"]):
            raise ValueError("Duplicate source fragment identity")
        if (
            len(row["obligations"]) > LIMITS["obligations"]
            or len(row["versions"]) > LIMITS["versions"]
        ):
            raise ValueError("Obligation capacity exceeded")
        versions = {}
        for item in row["versions"]:
            exact_object(
                item, {"id", "version", "content", "content_fingerprint"}, "Obligation version"
            )
            short_id(item["id"])
            content = validate_content(item["content"], blocks)
            if (
                type(item["version"]) is not int
                or item["version"] != versions.get(item["id"], 0) + 1
                or digest(content) != item["content_fingerprint"]
            ):
                raise ValueError("Obligation version identity changed")
            versions[item["id"]] = item["version"]
        if set(versions) != set(row["obligations"]):
            raise ValueError("Current obligation inventory differs from its version history")
        for oid, item in row["obligations"].items():
            if (
                item["id"] != oid
                or item not in row["versions"]
                or item != [x for x in row["versions"] if x["id"] == oid][-1]
            ):
                raise ValueError("Current obligation version changed")
            self.order(row, oid)
        if (
            type(row["revision"]) is not int
            or row["revision"] != len(row["requests"])
            or row["revision"] != len(row["events"])
            or row["revision"] > LIMITS["actions"]
        ):
            raise ValueError("Dossier revision and action history disagree")
        if len({request_id(x["id"]) for x in row["requests"]}) != row["revision"]:
            raise ValueError("Duplicate dossier action identity")
        for index, event in enumerate(row["events"], 1):
            if event["revision"] != index:
                raise ValueError("Dossier event revision changed")
        for event in row["source_reviews"]:
            exact_object(
                event,
                {"block_ids", "reviewer", "note", "confirmed", "at"},
                "Source extraction review",
            )
            ids = texts(event["block_ids"], "Reviewed source fragments", 24, 160)
            if (
                not ids
                or len(set(ids)) != len(ids)
                or not set(ids) <= set(blocks)
                or type(event["confirmed"]) is not bool
            ):
                raise ValueError("Invalid extraction review binding")
            text_field(event["reviewer"], "Extraction reviewer", 200)
        for event in row["reviews"]:
            if (
                event["obligation_id"] not in row["obligations"]
                or event["decision"] not in ("accept", "reject", "revoke")
                or event["authentication"] != "local-assertion-only"
            ):
                raise ValueError("Invalid semantic review binding")
            text_field(event["reviewer"], "Semantic reviewer", 200)
            if event["decision"] == "accept" and sorted(event["confirmations"]) != sorted(
                REVIEW_ITEMS
            ):
                raise ValueError("Incomplete semantic acceptance")
        imports = row.get("draft_imports", {})
        if not isinstance(imports, dict) or len(imports) > LIMITS["obligations"]:
            raise ValueError("Invalid automatic draft history")
        for bid, receipt in imports.items():
            exact_object(
                receipt,
                {
                    "obligation_id",
                    "initial_content_fingerprint",
                    "generator",
                    "plan_fingerprint",
                    "import_revision",
                },
                "Source draft import receipt",
            )
            oid = receipt["obligation_id"]
            initial = next((v for v in row["versions"] if v["id"] == oid), None)
            revision = receipt["import_revision"]
            if (
                bid not in blocks
                or blocks[bid]["kind"] != "claim"
                or oid != drafts.obligation_id(row["source_fingerprint"], bid)
                or not initial
                or initial["content_fingerprint"] != receipt["initial_content_fingerprint"]
                or initial["content"]["conclusion"] != blocks[bid]["text"]
                or {"block_id": bid, "role": "statement"} not in initial["content"]["sources"]
                or type(revision) is not int
                or not 1 <= revision <= row["revision"]
                or row["events"][revision - 1]["kind"] != "SOURCE_DRAFTS_IMPORTED_WITHOUT_REVIEW"
                or receipt["generator"] != drafts.SCHEMA
                or not isinstance(receipt["plan_fingerprint"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", receipt["plan_fingerprint"])
            ):
                raise ValueError("Automatic draft provenance changed")
        return row, source

    @staticmethod
    def order(row, oid):
        short_id(oid)
        return closure({key: item["content"] for key, item in row["obligations"].items()}, oid)

    def fingerprint(self, row, oid):
        order = self.order(row, oid)
        ids = {
            r["block_id"] for key in order for r in row["obligations"][key]["content"]["sources"]
        }
        extraction_reviews = {
            bid: next((x for x in reversed(row["source_reviews"]) if bid in x["block_ids"]), None)
            for bid in sorted(ids)
        }
        return digest(
            {
                "source": row["source_fingerprint"],
                "obligations": [row["obligations"][key] for key in order],
                "extraction_reviews": extraction_reviews,
            }
        )

    @staticmethod
    def source_snapshot(row, inputs, draft):
        return {
            "document_id": row["document_id"],
            "document_revision": row["revision"],
            "input_fingerprint": row["input_fingerprint"],
            "draft_sha256": row["draft_sha256"],
            "inputs": inputs,
            "title": row["title"],
            "url": row["url"],
            "method": draft["method"],
            "blocks": draft["blocks"],
            "pages": draft["pages"],
        }

    def source_state(self, source):
        try:
            row, inputs = self.documents.checked_inputs(source["document_id"])
            draft = self.documents.draft(source["document_id"])
            if row["revision"] != source["document_revision"]:
                return "SUPERSEDED"
            if digest(self.source_snapshot(row, inputs, draft)) != digest(source):
                return "SOURCE_CHANGED"
            return "CURRENT"
        except (OSError, ValueError, KeyError, TypeError):
            return "SOURCE_UNAVAILABLE"

    def require_current(self, source):
        if self.source_state(source) != "CURRENT":
            raise ValueError(
                "Source revision changed or is unavailable; preserve this history and create a new dossier from the current document"
            )

    def create(self, data):
        exact_object(
            data,
            {"request_id", "document_id", "expected_document_revision"},
            "Create fixed source obligation dossier",
        )
        request_id(data["request_id"])
        if type(data["expected_document_revision"]) is not int:
            raise ValueError("Use an explicit document revision")
        with lease(self.base):
            for path in self.paths():
                old, _ = self.read(path.name)
                if old["request_id"] == data["request_id"]:
                    if old["request_fingerprint"] != digest(data):
                        raise ValueError(
                            "Dossier request identifier belongs to another source revision"
                        )
                    return self.report(path.name)
            if len(self.paths()) >= LIMITS["dossiers"]:
                raise ValueError("Local dossier capacity reached")
            with lease(self.documents.directory(data["document_id"])):
                row, inputs = self.documents.checked_inputs(data["document_id"])
                draft = self.documents.draft(data["document_id"])
                if row["revision"] != data["expected_document_revision"]:
                    raise ValueError("Document revision changed; reload before freezing its source")
                source = self.source_snapshot(row, inputs, draft)
                key = "dossier-" + uuid.uuid4().hex
                record = {
                    "schema": SCHEMA,
                    "dossier_id": key,
                    "request_id": data["request_id"],
                    "request_fingerprint": digest(data),
                    "source_fingerprint": digest(source),
                    "revision": 0,
                    "created_at": _now(),
                    "obligations": {},
                    "versions": [],
                    "source_reviews": [],
                    "reviews": [],
                    "workspaces": [],
                    "links": [],
                    "requests": [],
                    "events": [],
                }
                staging = self.base / (".creating-" + key)
                staging.mkdir(mode=0o700)
                atomic(staging / "source.json", source)
                atomic(staging / "record.json", record)
                staging.rename(self.directory(key))
        return self.report(key)

    def change(self, key, data, kind, action):
        request_id(data["request_id"])
        with lease(self.directory(key)):
            row, source = self.read(key)
            old = next((x for x in row["requests"] if x["id"] == data["request_id"]), None)
            if old:
                if old["fingerprint"] != digest(data):
                    raise ValueError("Action identifier belongs to different content")
                return self.report(key)
            if (
                type(data["expected_revision"]) is not int
                or data["expected_revision"] != row["revision"]
            ):
                raise ValueError("Obligation dossier changed; reload before saving")
            with lease(self.documents.directory(source["document_id"])):
                self.require_current(source)
                if len(row["requests"]) >= LIMITS["actions"]:
                    raise ValueError("Dossier action budget exhausted; export before continuing")
                action(row, source)
                row["revision"] += 1
                row["requests"].append({"id": data["request_id"], "fingerprint": digest(data)})
                row["events"].append({"at": _now(), "kind": kind, "revision": row["revision"]})
                atomic(self.directory(key) / "record.json", row)
        return self.report(key)

    def save(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "obligation_id", "content"},
            "Save complete obligation",
        )
        oid = short_id(data["obligation_id"])

        def action(row, source):
            content = validate_content(data["content"], {b["id"]: b for b in source["blocks"]})
            old = row["obligations"].get(oid)
            if old and old["content"] == content:
                return
            if (
                not old
                and len(row["obligations"]) >= LIMITS["obligations"]
                or len(row["versions"]) >= LIMITS["versions"]
            ):
                raise ValueError("Obligation or version capacity reached")
            item = {
                "id": oid,
                "version": old["version"] + 1 if old else 1,
                "content": content,
                "content_fingerprint": digest(content),
            }
            row["obligations"][oid] = item
            for other in row["obligations"]:
                self.order(row, other)
            row["versions"].append(item)

        return self.change(key, data, "OBLIGATION_VERSION_SAVED", action)

    @staticmethod
    def draft_fingerprint(row):
        return digest(
            {
                "source": row["source_fingerprint"],
                "revision": row["revision"],
                "generator": drafts.SCHEMA,
                "implementation": drafts.IMPLEMENTATION,
                "limits": LIMITS,
                "fields": sorted(FIELDS),
            }
        )

    def _draft(self, row, source, bid):
        text_field(bid, "Source fragment ID", 160)
        value = drafts.candidate(source, bid)
        value["obligation_id"] = drafts.obligation_id(row["source_fingerprint"], bid)
        receipt = row.get("draft_imports", {}).get(bid)
        value["imported_obligation_id"] = receipt["obligation_id"] if receipt else None
        if not receipt and value["obligation_id"] in row["obligations"]:
            value["issues"].append("候选编号已被其他义务占用；保留原义务，不自动覆盖。")
        if value["content"] is not None:
            if len(complete_statement(value["content"])) > 8000 and value["content"]["definitions"]:
                value["content"]["definitions"] = ""
                value["content"]["questions"].append(
                    "引用定义未填入定义栏：完整目标超过 8000 字符预算，完整定义仍保留在关联来源；请人工整理后补全。"
                )
            try:
                value["content"] = validate_content(
                    value["content"], {b["id"]: b for b in source["blocks"]}
                )
            except ValueError as error:
                value["content"] = None
                value["issues"].append(str(error))
        value["status"] = "IMPORTED" if receipt else "BLOCKED" if value["issues"] else "READY"
        return value

    def draft_plan(self, key, data):
        exact_object(data, set(), "Prepare source draft inventory")
        row, source = self.read(key)
        self.require_current(source)
        candidates = []
        for block in source["blocks"]:
            if block["kind"] != "claim":
                continue
            item = self._draft(row, source, block["id"])
            candidates.append(
                {
                    "block_id": block["id"],
                    "title": item["title"],
                    "characters": len(block["text"]),
                    "obligation_id": item["obligation_id"],
                    "status": item["status"],
                    "issues": item["issues"],
                    "unreviewed_references": len(item["reference_hints"]),
                }
            )
        return {
            "schema": drafts.SCHEMA,
            "dossier_id": key,
            "revision": row["revision"],
            "source_fingerprint": row["source_fingerprint"],
            "plan_fingerprint": self.draft_fingerprint(row),
            "generator_sha256": drafts.IMPLEMENTATION,
            "candidates": candidates,
            "coverage": {
                "source_blocks": len(source["blocks"]),
                "claim_fragments": len(candidates),
                "nonclaim_fragments": len(source["blocks"]) - len(candidates),
                "not_a_complete_mathematical_inventory": True,
            },
            "limits": {
                "batch": drafts.MAX_BATCH,
                "obligations_remaining": LIMITS["obligations"] - len(row["obligations"]),
                "versions_remaining": LIMITS["versions"] - len(row["versions"]),
                "actions_remaining": LIMITS["actions"] - row["revision"],
            },
            "automatic_semantic_generation": False,
            "model_calls": 0,
            "lean_executions": 0,
            "whole_article_verified": False,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }

    def draft_preview(self, key, data):
        exact_object(data, {"block_id", "plan_fingerprint"}, "Preview an unreviewed source draft")
        row, source = self.read(key)
        self.require_current(source)
        if data["plan_fingerprint"] != self.draft_fingerprint(row):
            raise ValueError("Source draft plan changed; prepare the current inventory again")
        value = self._draft(row, source, data["block_id"])
        value.update(
            schema=drafts.SCHEMA,
            plan_fingerprint=data["plan_fingerprint"],
            source_fingerprint=row["source_fingerprint"],
        )
        return value

    def import_drafts(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "plan_fingerprint", "block_ids"},
            "Import unreviewed source drafts",
        )
        ids = texts(data["block_ids"], "Selected claim fragments", drafts.MAX_BATCH, 160)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Select 1–16 distinct claim fragments")

        def action(row, source):
            if data["plan_fingerprint"] != self.draft_fingerprint(row):
                raise ValueError("Source draft plan changed; prepare the current inventory again")
            values = [self._draft(row, source, bid) for bid in ids]
            if any(v["status"] == "BLOCKED" for v in values):
                raise ValueError(
                    "One or more selected drafts exceed limits or conflict; no drafts were imported"
                )
            new = [v for v in values if v["status"] != "IMPORTED"]
            if len({v["obligation_id"] for v in new}) != len(new):
                raise ValueError("Generated draft IDs conflict; no drafts were imported")
            if (
                len(row["obligations"]) + len(new) > LIMITS["obligations"]
                or len(row["versions"]) + len(new) > LIMITS["versions"]
            ):
                raise ValueError("Obligation or version capacity reached; no drafts were imported")
            for value in new:
                content, oid = value["content"], value["obligation_id"]
                item = {
                    "id": oid,
                    "version": 1,
                    "content": content,
                    "content_fingerprint": digest(content),
                }
                row["obligations"][oid] = item
                row["versions"].append(item)
                row.setdefault("draft_imports", {})[value["block_id"]] = {
                    "obligation_id": oid,
                    "initial_content_fingerprint": item["content_fingerprint"],
                    "generator": drafts.SCHEMA,
                    "plan_fingerprint": data["plan_fingerprint"],
                    "import_revision": row["revision"] + 1,
                }

        return self.change(key, data, "SOURCE_DRAFTS_IMPORTED_WITHOUT_REVIEW", action)

    def extraction_review(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "block_ids", "reviewer", "note", "confirmed"},
            "Review fixed source extraction",
        )
        text_field(data["reviewer"], "Extraction reviewer", 200)
        text_field(data["note"], "Extraction correspondence note", 2000)
        if type(data["confirmed"]) is not bool:
            raise ValueError("Use an explicit extraction confirmation flag")

        def action(row, source):
            ids = texts(data["block_ids"], "Source fragments", 24, 160)
            if (
                not ids
                or len(ids) != len(set(ids))
                or not set(ids) <= {b["id"] for b in source["blocks"]}
            ):
                raise ValueError("Choose distinct fixed source fragments")
            row["source_reviews"].append(
                {
                    "block_ids": ids,
                    "reviewer": data["reviewer"],
                    "note": data["note"],
                    "confirmed": data["confirmed"],
                    "at": _now(),
                }
            )

        return self.change(key, data, "EXTRACTION_REVIEWED_WITHOUT_MATH_EVIDENCE", action)

    @staticmethod
    def extraction_checked(row, source, bid):
        event = next((x for x in reversed(row["source_reviews"]) if bid in x["block_ids"]), None)
        return (
            event["confirmed"]
            if event
            else next(b for b in source["blocks"] if b["id"] == bid).get(
                "extraction_reviewed", False
            )
        )

    def effective_review(self, row, oid):
        event = next((x for x in reversed(row["reviews"]) if x["obligation_id"] == oid), None)
        return (
            event
            if event
            and event["decision"] == "accept"
            and event["obligation_fingerprint"] == self.fingerprint(row, oid)
            else None
        )

    def binding(self, row, oid):
        reviews = {key: self.effective_review(row, key) for key in self.order(row, oid)}
        if not all(reviews.values()):
            raise ValueError(
                "Confirm the complete obligation and each prerequisite before transferring or binding evidence"
            )
        return digest({"obligation": self.fingerprint(row, oid), "reviews": reviews})

    def review_preview(self, key, data):
        exact_object(data, {"obligation_id"}, "Preview source-to-obligation correspondence")
        row, source = self.read(key)
        return self._review_preview(row, source, data["obligation_id"])

    def _review_preview(self, row, source, oid):
        order = self.order(row, oid)
        item = row["obligations"][oid]
        ids = {
            r["block_id"] for key in order for r in row["obligations"][key]["content"]["sources"]
        }
        fragments = [
            {**deepcopy(b), "extraction_checked": self.extraction_checked(row, source, b["id"])}
            for b in source["blocks"]
            if b["id"] in ids
        ]
        value = {
            "dossier_id": row["dossier_id"],
            "revision": row["revision"],
            "source_fingerprint": row["source_fingerprint"],
            "source_state": self.source_state(source),
            "obligation_id": oid,
            "obligation_fingerprint": self.fingerprint(row, oid),
            "obligation": item,
            "complete_statement": complete_statement(item["content"]),
            "prerequisites": [row["obligations"][key] for key in order if key != oid],
            "fragments": fragments,
            "required_confirmations": list(REVIEW_ITEMS),
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        value["preview_fingerprint"] = digest(value)
        return value

    def review(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "obligation_id",
                "preview_fingerprint",
                "decision",
                "reviewer",
                "note",
                "confirmations",
            },
            "Review mathematical correspondence",
        )
        text_field(data["reviewer"], "Local semantic reviewer", 200)
        text_field(data["note"], "Semantic correspondence or unresolved issue", 4000)
        if data["decision"] not in ("accept", "reject", "revoke"):
            raise ValueError("Choose accept, reject or revoke")
        if not isinstance(data["confirmations"], list) or any(
            not isinstance(x, str) for x in data["confirmations"]
        ):
            raise ValueError("Use an explicit list of semantic confirmations")

        def action(row, source):
            preview = self._review_preview(row, source, data["obligation_id"])
            if preview["preview_fingerprint"] != data["preview_fingerprint"]:
                raise ValueError("Source or complete obligation preview changed")
            if data["decision"] == "accept":
                if sorted(data["confirmations"]) != sorted(REVIEW_ITEMS):
                    raise ValueError(
                        "Confirm sources, objects, definitions, quantifiers, all assumptions, conclusion and dependencies"
                    )
                if any(
                    row["obligations"][oid]["content"]["questions"]
                    for oid in self.order(row, data["obligation_id"])
                ):
                    raise ValueError(
                        "Resolve all open clarification questions before accepting semantics"
                    )
                if not all(x["extraction_checked"] for x in preview["fragments"]):
                    raise ValueError(
                        "Review source extraction separately before semantic acceptance"
                    )
            if data["decision"] == "revoke":
                old = self.effective_review(row, data["obligation_id"])
                if not old or old["reviewer"] != data["reviewer"]:
                    raise ValueError("Only the named reviewer can revoke their current acceptance")
            row["reviews"].append(
                {
                    "review_id": "semantic-" + uuid.uuid4().hex,
                    "obligation_id": data["obligation_id"],
                    "obligation_fingerprint": preview["obligation_fingerprint"],
                    "decision": data["decision"],
                    "reviewer": data["reviewer"],
                    "note": data["note"],
                    "confirmations": deepcopy(data["confirmations"]),
                    "at": _now(),
                    "authentication": "local-assertion-only",
                }
            )

        return self.change(key, data, "SEMANTIC_REVIEW_RECORDED_WITHOUT_PROOF", action)

    def create_workspace(self, key, data):
        exact_object(
            data,
            {"request_id", "expected_revision", "obligation_id", "area"},
            "Transfer complete obligation to research",
        )
        text_field(data["area"], "Mathematical area", 120)

        def action(row, source):
            oid = data["obligation_id"]
            binding = self.binding(row, oid)
            content = row["obligations"][oid]["content"]
            if content["kind"] == "definition":
                raise ValueError("A definition is context, not a theorem to prove")
            if len(row["workspaces"]) >= LIMITS["workspaces"]:
                raise ValueError("Dossier research-task capacity reached")
            # Dependency claims stay outside assumptions. The final checked target must discharge them.
            prerequisites = [
                {"id": p, "statement": complete_statement(row["obligations"][p]["content"])}
                for p in self.order(row, oid)
                if p != oid
            ]
            context = (
                "固定原文："
                + source["title"]
                + "\n"
                + source["url"]
                + "\n来源摘要："
                + row["source_fingerprint"]
                + "\n原文片段："
                + ", ".join(r["block_id"] for r in content["sources"])
                + "\n对象："
                + content["objects"]
                + "\n定义："
                + content["definitions"]
                + "\n引用线索（未经证明，不是新增假设）：\n"
                + "\n".join(content["references"])
                + "\n前置义务须证明或在最终目标中消解，不得直接添加为假设：\n"
                + "\n".join(p["id"] + ": " + p["statement"] for p in prerequisites)
            )
            problem = source_request(
                {
                    "title": content["title"],
                    "question": complete_statement(content),
                    "areas": [data["area"]],
                    "definitions": context,
                    "assumptions": "\n".join(content["assumptions"]),
                }
            )
            study = self.studio.create(
                {"request_id": "document-study-" + digest([key, data]), "source": problem}
            )
            if not study["plans"]:
                study = self.studio.scaffold(
                    study["study_id"],
                    {
                        "request_id": "document-scaffold-" + digest([key, data]),
                        "expected_revision": study["revision"],
                    },
                )
            row["workspaces"].append(
                {
                    "workspace_id": "obligationwork-" + uuid.uuid4().hex,
                    "obligation_id": oid,
                    "binding_fingerprint": binding,
                    "study_id": study["study_id"],
                    "study_source_fingerprint": study["source_fingerprint"],
                    "created_at": _now(),
                }
            )

        return self.change(key, data, "COMPLETE_OBLIGATION_TRANSFERRED_WITHOUT_EXECUTION", action)

    def workspace(self, row, workspace_id):
        workspace = next((x for x in row["workspaces"] if x["workspace_id"] == workspace_id), None)
        if workspace is None:
            raise ValueError("Unknown obligation research task")
        if self.binding(row, workspace["obligation_id"]) != workspace["binding_fingerprint"]:
            raise ValueError("Research task belongs to an earlier obligation or semantic review")
        study = self.studio.read(workspace["study_id"])
        if study["source_fingerprint"] != workspace["study_source_fingerprint"] or study["source"][
            "question"
        ] != complete_statement(row["obligations"][workspace["obligation_id"]]["content"]):
            raise ValueError("Research source no longer matches the complete obligation")
        return workspace, study

    def research_tasks(self, key, data):
        exact_object(data, {"workspace_id"}, "Read transferred verification candidates")
        row, source = self.read(key)
        self.require_current(source)
        workspace, study = self.workspace(row, data["workspace_id"])
        transfers = []
        for item in study["transfers"]:
            try:
                attempt = self.studio.round(study, item["round_id"])
                nodes = [
                    n["id"]
                    for n in attempt["proposal"]["formalization"]["request"]["nodes"]
                    if n["kind"] == "problem"
                    and n["target"]["original"] == study["source"]["question"]
                ]
            except ValueError:
                nodes = []
            transfers.append(
                {**deepcopy(item), "eligible_node_ids": nodes, "evidence_checked": False}
            )
        return {
            "workspace_id": workspace["workspace_id"],
            "study_id": study["study_id"],
            "transfers": transfers,
        }

    def _evidence_preview(self, row, source, data, reports=None):
        self.require_current(source)
        workspace, study = self.workspace(row, data["workspace_id"])
        transfer = next((x for x in study["transfers"] if x["graph_id"] == data["graph_id"]), None)
        if transfer is None:
            raise ValueError(
                "Select a verifier task actually transferred from this obligation's research notebook"
            )
        attempt = self.studio.round(study, transfer["round_id"])
        nodes = attempt["proposal"]["formalization"]["request"]["nodes"]
        if not any(
            n["id"] == data["node_id"]
            and n["kind"] == "problem"
            and n["target"]["original"] == study["source"]["question"]
            for n in nodes
        ):
            raise ValueError(
                "Evidence must cover the complete final obligation, not a supporting lemma"
            )
        reports = {} if reports is None else reports
        cache_key = (transfer["backend"], transfer["graph_id"])
        if cache_key not in reports:
            reports[cache_key] = ArticleEvidenceReader(self.root, *cache_key).run()
        report = reports[cache_key]
        identity = evidence_identity(report, data["node_id"])
        if (
            identity["plan_fingerprint"] != transfer["plan_fingerprint"]
            or identity["original"] != study["source"]["question"]
        ):
            raise ValueError(
                "Checked formal target differs from the transferred complete obligation"
            )
        if self.studio.read(study["study_id"])["revision"] != study["revision"]:
            raise ValueError("Research notebook changed during evidence review")
        self.require_current(source)
        value = {
            "dossier_id": row["dossier_id"],
            "revision": row["revision"],
            "workspace_id": workspace["workspace_id"],
            "obligation_id": workspace["obligation_id"],
            "binding_fingerprint": workspace["binding_fingerprint"],
            "study_id": study["study_id"],
            "study_revision": study["revision"],
            "graph_id": transfer["graph_id"],
            "node_id": data["node_id"],
            "backend": transfer["backend"],
            "identity": identity,
            "scope": "This complete reviewed obligation only; author proof steps and whole article remain unverified",
        }
        value["preview_fingerprint"] = digest(value)
        return value

    def evidence_preview(self, key, data):
        exact_object(
            data, {"workspace_id", "graph_id", "node_id"}, "Preview exact obligation evidence"
        )
        row, source = self.read(key)
        return self._evidence_preview(row, source, data)

    def bind(self, key, data):
        exact_object(
            data,
            {
                "request_id",
                "expected_revision",
                "workspace_id",
                "graph_id",
                "node_id",
                "preview_fingerprint",
                "reviewer",
                "note",
                "confirmed",
            },
            "Return checked evidence to the fixed source obligation",
        )
        text_field(data["reviewer"], "Evidence correspondence reviewer", 200)
        text_field(data["note"], "Complete source/formal correspondence and limitations", 4000)
        if data["confirmed"] is not True:
            raise ValueError(
                "Confirm the complete original obligation and exact checked formal target"
            )

        def action(row, source):
            preview = self._evidence_preview(row, source, data)
            if preview["preview_fingerprint"] != data["preview_fingerprint"]:
                raise ValueError("Evidence or source correspondence preview changed")
            if len(row["links"]) >= LIMITS["links"]:
                raise ValueError("Dossier evidence-link capacity reached")
            row["links"].append(
                {
                    "link_id": "obligationlink-" + uuid.uuid4().hex,
                    **{
                        k: preview[k]
                        for k in (
                            "workspace_id",
                            "obligation_id",
                            "binding_fingerprint",
                            "study_id",
                            "graph_id",
                            "node_id",
                            "backend",
                            "identity",
                        )
                    },
                    "reviewer": data["reviewer"],
                    "note": data["note"],
                    "at": _now(),
                    "authentication": "local-assertion-only",
                }
            )

        return self.change(key, data, "CHECKED_COMPLETE_OBLIGATION_LINKED", action)

    def recheck(self, key, data):
        exact_object(
            data, {"obligation_id"}, "Recheck current evidence for one complete obligation"
        )
        return self.report(key, recheck_obligation=data["obligation_id"])

    def report(self, key, *, recheck_obligation=None):
        row, source = self.read(key)
        if recheck_obligation is not None:
            self.order(row, recheck_obligation)
        source_state = self.source_state(source)
        result = deepcopy(row)
        result.update(
            source=source,
            source_state=source_state,
            whole_article_verified=False,
            math_verdict="UNKNOWN",
            evidence_level="E0",
            evidence_rechecked=recheck_obligation is not None,
            checked_at=_now() if recheck_obligation is not None else None,
        )
        result["obligation_states"] = {}
        for oid, item in row["obligations"].items():
            review = self.effective_review(row, oid)
            state = {
                "version": item["version"],
                "semantic_state": "ACCEPTED"
                if review and source_state == "CURRENT"
                else "NEEDS_REVIEW",
                "review": review,
                "complete_statement": complete_statement(item["content"]),
                "math_verdict": "UNKNOWN",
                "evidence_level": "E0",
            }
            result["obligation_states"][oid] = state
        result["workspace_states"] = []
        for workspace in row["workspaces"]:
            try:
                self.require_current(source)
                self.workspace(row, workspace["workspace_id"])
                status, reason = "CURRENT", "Exact source, obligation versions and reviews match"
            except (OSError, ValueError, KeyError, TypeError) as error:
                status, reason = "STALE", str(error)[:400]
            result["workspace_states"].append(
                {
                    **deepcopy(workspace),
                    "status": status,
                    "reason": reason,
                    "href": "/studio#" + workspace["study_id"],
                }
            )
        result["link_states"] = []
        reports = {}
        for link in row["links"]:
            try:
                self.require_current(source)
                workspace, _ = self.workspace(row, link["workspace_id"])
                if (
                    workspace["binding_fingerprint"] != link["binding_fingerprint"]
                    or workspace["obligation_id"] != link["obligation_id"]
                ):
                    raise ValueError("Bound source or semantic review changed")
                status, reason = (
                    "NOT_RECHECKED",
                    "Historical PROVED/E4 binding only; explicitly recheck current artifacts",
                )
                if link["obligation_id"] == recheck_obligation:
                    preview = self._evidence_preview(row, source, link, reports)
                    if preview["identity"] != link["identity"]:
                        raise ValueError("Bound evidence identity changed")
                    status, reason = (
                        "CURRENT",
                        "Provider rechecked exact target and recorded artifacts",
                    )
                    result["obligation_states"][link["obligation_id"]].update(
                        math_verdict="PROVED", evidence_level="E4"
                    )
            except WorkerBusy:
                status, reason = (
                    "BUSY",
                    "Verifier evidence is being updated; no current verdict granted",
                )
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                status, reason = "UNAVAILABLE", str(error)[:400]
            result["link_states"].append({**deepcopy(link), "status": status, "reason": reason})
        # A recheck can take time. Discard its observations if another writer changed
        # the dossier or source meanwhile; never publish an old preview as current.
        latest, _ = self.read(key)
        if latest["revision"] != row["revision"] or self.source_state(source) != source_state:
            raise ValueError(
                "Source or obligation changed during evidence read; reload the current snapshot"
            )
        return result

    def listing(self):
        values = []
        for path in self.paths():
            row, source = self.read(path.name)
            values.append(
                {
                    "dossier_id": path.name,
                    "title": source["title"],
                    "document_id": source["document_id"],
                    "document_revision": source["document_revision"],
                    "revision": row["revision"],
                    "obligations": len(row["obligations"]),
                    "source_state": self.source_state(source),
                    "created_at": row["created_at"],
                }
            )
        return values

    @staticmethod
    def catalog():
        return {
            "schema": SCHEMA,
            "candidate_fields": sorted(FIELDS),
            "source_roles": list(ROLES),
            "required_confirmations": list(REVIEW_ITEMS),
            "limits": LIMITS,
            "backends": BACKENDS,
            "candidate_contract": "Structured proposals require separate source extraction review, semantic review and existing verifier checks",
            "automatic_semantic_generation": False,
            "automatic_draft_preparation": True,
            "draft_generator": drafts.SCHEMA,
            "draft_batch_limit": drafts.MAX_BATCH,
            "model_calls": 0,
            "whole_article_verified": False,
        }
