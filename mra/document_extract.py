"""Source-first structural extraction. Every output is an unverified draft."""

from collections import Counter
import csv
import hashlib
from html import unescape
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from .article_source import index_html

MAX_BLOCKS = 1500
MAX_TEXT = 524288
MAX_PAGES = 60
KINDS = (
    "section",
    "definition",
    "claim",
    "proof",
    "equation",
    "reference",
    "notation",
    "passage",
    "unreadable",
)
ENV_KINDS = {
    **dict.fromkeys(
        (
            "theorem",
            "thm",
            "lemma",
            "lem",
            "proposition",
            "prop",
            "corollary",
            "cor",
            "claim",
            "conjecture",
        ),
        "claim",
    ),
    **dict.fromkeys(("definition", "def", "defn", "notation", "remark"), "definition"),
    "proof": "proof",
    "equation": "equation",
    "align": "equation",
    "gather": "equation",
    "multline": "equation",
    "displaymath": "equation",
    "thebibliography": "reference",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 160
        or re.search(r"[\x00-\x1f\x7f\\:]", value)
    ):
        raise ValueError("Use a bounded source filename without control characters")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Source filenames must be relative without traversal")
    return value


def readable_tex(text):
    text = re.sub(r"\\(?:label|index)\{[^{}]*\}", "", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?", "", text)
    text = re.sub(r"\\(?:textbf|textit|emph|textrm)\{([^{}]*)\}", r"\1", text)
    return text.strip()


def clean_comments(text):
    return re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group()), text)


def block(kind, text, source, span, **extra):
    if kind not in KINDS or not text.strip() or len(text) > 48000:
        raise ValueError("Extracted block is empty, oversized or of an unknown kind")
    return {
        "kind": kind,
        "text": text.strip(),
        "source": source,
        "span": list(span),
        "dependencies": [],
        "assumptions_status": "NOT_REVIEWED",
        **extra,
    }


def html_blocks(name, text):
    inventory = index_html(text)
    kinds = {
        "definition_candidate": "definition",
        "claim_candidate": "claim",
        "equation": "equation",
        "reference": "reference",
    }
    result = []
    for item in inventory["blocks"]:
        kind = kinds.get(item["kind"], "passage")
        if kind == "passage" and re.match(r"^(Proof\b|证明[.。:：]?)", item["quote"], re.I):
            kind = "proof"
        result.append(
            block(
                kind,
                unescape(item["quote"]),
                name,
                item["span"],
                label=item["anchor"],
                section=item["section"],
                source_slice_sha256=item["source_slice_sha256"],
            )
        )
    sections = [
        {"title": x["title"], "source": name, "anchor": x["id"]} for x in inventory["sections"]
    ]
    return (
        result,
        sections,
        [
            "HTML structure is extracted; implicit assumptions and proof dependencies still need review."
        ],
    )


def tex_blocks(sources, entrypoint):
    result, sections, warnings, visited = [], [], [], set()
    aliases = dict(ENV_KINDS)
    for text in sources.values():
        for match in re.finditer(
            r"\\newtheorem\*?\{([^{}]+)\}(?:\[[^\]]*\])?\{([^{}]+)\}", clean_comments(text)
        ):
            title = match[2].lower()
            aliases[match[1]] = "definition" if "def" in title or "notation" in title else "claim"

    def parse(name, stack):
        if len(result) > MAX_BLOCKS or sum(len(b["text"]) for b in result) > MAX_TEXT:
            raise ValueError("TeX expansion exceeds the structural draft budget")
        if name in stack or len(stack) > 12:
            warnings.append("Cyclic or excessively deep TeX include: " + name)
            return
        visited.add(name)
        original = sources[name]
        text = clean_comments(original)
        start = re.search(r"\\begin\{document\}", text)
        end = re.search(r"\\end\{document\}", text)
        lo, hi = start.end() if start else 0, end.start() if end else len(text)
        events, nesting = [], []
        for match in re.finditer(r"\\(begin|end)\{([A-Za-z*]+)\}", text[lo:hi]):
            a, b, env = lo + match.start(), lo + match.end(), match[2]
            if match[1] == "begin":
                nesting.append((env, a, b))
                if len(nesting) > 64:
                    raise ValueError("TeX environment nesting exceeds the intake budget")
            elif nesting and nesting[-1][0] == env:
                _, opened, body = nesting.pop()
                if env.rstrip("*") in aliases:
                    events.append((opened, b, aliases[env.rstrip("*")], text[body:a]))
            elif env.rstrip("*") in aliases:
                warnings.append("Unmatched TeX environment in " + name + ": " + env)
        if nesting:
            warnings.append("Unclosed TeX environments in " + name)
        for match in re.finditer(
            r"\\(?:sub)*section\*?(?:\[[^\]]*\])?\{([^{}]{1,300})\}", text[lo:hi]
        ):
            events.append((lo + match.start(), lo + match.end(), "section", match[1]))
        for match in re.finditer(r"\\(?:input|include)\{([^{}]{1,160})\}", text[lo:hi]):
            events.append((lo + match.start(), lo + match.end(), "include", match[1]))
        for match in re.finditer(r"\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$", text[lo:hi]):
            if not any(a <= lo + match.start() < b for a, b, _, _ in events):
                events.append(
                    (lo + match.start(), lo + match.end(), "equation", match[1] or match[2] or "")
                )
        events.sort(key=lambda e: (e[0], -e[1]))
        # Nested equations remain in their enclosing full claim/proof. They are
        # not silently removed from the statement or promoted as separate facts.
        top, covered = [], lo
        for event in events:
            if event[0] >= covered:
                top.append(event)
                covered = event[1]
        cursor, section = lo, "Document"

        def prose(a, b):
            for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text[a:b]):
                x, y = a + match.start(), a + match.end()
                value = text[x:y].strip()
                if value:
                    result.append(
                        block("passage", readable_tex(value), name, (x, y), section=section)
                    )

        for a, b, kind, value in top:
            prose(cursor, a)
            if kind == "section":
                section = readable_tex(value)
                sections.append({"title": section, "source": name, "span": [a, b]})
                result.append(block("section", section, name, (a, b), section=section))
            elif kind == "include":
                target = str(PurePosixPath(name).parent / value)
                if not target.endswith(".tex"):
                    target += ".tex"
                try:
                    safe_name(target)
                except ValueError:
                    target = ""
                if target in sources:
                    parse(target, (*stack, name))
                else:
                    warnings.append("Unresolved TeX include in " + name + ": " + value)
                    result.append(
                        block(
                            "reference",
                            original[a:b],
                            name,
                            (a, b),
                            section=section,
                            unresolved=True,
                        )
                    )
            else:
                labels = re.findall(r"\\label\{([^{}]+)\}", text[a:b])
                refs = re.findall(r"\\(?:eqref|ref|autoref|cref|Cref)\{([^{}]+)\}", text[a:b])
                result.append(
                    block(
                        kind,
                        readable_tex(value),
                        name,
                        (a, b),
                        section=section,
                        labels=labels,
                        reference_labels=refs,
                    )
                )
            cursor = b
        prose(cursor, hi)
        if re.search(r"\\(?:newcommand|renewcommand|def|DeclareMathOperator)\b", text):
            warnings.append(
                "TeX macros retained without execution or automatic expansion in " + name
            )
        if re.search(r"\\(?:write18|includegraphics|bibliography|usepackage)\b", text):
            warnings.append(
                "TeX external commands, images, packages and bibliography are not executed or fetched in "
                + name
            )

    parse(entrypoint, ())
    unused = sorted(set(sources) - visited)
    if unused:
        warnings.append("Supplied TeX files not reached from the entrypoint: " + ", ".join(unused))
    return result, sections, warnings


def words(text):
    return set(re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", readable_tex(text).lower()))


def suggest_pages(blocks, pages):
    vocabulary = [(p["page"], words(p["text"])) for p in pages if p["text"].strip()]
    for item in blocks:
        target = words(item["text"])
        ranked = sorted(
            (
                (len(target & tokens) / max(1, len(target)), number)
                for number, tokens in vocabulary
                if len(target & tokens) >= 5
            ),
            reverse=True,
        )
        hits = [
            number for score, number in ranked[:3] if score >= 0.55 and score >= ranked[0][0] - 0.03
        ]
        item["pages"] = sorted(hits)
        item["page_match"] = "AMBIGUOUS" if len(hits) > 1 else "SUGGESTED" if hits else "UNLOCATED"


def native_blocks(pages):
    result = []
    for page in pages:
        if not page["text"].strip():
            result.append(
                block(
                    "unreadable",
                    "This page has no recoverable text. Review the original page.",
                    "document.pdf",
                    (0, 0),
                    pages=[page["page"]],
                    page_match="EXACT_PAGE",
                )
            )
            continue
        for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", page["text"]):
            text = match.group().strip()
            kind = "passage"
            if re.match(
                r"^(Theorem|Lemma|Proposition|Corollary|Conjecture|Claim)\b|^(定理|引理|命题|推论)",
                text,
                re.I,
            ):
                kind = "claim"
            elif re.match(r"^(Definition|Notation)\b|^(定义|记号)", text, re.I):
                kind = "definition"
            elif re.match(r"^Proof\b|^证明", text, re.I):
                kind = "proof"
            elif len(text) < 180 and re.match(
                r"^(\d+(?:\.\d+)*[ .]+[A-Z]|Introduction$|References$|Abstract$)", text
            ):
                kind = "section"
            result.append(
                block(
                    kind,
                    text,
                    "document.pdf",
                    (match.start(), match.end()),
                    pages=[page["page"]],
                    page_match="EXACT_PAGE",
                    extraction=page["method"],
                )
            )
    return result


def usable(text):
    letters = sum(c.isalnum() for c in text)
    return letters >= 40 and text.count("\ufffd") / max(1, len(text)) < 0.03


def ocr_page(root, image):
    runtime = Path(root) / ".runtime/document-ingestion-v1/ocr"
    binary = runtime / "usr/bin/tesseract"
    data = runtime / "usr/share/tesseract-ocr/4.00/tessdata"
    if not binary.is_file() or not (data / "eng.traineddata").is_file():
        return None, "OCR_REQUIRED_UNAVAILABLE"
    env = {
        **os.environ,
        "OMP_THREAD_LIMIT": "2",
        "LD_LIBRARY_PATH": str(runtime / "usr/lib/x86_64-linux-gnu"),
    }
    try:
        run = subprocess.run(
            [
                str(binary),
                str(image),
                "stdout",
                "--tessdata-dir",
                str(data),
                "-l",
                "eng",
                "--psm",
                "3",
                "tsv",
            ],
            env=env,
            capture_output=True,
            timeout=35,
        )
    except subprocess.TimeoutExpired:
        return None, "OCR_TIMEOUT"
    if run.returncode or len(run.stdout) > 4194304:
        return None, "OCR_FAILED"
    rows = list(
        csv.DictReader(io.StringIO(run.stdout.decode("utf-8", errors="strict")), delimiter="\t")
    )
    lines, confidence = {}, []
    for row in rows:
        text = (row.get("text") or "").strip()
        if text:
            key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
            lines.setdefault(key, []).append(text)
            try:
                value = float(row["conf"])
                if 0 <= value <= 100:
                    confidence.append(value)
            except (ValueError, KeyError):
                pass
    return {
        "text": "\n".join(" ".join(v) for v in lines.values()),
        "mean_word_confidence": round(sum(confidence) / len(confidence), 1) if confidence else None,
    }, None


def extract_pdf(root, pdf_path, output, ocr, progress):
    import pypdf
    import pypdfium2

    pages, warnings = [], []
    reader = pypdf.PdfReader(pdf_path, strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported; supply an unlocked document")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("Import a PDF of 1–60 pages")
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        if len(pdf) != len(reader.pages):
            raise ValueError("PDF parsers disagree on the page count")
        for i, page in enumerate(reader.pages):
            progress("PDF_PAGE", i + 1, len(reader.pages))
            text_error = None
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                text, text_error = "", "PDF_TEXT_EXTRACTION_FAILED"
            rendered = pdf[i]
            try:
                width, height = rendered.get_size()
                if not 1 <= width <= 14400 or not 1 <= height <= 14400:
                    raise ValueError("PDF page dimensions exceed the rendering budget")
                scale = min(2.0, 2200 / max(width, height))
                bitmap = rendered.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    image_path = output / ("page-%03d.png" % (i + 1))
                    image.save(image_path, format="PNG")
                    pixel_size = list(image.size)
                    image.close()
                finally:
                    bitmap.close()
            finally:
                rendered.close()
            method, issue, confidence = "PDF_TEXT", text_error, None
            if not usable(text):
                if ocr == "auto":
                    progress("OCR_PAGE", i + 1, len(reader.pages))
                    recovered, issue = ocr_page(root, image_path)
                    if recovered and recovered["text"].strip():
                        text, confidence, method = (
                            recovered["text"],
                            recovered["mean_word_confidence"],
                            "OCR",
                        )
                    elif not issue:
                        issue = "OCR_NO_TEXT"
                else:
                    issue = "OCR_DISABLED_TEXT_INSUFFICIENT"
            pages.append(
                {
                    "page": i + 1,
                    "width": width,
                    "height": height,
                    "pixel_size": pixel_size,
                    "image": image_path.name,
                    "image_sha256": sha(image_path.read_bytes()),
                    "text": text,
                    "method": method,
                    "issue": issue,
                    "ocr_word_confidence": confidence,
                }
            )
            if sum(len(p["text"]) for p in pages) > MAX_TEXT:
                raise ValueError("Extracted PDF text exceeds the draft budget; split the document")
            if sum(p.stat().st_size for p in output.glob("page-*.png")) > 100 * 1024 * 1024:
                raise ValueError("Rendered page images exceed 100 MiB")
    finally:
        pdf.close()
    warnings.append(
        "PDF text/OCR does not guarantee faithful mathematical formula recovery; compare formulas with the page image."
    )
    return pages, warnings


def extract(root, directory, inputs, output, progress):
    sources = {
        s["name"]: (directory / s["file"]).read_bytes().decode("utf-8") for s in inputs["sources"]
    }
    pages, warnings, blocks, sections, selected = [], [], None, [], None
    progress("SOURCE_STRUCTURE", 0, 0)
    html = [s for s in sources if s.lower().endswith((".html", ".htm"))]
    tex = {n: t for n, t in sources.items() if n.lower().endswith(".tex")}
    if html:
        try:
            blocks, sections, extra = html_blocks(html[0], sources[html[0]])
            selected, method = html[0], "HTML_SOURCE"
            warnings.extend(extra)
        except ValueError as error:
            warnings.append(
                "HTML source could not be indexed; attempting the next input: " + str(error)
            )
    if blocks is None and tex:
        try:
            if inputs["entrypoint"] not in tex:
                raise ValueError("Choose a supplied TeX entrypoint")
            blocks, sections, extra = tex_blocks(tex, inputs["entrypoint"])
            if not blocks:
                raise ValueError("No readable TeX body was found")
            selected, method = inputs["entrypoint"], "LATEX_SOURCE"
            warnings.extend(extra)
        except ValueError as error:
            blocks = None
            warnings.append(
                "TeX source could not be indexed; attempting PDF fallback: " + str(error)
            )
    if inputs["pdf"]:
        # A usable supplied source already provides the content. PDF extraction
        # is then for comparison/page suggestions; it must not trigger OCR.
        pages, extra = extract_pdf(
            root,
            directory / "document.pdf",
            output,
            "off" if blocks is not None else inputs["ocr"],
            progress,
        )
        if blocks is not None:
            for page in pages:
                if page["issue"] == "OCR_DISABLED_TEXT_INSUFFICIENT":
                    page["issue"] = "SOURCE_USED_PDF_PAGE_MATCH_UNAVAILABLE"
        warnings.extend(extra)
    if blocks is None:
        blocks = native_blocks(pages)
        sections = [
            {"title": b["text"], "source": b["source"], "pages": b["pages"]}
            for b in blocks
            if b["kind"] == "section"
        ]
        method = "PDF_TEXT_WITH_OCR_FALLBACK"
    if selected is not None and pages:
        suggest_pages(blocks, pages)
        warnings.append(
            "Source-to-PDF page matches are text-based suggestions. Source/PDF equivalence has not been certified."
        )
    if not blocks or len(blocks) > MAX_BLOCKS or sum(len(b["text"]) for b in blocks) > MAX_TEXT:
        raise ValueError(
            "Use a document with 1–1500 blocks and at most 524288 extracted characters"
        )
    labels = {}
    for index, item in enumerate(blocks):
        item["id"] = "block-%04d" % (index + 1)
        item.setdefault("pages", [])
        item.setdefault("page_match", "NO_PDF")
        item["original_text_sha256"] = sha(item["text"].encode())
        item["math_verdict"], item["evidence_level"] = "UNKNOWN", "E0"
        for label in item.get("labels", []) + ([item["label"]] if item.get("label") else []):
            labels.setdefault(label, []).append(item["id"])
    for item in blocks:
        refs = item.get("reference_labels", [])
        item["dependencies"] = sorted(
            {target for ref in refs for target in labels.get(ref, []) if target != item["id"]}
        )
        item["unresolved_references"] = [ref for ref in refs if len(labels.get(ref, [])) != 1]
        item["dependency_scope"] = "EXPLICIT_REFERENCES_ONLY_UNREVIEWED"
    tools_path = Path(root) / ".runtime/document-ingestion-v1/installation.json"
    tools = (
        json.loads(tools_path.read_text())
        if tools_path.is_file() and tools_path.stat().st_size < 65536
        else None
    )
    return {
        "schema": "source-document-draft-v1",
        "method": method,
        "selected_source": selected,
        "extractor_sha256": sha(Path(__file__).read_bytes()),
        "extraction_tools": tools,
        "model_calls": 0,
        "lean_executions": 0,
        "pages": pages,
        "sections": sections,
        "blocks": blocks,
        "warnings": sorted(set(warnings)),
        "classification": "RULE_BASED_STRUCTURAL_DRAFT",
        "semantic_review_complete": False,
        "whole_article_verified": False,
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "summary": {
            "blocks": len(blocks),
            "pages": len(pages),
            "kinds": dict(Counter(b["kind"] for b in blocks)),
            "ocr_pages": [p["page"] for p in pages if p["method"] == "OCR"],
            "pages_with_issues": [p["page"] for p in pages if p["issue"]],
        },
    }
