"""Literal source packets, with unresolved semantics; never mathematical evidence."""

from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .domain import digest

SCHEMA = "source-obligation-drafts-v1"
IMPLEMENTATION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_BATCH = 16
QUESTIONS = [
    "核对对象、类型和空间，逐项填写量词顺序及其范围。",
    "原文陈述暂完整放在结论栏：请拆分全部前提与全部子结论，核对边界情形，不能加强前提或缩小结论。",
    "核对跨段定义、适用范围与引用依赖；邻近片段及引用候选不自动成为假设或已证事实。",
    "另行确认文字与公式提取准确；完成上述整理后再删除已解决的问题并进行七项语义审核。",
]
CUE_PATTERNS = {
    "objects": re.compile(r"\b(?:let|fix)\b|(?:^|[。；;])\s*设", re.I),
    "assumptions": re.compile(
        r"\b(?:assume|assuming|suppose|supposing|if|provided|such that)\b|假设|如果|若|满足", re.I
    ),
    "quantifiers": re.compile(
        r"\bfor (?:every|all|any|each)\b|对(?:于)?(?:任意|所有|每个)|任给", re.I
    ),
}


def obligation_id(source_fingerprint, block_id):
    return "draft_" + digest({"source": source_fingerprint, "block": block_id})[:24]


def cues(text):
    """Offsets refer to the fixed fragment text, not original-file character spans."""
    result, found, offset = [], 0, 0
    for line in text.splitlines(keepends=True):
        quote = line.rstrip("\r\n")
        kinds = [name for name, pattern in CUE_PATTERNS.items() if pattern.search(quote)]
        if kinds:
            found += 1
            if len(result) < 24 and len(quote) <= 2000:
                result.append(
                    {"kinds": kinds, "span": [offset, offset + len(quote)], "quote": quote}
                )
        offset += len(line)
    return {
        "basis": "FIXED_FRAGMENT_TEXT",
        "items": result,
        "matched_lines": found,
        "not_expanded": found - len(result),
        "interpretation": "LEXICAL_HINTS_ONLY",
    }


def nearby(source, index):
    blocks, primary = source["blocks"], source["blocks"][index]
    suggestions = []
    for step in (-1, 1):
        for distance in range(1, 7):
            pos = index + step * distance
            if not 0 <= pos < len(blocks):
                break
            item = blocks[pos]
            if (
                item["kind"] in ("section", "claim")
                or item["source"] != primary["source"]
                or item.get("section") != primary.get("section")
            ):
                break
            if item["kind"] in ("definition", "notation", "passage", "proof"):
                suggestions.append(
                    {
                        "block_id": item["id"],
                        "kind": item["kind"],
                        "relation": "PRECEDING" if step < 0 else "FOLLOWING",
                        "automatically_attached": False,
                    }
                )
    return suggestions


def candidate(source, block_id):
    blocks = {item["id"]: item for item in source["blocks"]}
    primary = blocks.get(block_id)
    if not primary or primary["kind"] != "claim":
        raise ValueError("Choose a fixed source fragment classified as a claim")
    index = next(i for i, b in enumerate(source["blocks"]) if b["id"] == block_id)
    title = next((line.strip() for line in primary["text"].splitlines() if line.strip()), block_id)
    result = {
        "block_id": block_id,
        "title": title[:200],
        "content": None,
        "issues": [],
        "reference_hints": [],
        "cues": cues(primary["text"]),
        "nearby_context": nearby(source, index),
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
    }
    if len(primary["text"]) > 6000:
        result["issues"].append(
            "陈述超过结论栏 6000 字符上限；请人工拆分完整义务，正文不会被截断。"
        )
        return result
    refs = list(dict.fromkeys(primary.get("reference_labels", [])))
    if len(refs) > 20:
        result["issues"].append("显式引用超过每条义务 20 项上限；请人工整理，引用不会被截断。")
        return result
    labels = {}
    for block in source["blocks"]:
        for label in set(
            block.get("labels", []) + ([block["label"]] if block.get("label") else [])
        ):
            labels.setdefault(label, []).append(block["id"])
    sources = [{"block_id": block_id, "role": "statement"}]
    definitions, references, questions = [], [], deepcopy(QUESTIONS)
    for label in refs:
        targets = labels.get(label, [])
        usable = (
            len(targets) == 1
            and targets[0] != block_id
            and blocks[targets[0]]["kind"] != "unreadable"
        )
        result["reference_hints"].append(
            {
                "label": label,
                "targets": targets,
                "status": "UNREVIEWED_TARGET" if usable else "UNRESOLVED_OR_AMBIGUOUS",
            }
        )
        references.append("原文显式引用（适用范围待审）：" + label)
        if usable:
            target = blocks[targets[0]]
            role = "definition" if target["kind"] in ("definition", "notation") else "context"
            ref = {"block_id": target["id"], "role": role}
            if ref not in sources:
                sources.append(ref)
            if role == "definition" and target["text"] not in definitions:
                definitions.append(target["text"])
    if any(x["status"] == "UNRESOLVED_OR_AMBIGUOUS" for x in result["reference_hints"]):
        questions.append(
            "存在未定位、多义、自指或不可读的显式引用；请回原文逐项解决，不自动建立证明依赖。"
        )
    if len(sources) > 24:
        result["issues"].append("所需显式来源超过 24 个上限；请人工整理，不自动删除来源。")
        return result
    definition_text = "\n\n".join(definitions)
    # Keep oversized context in its exact source fragment. Do not insert partial definitions.
    if len(definition_text) > 4000:
        definition_text = ""
        questions.append(
            "引用定义未填入定义栏：完整原文超过字段上限，请从已关联来源人工整理后补全。"
        )
    result["content"] = {
        "title": title[:200],
        "kind": "claim",
        "sources": sources,
        "objects": "",
        "definitions": definition_text,
        "quantifiers": "",
        "assumptions": [],
        "conclusion": primary["text"],
        "references": references,
        "depends_on": [],
        "questions": questions,
    }
    return result
