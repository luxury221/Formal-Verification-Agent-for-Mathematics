"""Deterministic source navigation and lossless reading partitions, not semantic parsing."""

from copy import deepcopy
import hashlib
import re

from .domain import canonical, digest

SCHEMA = "paper-source-index-v1"
MAX_SYMBOLS = 2048
MAX_FORMULAS = 2048
MAX_CHUNKS = 512
MAX_DEPTH = 6
TOKEN = re.compile(r"\\[A-Za-z]{1,64}|(?<![\w])(?:[A-Za-z]|[\u0370-\u03ff])(?![\w])")
BOUNDARY = re.compile(r"\n\s*\n|\n|[。；;]|[.!?](?=\s)")


def math_spans(text):
    """Locate paired literal math delimiters without expanding or executing TeX."""
    spans, issues, i = [], [], 0
    pairs = {"$$": "$$", "$": "$", r"\(": r"\)", r"\[": r"\]"}
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] not in "([":
            i += 2
            continue
        opening = next((p for p in ("$$", r"\(", r"\[", "$") if text.startswith(p, i)), None)
        if opening is None:
            i += 1
            continue
        closing, end = pairs[opening], i + len(opening)
        while True:
            end = text.find(closing, end)
            if end < 0:
                issues.append({"span": [i, i + len(opening)], "reason": "UNPAIRED_MATH_DELIMITER"})
                i += len(opening)
                break
            slash = end - 1
            while slash >= 0 and text[slash] == "\\":
                slash -= 1
            if (end - 1 - slash) % 2 == 0:
                end += len(closing)
                spans.append([i, end])
                i = end
                break
            end += len(closing)
    return spans, issues


def build_index(source):
    sections, blocks, symbols, issues = {}, [], [], []
    symbol_count, formula_count, issue_count = 0, 0, 0
    titles = {
        (s.get("source"), s.get("anchor", s.get("title"))): s.get("title", "")
        for s in source.get("sections", [])
    }
    for block in source["blocks"]:
        label = block.get("section", "Document")
        headings = [
            s["span"]
            for s in source.get("sections", [])
            if s.get("source") == block["source"]
            and s.get("title") == label
            and isinstance(s.get("span"), list)
            and block.get("span")
            and s["span"][0] <= block["span"][0]
        ]
        origin = max(headings, key=lambda s: s[0]) if headings else None
        scope = "scope-" + digest([block["source"], label, origin])[:16]
        sections.setdefault(
            scope,
            {
                "id": scope,
                "source": block["source"],
                "label": label[:512],
                "title": titles.get((block["source"], label), label)[:512],
                "blocks": [],
            },
        )["blocks"].append(block["id"])
        spans, unmatched = math_spans(block["text"])
        if block["kind"] == "equation" and not spans:
            spans = [[0, len(block["text"])]]
        names, observed = set(), set()
        for token in TOKEN.finditer(block["text"]):
            name = token.group()
            if name in observed:
                continue
            observed.add(name)
            symbol_count += 1
            if len(symbols) < MAX_SYMBOLS:
                names.add(name)
                symbols.append(
                    {
                        "symbol": name,
                        "block_id": block["id"],
                        "span": list(token.span()),
                        "scope_id": scope,
                        "status": "LEXICAL_CANDIDATE",
                    }
                )
        issue_count += len(unmatched)
        issues.extend(
            {"block_id": block["id"], **x} for x in unmatched[: max(0, 128 - len(issues))]
        )
        kept = spans[: max(0, MAX_FORMULAS - formula_count)]
        formula_count += len(spans)
        blocks.append(
            {
                "id": block["id"],
                "kind": block["kind"],
                "scope_id": scope,
                "source": block["source"],
                "source_span": block.get("span"),
                "characters": len(block["text"]),
                "formulas": [
                    {
                        "span": s,
                        "sha256": hashlib.sha256(block["text"][slice(*s)].encode()).hexdigest(),
                    }
                    for s in kept
                ],
                "formula_count": len(spans),
                "symbols": sorted(names),
                "definition_candidate": block["kind"] in ("definition", "notation"),
                "pages": block.get("pages", []),
            }
        )
    if symbol_count > MAX_SYMBOLS:
        issues.append({"reason": "SYMBOL_INDEX_LIMIT", "limit": MAX_SYMBOLS})
    if formula_count > MAX_FORMULAS:
        issues.append({"reason": "FORMULA_INDEX_LIMIT", "limit": MAX_FORMULAS})
    if issue_count > 128:
        issues.append({"reason": "DELIMITER_ISSUE_INDEX_LIMIT", "total": issue_count})
    result = {
        "schema": SCHEMA,
        "source_fingerprint": digest(source),
        "sections": list(sections.values()),
        "blocks": blocks,
        "symbols": symbols,
        "issues": issues,
        "scope": "EXTRACTED_TEXT_ONLY",
        "symbol_scope": "FIRST_TOKEN_PER_SYMBOL_PER_BLOCK",
        "semantic_scope_resolved": False,
        "extraction_fidelity_certified": False,
    }
    if len(canonical(result)) > 1800000:
        raise ValueError(
            "Source navigation index exceeds storage limit; source text was not truncated"
        )
    return result


def cut(text, start, stop, maximum):
    """Prefer a sentence/paragraph boundary outside a paired math region."""
    end = min(stop, start + maximum)
    if end == stop:
        return end
    protected, _ = math_spans(text)
    candidates = [
        m.end()
        for m in BOUNDARY.finditer(text, start, end)
        if m.end() > start + maximum // 3 and not any(a < m.end() < b for a, b in protected)
    ]
    if candidates:
        return candidates[-1]
    region = next(((a, b) for a, b in protected if a < end < b), None)
    if region and region[0] > start:
        return region[0]
    return end


def unit(block, start, end):
    return {
        "id": f"{block['id']}@{start}:{end}",
        "block_id": block["id"],
        "kind": block["kind"],
        "span": [start, end],
    }


def partition(source):
    """No grouping across section boundaries; full claims are kept when bounded."""
    result, current, size, previous = [], [], 0, None
    for block in source["blocks"]:
        scope = (block["source"], block.get("section", "Document"))
        start, length = 0, len(block["text"])
        while start < length or (length == 0 and start == 0):
            end = cut(block["text"], start, length, 7000)
            if current and (
                scope != previous
                or len(current) >= 8
                or size + end - start > 14000
                or block["kind"] == "section"
            ):
                result.append(current)
                current, size = [], 0
            current.append(unit(block, start, end))
            size += end - start
            previous = scope
            if end == length:
                break
            start = end
    if current:
        result.append(current)
    if len(result) > MAX_CHUNKS:
        raise ValueError("Initial reading plan exceeds 512 chunks; source text was not truncated")
    return result


def materialize(source, refs):
    by_id = {b["id"]: b for b in source["blocks"]}
    result = []
    for ref in refs:
        block, (a, b) = by_id[ref["block_id"]], ref["span"]
        if (
            ref != unit(block, a, b)
            or type(a) is not int
            or type(b) is not int
            or not 0 <= a <= b <= len(block["text"])
        ):
            raise ValueError("Reading partition no longer matches fixed source")
        result.append({**deepcopy(ref), "text": block["text"][a:b]})
    return result


def split_focus(source, refs):
    if len(refs) > 1:
        mid = len(refs) // 2
        return [deepcopy(refs[:mid]), deepcopy(refs[mid:])]
    ref = refs[0]
    a, b = ref["span"]
    if b - a < 2:
        return None
    block = next(b for b in source["blocks"] if b["id"] == ref["block_id"])
    mid = cut(block["text"], a, b, max(1, (b - a) // 2))
    if not a < mid < b:
        mid = a + (b - a) // 2
    return [[unit(block, a, mid)], [unit(block, mid, b)]]


def context_candidates(index, focus):
    by_id = {b["id"]: b for b in index["blocks"]}
    scopes = {by_id[u["block_id"]]["scope_id"] for u in focus}
    names = {s for u in focus for s in by_id[u["block_id"]]["symbols"]}
    return [
        b["id"]
        for b in index["blocks"]
        if b["definition_candidate"]
        and (b["scope_id"] in scopes or bool(names & set(b["symbols"])))
    ]


def scan_packet(source, index, focus, extra=()):
    from .paper_protocol import AUDIT_CHECKS, block_context

    blocks = source["blocks"]
    focus_ids = list(dict.fromkeys(u["block_id"] for u in focus))
    first = min(i for i, b in enumerate(blocks) if b["id"] in focus_ids)
    partial = [
        u["block_id"]
        for u in focus
        if u["span"] != [0, len(next(b["text"] for b in blocks if b["id"] == u["block_id"]))]
    ]
    required = list(dict.fromkeys([*extra, *partial]))
    wanted = list(
        dict.fromkeys(
            [
                *required,
                *[d for b in blocks if b["id"] in focus_ids for d in b.get("dependencies", [])],
                *context_candidates(index, focus),
                *[b["id"] for b in blocks[max(0, first - 3) : first]],
            ]
        )
    )
    scopes = {b["scope_id"] for b in index["blocks"] if b["id"] in focus_ids}
    related = [s for s in index["sections"] if s["id"] in scopes]
    symbols = [s for s in index["symbols"] if s["block_id"] in focus_ids]
    packet = {
        "focus": focus,
        "context": [],
        "omitted_context": [],
        "block_index": [{"id": b["id"], "kind": b["kind"]} for b in blocks],
        "navigation": {
            "sections": related,
            "symbol_candidates": symbols[:128],
            "omitted_symbol_candidates": max(0, len(symbols) - 128),
            "symbol_scope_resolved": False,
            "index_issues": index["issues"],
        },
        "audit_checks": list(AUDIT_CHECKS),
    }
    for bid in wanted:
        if bid in focus_ids and bid not in required:
            continue
        entry = block_context(source, [bid])[0]
        if len(canonical({**packet, "context": [*packet["context"], entry]}).decode()) > 70000:
            if bid in required:
                raise ValueError("Requested complete context exceeds the model input budget")
            packet["omitted_context"].append(bid)
        else:
            packet["context"].append(entry)
    return packet
