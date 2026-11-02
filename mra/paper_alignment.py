"""Inspectable component-to-source/AST correspondence; never certifies natural-language truth."""

from copy import deepcopy
import hashlib

from .domain import digest
from .formalizer import exact_object
from .model_service import text_field


def source_ref(block, span):
    return {
        "block_id": block["id"],
        "span": list(span),
        "slice_sha256": hashlib.sha256(block["text"][slice(*span)].encode()).hexdigest(),
    }


def components(content):
    result = []
    for field in ("objects", "definitions", "quantifiers", "assumptions", "conclusion"):
        values = content[field] if field == "assumptions" else [content[field]]
        for index, value in enumerate(values):
            # Original premises remain individual entries. Sub-conclusions must
            # be separate lines; this is a structural rule, not an NLP theorem.
            lines = [value] if field == "assumptions" else value.splitlines(keepends=True)
            start = 0
            for line_index, line in enumerate(lines):
                if line.strip():
                    result.append(
                        {
                            "id": f"{field}:{index}:{line_index}",
                            "kind": field,
                            "text": line,
                            "field": f"/content/{field}"
                            + (f"/{index}" if field == "assumptions" else ""),
                            "span": [start, start + len(line)],
                        }
                    )
                start += len(line)
    return result


def origins(source, quotes):
    blocks = {b["id"]: b for b in source["blocks"]}
    return [source_ref(blocks[q["block_id"]], q["span"]) for q in quotes]


def reading_requirements(source, focus, reading):
    blocks = {b["id"]: b for b in source["blocks"]}
    rows = [
        {
            "id": "unit:" + u["id"],
            "kind": "coverage",
            "text": "Account for this whole focus unit",
            "origins": [source_ref(blocks[u["block_id"]], u["span"])],
            "formal_paths": [],
        }
        for u in focus
    ]
    for item_id, item in reading["items"].items():
        for part in components(item["content"]):
            rows.append(
                {
                    **part,
                    "id": item_id + ":" + part["id"],
                    "origins": origins(source, item["quotes"]),
                    "formal_paths": [],
                }
            )
    return rows


def semantic_target(candidate):
    return {
        "profile": candidate["profile"],
        "request": {
            k: deepcopy(v)
            for k, v in candidate["request"].items()
            if k not in ("proof", "strategy", "timeout_seconds", "title", "original")
        },
        "formal_statement": candidate["formal_statement"],
    }


def catalog(candidate):
    request = candidate["request"]
    bindings = [
        f"/request/{field}/{i}"
        for field in ("variables", "spaces")
        for i, _ in enumerate(request.get(field, []))
    ]
    definitions = [
        f"/request/definitions/{i}" for i, _ in enumerate(request.get("definitions", []))
    ]
    assumptions = [
        f"/request/assumptions/{i}/formula" for i, _ in enumerate(request["assumptions"])
    ]
    quantifiers = []
    quantified_bindings = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("forall", "exists"):
                    quantifiers.append(path)
                    if isinstance(child, dict):
                        quantified_bindings.extend(
                            path + "/" + key + "/binders/" + str(i)
                            for i, _ in enumerate(child.get("binders", []))
                        )
                walk(child, path + "/" + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, path + "/" + str(i))

    def goals(value, path):
        if isinstance(value, dict) and set(value) == {"and"} and isinstance(value["and"], list):
            return [
                p
                for i, child in enumerate(value["and"])
                for p in goals(child, path + "/and/" + str(i))
            ]
        for op in ("forall", "exists"):
            if (
                isinstance(value, dict)
                and set(value) == {op}
                and isinstance(value[op], dict)
                and "body" in value[op]
            ):
                return goals(value[op]["body"], path + "/" + op + "/body")
        return [path]

    for region in ("goal", "assumptions", "definitions"):
        walk(request.get(region, []), "/request/" + region)
    bindings.extend(quantified_bindings)
    return {
        "objects": bindings or ["/request/goal"],
        "definitions": [*definitions, "/request/goal"],
        "quantifiers": list(dict.fromkeys([*bindings, *quantifiers])) or ["/request/goal"],
        "assumptions": assumptions,
        "conclusion": goals(request["goal"], "/request/goal"),
        "required_bindings": bindings,
        "required_definitions": definitions,
        "required_quantifiers": quantifiers,
    }


def formal_requirements(source, item, candidate):
    paths = catalog(candidate)
    return [
        {**part, "origins": origins(source, item["quotes"]), "formal_paths": paths[part["kind"]]}
        for part in components(item["content"])
    ]


def validate_audit(
    raw, source, source_fingerprint, proposal_fingerprint, requirements, candidate=None
):
    from .paper_protocol import audit_result

    exact_object(
        raw,
        {
            "source_fingerprint",
            "proposal_fingerprint",
            "decision",
            "checks",
            "issues",
            "required_context",
            "alignments",
        },
        "Component semantic audit v2",
    )
    audit_result(
        {k: v for k, v in raw.items() if k != "alignments"},
        source,
        source_fingerprint,
        proposal_fingerprint,
    )
    expected, seen = {r["id"]: r for r in requirements}, set()
    if not isinstance(raw["alignments"], list) or len(raw["alignments"]) > len(expected):
        raise ValueError("Audit must map the host's exact component inventory")
    blocks = {b["id"]: b for b in source["blocks"]}
    mapped = {}
    for row in raw["alignments"]:
        exact_object(
            row,
            {"id", "source_refs", "formal_paths", "relation", "explanation"},
            "Component correspondence",
        )
        key = row["id"]
        if key not in expected or key in seen:
            raise ValueError("Foreign or duplicate semantic component")
        seen.add(key)
        requirement = expected[key]
        if row["relation"] not in ("MATCH", "MISMATCH", "UNRESOLVED"):
            raise ValueError("Unknown correspondence relation")
        text_field(row["explanation"], "Specific component correspondence", 2000)
        refs = row["source_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError("Each correspondence needs exact fixed-source positions")
        for ref in refs:
            exact_object(ref, {"block_id", "span", "slice_sha256"}, "Alignment source slice")
            block, span = blocks.get(ref["block_id"]), ref["span"]
            if (
                block is None
                or not isinstance(span, list)
                or len(span) != 2
                or any(type(x) is not int for x in span)
                or not 0 <= span[0] <= span[1] <= len(block["text"])
            ):
                raise ValueError("Invalid component source range")
            if ref != source_ref(block, span) or not any(
                r["block_id"] == ref["block_id"]
                and r["span"][0] <= span[0] <= span[1] <= r["span"][1]
                for r in requirement["origins"]
            ):
                raise ValueError(
                    "Component correspondence is not grounded in its fixed original quotes"
                )
            if span[0] == span[1] and block["text"]:
                raise ValueError("A nonempty source needs a nonempty alignment slice")
        paths = row["formal_paths"]
        if (
            not isinstance(paths, list)
            or any(not isinstance(p, str) or p not in requirement["formal_paths"] for p in paths)
            or len(paths) != len(set(paths))
        ):
            raise ValueError("Correspondence must select the host's semantic AST paths")
        if raw["decision"] == "ACCEPT" and (
            row["relation"] != "MATCH" or (candidate is not None and not paths)
        ):
            raise ValueError("Acceptance needs explicit matching source and formal components")
        mapped[key] = paths
    if raw["decision"] == "ACCEPT":
        if seen != set(expected):
            raise ValueError("Acceptance cannot omit an original semantic component")
        if candidate is not None:
            available = catalog(candidate)
            for kind in ("assumptions", "conclusion"):
                assigned = [p for r in requirements if r["kind"] == kind for p in mapped[r["id"]]]
                if len(assigned) != len(set(assigned)) or set(assigned) != set(available[kind]):
                    raise ValueError(
                        "Original premises and sub-conclusions need separate, exhaustive AST correspondences"
                    )
            for kind, target_name in (
                ("objects", "required_bindings"),
                ("definitions", "required_definitions"),
                ("quantifiers", "required_quantifiers"),
            ):
                assigned = {p for r in requirements if r["kind"] == kind for p in mapped[r["id"]]}
                if not set(available[target_name]) <= assigned:
                    raise ValueError("A formal binding or definition has no original component")
    return deepcopy(raw)


def target_fingerprint(candidate):
    return digest(semantic_target(candidate))
