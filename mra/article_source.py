"""Bounded local HTML indexing. Extracted blocks are candidates, never proofs."""

from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .model_service import text_field

MAX_SOURCE_BYTES = 1048576
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict
    start: int
    end: int = 0
    parent: object = None
    children: list = field(default_factory=list)

    @property
    def classes(self):
        return set(self.attrs.get("class", "").split())

    def ancestors(self):
        parent = self.parent
        while parent is not None:
            yield parent
            parent = parent.parent


class IndexParser(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.offsets = [0] + [match.end() for match in re.finditer("\n", source)]
        self.root = Element("root", {}, 0, len(source))
        self.stack = [self.root]
        self.elements = []

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len(self.elements) >= 80000 or len(self.stack) >= 128:
            raise ValueError("HTML structure exceeds indexing budget")
        node = Element(
            tag, {key: value or "" for key, value in attrs}, self.position(), parent=self.stack[-1]
        )
        node.end = node.start + len(self.get_starttag_text())
        self.stack[-1].children.append(node)
        self.elements.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                end = self.source.find(">", self.position()) + 1
                for node in self.stack[index:]:
                    node.end = end
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def plain(node):
    if isinstance(node, str):
        return node
    if node.tag in ("script", "style", "noscript"):
        return ""
    if node.tag == "math" and node.attrs.get("alttext"):
        return " $" + node.attrs["alttext"] + "$ "
    return " " + " ".join(plain(child) for child in node.children) + " "


def normalized(node):
    return re.sub(r"\s+", " ", plain(node)).strip()


def source_url(value):
    text_field(value, "Source URL", 2000)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Source URL must be an HTTPS citation without credentials or fragment")
    if any(ord(char) < 33 for char in value):
        raise ValueError("Source URL contains control or whitespace characters")
    return value


def index_html(source):
    if (
        not isinstance(source, str)
        or not source.strip()
        or len(source.encode("utf-8")) > MAX_SOURCE_BYTES
    ):
        raise ValueError("Import a nonempty HTML document of at most 1 MiB")
    parser = IndexParser(source)
    parser.feed(source)
    parser.close()
    for node in parser.stack[1:]:
        node.end = len(source)
    articles = [node for node in parser.elements if node.tag == "article"]
    if len(articles) > 1:
        raise ValueError("Import one article at a time")
    container = articles[0] if articles else parser.root
    candidates = [
        node for node in parser.elements if node is container or container in node.ancestors()
    ]
    section_nodes = [node for node in candidates if node.tag == "section"]
    sections = []
    for node in section_nodes:
        headings = [
            child
            for child in node.children
            if isinstance(child, Element) and child.tag in ("h1", "h2", "h3", "h4", "h5", "h6")
        ]
        sections.append(
            {
                "id": node.attrs.get("id") or "section-" + str(node.start),
                "title": normalized(headings[0]) if headings else "Untitled section",
            }
        )
    blocks, seen = [], set()
    for node in candidates:
        classes = node.classes
        ancestors = list(node.ancestors())
        if any(ancestor.tag in ("script", "style", "nav", "noscript") for ancestor in ancestors):
            continue
        if any("ltx_theorem" in ancestor.classes for ancestor in ancestors):
            continue
        if "ltx_theorem" in classes:
            kind = (
                "definition_candidate"
                if any("defi" in item for item in classes)
                else "claim_candidate"
            )
        elif (
            node.tag == "tbody"
            and node.attrs.get("id")
            and any(
                isinstance(child, Element) and "ltx_equation" in child.classes
                for child in node.children
            )
        ) or ("ltx_equation" in classes and node.attrs.get("id")):
            kind = "equation"
        elif node.tag == "p":
            kind = "passage"
        elif "ltx_bibitem" in classes:
            kind = "reference"
        elif node.tag in ("figcaption", "caption"):
            kind = "caption"
        else:
            continue
        quote = normalized(node)
        if not quote:
            continue
        if len(quote) > 48000:
            raise ValueError("An extracted block exceeds the 48000-character budget")
        anchor = node.attrs.get("id") or "block-" + str(node.start)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", anchor) or anchor in seen:
            raise ValueError("Source blocks need distinct bounded anchors")
        seen.add(anchor)
        section = next((ancestor for ancestor in ancestors if ancestor.tag == "section"), None)
        end = node.end if node.end > node.start else len(source)
        blocks.append(
            {
                "id": anchor,
                "anchor": anchor,
                "kind": kind,
                "section": (section.attrs.get("id") or "section-" + str(section.start))
                if section
                else "article",
                "quote": quote,
                "span": [node.start, end],
                "source_slice_sha256": hashlib.sha256(
                    source[node.start : end].encode("utf-8")
                ).hexdigest(),
            }
        )
    if not blocks or len(blocks) > 1500 or len(sections) > 200:
        raise ValueError("Use a document with 1–1500 indexed blocks and at most 200 sections")
    return {
        "sections": sections,
        "blocks": blocks,
        "extraction_scope": "HTML theorem boxes, prose paragraphs, equations, references and captions; candidates require semantic review",
        "exhaustive_semantic_review": False,
    }
